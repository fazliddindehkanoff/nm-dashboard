import hashlib
import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import (
    AttendanceLesson,
    AttendanceRecord,
    Client,
    Course,
    EnrollmentQuestionnaire,
    Group,
    LegalAcceptance,
    MiniAppPurchase,
    MiniAppPurchaseMember,
    MulticardInvoice,
    Operator,
    TelegramUser,
)
from .services.legal import (
    CONTRACT_VERSION,
    TERMS_VERSION,
    contract_accepted,
    record_acceptance,
    render_contract_document,
    render_terms_document,
    terms_accepted,
)
from .services.telegram import TelegramNotConfigured, send_bot_message
from .services.telegram_auth import TelegramAuthenticationError, telegram_user_from_request
from .services.multicard import (
    InvalidCallback, MulticardError, MulticardNotConfigured,
    accept_success_callback, get_or_create_invoice, reconcile_invoice,
)

logger = logging.getLogger(__name__)


def _telegram_asset_version():
    """Change static URLs whenever a Mini App asset changes.

    Production serves static assets with an immutable seven-day cache, so a
    stable URL can leave Telegram running an older checkout flow after deploys.
    """
    digest = hashlib.sha256()
    for relative_path in (
        'main/static/main/css/telegram-app.css',
        'main/static/main/css/telegram-app-icons.css',
        'main/static/main/css/telegram-app-legal.css',
        'main/static/main/js/telegram-app.js',
    ):
        digest.update((settings.BASE_DIR / relative_path).read_bytes())
    return digest.hexdigest()[:12]


def _digits(value):
    return re.sub(r'\D', '', value or '')


def _normalise_phone(value):
    digits = _digits(value)
    if len(digits) == 9:
        digits = '998' + digits
    if len(digits) != 12 or not digits.startswith('998'):
        raise ValueError("Telefon raqamini +998 XX XXX XX XX ko'rinishida kiriting.")
    return '+' + digits


def _find_client_by_phone(phone):
    target = _digits(phone)
    for client in Client.objects.only('id', 'phone_number', 'full_name'):
        if _digits(client.phone_number) == target:
            return client
    return None


def _get_or_create_client(full_name, phone):
    client = _find_client_by_phone(phone)
    if client:
        if full_name and client.full_name != full_name:
            client.full_name = full_name
            client.save(update_fields=('full_name',))
        return client
    return Client.objects.create(full_name=full_name, phone_number=phone)


def _web_app_url(request):
    configured = (getattr(settings, 'TELEGRAM', {}) or {}).get('WEB_APP_URL', '')
    return configured or request.build_absolute_uri(reverse('main:telegram_app'))


def _send_onboarding_message(account, request):
    web_app_markup = {
        'inline_keyboard': [[{
            'text': 'Norbekov ilovasini ochish',
            'web_app': {'url': _web_app_url(request)},
        }]],
    }
    return send_bot_message(
        account.telegram_id,
        f"✅ Rahmat, <b>{escape(account.full_name)}</b>!\n\n"
        "Profilingiz tayyor. Kurslarni ko'rish va xarid qilish uchun ilovani oching.",
        reply_markup=web_app_markup,
    )


