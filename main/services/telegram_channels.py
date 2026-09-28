"""Telegram channel access for paid group members.

A group may name a Telegram channel where our bot is an administrator. Every
client with a confirmed payment gets a personal join-request link and the bot
approves only that client's request. ``channel_removal_days`` after the group
starts, clients who still owe money are banned and told how to come back;
paying the balance lifts the ban and sends a fresh link.
"""

import logging
import re
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.html import escape

from main.models import TelegramChannelMember, TelegramUser, TransactionClient
from main.templatetags.money import money
from .telegram import TelegramAPIError, TelegramNotConfigured, bot_api, bot_user_id, send_bot_message

logger = logging.getLogger(__name__)

REMOVAL_HOUR = 9  # Removals start in the morning, not at midnight.
LEASE = timedelta(minutes=5)
RETRY_AFTER = timedelta(minutes=10)
PERMANENT_RETRY_AFTER = timedelta(hours=1)
REQUIRED_RIGHTS = (
    ('can_invite_users', "Invite users via link (havola orqali qo'shish)"),
    ('can_restrict_members', "Ban users (foydalanuvchilarni chiqarish)"),
)


class ChannelSetupError(ValueError):
    pass


def normalise_channel_id(value):
    value = (value or '').strip()
    link = re.fullmatch(r'(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{3,})/?', value)
    if link:
        return '@' + link.group(1)
    if re.fullmatch(r'\d{6,}', value):
        # IDs are often copied without the -100 prefix channels carry in the Bot API.
        return '-100' + value
    return value


def check_channel(value):
    """Return ``(chat_id, title)`` when our bot can manage the channel, else raise ``ChannelSetupError``."""
    chat_id = normalise_channel_id(value)
    if not re.fullmatch(r'-\d{5,}|@[A-Za-z][A-Za-z0-9_]{3,}', chat_id):
        raise ChannelSetupError("Kanal ID sini -1001234567890 yoki @kanal_nomi ko'rinishida kiriting.")
    try:
        chat = bot_api('getChat', chat_id=chat_id)
        if chat.get('type') not in ('channel', 'supergroup'):
            raise ChannelSetupError("Bu ID kanalga tegishli emas. Telegram kanal ID sini kiriting.")
        membership = bot_api('getChatMember', chat_id=chat['id'], user_id=bot_user_id())
    except TelegramNotConfigured as exc:
        raise ChannelSetupError(str(exc))
    except TelegramAPIError as exc:
        if exc.retryable:
            raise ChannelSetupError(f"Telegram bilan bog'lanib bo'lmadi, birozdan keyin qayta urinib ko'ring. ({exc})")
        raise ChannelSetupError(
            "Kanal topilmadi yoki bot kanalga qo'shilmagan. Kanal ID sini tekshiring va botni kanalga admin qiling."
        )
    title = chat.get('title') or chat_id
    if membership.get('status') != 'administrator':
        raise ChannelSetupError(f"Bot «{title}» kanalida admin emas. Botni kanalga admin qilib qo'shing.")
    missing = [label for right, label in REQUIRED_RIGHTS if not membership.get(right)]
    if missing:
        raise ChannelSetupError(
            f"Bot «{title}» kanalida admin, lekin bu huquqlar yoqilmagan: {', '.join(missing)}."
        )
    return str(chat['id']), title


def removal_deadline(group):
    """Moment from which clients who still owe money are removed, or None."""
    if not group.telegram_channel_id or group.channel_removal_days is None or not group.start_date:
        return None
    day = group.start_date + timedelta(days=group.channel_removal_days)
    return timezone.make_aware(datetime.combine(day, time(REMOVAL_HOUR)))


def access_decision(member, now):
    """Return ``(allowed, debt, recheck_at)`` for the client's current payments."""
    totals = TransactionClient.objects.filter(
        client_id=member.client_id,
        transaction__group_id=member.group_id,
        transaction__is_confirmed=True,
        transaction__is_refunded=False,
    ).aggregate(count=models.Count('id'), debt=models.Sum('debt'))
    debt = totals['debt'] or Decimal(0)
    if not totals['count']:
        return False, debt, None
    if debt <= 0:
        return True, debt, None
    deadline = removal_deadline(member.group)
    if deadline is None:
        return True, debt, None
    if now < deadline:
        return True, debt, deadline
    return False, debt, None


def client_chat_ids(member):
    """Telegram chats of the client: their own account, the family buyer, or the same phone."""
    client = member.client
    query = models.Q(client=client) | models.Q(purchases__members__client=client)
    phone = re.sub(r'\D', '', client.phone_number or '')
    if len(phone) >= 9:
        query |= models.Q(phone_number__endswith=phone[-9:])
    chat_ids = sorted(set(TelegramUser.objects.filter(query).values_list('telegram_id', flat=True)))
    if member.telegram_id and member.telegram_id not in chat_ids:
        chat_ids.append(member.telegram_id)
    return chat_ids


