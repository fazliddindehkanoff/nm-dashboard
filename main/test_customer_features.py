import json
import tempfile
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth.models import User, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import test_multicard as multicard_fixture
from . import test_telegram_app as app_fixture
from .models import (EligibilityDocument, TelegramUser, Operator, MiniAppPurchase, MiniAppPurchaseMember,
    MulticardInvoice, PaymentQRDelivery, Discount, Client, Transaction, SubTransaction)
from .services.multicard import _settle, MulticardClient
from .services.payment_notifications import process_payment_notifications
from .telegram_views import process_telegram_update


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, TELEGRAM=app_fixture.TELEGRAM_SETTINGS)
class CustomerCheckoutTests(TestCase):
    post_json = app_fixture.TelegramMiniAppTests.post_json

    def setUp(self):
        app_fixture.TelegramMiniAppTests.setUp(self)
        self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers)
        self.account = TelegramUser.objects.get(telegram_id=900000001)
        self.post_json(reverse('main:telegram_app_accept_terms'), {'accepted': True, 'version': app_fixture.TERMS_VERSION})
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage_override = override_settings(PRIVATE_DOCUMENT_ROOT=self.temp.name)
        self.storage_override.enable(); self.addCleanup(self.storage_override.disable)

    def proof(self, phone=None, category='student'):
        return self.client.post(reverse('main:upload_eligibility_document'), {
            'category': category, 'phone_number': phone or self.account.phone_number,
            'document': SimpleUploadedFile('student.pdf', b'%PDF-1.7\nTest document', 'application/pdf'),
        }, **self.headers)

    def purchase(self, **extra):
        return self.post_json(reverse('main:telegram_app_create_purchase'), {
            'course_id': self.course.id, 'purchase_type': 'self', 'payment_mode': 'full', **extra})

    def test_social_discount_stacks_with_family_discount_once_per_person(self):
        Discount.objects.create(name='Family', amount=100000, min_participants=2, course=self.course)
        proof = self.proof().json()['id']
        response = self.purchase(purchase_type='family', eligibility_document_id=proof,
            members=[{'full_name': 'Family Member', 'phone_number': '+998901111111'}])
        self.assertEqual(response.status_code, 201, response.content)
        purchase = MiniAppPurchase.objects.get(pk=response.json()['purchase']['id'])
        self.assertEqual(purchase.social_discount_amount, 100000)
        self.assertEqual(purchase.total_amount, 2700000)
        self.assertEqual(purchase.members.filter(eligibility_document_id=proof).count(), 1)

    def test_foreign_account_or_phone_document_is_rejected(self):
        proof = self.proof(phone='+998901111111').json()['id']
        self.assertEqual(self.purchase(eligibility_document_id=proof).status_code, 400)
        EligibilityDocument.objects.filter(pk=proof).update(phone_number=self.account.phone_number,
            telegram_user=TelegramUser.objects.create(telegram_id=2323))
        self.assertEqual(self.purchase(eligibility_document_id=proof).status_code, 400)
        self.assertEqual(self.purchase(eligibility_document_id='invalid').status_code, 400)

    def test_invalid_document_and_category_rejected(self):
        self.assertEqual(self.proof(category='other').status_code, 400)
        response = self.client.post(reverse('main:upload_eligibility_document'), {
            'category': 'student', 'phone_number': self.account.phone_number,
            'document': SimpleUploadedFile('proof.jpg', b'<html>not an image</html>'),
        }, **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(EligibilityDocument.objects.count(), 0)

    def test_self_purchase_ignores_abandoned_family_fields(self):
        result = self.purchase(members=[{'full_name': '', 'phone_number': ''}])
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()['purchase']['participant_count'], 1)

    def test_banner_stays_visible_on_started_active_group(self):
        self.group.banner = 'group_banners/banner.jpg'; self.group.save()
        data = self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers).json()
        self.assertTrue(data['courses'][0]['active_groups'][0]['banner_url'].endswith('banner.jpg'))
        self.group.start_date = timezone.localdate(); self.group.save()
        courses = self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers).json()['courses']
        self.assertEqual(len(courses), 1)
        self.assertFalse(courses[0]['can_purchase'])
        self.assertTrue(courses[0]['active_groups'][0]['banner_url'].endswith('banner.jpg'))
        self.group.is_active = False
        self.group.save()
        self.assertEqual(self.client.get(reverse('main:telegram_app_bootstrap'), **self.headers).json()['courses'], [])

    def test_banner_serves_in_production_without_exposing_other_media(self):
        with override_settings(DEBUG=False, MEDIA_ROOT=self.temp.name):
            from io import BytesIO
            from PIL import Image
            buffer = BytesIO(); Image.new('RGB', (2, 2)).save(buffer, format='PNG')
            self.group.banner.save('banner.png', SimpleUploadedFile('banner.png', buffer.getvalue(), 'image/png'))
            response = self.client.get(self.group.banner.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'image/png'); response.close()
            self.assertEqual(self.client.get('/media/private_documents/proof.pdf').status_code, 404)

    def test_private_document_requires_authorized_staff_client_scope(self):
        proof = self.proof().json()['id']
        purchase = self.purchase(eligibility_document_id=proof).json()['purchase']
        member = MiniAppPurchaseMember.objects.get(purchase_id=purchase['id'])
        user = User.objects.create_user('seller', is_staff=True)
        op = Operator.objects.create(user=user, full_name='Seller')
        user.user_permissions.add(Permission.objects.get(codename='view_client', content_type__app_label='main'))
        member.client = Client.objects.create(full_name='Buyer', phone_number=self.account.phone_number, operator=op)
        member.save()
        url = reverse('main:eligibility_document', args=[proof])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition']); response.close()
        member.client.operator = None; member.client.save()
        self.assertEqual(self.client.get(url).status_code, 404)


