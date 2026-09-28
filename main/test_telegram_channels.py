from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Client, Course, Group, TelegramChannelMember, TelegramUser, Transaction, TransactionClient
from .services.telegram import TelegramAPIError
from .services.telegram_channels import process_due_members, removal_deadline
from .telegram_views import process_telegram_update

CHANNEL = '-1001234567890'
TELEGRAM_SETTINGS = {
    'BOT_TOKEN': '123456:test-token',
    'CHAT_ID': '',
    'WEB_APP_URL': 'https://example.com/telegram-app/',
    'WEBHOOK_SECRET': '',
}
ADMIN_RIGHTS = {'status': 'administrator', 'can_invite_users': True, 'can_restrict_members': True}


class FakeBot:
    """Records Bot API calls; ``responses`` overrides the result per method."""

    def __init__(self, **responses):
        self.calls = []
        self.responses = responses
        self.links = 0

    def __call__(self, method, **payload):
        self.calls.append((method, payload))
        response = self.responses.get(method)
        if isinstance(response, Exception):
            raise response
        if response is not None:
            return response
        if method == 'createChatInviteLink':
            self.links += 1
            return {'invite_link': f'https://t.me/+personal{self.links}'}
        return True

    def called(self, method):
        return [payload for name, payload in self.calls if name == method]