def _update(member, **fields):
    for name, value in fields.items():
        setattr(member, name, value)
    TelegramChannelMember.objects.filter(pk=member.pk).update(updated_at=timezone.now(), **fields)


def _notify(member, text, button=None):
    """Send to every chat of the client; return ``(delivered, error)``."""
    chat_ids = client_chat_ids(member)
    if not chat_ids:
        return False, "Mijozning Telegram akkaunti topilmadi — havolani qo'lda yuboring."
    markup = {'inline_keyboard': [[{'text': button[0], 'url': button[1]}]]} if button else None
    errors = []
    delivered = False
    for chat_id in chat_ids:
        ok, detail = send_bot_message(chat_id, text, reply_markup=markup)
        delivered = delivered or ok
        if not ok:
            errors.append(detail or '')
    if delivered:
        return True, ''
    return False, ('Xabar yetkazilmadi: ' + '; '.join(filter(None, errors)))[:255]


def _link_name(client):
    return f"#{client.pk} {client.full_name}"[:32]


def _invite_message(member, returning):
    name = escape(member.client.full_name)
    course = escape(member.group.course.name)
    if returning:
        return (
            f"✅ <b>To'lovingiz to'liq qabul qilindi, rahmat!</b>\n\n👤 {name}\n📚 {course}\n\n"
            "Telegram kanalga qaytish uchun pastdagi tugmani bosing."
        )
    return (
        f"📚 <b>{course}</b> guruhining Telegram kanali\n👤 {name}\n\n"
        "Kanalga qo'shilish uchun pastdagi tugmani bosing. Havola shaxsiy — faqat shu ishtirokchi uchun."
    )


def _removal_message(member, debt, was_joined):
    lines = [f"⚠️ <b>{escape(member.group.course.name)}</b>", f"👤 {escape(member.client.full_name)}", ""]
    if was_joined:
        lines.append("Kurs to'lovi to'liq amalga oshirilmagani uchun guruhning Telegram kanalidan vaqtincha chiqarildingiz.")
    else:
        lines.append("Kurs to'lovi to'liq amalga oshirilmagani uchun kanalga qo'shilish havolasi to'xtatildi.")
    if debt > 0:
        lines.append(f"Qolgan to'lov: <b>{money(debt)} so'm</b>")
    lines += ["", "To'lovni to'liq qilsangiz, sizni kanalga qaytaramiz."]
    return "\n".join(lines)


def sync_member(member, now=None):
    """Bring one client's channel access in line with their payment; return the next check time."""
    now = now or timezone.now()
    channel = member.group.telegram_channel_id
    if not channel:
        return None
    allowed, debt, recheck_at = access_decision(member, now)
    if allowed:
        if member.status in (TelegramChannelMember.STATUS_NEW, TelegramChannelMember.STATUS_REMOVED):
            if member.status == TelegramChannelMember.STATUS_REMOVED and member.telegram_id:
                bot_api('unbanChatMember', chat_id=channel, user_id=member.telegram_id, only_if_banned=True)
            link = bot_api(
                'createChatInviteLink', chat_id=channel, name=_link_name(member.client), creates_join_request=True,
            )['invite_link']
            _update(member, status=TelegramChannelMember.STATUS_INVITED, invite_link=link, link_sent_at=None)
        if member.status == TelegramChannelMember.STATUS_INVITED and not member.link_sent_at:
            returning = member.removed_at is not None
            button = ("Kanalga qaytish" if returning else "Kanalga qo'shilish", member.invite_link)
            delivered, error = _notify(member, _invite_message(member, returning), button)
            _update(member, link_sent_at=now if delivered else None, last_error=error)
        elif member.last_error:
            _update(member, last_error='')
    elif member.status in (TelegramChannelMember.STATUS_INVITED, TelegramChannelMember.STATUS_JOINED):
        was_joined = member.status == TelegramChannelMember.STATUS_JOINED
        told_about_link = member.link_sent_at is not None
        if was_joined and member.telegram_id:
            bot_api('banChatMember', chat_id=channel, user_id=member.telegram_id)
        if member.invite_link:
            try:
                bot_api('revokeChatInviteLink', chat_id=channel, invite_link=member.invite_link)
            except TelegramAPIError as exc:
                if exc.retryable:
                    raise
        _update(
            member, status=TelegramChannelMember.STATUS_REMOVED, removed_at=now,
            invite_link='', link_sent_at=None, last_error='',
        )
        if was_joined or told_about_link:
            _delivered, error = _notify(member, _removal_message(member, debt, was_joined))
            _update(member, last_error=error)
    return recheck_at


