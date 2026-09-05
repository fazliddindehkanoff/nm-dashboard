"""Multicard hosted checkout (success callback mode).

Never retry invoice POST after an ambiguous response. Provider callbacks are the
billing acknowledgement, so they must commit before returning success=True.
"""
import hashlib
import hmac
import re
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import UUID

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from main.models import Client, MiniAppPurchase, MiniAppPurchaseMember, MulticardInvoice


class MulticardError(Exception):
    pass


class MulticardNotConfigured(MulticardError):
    pass


class InvalidCallback(ValueError):
    pass


def https_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def configuration():
    config = getattr(settings, 'MULTICARD', {})
    required = ('APPLICATION_ID', 'SECRET', 'STORE_ID', 'CALLBACK_URL', 'RETURN_URL', 'OFD_MXIK', 'OFD_PACKAGE_CODE')
    if not config.get('ENABLED') or any(not config.get(key) for key in required):
        raise MulticardNotConfigured("Onlayn to'lov hali sozlanmagan. Administrator bilan bog'laning.")
    if config.get('BASE_URL', '').rstrip('/') not in ('https://dev-mesh.multicard.uz', 'https://mesh.multicard.uz'):
        raise MulticardNotConfigured('Multicard server manzili noto‘g‘ri.')
    if not all(https_url(config[key]) for key in ('CALLBACK_URL', 'RETURN_URL')):
        raise MulticardNotConfigured('Multicard HTTPS manzillari sozlanmagan.')
    if not str(config['STORE_ID']).isdigit() or int(config['STORE_ID']) <= 0:
        raise MulticardNotConfigured('Multicard raqamli store ID kerak.')
    if config.get('OFD_VAT', '') != '':
        try:
            vat = int(config['OFD_VAT'])
            if not 0 <= vat <= 100:
                raise ValueError
        except (TypeError, ValueError):
            raise MulticardNotConfigured('Multicard QQS qiymati noto‘g‘ri.')
    return config


def to_tiyin(amount):
    value = Decimal(amount) * 100
    if not value.is_finite() or value <= 0 or value != value.to_integral_value():
        raise MulticardError("To'lov summasi noto'g'ri.")
    return int(value)


class MulticardClient:
    def __init__(self, config=None):
        self.config = config or configuration()
        self.token = None

    def _request(self, method, path, payload=None, authenticated=True):
        if authenticated and not self.token:
            auth = self._request('POST', '/auth', {
                'application_id': self.config['APPLICATION_ID'], 'secret': self.config['SECRET'],
            }, authenticated=False)
            self.token = auth.get('token')
            if not isinstance(self.token, str) or not self.token:
                raise MulticardError('Multicard avtorizatsiyasi amalga oshmadi.')
        headers = {'Accept': 'application/json'}
        if authenticated:
            headers['Authorization'] = f'Bearer {self.token}'
        try:
            response = requests.request(
                method, self.config['BASE_URL'].rstrip('/') + path,
                json=payload, headers=headers, timeout=(5, 15), allow_redirects=False,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError):
            # Do not expose provider payloads, headers, tokens or card data.
            raise MulticardError('Multicard bilan aloqa amalga oshmadi. Holatni tekshiring.') from None
        if not isinstance(body, dict) or not 200 <= response.status_code < 300:
            raise MulticardError('Multicard javobi noto‘g‘ri.')
        if not authenticated:
            return body
        if body.get('success') is not True or not isinstance(body.get('data'), dict):
            raise MulticardError('Multicard so‘rovni bajara olmadi. Holatni tekshiring.')
        return body['data']

    def create_invoice(self, invoice, purchase):
        item = {
            'qty': purchase.participant_count, 'price': to_tiyin(purchase.unit_price),
            'total': invoice.amount, 'name': purchase.course.name,
            'mxik': self.config['OFD_MXIK'], 'package_code': self.config['OFD_PACKAGE_CODE'],
        }
        if self.config.get('OFD_VAT', '') != '':
            item['vat'] = int(self.config['OFD_VAT'])
        return self._request('POST', '/payment/invoice', {
            'store_id': invoice.store_id, 'amount': invoice.amount,
            'invoice_id': str(invoice.invoice_id), 'lang': 'uz',
            'return_url': self.config['RETURN_URL'], 'return_error_url': self.config['RETURN_URL'],
            'callback_url': self.config['CALLBACK_URL'], 'ofd': [item],
        })

    def get_invoice(self, provider_uuid):
        return self._request('GET', f'/payment/invoice/{UUID(str(provider_uuid))}')


