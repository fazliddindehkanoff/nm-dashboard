"""amoCRM integratsiyasi (API v4, uzoq muddatli token).

Yengil `requests` asosidagi mijoz. Barcha so'rovlar `Authorization: Bearer <token>`
va 5 soniyalik timeout bilan yuboriladi. Kalitlar `settings.AMOCRM` orqali beriladi:

    AMOCRM = {"SUBDOMAIN": "mycompany", "TOKEN": "<long-lived token>"}

Ochiq interfeys:
- ``find_lead_by_phone(phone)`` — telefon bo'yicha eng mos lead'ni topadi.
- ``close_lead(lead_id)`` — lead'ni "Muvaffaqiyatli yakunlandi" (142) ga o'tkazadi.
- ``link_client_to_amocrm(client)`` — mijozni topilgan lead bilan bog'laydi.
- ``sync_contacts(logger=None)`` — kontaktlarni `Client` modeliga import qiladi.

Xatolar: sozlanmagan bo'lsa ``AmoCRMNotConfigured``, tarmoq/HTTP xatolarida
``AmoCRMError``. Chaqiruvchilar bularni ushlab ogohlantirish xabariga aylantiradi —
lokal saqlash hech qachon bloklanmaydi.
"""

import re
from dataclasses import dataclass
from typing import Optional

import requests
from django.conf import settings
from django.utils import timezone

API_TIMEOUT = 5

# "Yakunlangan" bosqichlar: 142 = Muvaffaqiyatli, 143 = Muvaffaqiyatsiz.
STATUS_WON = 142
STATUS_LOST = 143
CLOSED_STATUSES = {STATUS_WON, STATUS_LOST}


class AmoCRMNotConfigured(Exception):
    """amoCRM kalitlari (SUBDOMAIN / TOKEN) sozlanmagan."""


class AmoCRMError(Exception):
    """amoCRM bilan bog'lanishda tarmoq yoki HTTP xatosi."""


@dataclass
class LeadMatch:
    contact_id: Optional[int]
    lead_id: Optional[int]
    is_active: bool
    # Kontakt ID allaqachon boshqa mijozda saqlangan bo'lsa True (faqat lead bog'landi).
    contact_conflict: bool = False


def _get_config():
    cfg = getattr(settings, "AMOCRM", {}) or {}
    subdomain = cfg.get("SUBDOMAIN")
    token = cfg.get("TOKEN")
    if not subdomain or not token:
        raise AmoCRMNotConfigured(
            "amoCRM sozlanmagan. AMOCRM_SUBDOMAIN va AMOCRM_TOKEN muhit "
            "o'zgaruvchilarini bering."
        )
    return subdomain, token


def _request(method, path, params=None, json=None):
    """amoCRM API v4 ga so'rov yuboradi.

    204 (bo'sh natija) uchun None qaytaradi. 401 va boshqa xatolar `AmoCRMError`
    ko'taradi.
    """
    subdomain, token = _get_config()
    url = "https://{sub}.amocrm.ru{path}".format(sub=subdomain, path=path)
    headers = {"Authorization": "Bearer {0}".format(token)}
    try:
        resp = requests.request(
            method, url, headers=headers, params=params, json=json, timeout=API_TIMEOUT
        )
    except requests.RequestException as exc:
        raise AmoCRMError("amoCRM bilan bog'lanib bo'lmadi: {0}".format(exc))

    if resp.status_code == 401:
        raise AmoCRMError("amoCRM token yaroqsiz — yangi token kiriting.")
    if resp.status_code == 204:
        return None
    if not resp.ok:
        raise AmoCRMError("amoCRM xatosi: HTTP {0}".format(resp.status_code))

    try:
        return resp.json()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Telefon normalizatsiyasi
# ---------------------------------------------------------------------------

