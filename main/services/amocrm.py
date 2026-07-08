"""amoCRM integratsiyasi.

`amocrm-api` (import nomi: ``amocrm``) kutubxonasi orqali amoCRM kontaktlarini
lokal `Client` modeliga sinxronlaydi. Kalitlar `settings.AMOCRM` orqali beriladi.
"""

from django.conf import settings
from django.utils import timezone


class AmoCRMNotConfigured(Exception):
    """amoCRM kalitlari sozlanmagan."""


def _get_config():
    cfg = getattr(settings, "AMOCRM", {}) or {}
    required = ("CLIENT_ID", "CLIENT_SECRET", "SUBDOMAIN", "REDIRECT_URL")
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        raise AmoCRMNotConfigured(
            "amoCRM sozlanmagan. Quyidagi muhit o'zgaruvchilarini bering: "
            + ", ".join("AMOCRM_" + key for key in missing)
        )
    return cfg


def _build_contact_model():
    """Telefon maydoni bilan kengaytirilgan Contact modelini qaytaradi."""
    from amocrm.v2 import Contact as BaseContact, custom_field

    class Contact(BaseContact):
        phone = custom_field.ContactPhoneField("Телефон")

    return Contact


def _init_tokens(cfg):
    from amocrm.v2 import tokens

    storage = tokens.FileTokensStorage(directory_path=str(settings.BASE_DIR))
    manager = tokens.default_token_manager(
        client_id=cfg["CLIENT_ID"],
        client_secret=cfg["CLIENT_SECRET"],
        subdomain=cfg["SUBDOMAIN"],
        redirect_url=cfg["REDIRECT_URL"],
        storage=storage,
    )
    # Birinchi ulanishda authorization code kerak bo'ladi. Keyingi safar
    # saqlangan tokenlardan foydalaniladi.
    if cfg.get("AUTH_CODE"):
        manager.init(code=cfg["AUTH_CODE"], skip_error=True)
    return manager


def _extract_phone(contact):
    """Kontaktdan telefon raqamini iloji boricha ajratib oladi."""
    try:
        phone = getattr(contact, "phone", None)
        if phone:
            return str(phone)
    except Exception:
        pass
    return ""


def sync_contacts(logger=None):
    """amoCRM kontaktlarini `Client` modeliga sinxronlaydi.

    `amocrm_id` bo'yicha moslashtiradi: mavjud bo'lsa yangilaydi, bo'lmasa yaratadi.
    Yaratilgan va yangilangan mijozlar sonini qaytaradi.
    """
    from main.models import Client

    cfg = _get_config()
    _init_tokens(cfg)
    Contact = _build_contact_model()

    created = 0
    updated = 0

    for contact in Contact.objects.all():
        amocrm_id = getattr(contact, "id", None)
        if amocrm_id is None:
            continue

        full_name = (getattr(contact, "name", None) or "").strip()
        if not full_name:
            first = (getattr(contact, "first_name", "") or "").strip()
            last = (getattr(contact, "last_name", "") or "").strip()
            full_name = (f"{last} {first}").strip() or f"amoCRM #{amocrm_id}"

        phone = _extract_phone(contact)

        client, was_created = Client.objects.update_or_create(
            amocrm_id=amocrm_id,
            defaults={
                "full_name": full_name,
                "phone_number": phone or "",
                "synced_at": timezone.now(),
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1
        if logger:
            logger(f"{'yaratildi' if was_created else 'yangilandi'}: {full_name}")

    return {"created": created, "updated": updated}
