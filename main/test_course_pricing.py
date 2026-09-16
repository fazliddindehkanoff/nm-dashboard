from datetime import date, datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Client, Course, Discount, Group, MiniAppPurchase, Transaction,
    TransactionClient, _recalc_transaction_participants,
)
from .test_telegram_app import TELEGRAM_SETTINGS
from .services.legal import TERMS_VERSION


@override_settings(DEBUG=True, TELEGRAM=TELEGRAM_SETTINGS)
class CourseCheckoutTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(name='Health', price=2000000)
        self.group = Group.objects.create(
            course=self.course, start_date=timezone.localdate() + timedelta(days=2),
        )
        self.headers = {'HTTP_X_TELEGRAM_DEMO': '1'}
        self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        self.post('telegram_app_accept_terms', {'accepted': True, 'version': TERMS_VERSION})

    def post(self, endpoint, payload):
        return self.client.post(
            reverse('main:' + endpoint), payload,
            content_type='application/json', **self.headers,
        )

    def purchase(self, count=1, **extra):
        return self.post('telegram_app_create_purchase', {
            'course_id': self.course.pk,
            'purchase_type': 'family' if count > 1 else 'self',
            'members': [
                {'full_name': f'Family Member {i}', 'phone_number': f'+99891111110{i}'}
                for i in range(count - 1)
            ], **extra,
        })

    def catalogue(self):
        return self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers).json()['courses']

    def test_only_future_active_groups_are_sellable(self):
        for days, active in [(-1, True), (0, True), (1, False)]:
            with self.subTest(days=days, active=active):
                self.group.start_date = timezone.localdate() + timedelta(days=days)
                self.group.is_active = active
                self.group.save()
                self.assertEqual(self.catalogue(), [])
                self.assertEqual(self.purchase().status_code, 404)
        self.assertFalse(MiniAppPurchase.objects.exists())

    def test_course_stays_until_last_upcoming_group_starts(self):
        future = Group.objects.create(course=self.course, start_date=self.group.start_date + timedelta(days=3))
        self.assertEqual(len(self.catalogue()), 1)
        self.group.start_date = timezone.localdate()
        self.group.save()
        self.assertEqual([g['id'] for g in self.catalogue()[0]['active_groups']], [future.pk])
        self.assertEqual(self.purchase().status_code, 201)
        future.is_active = False
        future.save()
        self.assertEqual(self.catalogue(), [])

    @override_settings(TIME_ZONE='Asia/Tashkent')
    def test_visibility_changes_at_tashkent_midnight(self):
        self.group.start_date = date(2026, 9, 10)
        self.group.save()
        with patch('django.utils.timezone.now', return_value=datetime(2026, 9, 9, 18, 59, tzinfo=datetime_timezone.utc)):
            self.assertEqual(len(self.catalogue()), 1)
        with patch('django.utils.timezone.now', return_value=datetime(2026, 9, 9, 19, 0, tzinfo=datetime_timezone.utc)):
            self.assertEqual(self.catalogue(), [])

    def test_best_eligible_rule_applies_per_person_and_is_snapshotted(self):
        Discount.objects.create(name='Two people', course=self.course, amount=100000, min_participants=2)
        best = Discount.objects.create(name='Three people', course=self.course, amount=200000, min_participants=3)
        self.assertEqual(len(self.catalogue()[0]['participant_discounts']), 2)
        for count, unit_price in [(1, 2000000), (2, 1900000), (3, 1800000), (8, 1800000)]:
            with self.subTest(count=count):
                response = self.purchase(count, total_amount=1, unit_price=1, discount_per_person=1999999)
                self.assertEqual(response.status_code, 201, response.content)
                payload = response.json()['purchase']
                self.assertEqual(Decimal(payload['unit_price']), unit_price)
                self.assertEqual(Decimal(payload['total_amount']), unit_price * count)
                self.assertEqual(Decimal(payload['discount_total']), (2000000 - unit_price) * count)
        purchase = MiniAppPurchase.objects.get(pk=payload['id'])
        best.amount = 500000
        best.save()
        self.course.price = 3000000
        self.course.save()
        purchase.refresh_from_db()
        self.assertEqual(purchase.unit_price, 1800000)
        self.assertEqual(purchase.discount_name, 'Three people')

    def test_no_discount_without_an_applicable_active_rule(self):
        other = Course.objects.create(name='Other', price=2000000)
        for params in [
            {'course': other, 'min_participants': 2},
            {'course': self.course, 'min_participants': 2, 'is_active': False},
            {'course': self.course, 'min_participants': 3},
            {'course': self.course, 'is_booking': True},
            {'course': self.course},
        ]:
            Discount.objects.create(name='Ineligible', amount=100000, **params)
        response = self.purchase(2)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Decimal(response.json()['purchase']['total_amount']), 4000000)

    def test_global_participant_rule_and_price_floor(self):
        Discount.objects.create(name='Global', amount=3000000, min_participants=2)
        response = self.purchase(2)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Decimal(response.json()['purchase']['total_amount']), 0)
        self.assertEqual(Decimal(response.json()['purchase']['discount_per_person']), 2000000)


class AdminDiscountTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(name='Course', price=2000000)
        self.group = Group.objects.create(course=self.course, start_date=timezone.localdate())
        Discount.objects.create(name='Family', course=self.course, amount=100000, min_participants=2)

    def test_adding_and_removing_participants_recalculates_discount_and_debt(self):
        payment = Transaction.objects.create(
            group=self.group, date=timezone.localdate(), amount=1000000, payment_type='to_liq_tolov',
            is_confirmed=True,
        )
        people = [Client.objects.create(full_name=f'Person {i}', phone_number=f'99890000000{i}') for i in range(2)]
        first = TransactionClient.objects.create(transaction=payment, client=people[0])
        _recalc_transaction_participants(payment)
        self.assertEqual(payment.discount_total, 0)
        second = TransactionClient.objects.create(transaction=payment, client=people[1])
        _recalc_transaction_participants(payment)
        self.assertEqual(payment.discount_total, 200000)
        first.refresh_from_db()
        self.assertEqual(first.share_discount, 100000)
        self.assertEqual(first.debt, 1400000)
        second.delete()
        _recalc_transaction_participants(payment)
        self.assertEqual(payment.discount_total, 0)
        first.refresh_from_db()
        self.assertEqual(first.debt, 1000000)

    def test_topup_does_not_grant_another_automatic_discount(self):
        payment = Transaction(group=self.group, payment_type='doplata')
        self.assertEqual(payment.calculate_discount_total(2), 0)

    def test_course_booking_rule_does_not_leak_to_other_courses(self):
        Discount.objects.create(name='Booking', course=self.course, amount=200000, is_booking=True)
        payment = Transaction(group=self.group, payment_type='bron')
        self.assertEqual(payment.calculate_discount_total(2), 600000)
        other = Course.objects.create(name='Online', price=1800000)
        payment.group = Group.objects.create(course=other, start_date=timezone.localdate())
        self.assertEqual(payment.calculate_discount_total(2), 0)

    def test_invalid_rule_is_rejected_by_admin_validation(self):
        for params in [{'amount': -1}, {'amount': 100, 'min_participants': 1},
                       {'amount': 100, 'min_participants': 2, 'is_booking': True}]:
            with self.subTest(params=params), self.assertRaises(ValidationError):
                Discount(name='Invalid', **params).full_clean()


class CourseImportTests(TestCase):
    def test_legacy_names_keep_existing_ids_and_group_links(self):
        course = Course.objects.create(name="Sog'lomlashtirish 1-bosqich", price=1, number_of_days=10)
        group = Group.objects.create(course=course, start_date=timezone.localdate())
        call_command('import_course_prices', stdout=StringIO())
        call_command('import_course_prices', stdout=StringIO())
        course.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(course.name, 'Соғломлаштириш биринчи курс')
        self.assertEqual(course.price, 2000000)
        self.assertEqual(course.number_of_days, 10)
        self.assertEqual(group.course_id, course.pk)
        self.assertEqual(Course.objects.count(), 13)

    def test_alias_and_canonical_name_collision_stops_import(self):
        for name in ["Sog'lomlashtirish 1-bosqich", 'Соғломлаштириш биринчи курс']:
            Course.objects.create(name=name, price=1)
        with self.assertRaises(CommandError):
            call_command('import_course_prices', stdout=StringIO())
        self.assertEqual(Course.objects.count(), 2)
        self.assertFalse(Discount.objects.exists())

    def test_import_is_repeatable_and_preserves_existing_data(self):
        existing = Course.objects.create(name='Соғломлаштириш биринчи курс', price=1, number_of_days=10)
        unrelated = Course.objects.create(name='Existing course', price=123)
        call_command('import_course_prices', stdout=StringIO())
        call_command('import_course_prices', stdout=StringIO())
        self.assertEqual(Course.objects.count(), 14)
        self.assertEqual(Discount.objects.filter(is_booking=True).count(), 10)
        self.assertEqual(Discount.objects.filter(min_participants=2).count(), 3)
        existing.refresh_from_db()
        self.assertEqual(existing.price, 2000000)
        self.assertEqual(existing.number_of_days, 10)
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.price, 123)
        self.assertFalse(Group.objects.exists())
        self.assertEqual(Discount.participant_discount(existing.pk, 2).amount, 100000)

    def test_dry_run_and_conflicting_price_option(self):
        call_command('import_course_prices', dry_run=True, stdout=StringIO())
        self.assertFalse(Course.objects.exists())
        self.assertFalse(Discount.objects.exists())
        call_command('import_course_prices', health_family_discount=200000, stdout=StringIO())
        course = Course.objects.get(name='Соғломлаштириш биринчи курс')
        self.assertEqual(Discount.participant_discount(course.pk, 2).amount, 200000)

    def test_duplicate_course_names_roll_back_import(self):
        for _ in range(2):
            Course.objects.create(name='Интуиция 3-курс', price=123)
        with self.assertRaises(CommandError):
            call_command('import_course_prices', stdout=StringIO())
        self.assertEqual(Course.objects.count(), 2)
        self.assertFalse(Discount.objects.exists())
