import hashlib
import hmac
import json
import time
from io import StringIO
from decimal import Decimal
from unittest.mock import Mock, patch
from urllib.parse import urlencode
from uuid import uuid4

import requests
from django.core.management import call_command
from django.test import Client as TestClient, TestCase, override_settings
from django.urls import reverse

from .models import Client, Course, LegalAcceptance, MiniAppPurchase, MiniAppPurchaseMember, MulticardInvoice, TelegramUser
from .services.legal import CONTRACT_VERSION, TERMS_VERSION
from .services.multicard import MulticardClient, MulticardError, get_or_create_invoice, reconcile_invoice, to_tiyin

CONFIG = {
    'ENABLED': True, 'BASE_URL': 'https://dev-mesh.multicard.uz',
    'APPLICATION_ID': 'test-app', 'SECRET': 'test-secret', 'STORE_ID': '6',
    'CALLBACK_URL': 'https://example.com/payments/multicard/callback/',
    'RETURN_URL': 'https://t.me/example_bot', 'OFD_MXIK': 'test-mxik',
    'OFD_PACKAGE_CODE': 'test-package', 'OFD_VAT': '0',
}
BOT_TOKEN = '123:test'


def response(data, status=200):
    result = Mock(status_code=status)
    result.json.return_value = data
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError()
    return result


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, MULTICARD=CONFIG, TELEGRAM={'BOT_TOKEN': BOT_TOKEN})
class MulticardTests(TestCase):
    def setUp(self):
        self.account = TelegramUser.objects.create(telegram_id=123, full_name='Test User', phone_number='+998901234567')
        self.course = Course.objects.create(name='Course', price=Decimal('125000.25'), number_of_days=10)
        self.purchase = MiniAppPurchase.objects.create(telegram_user=self.account, course=self.course,
            unit_price=self.course.price, total_amount=self.course.price * 2, participant_count=2, purchase_type='family')
        MiniAppPurchaseMember.objects.create(purchase=self.purchase, full_name='Test User', phone_number=self.account.phone_number, relationship='self')
        MiniAppPurchaseMember.objects.create(purchase=self.purchase, full_name='Family Member', phone_number='+998901111111', relationship='family')
        for document_type, version, purchase in [('terms', TERMS_VERSION, None), ('contract', CONTRACT_VERSION, self.purchase)]:
            LegalAcceptance.objects.create(telegram_user=self.account, purchase=purchase, document_type=document_type,
                version=version, document_hash='a' * 64)
        values = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': self.account.telegram_id})}
        secret = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
        values['hash'] = hmac.new(secret, '\n'.join(f'{k}={values[k]}' for k in sorted(values)).encode(), hashlib.sha256).hexdigest()
        self.headers = {'HTTP_X_TELEGRAM_INIT_DATA': urlencode(values)}
        self.payment_url = reverse('main:telegram_app_payment', args=[self.purchase.pk])
        self.callback_url = reverse('main:multicard_callback')
        self.provider_uuid = uuid4()
        self.payment_uuid = uuid4()

    def post(self, url, data=None, authenticated=True, client=None):
        return (client or self.client).post(url, json.dumps(data or {}), content_type='application/json',
            **(self.headers if authenticated else {}))

    def invoice(self, state='ready'):
        return MulticardInvoice.objects.create(purchase=self.purchase, provider_uuid=self.provider_uuid,
            amount=25000050, store_id='6', state=state, checkout_url='https://checkout.multicard.uz/invoice/test')

    def callback(self, invoice, **changes):
        payload = {'store_id': 6, 'invoice_id': str(invoice.invoice_id), 'amount': invoice.amount,
            'uuid': str(self.payment_uuid), 'receipt_url': 'https://checkout.multicard.uz/receipt/test'}
        payload.update(changes)
        payload['sign'] = hashlib.md5(f"{payload['store_id']}{payload['invoice_id']}{payload['amount']}{CONFIG['SECRET']}".encode()).hexdigest()
        return payload

    def provider_data(self, invoice, status='success'):
        return {'uuid': str(invoice.provider_uuid), 'store_id': 6, 'amount': invoice.amount,
            'invoice_id': str(invoice.invoice_id), 'payment': {'uuid': str(self.payment_uuid),
            'store_id': 6, 'store_invoice_id': str(invoice.invoice_id), 'status': status,
            'receipt_url': 'https://checkout.multicard.uz/receipt/test'}}

    @patch('main.services.multicard.requests.request')
    def test_create_uses_server_amount_and_reuses_invoice(self, request):
        def respond(method, url, **kwargs):
            if url.endswith('/auth'):
                self.assertEqual(kwargs['json'], {'application_id': 'test-app', 'secret': 'test-secret'})
                return response({'token': 'access-token'})
            payload = kwargs['json']
            self.assertEqual(payload['amount'], 25000050)
            self.assertEqual(payload['ofd'][0], {'qty': 2, 'price': 12500025, 'total': 25000050,
                'name': 'Course', 'mxik': 'test-mxik', 'package_code': 'test-package', 'vat': 0})
            self.assertEqual(kwargs['headers']['Authorization'], 'Bearer access-token')
            self.assertEqual(payload['callback_url'], CONFIG['CALLBACK_URL'])
            self.assertFalse(kwargs['allow_redirects'])
            return response({'success': True, 'data': {**payload, 'store_id': 6,
                'uuid': str(self.provider_uuid), 'checkout_url': 'https://checkout.multicard.uz/invoice/test'}})
        request.side_effect = respond
        first = self.post(self.payment_url, {'amount': 1})
        self.assertEqual(first.status_code, 200, first.content)
        second = self.post(self.payment_url)
        self.assertEqual(second.json()['checkout_url'], first.json()['checkout_url'])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(MulticardInvoice.objects.count(), 1)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.payment_status, 'pending')
        self.assertEqual(self.purchase.payment_provider, 'multicard')

    @patch('main.services.multicard.MulticardClient.create_invoice', side_effect=MulticardError('timeout'))
    def test_ambiguous_creation_is_not_retried(self, create):
        self.assertEqual(self.post(self.payment_url).status_code, 502)
        self.assertEqual(self.post(self.payment_url).status_code, 502)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(MulticardInvoice.objects.get().state, 'uncertain')

    def test_signed_callback_is_idempotent_and_links_family(self):
        invoice = self.invoice()
        payload = self.callback(invoice)
        for _ in range(2):
            result = self.post(self.callback_url, payload, authenticated=False, client=TestClient(enforce_csrf_checks=True))
            self.assertEqual(result.json(), {'success': True})
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.payment_status, 'success')
        self.assertEqual(self.purchase.payment_reference, str(self.payment_uuid))
        self.assertEqual(Client.objects.count(), 2)
        self.assertEqual(self.purchase.members.filter(client__isnull=False).count(), 2)
        self.account.refresh_from_db()
        self.assertIsNotNone(self.account.client_id)
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_uuid, self.payment_uuid)
        paid_at = self.purchase.paid_at
        self.post(self.callback_url, payload, authenticated=False)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.paid_at, paid_at)

    def test_invalid_signature_cannot_mark_paid(self):
        invoice = self.invoice()
        payload = self.callback(invoice)
        payload['sign'] = '0' * 32
        self.assertEqual(self.post(self.callback_url, payload, False).status_code, 400)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.payment_status, 'pending')

    def test_wrong_amount_store_invoice_and_malformed_payloads_are_rejected(self):
        invoice = self.invoice()
        for changes in ({'amount': 1}, {'store_id': 7}, {'invoice_id': str(uuid4())}, {'uuid': 'bad'},
                        {'amount': '25000050'}, {'amount': True}, {'status': 'error'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.post(self.callback_url, self.callback(invoice, **changes), False).status_code, 400)
        for payload in ([], 'not a dict', 123):
            result = self.client.post(self.callback_url, json.dumps(payload), content_type='application/json')
            self.assertEqual(result.status_code, 400)
        self.assertEqual(Client.objects.count(), 0)

    def test_second_payment_reference_cannot_replace_first(self):
        invoice = self.invoice()
        self.post(self.callback_url, self.callback(invoice), False)
        result = self.post(self.callback_url, self.callback(invoice, uuid=str(uuid4())), False)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(Client.objects.count(), 2)

    @patch('main.services.multicard._link_members', side_effect=RuntimeError('database failure'))
    def test_callback_failure_rolls_back_and_requests_retry(self, link):
        invoice = self.invoice()
        result = self.post(self.callback_url, self.callback(invoice), False)
        self.assertEqual(result.status_code, 500)
        self.purchase.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(self.purchase.payment_status, 'pending')
        self.assertEqual(invoice.state, 'ready')

    def test_callback_works_while_new_checkouts_disabled(self):
        invoice = self.invoice()
        with override_settings(MULTICARD={**CONFIG, 'ENABLED': False}):
            self.assertEqual(self.post(self.callback_url, self.callback(invoice), False).status_code, 200)

    def test_checkout_requires_authentication_ownership_and_legal_acceptance(self):
        self.assertEqual(self.post(self.payment_url, authenticated=False).status_code, 401)
        self.assertEqual(self.post(reverse('main:telegram_app_payment', args=[9999])).status_code, 404)
        self.purchase.legal_acceptances.all().delete()
        self.assertEqual(self.post(self.payment_url).status_code, 409)
        self.assertFalse(MulticardInvoice.objects.exists())

    def test_other_users_cannot_inspect_or_check_payment(self):
        other = TelegramUser.objects.create(telegram_id=999)
        self.purchase.telegram_user = other
        self.purchase.save()
        self.assertEqual(self.post(self.payment_url).status_code, 404)
        url = reverse('main:telegram_app_payment_status', args=[self.purchase.pk])
        self.assertEqual(self.client.get(url, **self.headers).status_code, 404)
        self.assertEqual(self.post(reverse('main:telegram_app_check_payment', args=[self.purchase.pk])).status_code, 404)

    def test_demo_endpoint_is_forbidden_for_real_users_even_in_debug(self):
        with override_settings(DEBUG=True):
            self.assertEqual(self.post(reverse('main:telegram_app_simulate_payment', args=[self.purchase.pk])).status_code, 403)

    @override_settings(MULTICARD={**CONFIG, 'ENABLED': False})
    def test_disabled_gateway_does_not_create_invoice(self):
        self.assertEqual(self.post(self.payment_url).status_code, 503)
        self.assertFalse(MulticardInvoice.objects.exists())

    def test_status_and_questionnaire_do_not_trust_browser_success(self):
        self.invoice()
        url = reverse('main:telegram_app_payment_status', args=[self.purchase.pk]) + '?success=true'
        result = self.client.get(url, **self.headers)
        self.assertEqual(result.json()['purchase']['payment_status'], 'pending')
        self.assertEqual(result['Cache-Control'], 'no-store')
        self.assertEqual(self.post(reverse('main:telegram_app_questionnaire', args=[self.purchase.pk])).status_code, 409)

    @patch('main.services.multicard.MulticardClient.get_invoice')
    def test_reconciliation_recovers_payment_and_records_refund(self, get):
        invoice = self.invoice()
        get.return_value = self.provider_data(invoice)
        reconcile_invoice(invoice)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.payment_status, 'success')
        get.return_value = self.provider_data(invoice, 'revert')
        reconcile_invoice(invoice)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.payment_status, 'refunded')
        # A delayed old success callback must not undo a refund.
        self.assertEqual(self.post(self.callback_url, self.callback(invoice), False).status_code, 400)
        self.assertEqual(self.post(reverse('main:telegram_app_questionnaire', args=[self.purchase.pk])).status_code, 409)

    @patch('main.services.multicard.MulticardClient.get_invoice')
    def test_reconciliation_rejects_mismatched_data_and_ignores_processing(self, get):
        invoice = self.invoice()
        for key, value in [('amount', 1), ('store_id', 7), ('invoice_id', 'other'), ('uuid', str(uuid4()))]:
            get.return_value = {**self.provider_data(invoice), key: value}
            with self.assertRaises(MulticardError):
                reconcile_invoice(invoice)
        for status in ('draft', 'billing', 'progress'):
            get.return_value = self.provider_data(invoice, status)
            reconcile_invoice(invoice)
            self.purchase.refresh_from_db()
            self.assertEqual(self.purchase.payment_status, 'pending')

    def test_currency_conversion_is_exact(self):
        self.assertEqual(to_tiyin(Decimal('125000.25')), 12500025)
        for value in ('0', '-1', '1.001', 'NaN', 'Infinity'):
            with self.assertRaises(MulticardError):
                to_tiyin(Decimal(value))

    @patch('main.services.multicard.requests.request')
    def test_provider_errors_do_not_leak_secret_details(self, request):
        request.return_value = response({'secret': 'do-not-leak'}, 500)
        with self.assertRaises(MulticardError) as error:
            MulticardClient().get_invoice(self.provider_uuid)
        self.assertNotIn('do-not-leak', str(error.exception))

    @patch('main.services.multicard.MulticardClient.create_invoice')
    def test_insecure_checkout_link_is_not_exposed(self, create):
        def invalid(invoice, purchase):
            return {'uuid': str(self.provider_uuid), 'invoice_id': str(invoice.invoice_id),
                'amount': invoice.amount, 'store_id': 6, 'checkout_url': 'javascript:alert(1)'}
        create.side_effect = invalid
        self.assertEqual(self.post(self.payment_url).status_code, 502)
        self.assertEqual(MulticardInvoice.objects.get().checkout_url, '')

    @patch('main.services.multicard.MulticardClient.get_invoice')
    def test_command_recovers_unknown_uuid_and_reconciles(self, get):
        invoice = self.invoice('uncertain')
        data = self.provider_data(invoice)
        data['checkout_url'] = invoice.checkout_url
        invoice.provider_uuid = None
        invoice.checkout_url = ''
        invoice.save()
        get.return_value = data
        output = StringIO()
        call_command('reconcile_multicard', purchase_id=self.purchase.pk,
                     provider_uuid=str(self.provider_uuid), stdout=output)
        invoice.refresh_from_db()
        self.assertEqual(invoice.provider_uuid, self.provider_uuid)
        self.assertEqual(invoice.state, 'success')
        self.assertIn('success', output.getvalue())

    def test_payment_post_still_requires_csrf(self):
        self.assertEqual(self.post(self.payment_url, client=TestClient(enforce_csrf_checks=True)).status_code, 403)
