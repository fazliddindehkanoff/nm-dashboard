import uuid as uuid_lib
from urllib.parse import parse_qs, urlparse

from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import admin
from .models import Operator, Transaction, TransactionClient, Client, Group
from django.db.models import Sum, Count
from datetime import datetime

import json
from django.db.models.functions import TruncMonth, ExtractMonth, ExtractYear
from datetime import timedelta

# Pie/doughnut chart uchun rang palitrasi
CHART_COLORS = [
    "#111827", "#374151", "#4b5563", "#6b7280", "#9ca3af",
    "#030712", "#1f2937", "#525252", "#737373", "#a3a3a3",
]


def build_statistics(transactions):
    """Berilgan (qaytarilmagan) to'lovlar to'plami bo'yicha statistika bloklarini qaytaradi:
    operatorlar reytingi, kurslar kesimi (pie) va sotuvlar manbasi (doughnut)."""
    total_amount = float(transactions.aggregate(t=Sum('amount'))['t'] or 0)
    total_count = transactions.count()

    # 1) Operatorlar reytingi (sotuvlar soni + miqdori bo'yicha)
    operators_rating = list(
        transactions
        .values('operator__id', 'operator__full_name')
        .annotate(sales_count=Count('id'), total=Sum('amount'))
        .order_by('-total', '-sales_count')
    )
    for i, row in enumerate(operators_rating, start=1):
        row['rank'] = i
        row['total'] = float(row['total'] or 0)
        row['name'] = row['operator__full_name'] or "—"

    # 2) Kurslar/guruhlar kesimidagi to'lovlar (pie chart)
    course_rows = list(
        transactions
        .values('group__course__name')
        .annotate(total=Sum('amount'), cnt=Count('id'))
        .order_by('-total')
    )
    course_labels, course_values, course_table = [], [], []
    for i, row in enumerate(course_rows):
        name = row['group__course__name'] or "Noma'lum"
        amount = float(row['total'] or 0)
        share = (amount / total_amount * 100) if total_amount else 0
        course_labels.append(name)
        course_values.append(amount)
        course_table.append({
            'name': name,
            'amount': amount,
            'count': row['cnt'],
            'share': share,
            'color': CHART_COLORS[i % len(CHART_COLORS)],
        })

    course_chart_data = json.dumps({
        "labels": course_labels,
        "datasets": [{
            "data": course_values,
            "backgroundColor": CHART_COLORS[:len(course_values)] or CHART_COLORS[:1],
            "borderWidth": 0,
        }]
    })

    # 3) Sotuvlar manbasi (qayerdan kelgan)
    source_rows_raw = {
        row['source']: row
        for row in transactions.values('source').annotate(cnt=Count('id'), total=Sum('amount'))
    }
    source_table, source_labels, source_values = [], [], []
    for i, (key, label) in enumerate(Transaction.SOURCE_TYPES):
        row = source_rows_raw.get(key)
        cnt = row['cnt'] if row else 0
        amount = float(row['total'] or 0) if row else 0
        share = (cnt / total_count * 100) if total_count else 0
        source_table.append({
            'label': label,
            'count': cnt,
            'amount': amount,
            'share': share,
            'color': CHART_COLORS[i % len(CHART_COLORS)],
        })
        source_labels.append(str(label))
        source_values.append(cnt)

    source_chart_data = json.dumps({
        "labels": source_labels,
        "datasets": [{
            "data": source_values,
            "backgroundColor": CHART_COLORS[:len(source_values)],
            "borderWidth": 0,
        }]
    })

    # Sayt orqali kelgan (amoCRM'da bor) to'lovlarni sayt nomi bo'yicha ajratish
    website_rows = list(
        transactions
        .filter(source='amocrm_website')
        .values('source_detail')
        .annotate(cnt=Count('id'), total=Sum('amount'))
        .order_by('-cnt')
    )
    for row in website_rows:
        row['name'] = row['source_detail'] or "Noma'lum sayt"
        row['total'] = float(row['total'] or 0)

    return {
        'operators_rating': operators_rating,
        'course_table': course_table,
        'course_chart_data': course_chart_data,
        'source_table': source_table,
        'source_chart_data': source_chart_data,
        'website_rows': website_rows,
    }


