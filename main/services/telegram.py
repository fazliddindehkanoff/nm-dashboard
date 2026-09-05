"""Telegram bot integratsiyasi.

To'lov tasdiqlanganda mijozning UUID si kodlangan QR kod rasmi yaratiladi va
bitta umumiy Telegram guruh chatiga yuboriladi (barcha guruhlar uchun bir xil
chat). Rasm izohida (caption) mijoz ismi, to'lovni qabul qilgan xodim, to'lov
turi va miqdori ko'rsatiladi.

Token `settings.TELEGRAM["BOT_TOKEN"]`, chat ID esa `settings.TELEGRAM["CHAT_ID"]`
orqali beriladi. Telegramga so'rovlar mavjud `requests` kutubxonasi orqali Bot
API HTTP endpointlariga yuboriladi — qo'shimcha og'ir bog'liqlik talab qilinmaydi.
"""

from io import BytesIO
import json
import socket
from contextlib import contextmanager
from threading import RLock

import requests
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils.html import escape

import qrcode

API_BASE = "https://api.telegram.org/bot{token}/{method}"
_DNS_LOCK = RLock()


class TelegramNotConfigured(Exception):
    """Telegram bot tokeni sozlanmagan."""


def _get_token():
    token = (getattr(settings, "TELEGRAM", {}) or {}).get("BOT_TOKEN")
    if not token:
        raise TelegramNotConfigured(
            "Telegram sozlanmagan. TELEGRAM_BOT_TOKEN muhit o'zgaruvchisini bering."
        )
    return token


def _get_chat_id():
    chat_id = (getattr(settings, "TELEGRAM", {}) or {}).get("CHAT_ID")
    if not chat_id:
        raise TelegramNotConfigured(
            "Telegram guruh chati sozlanmagan. TELEGRAM_CHAT_ID muhit o'zgaruvchisini bering."
        )
    return chat_id


@contextmanager
def _telegram_address_family():
    """Prefer Telegram's IPv6 address on hosts with a broken Telegram IPv4 route."""
    prefer_ipv6 = (getattr(settings, "TELEGRAM", {}) or {}).get("PREFER_IPV6", False)
    if not prefer_ipv6:
        yield
        return

    with _DNS_LOCK:
        original_getaddrinfo = socket.getaddrinfo

        def telegram_getaddrinfo(host, port, family=0, socktype=0, proto=0, flags=0):
            results = original_getaddrinfo(host, port, family, socktype, proto, flags)
            if host == 'api.telegram.org':
                ipv6_results = [result for result in results if result[0] == socket.AF_INET6]
                return ipv6_results or results
            return results

        socket.getaddrinfo = telegram_getaddrinfo
        try:
            yield
        finally:
            socket.getaddrinfo = original_getaddrinfo


def telegram_api_request(method, **kwargs):
    """Call the Bot API using the configured address preference."""
    token = _get_token()
    with _telegram_address_family():
        return requests.post(API_BASE.format(token=token, method=method), **kwargs)


def send_bot_message(chat_id, text, reply_markup=None):
    """Send a plain bot message and return ``(ok, detail)``.

    This lightweight helper is shared by webhook onboarding and attendance
    notifications so the project does not need a second Telegram library.
    """
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

    try:
        response = telegram_api_request(
            "sendMessage",
            data=payload,
            timeout=(2, 10),
        )
        result = response.json()
    except requests.RequestException as exc:
        return False, f"Telegram tarmoq xatosi: {exc}"
    except ValueError:
        return False, f"Telegram noto'g'ri javob qaytardi (HTTP {response.status_code})."
    if response.status_code == 200 and result.get("ok"):
        return True, None
    return False, result.get("description") or f"HTTP {response.status_code}"


def send_bot_photo(chat_id, photo_file):
    """Send a photo file. Network and response errors are returned, never raised."""
    try:
        response = telegram_api_request(
            "sendPhoto",
            data={'chat_id': chat_id},
            files={'photo': photo_file},
            timeout=(2, 10),
        )
        result = response.json()
    except TelegramNotConfigured:
        raise
    except requests.RequestException as exc:
        return False, f"Telegram tarmoq xatosi: {exc}"
    except ValueError:
        return False, f"Telegram noto'g'ri javob qaytardi (HTTP {response.status_code})."
    if response.status_code == 200 and result.get('ok'):
        return True, None
    return False, result.get('description') or f"HTTP {response.status_code}"


