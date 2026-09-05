import logging
import signal
import time

from django.core.management.base import BaseCommand

from main.services.telegram_campaigns import next_campaign, process_campaign


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Telegram reklama/xabarlar navbatini fon rejimida yuboradi."

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Navbatni bir marta ishlab, chiqadi.')
        parser.add_argument('--poll', type=float, default=2.0, help='Bo‘sh navbatni tekshirish oralig‘i.')

    def handle(self, *args, **options):
        self._stop = False
        try:
            signal.signal(signal.SIGTERM, self._request_stop)
            signal.signal(signal.SIGINT, self._request_stop)
        except (ValueError, OSError):
            logger.exception("Worker signal handlerlarini o'rnata olmadi")

        once = options['once']
        poll = max(0.5, options['poll'])
        self.stdout.write('Telegram campaign worker started')
        while not self._stop:
            try:
                campaign = next_campaign()
                if campaign:
                    process_campaign(campaign)
                    continue
                if once:
                    break
                time.sleep(poll)
            except Exception:
                logger.exception("Telegram worker siklida kutilmagan xato")
                if once:
                    break
                try:
                    time.sleep(poll)
                except Exception:
                    logger.exception("Telegram worker kutishida xato")
        self.stdout.write('Telegram campaign worker stopped')

    def _request_stop(self, signum, frame):
        try:
            self._stop = True
        except Exception:
            logger.exception("Telegram workerni to'xtatib bo'lmadi")