@csrf_exempt
@require_POST
def telegram_webhook(request):
    expected_secret = (getattr(settings, 'TELEGRAM', {}) or {}).get('WEBHOOK_SECRET', '')
    if expected_secret and request.headers.get('X-Telegram-Bot-Api-Secret-Token') != expected_secret:
        return JsonResponse({'ok': False, 'error': 'Forbidden'}, status=403)

    try:
        update = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    message = update.get('message') or {}
    sender = message.get('from') or {}
    telegram_id = sender.get('id')
    if not telegram_id:
        return JsonResponse({'ok': True})

    account, _ = TelegramUser.objects.get_or_create(
        telegram_id=telegram_id,
        defaults={'username': sender.get('username', '')},
    )
    if sender.get('username') and account.username != sender.get('username'):
        account.username = sender['username']
        account.save(update_fields=('username', 'updated_at'))

    text = (message.get('text') or '').strip()
    contact = message.get('contact') or {}
    try:
        if text.startswith('/start'):
            if account.onboarding_step == TelegramUser.STEP_READY and account.phone_number:
                _send_onboarding_message(account, request)
            else:
                account.onboarding_step = TelegramUser.STEP_NAME
                account.save(update_fields=('onboarding_step', 'updated_at'))
                send_bot_message(
                    telegram_id,
                    "Assalomu alaykum! Norbekov markaziga xush kelibsiz.\n\n"
                    "Avval ism va familiyangizni yozing.",
                    reply_markup={'remove_keyboard': True},
                )
        elif account.onboarding_step == TelegramUser.STEP_NAME:
            if len(text) < 3:
                send_bot_message(telegram_id, "Ism va familiyangizni to'liqroq yozing.")
            else:
                account.full_name = text[:255]
                account.onboarding_step = TelegramUser.STEP_CONTACT
                account.save(update_fields=('full_name', 'onboarding_step', 'updated_at'))
                send_bot_message(
                    telegram_id,
                    "Endi telefon raqamingizni tasdiqlang.",
                    reply_markup={
                        'keyboard': [[{'text': 'Kontaktni yuborish', 'request_contact': True}]],
                        'resize_keyboard': True,
                        'one_time_keyboard': True,
                    },
                )
        elif account.onboarding_step == TelegramUser.STEP_CONTACT:
            if not contact.get('phone_number'):
                send_bot_message(telegram_id, "Pastdagi «Kontaktni yuborish» tugmasini bosing.")
            elif contact.get('user_id') and contact.get('user_id') != telegram_id:
                send_bot_message(telegram_id, "Iltimos, aynan o'zingizning kontaktingizni yuboring.")
            else:
                account.phone_number = _normalise_phone(contact['phone_number'])
                account.client = _find_client_by_phone(account.phone_number)
                account.onboarding_step = TelegramUser.STEP_READY
                account.save(update_fields=(
                    'phone_number', 'client', 'onboarding_step', 'updated_at',
                ))
                _send_onboarding_message(account, request)
        elif account.onboarding_step == TelegramUser.STEP_READY:
            _send_onboarding_message(account, request)
    except (TelegramNotConfigured, ValueError) as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=503)

    return JsonResponse({'ok': True})


def telegram_app(request):
    return render(request, 'telegram_app/index.html', {
        'demo_mode': settings.DEBUG and request.GET.get('demo') == '1',
        'asset_version': _telegram_asset_version(),
    })


def _authenticate(request):
    try:
        return telegram_user_from_request(request), None
    except TelegramAuthenticationError as exc:
        return None, JsonResponse({'ok': False, 'error': str(exc)}, status=401)


def _is_demo_account(account):
    return settings.DEBUG and account.telegram_id == 900000001


def _purchase_payload(purchase):
    members = list(purchase.members.all())
    invoice = getattr(purchase, 'multicard_invoice', None)
    return {
        'id': purchase.id,
        'course': purchase.course.name,
        'course_id': purchase.course_id,
        'purchase_type': purchase.purchase_type,
        'purchase_type_label': purchase.get_purchase_type_display(),
        'unit_price': str(purchase.unit_price),
        'participant_count': purchase.participant_count,
        'total_amount': str(purchase.total_amount),
        'payment_status': purchase.payment_status,
        'payment_status_label': purchase.get_payment_status_display(),
        'payment_provider': purchase.payment_provider,
        'checkout_url': invoice.checkout_url if invoice and invoice.state in ('ready', 'error') else '',
        'receipt_url': invoice.receipt_url if invoice else '',
        'invoice_state': invoice.state if invoice else '',
        'contract_accepted': contract_accepted(purchase),
        'contract_version': CONTRACT_VERSION,
        'questionnaire_completed': purchase.questionnaire_completed,
        'members': [
            {
                'id': member.id,
                'full_name': member.full_name,
                'phone_number': member.phone_number,
                'relationship': member.relationship,
                'questionnaire_completed': hasattr(member, 'questionnaire'),
            }
            for member in members
        ],
    }


