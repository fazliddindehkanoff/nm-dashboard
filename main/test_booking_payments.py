from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .models import Discount, MiniAppPurchase, MulticardInvoice
from .services.legal import BOOKING_CONTRACT_VERSION, TERMS_VERSION
from .services.multicard import MulticardClient, reconcile_invoice
from .test_multicard import BOT_TOKEN, CONFIG, MulticardTests
from .test_telegram_app import TELEGRAM_SETTINGS, TelegramMiniAppTests


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, MULTICARD=CONFIG, TELEGRAM={'BOT_TOKEN': BOT_TOKEN})
class BookingPaymentTests(TestCase):
    post = MulticardTests.post
    callback = MulticardTests.callback
    provider_data = MulticardTests.provider_data

    def setUp(self):
        MulticardTests.setUp(self)
        self.purchase.unit_price = Decimal('1500000')
        self.purchase.total_amount = Decimal('3000000')
        self.purchase.is_booking = True
        self.purchase.booking_discount = Decimal('400000')
        self.purchase.save()
        self.purchase.legal_acceptances.update(version=BOOKING_CONTRACT_VERSION)
        patcher = patch('main.services.multicard.MulticardClient.create_invoice', side_effect=self.invoice_response)
        self.create = patcher.start()
        self.addCleanup(patcher.stop)

    def invoice_response(self, invoice, purchase):
        return {'uuid': str(uuid4()), 'invoice_id': str(invoice.invoice_id),
                'amount': invoice.amount, 'store_id': 6,
                'checkout_url': 'https://checkout.multicard.uz/invoice/test'}

    def pay(self, amount, expected=None):
        self.purchase.refresh_from_db()
        return self.post(self.payment_url, {'amount': amount, 'expected_paid':
                         str(self.purchase.paid_amount) if expected is None else expected})

    def settle_latest(self):
        invoice = self.purchase.multicard_invoices.latest('id')
        self.payment_uuid = uuid4()
        payload = self.callback(invoice)
        result = self.post(self.callback_url, payload, False)
        self.assertEqual(result.status_code, 200, result.content)
        self.purchase.refresh_from_db()
        return invoice, payload

    def test_family_minimum_and_invalid_amounts_never_call_provider(self):
        for amount in ('100000', '199999.99', '0', '-1', 'NaN', 'Infinity', '200000.001', '2600000.01', True, None):
            with self.subTest(amount=amount):
                self.assertEqual(self.pay(amount).status_code, 400)
        self.create.assert_not_called()
        self.assertFalse(MulticardInvoice.objects.exists())
        self.assertEqual(self.pay('200000').status_code, 200)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.paid_amount, 0)
        self.assertEqual(self.purchase.discount_amount, 0)

    def test_deposit_discount_and_three_installments_with_small_final_balance(self):
        self.assertEqual(self.pay('200000').status_code, 200)
        first, payload = self.settle_latest()
        self.assertEqual(self.purchase.payment_status, 'partial')
        self.assertEqual(self.purchase.paid_amount, 200000)
        self.assertEqual(self.purchase.discount_amount, 400000)
        self.assertEqual(self.purchase.remaining_amount, 2400000)
        self.assertEqual(self.purchase.members.filter(client__isnull=False).count(), 2)
        self.assertEqual(self.pay('2350000').status_code, 200)
        self.settle_latest()
        self.assertEqual(self.purchase.remaining_amount, 50000)
        self.assertEqual(self.purchase.minimum_payment, 50000)
        self.assertEqual(self.pay('50000').status_code, 200)
        self.settle_latest()
        self.assertEqual(self.purchase.payment_status, 'success')
        self.assertEqual(self.purchase.remaining_amount, 0)
        self.assertEqual(self.purchase.paid_amount, 2600000)
        self.assertEqual(self.purchase.discount_amount, 400000)
        self.assertEqual(MulticardInvoice.objects.count(), 3)
        # An older installment callback is still harmless after the final payment.
        self.assertEqual(self.post(self.callback_url, payload, False).status_code, 200)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.paid_amount, 2600000)

    def test_duplicate_callback_stale_request_and_open_invoice_amount(self):
        self.assertEqual(self.pay('200000').status_code, 200)
        self.assertEqual(self.pay('200000').status_code, 200)
        self.assertEqual(self.pay('300000').status_code, 400)
        self.assertEqual(self.create.call_count, 1)
        invoice, payload = self.settle_latest()
        self.assertEqual(self.post(self.callback_url, payload, False).status_code, 200)
        self.assertEqual(self.pay('200000', expected='0').status_code, 400)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.paid_amount, 200000)
        self.assertEqual(self.purchase.discount_amount, 400000)
        self.assertEqual(MulticardInvoice.objects.count(), 1)

    def test_single_participant_minimum(self):
        self.purchase.participant_count = 1
        self.purchase.total_amount = Decimal('1500000')
        self.purchase.booking_discount = Decimal('200000')
        self.purchase.save()
        self.assertEqual(self.pay('99999.99').status_code, 400)
        self.assertEqual(self.pay('100000').status_code, 200)
        self.settle_latest()
        self.assertEqual(self.purchase.remaining_amount, 1200000)

    def test_installment_receipt_total_matches_actual_payment(self):
        self.pay('200000')
        invoice = MulticardInvoice.objects.get()
        # Bypass the create mock to inspect the actual fiscal payload builder.
        with patch.object(MulticardClient, '_request') as request:
            from .services.multicard import MulticardClient as Client
            # The original method is saved before patching below.
            ORIGINAL_CREATE(Client(CONFIG), invoice, self.purchase)
        item = request.call_args.args[2]['ofd'][0]
        self.assertEqual(item['qty'] * item['price'], invoice.amount)
        self.assertEqual(item['total'], 20000000)

    @patch('main.services.multicard.MulticardClient.get_invoice')
    def test_refund_of_one_installment_preserves_other_payment(self, get):
        self.pay('200000')
        first, _ = self.settle_latest()
        self.pay('2400000')
        final, _ = self.settle_latest()
        final.refresh_from_db()
        get.return_value = self.provider_data(final, 'revert')
        reconcile_invoice(final)
        reconcile_invoice(final)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.payment_status, 'partial')
        self.assertEqual(self.purchase.paid_amount, 200000)
        self.assertEqual(self.purchase.discount_amount, 400000)
        self.assertEqual(self.purchase.remaining_amount, 2400000)
        first.refresh_from_db()
        self.payment_uuid = first.payment_uuid
        get.return_value = self.provider_data(first, 'revert')
        reconcile_invoice(first)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.payment_status, 'refunded')
        self.assertEqual(self.purchase.paid_amount, 0)
        self.assertEqual(self.purchase.discount_amount, 0)

    def test_partial_purchase_can_fill_questionnaire_and_payload_shows_debt(self):
        self.pay('200000')
        self.settle_latest()
        url = reverse('main:telegram_app_questionnaire', args=[self.purchase.pk])
        responses = [TelegramMiniAppTests.questionnaire(member) for member in self.purchase.members.all()]
        with patch('main.telegram_views.send_bot_message'):
            result = self.post(url, {'responses': responses})
        self.assertEqual(result.status_code, 200, result.content)
        payload = result.json()['purchase']
        self.assertEqual(Decimal(payload['remaining_amount']), 2400000)
        self.assertTrue(payload['questionnaire_completed'])
        self.assertEqual(payload['payment_status'], 'partial')