def dashboard_callback(request, context):
    month_filter = request.GET.get('month')
    operator_filter = request.GET.get('operator_id')

    # Restrict view for plain operators
    is_plain_op = not request.user.is_superuser and hasattr(request.user, 'operator')

    all_transactions = Transaction.objects.filter(is_refunded=False)
    if is_plain_op:
        all_transactions = all_transactions.filter(operator=request.user.operator)
        operator_filter = str(request.user.operator.id)
    elif operator_filter:
        all_transactions = all_transactions.filter(operator_id=operator_filter)

    confirmed_transactions = all_transactions.filter(is_confirmed=True)
    pending_transactions = all_transactions.filter(is_confirmed=False)

    # Count confirmed transactions per month respecting operator filtering
    monthly_counts = {
        item['month']: item['count']
        for item in confirmed_transactions
        .annotate(month=ExtractMonth('date')).values('month').annotate(count=Count('id'))
    }

    if month_filter:
        confirmed_transactions = confirmed_transactions.filter(date__month=month_filter)
        pending_transactions = pending_transactions.filter(date__month=month_filter)

    six_months_ago = datetime.now() - timedelta(days=180)
    monthly_data = (confirmed_transactions
                    .filter(date__gte=six_months_ago)
                    .annotate(month=TruncMonth('date'))
                    .values('month')
                    .annotate(total_amount=Sum('amount'), count=Count('id'))
                    .order_by('month'))

    months_list = []
    amounts = []
    counts = []

    for entry in monthly_data:
        months_list.append(entry['month'].strftime('%b %Y'))
        amounts.append(float(entry['total_amount'] or 0))
        counts.append(entry['count'])

    context.update({
        "is_plain_operator": is_plain_op,
        "total_income": confirmed_transactions.aggregate(total=Sum('amount'))['total'] or 0,
        "total_clients": Client.objects.count(),
        "total_groups": Group.objects.filter(is_active=True).count(),
        "transactions_count": confirmed_transactions.count(),
        "pending_count": pending_transactions.count(),
        "total_debt": TransactionClient.objects.filter(transaction__in=confirmed_transactions).aggregate(
            total=Sum('debt')
        )['total'] or 0,
        "recent_transactions": all_transactions.prefetch_related('clients').select_related('group').order_by('-date', '-id')[:6],
        "operators": Operator.objects.all() if not is_plain_op else Operator.objects.filter(id=request.user.operator.id),
        "months": [
            (1, "Yanvar", monthly_counts.get(1, 0)),
            (2, "Fevral", monthly_counts.get(2, 0)),
            (3, "Mart", monthly_counts.get(3, 0)),
            (4, "Aprel", monthly_counts.get(4, 0)),
            (5, "May", monthly_counts.get(5, 0)),
            (6, "Iyun", monthly_counts.get(6, 0)),
            (7, "Iyul", monthly_counts.get(7, 0)),
            (8, "Avgust", monthly_counts.get(8, 0)),
            (9, "Sentabr", monthly_counts.get(9, 0)),
            (10, "Oktabr", monthly_counts.get(10, 0)),
            (11, "Noyabr", monthly_counts.get(11, 0)),
            (12, "Dekabr", monthly_counts.get(12, 0))
        ],
        "selected_month": int(month_filter) if month_filter else '',
        "selected_operator": int(operator_filter) if operator_filter else '',
        "income_chart_data": json.dumps({
            "labels": months_list,
            "datasets": [{
                "label": "Tushum (UZS)",
                "data": amounts,
                "borderColor": "#111827",
                "backgroundColor": "rgba(17, 24, 39, 0.08)",
                "borderWidth": 2,
                "fill": True,
                "tension": 0.4
            }]
        }),
        "count_chart_data": json.dumps({
            "labels": months_list,
            "datasets": [{
                "label": "To'lovlar Soni",
                "data": counts,
                "backgroundColor": "#111827"
            }]
        })
    })
    context.update(build_statistics(confirmed_transactions))
    return context

