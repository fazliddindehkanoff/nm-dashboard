from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import admin
from .models import Operator, Transaction, Client, Group
from django.db.models import Sum, Count
from datetime import datetime

import json
from django.db.models.functions import TruncMonth
from datetime import timedelta

def dashboard_callback(request, context):
    month_filter = request.GET.get('month')
    operator_filter = request.GET.get('operator_id')

    transactions = Transaction.objects.all()
    if month_filter:
        transactions = transactions.filter(date__month=month_filter)
    if operator_filter:
        transactions = transactions.filter(operator_id=operator_filter)

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
        "total_income": transactions.aggregate(total=Sum('amount'))['total'] or 0,
        "total_clients": Client.objects.count(),
        "total_groups": Group.objects.count(),
        "transactions_count": transactions.count(),
        "recent_transactions": transactions.select_related('client', 'group').order_by('-date', '-id')[:6],
        "operators": Operator.objects.all(),
        "months": [
            (1, "Yanvar"), (2, "Fevral"), (3, "Mart"), (4, "Aprel"),
            (5, "May"), (6, "Iyun"), (7, "Iyul"), (8, "Avgust"),
            (9, "Sentabr"), (10, "Oktabr"), (11, "Noyabr"), (12, "Dekabr")
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
    return context

@staff_member_required
def kpi_calculator(request):
    operators = Operator.objects.all()
    current_year = datetime.now().year
    years = range(2023, current_year + 2)
    months = [
        (1, "Yanvar"), (2, "Fevral"), (3, "Mart"), (4, "Aprel"),
        (5, "May"), (6, "Iyun"), (7, "Iyul"), (8, "Avgust"),
        (9, "Sentabr"), (10, "Oktabr"), (11, "Noyabr"), (12, "Dekabr")
    ]
    
    context = {
        'operators': operators,
        'years': years,
        'months': months,
        'title': "KPI Kalkulyator",
        'current_year': current_year,
        'current_month': datetime.now().month,
    }
    context.update(admin.site.each_context(request))
    
    if request.method == 'POST':
        operator_id = request.POST.get('operator')
        month = request.POST.get('month')
        year = request.POST.get('year')
        kpi_percentage = request.POST.get('kpi_percentage')
        
        if operator_id and month and year and kpi_percentage:
            try:
                operator = Operator.objects.get(id=operator_id)
                kpi_percentage = float(kpi_percentage)
                
                transactions = Transaction.objects.filter(
                    operator=operator,
                    date__year=year,
                    date__month=month
                )
                
                total_collected = transactions.aggregate(total=Sum('amount'))['total'] or 0
                kpi_amount = float(total_collected) * (kpi_percentage / 100)
                
                context.update({
                    'selected_operator': operator,
                    'selected_month': int(month),
                    'selected_year': int(year),
                    'kpi_percentage': kpi_percentage,
                    'total_collected': total_collected,
                    'kpi_amount': kpi_amount,
                    'transactions_count': transactions.count(),
                    'result': True
                })
            except Exception as e:
                context['error'] = str(e)
                
    return render(request, 'admin/kpi_calculator.html', context)
