import tempfile
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    AttendanceRecord, Client, Course, Discount, Group, LegalAcceptance, MiniAppCartItem, MiniAppPurchase,
    TelegramUser, Transaction, TransactionClient,
)
from .services.legal import BOOKING_CONTRACT_VERSION, CONTRACT_VERSION, TERMS_VERSION
from .services.multicard import reconcile_invoice
from . import test_multicard as multicard_fixture
from . import test_telegram_app as app_fixture


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, TELEGRAM=app_fixture.TELEGRAM_SETTINGS)
class WebAppCartTests(TestCase):
    post_json = app_fixture.TelegramMiniAppTests.post_json

    def setUp(self):
        app_fixture.TelegramMiniAppTests.setUp(self)
        self.second = Course.objects.create(name='Ikkinchi kurs', price=Decimal('800000'), number_of_days=5)
        self.second_group = Group.objects.create(course=self.second, start_date=timezone.localdate() + timedelta(days=3))
        self.bootstrap()
        self.account = TelegramUser.objects.get(telegram_id=900000001)
        self.post_json(reverse('main:telegram_app_accept_terms'), {'accepted': True, 'version': TERMS_VERSION})
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        storage = override_settings(PRIVATE_DOCUMENT_ROOT=temp.name)
        storage.enable()
        self.addCleanup(storage.disable)

    def bootstrap(self):
        return self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers).json()

    def add(self, course=None, **extra):
        payload = {'course_id': (course or self.course).id, 'purchase_type': 'self', 'payment_mode': 'full', **extra}
        return self.post_json(reverse('main:telegram_app_save_cart_item'), payload)

    def checkout(self, *item_ids):
        return self.post_json(reverse('main:telegram_app_checkout'), {'item_ids': list(item_ids)})

    def proof(self, phone):
        return self.client.post(reverse('main:upload_eligibility_document'), {
            'category': 'student', 'phone_number': phone,
            'document': SimpleUploadedFile('student.pdf', b'%PDF-1.7\nTest document', 'application/pdf'),
        }, **self.headers).json()['id']

    def test_selected_cart_courses_become_one_checkout_paid_course_by_course(self):
        Discount.objects.create(name='Family', course=self.course, amount=100000, min_participants=2)
        proof = self.proof('+998911112233')
        first = self.add(purchase_type='family', members=[
            {'full_name': 'Ali Valiyev', 'phone_number': '+998 91 111 22 33', 'eligibility_document_id': proof},
        ])
        self.assertEqual(first.status_code, 200, first.content)
        item = first.json()['cart'][0]
        self.assertEqual(item['group']['id'], self.group.id)
        self.assertEqual(Decimal(item['total_amount']), 2 * 1400000 - 100000)
        self.assertEqual(item['members'][0]['eligibility_document']['id'], proof)
        second = self.add(self.second, group_id=self.second_group.id).json()['item_id']
        later = Course.objects.create(name='Keyinroq', price=Decimal('500000'), number_of_days=3)
        Group.objects.create(course=later, start_date=timezone.localdate() + timedelta(days=30))
        self.add(later)

        response = self.checkout(first.json()['item_id'], second)
        self.assertEqual(response.status_code, 201, response.content)
        purchases = response.json()['purchases']
        self.assertEqual([item['course'] for item in purchases], [self.course.name, 'Ikkinchi kurs'])
        self.assertEqual(len({item['checkout_batch'] for item in purchases}), 1)
        self.assertEqual([item['group']['id'] for item in purchases], [self.group.id, self.second_group.id])
        self.assertEqual([item['course'] for item in response.json()['cart']], ['Keyinroq'])
        stored = MiniAppPurchase.objects.get(pk=purchases[0]['id'])
        self.assertEqual(stored.social_discount_amount, 100000)
        self.assertEqual(stored.members.get(relationship='family').eligibility_document_id, proof)

        ids = [item['id'] for item in purchases]
        contracts = self.client.get(
            reverse('main:telegram_app_contracts') + f'?ids={ids[0]},{ids[1]}', **self.headers,
        ).json()['document']
        self.assertIn('Ikkinchi kurs', contracts['html'])
        self.assertIn(self.group.start_date.strftime('%d.%m.%Y'), contracts['html'])
        accept_url = reverse('main:telegram_app_accept_contracts')
        stale = {str(ids[0]): 'old', str(ids[1]): CONTRACT_VERSION}
        self.assertEqual(self.post_json(accept_url, {'accepted': True, 'purchase_ids': ids, 'versions': stale}).status_code, 400)
        accepted = self.post_json(accept_url, {'accepted': True, 'purchase_ids': ids, 'versions': contracts['versions']})
        self.assertEqual(accepted.status_code, 200, accepted.content)
        acceptances = LegalAcceptance.objects.filter(document_type='contract', purchase_id__in=ids)
        self.assertEqual(acceptances.count(), 2)
        self.assertEqual(len(set(acceptances.values_list('document_hash', flat=True))), 2)

        paid = self.post_json(reverse('main:telegram_app_simulate_payment', args=[ids[1]]), {})
        self.assertEqual(paid.status_code, 200, paid.content)
        payment = Transaction.objects.get(mini_app_purchase_id=ids[1])
        self.assertEqual((payment.group, payment.amount), (self.second_group, Decimal('800000')))
        self.assertEqual((payment.source, payment.payment_method, payment.is_confirmed), ('telegram_app', 'rahmat', True))
        self.assertEqual(TransactionClient.objects.get(transaction=payment).debt, 0)
        self.assertTrue(AttendanceRecord.objects.filter(group=self.second_group).exists())
        self.assertFalse(Transaction.objects.filter(mini_app_purchase_id=ids[0]).exists())

    def test_cart_items_never_expire_and_report_group_state(self):
        self.add()
        updated = self.add(purchase_type='family', members=[{'full_name': 'Ali Valiyev', 'phone_number': '+998911112233'}])
        self.assertEqual(len(updated.json()['cart']), 1)
        self.assertEqual(updated.json()['cart'][0]['participant_count'], 2)

        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(days=90)):
            cart = self.bootstrap()['cart']
        self.assertEqual([item['status'] for item in cart], ['course_unavailable'])

        next_group = Group.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=20))
        self.group.start_date = timezone.localdate()
        self.group.save()
        cart = self.bootstrap()['cart']
        self.assertEqual(cart[0]['status'], 'choose_group')
        self.assertEqual(self.checkout(cart[0]['id']).status_code, 400)
        self.add(group_id=next_group.id)
        self.assertEqual(self.bootstrap()['cart'][0]['status'], 'ready')

    def test_unpaid_purchase_returns_to_cart_with_its_participants(self):
        proof = self.proof(self.account.phone_number)
        item = self.add(payment_mode='booking', eligibility_document_id=proof).json()['item_id']
        purchase = self.checkout(item).json()['purchases'][0]
        self.assertTrue(purchase['can_return_to_cart'])
        self.post_json(reverse('main:telegram_app_accept_contract', args=[purchase['id']]),
                       {'accepted': True, 'version': BOOKING_CONTRACT_VERSION})
        returned = self.post_json(reverse('main:telegram_app_return_to_cart', args=[purchase['id']]), {})
        self.assertEqual(returned.status_code, 200, returned.content)
        cart = returned.json()['cart'][0]
        self.assertEqual((cart['payment_mode'], cart['eligibility_document']['id']), ('booking', proof))
        self.assertEqual(MiniAppPurchase.objects.get(pk=purchase['id']).payment_status, 'cancelled')
        self.assertEqual(self.bootstrap()['purchases'], [])

        cancelled = self.post_json(reverse('main:telegram_app_simulate_payment', args=[purchase['id']]), {})
        self.assertEqual(cancelled.status_code, 400)

        purchase = self.checkout(cart['id']).json()['purchases'][0]
        # A course added again after checkout keeps its newer cart settings.
        self.add(payment_mode='full')
        self.post_json(reverse('main:telegram_app_return_to_cart', args=[purchase['id']]), {})
        self.assertEqual(self.bootstrap()['cart'][0]['payment_mode'], 'full')
        purchase = self.checkout(self.bootstrap()['cart'][0]['id']).json()['purchases'][0]
        self.post_json(reverse('main:telegram_app_accept_contract', args=[purchase['id']]),
                       {'accepted': True, 'version': CONTRACT_VERSION})
        self.post_json(reverse('main:telegram_app_simulate_payment', args=[purchase['id']]), {})
        self.assertEqual(
            self.post_json(reverse('main:telegram_app_return_to_cart', args=[purchase['id']]), {}).status_code, 409,
        )

    def test_cart_validation_and_privacy(self):
        self.assertEqual(self.add(purchase_type='family', members=[]).status_code, 400)
        self.assertEqual(self.add(purchase_type='family', members=[
            {'full_name': 'Demo Foydalanuvchi', 'phone_number': self.account.phone_number},
        ]).status_code, 400)
        started = Group.objects.create(course=self.course, start_date=timezone.localdate())
        self.assertEqual(self.add(group_id=started.id).status_code, 400)
        Discount.objects.create(name='Bron', amount=1450000, is_booking=True)
        self.assertEqual(self.add(payment_mode='booking').status_code, 400)

        other = TelegramUser.objects.create(telegram_id=77, full_name='Other', phone_number='+998900000077')
        foreign = MiniAppCartItem.objects.create(telegram_user=other, course=self.course, group=self.group)
        self.assertEqual(self.checkout(foreign.id).status_code, 400)
        self.assertEqual(self.post_json(reverse('main:telegram_app_remove_cart_item', args=[foreign.id]), {}).status_code, 404)
        self.assertTrue(MiniAppCartItem.objects.filter(pk=foreign.pk).exists())
        self.assertEqual(self.checkout('1').status_code, 400)

        own = self.add().json()['item_id']
        self.assertEqual(self.post_json(reverse('main:telegram_app_remove_cart_item', args=[own]), {}).json()['cart'], [])

    def test_direct_purchase_is_assigned_to_the_chosen_or_nearest_group(self):
        later = Group.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=40))
        url = reverse('main:telegram_app_create_purchase')
        nearest = self.post_json(url, {'course_id': self.course.id, 'purchase_type': 'self'}).json()['purchase']
        chosen = self.post_json(url, {'course_id': self.course.id, 'purchase_type': 'self', 'group_id': later.id}).json()['purchase']
        self.assertEqual((nearest['group']['id'], chosen['group']['id']), (self.group.id, later.id))


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, MULTICARD=multicard_fixture.CONFIG,
                   TELEGRAM={'BOT_TOKEN': multicard_fixture.BOT_TOKEN})
