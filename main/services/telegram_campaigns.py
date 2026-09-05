"""Database-backed Telegram broadcast queue and delivery helpers."""

import logging
import time
from pathlib import Path

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from main.models import TelegramCampaign, TelegramCampaignRecipient, TelegramUser
from main.services.telegram import send_bot_message, send_bot_photo


logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
SEND_INTERVAL_SECONDS = 0.06


def queue_campaign(campaign, queued_by):
    """Freeze the audience and queue a draft campaign for the worker."""
    try:
        with transaction.atomic():
            campaign = TelegramCampaign.objects.select_for_update().get(pk=campaign.pk)
            if campaign.status != TelegramCampaign.STATUS_DRAFT:
                raise ValueError("Faqat qoralama xabarni navbatga qo'yish mumkin.")

            audience = TelegramUser.objects.all()
            if campaign.audience == TelegramCampaign.AUDIENCE_READY:
                audience = audience.filter(onboarding_step=TelegramUser.STEP_READY)
            audience = audience.order_by('id')
            recipients = [
                TelegramCampaignRecipient(campaign=campaign, telegram_user=user)
                for user in audience.iterator(chunk_size=1000)
            ]
            if not recipients:
                raise ValueError("Tanlangan auditoriyada Telegram foydalanuvchilar topilmadi.")

            TelegramCampaignRecipient.objects.bulk_create(recipients, ignore_conflicts=True)
            total = campaign.recipients.count()
            campaign.status = TelegramCampaign.STATUS_QUEUED
            campaign.total_recipients = total
            campaign.queued_count = total
            campaign.sent_count = 0
            campaign.failed_count = 0
            campaign.blocked_count = 0
            campaign.queued_by = queued_by
            campaign.queued_at = timezone.now()
            campaign.save(update_fields=(
                'status', 'total_recipients', 'queued_count', 'sent_count',
                'failed_count', 'blocked_count', 'queued_by', 'queued_at', 'updated_at',
            ))
            return total
    except (TelegramCampaign.DoesNotExist, ValueError):
        raise
    except Exception as exc:
        logger.exception("Telegram kampaniyasini navbatga qo'yib bo'lmadi", extra={'campaign_id': campaign.pk})
        raise RuntimeError("Kampaniyani navbatga qo'yishda tizim xatosi yuz berdi.") from exc


def refresh_campaign_stats(campaign_id):
    """Recalculate every displayed statistic from recipient source-of-truth rows."""
    try:
        rows = (
            TelegramCampaignRecipient.objects.filter(campaign_id=campaign_id)
            .values('status').annotate(total=Count('id'))
        )
        counts = {row['status']: row['total'] for row in rows}
        queued = counts.get(TelegramCampaignRecipient.STATUS_QUEUED, 0)
        sending = counts.get(TelegramCampaignRecipient.STATUS_SENDING, 0)
        sent = counts.get(TelegramCampaignRecipient.STATUS_SENT, 0)
        failed = counts.get(TelegramCampaignRecipient.STATUS_FAILED, 0)
        blocked = counts.get(TelegramCampaignRecipient.STATUS_BLOCKED, 0)
        updates = {
            'total_recipients': sum(counts.values()),
            'queued_count': queued + sending,
            'sent_count': sent,
            'failed_count': failed,
            'blocked_count': blocked,
            'updated_at': timezone.now(),
        }
        if queued + sending == 0:
            updates['status'] = (
                TelegramCampaign.STATUS_COMPLETED_ERRORS
                if failed or blocked else TelegramCampaign.STATUS_COMPLETED
            )
            updates['completed_at'] = timezone.now()
        TelegramCampaign.objects.filter(pk=campaign_id).update(**updates)
        return updates
    except Exception:
        logger.exception("Telegram kampaniya statistikasi yangilanmadi", extra={'campaign_id': campaign_id})
        return None


def _reply_markup(campaign):
    try:
        if not campaign.button_text or not campaign.button_url:
            return None
        button = {'text': campaign.button_text}
        if campaign.button_opens_mini_app:
            button['web_app'] = {'url': campaign.button_url}
        else:
            button['url'] = campaign.button_url
        return {'inline_keyboard': [[button]]}
    except Exception:
        logger.exception("Telegram tugmasini tayyorlashda xato", extra={'campaign_id': campaign.pk})
        return None


