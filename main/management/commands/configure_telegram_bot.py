from urllib.parse import urljoin

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse


class Command(BaseCommand):
    help = "Telegram webhook va Mini App menyu tugmasini production URL ga ulaydi."

    def add_arguments(self, parser):
        parser.add_argument(
            '--base-url', required=True,
            help='Public HTTPS manzil, masalan https://crm.example.uz/',
        )
        parser.add_argument(
            '--web-app-url',
            help='Mini App public HTTPS manzili; berilmasa base-url/telegram-app/ ishlatiladi.',
        )

    def handle(self, *args, **options):
        base_url = options['base_url'].rstrip('/') + '/'
        web_app_url = options.get('web_app_url') or urljoin(base_url, reverse('main:telegram_app').lstrip('/'))
        webhook_url = urljoin(base_url, reverse('main:telegram_webhook').lstrip('/'))
        if not base_url.startswith('https://') or not web_app_url.startswith('https://'):
            raise CommandError('Telegram uchun base-url va web-app-url HTTPS bo‘lishi shart.')

        telegram = getattr(settings, 'TELEGRAM', {}) or {}
        token = telegram.get('BOT_TOKEN')
        if not token:
            raise CommandError('TELEGRAM_BOT_TOKEN sozlanmagan.')

        api_base = f'https://api.telegram.org/bot{token}/'
        webhook_payload = {'url': webhook_url, 'allowed_updates': ['message']}
        if telegram.get('WEBHOOK_SECRET'):
            webhook_payload['secret_token'] = telegram['WEBHOOK_SECRET']
        self._call(api_base, 'setWebhook', webhook_payload)
        self._call(api_base, 'setChatMenuButton', {
            'menu_button': {
                'type': 'web_app',
                'text': 'Kurslar',
                'web_app': {'url': web_app_url},
            },
        })
        self.stdout.write(self.style.SUCCESS(f'Webhook ulandi: {webhook_url}'))
        self.stdout.write(self.style.SUCCESS(f'Mini App menyusi ulandi: {web_app_url}'))

    @staticmethod
    def _call(api_base, method, payload):
        try:
            response = requests.post(api_base + method, json=payload, timeout=20)
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CommandError(f'Telegram {method} so‘rovi bajarilmadi: {exc}')
        if response.status_code != 200 or not data.get('ok'):
            raise CommandError(data.get('description') or f'Telegram {method}: HTTP {response.status_code}')