class WebAppGroupPaymentTests(TestCase):
    post = multicard_fixture.MulticardTests.post
    callback = multicard_fixture.MulticardTests.callback
    provider_data = multicard_fixture.MulticardTests.provider_data

    def setUp(self):
        multicard_fixture.MulticardTests.setUp(self)
        self.course.price = Decimal('1500000')
        self.course.save()
        self.group = Group.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=5))
        MiniAppPurchase.objects.filter(pk=self.purchase.pk).update(
            group=self.group, unit_price=Decimal('1500000'), total_amount=Decimal('3000000'),
            is_booking=True, booking_discount=Decimal('400000'),
        )
        self.purchase.refresh_from_db()
        self.purchase.legal_acceptances.update(version=BOOKING_CONTRACT_VERSION)
        patcher = patch('main.services.multicard.MulticardClient.create_invoice', side_effect=self.invoice_response)
        patcher.start()
        self.addCleanup(patcher.stop)

    def invoice_response(self, invoice, purchase):
        return {'uuid': str(uuid4()), 'invoice_id': str(invoice.invoice_id), 'amount': invoice.amount,
                'store_id': 6, 'checkout_url': 'https://checkout.multicard.uz/invoice/test'}

    def pay(self, amount):
        self.purchase.refresh_from_db()
        result = self.post(self.payment_url, {'amount': amount, 'expected_paid': str(self.purchase.paid_amount)})
        self.assertEqual(result.status_code, 200, result.content)
        invoice = self.purchase.multicard_invoices.latest('id')
        self.payment_uuid = uuid4()
        for _ in range(2):  # Repeated callbacks must not change the group payment twice.
            self.assertEqual(self.post(self.callback_url, self.callback(invoice), False).status_code, 200)
        return invoice

    def debt(self):
        return sum(TransactionClient.objects.filter(transaction__mini_app_purchase=self.purchase).values_list('debt', flat=True))

    def test_installments_and_refunds_follow_the_purchase_balance(self):
        first = self.pay('200000')
        payment = Transaction.objects.get(mini_app_purchase=self.purchase)
        self.assertEqual((payment.group, payment.amount, payment.payment_type), (self.group, 200000, 'bron'))
        self.assertEqual(payment.participants.count(), 2)
        self.assertEqual(self.debt(), 2400000)

        self.pay('2400000')
        payment.refresh_from_db()
        self.assertEqual(payment.amount, 2600000)
        self.assertEqual(self.debt(), 0)
        self.assertEqual(Transaction.objects.filter(mini_app_purchase=self.purchase).count(), 1)

        first.refresh_from_db()
        self.payment_uuid = first.payment_uuid
        with patch('main.services.multicard.MulticardClient.get_invoice', return_value=self.provider_data(first, 'revert')):
            reconcile_invoice(first)
        payment.refresh_from_db()
        self.assertEqual((payment.amount, payment.is_refunded), (2400000, False))

        second = self.purchase.multicard_invoices.exclude(pk=first.pk).get()
        self.payment_uuid = second.payment_uuid
        with patch('main.services.multicard.MulticardClient.get_invoice', return_value=self.provider_data(second, 'revert')):
            reconcile_invoice(second)
        payment.refresh_from_db()
        self.assertTrue(payment.is_refunded)

    def test_group_sync_failure_never_blocks_recording_the_payment(self):
        with patch('main.services.multicard.sync_group_payment', side_effect=RuntimeError('crm failure')):
            self.pay('200000')
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.paid_amount, 200000)
        self.assertFalse(Transaction.objects.filter(mini_app_purchase=self.purchase).exists())

    def test_payments_page_lists_a_web_app_payment_once(self):
        self.pay('200000')
        admin = User.objects.create_superuser(username='payments-admin')
        self.client.force_login(admin)
        rows = list(self.client.get(reverse('main:payments')).context['page'])
        self.assertEqual([(row['source'], row['amount']) for row in rows], [('Web App', Decimal('200000'))])


class WebAppGroupAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser(username='group-admin'))
        self.course = Course.objects.create(name='Health', price=Decimal('1000000'), number_of_days=5)
        self.group = Group.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=4))
        account = TelegramUser.objects.create(telegram_id=5001, full_name='Aziza Karimova', phone_number='+998901112233')
        self.purchase = MiniAppPurchase.objects.create(
            telegram_user=account, course=self.course, purchase_type='family', unit_price=Decimal('1000000'),
            participant_count=2, total_amount=Decimal('2000000'), paid_amount=Decimal('2000000'), payment_status='success',
        )
        for name, phone in [('Aziza Karimova', '+998901112233'), ('Jasur Karimov', '+998901112244')]:
            client = Client.objects.create(full_name=name, phone_number=phone)
            self.purchase.members.create(full_name=name, phone_number=phone, client=client,
                                         relationship='self' if name.startswith('Aziza') else 'family')

    def test_legacy_payment_is_assigned_from_admin_and_counted_in_the_group(self):
        detail = reverse('admin:main_group_detail', args=[self.group.pk])
        self.assertEqual(self.client.get(detail).context['unassigned_web_payments'], 1)
        response = self.client.post(reverse('admin:main_miniapppurchase_change', args=[self.purchase.pk]), {
            'group': self.group.pk, 'questionnaire_completed': '',
            'members-TOTAL_FORMS': '0', 'members-INITIAL_FORMS': '0',
            'members-MIN_NUM_FORMS': '0', 'members-MAX_NUM_FORMS': '1000',
            'multicard_invoices-TOTAL_FORMS': '0', 'multicard_invoices-INITIAL_FORMS': '0',
            'multicard_invoices-MIN_NUM_FORMS': '0', 'multicard_invoices-MAX_NUM_FORMS': '1000',
        })
        self.assertEqual(response.status_code, 302)
        payment = Transaction.objects.get(mini_app_purchase=self.purchase)
        self.assertEqual((payment.group, payment.amount), (self.group, 2000000))

        page = self.client.get(detail)
        self.assertEqual(page.context['unassigned_web_payments'], 0)
        self.assertEqual(page.context['participants_count'], 2)
        self.assertContains(page, 'Web app')
        groups = self.client.get(reverse('admin:main_group_changelist')).context['cl'].result_list
        self.assertEqual({group.pk: group._participants_count for group in groups}, {self.group.pk: 2})
        change = self.client.get(reverse('admin:main_miniapppurchase_change', args=[self.purchase.pk]))
        self.assertNotIn('group', change.context['adminform'].form.fields)
