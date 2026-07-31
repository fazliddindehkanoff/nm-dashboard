from decimal import Decimal
from unittest import mock

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from main.models import (
    Operator, Transaction, TransactionClient, Client, Course, Group,
    _split_amount, _recalc_transaction_participants,
)
from main.admin import (
    OperatorForm, OperatorAdmin, TransactionAdmin, ClientAdmin, TransactionClientInline,
    grant_operator_permissions,
)
from main.views import dashboard_callback
from django.urls import reverse
from django.utils import timezone
from datetime import date


def _request_with_messages(user, path='/'):
    """messages freymvorki bilan ishlaydigan soxta GET so'rov."""
    request = RequestFactory().get(path)
    request.user = user
    setattr(request, 'session', {})
    setattr(request, '_messages', FallbackStorage(request))
    return request


def _post_request_with_messages(user, path, data):
    request = RequestFactory().post(path, data=data)
    request.user = user
    setattr(request, 'session', {})
    setattr(request, '_messages', FallbackStorage(request))
    return request


def _create_transaction_with_clients(clients, **kwargs):
    """Bitta yoki bir nechta mijoz biriktirilgan Transaction yaratadi va
    ulushlar/qarzni hisoblab beradi (test yordamchisi)."""
    if not isinstance(clients, (list, tuple)):
        clients = [clients]
    kwargs.setdefault('date', date.today())
    t = Transaction.objects.create(**kwargs)
    for c in clients:
        TransactionClient.objects.create(transaction=t, client=c)
    _recalc_transaction_participants(t)
    t.refresh_from_db()
    return t

