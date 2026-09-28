from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .admin import grant_operator_permissions
from .models import Client, Course, Group, Operator, Teacher, Transaction, TransactionClient


class AdminUsabilityTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.course = Course.objects.create(name='Sog‘lomlashtirish', price=Decimal('2000000'), number_of_days=10)
        self.later = Group.objects.create(course=self.course, start_date=today + timedelta(days=9))
        self.sooner = Group.objects.create(course=self.course, start_date=today + timedelta(days=2))
        self.sooner.teachers.add(Teacher.objects.create(full_name='Axadjon Qo‘shoqov'))
        self.superuser = User.objects.create_superuser(username='ux-admin', password='x')
        self.op_user = User.objects.create_user(username='+998901111111', password='x', is_staff=True)
        grant_operator_permissions(self.op_user)
        self.operator = Operator.objects.create(user=self.op_user, full_name='Sotuvchi', phone_number='+998901111111')

    def test_operator_adds_a_payment_from_the_compact_form(self):
        self.client.force_login(self.op_user)
        form = self.client.get(reverse('admin:main_transaction_add'))
        self.assertEqual(form.status_code, 200)
        self.assertNotIn('operator', form.context['adminform'].form.fields)
        self.assertEqual(form.context['adminform'].form.initial['date'], timezone.localdate())
        labels = [label for value, label in form.context['adminform'].form.fields['group'].choices if value]
        self.assertTrue(labels[0].startswith(self.sooner.start_date.strftime('%d.%m.%Y')))
        self.assertIn('Axadjon', labels[0])
        self.assertContains(form, 'Qidirish')  # Uzbek admin wording
        self.assertNotContains(form, 'Save and add another')

        data = {
            'group': str(self.sooner.pk), 'date': timezone.localdate().isoformat(), 'amount': '500000',
            'payment_method': 'naqd', 'payment_type': 'bron', 'discount': '',
            'participants-TOTAL_FORMS': '1', 'participants-INITIAL_FORMS': '0',
            'participants-MIN_NUM_FORMS': '1', 'participants-MAX_NUM_FORMS': '1000',
            'participants-0-client_name': 'Yangi Mijoz', 'participants-0-client_phone': '+998907777777',
            '_save': 'Saqlash',
        }
        with mock.patch('main.admin.link_client_to_amocrm', return_value=None):
            response = self.client.post(reverse('admin:main_transaction_add'), data)
        self.assertEqual(response.status_code, 302)
        payment = Transaction.objects.get()
        self.assertEqual((payment.operator, payment.group, payment.clients.count()), (self.operator, self.sooner, 1))

    def test_dashboard_lists_waiting_payments_and_upcoming_groups(self):
        client = Client.objects.create(full_name='Kutayotgan Mijoz', phone_number='+998908888888')
        payment = Transaction.objects.create(
            group=self.sooner, date=timezone.localdate(), amount=Decimal('1500000'),
            payment_type='bron', operator=self.operator,
        )
        TransactionClient.objects.create(transaction=payment, client=client)
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:index'))
        self.assertEqual(list(response.context['pending_transactions']), [payment])
        self.assertEqual([group.pk for group in response.context['upcoming_groups']], [self.sooner.pk, self.later.pk])
        self.assertEqual(response.context['upcoming_groups'][0].participants, 1)
        self.assertContains(response, 'Kutayotgan Mijoz')
        self.assertContains(response, '1 500 000 UZS')

    def test_payments_page_counts_each_status_tab(self):
        Transaction.objects.create(group=self.sooner, date=timezone.localdate(), amount=100, payment_type='bron')
        Transaction.objects.create(group=self.sooner, date=timezone.localdate(), amount=200, payment_type='bron',
                                   is_confirmed=True)
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('main:payments') + '?status=pending')
        counts = {tab['value']: tab['count'] for tab in response.context['tabs']}
        self.assertEqual((counts[''], counts['pending'], counts['success']), (2, 1, 1))
        self.assertEqual(len(response.context['page']), 1)