def _active_course_payloads():
    """Return one catalogue entry per course that has a sellable active group."""
    groups = (
        Group.objects.filter(is_active=True)
        .select_related('course')
        .prefetch_related('teachers')
        .order_by('course__name', 'start_date', 'id')
    )
    courses = {}
    for group in groups:
        course = group.course
        payload = courses.setdefault(course.id, {
            'id': course.id,
            'name': course.name,
            'price': str(course.price),
            'number_of_days': course.number_of_days,
            'active_groups': [],
        })
        payload['active_groups'].append({
            'id': group.id,
            'start_date': group.start_date.isoformat(),
            'number_of_days': group.number_of_days,
            'teachers': [teacher.full_name for teacher in group.teachers.all()],
        })
    return list(courses.values())


def _attendance_marker_name(user):
    if not user:
        return ''
    try:
        return user.operator.full_name
    except (AttributeError, Operator.DoesNotExist):
        return user.get_full_name() or user.username


def _attendance_participant_payload(record):
    lessons_by_date = {lesson.date: lesson for lesson in record.lessons.all()}
    lessons = []
    for day_index in range(record.group.number_of_days):
        lesson_date = record.group.start_date + timedelta(days=day_index)
        lesson = lessons_by_date.get(lesson_date)
        lessons.append({
            'day_number': day_index + 1,
            'date': lesson_date.isoformat(),
            'status': lesson.status if lesson else AttendanceLesson.STATUS_UNMARKED,
            'status_label': (
                lesson.get_status_display() if lesson else str(dict(AttendanceLesson.STATUSES)[AttendanceLesson.STATUS_UNMARKED])
            ),
            'reason': lesson.reason if lesson else '',
            'note': lesson.note if lesson else '',
            'marked_at': lesson.created_at.isoformat() if lesson else '',
            'marked_by': _attendance_marker_name(lesson.marked_by) if lesson else '',
        })
    return {
        'client_id': record.client_id,
        'full_name': record.client.full_name,
        'status': record.status,
        'status_label': record.get_status_display(),
        'last_attended_at': record.last_attended_at.isoformat() if record.last_attended_at else '',
        'attended_lessons_count': record.attended_lessons_count,
        'lessons': lessons,
    }


def _my_course_payloads(account, purchases):
    """Expose attendance only for the account owner and paid purchase members."""
    client_ids = {account.client_id} if account.client_id else set()
    paid_purchases = [
        purchase for purchase in purchases
        if purchase.payment_status == MiniAppPurchase.PAYMENT_SUCCESS
    ]
    for purchase in paid_purchases:
        client_ids.update(
            member.client_id for member in purchase.members.all() if member.client_id
        )

    records = list(
        AttendanceRecord.objects.filter(client_id__in=client_ids)
        .select_related('client', 'group__course')
        .prefetch_related('group__teachers', 'lessons__marked_by__operator')
        .order_by('-group__is_active', '-group__start_date', 'client__full_name')
    ) if client_ids else []

    grouped = {}
    for record in records:
        group = record.group
        course = group.course
        payload = grouped.setdefault(group.id, {
            'id': f'group-{group.id}',
            'group_id': group.id,
            'purchase_id': None,
            'course_id': course.id,
            'course': course.name,
            'start_date': group.start_date.isoformat(),
            'number_of_days': group.number_of_days,
            'is_active': group.is_active,
            'teachers': [teacher.full_name for teacher in group.teachers.all()],
            'assignment_status': 'assigned',
            'participants': [],
        })
        payload['participants'].append(_attendance_participant_payload(record))

    result = list(grouped.values())
    covered_pairs = {
        (record.client_id, record.group.course_id) for record in records
    }
    for purchase in paid_purchases:
        members = list(purchase.members.all())
        if any(
            member.client_id and (member.client_id, purchase.course_id) in covered_pairs
            for member in members
        ):
            continue
        result.append({
            'id': f'purchase-{purchase.id}',
            'group_id': None,
            'purchase_id': purchase.id,
            'course_id': purchase.course_id,
            'course': purchase.course.name,
            'start_date': '',
            'number_of_days': purchase.course.number_of_days,
            'is_active': True,
            'teachers': [],
            'assignment_status': 'awaiting_group',
            'participants': [
                {
                    'client_id': member.client_id,
                    'full_name': member.full_name,
                    'status': 'awaiting_group',
                    'status_label': 'Guruh biriktirilmoqda',
                    'last_attended_at': '',
                    'attended_lessons_count': 0,
                    'lessons': [],
                }
                for member in members
            ],
        })
    return result


