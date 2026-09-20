from datetime import timedelta
from decimal import Decimal
from django.db.models import F
from django.utils import timezone
from main.models import PaymentQRDelivery
from .telegram import generate_qr_png, send_bot_photo


def process_payment_notifications(limit=8):
    for delivery in PaymentQRDelivery.objects.filter(sent_at__isnull=True, next_attempt_at__lte=timezone.now()).select_related(
        'invoice__purchase__telegram_user', 'member__client', 'invoice__purchase__course',
    ).order_by('id')[:limit]:
        # An atomic lease prevents concurrent workers from sending the same row.
        claimed = PaymentQRDelivery.objects.filter(pk=delivery.pk, sent_at__isnull=True, next_attempt_at__lte=timezone.now()).update(
            next_attempt_at=timezone.now() + timedelta(minutes=5), attempts=F('attempts') + 1)
        if not claimed:
            continue
        invoice, member = delivery.invoice, delivery.member
        if invoice.state != 'success' or not member.client_id:
            PaymentQRDelivery.objects.filter(pk=delivery.pk).update(sent_at=timezone.now(), last_error='Payment no longer valid')
            continue
        purchase = invoice.purchase
        money = lambda value: f'{value:,.0f}'.replace(',', ' ') + ' so‘m'
        caption = (f'To‘lov muvaffaqiyatli!\n{purchase.course.name}\n{member.full_name}\n'
                   f'Ushbu to‘lov: {money(Decimal(invoice.amount) / 100)}\n'
                   f'Xarid bo‘yicha jami to‘langan: {money(purchase.paid_amount)}\n'
                   f'Qolgan qarz: {money(purchase.remaining_amount)}\n'
                   'Darsga kelganda ushbu QR-kodni ko‘rsating.')
        try:
            with generate_qr_png(str(member.client.uuid)) as photo:
                ok, error = send_bot_photo(purchase.telegram_user.telegram_id, photo, caption=caption[:1024])
        except Exception:
            ok, error = False, 'Telegram delivery failed'
        PaymentQRDelivery.objects.filter(pk=delivery.pk).update(
            sent_at=timezone.now() if ok else None, last_error='' if ok else str(error)[:500],
            next_attempt_at=timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** min(delivery.attempts, 7))))