@override_settings(SECURE_SSL_REDIRECT=False, TELEGRAM={**app_fixture.TELEGRAM_SETTINGS, 'BOT_USERNAME':'example_bot'})
class ReferralTests(TestCase):
    @patch('main.telegram_views.send_bot_message', return_value=(True, None))
    def test_first_registration_attribution_cannot_be_replaced(self, send):
        user = User.objects.create_user('seller', is_staff=True)
        op = Operator.objects.create(user=user, full_name='Seller')
        other = Operator.objects.create(user=User.objects.create_user('other', is_staff=True), full_name='Other')
        def start(code): process_telegram_update({'message': {'from': {'id': 112233}, 'text': f'/start ref_{code}'}})
        start(op.referral_code.hex); start(other.referral_code.hex)
        self.assertEqual(TelegramUser.objects.get(telegram_id=112233).referrer, op)
        self.client.force_login(user)
        response = self.client.get(reverse('main:referrals'))
        self.assertContains(response, f'https://t.me/example_bot?start=ref_{op.referral_code.hex}')
        self.assertNotContains(response, other.referral_code.hex)

    @patch('main.telegram_views.send_bot_message', return_value=(True, None))
    def test_existing_user_is_not_reassigned_to_seller(self, send):
        op = Operator.objects.create(user=User.objects.create_user('seller'), full_name='Seller')
        TelegramUser.objects.create(telegram_id=44)
        process_telegram_update({'message': {'from': {'id': 44}, 'text': f'/start ref_{op.referral_code.hex}'}})
        self.assertIsNone(TelegramUser.objects.get(telegram_id=44).referrer_id)


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, MULTICARD=multicard_fixture.CONFIG, TELEGRAM={'BOT_TOKEN': multicard_fixture.BOT_TOKEN})
class PaymentDeliveryTests(TestCase):
    def setUp(self):
        multicard_fixture.MulticardTests.setUp(self)
        self.invoice = MulticardInvoice.objects.create(purchase=self.purchase, amount=25000050, store_id='6')

    def settle(self):
        with transaction.atomic(): _settle(self.invoice, self.payment_uuid)

    @patch('main.services.payment_notifications.send_bot_photo', return_value=(True, None))
    def test_duplicate_callback_queues_one_qr_per_member_and_sends_balance(self, send):
        self.settle(); self.settle()
        self.assertEqual(PaymentQRDelivery.objects.count(), 2)
        self.invoice.refresh_from_db(); self.assertIsNotNone(self.invoice.paid_at)
        process_payment_notifications(); process_payment_notifications()
        self.assertEqual(send.call_count, 2)
        self.assertIn('Qolgan qarz: 0 so‘m', send.call_args.kwargs['caption'])
        self.assertEqual(send.call_args.args[0], self.account.telegram_id)

    @patch('main.services.payment_notifications.send_bot_photo', return_value=(False, 'Temporary error'))
    def test_failed_delivery_retries_and_refund_suppresses_unsent_qr(self, send):
        self.settle(); process_payment_notifications()
        self.assertEqual(PaymentQRDelivery.objects.filter(sent_at__isnull=True).count(), 2)
        self.assertEqual(send.call_count, 2)
        process_payment_notifications(); self.assertEqual(send.call_count, 2)
        PaymentQRDelivery.objects.update(next_attempt_at=timezone.now()-timedelta(seconds=1))
        self.invoice.state='revert'; self.invoice.save()
        process_payment_notifications(); self.assertEqual(send.call_count, 2)

    def test_referral_payment_attribution_and_unified_method_filters(self):
        op = Operator.objects.create(user=User.objects.create_user('seller', is_staff=True), full_name='Seller')
        self.purchase.referrer=op; self.purchase.save()
        self.settle()
        self.assertEqual(Client.objects.filter(operator=op).count(), 2)
        admin = User.objects.create_superuser('admin', password='test')
        self.client.force_login(admin)
        page = self.client.get(reverse('main:payments'), {'method':'rahmat','status':'success'})
        self.assertContains(page, 'Web App')
        self.assertNotContains(self.client.get(reverse('main:payments'), {'method':'terminal'}), 'Web App')
        salary = self.client.get(reverse('main:salaries'), {'month':timezone.localdate().month, 'year':timezone.localdate().year})
        self.assertEqual(salary.context['total_collected_all'], float(self.purchase.total_amount))

    @patch.object(MulticardClient, '_request')
    def test_social_discount_receipt_matches_actual_invoice(self, request):
        self.purchase.social_discount_amount=100000
        self.purchase.total_amount-=100000
        self.invoice.amount-=10000000
        MulticardClient(multicard_fixture.CONFIG).create_invoice(self.invoice, self.purchase)
        data=request.call_args.args[2]
        item=data['ofd'][0]
        self.assertEqual(item['qty'] * item['price'], data['amount'])


from django.test import TransactionTestCase


class CustomerFeatureMigrationTests(TransactionTestCase):
    def test_existing_operators_receive_distinct_links_without_retroactive_qr(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        old = [('main', '0029_payment_settings')]
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            historical_operator = apps.get_model('main', 'Operator')
            first = historical_operator.objects.create(full_name='First')
            second = historical_operator.objects.create(full_name='Second')
            apps.get_model('main', 'PaymentSettings').objects.update_or_create(pk=1, defaults={'minimum_booking_amount':1000})
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            from .models import PaymentSettings
            self.assertNotEqual(Operator.objects.get(pk=first.pk).referral_code, Operator.objects.get(pk=second.pk).referral_code)
            self.assertEqual(PaymentSettings.booking_minimum(), 1000)
            self.assertFalse(PaymentQRDelivery.objects.exists())
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
