"""Telegram Mini App init-data validation and request authentication."""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from django.conf import settings

from main.models import TelegramUser


class TelegramAuthenticationError(ValueError):
    pass


def validate_init_data(init_data, max_age=86400):
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop('hash', '')
    if not received_hash:
        raise TelegramAuthenticationError('Telegram tasdiqlash imzosi topilmadi.')

    token = (getattr(settings, 'TELEGRAM', {}) or {}).get('BOT_TOKEN', '')
    if not token:
        raise TelegramAuthenticationError('Telegram bot tokeni sozlanmagan.')

    data_check_string = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    secret_key = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise TelegramAuthenticationError("Telegram ma'lumotlari haqiqiy emas.")

    try:
        auth_date = int(values.get('auth_date', '0'))
    except (TypeError, ValueError):
        raise TelegramAuthenticationError("Telegram auth_date noto'g'ri.")
    if max_age and abs(int(time.time()) - auth_date) > max_age:
        raise TelegramAuthenticationError('Telegram sessiyasi eskirgan.')

    try:
        user = json.loads(values.get('user', '{}'))
    except json.JSONDecodeError:
        raise TelegramAuthenticationError("Telegram foydalanuvchi ma'lumoti noto'g'ri.")
    if not user.get('id'):
        raise TelegramAuthenticationError('Telegram foydalanuvchi ID topilmadi.')
    return user


def telegram_user_from_request(request):
    init_data = request.headers.get('X-Telegram-Init-Data', '')
    if init_data:
        user_data = validate_init_data(init_data)
        defaults = {
            'username': user_data.get('username', ''),
        }
        first_name = user_data.get('first_name', '')
        last_name = user_data.get('last_name', '')
        suggested_name = ' '.join(filter(None, (first_name, last_name))).strip()
        account, _ = TelegramUser.objects.get_or_create(
            telegram_id=user_data['id'], defaults={**defaults, 'full_name': suggested_name},
        )
        updates = []
        if defaults['username'] and account.username != defaults['username']:
            account.username = defaults['username']
            updates.append('username')
        if suggested_name and not account.full_name:
            account.full_name = suggested_name
            updates.append('full_name')
        if updates:
            account.save(update_fields=updates + ['updated_at'])
        return account

    if settings.DEBUG and request.headers.get('X-Telegram-Demo') == '1':
        account, _ = TelegramUser.objects.get_or_create(
            telegram_id=900000001,
            defaults={
                'username': 'norbekov_demo',
                'full_name': 'Demo Foydalanuvchi',
                'phone_number': '+998901234567',
                'onboarding_step': TelegramUser.STEP_READY,
            },
        )
        return account

    raise TelegramAuthenticationError('Telegram orqali qayta oching.')