class OperatorAdminTestCase(TestCase):
    def test_clean_missing_user_and_credentials(self):
        form = OperatorForm(data={
            'full_name': 'Test Operator',
            'phone_number': '',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)

    def test_clean_existing_phone_number_as_username(self):
        User.objects.create_user(username='+998901234567', password='password123')
        form = OperatorForm(data={
            'full_name': 'Test Operator',
            'phone_number': '+998 90 123-45-67', # contains formatting to test sanitization in clean
            'password': 'password123',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)

    def test_clean_valid_new_user(self):
        form = OperatorForm(data={
            'full_name': 'Test Operator',
            'phone_number': '+998901234567',
            'password': 'password123',
        })
        self.assertTrue(form.is_valid())



    def test_save_model_creates_user_with_phone_as_username(self):
        from django.contrib.admin.sites import AdminSite

        site = AdminSite()
        admin_instance = OperatorAdmin(Operator, site)

        form_data = {
            'full_name': 'New Operator',
            'phone_number': '+998 90 111-22-33',
            'password': 'secret_password_123',
        }
        form = OperatorForm(data=form_data)
        self.assertTrue(form.is_valid())

        operator_instance = form.save(commit=False)
        
        class DummyRequest:
            user = User.objects.create_superuser(username='admin', password='adminpassword')
        
        request = DummyRequest()
        admin_instance.save_model(request, operator_instance, form, change=False)
        operator_instance.save()
        
        # Verify operator is saved with user
        self.assertIsNotNone(operator_instance.user)
        self.assertEqual(operator_instance.user.username, '+998901112233')
        self.assertTrue(operator_instance.user.is_staff)
        self.assertTrue(operator_instance.user.has_perm('main.add_transaction'))
        # Operator needs TransactionClient perms too, or the "To'lov ishtirokchilari"
        # inline (where they attach clients to a payment) won't render for them at all.
        self.assertTrue(operator_instance.user.has_perm('main.add_transactionclient'))
        self.assertTrue(operator_instance.user.has_perm('main.change_transactionclient'))
        self.assertTrue(operator_instance.user.has_perm('main.view_transactionclient'))
        self.assertTrue(operator_instance.user.has_perm('main.delete_transactionclient'))
        self.assertTrue(operator_instance.user.check_password('secret_password_123'))


class TransactionAdminPermissionsTestCase(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        self.site = AdminSite()
        self.admin_instance = TransactionAdmin(Transaction, self.site)

        # Create superuser
        self.superuser = User.objects.create_superuser(username='admin', password='password')

        # Create operator user and operator model
        self.op_user = User.objects.create_user(username='+998901111111', password='password', is_staff=True)
        grant_operator_permissions(self.op_user)
        self.operator = Operator.objects.create(user=self.op_user, full_name='Op One', phone_number='+998901111111')

        # Create operator user 2 and operator model 2
        self.op_user2 = User.objects.create_user(username='+998902222222', password='password', is_staff=True)
        grant_operator_permissions(self.op_user2)
        self.operator2 = Operator.objects.create(user=self.op_user2, full_name='Op Two', phone_number='+998902222222')

        # Create dummy Client, Course, Group
        self.client = Client.objects.create(full_name='Test Client', phone_number='+998903333333')
        self.course = Course.objects.create(name='Test Course', price=100000)
        self.group = Group.objects.create(course=self.course, start_date=date.today(), is_active=True)

        # Create unconfirmed and confirmed transactions
        self.tx_unconfirmed = _create_transaction_with_clients(
            self.client,
            operator=self.operator,
            group=self.group,
            date=date.today(),
            amount=50000,
            payment_type='bron',
            is_confirmed=False
        )
        self.tx_confirmed = _create_transaction_with_clients(
            self.client,
            operator=self.operator,
            group=self.group,
            date=date.today(),
            amount=100000,
            payment_type='to_liq_tolov',
            is_confirmed=True
        )

    def test_approve_permission_restrictions(self):
        class DummyRequest:
            def __init__(self, user):
                self.user = user
        
        req_superuser = DummyRequest(self.superuser)
        req_operator = DummyRequest(self.op_user)

        self.assertTrue(self.admin_instance.has_confirm_permission(req_superuser))
        self.assertFalse(self.admin_instance.has_confirm_permission(req_operator))

    def test_refund_permission_restrictions(self):
        class DummyRequest:
            def __init__(self, user):
                self.user = user

        req_superuser = DummyRequest(self.superuser)
        req_operator = DummyRequest(self.op_user)

        self.assertTrue(self.admin_instance.has_refund_permission(req_superuser))
        self.assertFalse(self.admin_instance.has_refund_permission(req_operator))

    def test_operator_cannot_change_or_delete_confirmed_transaction(self):
        class DummyRequest:
            def __init__(self, user):
                self.user = user

        req_operator = DummyRequest(self.op_user)
        req_superuser = DummyRequest(self.superuser)

        # Superuser can change/delete confirmed
        self.assertTrue(self.admin_instance.has_change_permission(req_superuser, self.tx_confirmed))
        self.assertTrue(self.admin_instance.has_delete_permission(req_superuser, self.tx_confirmed))

        # Operator CANNOT change/delete confirmed
        self.assertFalse(self.admin_instance.has_change_permission(req_operator, self.tx_confirmed))
        self.assertFalse(self.admin_instance.has_delete_permission(req_operator, self.tx_confirmed))

        # Operator CAN change their own unconfirmed, but CANNOT delete since they don't have delete permission
        self.assertTrue(self.admin_instance.has_change_permission(req_operator, self.tx_unconfirmed))
        self.assertFalse(self.admin_instance.has_delete_permission(req_operator, self.tx_unconfirmed))

    def test_operator_queryset_isolation(self):
        class DummyRequest:
            def __init__(self, user):
                self.user = user

        req_operator = DummyRequest(self.op_user)
        req_operator2 = DummyRequest(self.op_user2)
        req_superuser = DummyRequest(self.superuser)

        # Superuser sees all
        qs_super = self.admin_instance.get_queryset(req_superuser)
        self.assertEqual(qs_super.count(), 2)

        # Operator 1 only sees their own
        qs_op1 = self.admin_instance.get_queryset(req_operator)
        self.assertEqual(qs_op1.count(), 2) # both are theirs

        # Operator 2 sees none (as they have none)
        qs_op2 = self.admin_instance.get_queryset(req_operator2)
        self.assertEqual(qs_op2.count(), 0)

    def test_dashboard_callback_isolation(self):
        class DummyRequest:
            def __init__(self, user):
                self.user = user
                self.GET = {}

        req_operator = DummyRequest(self.op_user)
        context = {}
        dashboard_callback(req_operator, context)

        # Verify context is populated and restricted
        self.assertTrue(context['is_plain_operator'])
        self.assertEqual(context['transactions_count'], 2) # only operator 1's transactions
        self.assertEqual(len(context['operators']), 1)
        self.assertEqual(context['operators'][0], self.operator)


class DebtCalculationTestCase(TestCase):
    """Qarz mijoz+guruh bo'yicha jami to'lovlar asosida hisoblanishi kerak."""

    def setUp(self):
        from main.models import Discount
        self.course = Course.objects.create(name='Kurs', price=1000000)
        self.group = Group.objects.create(course=self.course, start_date=date.today())
        Discount.objects.filter(is_booking=True).update(is_active=False)
        Discount.objects.create(name='Bron', amount=200000, is_booking=True, is_active=True)
        self.client_obj = Client.objects.create(full_name='C', phone_number='+998900000001')

    def _tx(self, amount, ptype, day):
        t = Transaction.objects.create(
            group=self.group, date=date(2026, 1, day), amount=amount, payment_type=ptype,
        )
        TransactionClient.objects.create(transaction=t, client=self.client_obj)
        _recalc_transaction_participants(t)
        return t

    def _debt(self, t):
        return TransactionClient.objects.get(transaction=t, client=self.client_obj).debt

    def _group_debt(self):
        return sum(
            tc.debt for tc in TransactionClient.objects.filter(
                client=self.client_obj, transaction__group=self.group, transaction__is_refunded=False
            )
        )

    def test_single_bron_creates_debt(self):
        t = self._tx(300000, 'bron', 1)  # net 800k
        self.assertEqual(self._debt(t), 500000)

    def test_doplata_reduces_prior_bron_debt(self):
        self._tx(300000, 'bron', 1)
        t2 = self._tx(400000, 'doplata', 2)
        self.assertEqual(self._debt(t2), 100000)
        self.assertEqual(self._group_debt(), 100000)

    def test_debt_cleared_when_fully_paid(self):
        self._tx(300000, 'bron', 1)
        self._tx(400000, 'doplata', 2)
        self._tx(100000, 'doplata', 3)
        self.assertEqual(self._group_debt(), 0)

    def test_only_latest_transaction_holds_debt(self):
        t1 = self._tx(300000, 'bron', 1)
        self._tx(400000, 'doplata', 2)
        self.assertEqual(self._debt(t1), 0)  # eski to'lov qarzni saqlamaydi

    def test_full_payment_no_debt(self):
        t = self._tx(1000000, 'to_liq_tolov', 1)  # chegirmasiz to'liq
        self.assertEqual(self._debt(t), 0)

    def test_refund_recomputes_debt(self):
        bron = self._tx(300000, 'bron', 1)
        self._tx(400000, 'doplata', 2)
        self.assertEqual(self._group_debt(), 100000)
        bron.is_refunded = True
        bron.save(update_fields=['is_refunded'])
        # bron qaytarilgach bron chegirmasi ham yo'qoladi: net 1M - 400k = 600k
        self.assertEqual(self._group_debt(), 600000)
        self.assertEqual(self._debt(bron), 0)


class HomepageRedirectTestCase(TestCase):
    def test_homepage_redirects_to_admin(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/admin/')


class OperatorRedirectMiddlewareTestCase(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username='admin', password='password')
        self.op_user = User.objects.create_user(username='+998901111111', password='password', is_staff=True)
        grant_operator_permissions(self.op_user)
        self.operator = Operator.objects.create(user=self.op_user, full_name='Op One', phone_number='+998901111111')

    def test_operator_redirected_from_dashboard(self):
        self.client.force_login(self.op_user)
        response = self.client.get('/admin/')
        self.assertRedirects(response, '/admin/main/transaction/')

    def test_operator_redirected_from_operators_page(self):
        self.client.force_login(self.op_user)
        response = self.client.get('/admin/main/operator/')
        self.assertRedirects(response, '/admin/main/transaction/')

    def test_superuser_not_redirected_from_dashboard(self):
        self.client.force_login(self.superuser)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)


class ClientAdminPermissionsTestCase(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        from main.admin import ClientAdmin
        self.site = AdminSite()
        self.admin_instance = ClientAdmin(Client, self.site)

        # Create two operator users
        self.op_user1 = User.objects.create_user(username='+998901111111', password='password', is_staff=True)
        grant_operator_permissions(self.op_user1)
        self.op1 = Operator.objects.create(user=self.op_user1, full_name='Op One', phone_number='+998901111111')

        self.op_user2 = User.objects.create_user(username='+998902222222', password='password', is_staff=True)
        grant_operator_permissions(self.op_user2)
        self.op2 = Operator.objects.create(user=self.op_user2, full_name='Op Two', phone_number='+998902222222')

        # Create clients
        self.client1 = Client.objects.create(full_name='Client One', phone_number='+998903333333', operator=self.op1)
        self.client2 = Client.objects.create(full_name='Client Two', phone_number='+998904444444', operator=self.op2)
        self.client_shared = Client.objects.create(full_name='Client Shared', phone_number='+998905555555', operator=None)

    def test_operator_has_add_change_client_permissions(self):
        self.assertTrue(self.op_user1.has_perm('main.add_client'))
        self.assertTrue(self.op_user1.has_perm('main.change_client'))

    def test_operator_client_queryset_isolation(self):
        class DummyRequest:
            def __init__(self, user):
                self.user = user

        req_op1 = DummyRequest(self.op_user1)
        qs_op1 = self.admin_instance.get_queryset(req_op1)
        self.assertEqual(qs_op1.count(), 1)
        self.assertEqual(qs_op1.first(), self.client1)

    def test_operator_client_auto_assigns_operator(self):
        # save_model endi yangi mijozda amoCRM hook'ini chaqiradi (sozlanmagan
        # bo'lsa ogohlantirish xabari) — shuning uchun messages qo'llab-quvvatlash kerak.
        req_op1 = _request_with_messages(self.op_user1)
        new_client = Client(full_name='Client New', phone_number='+998906666666')
        self.admin_instance.save_model(req_op1, new_client, form=None, change=False)
        self.assertEqual(new_client.operator, self.op1)


# ---------------------------------------------------------------------------
# amoCRM integratsiyasi testlari (tarmoqqa chiqmaydi — HTTP qatlami mock qilinadi)
# ---------------------------------------------------------------------------

def _contact(contact_id, phone, lead_ids):
    return {
        "id": contact_id,
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": phone}]}
        ],
        "_embedded": {"leads": [{"id": lid} for lid in lead_ids]},
    }


def _fake_request(contacts=None, leads=None):
    """`_request` uchun path bo'yicha javob qaytaruvchi side_effect yasaydi."""
    contacts_resp = {"_embedded": {"contacts": contacts}} if contacts is not None else None
    leads_resp = {"_embedded": {"leads": leads}} if leads is not None else None

    def _side(method, path, params=None, json=None):
        if path == "/api/v4/contacts":
            return contacts_resp
        if path == "/api/v4/leads":
            return leads_resp
        if path.startswith("/api/v4/leads/"):
            return {"id": 1, "status_id": 142}
        return None

    return _side


class AmoCRMPhoneNormalizeTestCase(TestCase):
    def test_various_formats_same_key(self):
        from main.services.amocrm import _normalize_phone
        self.assertEqual(_normalize_phone("+998 90 123-45-67"), "901234567")
        self.assertEqual(_normalize_phone("901234567"), "901234567")
        self.assertEqual(_normalize_phone("998901234567"), "901234567")

    def test_short_phone_returns_none(self):
        from main.services.amocrm import _normalize_phone
        self.assertIsNone(_normalize_phone("12345"))
        self.assertIsNone(_normalize_phone(""))
        self.assertIsNone(_normalize_phone(None))


class AmoCRMFindLeadTestCase(TestCase):
    def test_active_lead_match(self):
        from main.services import amocrm
        side = _fake_request(
            contacts=[_contact(11, "+998901234567", [501])],
            leads=[{"id": 501, "status_id": 100, "created_at": 1000}],
        )
        with mock.patch.object(amocrm, "_request", side_effect=side):
            match = amocrm.find_lead_by_phone("+998 90 123-45-67")
        self.assertIsNotNone(match)
        self.assertEqual(match.contact_id, 11)
        self.assertEqual(match.lead_id, 501)
        self.assertTrue(match.is_active)

    def test_no_contacts_returns_none(self):
        from main.services import amocrm
        with mock.patch.object(amocrm, "_request", side_effect=_fake_request(contacts=None)):
            self.assertIsNone(amocrm.find_lead_by_phone("901234567"))

    def test_short_phone_skips_lookup(self):
        from main.services import amocrm
        with mock.patch.object(amocrm, "_request") as m:
            self.assertIsNone(amocrm.find_lead_by_phone("123"))
        m.assert_not_called()

    def test_fuzzy_false_positive_filtered(self):
        from main.services import amocrm
        # amoCRM boshqa telefonli kontaktni qaytaradi — u filtrlanishi kerak.
        side = _fake_request(
            contacts=[_contact(11, "+998907776655", [501])],
            leads=[{"id": 501, "status_id": 100}],
        )
        with mock.patch.object(amocrm, "_request", side_effect=side):
            self.assertIsNone(amocrm.find_lead_by_phone("+998901234567"))

    def test_newest_active_lead_wins(self):
        from main.services import amocrm
        side = _fake_request(
            contacts=[_contact(11, "+998901234567", [501, 502, 503])],
            leads=[
                {"id": 501, "status_id": 142, "created_at": 3000},  # yopiq
                {"id": 502, "status_id": 100, "created_at": 1000},  # faol, eski
                {"id": 503, "status_id": 100, "created_at": 2000},  # faol, yangi
            ],
        )
        with mock.patch.object(amocrm, "_request", side_effect=side):
            match = amocrm.find_lead_by_phone("901234567")
        self.assertEqual(match.lead_id, 503)
        self.assertTrue(match.is_active)

    def test_only_closed_leads(self):
        from main.services import amocrm
        side = _fake_request(
            contacts=[_contact(11, "+998901234567", [501, 502])],
            leads=[
                {"id": 501, "status_id": 143, "created_at": 1000},
                {"id": 502, "status_id": 142, "created_at": 2000},
            ],
        )
        with mock.patch.object(amocrm, "_request", side_effect=side):
            match = amocrm.find_lead_by_phone("901234567")
        self.assertEqual(match.lead_id, 502)  # eng yangi yopiq
        self.assertFalse(match.is_active)


class AmoCRMLinkClientTestCase(TestCase):
    def test_stores_fields_on_match(self):
        from main.services import amocrm
        client = Client.objects.create(full_name="A", phone_number="+998901234567")
        match = amocrm.LeadMatch(contact_id=11, lead_id=501, is_active=True)
        with mock.patch.object(amocrm, "find_lead_by_phone", return_value=match):
            result = amocrm.link_client_to_amocrm(client)
        client.refresh_from_db()
        self.assertEqual(result.lead_id, 501)
        self.assertEqual(client.amocrm_id, 11)
        self.assertEqual(client.amocrm_lead_id, 501)
        self.assertIsNotNone(client.synced_at)

    def test_existing_amocrm_id_not_overwritten(self):
        from main.services import amocrm
        client = Client.objects.create(full_name="A", phone_number="+998901234567", amocrm_id=999)
        match = amocrm.LeadMatch(contact_id=11, lead_id=501, is_active=True)
        with mock.patch.object(amocrm, "find_lead_by_phone", return_value=match):
            amocrm.link_client_to_amocrm(client)
        client.refresh_from_db()
        self.assertEqual(client.amocrm_id, 999)  # o'zgarmagan
        self.assertEqual(client.amocrm_lead_id, 501)

    def test_contact_id_conflict_writes_only_lead(self):
        from main.services import amocrm
        Client.objects.create(full_name="Other", phone_number="+998900000000", amocrm_id=11)
        client = Client.objects.create(full_name="A", phone_number="+998901234567")
        match = amocrm.LeadMatch(contact_id=11, lead_id=501, is_active=True)
        with mock.patch.object(amocrm, "find_lead_by_phone", return_value=match):
            result = amocrm.link_client_to_amocrm(client)  # IntegrityError bo'lmasligi kerak
        client.refresh_from_db()
        self.assertIsNone(client.amocrm_id)
        self.assertEqual(client.amocrm_lead_id, 501)
        self.assertTrue(result.contact_conflict)

    def test_no_match_returns_none(self):
        from main.services import amocrm
        client = Client.objects.create(full_name="A", phone_number="+998901234567")
        with mock.patch.object(amocrm, "find_lead_by_phone", return_value=None):
            self.assertIsNone(amocrm.link_client_to_amocrm(client))
        client.refresh_from_db()
        self.assertIsNone(client.amocrm_lead_id)


class AmoCRMCloseLeadTestCase(TestCase):
    def test_close_lead_patches_status_142(self):
        from main.services import amocrm
        with mock.patch.object(amocrm, "_request", return_value=None) as m:
            amocrm.close_lead(501)
        m.assert_called_once()
        args, kwargs = m.call_args
        self.assertEqual(args[0], "PATCH")
        self.assertEqual(args[1], "/api/v4/leads/501")
        self.assertEqual(kwargs["json"], {"status_id": 142})


@override_settings(AMOCRM={"SUBDOMAIN": "", "TOKEN": ""})
class AmoCRMNotConfiguredTestCase(TestCase):
    def test_raises_when_unconfigured(self):
        from main.services import amocrm
        with self.assertRaises(amocrm.AmoCRMNotConfigured):
            amocrm._request("GET", "/api/v4/contacts")


class TransactionAdminSourceTestCase(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        self.admin = TransactionAdmin(Transaction, AdminSite())
        self.super = User.objects.create_superuser(username='admin', password='x')
        self.course = Course.objects.create(name='C', price=100000)
        self.group = Group.objects.create(course=self.course, start_date=date.today())

    def _new_tx(self, client):
        tx = Transaction.objects.create(
            operator=None, group=self.group, date=date.today(), amount=50000, payment_type='naqd',
        )
        TransactionClient.objects.create(transaction=tx, client=client)
        return tx

    def test_source_amocrm_when_lead_found(self):
        from main.services import amocrm
        client = Client.objects.create(full_name='A', phone_number='+998901234567')
        obj = self._new_tx(client)
        match = amocrm.LeadMatch(contact_id=11, lead_id=501, is_active=True)
        with mock.patch('main.admin.link_client_to_amocrm', return_value=match):
            self.admin._amocrm_set_source(_request_with_messages(self.super), obj)
        self.assertEqual(obj.source, 'amocrm_other')

    def test_source_not_in_amocrm_when_no_lead(self):
        client = Client.objects.create(full_name='A', phone_number='+998901234567')
        obj = self._new_tx(client)
        with mock.patch('main.admin.link_client_to_amocrm', return_value=None):
            self.admin._amocrm_set_source(_request_with_messages(self.super), obj)
        self.assertEqual(obj.source, 'not_in_amocrm')

    def test_source_not_in_amocrm_on_error(self):
        from main.services.amocrm import AmoCRMError
        client = Client.objects.create(full_name='A', phone_number='+998901234567')
        obj = self._new_tx(client)
        with mock.patch('main.admin.link_client_to_amocrm', side_effect=AmoCRMError("down")):
            self.admin._amocrm_set_source(_request_with_messages(self.super), obj)
        self.assertEqual(obj.source, 'not_in_amocrm')

    def test_reuses_existing_lead_without_lookup(self):
        client = Client.objects.create(full_name='A', phone_number='+998901234567',
                                       amocrm_id=11, amocrm_lead_id=501)
        obj = self._new_tx(client)
        with mock.patch('main.admin.link_client_to_amocrm') as m:
            matches = self.admin._amocrm_set_source(_request_with_messages(self.super), obj)
        m.assert_not_called()
        self.assertEqual(obj.source, 'amocrm_other')
        self.assertEqual(matches[0].lead_id, 501)

    def test_source_amocrm_other_when_any_client_has_lead(self):
        client_no_lead = Client.objects.create(full_name='NoLead', phone_number='+998903333333')
        client_with_lead = Client.objects.create(
            full_name='HasLead', phone_number='+998904444444', amocrm_id=1, amocrm_lead_id=501,
        )
        tx = Transaction.objects.create(group=self.group, date=date.today(), amount=50000, payment_type='naqd')
        TransactionClient.objects.create(transaction=tx, client=client_no_lead)
        TransactionClient.objects.create(transaction=tx, client=client_with_lead)

        with mock.patch('main.admin.link_client_to_amocrm', return_value=None):
            matches = self.admin._amocrm_set_source(_request_with_messages(self.super), tx)
        self.assertEqual(tx.source, 'amocrm_other')
        self.assertEqual(len(matches), 2)

    def test_source_not_in_amocrm_when_no_client_has_lead(self):
        client1 = Client.objects.create(full_name='A2', phone_number='+998905555555')
        client2 = Client.objects.create(full_name='B2', phone_number='+998906666666')
        tx = Transaction.objects.create(group=self.group, date=date.today(), amount=50000, payment_type='naqd')
        TransactionClient.objects.create(transaction=tx, client=client1)
        TransactionClient.objects.create(transaction=tx, client=client2)

        with mock.patch('main.admin.link_client_to_amocrm', return_value=None):
            self.admin._amocrm_set_source(_request_with_messages(self.super), tx)
        self.assertEqual(tx.source, 'not_in_amocrm')


# ---------------------------------------------------------------------------
# Oilaviy (bir nechta mijozli) to'lov testlari
# ---------------------------------------------------------------------------

class SplitAmountTestCase(TestCase):
    def test_even_split(self):
        shares = _split_amount(Decimal('300000'), 3)
        self.assertEqual(shares, [Decimal('100000.00')] * 3)
        self.assertEqual(sum(shares), Decimal('300000'))

    def test_uneven_split_remainder_goes_to_first_rows(self):
        shares = _split_amount(Decimal('100000'), 3)
        self.assertEqual(sum(shares), Decimal('100000'))
        self.assertEqual(shares[0], Decimal('33333.34'))
        self.assertEqual(shares[1], Decimal('33333.33'))
        self.assertEqual(shares[2], Decimal('33333.33'))

    def test_two_way_split(self):
        shares = _split_amount(Decimal('500000'), 2)
        self.assertEqual(shares, [Decimal('250000.00'), Decimal('250000.00')])


class MultiClientTransactionTestCase(TestCase):
    """Bitta Transaction'ga bir nechta mijoz biriktirilganda ulush/qarz hisob-kitobi."""

    def setUp(self):
        self.course1 = Course.objects.create(name='C1', price=1000000)
        self.course2 = Course.objects.create(name='C2', price=500000)
        self.group1 = Group.objects.create(course=self.course1, start_date=date.today(), is_active=True)
        self.group2 = Group.objects.create(course=self.course2, start_date=date.today(), is_active=True)

    def test_two_clients_same_group_split_evenly(self):
        client_a = Client.objects.create(full_name='Child A', phone_number='+998901111111')
        client_b = Client.objects.create(full_name='Child B', phone_number='+998902222222')
        tx = _create_transaction_with_clients(
            [client_a, client_b], group=self.group1, amount=500000, payment_type='naqd',
        )
        self.assertEqual(tx.clients.count(), 2)
        rows = {tc.client.full_name: tc for tc in TransactionClient.objects.filter(transaction=tx)}
        for name in ('Child A', 'Child B'):
            self.assertEqual(rows[name].share_amount, Decimal('250000.00'))
            # Har bir mijozning qarzi mustaqil hisoblanadi: 1,000,000 - 250,000 = 750,000
            self.assertEqual(rows[name].debt, Decimal('750000'))

    def test_three_clients_non_divisible_amount_sums_exactly(self):
        clients = [
            Client.objects.create(full_name=f'Child {i}', phone_number=f'+99890111111{i}')
            for i in range(3)
        ]
        tx = _create_transaction_with_clients(clients, group=self.group1, amount=100000, payment_type='naqd')
        shares = [tc.share_amount for tc in TransactionClient.objects.filter(transaction=tx)]
        self.assertEqual(len(shares), 3)
        self.assertEqual(sum(shares), Decimal('100000'))

    def test_clients_in_same_transaction_share_the_group(self):
        # Transaction.group bitta — har bir mijoz shu bitta guruh bo'yicha qarz oladi,
        # lekin ularning boshqa (alohida) tranzaksiyalari mustaqil hisoblanadi.
        client_a = Client.objects.create(full_name='Child A', phone_number='+998901111111')
        client_b = Client.objects.create(full_name='Child B', phone_number='+998902222222')
        tx = _create_transaction_with_clients(
            [client_a, client_b], group=self.group1, amount=400000, payment_type='naqd',
        )
        # client_b to'liq boshqa guruhda alohida to'lov qiladi — client_a ga ta'sir qilmasligi kerak
        _create_transaction_with_clients(client_b, group=self.group2, amount=500000, payment_type='naqd')

        tc_a = TransactionClient.objects.get(transaction=tx, client=client_a)
        tc_b_group1 = TransactionClient.objects.get(transaction=tx, client=client_b)
        self.assertEqual(tc_a.debt, Decimal('800000'))  # 1,000,000 - 200,000
        self.assertEqual(tc_b_group1.debt, Decimal('800000'))
        tc_b_group2 = TransactionClient.objects.get(client=client_b, transaction__group=self.group2)
        self.assertEqual(tc_b_group2.debt, Decimal('0'))  # 500,000 - 500,000


class TransactionInlineClientTestCase(TestCase):
    """'+ Add client' (inline formset) orqali mavjud to'lovga mijoz qo'shish."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        self.site = AdminSite()
        self.superuser = User.objects.create_superuser(username='admin', password='x')
        self.operator = Operator.objects.create(full_name='Op', phone_number='+998900000000')
        self.course = Course.objects.create(name='C', price=1000000)
        self.group = Group.objects.create(course=self.course, start_date=date.today(), is_active=True)

    def _formset_for(self, request, transaction, data):
        inline = TransactionClientInline(Transaction, self.site)
        FormSetClass = inline.get_formset(request, transaction)
        prefix = FormSetClass.get_default_prefix()
        full_data = {f'{prefix}-{k}': v for k, v in data.items()}
        # Unfold's PaginationFormSetMixin takes (request, per_page, *args, **kwargs) —
        # `request` must be passed positionally first, matching how the real admin
        # constructs inline formsets (not just `FormSetClass(data, ...)`).
        return FormSetClass(request, None, data=full_data, instance=transaction, prefix=prefix)

    def test_add_second_client_to_existing_transaction(self):
        request = _request_with_messages(self.superuser)
        client_a = Client.objects.create(full_name='Child A', phone_number='+998901111111')
        tx = _create_transaction_with_clients(client_a, group=self.group, amount=500000, payment_type='naqd')
        existing_tc = TransactionClient.objects.get(transaction=tx, client=client_a)

        data = {
            'TOTAL_FORMS': '2',
            'INITIAL_FORMS': '1',
            'MIN_NUM_FORMS': '1',
            'MAX_NUM_FORMS': '1000',
            '0-id': str(existing_tc.pk),
            '0-client_name': 'Child A',
            '0-client_phone': '+998901111111',
            '1-client_name': 'Child B',
            '1-client_phone': '+998902222222',
        }
        formset = self._formset_for(request, tx, data)
        self.assertTrue(formset.is_valid(), formset.errors)
        with mock.patch('main.admin.link_client_to_amocrm', return_value=None):
            formset.save()
        _recalc_transaction_participants(tx)

        self.assertEqual(tx.clients.count(), 2)
        shares = {tc.client.full_name: tc.share_amount for tc in TransactionClient.objects.filter(transaction=tx)}
        self.assertEqual(shares['Child A'], Decimal('250000.00'))
        self.assertEqual(shares['Child B'], Decimal('250000.00'))

    def test_add_transaction_end_to_end_via_real_admin_post(self):
        # `formset.save()` above bypasses `TransactionAdmin.save_related` entirely —
        # this test goes through the real admin add view (self.client.post), the
        # only path that actually exercises `save_related`'s recalculation call.
        self.client.force_login(self.superuser)
        data = {
            'operator': str(self.operator.pk),
            'group': str(self.group.pk),
            'date': '2026-01-01',
            'amount': '500000',
            'payment_type': 'naqd',
            'discount': '',
            'participants-TOTAL_FORMS': '2',
            'participants-INITIAL_FORMS': '0',
            'participants-MIN_NUM_FORMS': '1',
            'participants-MAX_NUM_FORMS': '1000',
            'participants-0-client_name': 'E2E Child A',
            'participants-0-client_phone': '+998907777777',
            'participants-1-client_name': 'E2E Child B',
            'participants-1-client_phone': '+998908888888',
            '_save': 'Saqlash',
        }
        with mock.patch('main.admin.link_client_to_amocrm', return_value=None):
            response = self.client.post('/admin/main/transaction/add/', data=data)
        self.assertEqual(response.status_code, 302, getattr(response, 'context_data', {}).get('errors'))

        tx = Transaction.objects.get()
        self.assertEqual(tx.clients.count(), 2)
        for tc in TransactionClient.objects.filter(transaction=tx):
            self.assertEqual(tc.share_amount, Decimal('250000.00'))


class ReceivePaymentTestCase(TestCase):
    """Tranzaksiya detail sahifasidagi 'Pul qabul qilish' (faqat admin)."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        self.admin = TransactionAdmin(Transaction, AdminSite())
        self.superuser = User.objects.create_superuser(username='admin', password='x')
        self.op_user = User.objects.create_user(username='+998900000009', password='x', is_staff=True)
        grant_operator_permissions(self.op_user)
        Operator.objects.create(user=self.op_user, full_name='Op', phone_number='+998900000009')
        self.course = Course.objects.create(name='C', price=1000000)
        self.group = Group.objects.create(course=self.course, start_date=date.today(), is_active=True)
        self.client_obj = Client.objects.create(full_name='A', phone_number='+998901111111')

    def test_accumulates_amount_across_two_calls_single_client(self):
        tx = _create_transaction_with_clients(
            self.client_obj, group=self.group, amount=100000, payment_type='naqd',
        )
        request = _post_request_with_messages(
            self.superuser, f'/admin/main/transaction/{tx.pk}/receive-payment/', {'amount': '200000'},
        )
        self.admin.receive_payment_detail(request, str(tx.pk))
        tx.refresh_from_db()
        self.assertEqual(tx.amount, Decimal('300000'))
        tc = TransactionClient.objects.get(transaction=tx, client=self.client_obj)
        self.assertEqual(tc.debt, Decimal('700000'))

        request2 = _post_request_with_messages(
            self.superuser, f'/admin/main/transaction/{tx.pk}/receive-payment/', {'amount': '700000'},
        )
        self.admin.receive_payment_detail(request2, str(tx.pk))
        tx.refresh_from_db()
        self.assertEqual(tx.amount, Decimal('1000000'))
        tc.refresh_from_db()
        self.assertEqual(tc.debt, Decimal('0'))

    def test_accumulates_and_splits_across_two_clients(self):
        client_b = Client.objects.create(full_name='B', phone_number='+998902222222')
        tx = _create_transaction_with_clients(
            [self.client_obj, client_b], group=self.group, amount=200000, payment_type='naqd',
        )
        request = _post_request_with_messages(
            self.superuser, f'/admin/main/transaction/{tx.pk}/receive-payment/', {'amount': '800000'},
        )
        self.admin.receive_payment_detail(request, str(tx.pk))
        tx.refresh_from_db()
        self.assertEqual(tx.amount, Decimal('1000000'))
        for tc in TransactionClient.objects.filter(transaction=tx):
            self.assertEqual(tc.share_amount, Decimal('500000.00'))
            self.assertEqual(tc.debt, Decimal('500000'))

    def test_get_request_shows_form_without_changes(self):
        tx = _create_transaction_with_clients(
            self.client_obj, group=self.group, amount=100000, payment_type='naqd',
        )
        request = _request_with_messages(
            self.superuser, f'/admin/main/transaction/{tx.pk}/receive-payment/'
        )
        response = self.admin.receive_payment_detail(request, str(tx.pk))
        response.render()
        self.assertEqual(response.status_code, 200)
        tx.refresh_from_db()
        self.assertEqual(tx.amount, Decimal('100000'))

    def test_superuser_only_permission(self):
        class DummyRequest:
            def __init__(self, user):
                self.user = user

        self.assertTrue(self.admin.has_receive_payment_permission(DummyRequest(self.superuser)))
        self.assertFalse(self.admin.has_receive_payment_permission(DummyRequest(self.op_user)))

    def test_get_request_includes_payment_impact_summary(self):
        tx = _create_transaction_with_clients(
            self.client_obj, group=self.group, amount=100000, payment_type='naqd',
        )
        request = _request_with_messages(
            self.superuser, f'/admin/main/transaction/{tx.pk}/receive-payment/'
        )
        response = self.admin.receive_payment_detail(request, str(tx.pk))
        response.render()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context_data['current_debt'], Decimal('900000'))
        self.assertEqual(response.context_data['course_price'], Decimal('1000000'))
        self.assertEqual(response.context_data['clients_count'], 1)


class ConfirmRefundPreviewTestCase(TestCase):
    """Confirm/refund action GET must be preview-only; POST mutates finance state."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        self.admin = TransactionAdmin(Transaction, AdminSite())
        self.superuser = User.objects.create_superuser(username='admin-preview', password='x')
        self.course = Course.objects.create(name='C', price=1000000)
        self.group = Group.objects.create(course=self.course, start_date=date.today(), is_active=True)
        self.client_obj = Client.objects.create(full_name='Preview Client', phone_number='+998****3333')

    def _tx(self):
        return _create_transaction_with_clients(
            self.client_obj, group=self.group, amount=100000, payment_type='naqd',
        )

    def test_confirm_get_preview_does_not_mutate(self):
        tx = self._tx()
        path = f'/admin/main/transaction/{tx.pk}/confirm-detail/'
        request = _request_with_messages(self.superuser, path)
        response = self.admin.confirm_transaction_detail(request, str(tx.pk))
        response.render()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.template_name, 'admin/main/transaction/confirm_confirm.html')
        tx.refresh_from_db()
        self.assertFalse(tx.is_confirmed)
        self.assertIsNone(tx.confirmed_at)

    def test_confirm_post_mutates(self):
        tx = self._tx()
        request = _post_request_with_messages(
            self.superuser,
            f'/admin/main/transaction/{tx.pk}/confirm-detail/',
            {'next': reverse('admin:main_transaction_change', args=[tx.pk])},
        )
        with mock.patch('main.admin.send_payment_qr', return_value=(True, 'ok')):
            response = self.admin.confirm_transaction_detail(request, str(tx.pk))
        self.assertEqual(response.status_code, 302)
        tx.refresh_from_db()
        self.assertTrue(tx.is_confirmed)
        self.assertEqual(tx.confirmed_by, self.superuser)

    def test_refund_get_preview_does_not_mutate(self):
        tx = self._tx()
        path = f'/admin/main/transaction/{tx.pk}/refund-detail/'
        request = _request_with_messages(self.superuser, path)
        response = self.admin.refund_transaction_detail(request, str(tx.pk))
        response.render()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.template_name, 'admin/main/transaction/refund_confirm.html')
        tx.refresh_from_db()
        self.assertFalse(tx.is_refunded)
        self.assertIsNone(tx.refunded_at)

    def test_refund_post_mutates(self):
        tx = self._tx()
        request = _post_request_with_messages(
            self.superuser,
            f'/admin/main/transaction/{tx.pk}/refund-detail/',
            {'next': reverse('admin:main_transaction_change', args=[tx.pk])},
        )
        response = self.admin.refund_transaction_detail(request, str(tx.pk))
        self.assertEqual(response.status_code, 302)
        tx.refresh_from_db()
        self.assertTrue(tx.is_refunded)
        self.assertEqual(tx.refunded_at, timezone.now().date())


class QRVerifyViewTestCase(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username='qr-admin', password='x')
        self.client_obj = Client.objects.create(full_name='QR Client', phone_number='+998****4444')

    def test_profile_link_points_to_read_only_client_detail(self):
        self.client.force_login(self.superuser)
        response = self.client.get('/qr-verify/', {'code': str(self.client_obj.uuid)})
        self.assertEqual(response.status_code, 200)
        detail_url = reverse('admin:main_client_detail', args=[self.client_obj.pk])
        change_url = reverse('admin:main_client_change', args=[self.client_obj.pk])
        self.assertContains(response, detail_url)
        self.assertNotContains(response, f'href="{change_url}"')


# ---------------------------------------------------------------------------
# Maosh hisob-kitobi: sotuvlar soni emas, summasiga qarab foiz
# ---------------------------------------------------------------------------

class SalaryPercentageTestCase(TestCase):
    def test_bracket_boundaries(self):
        from main.views import calculate_salary_percentage as calc
        self.assertEqual(calc(0), 1)
        self.assertEqual(calc(30_000_000), 1)
        self.assertEqual(calc(30_000_001), 2)
        self.assertEqual(calc(50_000_000), 2)
        self.assertEqual(calc(50_000_001), 5)
        self.assertEqual(calc(100_000_000), 5)
        self.assertEqual(calc(100_000_001), 8)
        self.assertEqual(calc(150_000_000), 8)
        self.assertEqual(calc(150_000_001), 9)


class SalariesViewTestCase(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username='admin', password='x')
        self.course = Course.objects.create(name='C', price=1000000)
        self.group = Group.objects.create(course=self.course, start_date=date.today(), is_active=True)
        self.op_user = User.objects.create_user(username='+998900000001', password='x', is_staff=True)
        grant_operator_permissions(self.op_user)
        self.operator = Operator.objects.create(user=self.op_user, full_name='Op1', phone_number='+998900000001')
        self.client_obj = Client.objects.create(full_name='C1', phone_number='+998900000002')

    def _tx(self, amount, day, month, year, confirmed=True, refunded=False):
        return _create_transaction_with_clients(
            self.client_obj, operator=self.operator, group=self.group,
            date=date(year, month, day), amount=amount, payment_type='naqd',
            is_confirmed=confirmed, is_refunded=refunded,
        )

    def test_percentage_based_on_amount_excludes_unconfirmed_refunded_and_other_years(self):
        self._tx(40_000_000, 1, 3, 2026, confirmed=True)            # sanaladi
        self._tx(20_000_000, 2, 3, 2026, confirmed=False)           # tasdiqlanmagan -> chetlanadi
        self._tx(999_000_000, 3, 3, 2025, confirmed=True)           # boshqa yil -> chetlanadi
        self._tx(5_000_000, 4, 3, 2026, confirmed=True, refunded=True)  # qaytarilgan -> chetlanadi

        self.client.force_login(self.superuser)
        response = self.client.get('/salaries/', {'month': 3, 'year': 2026})
        self.assertEqual(response.status_code, 200)

        rows = {r['operator'].id: r for r in response.context['rows']}
        row = rows[self.operator.id]
        self.assertEqual(row['total_collected'], Decimal('40000000'))
        self.assertEqual(row['percentage'], 2)  # 30mln dan ortiq, 50mln dan kam
        self.assertAlmostEqual(row['salary'], 800000.0)

        self.assertIn(2025, response.context['available_years'])
        self.assertIn(2026, response.context['available_years'])

    def test_higher_bracket_applies_to_whole_amount(self):
        self._tx(120_000_000, 10, 4, 2026, confirmed=True)

        self.client.force_login(self.superuser)
        response = self.client.get('/salaries/', {'month': 4, 'year': 2026})
        row = {r['operator'].id: r for r in response.context['rows']}[self.operator.id]
        self.assertEqual(row['percentage'], 8)
        self.assertAlmostEqual(row['salary'], 120_000_000 * 0.08)


# ---------------------------------------------------------------------------
# Telegram: bitta umumiy guruh chatiga QR yuborish
# ---------------------------------------------------------------------------

class TelegramSingleChatTestCase(TestCase):
    def setUp(self):
        self.course = Course.objects.create(name='C', price=100000)
        self.group = Group.objects.create(course=self.course, start_date=date.today())
        self.client_obj = Client.objects.create(full_name='A', phone_number='+998901111111')
        self.tx = _create_transaction_with_clients(
            self.client_obj, group=self.group, date=date.today(),
            amount=50000, payment_type='naqd',
        )

    @override_settings(TELEGRAM={'BOT_TOKEN': '', 'CHAT_ID': ''})
    def test_raises_when_token_missing(self):
        from main.services import telegram
        with self.assertRaises(telegram.TelegramNotConfigured):
            telegram.send_payment_qr(self.tx)

    @override_settings(TELEGRAM={'BOT_TOKEN': 'tok', 'CHAT_ID': ''})
    def test_raises_when_chat_id_missing(self):
        from main.services import telegram
        with self.assertRaises(telegram.TelegramNotConfigured):
            telegram.send_payment_qr(self.tx)

    @override_settings(TELEGRAM={'BOT_TOKEN': 'tok', 'CHAT_ID': '-100999'})
    def test_sends_to_configured_chat_id(self):
        from main.services import telegram
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {'ok': True}
        with mock.patch('main.services.telegram.requests.post', return_value=fake_response) as m:
            ok, detail = telegram.send_payment_qr(self.tx)
        self.assertTrue(ok)
        _, kwargs = m.call_args
        self.assertEqual(kwargs['data']['chat_id'], '-100999')

    @override_settings(TELEGRAM={'BOT_TOKEN': 'tok', 'CHAT_ID': '-100999'})
    def test_sends_even_when_transaction_has_no_group(self):
        from main.services import telegram
        tx_no_group = _create_transaction_with_clients(
            self.client_obj, group=None, date=date.today(), amount=1000, payment_type='naqd',
        )
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {'ok': True}
        with mock.patch('main.services.telegram.requests.post', return_value=fake_response):
            ok, detail = telegram.send_payment_qr(tx_no_group)
        self.assertTrue(ok)

    @override_settings(TELEGRAM={'BOT_TOKEN': 'tok', 'CHAT_ID': '-100999'})
    def test_sends_one_qr_per_client(self):
        from main.services import telegram
        client_b = Client.objects.create(full_name='B', phone_number='+998902222222')
        tx = _create_transaction_with_clients(
            [self.client_obj, client_b], group=self.group, date=date.today(), amount=50000, payment_type='naqd',
        )
        fake_response = mock.Mock(status_code=200)
        fake_response.json.return_value = {'ok': True}
        with mock.patch('main.services.telegram.requests.post', return_value=fake_response) as m:
            ok, detail = telegram.send_payment_qr(tx)
        self.assertTrue(ok)
        self.assertEqual(m.call_count, 2)
        chat_ids = {call.kwargs['data']['chat_id'] for call in m.call_args_list}
        self.assertEqual(chat_ids, {'-100999'})

    @override_settings(TELEGRAM={'BOT_TOKEN': 'tok', 'CHAT_ID': '-100999'})
    def test_partial_failure_reports_failing_client(self):
        from main.services import telegram
        client_b = Client.objects.create(full_name='FailClient', phone_number='+998902222222')
        tx = _create_transaction_with_clients(
            [self.client_obj, client_b], group=self.group, date=date.today(), amount=50000, payment_type='naqd',
        )

        ok_response = mock.Mock(status_code=200)
        ok_response.json.return_value = {'ok': True}
        fail_response = mock.Mock(status_code=400)
        fail_response.json.return_value = {'ok': False, 'description': 'blocked'}

        def fake_post(url, data=None, files=None, timeout=None):
            return fail_response if 'FailClient' in data['caption'] else ok_response

        with mock.patch('main.services.telegram.requests.post', side_effect=fake_post):
            ok, detail = telegram.send_payment_qr(tx)
        self.assertFalse(ok)
        self.assertIn('FailClient', detail)


class ExistingDebtCalculationRegressionTestCase(TestCase):
    """Oddiy (bitta mijozli) to'lov yo'li multi-client qo'shilgandan keyin ham buzilmasligini tekshiradi."""

    def test_single_client_transaction_fully_paid_has_zero_debt(self):
        course = Course.objects.create(name='Kurs', price=1000000)
        group = Group.objects.create(course=course, start_date=date.today())
        client = Client.objects.create(full_name='Solo', phone_number='+998909999999')
        t = _create_transaction_with_clients(
            client, group=group, date=date.today(), amount=1000000, payment_type='to_liq_tolov',
        )
        self.assertEqual(t.clients.count(), 1)
        tc = TransactionClient.objects.get(transaction=t, client=client)
        self.assertEqual(tc.debt, 0)


# ---------------------------------------------------------------------------
# Mijoz detail sahifasi, ro'yxat annotatsiyalari va tezkor qo'shish modallari
# ---------------------------------------------------------------------------


class ClientDetailViewTestCase(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        self.admin = ClientAdmin(Client, AdminSite())
        self.superuser = User.objects.create_superuser(username='admin', password='x')
        self.course1 = Course.objects.create(name='C1', price=1000000)
        self.course2 = Course.objects.create(name='C2', price=500000)
        self.group1 = Group.objects.create(course=self.course1, start_date=date.today(), is_active=True)
        self.group2 = Group.objects.create(course=self.course2, start_date=date.today(), is_active=True)
        self.client_obj = Client.objects.create(full_name='Detail Client', phone_number='+998907777777')

    def test_detail_view_shows_stats_and_transactions(self):
        _create_transaction_with_clients(
            self.client_obj, group=self.group1, date=date.today(),
            amount=200000, payment_type='naqd',
        )
        _create_transaction_with_clients(
            self.client_obj, group=self.group2, date=date.today(),
            amount=100000, payment_type='naqd',
        )
        refunded = _create_transaction_with_clients(
            self.client_obj, group=self.group1, date=date.today(),
            amount=50000, payment_type='naqd',
        )
        refunded.is_refunded = True
        refunded.save(update_fields=['is_refunded'])

        request = _request_with_messages(self.superuser)
        response = self.admin.client_detail_view(request, str(self.client_obj.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context_data['joined_groups_count'], 2)
        self.assertEqual(len(response.context_data['transactions']), 3)
        # group1: 1,000,000 - 200,000 = 800,000 qarz; group2: 500,000 - 100,000 = 400,000
        self.assertEqual(response.context_data['loan_amount'], Decimal('1200000'))

    def test_detail_view_missing_client_redirects(self):
        request = _request_with_messages(self.superuser)
        response = self.admin.client_detail_view(request, '999999')
        self.assertEqual(response.status_code, 302)


class ClientListAnnotationTestCase(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        self.admin = ClientAdmin(Client, AdminSite())
        self.superuser = User.objects.create_superuser(username='admin', password='x')
        self.course = Course.objects.create(name='C', price=1000000)
        self.group = Group.objects.create(course=self.course, start_date=date.today(), is_active=True)
        self.client_obj = Client.objects.create(full_name='Annotated Client', phone_number='+998908888888')

    def test_annotated_fields_match_manual_computation(self):
        _create_transaction_with_clients(
            self.client_obj, group=self.group, date=date.today(),
            amount=300000, payment_type='naqd',
        )
        request = _request_with_messages(self.superuser)
        obj = self.admin.get_queryset(request).get(pk=self.client_obj.pk)
        self.assertEqual(obj.joined_groups_count, 1)
        self.assertEqual(obj.loan_amount, Decimal('700000'))


class QuickAddClientTestCase(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        self.admin = ClientAdmin(Client, AdminSite())
        self.superuser = User.objects.create_superuser(username='admin', password='x')
        self.op_user = User.objects.create_user(username='+998909999999', password='x', is_staff=True)
        grant_operator_permissions(self.op_user)
        self.operator = Operator.objects.create(user=self.op_user, full_name='Op1', phone_number='+998909999999')

    def test_creates_client_with_name_and_phone(self):
        request = _post_request_with_messages(
            self.superuser, '/admin/main/client/add-quick/',
            {'client_name': 'Quick Client', 'client_phone': '+998901112233'},
        )
        with mock.patch('main.admin.link_client_to_amocrm', return_value=None):
            response = self.admin.add_client_quick(request)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Client.objects.filter(full_name='Quick Client', phone_number='+998901112233').exists())

    def test_missing_phone_creates_nothing(self):
        request = _post_request_with_messages(
            self.superuser, '/admin/main/client/add-quick/', {'client_name': 'No Phone'},
        )
        response = self.admin.add_client_quick(request)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Client.objects.filter(full_name='No Phone').exists())

    def test_plain_operator_auto_assigned(self):
        request = _post_request_with_messages(
            self.op_user, '/admin/main/client/add-quick/',
            {'client_name': 'Op Client', 'client_phone': '+998904445566'},
        )
        with mock.patch('main.admin.link_client_to_amocrm', return_value=None):
            self.admin.add_client_quick(request)
        client = Client.objects.get(full_name='Op Client')
        self.assertEqual(client.operator, self.operator)