def get_or_create_invoice(purchase):
    config = configuration()
    amount = to_tiyin(purchase.total_amount)
    if amount != to_tiyin(purchase.unit_price) * purchase.participant_count:
        raise MulticardError('Xarid summasi ishtirokchilar soniga mos emas.')
    invoice, created = MulticardInvoice.objects.get_or_create(
        purchase=purchase, defaults={'store_id': str(int(config['STORE_ID'])), 'amount': amount},
    )
    if not created:
        if invoice.checkout_url and invoice.state in ('ready', 'error'):
            return invoice
        raise MulticardError("To'lov holati tekshirilmoqda. Qayta to'lamang; administrator bilan bog'laning.")
    try:
        data = MulticardClient(config).create_invoice(invoice, purchase)
        provider_uuid = UUID(data.get('uuid', ''))
        if (str(data.get('invoice_id')) != str(invoice.invoice_id)
                or type(data.get('amount')) is not int or data['amount'] != amount
                or str(data.get('store_id')) != invoice.store_id or not https_url(data.get('checkout_url'))):
            raise ValueError
        with transaction.atomic():
            current = MulticardInvoice.objects.select_for_update().get(pk=invoice.pk)
            current.provider_uuid = provider_uuid
            current.store_id = str(data['store_id'])
            current.checkout_url = data['checkout_url']
            if current.state in ('creating', 'uncertain'):
                current.state = 'ready'
            current.save(update_fields=('provider_uuid', 'store_id', 'checkout_url', 'state', 'updated_at'))
        MiniAppPurchase.objects.filter(pk=purchase.pk).update(payment_provider='multicard')
        return current
    except (MulticardError, ValueError, TypeError, AttributeError):
        MulticardInvoice.objects.filter(pk=invoice.pk, state='creating').update(state='uncertain')
        raise MulticardError("To'lov havolasini olib bo'lmadi. Qayta to'lamang; administrator bilan bog'laning.") from None


def _link_members(purchase):
    account = purchase.telegram_user
    for member in purchase.members.all():
        # Keep the existing CRM phone normalization behavior.
        target = ''.join(filter(str.isdigit, member.phone_number))
        client = next((item for item in Client.objects.only('id', 'phone_number')
                       if ''.join(filter(str.isdigit, item.phone_number)) == target), None)
        if not client:
            client = Client.objects.create(full_name=member.full_name, phone_number=member.phone_number)
        member.client = client
        member.save(update_fields=('client',))
        if member.relationship == MiniAppPurchaseMember.RELATION_SELF:
            account.client = client
            account.save(update_fields=('client', 'updated_at'))


def _settle(invoice, payment_uuid, receipt_url=''):
    """Caller holds an atomic transaction and invoice lock."""
    purchase = MiniAppPurchase.objects.select_for_update().get(pk=invoice.purchase_id)
    if invoice.payment_uuid and invoice.payment_uuid != payment_uuid:
        raise InvalidCallback('Payment reference mismatch')
    if invoice.state == 'revert' or purchase.payment_status == MiniAppPurchase.PAYMENT_REFUNDED:
        raise InvalidCallback('Payment already refunded')
    if purchase.payment_status == MiniAppPurchase.PAYMENT_SUCCESS:
        if purchase.payment_reference != str(payment_uuid):
            raise InvalidCallback('Purchase already paid')
        return
    # Conditional write also makes simultaneous callbacks safe on SQLite, where
    # select_for_update is a no-op (a competing transaction fails and retries).
    updated = MiniAppPurchase.objects.filter(pk=purchase.pk).exclude(
        payment_status=MiniAppPurchase.PAYMENT_SUCCESS,
    ).update(payment_status=MiniAppPurchase.PAYMENT_SUCCESS, payment_provider='multicard',
             payment_reference=str(payment_uuid), paid_at=timezone.now(), updated_at=timezone.now())
    if updated:
        _link_members(purchase)
    invoice.payment_uuid = payment_uuid
    invoice.state = 'success'
    if https_url(receipt_url):
        invoice.receipt_url = receipt_url
    invoice.save(update_fields=('payment_uuid', 'state', 'receipt_url', 'updated_at'))