@require_GET
def telegram_app_bootstrap(request):
    account, error = _authenticate(request)
    if error:
        return error
    purchases = list(
        account.purchases.select_related('course', 'multicard_invoice')
        .prefetch_related('members__questionnaire', 'legal_acceptances')
    )
    return JsonResponse({
        'ok': True,
        'profile': {
            'full_name': account.full_name,
            'phone_number': account.phone_number,
            'username': account.username,
        },
        'legal': {
            'terms_required': not terms_accepted(account),
            'terms_version': TERMS_VERSION,
        },
        'courses': _active_course_payloads(),
        'my_courses': _my_course_payloads(account, purchases),
        'purchases': [_purchase_payload(item) for item in purchases],
    })


def _json_body(request):
    try:
        return json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        raise ValueError("So'rov ma'lumoti noto'g'ri.")


@require_GET
def telegram_app_terms(request):
    account, error = _authenticate(request)
    if error:
        return error
    try:
        return JsonResponse({
            'ok': True,
            'document': {
                'type': LegalAcceptance.DOCUMENT_TERMS,
                'version': TERMS_VERSION,
                'title': 'Foydalanish shartlari',
                'html': render_terms_document(),
                'accepted': terms_accepted(account),
            },
        })
    except Exception:
        return JsonResponse({
            'ok': False,
            'error': "Foydalanish shartlarini yuklab bo'lmadi.",
        }, status=500)


@require_POST
def telegram_app_accept_terms(request):
    account, error = _authenticate(request)
    if error:
        return error
    try:
        data = _json_body(request)
        if data.get('accepted') is not True or data.get('version') != TERMS_VERSION:
            raise ValueError("Amaldagi foydalanish shartlarini qabul qiling.")
        html = render_terms_document()
        with transaction.atomic():
            acceptance, _ = record_acceptance(
                account,
                LegalAcceptance.DOCUMENT_TERMS,
                TERMS_VERSION,
                html,
                request,
            )
        return JsonResponse({
            'ok': True,
            'accepted_at': acceptance.accepted_at.isoformat(),
            'version': acceptance.version,
        })
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception:
        return JsonResponse({
            'ok': False,
            'error': "Rozilikni saqlab bo'lmadi. Qayta urinib ko'ring.",
        }, status=500)


def _account_purchase(account, purchase_id):
    return (
        account.purchases.select_related('course', 'telegram_user', 'multicard_invoice')
        .prefetch_related('members', 'legal_acceptances')
        .filter(pk=purchase_id)
        .first()
    )


@require_GET
def telegram_app_contract(request, purchase_id):
    account, error = _authenticate(request)
    if error:
        return error
    try:
        purchase = _account_purchase(account, purchase_id)
        if not purchase:
            return JsonResponse({'ok': False, 'error': "Xarid topilmadi."}, status=404)
        return JsonResponse({
            'ok': True,
            'document': {
                'type': LegalAcceptance.DOCUMENT_CONTRACT,
                'version': CONTRACT_VERSION,
                'title': "Sog'lomlashtirish xizmatlari shartnomasi",
                'html': render_contract_document(purchase),
                'accepted': contract_accepted(purchase),
            },
        })
    except Exception:
        return JsonResponse({
            'ok': False,
            'error': "Shartnomani yuklab bo'lmadi.",
        }, status=500)


