import json
import logging
import os
import signal
import time
from pathlib import Path

import requests
from django.core.management.base import BaseCommand

from main.services.telegram import TelegramNotConfigured, telegram_api_request
from main.telegram_views import TelegramDeliveryError, process_telegram_update


logger = logging.getLogger(__name__)


def load_offset(path):
    try:
        return int(Path(path).read_text().strip())
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return None


def save_offset(path, offset):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(str(offset))
    os.replace(temporary, target)


class Command(BaseCommand):
    help = "Telegram xabarlarini IPv6 orqali long polling bilan qabul qiladi."

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Bitta polling so‘rovidan keyin chiqadi.')
        parser.add_argument('--timeout', type=int, default=25, help='Telegram long-poll muddati.')
        parser.add_argument(
            '--offset-file',
            default=os.environ.get(
                'TELEGRAM_UPDATE_OFFSET_FILE',
                '/var/lib/norbekov/telegram-update-offset',
            ),
            help='Oxirgi tasdiqlangan update offset fayli.',
        )

    def handle(self, *args, **options):
        self._stop = False
        try:
            signal.signal(signal.SIGTERM, self._request_stop)
            signal.signal(signal.SIGINT, self._request_stop)
        except (ValueError, OSError):
            logger.exception("Telegram update worker signal handlerini o‘rnata olmadi")

        once = options['once']
        poll_timeout = min(max(options['timeout'], 1), 50)
        offset_file = options['offset_file']
        offset = load_offset(offset_file)
        self.stdout.write('Telegram update worker started')

        while not self._stop:
            payload = {
                'timeout': poll_timeout,
                'allowed_updates': ['message'],
            }
            if offset is not None:
                payload['offset'] = offset
            try:
                response = telegram_api_request(
                    'getUpdates', json=payload, timeout=(2, poll_timeout + 5),
                )
                data = response.json()
                if response.status_code != 200 or not data.get('ok'):
                    raise RuntimeError(
                        data.get('description') or f'Telegram HTTP {response.status_code}'
                    )
                updates = data.get('result') or []
                for update in updates:
                    update_id = int(update['update_id'])
                    try:
                        process_telegram_update(update)
                    except TelegramDeliveryError as exc:
                        if exc.retryable:
                            raise
                        logger.warning(
                            "Telegram update %s permanently undeliverable; skipping: %s",
                            update_id, exc,
                        )
                    offset = update_id + 1
                    save_offset(offset_file, offset)
            except TelegramDeliveryError as exc:
                logger.warning("Telegram javobi yuborilmadi, update qayta uriniladi: %s", exc)
                time.sleep(1)
            except (TelegramNotConfigured, requests.RequestException, ValueError, RuntimeError):
                logger.exception("Telegram update polling vaqtinchalik ishlamadi")
                time.sleep(2)
            except Exception:
                logger.exception("Telegram update worker kutilmagan xatoga uchradi")
                time.sleep(2)

            if once:
                break

        self.stdout.write('Telegram update worker stopped')

    def _request_stop(self, signum, frame):
        self._stop = True