def _normalize_phone(phone):
    """Telefonni oxirgi 9 raqamiga keltiradi. 9 tadan kam bo'lsa None."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 9:
        return None
    return digits[-9:]


def _contact_phones(contact):
    """Kontaktning telefon custom-field qiymatlarini qaytaradi."""
    phones = []
    for cf in contact.get("custom_fields_values") or []:
        if cf.get("field_code") == "PHONE":
            for v in cf.get("values") or []:
                value = v.get("value")
                if value:
                    phones.append(value)
    return phones


# ---------------------------------------------------------------------------
# Lead qidirish / yopish
# ---------------------------------------------------------------------------

def _fetch_leads(lead_ids):
    """Berilgan lead ID lar bo'yicha to'liq lead obyektlarini oladi."""
    if not lead_ids:
        return []
    params = [("filter[id][]", lid) for lid in lead_ids]
    data = _request("GET", "/api/v4/leads", params=params)
    if not data:
        return []
    return (data.get("_embedded") or {}).get("leads") or []


def find_lead_by_phone(phone):
    """Telefon raqami bo'yicha eng mos lead'ni topadi.

    Faol lead (yakunlanmagan) yangi sana bo'yicha ustun; faqat yopiq lead'lar
    bo'lsa, eng yangisi `is_active=False` bilan qaytariladi. Mos kontakt yoki
    lead topilmasa None qaytariladi.
    """
    key = _normalize_phone(phone)
    if not key:
        return None

    data = _request("GET", "/api/v4/contacts", params={"query": key, "with": "leads"})
    if not data:
        return None

    contacts = (data.get("_embedded") or {}).get("contacts") or []

    # amoCRM `query` fuzzy — kontaktda haqiqatan shu telefon borligini tekshiramiz.
    lead_ids = []
    contact_by_lead = {}
    for contact in contacts:
        if not any(_normalize_phone(p) == key for p in _contact_phones(contact)):
            continue
        for lead in (contact.get("_embedded") or {}).get("leads") or []:
            lid = lead.get("id")
            if lid:
                lead_ids.append(lid)
                contact_by_lead[lid] = contact.get("id")

    if not lead_ids:
        return None

    leads = _fetch_leads(lead_ids)
    if not leads:
        return None

    def sort_key(lead):
        active = lead.get("status_id") not in CLOSED_STATUSES
        created = lead.get("created_at") or 0
        return (active, created, lead.get("id") or 0)

    leads.sort(key=sort_key, reverse=True)
    best = leads[0]
    best_id = best.get("id")
    return LeadMatch(
        contact_id=contact_by_lead.get(best_id),
        lead_id=best_id,
        is_active=best.get("status_id") not in CLOSED_STATUSES,
    )


def close_lead(lead_id):
    """Lead'ni "Muvaffaqiyatli yakunlandi" (142) bosqichiga o'tkazadi (idempotent)."""
    _request(
        "PATCH",
        "/api/v4/leads/{0}".format(lead_id),
        json={"status_id": STATUS_WON},
    )


def link_client_to_amocrm(client):
    """Mijozni telefon bo'yicha topilgan amoCRM lead bilan bog'laydi.

    Kafolatlar:
    - Mavjud `amocrm_id` hech qachon qayta yozilmaydi.
    - Kontakt ID boshqa mijozda bo'lsa (bir telefon ikki mijozda), `amocrm_id`
      yozilmaydi (faqat `amocrm_lead_id` / `synced_at`) va `contact_conflict=True`
      belgilanadi. `IntegrityError` hech qachon ko'tarilmaydi.

    Mos lead topilsa `LeadMatch` qaytaradi, aks holda None.
    """
    from main.models import Client

    match = find_lead_by_phone(client.phone_number)
    if not match:
        return None

    update_fields = []

    if not client.amocrm_id and match.contact_id:
        conflict = (
            Client.objects.filter(amocrm_id=match.contact_id)
            .exclude(pk=client.pk)
            .exists()
        )
        if conflict:
            match.contact_conflict = True
        else:
            client.amocrm_id = match.contact_id
            update_fields.append("amocrm_id")

    client.amocrm_lead_id = match.lead_id
    update_fields.append("amocrm_lead_id")
    client.synced_at = timezone.now()
    update_fields.append("synced_at")

    client.save(update_fields=update_fields)
    return match


# ---------------------------------------------------------------------------
# Kontaktlarni ommaviy import qilish (admin action + management command)
# ---------------------------------------------------------------------------

def sync_contacts(logger=None):
    """amoCRM kontaktlarini `Client` modeliga sinxronlaydi.

    `amocrm_id` bo'yicha moslashtiradi: mavjud bo'lsa yangilaydi, bo'lmasa yaratadi.
    Yaratilgan va yangilangan mijozlar sonini qaytaradi.
    """
    from main.models import Client

    created = 0
    updated = 0
    page = 1

    while True:
        data = _request(
            "GET", "/api/v4/contacts", params={"page": page, "limit": 250}
        )
        if not data:
            break
        contacts = (data.get("_embedded") or {}).get("contacts") or []
        if not contacts:
            break

        for contact in contacts:
            amocrm_id = contact.get("id")
            if not amocrm_id:
                continue

            full_name = (contact.get("name") or "").strip() or "amoCRM #{0}".format(
                amocrm_id
            )
            phones = _contact_phones(contact)
            phone = phones[0] if phones else ""

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
                logger(
                    "{0}: {1}".format(
                        "yaratildi" if was_created else "yangilandi", full_name
                    )
                )

        if not (data.get("_links") or {}).get("next"):
            break
        page += 1

    return {"created": created, "updated": updated}
