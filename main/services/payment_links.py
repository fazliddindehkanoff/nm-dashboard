"""Payment links that staff create for a client to pay in any browser.

A course link sells a course to the client like the web app does. A debt link
collects the balance of an earlier sale: a web app purchase is shared as it is,
and a CRM payment receives the online payment as an approved sub-payment.
"""

import re
import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from main.models import (
    MiniAppCartItem,
    MiniAppPurchase,
    MiniAppPurchaseMember,
    Operator,
    SubTransaction,
    TelegramUser,
    Transaction,
    TransactionClient,
)
from .mini_app import _close, can_cancel, normalise_phone, quote


def client_seller(client, creator=None):
    """Who is credited for a link sale: the client's referral seller, their CRM seller, then the creator."""
    sellers = Operator.objects.filter(role='operator', user__is_active=True)
    account = TelegramUser.objects.filter(
        _accounts_of(client), referrer__in=sellers,
    ).select_related('referrer').order_by('created_at').first()
    if account:
        return account.referrer
    if client.operator_id and sellers.filter(pk=client.operator_id).exists():
        return client.operator
    return sellers.filter(user=creator).first() if creator else None


def _accounts_of(client):
    query = Q(client=client)
    phone = re.sub(r'\D', '', client.phone_number or '')
    if len(phone) >= 9:
        query |= Q(phone_number__endswith=phone[-9:])
    return query


def client_chat_id(client_id):
    """Telegram chat of a CRM client, if they use the bot."""
    return TelegramUser.objects.filter(client_id=client_id).values_list('telegram_id', flat=True).first()


def _buyer(client):
    try:
        phone = normalise_phone(client.phone_number)
    except ValueError:
        raise ValueError("Mijozning telefon raqami noto'g'ri. Avval uni +998 XX XXX XX XX ko'rinishida tuzating.")
    return {
        'full_name': client.full_name,
        'phone_number': phone,
        'relationship': MiniAppPurchaseMember.RELATION_SELF,
        'eligibility_document_id': None,
    }


def create_course_link(client, group, payment_mode, creator):
    """A course order for the client that they pay on the link page."""
    if not group.is_active or group.start_date <= timezone.localdate():
        raise ValueError("Bu guruh boshlangan yoki faol emas. Boshqa guruhni tanlang.")
    joined = TransactionClient.objects.filter(
        client=client, transaction__group=group, transaction__is_refunded=False,
    ).exists() or MiniAppPurchase.objects.filter(
        members__client=client, group=group, crm_transaction__isnull=True,
        payment_status__in=(MiniAppPurchase.PAYMENT_PENDING, MiniAppPurchase.PAYMENT_PARTIAL, MiniAppPurchase.PAYMENT_SUCCESS),
    ).exists()
    if joined:
        raise ValueError(
            "Mijoz bu guruhga allaqachon yozilgan yoki unga havola yuborilgan. "
            "Qarz bo'lsa, «Qarzni to'lash uchun» bo'limidan foydalaning."
        )
    participant = _buyer(client)
    price = quote(group.course, [participant], payment_mode)
    booking = payment_mode == MiniAppCartItem.MODE_BOOKING
    if booking and not price['booking_available']:
        raise ValueError("Bu kurs uchun bron summasi yetarli emas. To'liq to'lovni tanlang.")
    with db_transaction.atomic():
        purchase = MiniAppPurchase.objects.create(
            telegram_user=None,
            link_token=uuid.uuid4(),
            created_by=creator,
            referrer=client_seller(client, creator),
            course=group.course,
            group=group,
            purchase_type=MiniAppPurchase.TYPE_SELF,
            unit_price=price['unit_price'],
            discount_per_person=price['discount_per_person'],
            discount_name=price['discount_name'],
            participant_count=1,
            total_amount=price['total'],
            is_booking=booking,
            booking_discount=price['booking_discount'],
        )
        MiniAppPurchaseMember.objects.create(purchase=purchase, client=client, **participant)
    return purchase


def open_debts(client):
    """Balances the client still owes, one row per group."""
    rows = TransactionClient.objects.filter(
        client=client, debt__gt=0, transaction__is_confirmed=True, transaction__is_refunded=False,
        transaction__group__isnull=False,
    ).select_related('transaction__group__course', 'transaction__mini_app_purchase').order_by('-transaction__date', '-id')
    return list(rows)