@require_POST
def telegram_app_accept_contract(request, purchase_id):
    account, error = _authenticate(request)
    if error:
        return error
    try:
        if not terms_accepted(account):
            return JsonResponse({
                'ok': False,
                'error': "Avval foydalanish shartlarini qabul qiling.",
            }, status=409)
        purchase = _account_purchase(account, purchase_id)
        if not purchase:
            return JsonResponse({'ok': False, 'error': "Xarid topilmadi."}, status=404)
        data = _json_body(request)
        if data.get('accepted') is not True or data.get('version') != CONTRACT_VERSION:
            raise ValueError("Amaldagi shartnomani qabul qiling.")
        html = render_contract_document(purchase)
        with transaction.atomic():
            acceptance, _ = record_acceptance(
                account,
                LegalAcceptance.DOCUMENT_CONTRACT,
                CONTRACT_VERSION,
                html,
                request,
                purchase=purchase,
            )
        return JsonResponse({
            'ok': True,
            'accepted_at': acceptance.accepted_at.isoformat(),
            'version': acceptance.version,
            'purchase': _purchase_payload(_account_purchase(account, purchase_id)),
        })
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception:
        return JsonResponse({
            'ok': False,
            'error': "Shartnomani qabul qilishni saqlab bo'lmadi. Qayta urinib ko'ring.",
        }, status=500)


@require_POST
def telegram_app_create_purchase(request):
    account, error = _authenticate(request)
    if error:
        return error
    try:
        if not terms_accepted(account):
            return JsonResponse({
                'ok': False,
                'error': "Avval foydalanish shartlarini qabul qiling.",
            }, status=409)
        data = _json_body(request)
        course = Course.objects.filter(
            pk=data.get('course_id'), group__is_active=True,
        ).distinct().get()
        purchase_type = data.get('purchase_type')
        if purchase_type not in dict(MiniAppPurchase.PURCHASE_TYPES):
            raise ValueError("Xarid turini tanlang.")
        if not account.full_name or not account.phone_number:
            raise ValueError("Avval Telegram botdagi ism va kontakt bosqichini yakunlang.")

        participants = [{
            'full_name': account.full_name,
            'phone_number': _normalise_phone(account.phone_number),
            'relationship': MiniAppPurchaseMember.RELATION_SELF,
        }]
        if purchase_type == MiniAppPurchase.TYPE_FAMILY:
            family_members = data.get('members') or []
            if not family_members:
                raise ValueError("Kamida bitta oila a'zosini qo'shing.")
            if len(family_members) > 7:
                raise ValueError("Bitta xaridda ko'pi bilan 8 kishi qatnashishi mumkin.")
            for member in family_members:
                full_name = (member.get('full_name') or '').strip()
                if len(full_name) < 3:
                    raise ValueError("Har bir oila a'zosining to'liq ismini kiriting.")
                participants.append({
                    'full_name': full_name[:255],
                    'phone_number': _normalise_phone(member.get('phone_number')),
                    'relationship': MiniAppPurchaseMember.RELATION_FAMILY,
                })
        phones = [item['phone_number'] for item in participants]
        if len(set(phones)) != len(phones):
            raise ValueError("Bir telefon raqamini ikki marta qo'shib bo'lmaydi.")

        with transaction.atomic():
            total = Decimal(course.price) * len(participants)
            purchase = MiniAppPurchase.objects.create(
                telegram_user=account,
                course=course,
                purchase_type=purchase_type,
                unit_price=course.price,
                participant_count=len(participants),
                total_amount=total,
            )
            MiniAppPurchaseMember.objects.bulk_create([
                MiniAppPurchaseMember(purchase=purchase, **participant)
                for participant in participants
            ])
        purchase = MiniAppPurchase.objects.select_related('course').prefetch_related('members').get(pk=purchase.pk)
        return JsonResponse({'ok': True, 'purchase': _purchase_payload(purchase)}, status=201)
    except Course.DoesNotExist:
        return JsonResponse({'ok': False, 'error': "Kurs topilmadi."}, status=404)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