def send_attendance_notification(lesson):
    """Notify the Telegram purchaser when a participant's attendance is marked."""
    from main.models import TelegramUser

    record = lesson.attendance
    client = record.client
    recipients = TelegramUser.objects.filter(
        models.Q(client=client)
        | models.Q(purchases__members__client=client)
    ).distinct()
    if not recipients:
        return False, "Telegram foydalanuvchi topilmadi."

    group = record.group
    day_number = (lesson.date - group.start_date).days + 1
    status_label = lesson.get_status_display()
    status_icons = {
        "attended": "✅",
        "absent": "❌",
        "excused": "🟡",
        "late": "🕒",
        "unmarked": "⚪️",
    }
    message = (
        f"{status_icons.get(lesson.status, '📋')} <b>Davomat yangilandi</b>\n\n"
        f"👤 {escape(client.full_name)}\n"
        f"📚 {escape(group.course.name)}\n"
        f"🗓 {day_number}-kun\n"
        f"Holat: <b>{escape(str(status_label))}</b>"
    )
    if lesson.reason:
        message += f"\nSabab: {escape(lesson.reason)}"

    failures = []
    for recipient in recipients:
        ok, detail = send_bot_message(recipient.telegram_id, message)
        if not ok:
            failures.append(detail)
    return not failures, "; ".join(filter(None, failures)) or None


def generate_qr_png(data):
    """Berilgan matndan QR kod PNG baytlarini (BytesIO) qaytaradi."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(str(data))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    buffer.name = "qr.png"
    return buffer


def _user_name(user):
    if not user:
        return "—"
    try:
        return user.operator.full_name
    except (AttributeError, ObjectDoesNotExist):
        return user.get_full_name() or user.username


def _format_amount(amount):
    return f"{amount:,.2f}".replace(",", " ") + " UZS"


def _build_caption(transaction, client, sub_transaction=None):
    client_name = client.full_name if client else "-"
    group_name = str(transaction.group) if transaction.group else "—"

    if sub_transaction is None:
        title = "Tasdiqlangan to'lov"
        receiver_name = transaction.operator.full_name if transaction.operator else "—"
        approver_name = _user_name(transaction.confirmed_by)
        payment_type = transaction.get_payment_type_display()
        amount = transaction.amount
        payment_id = f"#{transaction.pk}"
    else:
        title = "Tasdiqlangan ichki to'lov"
        receiver_name = sub_transaction.receiver_name
        approver_name = sub_transaction.reviewer_name
        payment_type = sub_transaction.get_payment_method_display()
        amount = sub_transaction.amount
        payment_id = f"#{transaction.pk}.{sub_transaction.pk}"

    lines = [
        f"🧾 <b>{title}</b>",
        "",
        f"🔖 To'lov ID: {escape(payment_id)}",
        f"👤 Mijoz: {escape(client_name)}",
        f"📚 Guruh / kurs: {escape(group_name)}",
        f"🧑‍💼 Qabul qildi: {escape(receiver_name)}",
        f"✅ Tasdiqladi: {escape(approver_name)}",
        f"💳 To'lov turi: {escape(payment_type)}",
        f"💰 To'lov miqdori: <b>{escape(_format_amount(amount))}</b>",
    ]
    return "\n".join(lines)


def _send_qr_to_client(token, chat_id, transaction, client, sub_transaction=None):
    qr_buffer = generate_qr_png(client.uuid)
    caption = _build_caption(transaction, client, sub_transaction=sub_transaction)

    resp = telegram_api_request(
        "sendPhoto",
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("qr.png", qr_buffer, "image/png")},
        timeout=(2, 10),
    )

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if resp.status_code == 200 and payload.get("ok"):
        return True, None

    description = payload.get("description") or f"HTTP {resp.status_code}"
    return False, f"{client.full_name}: {description}"


def send_payment_qr(transaction, sub_transaction=None):
    """To'lovga biriktirilgan har bir mijoz uchun alohida QR kodni umumiy
    Telegram guruh chatiga yuboradi.

    Har bir QR kod shu mijozning UUID sini kodlaydi. Barcha mijozlar uchun
    bitta umumiy chatga (`settings.TELEGRAM["CHAT_ID"]`) yuboriladi.

    Returns:
        (ok: bool, detail: str) — `ok` faqat barcha mijozlarga muvaffaqiyatli
        yuborilganda True bo'ladi; `detail` muvaffaqiyatsiz bo'lganlarni
        nomlaydi.
    """
    token = _get_token()
    chat_id = _get_chat_id()

    clients = list(
        sub_transaction.clients.all() if sub_transaction is not None else transaction.clients.all()
    )
    if not clients:
        return False, "To'lovga mijoz biriktirilmagan."

    failures = []
    for client in clients:
        ok, failure_detail = _send_qr_to_client(
            token, chat_id, transaction, client, sub_transaction=sub_transaction,
        )
        if not ok:
            failures.append(failure_detail)

    if not failures:
        return True, "Yuborildi."
    return False, "Telegram xatosi: " + "; ".join(failures)
