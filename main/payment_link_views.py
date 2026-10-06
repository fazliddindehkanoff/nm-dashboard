"""Public page behind a payment link that staff send to a client.

The page needs no login: the unguessable link token is the only key. It shows
the order, asks for the contract when the link sells a course, and hands the
payment over to Multicard, which returns the client to this page.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .models import LegalAcceptance, MiniAppPurchase, MulticardInvoice
from .services.booking import check_payment_version, payment_amount
from .services.legal import contract_accepted, contract_version, record_acceptance, render_contract_document
from .services.multicard import MulticardError, _settle, get_or_create_invoice, to_tiyin
from .services.payment_links import crm_debt
from .telegram_views import _telegram_asset_version

OPEN_INVOICE_STATES = ('creating', 'ready', 'uncertain', 'error')


def _purchase(token):
    purchase = (
        MiniAppPurchase.objects.filter(link_token=token)
        .select_related('course', 'group', 'telegram_user', 'crm_transaction')
        .prefetch_related('members', 'multicard_invoices', 'legal_acceptances', 'group__teachers')
        .first()
    )
    if purchase is None:
        raise Http404
    return purchase


def _needs_contract(purchase):
    # A debt link pays for a course the client has already joined.
    return not purchase.crm_transaction_id and not contract_accepted(purchase)


def _debt_changed(purchase):
    """The CRM debt was paid some other way since the link was made."""
    return (
        bool(purchase.crm_transaction_id) and not purchase.paid_amount
        and crm_debt(purchase) < purchase.payable_amount
    )


def _state(purchase):
    if purchase.payment_status == MiniAppPurchase.PAYMENT_CANCELLED:
        return 'cancelled'
    if purchase.payment_status == MiniAppPurchase.PAYMENT_REFUNDED:
        return 'refunded'
    if purchase.payment_status == MiniAppPurchase.PAYMENT_SUCCESS:
        return 'paid'
    if _debt_changed(purchase):
        return 'outdated'
    return 'open'


def _context(request, purchase, error=''):
    invoices = sorted(purchase.multicard_invoices.all(), key=lambda item: item.pk)
    open_invoice = next((item for item in invoices if item.state in OPEN_INVOICE_STATES), None)
    needs_contract = _needs_contract(purchase)
    paid = [item for item in invoices if item.state == 'success']
    return {
        'purchase': purchase,
        'state': _state(purchase),
        'error': error,
        'is_debt': bool(purchase.crm_transaction_id),
        'gross': purchase.total_amount + purchase.discount_per_person * purchase.participant_count
        + purchase.social_discount_amount,
        'members': [member.full_name for member in purchase.members.all()],
        'teachers': ', '.join(teacher.full_name for teacher in purchase.group.teachers.all()) if purchase.group else '',
        'open_invoice': open_invoice,
        'open_amount': Decimal(open_invoice.amount) / 100 if open_invoice else None,
        'asset_version': _telegram_asset_version(),
        'busy': bool(open_invoice and open_invoice.state in ('creating', 'uncertain')),
        'continue_url': open_invoice.checkout_url if open_invoice and open_invoice.state in ('ready', 'error') else '',
        'receipts': [item.receipt_url for item in paid if item.receipt_url],
        'needs_contract': needs_contract,
        'contract_html': render_contract_document(purchase) if needs_contract else '',
        'contract_version': contract_version(purchase),
        'default_amount': purchase.minimum_payment if purchase.is_booking else purchase.payable_amount,
        'demo': settings.DEBUG,
    }


def _pay(request, purchase):
    if _state(purchase) != 'open':
        raise ValueError("Bu havola orqali to'lab bo'lmaydi. Sahifani yangilang.")
    if _needs_contract(purchase):
        version = contract_version(purchase)
        if request.POST.get('consent') != 'on' or request.POST.get('version') != version:
            raise ValueError("Shartnomani oxirigacha o'qib, roziligingizni belgilang.")
        record_acceptance(
            purchase.telegram_user, LegalAcceptance.DOCUMENT_CONTRACT, version,
            render_contract_document(purchase), request, purchase=purchase,
        )
    amount = request.POST.get('amount') if purchase.is_booking else None
    expected_paid = request.POST.get('expected_paid')
    page = reverse('main:payment_link', args=[purchase.link_token])
    if settings.DEBUG and request.POST.get('demo') == '1':
        # Local checks only: settle without Multicard.
        with transaction.atomic():
            locked = MiniAppPurchase.objects.select_for_update().get(pk=purchase.pk)
            if locked.is_booking:
                check_payment_version(locked, expected_paid)
            invoice = MulticardInvoice.objects.create(
                purchase=locked, store_id='demo', amount=to_tiyin(payment_amount(locked, amount)),
            )
            _settle(invoice, uuid.uuid4(), provider='demo')
        return redirect(page)
    invoice = get_or_create_invoice(purchase, amount, expected_paid, return_url=request.build_absolute_uri(page))
    return redirect(invoice.checkout_url)


@require_http_methods(['GET', 'POST'])
def payment_link(request, token):
    purchase = _purchase(token)
    error = ''
    if request.method == 'POST':
        try:
            return _pay(request, purchase)
        except (ValueError, MulticardError) as exc:
            error = str(exc)
            purchase = _purchase(token)
    response = render(request, 'pay/link.html', _context(request, purchase, error))
    response['Cache-Control'] = 'no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    # The link stays private: it is never sent as a referrer to another site.
    response['Referrer-Policy'] = 'same-origin'
    return response