@require_POST
def telegram_app_simulate_payment(request, purchase_id):
    account, error = _authenticate(request)
    if error:
        return error
    if not _is_demo_account(account):
        return JsonResponse({'ok': False, 'error': 'Demo to‘lov mavjud emas.'}, status=403)
    if not terms_accepted(account):
        return JsonResponse({
            'ok': False,
            'error': "Avval foydalanish shartlarini qabul qiling.",
        }, status=409)
    purchase = _account_purchase(account, purchase_id)
    if not purchase:
        return JsonResponse({'ok': False, 'error': "Xarid topilmadi."}, status=404)
    if not contract_accepted(purchase):
        return JsonResponse({
            'ok': False,
            'error': "To'lovdan oldin shartnomani o'qib, qabul qiling.",
        }, status=409)
    if purchase.payment_status != MiniAppPurchase.PAYMENT_SUCCESS:
        with transaction.atomic():
            purchase.payment_provider = 'demo'
            purchase.save(update_fields=('payment_provider',))
            purchase.mark_paid(reference=f'DEMO-{purchase.uuid.hex[:12].upper()}')
            for member in purchase.members.all():
                member.client = _get_or_create_client(member.full_name, member.phone_number)
                member.save(update_fields=('client',))
                if member.relationship == MiniAppPurchaseMember.RELATION_SELF:
                    account.client = member.client
            account.save(update_fields=('client', 'updated_at'))
        if not _is_demo_account(account):
            try:
                send_bot_message(
                    account.telegram_id,
                    f"✅ <b>To'lov muvaffaqiyatli</b>\n\n"
                    f"Kurs: {escape(purchase.course.name)}\n"
                    f"Summa: {purchase.total_amount:,.0f} UZS\n\n"
                    "Kursni faollashtirish uchun majburiy anketani to'ldiring.",
                )
            except Exception:
                pass
    purchase = account.purchases.select_related('course', 'multicard_invoice').prefetch_related('members__questionnaire').get(pk=purchase.pk)
    return JsonResponse({'ok': True, 'purchase': _purchase_payload(purchase)})


@require_POST
def telegram_app_questionnaire(request, purchase_id):
    account, error = _authenticate(request)
    if error:
        return error
    purchase = account.purchases.filter(pk=purchase_id).prefetch_related('members').first()
    if not purchase:
        return JsonResponse({'ok': False, 'error': "Xarid topilmadi."}, status=404)
    if purchase.payment_status != MiniAppPurchase.PAYMENT_SUCCESS:
        return JsonResponse({'ok': False, 'error': "Avval to'lovni yakunlang."}, status=409)
    try:
        data = _json_body(request)
        responses = {int(item.get('member_id')): item for item in (data.get('responses') or [])}
        members = list(purchase.members.all())
        if set(responses) != {member.id for member in members}:
            raise ValueError("Har bir ishtirokchi uchun anketani to'ldiring.")

        with transaction.atomic():
            for member in members:
                item = responses[member.id]
                city = (item.get('city') or '').strip()
                learning_goal = (item.get('learning_goal') or '').strip()
                if not city or not learning_goal or item.get('consent') is not True:
                    raise ValueError("Majburiy maydonlarni to'ldiring va tasdiqlashni belgilang.")
                try:
                    birth_date = date.fromisoformat(item.get('birth_date') or '')
                except ValueError:
                    raise ValueError("Tug'ilgan sanani kiriting.")
                if birth_date >= timezone.localdate():
                    raise ValueError("Tug'ilgan sana noto'g'ri.")
                EnrollmentQuestionnaire.objects.update_or_create(
                    member=member,
                    defaults={
                        'birth_date': birth_date,
                        'city': city[:120],
                        'occupation': (item.get('occupation') or '').strip()[:160],
                        'learning_goal': learning_goal,
                        'prior_experience': (item.get('prior_experience') or '').strip(),
                        'health_notes': (item.get('health_notes') or '').strip(),
                        'consent': True,
                        'completed_at': timezone.now(),
                    },
                )
            purchase.questionnaire_completed = True
            purchase.save(update_fields=('questionnaire_completed', 'updated_at'))
        if not _is_demo_account(account):
            try:
                send_bot_message(
                    account.telegram_id,
                    f"🎉 <b>Ro'yxatdan o'tish yakunlandi</b>\n\n"
                    f"{escape(purchase.course.name)} kursi uchun anketa qabul qilindi.",
                )
            except Exception:
                pass
        purchase = account.purchases.select_related('course', 'multicard_invoice').prefetch_related('members__questionnaire').get(pk=purchase.pk)
        return JsonResponse({'ok': True, 'purchase': _purchase_payload(purchase)})
    except (TypeError, ValueError) as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


