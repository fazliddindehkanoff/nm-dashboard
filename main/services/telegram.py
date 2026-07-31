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

import requests
from django.conf import settings

import qrcode

API_BASE = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 15


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


def _build_caption(transaction, client):
    operator = transaction.operator
    client_name = client.full_name if client else "-"
    operator_name = operator.full_name if operator else "-"
    payment_type = transaction.get_payment_type_display()
    amount = transaction.amount

    lines = [
        "🧾 <b>Yangi tasdiqlangan to'lov</b>",
        "",
        f"👤 Mijoz: {client_name}",
        f"🧑‍💼 Qabul qilgan xodim: {operator_name}",
        f"💳 To'lov turi: {payment_type}",
        f"💰 To'lov miqdori: {amount}",
    ]
    return "\n".join(lines)


def _send_qr_to_client(token, chat_id, transaction, client):
    qr_buffer = generate_qr_png(client.uuid)
    caption = _build_caption(transaction, client)

    url = API_BASE.format(token=token, method="sendPhoto")
    resp = requests.post(
        url,
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("qr.png", qr_buffer, "image/png")},
        timeout=_TIMEOUT,
    )

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if resp.status_code == 200 and payload.get("ok"):
        return True, None

    description = payload.get("description") or f"HTTP {resp.status_code}"
    return False, f"{client.full_name}: {description}"


def send_payment_qr(transaction):
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

    clients = list(transaction.clients.all())
    if not clients:
        return False, "To'lovga mijoz biriktirilmagan."

    failures = []
    for client in clients:
        ok, failure_detail = _send_qr_to_client(token, chat_id, transaction, client)
        if not ok:
            failures.append(failure_detail)

    if not failures:
        return True, "Yuborildi."
    return False, "Telegram xatosi: " + "; ".join(failures)
