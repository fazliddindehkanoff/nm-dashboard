from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import grant_operator_permissions
from .models import (
    Client, Course, Group, LegalAcceptance, MiniAppPurchase, MulticardInvoice, Operator, SubTransaction,
    TelegramUser, Transaction, TransactionClient,
)
from .services.multicard import _settle
from .services.payment_links import reverse_crm_installment


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False)
class PaymentLinkTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(name='Sog‘lomlashtirish', price=Decimal('2000000'), number_of_days=10)
        self.group = Group.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=7))
        self.seller = Operator.objects.create(
            user=User.objects.create_user('seller', is_staff=True), full_name='Seller', role='operator',
        )
        grant_operator_permissions(self.seller.user)
        self.client_record = Client.objects.create(
            full_name='Aziza Karimova', phone_number='+998 90 123 45 67', operator=self.seller,
        )
        self.admin = User.objects.create_superuser('link-admin')

    # ---- helpers

    def link_page(self, client=None):
        return reverse('admin:main_client_payment_link', args=[(client or self.client_record).pk])

    def create_link(self, user=None, **data):
        self.client.force_login(user or self.admin)
        response = self.client.post(self.link_page(), data)
        self.assertEqual(response.status_code, 302, response.content[:300])
        return MiniAppPurchase.objects.order_by('-id').first()

    def public_url(self, purchase):
        return reverse('main:payment_link', args=[purchase.link_token])

    def pay_demo(self, purchase, **data):
        self.client.logout()
        return self.client.post(self.public_url(purchase), {'expected_paid': purchase.paid_amount, 'demo': '1', **data})

    def settle(self, purchase, amount):
        invoice = MulticardInvoice.objects.create(purchase=purchase, store_id='6', amount=int(amount * 100))
        with transaction.atomic():
            _settle(invoice, uuid4())
        return invoice

    def cash_payment(self, amount):
        payment = Transaction.objects.create(
            group=self.group, date=timezone.localdate(), amount=Decimal(amount), payment_type='bron',
            is_confirmed=True, operator=self.seller,
        )
        TransactionClient.objects.create(transaction=payment, client=self.client_record)
        payment.save()
        return payment

    def debt(self):
        return sum(TransactionClient.objects.filter(client=self.client_record).values_list('debt', flat=True), Decimal(0))

    # ---- course links

    def test_course_link_is_paid_in_a_browser_after_the_contract(self):
        purchase = self.create_link(action='course', group=self.group.pk, payment_mode='full')
        self.assertIsNone(purchase.telegram_user)
        self.assertEqual((purchase.created_by, purchase.referrer, purchase.group), (self.admin, self.seller, self.group))
        self.assertEqual(purchase.total_amount, Decimal('2000000'))
        self.assertContains(self.client.get(self.link_page()), str(purchase.link_token))

        self.client.logout()
        page = self.client.get(self.public_url(purchase))
        self.assertContains(page, 'Sog‘lomlashtirish')
        self.assertContains(page, 'Shartnomani qabul qilaman va to‘layman')
        self.assertContains(page, 'Aziza Karimova')  # the contract names the client
        self.assertEqual(page['X-Robots-Tag'], 'noindex, nofollow')
        self.assertEqual(page['Referrer-Policy'], 'same-origin')  # "no-referrer" breaks the CSRF check

        refused = self.pay_demo(purchase)
        self.assertContains(refused, 'roziligingizni belgilang')
        self.assertFalse(LegalAcceptance.objects.exists())

        version = page.context['contract_version']
        self.assertRedirects(self.pay_demo(purchase, consent='on', version=version), self.public_url(purchase))
        purchase.refresh_from_db()
        self.assertEqual(purchase.payment_status, MiniAppPurchase.PAYMENT_SUCCESS)
        acceptance = LegalAcceptance.objects.get(purchase=purchase)
        self.assertIsNone(acceptance.telegram_user)
        # The sale is recorded in the group for this client.
        mirror = Transaction.objects.get(mini_app_purchase=purchase)
        self.assertEqual((mirror.group, list(mirror.clients.all())), (self.group, [self.client_record]))
        self.assertContains(self.client.get(self.public_url(purchase)), 'To‘lov qabul qilindi')

    def test_booking_link_form_posts_plain_numbers(self):
        purchase = self.create_link(action='course', group=self.group.pk, payment_mode='booking')
        self.assertTrue(purchase.is_booking)
        self.client.logout()
        page = self.client.get(self.public_url(purchase))
        # Localised decimals ("100 000,00") would break the number input and the stale-payment check.
        self.assertContains(page, 'name="expected_paid" value="0"')
        self.assertContains(page, f'value="{purchase.minimum_payment:.0f}"')
        response = self.pay_demo(purchase, consent='on', version=page.context['contract_version'],
                                 amount=str(purchase.minimum_payment))
        self.assertRedirects(response, self.public_url(purchase))
        purchase.refresh_from_db()
        self.assertEqual(purchase.payment_status, MiniAppPurchase.PAYMENT_PARTIAL)

    def test_link_payment_opens_multicard_and_returns_to_the_link(self):
        purchase = self.create_link(action='course', group=self.group.pk, payment_mode='full')
        self.client.logout()
        version = self.client.get(self.public_url(purchase)).context['contract_version']
        invoice = SimpleNamespace(checkout_url='https://checkout.multicard.uz/abc')
        with patch('main.payment_link_views.get_or_create_invoice', return_value=invoice) as create:
            response = self.client.post(self.public_url(purchase), {'consent': 'on', 'version': version})
        self.assertRedirects(response, 'https://checkout.multicard.uz/abc', fetch_redirect_response=False)
        self.assertTrue(create.call_args.kwargs['return_url'].endswith(self.public_url(purchase)))

    def test_referral_seller_is_credited_for_a_link_sale(self):
        referral = Operator.objects.create(user=User.objects.create_user('referral'), full_name='Referral', role='operator')
        TelegramUser.objects.create(telegram_id=77, client=self.client_record, referrer=referral)
        purchase = self.create_link(action='course', group=self.group.pk, payment_mode='full')
        self.assertEqual(purchase.referrer, referral)
        self.settle(purchase, Decimal('2000000'))
        self.client.force_login(self.admin)
        row = next(row for row in self.client.get(reverse('main:referrals')).context['rows'] if row['name'] == 'Referral')
        self.assertEqual((row['sales'], row['paid']), (1, Decimal('2000000')))

    # ---- debt links

    def test_debt_link_adds_the_online_payment_to_the_crm_payment(self):
        payment = self.cash_payment(500000)
        self.assertEqual(self.debt(), Decimal('1500000'))
        participation = TransactionClient.objects.get(transaction=payment)
        first = self.create_link(action='debt', participation=participation.pk, amount='1500000')
        purchase = self.create_link(action='debt', participation=participation.pk, amount='1000000')
        first.refresh_from_db()
        self.assertEqual(first.payment_status, MiniAppPurchase.PAYMENT_CANCELLED)  # replaced by the newer link
        self.assertEqual((purchase.crm_transaction, purchase.total_amount, purchase.referrer), (payment, Decimal('1000000'), None))

        self.client.logout()
        page = self.client.get(self.public_url(purchase))
        self.assertNotContains(page, 'Xizmat shartnomasi')  # the course was already sold
        self.settle(purchase, Decimal('1000000'))  # the Multicard success callback
        purchase.refresh_from_db()
        self.assertEqual(purchase.payment_status, MiniAppPurchase.PAYMENT_SUCCESS)
        sub = SubTransaction.objects.get(transaction=payment)
        self.assertEqual((sub.status, sub.amount, list(sub.clients.all())), ('approved', Decimal('1000000'), [self.client_record]))
        payment.refresh_from_db()
        self.assertEqual((payment.amount, self.debt()), (Decimal('1500000'), Decimal('500000')))
        self.assertFalse(Transaction.objects.filter(mini_app_purchase=purchase).exists())

        # Listed once on the payments page, from the online payment.
        self.client.force_login(self.admin)
        rows = self.client.get(reverse('main:payments')).context['page']
        link_rows = [row for row in rows if row['course'] == 'Sog‘lomlashtirish' and row['amount'] == Decimal('1000000')]
        self.assertEqual([row['source'] for row in link_rows], ['To‘lov havolasi'])

        # A refund returns the debt.
        with transaction.atomic():
            reverse_crm_installment(sub.multicard_invoice)
        payment.refresh_from_db()
        sub.refresh_from_db()
        self.assertEqual((sub.status, payment.amount, self.debt()), ('rejected', Decimal('500000'), Decimal('1500000')))

    def test_debt_link_stops_when_the_debt_was_paid_another_way(self):
        payment = self.cash_payment(500000)
        participation = TransactionClient.objects.get(transaction=payment)
        purchase = self.create_link(action='debt', participation=participation.pk, amount='1500000')
        self.cash_payment(1000000)  # paid at the centre after the link was sent
        self.client.logout()
        self.assertContains(self.client.get(self.public_url(purchase)), 'boshqa yo‘l bilan to‘langan')
        self.assertContains(self.pay_demo(purchase), 'Bu havola orqali to')
        purchase.refresh_from_db()
        self.assertEqual(purchase.paid_amount, 0)

    def test_web_app_debt_is_shared_as_its_own_purchase(self):
        account = TelegramUser.objects.create(telegram_id=88, full_name='Aziza', phone_number='+998901234567')
        web = MiniAppPurchase.objects.create(
            telegram_user=account, course=self.course, group=self.group, unit_price=Decimal('2000000'),
            total_amount=Decimal('2000000'), is_booking=True,
        )
        web.members.create(full_name='Aziza', phone_number='+998901234567', relationship='self')
        self.settle(web, Decimal('500000'))
        participation = TransactionClient.objects.get(transaction__mini_app_purchase=web)
        shared = self.create_link(action='debt', participation=participation.pk)
        self.assertEqual(shared.pk, web.pk)
        self.assertIsNotNone(shared.link_token)
        self.client.logout()
        self.assertContains(self.client.get(self.public_url(shared)), 'Hozir to‘laydigan summa')

    # ---- access

    def test_links_are_for_staff_who_sell(self):
        other = Client.objects.create(full_name='Boshqa mijoz', phone_number='+998901112233')
        self.client.force_login(self.seller.user)
        self.assertEqual(self.client.get(self.link_page()).status_code, 200)
        # A seller only reaches their own clients.
        self.assertEqual(self.client.get(self.link_page(other)).status_code, 302)
        accountant = Operator.objects.create(user=User.objects.create_user('accountant', is_staff=True), full_name='Hisobchi', role='accountant')
        self.client.force_login(accountant.user)
        self.assertEqual(self.client.get(self.link_page()).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('main:payment_link', args=[uuid4()])).status_code, 404)

    def test_course_link_is_refused_for_a_group_the_client_joined(self):
        self.cash_payment(500000)
        self.client.force_login(self.admin)
        response = self.client.post(self.link_page(), {'action': 'course', 'group': self.group.pk, 'payment_mode': 'full'}, follow=True)
        self.assertContains(response, 'allaqachon yozilgan')
        self.assertFalse(MiniAppPurchase.objects.exists())

    def test_cancelled_link_cannot_be_paid(self):
        purchase = self.create_link(action='course', group=self.group.pk, payment_mode='full')
        self.client.post(self.link_page(), {'action': 'cancel', 'purchase': purchase.pk})
        self.client.logout()
        self.assertContains(self.client.get(self.public_url(purchase)), 'havola bekor qilingan')