def calculate_salary_percentage(total_amount):
    """Oylik sotuvlar summasiga (so'mda) qarab operator maoshining foizini qaytaradi."""
    if total_amount > 150_000_000:
        return 9
    elif total_amount > 100_000_000:
        return 8
    elif total_amount > 50_000_000:
        return 5
    elif total_amount > 30_000_000:
        return 2
    else:
        return 1


@staff_member_required
def salaries(request):
    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')
    operator_filter = request.GET.get('operator_id')

    is_plain_op = not request.user.is_superuser and hasattr(request.user, 'operator')

    operators = Operator.objects.all()
    if is_plain_op:
        operators = operators.filter(id=request.user.operator.id)
        filtered_operators = operators
        operator_filter = str(request.user.operator.id)
    else:
        filtered_operators = operators
        if operator_filter:
            filtered_operators = filtered_operators.filter(id=operator_filter)

    selected_month = int(month_filter) if month_filter else datetime.now().month
    selected_year = int(year_filter) if year_filter else datetime.now().year

    # "Faqat tasdiqlangan, qaytarilmagan" to'lovlar bazaviy filtri (badge va
    # asosiy hisob-kitob uchun umumiy).
    base_qs = Transaction.objects.filter(is_refunded=False, is_confirmed=True)
    if is_plain_op:
        base_qs = base_qs.filter(operator=request.user.operator)
    elif operator_filter:
        base_qs = base_qs.filter(operator_id=operator_filter)

    # Badge'larda ko'rsatiladigan yillar ro'yxati (mavjud ma'lumotlar + tanlangan yil).
    available_years = sorted(
        base_qs.annotate(year=ExtractYear('date')).values_list('year', flat=True).distinct(),
        reverse=True,
    )
    if selected_year not in available_years:
        available_years = sorted(set(available_years) | {selected_year}, reverse=True)

    # Oy bo'yicha badge sonlari (tanlangan yil bo'yicha)
    monthly_counts = {
        item['month']: item['count']
        for item in base_qs.filter(date__year=selected_year)
        .annotate(month=ExtractMonth('date')).values('month').annotate(count=Count('id'))
    }

    months = [
        (1, "Yanvar", monthly_counts.get(1, 0)),
        (2, "Fevral", monthly_counts.get(2, 0)),
        (3, "Mart", monthly_counts.get(3, 0)),
        (4, "Aprel", monthly_counts.get(4, 0)),
        (5, "May", monthly_counts.get(5, 0)),
        (6, "Iyun", monthly_counts.get(6, 0)),
        (7, "Iyul", monthly_counts.get(7, 0)),
        (8, "Avgust", monthly_counts.get(8, 0)),
        (9, "Sentabr", monthly_counts.get(9, 0)),
        (10, "Oktabr", monthly_counts.get(10, 0)),
        (11, "Noyabr", monthly_counts.get(11, 0)),
        (12, "Dekabr", monthly_counts.get(12, 0))
    ]

    rows = []
    total_salary = 0
    total_collected_all = 0
    total_sales_all = 0
    for operator in filtered_operators:
        transactions = Transaction.objects.filter(
            operator=operator,
            date__year=selected_year,
            date__month=selected_month,
            is_refunded=False,
            is_confirmed=True,
        )
        sales_count = transactions.count()
        total_collected = transactions.aggregate(total=Sum('amount'))['total'] or 0
        percentage = calculate_salary_percentage(float(total_collected))
        salary = float(total_collected) * (percentage / 100)

        total_salary += salary
        total_collected_all += float(total_collected)
        total_sales_all += sales_count

        rows.append({
            'operator': operator,
            'sales_count': sales_count,
            'total_collected': total_collected,
            'percentage': percentage,
            'salary': salary,
        })

    context = {
        'title': "Maoshlar",
        'rows': rows,
        'operators': operators,
        'months': months,
        'available_years': available_years,
        'selected_month': selected_month,
        'selected_year': selected_year,
        'selected_operator': int(operator_filter) if operator_filter else '',
        'is_plain_operator': is_plain_op,
        'total_salary': total_salary,
        'total_collected_all': total_collected_all,
        'total_sales_all': total_sales_all,
        'salary_tiers': [
            ("0 - 30 mln", "1%"),
            ("30 - 50 mln", "2%"),
            ("50 - 100 mln", "5%"),
            ("100 - 150 mln", "8%"),
            ("150 mln dan ortiq", "9%"),
        ],
    }
    context.update(admin.site.each_context(request))

    return render(request, 'admin/salaries.html', context)


