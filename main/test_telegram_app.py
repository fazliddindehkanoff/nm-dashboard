import hashlib
import hmac
import json
import time
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    AttendanceLesson,
    AttendanceRecord,
    Client,
    Course,
    EnrollmentQuestionnaire,
    Group,
    LegalAcceptance,
    MiniAppPurchase,
    TelegramUser,
)
from .services.legal import CONTRACT_VERSION, TERMS_VERSION
from .services.telegram import send_attendance_notification
from .services.telegram_auth import validate_init_data


TELEGRAM_SETTINGS = {
    'BOT_TOKEN': '123456:test-token',
    'CHAT_ID': '',
    'WEB_APP_URL': 'https://example.com/telegram-app/',
    'WEBHOOK_SECRET': 'webhook-secret',
}


@override_settings(TELEGRAM=TELEGRAM_SETTINGS)
class TelegramWebhookTests(TestCase):
    def post_update(self, message, secret='webhook-secret'):
        return self.client.post(
            reverse('main:telegram_webhook'),
            data=json.dumps({'message': message}),
            content_type='application/json',
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
        )

    @patch('main.telegram_views.send_bot_message', return_value=(True, None))
    def test_onboarding_requests_name_then_own_contact(self, send_message):
        sender = {'id': 7711, 'username': 'example'}
        response = self.post_update({'from': sender, 'text': '/start'})
        self.assertEqual(response.status_code, 200)
        account = TelegramUser.objects.get(telegram_id=7711)
        self.assertEqual(account.onboarding_step, TelegramUser.STEP_NAME)

        self.post_update({'from': sender, 'text': 'Aziza Karimova'})
        account.refresh_from_db()
        self.assertEqual(account.onboarding_step, TelegramUser.STEP_CONTACT)

        self.post_update({
            'from': sender,
            'contact': {'user_id': 7711, 'phone_number': '90 123 45 67'},
        })
        account.refresh_from_db()
        self.assertEqual(account.phone_number, '+998901234567')
        self.assertEqual(account.onboarding_step, TelegramUser.STEP_READY)
        self.assertEqual(send_message.call_count, 3)

    def test_webhook_rejects_wrong_secret(self):
        response = self.post_update({'from': {'id': 1}, 'text': '/start'}, secret='wrong')
        self.assertEqual(response.status_code, 403)

    @patch(
        'main.telegram_views.send_bot_message',
        return_value=(False, 'Telegram tarmoq xatosi'),
    )
    def test_webhook_retries_failed_delivery_without_advancing_onboarding(self, send_message):
        response = self.post_update({
            'from': {'id': 7722, 'username': 'retry-user'},
            'text': '/start',
        })
        self.assertEqual(response.status_code, 503)
        self.assertFalse(TelegramUser.objects.filter(telegram_id=7722).exists())
        self.assertEqual(send_message.call_count, 1)

    @patch(
        'main.telegram_views.send_bot_message',
        return_value=(False, "Forbidden: bot can't initiate conversation with a user"),
    )
    def test_webhook_acknowledges_permanently_undeliverable_update(self, send_message):
        response = self.post_update({
            'from': {'id': 7723, 'username': 'blocked-user'},
            'text': '/start',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TelegramUser.objects.filter(telegram_id=7723).exists())
        self.assertEqual(send_message.call_count, 1)


@override_settings(DEBUG=True, TELEGRAM=TELEGRAM_SETTINGS)
class TelegramMiniAppTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            name='Sog‘lomlashtirish kursi', price=Decimal('1500000'), number_of_days=10,
        )
        self.group = Group.objects.create(
            course=self.course, start_date=date(2026, 9, 10), number_of_days=10,
            is_active=True,
        )
        self.headers = {'HTTP_X_TELEGRAM_DEMO': '1'}

    def post_json(self, url, payload):
        return self.client.post(
            url, data=json.dumps(payload), content_type='application/json', **self.headers,
        )

    def test_family_purchase_payment_and_mandatory_questionnaire(self):
        bootstrap = self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        self.assertEqual(bootstrap.status_code, 200)
        self.assertEqual(bootstrap.json()['courses'][0]['number_of_days'], 10)
        self.assertTrue(bootstrap.json()['legal']['terms_required'])

        blocked_purchase = self.post_json(reverse('main:telegram_app_create_purchase'), {
            'course_id': self.course.id,
            'purchase_type': 'self',
            'members': [],
        })
        self.assertEqual(blocked_purchase.status_code, 409)

        terms = self.client.get(reverse('main:telegram_app_terms'), **self.headers)
        self.assertEqual(terms.status_code, 200)
        self.assertIn('13. Aloqa', terms.json()['document']['html'])
        accepted_terms = self.post_json(reverse('main:telegram_app_accept_terms'), {
            'accepted': True,
            'version': TERMS_VERSION,
        })
        self.assertEqual(accepted_terms.status_code, 200)
        self.assertEqual(
            LegalAcceptance.objects.filter(document_type=LegalAcceptance.DOCUMENT_TERMS).count(),
            1,
        )

        response = self.post_json(reverse('main:telegram_app_create_purchase'), {
            'course_id': self.course.id,
            'purchase_type': 'family',
            'members': [
                {'full_name': 'Ali Valiyev', 'phone_number': '+998 91 111 22 33'},
                {'full_name': 'Nodira Valiyeva', 'phone_number': '+998 93 444 55 66'},
            ],
        })
        self.assertEqual(response.status_code, 201)
        purchase_id = response.json()['purchase']['id']
        purchase = MiniAppPurchase.objects.get(pk=purchase_id)
        self.assertEqual(purchase.participant_count, 3)
        self.assertEqual(purchase.total_amount, Decimal('4500000'))

        contract = self.client.get(
            reverse('main:telegram_app_contract', args=[purchase_id]), **self.headers,
        )
        self.assertEqual(contract.status_code, 200)
        self.assertIn('Sog‘lomlashtirish kursi', contract.json()['document']['html'])
        self.assertIn('4500000', contract.json()['document']['html'])

        payment_without_contract = self.post_json(
            reverse('main:telegram_app_simulate_payment', args=[purchase_id]), {},
        )
        self.assertEqual(payment_without_contract.status_code, 409)

        accepted_contract = self.post_json(
            reverse('main:telegram_app_accept_contract', args=[purchase_id]),
            {'accepted': True, 'version': CONTRACT_VERSION},
        )
        self.assertEqual(accepted_contract.status_code, 200)
        contract_acceptance = LegalAcceptance.objects.get(
            purchase=purchase,
            document_type=LegalAcceptance.DOCUMENT_CONTRACT,
        )
        self.assertEqual(len(contract_acceptance.document_hash), 64)

        repeated_acceptance = self.post_json(
            reverse('main:telegram_app_accept_contract', args=[purchase_id]),
            {'accepted': True, 'version': CONTRACT_VERSION},
        )
        self.assertEqual(repeated_acceptance.status_code, 200)
        self.assertEqual(
            LegalAcceptance.objects.filter(
                purchase=purchase,
                document_type=LegalAcceptance.DOCUMENT_CONTRACT,
            ).count(),
            1,
        )

        with patch('main.telegram_views.send_bot_message', return_value=(True, None)):
            payment = self.post_json(
                reverse('main:telegram_app_simulate_payment', args=[purchase_id]), {},
            )
        self.assertEqual(payment.status_code, 200)
        purchase.refresh_from_db()
        self.assertEqual(purchase.payment_status, MiniAppPurchase.PAYMENT_SUCCESS)
        self.assertEqual(Client.objects.count(), 3)

        members = list(purchase.members.all())
        partial = self.post_json(
            reverse('main:telegram_app_questionnaire', args=[purchase_id]),
            {'responses': [self.questionnaire(members[0])]},
        )
        self.assertEqual(partial.status_code, 400)
        self.assertFalse(EnrollmentQuestionnaire.objects.exists())

        with patch('main.telegram_views.send_bot_message', return_value=(True, None)):
            completed = self.post_json(
                reverse('main:telegram_app_questionnaire', args=[purchase_id]),
                {'responses': [self.questionnaire(member) for member in members]},
            )
        self.assertEqual(completed.status_code, 200)
        purchase.refresh_from_db()
        self.assertTrue(purchase.questionnaire_completed)
        self.assertEqual(EnrollmentQuestionnaire.objects.count(), 3)

        final_bootstrap = self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        self.assertFalse(final_bootstrap.json()['legal']['terms_required'])
        self.assertTrue(final_bootstrap.json()['purchases'][0]['contract_accepted'])
        self.assertEqual(final_bootstrap.json()['my_courses'][0]['assignment_status'], 'awaiting_group')

    def test_catalogue_only_lists_courses_with_active_groups(self):
        inactive_course = Course.objects.create(
            name='Yopiq kurs', price=Decimal('900000'), number_of_days=5,
        )
        Group.objects.create(
            course=inactive_course, start_date=date(2026, 10, 1), number_of_days=5,
            is_active=False,
        )

        response = self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        self.assertEqual(
            [course['id'] for course in response.json()['courses']],
            [self.course.id],
        )
        self.assertEqual(
            response.json()['courses'][0]['active_groups'][0]['start_date'],
            '2026-09-10',
        )

        self.post_json(reverse('main:telegram_app_accept_terms'), {
            'accepted': True,
            'version': TERMS_VERSION,
        })
        unavailable = self.post_json(reverse('main:telegram_app_create_purchase'), {
            'course_id': inactive_course.id,
            'purchase_type': MiniAppPurchase.TYPE_SELF,
            'members': [],
        })
        self.assertEqual(unavailable.status_code, 404)

    def test_bootstrap_includes_private_attendance_timeline(self):
        self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        account = TelegramUser.objects.get(telegram_id=900000001)
        linked_client = Client.objects.create(
            full_name='Demo Foydalanuvchi', phone_number='+998901234567',
        )
        outsider = Client.objects.create(
            full_name='Boshqa mijoz', phone_number='+998909999999',
        )
        account.client = linked_client
        account.save(update_fields=('client', 'updated_at'))
        marker = User.objects.create_user(
            username='attendance-operator', first_name='Dilnoza', last_name='Karimova',
        )
        record = AttendanceRecord.objects.create(client=linked_client, group=self.group)
        AttendanceRecord.objects.create(client=outsider, group=self.group)
        AttendanceLesson.objects.create(
            attendance=record,
            date=date(2026, 9, 10),
            status=AttendanceLesson.STATUS_ATTENDED,
            note='QR orqali tasdiqlandi',
            marked_by=marker,
        )

        response = self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        course = response.json()['my_courses'][0]
        self.assertEqual(course['course'], self.course.name)
        self.assertEqual(course['teachers'], [])
        self.assertEqual(len(course['participants']), 1)
        self.assertEqual(course['participants'][0]['full_name'], linked_client.full_name)
        self.assertEqual(len(course['participants'][0]['lessons']), 10)
        first_lesson = course['participants'][0]['lessons'][0]
        self.assertEqual(first_lesson['status'], AttendanceLesson.STATUS_ATTENDED)
        self.assertEqual(first_lesson['marked_by'], 'Dilnoza Karimova')
        self.assertTrue(first_lesson['marked_at'])
        self.assertEqual(first_lesson['note'], 'QR orqali tasdiqlandi')
        self.assertEqual(
            course['participants'][0]['lessons'][1]['status'],
            AttendanceLesson.STATUS_UNMARKED,
        )

    def test_each_purchase_requires_its_own_contract_acceptance(self):
        self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        self.post_json(reverse('main:telegram_app_accept_terms'), {
            'accepted': True,
            'version': TERMS_VERSION,
        })

        purchase_ids = []
        for _ in range(2):
            response = self.post_json(reverse('main:telegram_app_create_purchase'), {
                'course_id': self.course.id,
                'purchase_type': MiniAppPurchase.TYPE_SELF,
                'members': [],
            })
            self.assertEqual(response.status_code, 201)
            purchase_ids.append(response.json()['purchase']['id'])

        first_acceptance = self.post_json(
            reverse('main:telegram_app_accept_contract', args=[purchase_ids[0]]),
            {'accepted': True, 'version': CONTRACT_VERSION},
        )
        self.assertEqual(first_acceptance.status_code, 200)
        self.assertTrue(first_acceptance.json()['purchase']['contract_accepted'])

        second_contract = self.client.get(
            reverse('main:telegram_app_contract', args=[purchase_ids[1]]),
            **self.headers,
        )
        self.assertEqual(second_contract.status_code, 200)
        self.assertFalse(second_contract.json()['document']['accepted'])
        blocked_payment = self.post_json(
            reverse('main:telegram_app_simulate_payment', args=[purchase_ids[1]]), {},
        )
        self.assertEqual(blocked_payment.status_code, 409)

        second_acceptance = self.post_json(
            reverse('main:telegram_app_accept_contract', args=[purchase_ids[1]]),
            {'accepted': True, 'version': CONTRACT_VERSION},
        )
        self.assertEqual(second_acceptance.status_code, 200)
        self.assertTrue(second_acceptance.json()['purchase']['contract_accepted'])
        self.assertEqual(
            LegalAcceptance.objects.filter(
                telegram_user__telegram_id=900000001,
                document_type=LegalAcceptance.DOCUMENT_CONTRACT,
            ).count(),
            2,
        )

    def test_mini_app_uses_content_versioned_static_assets(self):
        response = self.client.get(reverse('main:telegram_app'))
        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.content.decode(),
            r'main/js/telegram-app\.js\?v=[0-9a-f]{12}',
        )

    def test_rejects_false_or_outdated_legal_acceptance(self):
        false_terms = self.post_json(reverse('main:telegram_app_accept_terms'), {
            'accepted': False,
            'version': TERMS_VERSION,
        })
        self.assertEqual(false_terms.status_code, 400)
        old_terms = self.post_json(reverse('main:telegram_app_accept_terms'), {
            'accepted': True,
            'version': 'old-version',
        })
        self.assertEqual(old_terms.status_code, 400)
        self.assertFalse(LegalAcceptance.objects.exists())

    @staticmethod
    def questionnaire(member):
        return {
            'member_id': member.id,
            'birth_date': '1990-05-10',
            'city': 'Toshkent',
            'occupation': 'Tadbirkor',
            'learning_goal': 'Sog‘lom odatlarni rivojlantirish',
            'prior_experience': '',
            'health_notes': '',
            'consent': True,
        }


