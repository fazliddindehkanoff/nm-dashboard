from django.urls import path
from .views import cashflow, salaries, qr_verify
from .telegram_views import (
    telegram_app,
    telegram_app_accept_contract,
    telegram_app_accept_terms,
    telegram_app_bootstrap,
    telegram_app_contract,
    telegram_app_create_purchase,
    telegram_app_questionnaire,
    telegram_app_simulate_payment,
    telegram_app_terms,
    telegram_webhook,
    telegram_app_payment,
    telegram_app_payment_status,
    telegram_app_check_payment,
    multicard_callback,
)

app_name = 'main'

urlpatterns = [
    path('salaries/', salaries, name='salaries'),
    path('qr-verify/', qr_verify, name='qr_verify'),
    path('cashflow/', cashflow, name='cashflow'),
    path('telegram/webhook/', telegram_webhook, name='telegram_webhook'),
    path('payments/multicard/callback/', multicard_callback, name='multicard_callback'),
    path('telegram-app/', telegram_app, name='telegram_app'),
    path('telegram-app/api/bootstrap/', telegram_app_bootstrap, name='telegram_app_bootstrap'),
    path('telegram-app/api/legal/terms/', telegram_app_terms, name='telegram_app_terms'),
    path(
        'telegram-app/api/legal/terms/accept/',
        telegram_app_accept_terms,
        name='telegram_app_accept_terms',
    ),
    path('telegram-app/api/purchases/', telegram_app_create_purchase, name='telegram_app_create_purchase'),
    path(
        'telegram-app/api/purchases/<int:purchase_id>/contract/',
        telegram_app_contract,
        name='telegram_app_contract',
    ),
    path(
        'telegram-app/api/purchases/<int:purchase_id>/contract/accept/',
        telegram_app_accept_contract,
        name='telegram_app_accept_contract',
    ),
    path(
        'telegram-app/api/purchases/<int:purchase_id>/payment/',
        telegram_app_payment,
        name='telegram_app_payment',
    ),
    path(
        'telegram-app/api/purchases/<int:purchase_id>/payment/status/',
        telegram_app_payment_status,
        name='telegram_app_payment_status',
    ),
    path(
        'telegram-app/api/purchases/<int:purchase_id>/payment/check/',
        telegram_app_check_payment,
        name='telegram_app_check_payment',
    ),
    path(
        'telegram-app/api/purchases/<int:purchase_id>/demo-payment/',
        telegram_app_simulate_payment,
        name='telegram_app_simulate_payment',
    ),
    path(
        'telegram-app/api/purchases/<int:purchase_id>/questionnaire/',
        telegram_app_questionnaire,
        name='telegram_app_questionnaire',
    ),
]
