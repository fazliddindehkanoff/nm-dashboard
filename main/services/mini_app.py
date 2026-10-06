"""Telegram web app cart, checkout and group payment bookkeeping."""

import re
import uuid
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from main.models import (
    Client,
    Discount,
    EligibilityDocument,
    Group,
    MiniAppCartItem,
    MiniAppCartMember,
    MiniAppPurchase,
    MiniAppPurchaseMember,
    PaymentSettings,
    Transaction,
    TransactionClient,
    _recalc_transaction_participants,
)
from .booking import booking_discount_for

MAX_FAMILY_MEMBERS = 7
SOCIAL_DISCOUNT = Decimal(100000)


class CheckoutError(ValueError):
    pass


def digits(value):
    return re.sub(r'\D', '', value or '')


def normalise_phone(value):
    result = digits(value)
    if len(result) == 9:
        result = '998' + result
    if len(result) != 12 or not result.startswith('998'):
        raise ValueError("Telefon raqamini +998 XX XXX XX XX ko'rinishida kiriting.")
    return '+' + result


def find_client_by_phone(phone):
    target = digits(phone)
    for client in Client.objects.only('id', 'phone_number', 'full_name'):
        if digits(client.phone_number) == target:
            return client
    return None


def upcoming_groups_by_course(course_ids):
    groups = {}
    queryset = (
        Group.objects.upcoming().filter(course_id__in=course_ids)
        .prefetch_related('teachers').order_by('start_date', 'id')
    )
    for group in queryset:
        groups.setdefault(group.course_id, []).append(group)
    return groups


def build_participants(account, purchase_type, members, self_document_id=None):
    """Validate the buyer and family members submitted from the web app."""
    if purchase_type not in dict(MiniAppPurchase.PURCHASE_TYPES):
        raise ValueError("Xarid turini tanlang.")
    if not account.full_name or not account.phone_number:
        raise ValueError("Avval Telegram botdagi ism va kontakt bosqichini yakunlang.")
    participants = [{
        'full_name': account.full_name,
        'phone_number': normalise_phone(account.phone_number),
        'relationship': MiniAppPurchaseMember.RELATION_SELF,
        'eligibility_document_id': self_document_id,
    }]
    if purchase_type == MiniAppPurchase.TYPE_FAMILY:
        family_members = members or []
        if not family_members:
            raise ValueError("Kamida bitta oila a'zosini qo'shing.")
        if len(family_members) > MAX_FAMILY_MEMBERS:
            raise ValueError("Bitta xaridda ko'pi bilan 8 kishi qatnashishi mumkin.")
        for member in family_members:
            if not isinstance(member, dict):
                raise ValueError("Oila a'zosi ma'lumoti noto'g'ri.")
            full_name = (member.get('full_name') or '').strip()
            if len(full_name) < 3:
                raise ValueError("Har bir oila a'zosining to'liq ismini kiriting.")
            participants.append({
                'full_name': full_name[:255],
                'phone_number': normalise_phone(member.get('phone_number')),
                'relationship': MiniAppPurchaseMember.RELATION_FAMILY,
                'eligibility_document_id': member.get('eligibility_document_id'),
            })
    phones = [item['phone_number'] for item in participants]
    if len(set(phones)) != len(phones):
        raise ValueError("Bir telefon raqamini ikki marta qo'shib bo'lmaydi.")
    return participants


def validate_documents(account, participants):
    """Each social discount needs this account's proof for that participant."""
    for participant in participants:
        proof_id = participant['eligibility_document_id']
        if not proof_id:
            participant['eligibility_document_id'] = None
            continue
        if not isinstance(proof_id, int) or not EligibilityDocument.objects.filter(
            pk=proof_id, telegram_user=account, phone_number=participant['phone_number'],
        ).exists():
            raise ValueError("Chegirma uchun shu ishtirokchining tasdiqlovchi hujjatini yuboring.")


def quote(course, participants, payment_mode):
    """Server-side price for one course, following the checkout discount rules."""
    if payment_mode not in dict(MiniAppCartItem.PAYMENT_MODES):
        raise ValueError("To'lov usulini tanlang.")
    count = len(participants)
    rule = Discount.participant_discount(course.id, count)
    discount_per_person = min(rule.amount, course.price) if rule else Decimal(0)
    unit_price = course.price - discount_per_person
    eligible = sum(1 for participant in participants if participant['eligibility_document_id'])
    social_discount = min(SOCIAL_DISCOUNT, unit_price) * eligible
    total = unit_price * count - social_discount
    booking_discount = min(booking_discount_for(unit_price, count, course.id), total)
    minimum_booking = PaymentSettings.booking_minimum() * count
    return {
        'discount_name': rule.name if rule else '',
        'discount_per_person': discount_per_person,
        'unit_price': unit_price,
        'social_discount': social_discount,
        'total': total,
        'booking_discount': booking_discount if payment_mode == MiniAppCartItem.MODE_BOOKING else Decimal(0),
        'booking_available': total - booking_discount >= minimum_booking,
        'minimum_booking': minimum_booking,
    }


