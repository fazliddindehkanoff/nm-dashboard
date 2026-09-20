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
from .booking import check_payment_version, payment_amount


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
        if purchase.is_booking or purchase.social_discount_amount:
            # An installment is one payment towards the course, not N full fees.
            item.update(qty=1, price=invoice.amount,
                        name=f'{purchase.course.name} — qisman to‘lov')
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


def get_or_create_invoice(purchase, requested_amount=None, expected_paid=None):
    config = configuration()
    with transaction.atomic():
        purchase = MiniAppPurchase.objects.select_for_update().get(pk=purchase.pk)
        if purchase.payment_status in (MiniAppPurchase.PAYMENT_SUCCESS, MiniAppPurchase.PAYMENT_REFUNDED):
            raise ValueError("Bu xarid uchun yangi to'lov ochib bo'lmaydi.")
        if purchase.is_booking:
            check_payment_version(purchase, expected_paid)
        if purchase.total_amount != purchase.unit_price * purchase.participant_count - purchase.social_discount_amount:
            raise MulticardError('Xarid summasi ishtirokchilar soniga mos emas.')
        # An existing immutable invoice keeps its agreed amount even if the CRM
        # minimum has since changed. Its amount must still match exactly below.
        has_open_invoice = purchase.multicard_invoices.filter(
            state__in=('creating', 'ready', 'uncertain', 'error'),
        ).exists()
        amount = to_tiyin(payment_amount(
            purchase, requested_amount, minimum=0 if has_open_invoice else None,
        ))
        invoice, created = MulticardInvoice.objects.get_or_create(
            purchase=purchase, state__in=('creating', 'ready', 'uncertain', 'error'),
            defaults={'store_id': str(int(config['STORE_ID'])), 'amount': amount},
        )
        if not created:
            if invoice.amount != amount:
                raise ValueError("Avval ochilgan to'lovni yakunlang. Uning summasini o'zgartirib bo'lmaydi.")
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
        if purchase.referrer_id:
            Client.objects.filter(pk=client.pk, operator__isnull=True).update(operator_id=purchase.referrer_id)
        member.client = client
        member.save(update_fields=('client',))
        if member.relationship == MiniAppPurchaseMember.RELATION_SELF:
            account.client = client
            account.save(update_fields=('client', 'updated_at'))


def _settle(invoice, payment_uuid, receipt_url='', provider='multicard'):
    """Caller holds an atomic transaction and invoice lock."""
    purchase = MiniAppPurchase.objects.select_for_update().get(pk=invoice.purchase_id)
    if invoice.payment_uuid and invoice.payment_uuid != payment_uuid:
        raise InvalidCallback('Payment reference mismatch')
    if invoice.state == 'revert' or purchase.payment_status == MiniAppPurchase.PAYMENT_REFUNDED:
        raise InvalidCallback('Payment already refunded')
    if invoice.state == 'success':
        return
    if purchase.payment_status == MiniAppPurchase.PAYMENT_SUCCESS:
        raise InvalidCallback('Purchase already paid')
    amount = Decimal(invoice.amount) / 100
    if amount > purchase.payable_amount:
        raise InvalidCallback('Payment exceeds outstanding balance')
    discount = purchase.booking_discount if purchase.is_booking else Decimal(0)
    paid = purchase.paid_amount + amount
    status = (MiniAppPurchase.PAYMENT_SUCCESS if paid >= purchase.total_amount - discount
              else MiniAppPurchase.PAYMENT_PARTIAL)
    # Compare the balance as well as holding the lock. On SQLite a competing
    # transaction fails and the provider can retry its signed callback safely.
    updated = MiniAppPurchase.objects.filter(pk=purchase.pk, paid_amount=purchase.paid_amount).update(
             payment_status=status, payment_provider=provider,
             paid_amount=paid, discount_amount=discount,
             payment_reference=str(payment_uuid), paid_at=timezone.now(), updated_at=timezone.now())
    if not updated:
        raise InvalidCallback('Purchase balance changed; retry callback')
    _link_members(purchase)
    invoice.payment_uuid = payment_uuid
    invoice.state = 'success'
    if https_url(receipt_url):
        invoice.receipt_url = receipt_url
    invoice.paid_at = timezone.now()
    invoice.save(update_fields=('payment_uuid', 'state', 'receipt_url', 'paid_at', 'updated_at'))
    if provider != 'demo':
        from main.models import PaymentQRDelivery
        for member in purchase.members.all():
            PaymentQRDelivery.objects.get_or_create(invoice=invoice, member=member)


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
            purchase = MiniAppPurchase.objects.select_for_update().get(pk=invoice.purchase_id)
            invoice.state = 'revert'
            invoice.save(update_fields=('state', 'updated_at'))
            # Recalculate from settled installments: repeated refunds are idempotent.
            paid = sum((Decimal(value) / 100 for value in
                        purchase.multicard_invoices.filter(state='success').values_list('amount', flat=True)), Decimal(0))
            discount = purchase.booking_discount if purchase.is_booking and paid > 0 else Decimal(0)
            purchase_status = (MiniAppPurchase.PAYMENT_REFUNDED if paid == 0 else
                               MiniAppPurchase.PAYMENT_SUCCESS if paid >= purchase.total_amount - discount else
                               MiniAppPurchase.PAYMENT_PARTIAL)
            MiniAppPurchase.objects.filter(pk=purchase.pk).update(
                paid_amount=paid, discount_amount=discount,
                payment_status=purchase_status, updated_at=timezone.now(),
            )
        elif status == 'error' and invoice.state not in ('success', 'revert'):
            invoice.state = 'error'
            invoice.save(update_fields=('state', 'updated_at'))
            MiniAppPurchase.objects.filter(pk=invoice.purchase_id).exclude(
                payment_status__in=(MiniAppPurchase.PAYMENT_SUCCESS, MiniAppPurchase.PAYMENT_REFUNDED, MiniAppPurchase.PAYMENT_PARTIAL),
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
