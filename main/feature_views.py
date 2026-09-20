"""Payment overview, seller attribution and private eligibility documents."""
import uuid
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from io import BytesIO

from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.files.storage import FileSystemStorage
from django.core.paginator import Paginator
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .models import (EligibilityDocument, Operator, Transaction, SubTransaction,
                     MulticardInvoice, PAYMENT_METHODS)
from .permissions import is_operator, permission_required


def private_storage():
    return FileSystemStorage(location=getattr(settings, 'PRIVATE_DOCUMENT_ROOT', settings.BASE_DIR / 'private_documents'))


@require_POST
def upload_eligibility_document(request):
    from .telegram_views import _authenticate, _normalise_phone
    account, error = _authenticate(request)
    if error:
        return error
    try:
        category = request.POST.get('category')
        if category not in dict(EligibilityDocument.CATEGORIES):
            raise ValueError('Chegirma toifasini tanlang.')
        phone = _normalise_phone(request.POST.get('phone_number', ''))
        upload = request.FILES.get('document')
        if not upload or not 0 < upload.size <= 5 * 1024 * 1024:
            raise ValueError('5 MB gacha PDF, JPG yoki PNG hujjat yuboring.')
        data = upload.read()
        suffix = Path(upload.name).suffix.lower()
        if suffix == '.pdf' and data.startswith(b'%PDF-'):
            pass
        elif suffix in ('.jpg', '.jpeg', '.png'):
            try:
                with Image.open(BytesIO(data)) as image:
                    if image.format not in ('PNG', 'JPEG') or image.width * image.height > 25000000:
                        raise ValueError('Rasm hajmi juda katta yoki formati noto‘g‘ri.')
                    image.verify()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                raise ValueError('Hujjat rasmini o‘qib bo‘lmadi.') from None
        else:
            raise ValueError('PDF, JPG yoki PNG hujjat yuboring.')
        if EligibilityDocument.objects.filter(telegram_user=account, created_at__gte=timezone.now()-timezone.timedelta(hours=1)).count() >= 30:
            raise ValueError('Hujjat yuborish limiti tugadi. Keyinroq urinib ko‘ring.')
        upload.seek(0)
        storage = private_storage()
        name = storage.save(f'{uuid.uuid4().hex}{suffix}', upload)
        try:
            proof = EligibilityDocument.objects.create(telegram_user=account, phone_number=phone, category=category,
                storage_name=name, original_name=Path(upload.name).name[:200])
        except Exception:
            storage.delete(name)
            raise
        return JsonResponse({'ok': True, 'id': proof.pk}, status=201)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


@staff_member_required
@permission_required('main.view_client')
def eligibility_document(request, document_id):
    documents = EligibilityDocument.objects.filter(enrollments__client__isnull=False)
    if is_operator(request.user):
        documents = documents.filter(enrollments__client__operator=request.user.operator)
    document = get_object_or_404(documents.distinct(), pk=document_id)
    response = FileResponse(private_storage().open(document.storage_name, 'rb'), as_attachment=True,
                            filename=document.original_name, content_type='application/octet-stream')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def referral_link(operator):
    username = settings.TELEGRAM.get('BOT_USERNAME', '').lstrip('@')
    return f'https://t.me/{username}?start=ref_{operator.referral_code.hex}' if username else 'Bot username sozlanmagan'


@staff_member_required
def referrals(request):
    operators = Operator.objects.filter(role='operator', user__is_active=True)
    if is_operator(request.user):
        operators = operators.filter(pk=request.user.operator.pk)
    elif not request.user.has_perm('main.view_operator'):
        raise PermissionDenied
    rows = []
    for operator in operators:
        invoices = MulticardInvoice.objects.filter(purchase__referrer=operator, state='success').exclude(store_id='demo')
        rows.append({'name': operator.full_name, 'link': referral_link(operator),
                     'registrations': operator.referral_accounts.filter(onboarding_step='ready').count(),
                     'sales': invoices.values('purchase_id').distinct().count(),
                     'paid': Decimal(invoices.aggregate(total=Sum('amount'))['total'] or 0) / 100})
    return render(request, 'admin/main/referrals.html', {**admin.site.each_context(request), 'title': 'Referal havolalar', 'rows': rows})


