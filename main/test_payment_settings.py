from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import test_booking_payments as booking_tests
from .models import Discount, MiniAppPurchase, PaymentSettings
from .services.legal import contract_version, render_contract_document, CONTRACT_VERSION


def set_minimum(amount):
    PaymentSettings.objects.update_or_create(pk=1, defaults={'minimum_booking_amount': amount})


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False,
                   MULTICARD=booking_tests.CONFIG, TELEGRAM={'BOT_TOKEN': booking_tests.BOT_TOKEN})
class ConfiguredPaymentTests(TestCase):
    post = booking_tests.BookingPaymentTests.post
    callback = booking_tests.BookingPaymentTests.callback
    provider_data = booking_tests.BookingPaymentTests.provider_data
    invoice_response = booking_tests.BookingPaymentTests.invoice_response
    pay = booking_tests.BookingPaymentTests.pay
    settle_latest = booking_tests.BookingPaymentTests.settle_latest

    def setUp(self):
        booking_tests.BookingPaymentTests.setUp(self)
        set_minimum(1000)
        self.purchase.legal_acceptances.update(version=contract_version(self.purchase))

    def test_single_person_can_pay_1000_and_receipt_uses_tiyin(self):
        self.purchase.participant_count = 1
        self.purchase.total_amount = self.purchase.unit_price
        self.purchase.booking_discount = 200000
        self.purchase.save()
        self.purchase.members.filter(relationship='family').delete()
        self.assertEqual(self.pay('999.99').status_code, 400)
        self.create.assert_not_called()
        result = self.pay('1000')
        self.assertEqual(result.status_code, 200, result.content)
        invoice = self.purchase.multicard_invoices.get()
        self.assertEqual(invoice.amount, 100000)
        self.settle_latest()
        self.assertEqual(self.purchase.paid_amount, 1000)

    def test_family_minimum_and_restore_take_effect_immediately(self):
        self.assertEqual(self.purchase.minimum_payment, 2000)
        self.assertEqual(self.pay('1000').status_code, 400)
        set_minimum(100000)
        self.purchase.legal_acceptances.update(version=contract_version(self.purchase))
        self.assertEqual(self.purchase.minimum_payment, 200000)
        self.assertEqual(self.pay('2000').status_code, 400)
        self.assertEqual(self.pay('200000').status_code, 200)

    def test_open_invoice_keeps_amount_after_minimum_changes(self):
        self.assertEqual(self.pay('2000').status_code, 200)
        invoice = self.purchase.multicard_invoices.get()
        set_minimum(100000)
        self.purchase.legal_acceptances.update(version=contract_version(self.purchase))
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount, 200000)
        self.assertEqual(self.pay('2000').status_code, 200)
        self.assertEqual(self.pay('200000').status_code, 400)
        self.assertEqual(self.create.call_count, 1)
        self.settle_latest()
        self.assertEqual(self.purchase.paid_amount, 2000)

    def test_small_final_balance_and_full_payment_behavior(self):
        self.purchase.paid_amount = self.purchase.payable_amount - Decimal('500')
        self.assertEqual(self.purchase.minimum_payment, 500)
        self.purchase.is_booking = False
        self.assertEqual(self.purchase.minimum_payment, self.purchase.payable_amount)

    def test_contract_displays_minimum_and_requires_matching_acceptance(self):
        html = render_contract_document(self.purchase)
        self.assertIn('1 000 UZS', html)
        self.assertNotIn('100 000 UZS', html)
        version = contract_version(self.purchase)
        set_minimum(100000)
        self.assertNotEqual(contract_version(self.purchase), version)
        self.assertEqual(self.pay('200000').status_code, 409)
        self.purchase.is_booking = False
        self.assertEqual(contract_version(self.purchase), CONTRACT_VERSION)


@override_settings(DEBUG=True, TELEGRAM=booking_tests.TELEGRAM_SETTINGS)
class ConfiguredCheckoutTests(TestCase):
    setUp = booking_tests.BookingPurchaseTests.setUp
    post_json = booking_tests.BookingPurchaseTests.post_json
    create_booking = booking_tests.BookingPurchaseTests.create_booking

    def test_catalogue_and_creation_use_setting(self):
        set_minimum(1000)
        self.course.price = 5000
        self.course.save()
        response = self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        self.assertEqual(Decimal(response.json()['courses'][0]['minimum_booking']), 1000)
        created = self.create_booking()
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(Decimal(created.json()['purchase']['minimum_payment']), 1000)
        set_minimum(100000)
        self.assertEqual(self.create_booking().status_code, 400)


@override_settings(SECURE_SSL_REDIRECT=False)
class PaymentSettingsAdminTests(TestCase):
    def setUp(self):
        set_minimum(100000)
        self.url = reverse('admin:main_paymentsettings_change', args=[1])

    def test_admin_can_change_and_restore_setting(self):
        user = User.objects.create_superuser('payment-admin', password='test-password')
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse('admin:main_paymentsettings_changelist')), self.url)
        for amount in (1000, 100000):
            result = self.client.post(self.url, {'minimum_booking_amount': amount, '_save': 'Save'})
            self.assertEqual(result.status_code, 302, result.content)
            self.assertEqual(PaymentSettings.booking_minimum(), amount)
        self.assertEqual(PaymentSettings.objects.count(), 1)

    def test_view_only_staff_cannot_change_settings(self):
        user = User.objects.create_user('payment-viewer', password='test-password', is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='view_paymentsettings'))
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, {'minimum_booking_amount': 1000}).status_code, 403)
        self.assertEqual(PaymentSettings.booking_minimum(), 100000)

    def test_invalid_minimum_cannot_be_saved(self):
        for amount in (0, -1):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                PaymentSettings(pk=1, minimum_booking_amount=amount).full_clean()

    def test_missing_settings_preserve_normal_default(self):
        PaymentSettings.objects.all().delete()
        self.assertEqual(PaymentSettings.booking_minimum(), 100000)