@require_POST
def telegram_app_payment(request, purchase_id):
    account, error = _authenticate(request)
    if error:
        return error
    purchase = _account_purchase(account, purchase_id)
    if not purchase:
        return JsonResponse({'ok': False, 'error': 'Xarid topilmadi.'}, status=404)
    if _is_demo_account(account):
        return JsonResponse({'ok': False, 'error': 'Demo hisobda haqiqiy to‘lov mavjud emas.'}, status=403)
    if not terms_accepted(account) or not contract_accepted(purchase):
        return JsonResponse({'ok': False, 'error': 'To‘lovdan oldin shartlarni va shartnomani qabul qiling.'}, status=409)
    if purchase.payment_status == MiniAppPurchase.PAYMENT_SUCCESS:
        return JsonResponse({'ok': True, 'purchase': _purchase_payload(purchase)})
    if purchase.payment_status == MiniAppPurchase.PAYMENT_REFUNDED:
        return JsonResponse({'ok': False, 'error': 'Bu to‘lov qaytarilgan. Yangi xarid yarating.'}, status=409)
    try:
        invoice = get_or_create_invoice(purchase)
    except MulticardNotConfigured as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=503)
    except MulticardError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=502)
    purchase = _account_purchase(account, purchase_id)
    return JsonResponse({'ok': True, 'checkout_url': invoice.checkout_url, 'purchase': _purchase_payload(purchase)})


@require_GET
def telegram_app_payment_status(request, purchase_id):
    account, error = _authenticate(request)
    if error:
        return error
    purchase = _account_purchase(account, purchase_id)
    if not purchase:
        return JsonResponse({'ok': False, 'error': 'Xarid topilmadi.'}, status=404)
    response = JsonResponse({'ok': True, 'purchase': _purchase_payload(purchase)})
    response['Cache-Control'] = 'no-store'
    return response


@require_POST
def telegram_app_check_payment(request, purchase_id):
    account, error = _authenticate(request)
    if error:
        return error
    purchase = _account_purchase(account, purchase_id)
    if not purchase:
        return JsonResponse({'ok': False, 'error': 'Xarid topilmadi.'}, status=404)
    invoice = MulticardInvoice.objects.filter(purchase=purchase).first()
    if invoice:
        try:
            reconcile_invoice(invoice)
        except (MulticardError, InvalidCallback):
            return JsonResponse({'ok': False, 'error': 'To‘lov holatini tekshirib bo‘lmadi. Keyinroq qayta tekshiring.'}, status=502)
    return JsonResponse({'ok': True, 'purchase': _purchase_payload(_account_purchase(account, purchase_id))})


@csrf_exempt
@require_POST
def multicard_callback(request):
    try:
        data = json.loads(request.body)
        accept_success_callback(data)
    except (json.JSONDecodeError, UnicodeDecodeError, InvalidCallback):
        return JsonResponse({'success': False, 'message': 'Invalid invoice or signature'}, status=400)
    except MulticardNotConfigured:
        return JsonResponse({'success': False, 'message': 'Temporarily unavailable'}, status=500)
    except Exception:
        # Avoid logging signed payloads or payment/card data. A 500 asks Multicard
        # to retry; never acknowledge a database write that did not commit.
        logger.error('Multicard callback could not be committed')
        return JsonResponse({'success': False, 'message': 'Temporarily unavailable'}, status=500)
    return JsonResponse({'success': True})
