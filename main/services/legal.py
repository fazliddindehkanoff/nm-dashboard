"""Versioned legal-document rendering and acceptance audit helpers."""

import hashlib
import ipaddress

from django.template.loader import render_to_string
from django.utils import timezone

from main.models import LegalAcceptance


TERMS_VERSION = '2026-09-03'
CONTRACT_VERSION = '2026-09-03.1'


def render_terms_document():
    return render_to_string('telegram_app/legal/terms.html')


def render_contract_document(purchase):
    return render_to_string('telegram_app/legal/contract.html', {
        'purchase': purchase,
        'contract_date': timezone.localtime(purchase.created_at).date(),
    })


def document_hash(html):
    return hashlib.sha256(html.encode('utf-8')).hexdigest()


def terms_accepted(account):
    return account.legal_acceptances.filter(
        document_type=LegalAcceptance.DOCUMENT_TERMS,
        version=TERMS_VERSION,
        purchase__isnull=True,
    ).exists()


def contract_accepted(purchase):
    prefetched = getattr(purchase, '_prefetched_objects_cache', {}).get('legal_acceptances')
    if prefetched is not None:
        return any(
            item.document_type == LegalAcceptance.DOCUMENT_CONTRACT
            and item.version == CONTRACT_VERSION
            for item in prefetched
        )
    return purchase.legal_acceptances.filter(
        document_type=LegalAcceptance.DOCUMENT_CONTRACT,
        version=CONTRACT_VERSION,
    ).exists()


def request_ip(request):
    for value in (request.META.get('HTTP_X_REAL_IP'), request.META.get('REMOTE_ADDR')):
        try:
            return str(ipaddress.ip_address(value))
        except (TypeError, ValueError):
            continue
    return None


def record_acceptance(account, document_type, version, html, request, purchase=None):
    acceptance, created = LegalAcceptance.objects.get_or_create(
        telegram_user=account,
        purchase=purchase,
        document_type=document_type,
        version=version,
        defaults={
            'document_hash': document_hash(html),
            'ip_address': request_ip(request),
            'user_agent': (request.META.get('HTTP_USER_AGENT') or '')[:255],
        },
    )
    return acceptance, created
