from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    EARLY_FULL_PAYMENT_DISCOUNT_SINCE, Client, Course, Discount, Group, MiniAppPurchase, MulticardInvoice,
    Transaction, TransactionClient,
)
from .services.legal import CONTRACT_VERSION, TERMS_VERSION, contract_version, render_contract_document
from .services.multicard import _settle
from . import test_telegram_app as app_fixture


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, TELEGRAM=app_fixture.TELEGRAM_SETTINGS)
class EarlyFullPaymentDiscountTests(TestCase):
    post_json = app_fixture.TelegramMiniAppTests.post_json

    def setUp(self):
        app_fixture.TelegramMiniAppTests.setUp(self)  # 1 500 000 course, group in 10 days
        Discount.objects.create(name='Bron chegirmasi', amount=Decimal('200000'), course=self.course, is_booking=True)
        self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        self.post_json(reverse('main:telegram_app_accept_terms'), {'accepted': True, 'version': TERMS_VERSION})

    def buy(self, payment_mode):
        response = self.post_json(reverse('main:telegram_app_create_purchase'), {
            'course_id': self.course.id, 'purchase_type': 'self', 'payment_mode': payment_mode,
        })
        self.assertEqual(response.status_code, 201, response.content)
        return MiniAppPurchase.objects.get(pk=response.json()['purchase']['id'])

    def test_paying_in_full_before_the_start_gets_the_booking_discount(self):
        purchase = self.buy('full')
        self.assertFalse(purchase.is_booking)
        self.assertEqual(purchase.booking_discount, Decimal('200000'))
        self.assertEqual((purchase.payable_amount, purchase.sale_amount), (Decimal('1300000'), Decimal('1300000')))
        self.assertEqual(contract_version(purchase), f'{CONTRACT_VERSION}.early')
        self.assertIn('to‘liq to‘lov chegirmasi', render_contract_document(purchase))

        invoice = MulticardInvoice.objects.create(purchase=purchase, store_id='6', amount=130000000)
        with transaction.atomic():
            _settle(invoice, uuid4())
        purchase.refresh_from_db()
        self.assertEqual((purchase.payment_status, purchase.discount_amount), ('success', Decimal('200000')))
        # The group records the discounted price as fully paid.
        mirror = Transaction.objects.get(mini_app_purchase=purchase)
        self.assertEqual((mirror.payment_type, mirror.amount, mirror.total_remaining), ('to_liq_tolov', Decimal('1300000'), 0))

    def test_booking_still_gets_the_discount_after_its_first_payment(self):
        purchase = self.buy('booking')
        self.assertEqual((purchase.booking_discount, purchase.payable_amount), (Decimal('200000'), Decimal('1300000')))

    def test_courses_without_a_booking_discount_are_unchanged(self):
        Discount.objects.all().delete()
        purchase = self.buy('full')
        self.assertEqual((purchase.booking_discount, purchase.payable_amount), (0, Decimal('1500000')))
        self.assertEqual(contract_version(purchase), CONTRACT_VERSION)


class CrmFullPaymentDiscountTests(TestCase):
    def setUp(self):
        course = Course.objects.create(name='Kurs', price=Decimal('2000000'), number_of_days=10)
        Discount.objects.create(name='Bron', amount=Decimal('200000'), course=course, is_booking=True)
        self.group = Group.objects.create(course=course, start_date=EARLY_FULL_PAYMENT_DISCOUNT_SINCE + timedelta(days=10))
        self.client_record = Client.objects.create(full_name='Mijoz', phone_number='+998901112233')

    def payment(self, payment_type, day):
        payment = Transaction.objects.create(
            group=self.group, date=day, amount=Decimal('1800000'), payment_type=payment_type, is_confirmed=True,
        )
        TransactionClient.objects.create(transaction=payment, client=self.client_record)
        payment.save()
        return payment

    def test_full_payment_before_the_start_gets_the_booking_discount(self):
        payment = self.payment('to_liq_tolov', EARLY_FULL_PAYMENT_DISCOUNT_SINCE)
        self.assertEqual((payment.discount_total, payment.total_remaining), (Decimal('200000'), 0))

    def test_full_payment_after_the_start_or_before_the_rule_keeps_the_full_price(self):
        late = self.payment('to_liq_tolov', self.group.start_date)
        self.assertEqual(late.discount_total, 0)
        late.delete()
        older = self.payment('to_liq_tolov', EARLY_FULL_PAYMENT_DISCOUNT_SINCE - timedelta(days=1))
        self.assertEqual(older.discount_total, 0)


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False)
class StartedGroupLinkTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(name='Kurs', price=Decimal('2000000'), number_of_days=10)
        Discount.objects.create(name='Bron', amount=Decimal('200000'), course=self.course, is_booking=True)
        today = timezone.localdate()
        self.started = Group.objects.create(course=self.course, start_date=today - timedelta(days=2))
        self.upcoming = Group.objects.create(course=self.course, start_date=today + timedelta(days=5))
        self.archived = Group.objects.create(course=self.course, start_date=today - timedelta(days=30), is_active=False)
        self.client_record = Client.objects.create(full_name='Mijoz', phone_number='+998901112233')
        self.client.force_login(User.objects.create_superuser('links-admin'))
        self.page = reverse('admin:main_client_payment_link', args=[self.client_record.pk])

    def create(self, group, payment_mode='full'):
        response = self.client.post(self.page, {'action': 'course', 'group': group.pk, 'payment_mode': payment_mode})
        self.assertEqual(response.status_code, 302)
        return MiniAppPurchase.objects.order_by('-id').first()

    def test_active_groups_that_started_can_be_chosen(self):
        groups = list(self.client.get(self.page).context['groups'])
        self.assertEqual(groups, [self.started, self.upcoming])
        self.assertContains(self.client.get(self.page), '(boshlangan)')

    def test_started_group_has_no_early_discount(self):
        late = self.create(self.started)
        self.assertEqual((late.group, late.booking_discount, late.payable_amount), (self.started, 0, Decimal('2000000')))
        late.payment_status = MiniAppPurchase.PAYMENT_CANCELLED
        late.save()
        early = self.create(self.upcoming)
        self.assertEqual((early.booking_discount, early.payable_amount), (Decimal('200000'), Decimal('1800000')))

    def test_archived_group_cannot_be_used(self):
        response = self.client.post(self.page, {'action': 'course', 'group': self.archived.pk, 'payment_mode': 'full'}, follow=True)
        self.assertContains(response, 'topilmadi')
        self.assertFalse(MiniAppPurchase.objects.exists())