@staff_member_required
@permission_required('main.view_transaction')
def payments(request):
    method = request.GET.get('method', '')
    status = request.GET.get('status', '')
    search = request.GET.get('q', '').strip().casefold()
    def date_filter(value):
        try:
            return parse_date(value)
        except ValueError:
            return None
    start, end = date_filter(request.GET.get('from', '')), date_filter(request.GET.get('to', ''))
    manual = Transaction.objects.select_related('group__course', 'operator').prefetch_related('clients', 'sub_transactions')
    subs = SubTransaction.objects.select_related('transaction__group__course', 'transaction__operator').prefetch_related('clients')
    online = MulticardInvoice.objects.select_related('purchase__course', 'purchase__telegram_user', 'purchase__referrer').exclude(store_id='demo').annotate(effective_at=Coalesce('paid_at', 'created_at'))
    if is_operator(request.user):
        operator = request.user.operator
        manual = manual.filter(operator=operator)
        subs = subs.filter(transaction__operator=operator)
        online = online.filter(purchase__referrer=operator)
    if start:
        manual, subs, online = manual.filter(date__gte=start), subs.filter(received_at__date__gte=start), online.filter(effective_at__date__gte=start)
    if end:
        manual, subs, online = manual.filter(date__lte=end), subs.filter(received_at__date__lte=end), online.filter(effective_at__date__lte=end)
    if method:
        manual, subs = manual.filter(payment_method=method), subs.filter(payment_method=method)
        if method != 'rahmat': online = online.none()
    rows = []
    for item in manual:
        rows.append(dict(date=item.date, sort=timezone.make_aware(datetime.combine(item.date, time.min)),
            name=', '.join(c.full_name for c in item.clients.all()), course=str(item.group or '—'),
            amount=item.initial_amount, method=item.get_payment_method_display() or 'Avval kiritilmagan',
            status='refunded' if item.is_refunded else 'success' if item.is_confirmed else 'pending',
            source='CRM', reference=f'T-{item.pk}', link=reverse('admin:main_transaction_change', args=[item.pk])))
    for item in subs:
        rows.append(dict(date=timezone.localdate(item.received_at), sort=item.received_at,
            name=', '.join(c.full_name for c in item.clients.all()), course=str(item.transaction.group or '—'),
            amount=item.amount, method=item.get_payment_method_display() or 'Avval kiritilmagan',
            status=('refunded' if item.transaction.is_refunded else 'success') if item.status == 'approved' else 'failed' if item.status == 'rejected' else 'pending',
            source='Qo‘shimcha to‘lov', reference=f'S-{item.pk}', link=reverse('admin:main_subtransaction_change', args=[item.pk])))
    for item in online:
        rows.append(dict(date=timezone.localdate(item.paid_at or item.created_at), sort=item.paid_at or item.created_at,
            name=item.purchase.telegram_user.full_name, course=item.purchase.course.name, amount=Decimal(item.amount)/100,
            method='Rahmat', status={'success':'success', 'revert':'refunded', 'error':'failed'}.get(item.state,'pending'),
            source='Web App', reference=f'R-{item.pk}', link=reverse('admin:main_miniapppurchase_change', args=[item.purchase_id]) if request.user.has_perm('main.view_miniapppurchase') else ''))
    labels = {'pending': 'Kutilmoqda', 'success': 'Tasdiqlangan', 'failed': 'Rad etilgan / xato', 'refunded': 'Qaytarilgan'}
    rows = [row for row in rows if (not status or row['status'] == status) and (not search or search in f"{row['name']} {row['course']} {row['reference']}".casefold())]
    rows.sort(key=lambda row: row['sort'], reverse=True)
    total = sum((row['amount'] for row in rows if row['status'] == 'success'), Decimal(0))
    for row in rows: row['status_label'] = labels[row['status']]
    query = request.GET.copy(); query.pop('page', None)
    return render(request, 'admin/main/payments.html', {**admin.site.each_context(request), 'title': 'To‘lovlar',
        'page': Paginator(rows, 50).get_page(request.GET.get('page')), 'total': total, 'methods': PAYMENT_METHODS,
        'statuses': labels.items(), 'selected_method': method, 'selected_status': status, 'query': query.urlencode(),
        'can_add': request.user.has_perm('main.add_transaction')})


def group_banner(request, banner_path):
    """Serve only published group images; private documents never use MEDIA_URL."""
    from .models import Group
    group = get_object_or_404(Group.objects.exclude(banner=''), banner=f'group_banners/{banner_path}')
    try:
        response = FileResponse(group.banner.open('rb'))
    except FileNotFoundError:
        from django.http import Http404
        raise Http404 from None
    response['Cache-Control'] = 'public, max-age=300'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