@staff_member_required
def qr_verify(request):
    """QR skaner orqali mijozni tekshirish sahifasi.

    QR kod mijozning UUID sini kodlaydi. Skaner (klaviatura sifatida) UUID ni
    inputga yozadi va odatda Enter yuboradi — shunda forma avtomatik yuboriladi.
    Server UUID bo'yicha mijozni topib, uning barcha to'lovlarini (qaysi kurs,
    qachon, qancha) ko'rsatadi.
    """
    code = (request.GET.get('code') or '').strip()

    client = None
    transactions = []
    summary = None
    searched = bool(code)
    invalid_code = False

    is_plain_op = not request.user.is_superuser and hasattr(request.user, 'operator')

    if code:
        # QR odatda UUID ni saqlaydi. Link skaner qilinganda querystringdagi
        # ?code=<uuid> ni ham tushunamiz, aks holda oxirgi path bo'lagini olamiz.
        parsed_url = urlparse(code)
        query_code = (parse_qs(parsed_url.query).get('code') or [None])[0]
        candidate_source = query_code or parsed_url.path or code
        candidate = candidate_source.rstrip('/').split('/')[-1].strip()
        try:
            parsed = uuid_lib.UUID(candidate)
        except (ValueError, AttributeError):
            invalid_code = True
        else:
            client_qs = Client.objects.filter(uuid=parsed).select_related('operator')
            if is_plain_op:
                client_qs = client_qs.filter(operator=request.user.operator)
            client = client_qs.first()
            if client:
                transactions = list(
                    TransactionClient.objects
                    .filter(client=client)
                    .select_related(
                        'transaction', 'transaction__group', 'transaction__group__course',
                        'transaction__operator', 'transaction__discount',
                    )
                    .order_by('-transaction__date', '-transaction_id')
                )
                active_confirmed = [
                    tc for tc in transactions
                    if tc.transaction.is_confirmed and not tc.transaction.is_refunded
                ]
                active_pending = [
                    tc for tc in transactions
                    if not tc.transaction.is_confirmed and not tc.transaction.is_refunded
                ]
                summary = {
                    'total_paid': sum((tc.share_amount for tc in active_confirmed), 0),
                    'pending_paid': sum((tc.share_amount for tc in active_pending), 0),
                    'pending_count': len(active_pending),
                    'total_debt': sum((tc.debt for tc in active_confirmed), 0),
                    'count': len(transactions),
                    'courses': sorted({
                        tc.transaction.group.course.name
                        for tc in active_confirmed
                        if tc.transaction.group and tc.transaction.group.course_id
                    }),
                }

    context = {
        'title': "QR tekshirish",
        'code': code,
        'searched': searched,
        'invalid_code': invalid_code,
        'client': client,
        'transactions': transactions,
        'summary': summary,
        'is_plain_operator': is_plain_op,
    }
    context.update(admin.site.each_context(request))

    return render(request, 'admin/qr_verify.html', context)