def create_purchase(account, course, purchase_type, participants, payment_mode, group=None, checkout_batch=None):
    """Snapshot prices and participants into an unpaid purchase."""
    validate_documents(account, participants)
    price = quote(course, participants, payment_mode)
    booking = payment_mode == MiniAppCartItem.MODE_BOOKING
    if booking and not price['booking_available']:
        raise ValueError("Bu kurs uchun bron summasi yetarli emas. To'liq to'lovni tanlang.")
    purchase = MiniAppPurchase.objects.create(
        telegram_user=account,
        referrer=account.referrer,
        social_discount_amount=price['social_discount'],
        course=course,
        group=group,
        checkout_batch=checkout_batch,
        purchase_type=purchase_type,
        unit_price=price['unit_price'],
        discount_per_person=price['discount_per_person'],
        discount_name=price['discount_name'],
        participant_count=len(participants),
        total_amount=price['total'],
        is_booking=booking,
        booking_discount=price['booking_discount'],
    )
    MiniAppPurchaseMember.objects.bulk_create([
        MiniAppPurchaseMember(purchase=purchase, **participant) for participant in participants
    ])
    return purchase


def save_cart_item(account, course, group, purchase_type, members, self_document_id, payment_mode):
    """Add a course to the cart or update the existing entry for it."""
    participants = build_participants(account, purchase_type, members, self_document_id)
    validate_documents(account, participants)
    price = quote(course, participants, payment_mode)
    if payment_mode == MiniAppCartItem.MODE_BOOKING and not price['booking_available']:
        raise ValueError("Bu kurs uchun bron summasi yetarli emas. To'liq to'lovni tanlang.")
    with db_transaction.atomic():
        item, _ = MiniAppCartItem.objects.update_or_create(
            telegram_user=account, course=course,
            defaults={
                'group': group,
                'purchase_type': purchase_type,
                'payment_mode': payment_mode,
                'eligibility_document_id': participants[0]['eligibility_document_id'],
            },
        )
        item.members.all().delete()
        MiniAppCartMember.objects.bulk_create([
            MiniAppCartMember(
                item=item, full_name=participant['full_name'], phone_number=participant['phone_number'],
                eligibility_document_id=participant['eligibility_document_id'],
            )
            for participant in participants[1:]
        ])
    return item


def cart_participants(account, item):
    members = [
        {
            'full_name': member.full_name,
            'phone_number': member.phone_number,
            'eligibility_document_id': member.eligibility_document_id,
        }
        for member in item.members.all()
    ]
    return build_participants(account, item.purchase_type, members, item.eligibility_document_id)


def checkout_cart(account, items):
    """Turn the selected cart items into unpaid purchases, one per course."""
    if not items:
        raise CheckoutError("To'lov uchun kamida bitta kursni tanlang.")
    groups = upcoming_groups_by_course([item.course_id for item in items])
    for item in items:
        if item.group_id not in {group.id for group in groups.get(item.course_id, [])}:
            raise CheckoutError(
                f"«{item.course.name}» uchun tanlangan guruh endi mavjud emas. Boshqa guruhni tanlang."
            )
    batch = uuid.uuid4()
    with db_transaction.atomic():
        purchases = [
            create_purchase(
                account, item.course, item.purchase_type, cart_participants(account, item),
                item.payment_mode, group=item.group, checkout_batch=batch,
            )
            for item in items
        ]
        MiniAppCartItem.objects.filter(pk__in=[item.pk for item in items]).delete()
    return purchases


def can_cancel(purchase, invoices=None):
    invoices = purchase.multicard_invoices.all() if invoices is None else invoices
    return (
        purchase.payment_status in (MiniAppPurchase.PAYMENT_PENDING, MiniAppPurchase.PAYMENT_FAILED)
        and not purchase.paid_amount
        # Ambiguous invoices must be reconciled by staff first.
        and not any(invoice.state in ('creating', 'uncertain', 'success') for invoice in invoices)
    )