def deliver_recipient(recipient):
    """Deliver one recipient with complete exception isolation."""
    try:
        campaign = recipient.campaign
        if campaign.image:
            try:
                image_path = Path(campaign.image.path)
                with image_path.open('rb') as photo_file:
                    ok, detail = send_bot_photo(recipient.telegram_user.telegram_id, photo_file)
            except (OSError, ValueError) as exc:
                return False, f"Rasmni o'qib bo'lmadi: {exc}"
            if not ok:
                return False, detail

        return send_bot_message(
            recipient.telegram_user.telegram_id,
            campaign.message,
            reply_markup=_reply_markup(campaign),
        )
    except Exception as exc:
        logger.exception(
            "Telegram xabarini yuborishda kutilmagan xato",
            extra={'recipient_id': recipient.pk, 'campaign_id': recipient.campaign_id},
        )
        return False, f"Kutilmagan xato: {exc}"


def _is_blocked_error(detail):
    try:
        value = (detail or '').lower()
        return any(marker in value for marker in (
            'bot was blocked', 'user is deactivated', 'chat not found',
            'forbidden: bot', 'bot can\'t initiate conversation',
        ))
    except Exception:
        return False


def process_campaign(campaign):
    """Process a campaign to completion; each recipient failure remains isolated."""
    try:
        if campaign.status == TelegramCampaign.STATUS_QUEUED:
            TelegramCampaign.objects.filter(pk=campaign.pk).update(
                status=TelegramCampaign.STATUS_SENDING,
                started_at=campaign.started_at or timezone.now(),
                updated_at=timezone.now(),
            )

        while True:
            recipient = None
            try:
                recipient = (
                    TelegramCampaignRecipient.objects.select_related('campaign', 'telegram_user')
                    .filter(
                        campaign_id=campaign.pk,
                        status__in=(
                            TelegramCampaignRecipient.STATUS_QUEUED,
                            TelegramCampaignRecipient.STATUS_SENDING,
                        ),
                        attempts__lt=MAX_ATTEMPTS,
                    )
                    .order_by('id').first()
                )
                if recipient is None:
                    break

                recipient.status = TelegramCampaignRecipient.STATUS_SENDING
                recipient.attempts += 1
                recipient.last_attempt_at = timezone.now()
                recipient.save(update_fields=('status', 'attempts', 'last_attempt_at'))
                ok, detail = deliver_recipient(recipient)
                if ok:
                    recipient.status = TelegramCampaignRecipient.STATUS_SENT
                    recipient.sent_at = timezone.now()
                    recipient.error_message = ''
                elif _is_blocked_error(detail):
                    recipient.status = TelegramCampaignRecipient.STATUS_BLOCKED
                    recipient.error_message = (detail or '')[:500]
                elif recipient.attempts >= MAX_ATTEMPTS:
                    recipient.status = TelegramCampaignRecipient.STATUS_FAILED
                    recipient.error_message = (detail or "Noma'lum Telegram xatosi")[:500]
                else:
                    recipient.status = TelegramCampaignRecipient.STATUS_QUEUED
                    recipient.error_message = (detail or "Noma'lum Telegram xatosi")[:500]
                recipient.save(update_fields=('status', 'sent_at', 'error_message'))
                refresh_campaign_stats(campaign.pk)
                try:
                    # Stay below Telegram's broadcast rate limit while the worker
                    # continues processing the queue in the background.
                    time.sleep(SEND_INTERVAL_SECONDS)
                except Exception:
                    logger.exception(
                        "Telegram yuborish oralig'ini kutishda xato",
                        extra={'campaign_id': campaign.pk, 'recipient_id': recipient.pk},
                    )
            except Exception:
                logger.exception(
                    "Telegram qabul qiluvchisini qayta ishlashda xato",
                    extra={'campaign_id': campaign.pk},
                )
                if recipient:
                    try:
                        recipient.status = TelegramCampaignRecipient.STATUS_FAILED
                        recipient.error_message = "Worker ichki xatosi"
                        recipient.save(update_fields=('status', 'error_message'))
                    except Exception:
                        logger.exception("Qabul qiluvchi xato holatini saqlab bo'lmadi")
                continue
        refresh_campaign_stats(campaign.pk)
    except Exception:
        logger.exception("Telegram kampaniyasini qayta ishlashda xato", extra={'campaign_id': campaign.pk})
        refresh_campaign_stats(campaign.pk)


def next_campaign():
    try:
        return TelegramCampaign.objects.filter(
            Q(status=TelegramCampaign.STATUS_QUEUED)
            | Q(status=TelegramCampaign.STATUS_SENDING)
        ).order_by('queued_at', 'id').first()
    except Exception:
        logger.exception("Telegram navbatidan kampaniya olinmadi")
        return None
