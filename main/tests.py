from unittest import mock

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from main.models import Operator, Transaction, Client, Course, Group
from main.admin import OperatorForm, OperatorAdmin, TransactionAdmin, grant_operator_permissions
from main.views import dashboard_callback
from django.utils import timezone
from datetime import date


def _request_with_messages(user):
    """messages freymvorki bilan ishlaydigan soxta so'rov (admin save_model uchun)."""
    request = RequestFactory().get('/')
    request.user = user
    setattr(request, 'session', {})
    setattr(request, '_messages', FallbackStorage(request))
    return request

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
        self.tx_unconfirmed = Transaction.objects.create(
            operator=self.operator,
            client=self.client,
            group=self.group,
            date=date.today(),
            amount=50000,
            payment_type='bron',
            is_confirmed=False
        )
        self.tx_confirmed = Transaction.objects.create(
            operator=self.operator,
            client=self.client,
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
        return Transaction(operator=None, client=client, group=self.group,
                           date=date.today(), amount=50000, payment_type='naqd')

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
            match = self.admin._amocrm_set_source(_request_with_messages(self.super), obj)
        m.assert_not_called()
        self.assertEqual(obj.source, 'amocrm_other')
        self.assertEqual(match.lead_id, 501)
