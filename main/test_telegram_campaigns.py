from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import (
    TelegramCampaign, TelegramCampaignRecipient, TelegramUser,
)
from .services.telegram_campaigns import process_campaign, queue_campaign


class TelegramCampaignQueueTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('campaign-admin', 'admin@example.com', 'pass')
        self.ready = TelegramUser.objects.create(
            telegram_id=10001, full_name='Tayyor User', phone_number='+998901111111',
            onboarding_step=TelegramUser.STEP_READY,
        )
        self.incomplete = TelegramUser.objects.create(
            telegram_id=10002, full_name='Yangi User', onboarding_step=TelegramUser.STEP_NAME,
        )
        self.campaign = TelegramCampaign.objects.create(
            title='Sentabr e’loni', message='<b>Yangi kurs</b>', created_by=self.admin,
        )

    def queue(self):
        total = queue_campaign(self.campaign, self.admin)
        self.campaign.refresh_from_db()
        return total

    def test_ready_audience_is_frozen_and_counted(self):
        self.assertEqual(self.queue(), 1)
        self.assertEqual(self.campaign.status, TelegramCampaign.STATUS_QUEUED)
        self.assertEqual(self.campaign.total_recipients, 1)
        self.assertEqual(self.campaign.queued_count, 1)
        recipient = self.campaign.recipients.get()
        self.assertEqual(recipient.telegram_user, self.ready)

    @patch('main.services.telegram_campaigns.send_bot_message', return_value=(True, None))
    def test_worker_marks_success_and_all_statistics(self, send_message):
        self.queue()
        process_campaign(self.campaign)
        self.campaign.refresh_from_db()
        recipient = self.campaign.recipients.get()
        self.assertEqual(recipient.status, TelegramCampaignRecipient.STATUS_SENT)
        self.assertEqual(self.campaign.status, TelegramCampaign.STATUS_COMPLETED)
        self.assertEqual(self.campaign.sent_count, 1)
        self.assertEqual(self.campaign.queued_count, 0)
        self.assertEqual(self.campaign.failed_count, 0)
        self.assertEqual(self.campaign.blocked_count, 0)
        self.assertEqual(send_message.call_count, 1)

    @patch(
        'main.services.telegram_campaigns.send_bot_message',
        return_value=(False, 'Forbidden: bot was blocked by the user'),
    )
    def test_blocked_user_does_not_stop_campaign(self, send_message):
        self.queue()
        process_campaign(self.campaign)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.blocked_count, 1)
        self.assertEqual(self.campaign.failed_count, 0)
        self.assertEqual(self.campaign.status, TelegramCampaign.STATUS_COMPLETED_ERRORS)
        self.assertEqual(send_message.call_count, 1)

    @patch(
        'main.services.telegram_campaigns.send_bot_message',
        return_value=(False, 'Telegram tarmoq xatosi'),
    )
    def test_transient_error_is_retried_then_counted(self, send_message):
        self.queue()
        process_campaign(self.campaign)
        self.campaign.refresh_from_db()
        recipient = self.campaign.recipients.get()
        self.assertEqual(recipient.status, TelegramCampaignRecipient.STATUS_FAILED)
        self.assertEqual(recipient.attempts, 3)
        self.assertEqual(self.campaign.failed_count, 1)
        self.assertEqual(send_message.call_count, 3)

    @patch(
        'main.services.telegram_campaigns.send_bot_message',
        side_effect=RuntimeError('unexpected Telegram failure'),
    )
    def test_unexpected_exception_is_isolated_and_counted(self, send_message):
        self.queue()
        process_campaign(self.campaign)
        self.campaign.refresh_from_db()
        recipient = self.campaign.recipients.get()
        self.assertEqual(recipient.status, TelegramCampaignRecipient.STATUS_FAILED)
        self.assertEqual(recipient.attempts, 3)
        self.assertEqual(self.campaign.failed_count, 1)
        self.assertEqual(self.campaign.queued_count, 0)
        self.assertEqual(send_message.call_count, 3)

    def test_campaign_dashboard_displays_all_stats(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin:main_telegramcampaign_changelist'))
        self.assertEqual(response.status_code, 200)
        for label in ('Obunachilar', 'Tayyor profil', 'Kampaniyalar', 'Yuborildi', 'Navbatda', 'Xato', 'Bloklangan'):
            self.assertContains(response, label)
