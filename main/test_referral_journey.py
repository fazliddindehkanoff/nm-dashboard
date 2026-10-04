import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Client, Course, Group, MiniAppPurchase, MulticardInvoice, Operator, TelegramUser, Transaction
from .services.legal import TERMS_VERSION
from .services.multicard import _settle
from .telegram_views import process_telegram_update
from . import test_telegram_app as app_fixture

DEMO_ID = 900000001  # The web app's DEBUG demo account, reached through the same Telegram ID.


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, TELEGRAM={**app_fixture.TELEGRAM_SETTINGS, 'BOT_USERNAME': 'example_bot'})
class ReferralJourneyTests(TestCase):
    headers = {'HTTP_X_TELEGRAM_DEMO': '1'}

    def setUp(self):
        self.seller = Operator.objects.create(
            user=User.objects.create_user('seller', is_staff=True), full_name='Seller', role='operator',
        )
        self.course = Course.objects.create(name='Sog‘lomlashtirish', price=Decimal('2000000'), number_of_days=10)
        Group.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=7))

    def message(self, **message):
        with patch('main.telegram_views.send_bot_message', return_value=(True, None)):
            process_telegram_update({'message': {'from': {'id': DEMO_ID}, **message}})

    def post_json(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type='application/json', **self.headers)

    def test_seller_link_follows_the_customer_to_the_paid_sale_and_salary(self):
        # The customer opens the seller's link and registers in the bot.
        self.message(text=f'/start ref_{self.seller.referral_code.hex}')
        self.message(text='Aziza Karimova')
        self.message(contact={'user_id': DEMO_ID, 'phone_number': '998901112233'})
        account = TelegramUser.objects.get(telegram_id=DEMO_ID)
        self.assertEqual((account.referrer, account.onboarding_step), (self.seller, TelegramUser.STEP_READY))

        # The purchase made in the web app carries the seller.
        self.post_json(reverse('main:telegram_app_accept_terms'), {'accepted': True, 'version': TERMS_VERSION})
        created = self.post_json(reverse('main:telegram_app_create_purchase'), {
            'course_id': self.course.id, 'purchase_type': 'self', 'payment_mode': 'full',
        })
        self.assertEqual(created.status_code, 201, created.content)
        purchase = MiniAppPurchase.objects.get(pk=created.json()['purchase']['id'])
        self.assertEqual(purchase.referrer, self.seller)

        invoice = MulticardInvoice.objects.create(purchase=purchase, amount=int(purchase.payable_amount * 100), store_id='6')
        with transaction.atomic():
            _settle(invoice, uuid4())
        self.assertEqual(Client.objects.get(phone_number='+998901112233').operator, self.seller)
        # The group copy of the payment has no operator, so the sale is not counted twice.
        self.assertIsNone(Transaction.objects.get(mini_app_purchase=purchase).operator)

        self.client.force_login(User.objects.create_superuser('referral-admin'))
        row = next(row for row in self.client.get(reverse('main:referrals')).context['rows'] if row['name'] == 'Seller')
        self.assertEqual(
            (row['registrations'], row['sales'], row['sales_amount'], row['paid']),
            (1, 1, Decimal('2000000'), Decimal('2000000')),
        )
        today = timezone.localdate()
        salaries = self.client.get(reverse('main:salaries'), {'month': today.month, 'year': today.year})
        salary_row = next(row for row in salaries.context['rows'] if row['operator'] == self.seller)
        self.assertEqual((salary_row['sales_count'], salary_row['total_collected']), (1, Decimal('2000000')))