def parse_amount(value, maximum):
    try:
        amount = Decimal(str(value).replace(' ', '').replace(',', '.')) if value not in (None, '') else maximum
    except InvalidOperation:
        raise ValueError("Summani raqam bilan kiriting.")
    if amount <= 0 or amount > maximum or amount != amount.quantize(Decimal('0.01')):
        raise ValueError("Summa 0 dan katta va qarzdan oshmasligi kerak.")
    return amount


def create_debt_link(client, participation_id, amount, creator):
    """A link that pays what the client still owes for one group."""
    row = next((item for item in open_debts(client) if item.pk == participation_id), None)
    if row is None:
        raise ValueError("Bu qarz topilmadi yoki allaqachon to'langan.")
    amount = parse_amount(amount, row.debt)
    payment = row.transaction
    if payment.mini_app_purchase_id:
        # A web app purchase already takes installments, so it is shared instead of a second order.
        return share(payment.mini_app_purchase)
    try:
        phone = normalise_phone(client.phone_number)
    except ValueError:
        phone = client.phone_number
    with db_transaction.atomic():
        # A newer link replaces unpaid older ones, so one debt cannot be paid twice.
        older = MiniAppPurchase.objects.select_for_update().filter(
            crm_transaction=payment,
            payment_status__in=(MiniAppPurchase.PAYMENT_PENDING, MiniAppPurchase.PAYMENT_FAILED),
        ).prefetch_related('multicard_invoices')
        for item in older:
            if can_cancel(item):
                _close(item)
        purchase = MiniAppPurchase.objects.create(
            telegram_user=None,
            link_token=uuid.uuid4(),
            created_by=creator,
            crm_transaction=payment,
            course=payment.group.course,
            group=payment.group,
            purchase_type=MiniAppPurchase.TYPE_SELF,
            unit_price=amount,
            participant_count=1,
            total_amount=amount,
        )
        MiniAppPurchaseMember.objects.create(
            purchase=purchase, client=client, full_name=client.full_name, phone_number=phone,
            relationship=MiniAppPurchaseMember.RELATION_SELF,
        )
    return purchase


def share(purchase):
    """Give an existing purchase a payment link."""
    if not purchase.link_token:
        MiniAppPurchase.objects.filter(pk=purchase.pk, link_token__isnull=True).update(link_token=uuid.uuid4())
        purchase.refresh_from_db(fields=['link_token'])
    return purchase


def crm_debt(purchase):
    """What the client still owes on the CRM payment a debt link pays."""
    member = next(iter(purchase.members.all()), None)
    if not member or not member.client_id:
        return Decimal(0)
    rows = TransactionClient.objects.filter(
        client_id=member.client_id, transaction__group_id=purchase.crm_transaction.group_id,
        transaction__is_confirmed=True, transaction__is_refunded=False,
    )
    return sum((row.debt for row in rows), Decimal(0))


def record_crm_installment(purchase, invoice):
    """Add a paid debt link to its CRM payment as an approved sub-payment."""
    if SubTransaction.objects.filter(multicard_invoice=invoice).exists():
        return
    payment = Transaction.objects.select_for_update().get(pk=purchase.crm_transaction_id)
    amount = Decimal(invoice.amount) / 100
    sub = SubTransaction.objects.create(
        transaction=payment,
        amount=amount,
        payment_method='rahmat',
        status=SubTransaction.STATUS_APPROVED,
        received_by=purchase.created_by,
        reviewed_by=purchase.created_by,
        reviewed_at=timezone.now(),
        review_note="To'lov havolasi orqali onlayn to'lov (Multicard)",
        multicard_invoice=invoice,
    )
    sub.clients.set([member.client_id for member in purchase.members.all() if member.client_id])
    getattr(payment, '_prefetched_objects_cache', {}).pop('sub_transactions', None)
    payment.amount = (payment.amount or Decimal(0)) + amount
    payment.save()


def reverse_crm_installment(invoice):
    """A refunded debt link payment no longer reduces the CRM debt."""
    sub = SubTransaction.objects.select_for_update().filter(
        multicard_invoice=invoice, status=SubTransaction.STATUS_APPROVED,
    ).first()
    if sub is None:
        return
    SubTransaction.objects.filter(pk=sub.pk).update(
        status=SubTransaction.STATUS_REJECTED, reviewed_at=timezone.now(),
        review_note="Multicard to'lovni qaytardi",
    )
    payment = Transaction.objects.select_for_update().get(pk=sub.transaction_id)
    getattr(payment, '_prefetched_objects_cache', {}).pop('sub_transactions', None)
    payment.amount = max((payment.amount or Decimal(0)) - sub.amount, Decimal(0))
    payment.save()
