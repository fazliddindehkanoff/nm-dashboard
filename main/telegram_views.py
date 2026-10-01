import hashlib
import json
import logging
import uuid
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
    Course,
    Discount,
    EnrollmentQuestionnaire,
    Group,
    LegalAcceptance,
    MiniAppPurchase,
    MulticardInvoice,
    Operator,
    PaymentSettings,
    TelegramUser,
)
from .services.booking import booking_discount_for, check_payment_version, payment_amount
from .services.images import web_banner_url
from .services.legal import (
    TERMS_VERSION,
    contract_accepted,
    contract_version,
    record_acceptance,
    render_contract_document,
    render_terms_document,
    terms_accepted,
)
from .services.mini_app import (
    CheckoutError,
    build_participants,
    can_return_to_cart,
    checkout_cart,
    create_purchase,
    find_client_by_phone as _find_client_by_phone,
    normalise_phone as _normalise_phone,
    quote,
    return_to_cart,
    save_cart_item,
    upcoming_groups_by_course,
)
from .services.telegram import TelegramAPIError, TelegramNotConfigured, send_bot_message
from .services.telegram_channels import handle_join_request, schedule_undelivered_links
from .services.telegram_auth import TelegramAuthenticationError, telegram_user_from_request
from .services.multicard import (
    InvalidCallback, MulticardError, MulticardNotConfigured,
    accept_success_callback, get_or_create_invoice, reconcile_invoice, _settle, to_tiyin,
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


def _web_app_url(request=None):
    configured = (getattr(settings, 'TELEGRAM', {}) or {}).get('WEB_APP_URL', '')
    if configured:
        return configured
    if request is None:
        raise TelegramNotConfigured('TELEGRAM_WEB_APP_URL sozlanmagan.')
    return request.build_absolute_uri(reverse('main:telegram_app'))


class TelegramDeliveryError(Exception):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


def _deliver_bot_message(chat_id, text, reply_markup=None):
    ok, detail = send_bot_message(chat_id, text, reply_markup=reply_markup)
    if not ok:
        message = detail or 'Telegram xabarni qabul qilmadi.'
        normalized = message.lower()
        permanent = normalized.startswith(('forbidden:', 'bad request:'))
        raise TelegramDeliveryError(message, retryable=not permanent)


def _send_onboarding_message(account, request):
    web_app_markup = {
        'inline_keyboard': [[{
            'text': 'Norbekov ilovasini ochish',
            'web_app': {'url': _web_app_url(request)},
        }]],
    }
    _deliver_bot_message(
        account.telegram_id,
        f"✅ Rahmat, <b>{escape(account.full_name)}</b>!\n\n"
        "Profilingiz tayyor. Kurslarni ko'rish va xarid qilish uchun ilovani oching.",
        reply_markup=web_app_markup,
    )


def process_telegram_update(update, request=None):
    """Process one update atomically so a polling retry cannot skip onboarding steps."""
    if update.get('chat_join_request'):
        try:
            handle_join_request(update['chat_join_request'])
        except TelegramAPIError as exc:
            raise TelegramDeliveryError(str(exc), retryable=exc.retryable)
        return

    message = update.get('message') or {}
    sender = message.get('from') or {}
    telegram_id = sender.get('id')
    if not telegram_id:
        return

    with transaction.atomic():
        account, created = TelegramUser.objects.get_or_create(
            telegram_id=telegram_id,
            defaults={'username': sender.get('username', '')},
        )
        if sender.get('username') and account.username != sender.get('username'):
            account.username = sender['username']
            account.save(update_fields=('username', 'updated_at'))

        text = (message.get('text') or '').strip()
        if created and text.startswith('/start ref_'):
            try:
                code = uuid.UUID(text.split('ref_', 1)[1])
                account.referrer = Operator.objects.filter(referral_code=code, user__is_active=True, role='operator').first()
                account.save(update_fields=('referrer', 'updated_at'))
            except (ValueError, TypeError):
                pass
        contact = message.get('contact') or {}
        if text.startswith('/start'):
            if account.onboarding_step == TelegramUser.STEP_READY and account.phone_number:
                _send_onboarding_message(account, request)
            else:
                account.onboarding_step = TelegramUser.STEP_NAME
                account.save(update_fields=('onboarding_step', 'updated_at'))
                _deliver_bot_message(
                    telegram_id,
                    "Assalomu alaykum! Norbekov markaziga xush kelibsiz.\n\n"
                    "Avval ism va familiyangizni yozing.",
                    reply_markup={'remove_keyboard': True},
                )
        elif account.onboarding_step == TelegramUser.STEP_NAME:
            if len(text) < 3:
                _deliver_bot_message(telegram_id, "Ism va familiyangizni to'liqroq yozing.")
            else:
                account.full_name = text[:255]
                account.onboarding_step = TelegramUser.STEP_CONTACT
                account.save(update_fields=('full_name', 'onboarding_step', 'updated_at'))
                _deliver_bot_message(
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
                _deliver_bot_message(telegram_id, "Pastdagi «Kontaktni yuborish» tugmasini bosing.")
            elif contact.get('user_id') and contact.get('user_id') != telegram_id:
                _deliver_bot_message(telegram_id, "Iltimos, aynan o'zingizning kontaktingizni yuboring.")
            else:
                account.phone_number = _normalise_phone(contact['phone_number'])
                account.client = _find_client_by_phone(account.phone_number)
                account.onboarding_step = TelegramUser.STEP_READY
                account.save(update_fields=(
                    'phone_number', 'client', 'onboarding_step', 'updated_at',
                ))
                if account.client_id:
                    schedule_undelivered_links(account.client_id)
                _send_onboarding_message(account, request)
        elif account.onboarding_step == TelegramUser.STEP_READY:
            _send_onboarding_message(account, request)


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

    try:
        process_telegram_update(update, request)
    except TelegramDeliveryError as exc:
        if not exc.retryable:
            logger.warning("Telegram update permanently undeliverable: %s", exc)
            return JsonResponse({'ok': True})
        return JsonResponse({'ok': False, 'error': str(exc)}, status=503)
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


def _group_payload(group):
    return {
        'id': group.id,
        'banner_url': web_banner_url(group.banner) if group.banner else '',
        'start_date': group.start_date.isoformat(),
        'number_of_days': group.number_of_days,
        'teachers': [teacher.full_name for teacher in group.teachers.all()],
    }


def _purchase_payload(purchase):
    members = list(purchase.members.all())
    invoices = sorted(purchase.multicard_invoices.all(), key=lambda item: item.pk, reverse=True)
    invoice = next((item for item in invoices if item.state in ('creating', 'ready', 'uncertain', 'error')), invoices[0] if invoices else None)
    return {
        'id': purchase.id,
        'course': purchase.course.name,
        'course_id': purchase.course_id,
        'group': _group_payload(purchase.group) if purchase.group else None,
        'checkout_batch': str(purchase.checkout_batch) if purchase.checkout_batch else '',
        'can_return_to_cart': can_return_to_cart(purchase, invoices),
        'purchase_type': purchase.purchase_type,
        'purchase_type_label': purchase.get_purchase_type_display(),
        'unit_price': str(purchase.unit_price),
        'discount_per_person': str(purchase.discount_per_person),
        'social_discount_amount': str(purchase.social_discount_amount),
        'discount_name': purchase.discount_name,
        'discount_total': str(purchase.discount_per_person * purchase.participant_count),
        'participant_count': purchase.participant_count,
        'total_amount': str(purchase.total_amount),
        'sale_amount': str(purchase.sale_amount),
        'is_booking': purchase.is_booking,
        'booking_discount': str(purchase.booking_discount),
        'discount_amount': str(purchase.discount_amount),
        'paid_amount': str(purchase.paid_amount),
        'remaining_amount': str(purchase.remaining_amount),
        'payable_amount': str(purchase.payable_amount),
        'minimum_payment': str(purchase.minimum_payment),
        'invoice_amount': str(Decimal(invoice.amount) / 100) if invoice else '',
        'payments': [{'amount': str(Decimal(item.amount) / 100), 'state': item.state,
                      'receipt_url': item.receipt_url} for item in invoices],
        'payment_status': purchase.payment_status,
        'payment_status_label': purchase.get_payment_status_display(),
        'payment_provider': purchase.payment_provider,
        'checkout_url': invoice.checkout_url if invoice and invoice.state in ('ready', 'error') else '',
        'receipt_url': invoice.receipt_url if invoice else '',
        'invoice_state': invoice.state if invoice else '',
        'contract_accepted': contract_accepted(purchase),
        'contract_version': contract_version(purchase),
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
    """List every active group; new sales still require an upcoming group."""
    minimum_booking = PaymentSettings.booking_minimum()
    today = timezone.localdate()
    groups = (
        Group.objects.filter(is_active=True)
        .select_related('course')
        .prefetch_related('teachers')
        .order_by('course__name', 'start_date', 'id')
    )
    courses = {}
    for group in groups:
        course = group.course
        if course.id not in courses:
            courses[course.id] = {
                'id': course.id,
                'name': course.name,
                'price': str(course.price),
                'booking_discount': str(booking_discount_for(course.price, 1, course.id)),
                'minimum_booking': str(minimum_booking),
                'number_of_days': course.number_of_days,
                'participant_discounts': [
                    {'name': rule.name, 'amount': str(rule.amount), 'min_participants': rule.min_participants}
                    for rule in Discount.participant_rules(course.id)
                ],
                'active_groups': [],
                'can_purchase': False,
            }
        payload = courses[course.id]
        can_purchase = group.start_date > today
        payload['can_purchase'] = payload['can_purchase'] or can_purchase
        payload['active_groups'].append({**_group_payload(group), 'can_purchase': can_purchase})
    return list(courses.values())


def _document_payload(document):
    if not document:
        return None
    return {'id': document.id, 'category': document.category, 'name': document.original_name}


def _cart_payload(account):
    items = list(
        account.cart_items.select_related('course', 'group', 'eligibility_document')
        .prefetch_related('members__eligibility_document', 'group__teachers')
    )
    groups = upcoming_groups_by_course({item.course_id for item in items})
    result = []
    for item in items:
        family = list(item.members.all()) if item.purchase_type == MiniAppPurchase.TYPE_FAMILY else []
        participants = [{'eligibility_document_id': item.eligibility_document_id}] + [
            {'eligibility_document_id': member.eligibility_document_id} for member in family
        ]
        price = quote(item.course, participants, item.payment_mode)
        available = groups.get(item.course_id, [])
        if not available:
            status, message = 'course_unavailable', "Hozircha bu kurs uchun ochiq guruh yo'q. Kurs savatda saqlanib turadi."
        elif item.group_id not in {group.id for group in available}:
            status, message = 'choose_group', "Tanlangan guruh boshlangan. Yangi guruhni tanlang."
        elif item.payment_mode == 'booking' and not price['booking_available']:
            status, message = 'needs_update', "Bron summasi yetarli emas. To'liq to'lovni tanlang."
        else:
            status, message = 'ready', ''
        result.append({
            'id': item.id,
            'course_id': item.course_id,
            'course': item.course.name,
            'number_of_days': item.course.number_of_days,
            'price': str(item.course.price),
            'group': _group_payload(item.group) if item.group else None,
            'purchase_type': item.purchase_type,
            'purchase_type_label': item.get_purchase_type_display(),
            'payment_mode': item.payment_mode,
            'payment_mode_label': item.get_payment_mode_display(),
            'eligibility_document': _document_payload(item.eligibility_document),
            'members': [
                {
                    'full_name': member.full_name,
                    'phone_number': member.phone_number,
                    'eligibility_document': _document_payload(member.eligibility_document),
                }
                for member in family
            ],
            'participant_count': len(participants),
            'unit_price': str(price['unit_price']),
            'discount_name': price['discount_name'],
            'discount_total': str(price['discount_per_person'] * len(participants)),
            'social_discount_amount': str(price['social_discount']),
            'total_amount': str(price['total']),
            'sale_amount': str(price['total'] - price['booking_discount']),
            'booking_discount': str(price['booking_discount']),
            'minimum_booking': str(price['minimum_booking']),
            'status': status,
            'status_message': message,
        })
    return result


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
        if purchase.payment_status in (MiniAppPurchase.PAYMENT_SUCCESS, MiniAppPurchase.PAYMENT_PARTIAL)
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
            'banner_url': web_banner_url(group.banner) if group.banner else '',
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
            'banner_url': '',
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
        account.purchases.exclude(payment_status=MiniAppPurchase.PAYMENT_CANCELLED)
        .select_related('course', 'group').prefetch_related('multicard_invoices', 'group__teachers')
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
        'cart': _cart_payload(account),
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
        account.purchases.select_related('course', 'telegram_user', 'group')
        .prefetch_related('multicard_invoices', 'group__teachers')
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
                'version': contract_version(purchase),
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
        if data.get('accepted') is not True or data.get('version') != contract_version(purchase):
            raise ValueError("Amaldagi shartnomani qabul qiling.")
        html = render_contract_document(purchase)
        with transaction.atomic():
            acceptance, _ = record_acceptance(
                account,
                LegalAcceptance.DOCUMENT_CONTRACT,
                contract_version(purchase),
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


def _terms_required_response():
    return JsonResponse({
        'ok': False,
        'error': "Avval foydalanish shartlarini qabul qiling.",
    }, status=409)


def _upcoming_group(course, group_id):
    """The chosen upcoming group, or the nearest one when none was chosen."""
    groups = upcoming_groups_by_course([course.id])[course.id]
    if not group_id:
        return groups[0]
    group = next((item for item in groups if str(item.id) == str(group_id)), None)
    if group is None:
        raise ValueError("Tanlangan guruh endi mavjud emas. Boshqa guruhni tanlang.")
    return group


@require_POST
def telegram_app_create_purchase(request):
    account, error = _authenticate(request)
    if error:
        return error
    try:
        if not terms_accepted(account):
            return _terms_required_response()
        data = _json_body(request)
        course = Course.objects.filter(
            pk=data.get('course_id'), group__in=Group.objects.upcoming(),
        ).distinct().get()
        participants = build_participants(
            account, data.get('purchase_type'), data.get('members'), data.get('eligibility_document_id'),
        )
        with transaction.atomic():
            purchase = create_purchase(
                account, course, data.get('purchase_type'), participants, data.get('payment_mode', 'full'),
                group=_upcoming_group(course, data.get('group_id')),
            )
        purchase = MiniAppPurchase.objects.select_related('course', 'group').prefetch_related('members').get(pk=purchase.pk)
        return JsonResponse({'ok': True, 'purchase': _purchase_payload(purchase)}, status=201)
    except Course.DoesNotExist:
        return JsonResponse({'ok': False, 'error': "Kurs topilmadi."}, status=404)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


@require_POST
def telegram_app_save_cart_item(request):
    account, error = _authenticate(request)
    if error:
        return error
    try:
        data = _json_body(request)
        course = Course.objects.filter(
            pk=data.get('course_id'), group__in=Group.objects.upcoming(),
        ).distinct().get()
        item = save_cart_item(
            account, course, _upcoming_group(course, data.get('group_id')), data.get('purchase_type'),
            data.get('members'), data.get('eligibility_document_id'), data.get('payment_mode', 'booking'),
        )
        return JsonResponse({'ok': True, 'item_id': item.id, 'cart': _cart_payload(account)})
    except Course.DoesNotExist:
        return JsonResponse({'ok': False, 'error': "Kurs topilmadi yoki yangi guruh hali ochilmagan."}, status=404)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


@require_POST
def telegram_app_remove_cart_item(request, item_id):
    account, error = _authenticate(request)
    if error:
        return error
    deleted, _ = account.cart_items.filter(pk=item_id).delete()
    if not deleted:
        return JsonResponse({'ok': False, 'error': "Savatda bunday kurs topilmadi."}, status=404)
    return JsonResponse({'ok': True, 'cart': _cart_payload(account)})


@require_POST
def telegram_app_checkout(request):
    account, error = _authenticate(request)
    if error:
        return error
    if not terms_accepted(account):
        return _terms_required_response()
    try:
        item_ids = _json_body(request).get('item_ids')
        if not isinstance(item_ids, list) or not item_ids:
            raise CheckoutError("To'lov uchun kamida bitta kursni tanlang.")
        if not all(type(item_id) is int for item_id in item_ids):
            raise CheckoutError("Tanlangan kurslar ro'yxati noto'g'ri.")
        items = list(
            account.cart_items.filter(pk__in=item_ids)
            .select_related('course', 'group').prefetch_related('members')
        )
        if len(items) != len(set(item_ids)):
            raise CheckoutError("Savat yangilangan. Sahifani yangilab, kurslarni qayta tanlang.")
        purchases = checkout_cart(account, items)
        return JsonResponse({
            'ok': True,
            'purchases': [_purchase_payload(_account_purchase(account, item.pk)) for item in purchases],
            'cart': _cart_payload(account),
        }, status=201)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


@require_POST
def telegram_app_return_to_cart(request, purchase_id):
    account, error = _authenticate(request)
    if error:
        return error
    purchase = _account_purchase(account, purchase_id)
    if not purchase:
        return JsonResponse({'ok': False, 'error': "Xarid topilmadi."}, status=404)
    try:
        return_to_cart(purchase)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=409)
    return JsonResponse({'ok': True, 'cart': _cart_payload(account)})


def _selected_purchases(account, ids):
    if not isinstance(ids, list) or not ids or not all(type(item) is int for item in ids):
        raise ValueError("Xaridlar ro'yxati noto'g'ri.")
    purchases = [_account_purchase(account, purchase_id) for purchase_id in dict.fromkeys(ids)]
    if not all(purchases):
        raise LookupError
    return purchases


def _contracts_html(purchases):
    if len(purchases) == 1:
        return render_contract_document(purchases[0])
    return ''.join(
        f'<section class="legal-contract-part"><p class="legal-contract-part__label">'
        f'{index}-shartnoma · {escape(purchase.course.name)}</p>{render_contract_document(purchase)}</section>'
        for index, purchase in enumerate(purchases, start=1)
    )


@require_GET
def telegram_app_contracts(request):
    """All contracts of one checkout, read and accepted together."""
    account, error = _authenticate(request)
    if error:
        return error
    try:
        ids = [int(value) for value in request.GET.get('ids', '').split(',') if value]
        purchases = _selected_purchases(account, ids)
        return JsonResponse({
            'ok': True,
            'document': {
                'type': LegalAcceptance.DOCUMENT_CONTRACT,
                'versions': {str(item.pk): contract_version(item) for item in purchases},
                'title': "Sog'lomlashtirish xizmatlari shartnomasi",
                'html': _contracts_html(purchases),
                'accepted': all(contract_accepted(item) for item in purchases),
            },
        })
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except LookupError:
        return JsonResponse({'ok': False, 'error': "Xarid topilmadi."}, status=404)


@require_POST
def telegram_app_accept_contracts(request):
    account, error = _authenticate(request)
    if error:
        return error
    if not terms_accepted(account):
        return _terms_required_response()
    try:
        data = _json_body(request)
        purchases = _selected_purchases(account, data.get('purchase_ids'))
        versions = data.get('versions') or {}
        if data.get('accepted') is not True or any(
            versions.get(str(item.pk)) != contract_version(item) for item in purchases
        ):
            raise ValueError("Amaldagi shartnomani qabul qiling.")
        # Every course keeps its own signed contract text and audit hash.
        with transaction.atomic():
            for purchase in purchases:
                record_acceptance(
                    account, LegalAcceptance.DOCUMENT_CONTRACT, contract_version(purchase),
                    render_contract_document(purchase), request, purchase=purchase,
                )
        return JsonResponse({
            'ok': True,
            'purchases': [_purchase_payload(_account_purchase(account, item.pk)) for item in purchases],
        })
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except LookupError:
        return JsonResponse({'ok': False, 'error': "Xarid topilmadi."}, status=404)


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
    try:
        with transaction.atomic():
            purchase = MiniAppPurchase.objects.select_for_update().get(pk=purchase.pk)
            if purchase.payment_status == MiniAppPurchase.PAYMENT_REFUNDED:
                raise ValueError("Bu to'lov qaytarilgan.")
            if purchase.payment_status == MiniAppPurchase.PAYMENT_CANCELLED:
                raise ValueError("Bu xarid savatga qaytarilgan.")
            if purchase.payment_status != MiniAppPurchase.PAYMENT_SUCCESS:
                data = _json_body(request)
                if purchase.is_booking:
                    check_payment_version(purchase, data.get('expected_paid'))
                amount = payment_amount(purchase, data.get('amount') if purchase.is_booking else None)
                invoice = MulticardInvoice.objects.create(purchase=purchase, store_id='demo', amount=to_tiyin(amount))
                _settle(invoice, uuid.uuid4(), provider='demo')
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    purchase = account.purchases.select_related('course').prefetch_related('multicard_invoices').prefetch_related('members__questionnaire').get(pk=purchase.pk)
    return JsonResponse({'ok': True, 'purchase': _purchase_payload(purchase)})


@require_POST
def telegram_app_questionnaire(request, purchase_id):
    account, error = _authenticate(request)
    if error:
        return error
    purchase = account.purchases.filter(pk=purchase_id).prefetch_related('members').first()
    if not purchase:
        return JsonResponse({'ok': False, 'error': "Xarid topilmadi."}, status=404)
    if purchase.payment_status not in (MiniAppPurchase.PAYMENT_SUCCESS, MiniAppPurchase.PAYMENT_PARTIAL):
        return JsonResponse({'ok': False, 'error': "Avval bron yoki to'liq to'lovni amalga oshiring."}, status=409)
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
        purchase = account.purchases.select_related('course').prefetch_related('multicard_invoices').prefetch_related('members__questionnaire').get(pk=purchase.pk)
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
    if purchase.payment_status == MiniAppPurchase.PAYMENT_CANCELLED:
        return JsonResponse({'ok': False, 'error': 'Bu xarid savatga qaytarilgan. Savatdan qayta rasmiylashtiring.'}, status=409)
    try:
        data = _json_body(request)
        invoice = get_or_create_invoice(
            purchase, data.get('amount') if purchase.is_booking else None, data.get('expected_paid'),
        )
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
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
    invoices = MulticardInvoice.objects.filter(purchase=purchase).exclude(store_id='demo').order_by('id')
    try:
        for invoice in invoices:
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