class ChannelTestMixin:
    def setUp(self):
        super().setUp()
        self.bot = FakeBot()
        self.sent = []
        patches = [
            mock.patch('main.services.telegram_channels.bot_api', side_effect=lambda method, **kw: self.bot(method, **kw)),
            mock.patch('main.services.telegram_channels.send_bot_message', side_effect=self._send),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _send(self, chat_id, text, reply_markup=None):
        self.sent.append({'chat_id': chat_id, 'text': text, 'markup': reply_markup})
        return True, None


@override_settings(TELEGRAM=TELEGRAM_SETTINGS)
class ChannelAccessTests(ChannelTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.course = Course.objects.create(name='Sog‘lomlashtirish', price=Decimal('2000000'), number_of_days=10)
        self.group = Group.objects.create(
            course=self.course, start_date=timezone.localdate() + timedelta(days=2),
            telegram_channel_id=CHANNEL, telegram_channel_title='NM kanal', channel_removal_days=3,
        )
        self.buyer = Client.objects.create(full_name='Kanal Mijoz', phone_number='+998 90 123 45 67')
        TelegramUser.objects.create(
            telegram_id=5551, full_name='Kanal Mijoz', phone_number='+998901234567',
            onboarding_step=TelegramUser.STEP_READY,
        )

    def pay(self, amount, confirmed=True, client=None):
        payment = Transaction.objects.create(
            group=self.group, date=timezone.localdate(), amount=Decimal(amount),
            payment_type='bron', is_confirmed=confirmed,
        )
        TransactionClient.objects.create(transaction=payment, client=client or self.buyer)
        payment.save()  # recalculates shares and debts like the admin does
        return payment

    def member(self):
        return TelegramChannelMember.objects.get(group=self.group, client=self.buyer)

    def join(self, user_id, link):
        process_telegram_update({'update_id': 1, 'chat_join_request': {
            'chat': {'id': int(CHANNEL), 'type': 'channel'}, 'from': {'id': user_id}, 'user_chat_id': user_id,
            'date': 0, 'invite_link': {'invite_link': link, 'creates_join_request': True},
        }})

    def run_worker(self):
        TelegramChannelMember.objects.update(next_sync_at=timezone.now())
        process_due_members()

    def start_group_in_the_past(self):
        Group.objects.filter(pk=self.group.pk).update(start_date=timezone.localdate() - timedelta(days=10))

    def test_confirmed_payment_sends_a_personal_join_request_link(self):
        self.pay(500000)
        process_due_members()

        member = self.member()
        link = self.bot.called('createChatInviteLink')[0]
        self.assertEqual((link['chat_id'], link['creates_join_request']), (CHANNEL, True))
        self.assertEqual(member.status, TelegramChannelMember.STATUS_INVITED)
        self.assertIsNotNone(member.link_sent_at)
        self.assertEqual(member.next_sync_at, removal_deadline(self.group))  # re-checked at the deadline
        self.assertEqual(self.sent[0]['chat_id'], 5551)  # matched by phone
        self.assertEqual(self.sent[0]['markup']['inline_keyboard'][0][0]['url'], member.invite_link)

    def test_unconfirmed_payment_gets_no_link(self):
        self.pay(500000, confirmed=False)
        self.assertFalse(TelegramChannelMember.objects.exists())

    def test_personal_link_admits_only_the_first_account(self):
        self.pay(500000)
        process_due_members()
        link = self.member().invite_link

        self.join(7777, link)
        member = self.member()
        self.assertEqual((member.status, member.telegram_id), (TelegramChannelMember.STATUS_JOINED, 7777))
        self.assertEqual(self.bot.called('approveChatJoinRequest'), [{'chat_id': CHANNEL, 'user_id': 7777}])

        self.join(8888, link)
        self.assertEqual(self.bot.called('declineChatJoinRequest'), [{'chat_id': CHANNEL, 'user_id': 8888}])
        self.assertEqual(self.member().telegram_id, 7777)

    def test_unknown_join_requests_are_left_to_channel_admins(self):
        self.join(9999, 'https://t.me/+someone-else')
        self.assertEqual(self.bot.calls, [])

    def test_debtor_is_removed_after_the_deadline_and_readmitted_after_full_payment(self):
        self.pay(500000)
        process_due_members()
        self.join(7777, self.member().invite_link)
        self.start_group_in_the_past()
        self.sent.clear()

        self.run_worker()
        member = self.member()
        self.assertEqual(member.status, TelegramChannelMember.STATUS_REMOVED)
        self.assertEqual(self.bot.called('banChatMember'), [{'chat_id': CHANNEL, 'user_id': 7777}])
        self.assertEqual(len(self.bot.called('revokeChatInviteLink')), 1)
        self.assertEqual({message['chat_id'] for message in self.sent}, {5551, 7777})
        self.assertIn("Qolgan to'lov: <b>1 500 000 so'm</b>", self.sent[0]['text'])
        self.assertIn("To'lovni to'liq qilsangiz, sizni kanalga qaytaramiz.", self.sent[0]['text'])

        # The general link cannot bring a debtor back.
        self.join(7777, 'https://t.me/+general')
        self.assertEqual(self.bot.called('declineChatJoinRequest'), [{'chat_id': CHANNEL, 'user_id': 7777}])

        self.sent.clear()
        self.pay(1500000)  # payment confirmation marks the member due
        process_due_members()
        member = self.member()
        self.assertEqual(
            self.bot.called('unbanChatMember'), [{'chat_id': CHANNEL, 'user_id': 7777, 'only_if_banned': True}],
        )
        self.assertEqual(member.status, TelegramChannelMember.STATUS_INVITED)
        self.assertIn("To'lovingiz to'liq qabul qilindi", self.sent[0]['text'])
        self.assertEqual(self.sent[0]['markup']['inline_keyboard'][0][0]['text'], 'Kanalga qaytish')

        self.join(7777, member.invite_link)
        member = self.member()
        self.assertEqual(member.status, TelegramChannelMember.STATUS_JOINED)
        self.assertIsNone(member.removed_at)
        self.assertIsNone(member.next_sync_at)

    def test_fully_paid_client_stays_after_the_deadline(self):
        self.pay(2000000)
        self.start_group_in_the_past()
        process_due_members()
        member = self.member()
        self.assertEqual(member.status, TelegramChannelMember.STATUS_INVITED)
        self.assertIsNone(member.next_sync_at)
        self.assertEqual(self.bot.called('banChatMember'), [])

    def test_no_deadline_means_nobody_is_removed(self):
        Group.objects.filter(pk=self.group.pk).update(channel_removal_days=None)
        self.pay(500000)
        process_due_members()
        self.join(7777, self.member().invite_link)
        self.start_group_in_the_past()
        self.run_worker()
        self.assertEqual(self.member().status, TelegramChannelMember.STATUS_JOINED)

    def test_link_waits_until_the_client_starts_the_bot(self):
        TelegramUser.objects.all().delete()
        self.pay(500000)
        process_due_members()
        member = self.member()
        self.assertEqual(member.status, TelegramChannelMember.STATUS_INVITED)
        self.assertIsNone(member.link_sent_at)
        self.assertIn('Telegram akkaunti topilmadi', member.last_error)

        # Sharing the contact in the bot matches the client and delivers the waiting link.
        account = TelegramUser.objects.create(
            telegram_id=6161, full_name='Kanal Mijoz', onboarding_step=TelegramUser.STEP_CONTACT,
        )
        with mock.patch('main.telegram_views.send_bot_message', return_value=(True, None)):
            process_telegram_update({'update_id': 2, 'message': {
                'from': {'id': 6161}, 'contact': {'user_id': 6161, 'phone_number': '998901234567'},
            }})
        account.refresh_from_db()
        self.assertEqual(account.client, self.buyer)
        process_due_members()
        member = self.member()
        self.assertIsNotNone(member.link_sent_at)
        self.assertEqual(member.last_error, '')
        self.assertEqual(self.sent[-1]['chat_id'], 6161)

    def test_failed_telegram_call_is_retried_later(self):
        self.bot.responses['createChatInviteLink'] = TelegramAPIError('Telegram tarmoq xatosi')
        self.pay(500000)
        process_due_members()
        member = self.member()
        self.assertEqual(member.status, TelegramChannelMember.STATUS_NEW)
        self.assertEqual(member.last_error, 'Telegram tarmoq xatosi')
        self.assertGreater(member.next_sync_at, timezone.now())

    def test_polling_receives_join_requests(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {'ok': True, 'result': []}
        with TemporaryDirectory() as directory, mock.patch(
            'main.management.commands.process_telegram_updates.telegram_api_request', return_value=response,
        ) as api_request:
            call_command(
                'process_telegram_updates', '--once', '--timeout', '1',
                '--offset-file', str(Path(directory) / 'offset'), verbosity=0,
            )
        self.assertIn('chat_join_request', api_request.call_args.kwargs['json']['allowed_updates'])


@override_settings(TELEGRAM=TELEGRAM_SETTINGS)
class GroupChannelAdminTests(ChannelTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.course = Course.objects.create(name='Sog‘lomlashtirish', price=Decimal('2000000'), number_of_days=10)
        self.admin_user = User.objects.create_superuser(username='channel-admin', password='x')
        self.client.force_login(self.admin_user)

    def add_group(self, **extra):
        data = {
            'course': str(self.course.pk), 'start_date': (timezone.localdate() + timedelta(days=5)).isoformat(),
            'number_of_days': '10', 'is_active': 'on', 'telegram_channel_id': '', 'channel_removal_days': '',
            '_save': 'Saqlash', **extra,
        }
        return self.client.post(reverse('admin:main_group_add'), data)

    def test_group_is_not_created_when_the_bot_is_not_an_admin(self):
        self.bot.responses.update(
            getChat={'id': int(CHANNEL), 'type': 'channel', 'title': 'NM kanal'},
            getChatMember={'status': 'left'},
        )
        response = self.add_group(telegram_channel_id=CHANNEL, channel_removal_days='3')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bot «NM kanal» kanalida admin emas')
        self.assertFalse(Group.objects.exists())

    def test_group_is_not_created_without_ban_and_invite_rights(self):
        self.bot.responses.update(
            getChat={'id': int(CHANNEL), 'type': 'channel', 'title': 'NM kanal'},
            getChatMember={'status': 'administrator', 'can_invite_users': True, 'can_restrict_members': False},
        )
        response = self.add_group(telegram_channel_id=CHANNEL)
        self.assertContains(response, 'Ban users')
        self.assertFalse(Group.objects.exists())

    def test_group_is_not_created_for_an_unknown_channel(self):
        self.bot.responses['getChat'] = TelegramAPIError('Bad Request: chat not found', retryable=False)
        response = self.add_group(telegram_channel_id=CHANNEL)
        self.assertContains(response, 'Kanal topilmadi yoki bot kanalga qo')
        self.assertFalse(Group.objects.exists())

    def test_group_is_created_when_the_bot_can_manage_the_channel(self):
        self.bot.responses.update(
            getChat={'id': int(CHANNEL), 'type': 'channel', 'title': 'NM kanal'}, getChatMember=ADMIN_RIGHTS,
        )
        response = self.add_group(telegram_channel_id='@nm_kanal', channel_removal_days='3')
        self.assertEqual(response.status_code, 302)
        group = Group.objects.get()
        self.assertEqual(
            (group.telegram_channel_id, group.telegram_channel_title, group.channel_removal_days),
            (CHANNEL, 'NM kanal', 3),
        )
        self.assertEqual(self.bot.called('getChatMember'), [{'chat_id': int(CHANNEL), 'user_id': 123456}])

    def test_removal_days_need_a_channel(self):
        response = self.add_group(channel_removal_days='3')
        self.assertContains(response, 'Avval Telegram kanal ID sini kiriting.')
        self.assertFalse(Group.objects.exists())

    def test_connecting_a_channel_to_an_existing_group_invites_paid_clients(self):
        group = Group.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=5))
        paid = Client.objects.create(full_name='To‘lagan', phone_number='+998901111111')
        payment = Transaction.objects.create(
            group=group, date=timezone.localdate(), amount=Decimal(500000), payment_type='bron', is_confirmed=True,
        )
        TransactionClient.objects.create(transaction=payment, client=paid)
        payment.save()
        self.assertFalse(TelegramChannelMember.objects.exists())  # no channel yet

        self.bot.responses.update(
            getChat={'id': int(CHANNEL), 'type': 'channel', 'title': 'NM kanal'}, getChatMember=ADMIN_RIGHTS,
        )
        response = self.client.post(reverse('admin:main_group_change', args=[group.pk]), {
            'course': str(self.course.pk), 'start_date': group.start_date.isoformat(), 'number_of_days': '10',
            'is_active': 'on', 'telegram_channel_id': CHANNEL, 'channel_removal_days': '3', '_save': 'Saqlash',
        })
        self.assertEqual(response.status_code, 302)
        member = TelegramChannelMember.objects.get(group=group)
        self.assertEqual(member.client, paid)
        self.assertIsNotNone(member.next_sync_at)

        # Saving again without touching the channel does not call Telegram.
        self.bot.calls.clear()
        self.client.post(reverse('admin:main_group_change', args=[group.pk]), {
            'course': str(self.course.pk), 'start_date': group.start_date.isoformat(), 'number_of_days': '12',
            'is_active': 'on', 'telegram_channel_id': CHANNEL, 'channel_removal_days': '3', '_save': 'Saqlash',
        })
        self.assertEqual(self.bot.calls, [])

    def test_channel_tab_lists_members_and_resends_links(self):
        group = Group.objects.create(
            course=self.course, start_date=timezone.localdate() + timedelta(days=5),
            telegram_channel_id=CHANNEL, telegram_channel_title='NM kanal', channel_removal_days=3,
        )
        client = Client.objects.create(full_name='Havola Kutayotgan', phone_number='+998902222222')
        member = TelegramChannelMember.objects.create(
            group=group, client=client, status=TelegramChannelMember.STATUS_INVITED,
            invite_link='https://t.me/+personal-link', link_sent_at=timezone.now(),
        )
        response = self.client.get(reverse('admin:main_group_detail', args=[group.pk]) + '?tab=channel')
        self.assertContains(response, 'Havola Kutayotgan')
        self.assertContains(response, 'https://t.me/+personal-link')
        self.assertContains(response, 'NM kanal')

        response = self.client.post(reverse('admin:main_group_channel_resend', args=[group.pk, member.pk]))
        self.assertEqual(response.status_code, 302)
        member.refresh_from_db()
        self.assertIsNone(member.link_sent_at)
        self.assertIsNotNone(member.next_sync_at)

        listing = self.client.get(reverse('admin:main_group_changelist'))
        self.assertContains(listing, 'Ulangan')