@override_settings(TELEGRAM=TELEGRAM_SETTINGS)
class TelegramSecurityAndNotificationTests(TestCase):
    def test_validates_telegram_init_data_signature(self):
        values = {
            'auth_date': str(int(time.time())),
            'query_id': 'AAE-test',
            'user': json.dumps({'id': 8822, 'first_name': 'Aziza'}, separators=(',', ':')),
        }
        check_string = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
        secret = hmac.new(b'WebAppData', TELEGRAM_SETTINGS['BOT_TOKEN'].encode(), hashlib.sha256).digest()
        values['hash'] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(validate_init_data(urlencode(values))['id'], 8822)

    @patch('main.services.telegram.send_bot_message', return_value=(True, None))
    def test_attendance_notification_reaches_linked_client(self, send_message):
        course = Course.objects.create(name='Kurs', price=100, number_of_days=3)
        group = Group.objects.create(course=course, start_date=date(2026, 9, 1), number_of_days=3)
        client = Client.objects.create(full_name='Aziza Karimova', phone_number='+998901112233')
        TelegramUser.objects.create(
            telegram_id=9911, full_name=client.full_name, phone_number=client.phone_number,
            client=client, onboarding_step=TelegramUser.STEP_READY,
        )
        record = AttendanceRecord.objects.create(client=client, group=group)
        lesson = AttendanceLesson.objects.create(
            attendance=record, date=date(2026, 9, 2), status=AttendanceLesson.STATUS_ATTENDED,
        )
        ok, _ = send_attendance_notification(lesson)
        self.assertTrue(ok)
        self.assertIn('2-kun', send_message.call_args.args[1])