ORIGINAL_CREATE = MulticardClient.create_invoice


@override_settings(DEBUG=True, TELEGRAM=TELEGRAM_SETTINGS)
class BookingPurchaseTests(TestCase):
    setUp = TelegramMiniAppTests.setUp
    post_json = TelegramMiniAppTests.post_json

    def create_booking(self, family=False):
        self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        self.post_json(reverse('main:telegram_app_accept_terms'), {'accepted': True, 'version': TERMS_VERSION})
        members = [{'full_name': 'Test Family', 'phone_number': '+998901111111'}] if family else []
        return self.post_json(reverse('main:telegram_app_create_purchase'), {
            'course_id': self.course.pk, 'purchase_type': 'family' if family else 'self',
            'members': members, 'payment_mode': 'booking', 'booking_discount': '99999999',
        })

    def test_snapshot_is_per_person_and_cannot_be_changed_by_request_or_admin_discount_edits(self):
        discount = Discount.objects.create(name='Bron', amount=200000, is_booking=True)
        result = self.create_booking(family=True)
        self.assertEqual(result.status_code, 201, result.content)
        purchase = MiniAppPurchase.objects.get(pk=result.json()['purchase']['id'])
        self.assertEqual(purchase.booking_discount, 400000)
        self.assertEqual(purchase.minimum_payment, 200000)
        self.assertEqual(purchase.discount_amount, 0)
        discount.amount = 500000
        discount.save()
        purchase.refresh_from_db()
        self.assertEqual(purchase.booking_discount, 400000)
        url = reverse('main:telegram_app_accept_contract', args=[purchase.pk])
        self.assertEqual(self.post_json(url, {'accepted': True, 'version': BOOKING_CONTRACT_VERSION}).status_code, 200)
        url = reverse('main:telegram_app_simulate_payment', args=[purchase.pk])
        self.assertEqual(self.post_json(url, {'amount': '100000', 'expected_paid': '0'}).status_code, 400)
        paid = self.post_json(url, {'amount': '200000', 'expected_paid': '0'})
        self.assertEqual(paid.status_code, 200, paid.content)
        self.assertEqual(Decimal(paid.json()['purchase']['remaining_amount']), 2400000)
        self.assertEqual(self.post_json(url, {'amount': '200000', 'expected_paid': '0'}).status_code, 400)

    def test_course_booking_rule_takes_precedence_and_preserves_family_discount(self):
        Discount.objects.create(name='Global', amount=300000, is_booking=True)
        Discount.objects.create(name='Course booking', course=self.course, amount=200000, is_booking=True)
        Discount.objects.create(name='Family', course=self.course, amount=100000, min_participants=2)
        result = self.create_booking(family=True)
        self.assertEqual(result.status_code, 201, result.content)
        payload = result.json()['purchase']
        self.assertEqual(Decimal(payload['discount_total']), 200000)
        self.assertEqual(Decimal(payload['total_amount']), 2800000)
        self.assertEqual(Decimal(payload['booking_discount']), 400000)
        self.assertEqual(Decimal(payload['payable_amount']), 2400000)
        self.assertEqual(Decimal(payload['minimum_payment']), 200000)

    def test_no_active_booking_discount_keeps_full_price(self):
        Discount.objects.create(name='Inactive', amount=200000, is_booking=True, is_active=False)
        Discount.objects.create(name='Other', amount=500000, is_booking=False)
        result = self.create_booking()
        self.assertEqual(result.status_code, 201)
        self.assertEqual(Decimal(result.json()['purchase']['booking_discount']), 0)

    def test_booking_cannot_charge_less_than_minimum_when_discount_too_large(self):
        Discount.objects.create(name='Bron', amount=1450000, is_booking=True)
        self.assertEqual(self.create_booking().status_code, 400)
        self.assertFalse(MiniAppPurchase.objects.exists())


