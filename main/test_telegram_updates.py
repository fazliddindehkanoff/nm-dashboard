from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from main.management.commands.process_telegram_updates import load_offset, save_offset
from main.telegram_views import TelegramDeliveryError


class TelegramUpdateWorkerTests(SimpleTestCase):
    def test_offset_is_saved_atomically(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'offset'
            self.assertIsNone(load_offset(path))
            save_offset(path, 43)
            self.assertEqual(load_offset(path), 43)

    @patch('main.management.commands.process_telegram_updates.process_telegram_update')
    @patch('main.management.commands.process_telegram_updates.telegram_api_request')
    def test_once_processes_update_and_persists_next_offset(self, api_request, process_update):
        response = Mock(status_code=200)
        response.json.return_value = {
            'ok': True,
            'result': [{'update_id': 91, 'message': {'text': '/start'}}],
        }
        api_request.return_value = response

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'offset'
            call_command(
                'process_telegram_updates', '--once', '--timeout', '1',
                '--offset-file', str(path), verbosity=0,
            )
            self.assertEqual(load_offset(path), 92)

        process_update.assert_called_once_with(
            {'update_id': 91, 'message': {'text': '/start'}},
        )

    @patch('main.management.commands.process_telegram_updates.process_telegram_update')
    @patch('main.management.commands.process_telegram_updates.telegram_api_request')
    def test_once_skips_permanent_delivery_failure(self, api_request, process_update):
        response = Mock(status_code=200)
        response.json.return_value = {
            'ok': True,
            'result': [{'update_id': 105, 'message': {'text': '/start'}}],
        }
        api_request.return_value = response
        process_update.side_effect = TelegramDeliveryError('Forbidden', retryable=False)

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'offset'
            call_command(
                'process_telegram_updates', '--once', '--timeout', '1',
                '--offset-file', str(path), verbosity=0,
            )
            self.assertEqual(load_offset(path), 106)