def accept_success_callback(data):
    # Callback processing stays available when new checkouts are disabled.
    secret = getattr(settings, 'MULTICARD', {}).get('SECRET', '')
    if not secret:
        raise MulticardNotConfigured('Callback secret is not configured')
    if not isinstance(data, dict):
        raise InvalidCallback('Invalid payload')
    amount, store_id, invoice_id, sign = (data.get(key) for key in ('amount', 'store_id', 'invoice_id', 'sign'))
    if (type(amount) is not int or amount <= 0 or type(store_id) is not int
            or not isinstance(invoice_id, str) or not isinstance(sign, str)
            or not re.fullmatch('[0-9a-f]{32}', sign)):
        raise InvalidCallback('Invalid payload')
    expected = hashlib.md5(f'{store_id}{invoice_id}{amount}{secret}'.encode()).hexdigest()
    if not hmac.compare_digest(expected, sign):
        raise InvalidCallback('Invalid signature')
    # This endpoint implements the documented MD5 success callback only, not the
    # differently signed SHA1 status webhook protocol.
    if 'status' in data:
        raise InvalidCallback('Use success callback mode')
    try:
        local_id, payment_uuid = UUID(invoice_id), UUID(data.get('uuid', ''))
    except (ValueError, TypeError, AttributeError):
        raise InvalidCallback('Invalid reference') from None
    with transaction.atomic():
        invoice = MulticardInvoice.objects.select_for_update().filter(invoice_id=local_id).first()
        if not invoice or invoice.amount != amount or invoice.store_id != str(store_id):
            raise InvalidCallback('Invoice mismatch')
        _settle(invoice, payment_uuid, data.get('receipt_url'))


def reconcile_invoice(invoice):
    """Recover missed confirmations and detect refunds via authenticated GET."""
    if not invoice.provider_uuid:
        raise MulticardError('Invoice UUID is unknown; reconcile in the merchant portal before retrying.')
    data = MulticardClient().get_invoice(invoice.provider_uuid)
    if (str(data.get('uuid')) != str(invoice.provider_uuid)
            or str(data.get('invoice_id')) != str(invoice.invoice_id)
            or str(data.get('store_id')) != invoice.store_id
            or type(data.get('amount')) is not int or data['amount'] != invoice.amount):
        raise MulticardError('Invoice reconciliation mismatch')
    payment = data.get('payment')
    if not payment:
        return
    if not isinstance(payment, dict):
        raise MulticardError('Invalid payment response')
    if (str(payment.get('store_id')) != invoice.store_id
            or str(payment.get('store_invoice_id')) != str(invoice.invoice_id)):
        raise MulticardError('Payment reconciliation mismatch')
    try:
        payment_uuid = UUID(payment.get('uuid', ''))
    except (ValueError, TypeError, AttributeError):
        raise MulticardError('Invalid payment UUID') from None
    with transaction.atomic():
        invoice = MulticardInvoice.objects.select_for_update().get(pk=invoice.pk)
        status = payment.get('status')
        if status == 'success':
            # The immutable invoice amount is the merchant amount; payment totals
            # may include an additional payer commission.
            _settle(invoice, payment_uuid, payment.get('receipt_url'))
        elif status == 'revert' and invoice.payment_uuid == payment_uuid:
            invoice.state = 'revert'
            invoice.save(update_fields=('state', 'updated_at'))
            MiniAppPurchase.objects.filter(pk=invoice.purchase_id).update(
                payment_status=MiniAppPurchase.PAYMENT_REFUNDED, updated_at=timezone.now(),
            )
        elif status == 'error' and invoice.state not in ('success', 'revert'):
            invoice.state = 'error'
            invoice.save(update_fields=('state', 'updated_at'))
            MiniAppPurchase.objects.filter(pk=invoice.purchase_id).exclude(
                payment_status__in=(MiniAppPurchase.PAYMENT_SUCCESS, MiniAppPurchase.PAYMENT_REFUNDED),
            ).update(payment_status=MiniAppPurchase.PAYMENT_FAILED, updated_at=timezone.now())


def recover_invoice(invoice, provider_uuid):
    """Attach a UUID found in the merchant portal after an ambiguous POST."""
    provider_uuid = UUID(str(provider_uuid))
    data = MulticardClient().get_invoice(provider_uuid)
    if (str(data.get('uuid')) != str(provider_uuid)
            or str(data.get('invoice_id')) != str(invoice.invoice_id)
            or str(data.get('store_id')) != invoice.store_id
            or type(data.get('amount')) is not int or data['amount'] != invoice.amount
            or not https_url(data.get('checkout_url'))):
        raise MulticardError('Recovery invoice mismatch')
    with transaction.atomic():
        current = MulticardInvoice.objects.select_for_update().get(pk=invoice.pk)
        if current.provider_uuid and current.provider_uuid != provider_uuid:
            raise MulticardError('Invoice already has a different UUID')
        current.provider_uuid = provider_uuid
        current.checkout_url = data['checkout_url']
        if current.state in ('creating', 'uncertain'):
            current.state = 'ready'
        current.save(update_fields=('provider_uuid', 'checkout_url', 'state', 'updated_at'))
    return current
