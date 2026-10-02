from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Course, Group, MiniAppPurchase
from .services.legal import CONTRACT_VERSION, TERMS_VERSION
from . import test_telegram_app as app_fixture


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, TELEGRAM=app_fixture.TELEGRAM_SETTINGS)
class DirectPurchaseTests(TestCase):
    post_json = app_fixture.TelegramMiniAppTests.post_json

    def setUp(self):
        app_fixture.TelegramMiniAppTests.setUp(self)
        self.later_group = Group.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=30))
        self.other = Course.objects.create(name='Boshqa kurs', price=Decimal('800000'), number_of_days=5)
        Group.objects.create(course=self.other, start_date=timezone.localdate() + timedelta(days=3))
        self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        self.post_json(reverse('main:telegram_app_accept_terms'), {'accepted': True, 'version': TERMS_VERSION})

    def buy(self, course=None, **extra):
        payload = {'course_id': (course or self.course).id, 'purchase_type': 'self', 'payment_mode': 'full', **extra}
        response = self.post_json(reverse('main:telegram_app_create_purchase'), payload)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()['purchase']

    def bootstrap_purchase_ids(self):
        data = self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers).json()
        return [item['id'] for item in data['purchases']]

    def pay(self, purchase):
        self.post_json(reverse('main:telegram_app_accept_contract', args=[purchase['id']]),
                       {'accepted': True, 'version': CONTRACT_VERSION})
        response = self.post_json(reverse('main:telegram_app_simulate_payment', args=[purchase['id']]), {})
        self.assertEqual(response.status_code, 200, response.content)

    def test_buying_a_course_again_replaces_its_unpaid_order(self):
        first = self.buy()
        other = self.buy(self.other)
        second = self.buy(group_id=self.later_group.id)

        self.assertEqual(MiniAppPurchase.objects.get(pk=first['id']).payment_status, 'cancelled')
        self.assertEqual(second['group']['id'], self.later_group.id)
        self.assertCountEqual(self.bootstrap_purchase_ids(), [second['id'], other['id']])

        # A paid order is kept when the same course is bought again.
        self.pay(second)
        third = self.buy()
        self.assertEqual(MiniAppPurchase.objects.get(pk=second['id']).payment_status, 'success')
        self.assertCountEqual(self.bootstrap_purchase_ids(), [second['id'], third['id'], other['id']])

    def test_unpaid_order_can_be_cancelled_but_a_paid_one_cannot(self):
        purchase = self.buy()
        self.assertTrue(purchase['can_cancel'])
        url = reverse('main:telegram_app_cancel_purchase', args=[purchase['id']])
        self.assertEqual(self.post_json(url, {}).status_code, 200)
        self.assertEqual(MiniAppPurchase.objects.get(pk=purchase['id']).payment_status, 'cancelled')
        self.assertNotIn(purchase['id'], self.bootstrap_purchase_ids())
        self.assertEqual(self.post_json(url, {}).status_code, 409)

        paid = self.buy()
        self.pay(paid)
        self.assertEqual(self.post_json(reverse('main:telegram_app_cancel_purchase', args=[paid['id']]), {}).status_code, 409)
        self.assertEqual(self.post_json(reverse('main:telegram_app_cancel_purchase', args=[999999]), {}).status_code, 404)

    def test_web_app_opens_on_the_catalogue_without_a_cart(self):
        page = self.client.get(reverse('main:telegram_app'))
        self.assertContains(page, 'id="catalogView"')
        self.assertContains(page, 'Sotib olish')
        self.assertNotContains(page, 'Savat')
        # Two tabs: the catalogue and the profile, which lists purchased courses.
        self.assertEqual(page.content.decode().count('data-tab='), 2)
        self.assertContains(page, 'Sotib olingan kurslar')