def return_to_cart(purchase):
    """Put an unpaid purchase back into the cart; the purchase is closed.

    A late payment for a returned purchase is still recorded by the callback.
    """
    with db_transaction.atomic():
        purchase = MiniAppPurchase.objects.select_for_update().get(pk=purchase.pk)
        if not can_cancel(purchase):
            raise ValueError("Bu xaridni savatga qaytarib bo'lmaydi. To'lov holatini tekshiring.")
        _close(purchase)
        existing = MiniAppCartItem.objects.filter(telegram_user=purchase.telegram_user, course=purchase.course).first()
        if existing:
            # The course was added again after checkout; keep the newer choice.
            return existing
        members = list(purchase.members.order_by('id'))
        buyer = next((member for member in members if member.relationship == MiniAppPurchaseMember.RELATION_SELF), None)
        item = MiniAppCartItem.objects.create(
            telegram_user=purchase.telegram_user,
            course=purchase.course,
            group=purchase.group,
            purchase_type=purchase.purchase_type,
            payment_mode=MiniAppCartItem.MODE_BOOKING if purchase.is_booking else MiniAppCartItem.MODE_FULL,
            eligibility_document_id=buyer.eligibility_document_id if buyer else None,
        )
        MiniAppCartMember.objects.bulk_create([
            MiniAppCartMember(
                item=item, full_name=member.full_name, phone_number=member.phone_number,
                eligibility_document_id=member.eligibility_document_id,
            )
            for member in members if member.relationship != MiniAppPurchaseMember.RELATION_SELF
        ])
    return item


def _close(purchase):
    MiniAppPurchase.objects.filter(pk=purchase.pk).update(
        payment_status=MiniAppPurchase.PAYMENT_CANCELLED, updated_at=timezone.now(),
    )


def cancel_purchase(purchase):
    """Cancel an unpaid purchase; a late payment is still recorded by the callback."""
    with db_transaction.atomic():
        purchase = MiniAppPurchase.objects.select_for_update().get(pk=purchase.pk)
        if not can_cancel(purchase):
            raise ValueError("Bu xaridni bekor qilib bo'lmaydi. To'lov holatini tekshiring.")
        _close(purchase)


def cancel_older_unpaid(purchase):
    """Buying a course again replaces the buyer's earlier unpaid order for it."""
    older = MiniAppPurchase.objects.select_for_update().filter(
        telegram_user_id=purchase.telegram_user_id,
        course_id=purchase.course_id,
        payment_status__in=(MiniAppPurchase.PAYMENT_PENDING, MiniAppPurchase.PAYMENT_FAILED),
    ).exclude(pk=purchase.pk).prefetch_related('multicard_invoices')
    for item in older:
        if can_cancel(item):
            _close(item)


def sync_group_payment(purchase):
    """Mirror the web app balance as one confirmed payment in the chosen group.

    The CRM payment amount follows every settled installment and refund, so the
    group's payments, attendance and debt match the purchase.
    """
    purchase = MiniAppPurchase.objects.select_related('group__course').get(pk=purchase.pk)
    if purchase.crm_transaction_id:
        return None  # A debt link is recorded on its CRM payment, not as a new course sale.
    payment = Transaction.objects.filter(mini_app_purchase=purchase).first()
    if not purchase.group_id or (payment is None and purchase.paid_amount <= 0):
        return payment
    refunded = purchase.paid_amount <= 0
    if payment is None:
        paid_at = (
            purchase.multicard_invoices.filter(state='success', paid_at__isnull=False)
            .order_by('paid_at', 'id').values_list('paid_at', flat=True).first()
            or purchase.paid_at or timezone.now()
        )
        payment = Transaction(
            mini_app_purchase=purchase,
            group=purchase.group,
            date=timezone.localdate(paid_at),
            payment_type='bron' if purchase.is_booking else 'to_liq_tolov',
            payment_method='rahmat',
            source=Transaction.SOURCE_TELEGRAM_APP,
            source_detail='Rahmat (web app)',
            is_confirmed=True,
            confirmed_at=paid_at,
        )
    if not refunded:
        payment.amount = purchase.paid_amount
    payment.is_refunded = refunded
    payment.refunded_at = (payment.refunded_at or timezone.localdate()) if refunded else None
    payment.save()
    for member in purchase.members.all():
        if member.client_id:
            TransactionClient.objects.get_or_create(transaction=payment, client_id=member.client_id)
    _recalc_transaction_participants(payment)
    return payment
