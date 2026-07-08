from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import admin
from .models import Operator, Transaction, Client, Group
from django.db.models import Sum, Count
from datetime import datetime

import json
from django.db.models.functions import TruncMonth, ExtractMonth
from datetime import timedelta

# Pie/doughnut chart uchun rang palitrasi
CHART_COLORS = [
    "#9333ea", "#2563eb", "#16a34a", "#ea580c", "#dc2626",
    "#0891b2", "#ca8a04", "#db2777", "#4f46e5", "#65a30d",
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

    transactions = Transaction.objects.filter(is_refunded=False)
    if is_plain_op:
        transactions = transactions.filter(operator=request.user.operator)
        operator_filter = str(request.user.operator.id)
    elif operator_filter:
        transactions = transactions.filter(operator_id=operator_filter)

    # Count transactions per month respecting operator filtering
    count_qs = Transaction.objects.filter(is_refunded=False)
    if is_plain_op:
        count_qs = count_qs.filter(operator=request.user.operator)
    elif operator_filter:
        count_qs = count_qs.filter(operator_id=operator_filter)

    monthly_counts = {
        item['month']: item['count']
        for item in count_qs.annotate(month=ExtractMonth('date')).values('month').annotate(count=Count('id'))
    }

    if month_filter:
        transactions = transactions.filter(date__month=month_filter)

    six_months_ago = datetime.now() - timedelta(days=180)
    monthly_data = (transactions
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
        "total_income": transactions.aggregate(total=Sum('amount'))['total'] or 0,
        "total_clients": Client.objects.count(),
        "total_groups": Group.objects.filter(is_active=True).count(),
        "transactions_count": transactions.count(),
        "pending_count": transactions.filter(is_confirmed=False).count(),
        "total_debt": transactions.aggregate(total=Sum('debt'))['total'] or 0,
        "recent_transactions": transactions.select_related('client', 'group').order_by('-date', '-id')[:6],
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
                "borderColor": "var(--color-primary-700)",
                "backgroundColor": "rgba(147, 51, 234, 0.1)",
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
                "backgroundColor": "var(--color-primary-600)"
            }]
        })
    })
    context.update(build_statistics(transactions))
    return context

def calculate_salary_percentage(sales_count):
    """Oylik sotuvlar soniga qarab operator maoshining foizini qaytaradi."""
    if sales_count > 150:
        return 9
    elif sales_count > 100:
        return 8
    elif sales_count > 50:
        return 5
    elif sales_count > 30:
        return 2
    else:
        return 1


@staff_member_required
def salaries(request):
    month_filter = request.GET.get('month')
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

    # Compute monthly counts for badges (respecting operator filtering)
    count_qs = Transaction.objects.filter(is_refunded=False)
    if is_plain_op:
        count_qs = count_qs.filter(operator=request.user.operator)
    elif operator_filter:
        count_qs = count_qs.filter(operator_id=operator_filter)

    monthly_counts = {
        item['month']: item['count']
        for item in count_qs.annotate(month=ExtractMonth('date')).values('month').annotate(count=Count('id'))
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
            date__month=selected_month,
            is_refunded=False,
        )
        sales_count = transactions.count()
        total_collected = transactions.aggregate(total=Sum('amount'))['total'] or 0
        percentage = calculate_salary_percentage(sales_count)
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
        'selected_month': selected_month,
        'selected_operator': int(operator_filter) if operator_filter else '',
        'is_plain_operator': is_plain_op,
        'total_salary': total_salary,
        'total_collected_all': total_collected_all,
        'total_sales_all': total_sales_all,
        'salary_tiers': [
            ("0 - 30", "1%"),
            ("30 - 50", "2%"),
            ("50 - 100", "5%"),
            ("100 dan ortiq", "8%"),
            ("150 dan ortiq", "9%"),
        ],
    }
    context.update(admin.site.each_context(request))

    return render(request, 'admin/salaries.html', context)
