from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import (
    Client,
    Course,
    Expense,
    Group,
    Operator,
    RoleConfiguration,
    Transaction,
    TransactionClient,
)
from .permissions import can_create_payment_links


class RoleAccessTestCase(TestCase):
    def create_profile(self, username, role):
        user = User.objects.create_user(
            username=username, password='test-password', is_staff=True,
        )
        Operator.objects.create(
            user=user, full_name=username, phone_number=username, role=role,
        )
        return user

    def test_five_system_roles_are_seeded(self):
        self.assertSetEqual(
            set(RoleConfiguration.objects.values_list('code', flat=True)),
            {'admin', 'operator', 'owner', 'accountant', 'reception'},
        )

    def test_reception_handles_clients_attendance_and_payment_links_only(self):
        reception = self.create_profile('reception', RoleConfiguration.ROLE_RECEPTION)
        for codename in (
            'view_client', 'add_client', 'change_client', 'view_group', 'view_course',
            'access_qr_scanner', 'view_attendancerecord', 'add_attendancerecord', 'change_attendancerecord',
        ):
            self.assertTrue(reception.has_perm(f'main.{codename}'), codename)
        for codename in (
            'delete_client', 'view_transaction', 'add_transaction', 'access_cashflow',
            'access_dashboard', 'view_expense', 'delete_attendancelesson',
        ):
            self.assertFalse(reception.has_perm(f'main.{codename}'), codename)
        self.assertTrue(can_create_payment_links(reception))

        self.client.force_login(reception)
        self.assertEqual(self.client.get('/admin/main/client/').status_code, 200)
        self.assertEqual(self.client.get('/admin/main/transaction/').status_code, 403)

    def test_owner_gets_reports_without_operational_write_access(self):
        owner = self.create_profile('owner', RoleConfiguration.ROLE_OWNER)
        self.assertTrue(owner.has_perm('main.access_dashboard'))
        self.assertTrue(owner.has_perm('main.access_salary_report'))
        self.assertTrue(owner.has_perm('main.access_cashflow'))
        self.assertFalse(owner.has_perm('main.add_transaction'))
        self.assertFalse(owner.has_perm('main.add_expense'))

    def test_accountant_can_record_expense_and_open_cashflow(self):
        accountant = self.create_profile('accountant', RoleConfiguration.ROLE_ACCOUNTANT)
        self.assertTrue(accountant.has_perm('main.add_expense'))
        self.assertTrue(accountant.has_perm('main.view_expense'))
        self.assertTrue(accountant.has_perm('main.access_cashflow'))
        self.assertFalse(accountant.has_perm('main.add_transaction'))

        self.client.force_login(accountant)
        response = self.client.get(reverse('main:cashflow'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kirim-chiqim va balans')
        self.assertNotContains(response, '>Dashboard<')
        self.assertNotContains(response, ">To'lovlar<")
        self.assertEqual(self.client.get('/admin/main/expense/add/').status_code, 200)

    def test_role_permission_changes_take_effect_for_role_members(self):
        owner = self.create_profile('owner-2', RoleConfiguration.ROLE_OWNER)
        role = RoleConfiguration.objects.get(code=RoleConfiguration.ROLE_OWNER)
        role.permissions.remove(
            role.permissions.get(codename='access_salary_report')
        )

        owner = User.objects.get(pk=owner.pk)
        self.assertFalse(owner.has_perm('main.access_salary_report'))


class CashflowTestCase(TestCase):
    def test_balance_is_confirmed_income_minus_expenses(self):
        accountant = User.objects.create_user(
            username='cashier', password='test-password', is_staff=True,
        )
        Operator.objects.create(
            user=accountant,
            full_name='Kassir',
            role=RoleConfiguration.ROLE_ACCOUNTANT,
        )
        client = Client.objects.create(full_name='Mijoz', phone_number='+998900000000')
        course = Course.objects.create(name='Kurs', price=Decimal('100000'))
        group = Group.objects.create(course=course, start_date=date.today())
        transaction = Transaction.objects.create(
            group=group,
            date=date.today(),
            amount=Decimal('100000'),
            payment_type='to_liq_tolov',
            is_confirmed=True,
        )
        TransactionClient.objects.create(transaction=transaction, client=client)
        Expense.objects.create(
            date=date.today(),
            category=Expense.CATEGORY_RENT,
            amount=Decimal('25000'),
            created_by=accountant,
        )

        self.client.force_login(accountant)
        response = self.client.get(reverse('main:cashflow'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['balance'], Decimal('75000'))
        self.assertContains(response, 'Ayni vaqtdagi kassa balansi')