def process_due_members(limit=10):
    """Worker step: sync channel members whose check is due."""
    now = timezone.now()
    due = list(
        TelegramChannelMember.objects.filter(next_sync_at__lte=now)
        .order_by('next_sync_at', 'id').values_list('pk', flat=True)[:limit]
    )
    for pk in due:
        lease = now + LEASE
        if not TelegramChannelMember.objects.filter(pk=pk, next_sync_at__lte=now).update(next_sync_at=lease):
            continue
        member = TelegramChannelMember.objects.select_related('group__course', 'client').get(pk=pk)
        try:
            next_at = sync_member(member, now)
        except TelegramAPIError as exc:
            next_at = now + (RETRY_AFTER if exc.retryable else PERMANENT_RETRY_AFTER)
            TelegramChannelMember.objects.filter(pk=pk).update(last_error=str(exc)[:255])
        except TelegramNotConfigured as exc:
            next_at = now + PERMANENT_RETRY_AFTER
            TelegramChannelMember.objects.filter(pk=pk).update(last_error=str(exc)[:255])
        except Exception:
            logger.exception("Telegram kanal a'zosini sinxronlab bo'lmadi: %s", pk)
            next_at = now + RETRY_AFTER
            TelegramChannelMember.objects.filter(pk=pk).update(last_error="Kutilmagan xato, qayta uriniladi.")
        # A payment change during this sync marked the row due again; keep that mark.
        TelegramChannelMember.objects.filter(pk=pk, next_sync_at=lease).update(next_sync_at=next_at)
    return len(due)


def refresh_group(group, channel_changed=False):
    """Add paid clients and re-check everyone after the group's channel settings change."""
    if channel_changed:
        group.channel_members.all().delete()
    if not group.telegram_channel_id:
        return
    now = timezone.now()
    paid_clients = set(
        TransactionClient.objects.filter(
            transaction__group=group, transaction__is_confirmed=True, transaction__is_refunded=False,
        ).values_list('client_id', flat=True)
    )
    existing = set(group.channel_members.values_list('client_id', flat=True))
    TelegramChannelMember.objects.bulk_create(
        [TelegramChannelMember(group=group, client_id=client_id, next_sync_at=now) for client_id in paid_clients - existing],
        ignore_conflicts=True,
    )
    group.channel_members.update(next_sync_at=now)


def resend_link(member):
    fields = {'next_sync_at': timezone.now()}
    if member.status == TelegramChannelMember.STATUS_INVITED:
        fields['link_sent_at'] = None
    TelegramChannelMember.objects.filter(pk=member.pk).update(**fields)


def schedule_undelivered_links(client_id):
    """A client who has just started the bot can now receive links we could not send before."""
    TelegramChannelMember.objects.filter(
        client_id=client_id, status=TelegramChannelMember.STATUS_INVITED, link_sent_at__isnull=True,
    ).update(next_sync_at=timezone.now())


def handle_join_request(join_request):
    """Approve a join request only for the client the personal link belongs to."""
    chat_id = str((join_request.get('chat') or {}).get('id') or '')
    user_id = (join_request.get('from') or {}).get('id')
    if not chat_id or not user_id:
        return
    members = TelegramChannelMember.objects.filter(group__telegram_channel_id=chat_id).select_related(
        'group__course', 'client',
    )
    link = (join_request.get('invite_link') or {}).get('invite_link') or ''
    member = members.filter(invite_link=link).first() if link else None
    if member is None:
        member = members.filter(telegram_id=user_id).first()
    if member is None:
        account = TelegramUser.objects.filter(telegram_id=user_id, client__isnull=False).first()
        member = members.filter(client_id=account.client_id).first() if account else None
    if member is None:
        return  # Not one of our clients: the channel admins decide.

    now = timezone.now()
    allowed, _debt, recheck_at = access_decision(member, now)
    rejoining = member.status == TelegramChannelMember.STATUS_JOINED
    if not allowed or (rejoining and member.telegram_id != user_id):
        # Debtors stay out, and a personal link admits only one account.
        bot_api('declineChatJoinRequest', chat_id=chat_id, user_id=user_id)
        return
    if member.status == TelegramChannelMember.STATUS_REMOVED:
        return  # The worker lifts the ban and sends a fresh link.
    try:
        bot_api('approveChatJoinRequest', chat_id=chat_id, user_id=user_id)
    except TelegramAPIError as exc:
        if 'USER_ALREADY_PARTICIPANT' not in str(exc):
            raise
    _update(
        member, status=TelegramChannelMember.STATUS_JOINED, telegram_id=user_id,
        joined_at=member.joined_at if rejoining else now, removed_at=None, last_error='',
        next_sync_at=recheck_at,
    )