class BookingMigrationTests(TransactionTestCase):
    def test_existing_paid_purchase_and_invoice_survive_migration(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        old = [('main', '0027_automatic_discount_choices')]
        new = [('main', '0028_mini_app_booking_installments')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            account = apps.get_model('main', 'TelegramUser').objects.create(telegram_id=92345)
            course = apps.get_model('main', 'Course').objects.create(name='Historical', price=1500000, number_of_days=10)
            purchase = apps.get_model('main', 'MiniAppPurchase').objects.create(
                telegram_user_id=account.pk, course_id=course.pk, unit_price=1500000,
                total_amount=1500000, payment_status='success')
            invoice = apps.get_model('main', 'MulticardInvoice').objects.create(
                purchase_id=purchase.pk, store_id='6', amount=150000000, state='success', payment_uuid=uuid4())
            pending = apps.get_model('main', 'MiniAppPurchase').objects.create(
                telegram_user_id=account.pk, course_id=course.pk, unit_price=1500000, total_amount=1500000)
            executor = MigrationExecutor(connection)
            executor.migrate(new)
            executor.migrate(executor.loader.graph.leaf_nodes())
            purchase = MiniAppPurchase.objects.get(pk=purchase.pk)
            self.assertEqual(purchase.paid_amount, 1500000)
            self.assertEqual(purchase.remaining_amount, 0)
            self.assertEqual(purchase.discount_amount, 0)
            self.assertFalse(purchase.is_booking)
            self.assertEqual(purchase.multicard_invoices.get().payment_uuid, invoice.payment_uuid)
            self.assertEqual(MiniAppPurchase.objects.get(pk=pending.pk).paid_amount, 0)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
