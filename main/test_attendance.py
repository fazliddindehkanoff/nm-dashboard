from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .models import (
    AttendanceLesson,
    AttendanceRecord,
    Client,
    Course,
    Group,
    Operator,
    RoleConfiguration,
    Transaction,
    TransactionClient,
)
from .admin import AttendanceRecordAdmin


class AttendanceRecordTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='operator-attendance', password='password', is_staff=True,
        )
        self.operator = Operator.objects.create(
            user=self.user,
            full_name='Operator',
            role=RoleConfiguration.ROLE_OPERATOR,
        )
        self.client_obj = Client.objects.create(
            full_name='Davomat Mijoz', phone_number='+998901234567', operator=self.operator,
        )
        self.course = Course.objects.create(name='Davomat kursi', price=Decimal('100000'))
        self.group = Group.objects.create(course=self.course, start_date=date(2026, 8, 1))

    def create_participation(self, amount='50000', confirmed=True):
        transaction = Transaction.objects.create(
            operator=self.operator,
            group=self.group,
            date=date(2026, 8, 1),
            amount=Decimal(amount),
            payment_type='bron',
            is_confirmed=confirmed,
        )
        participation = TransactionClient.objects.create(
            transaction=transaction, client=self.client_obj,
        )
        transaction.save()
        return transaction, participation

    def test_enrollment_automatically_creates_one_permanent_attendance_record(self):
        first_transaction, _ = self.create_participation()
        second_transaction, _ = self.create_participation(amount='25000')

        self.assertEqual(AttendanceRecord.objects.count(), 1)
        record = AttendanceRecord.objects.get()
        self.assertEqual(record.client, self.client_obj)
        self.assertEqual(record.group, self.group)
        self.assertEqual(record.created_by, self.user)

        first_transaction.delete()
        second_transaction.delete()
        self.assertTrue(AttendanceRecord.objects.filter(pk=record.pk).exists())

    def test_lesson_dates_update_last_attended_date_and_count(self):
        self.create_participation()
        record = AttendanceRecord.objects.get()
        AttendanceLesson.objects.create(
            attendance=record, date=date(2026, 8, 4), marked_by=self.user,
        )
        later = AttendanceLesson.objects.create(
            attendance=record, date=date(2026, 8, 11), marked_by=self.user,
        )

        record.refresh_from_db()
        self.assertEqual(record.last_attended_at, date(2026, 8, 11))
        self.assertEqual(record.attended_lessons_count, 2)

        later.delete()
        record.refresh_from_db()
        self.assertEqual(record.last_attended_at, date(2026, 8, 4))

    def test_payment_status_is_derived_from_current_payments(self):
        self.create_participation(amount='50000', confirmed=True)
        record = AttendanceRecord.objects.get()
        self.assertEqual(record.payment_status, AttendanceRecord.PAYMENT_PARTIAL)

        pending_client = Client.objects.create(
            full_name='Pending', phone_number='+998900000002', operator=self.operator,
        )
        pending_transaction = Transaction.objects.create(
            operator=self.operator,
            group=self.group,
            date=date(2026, 8, 2),
            amount=Decimal('100000'),
            payment_type='to_liq_tolov',
            is_confirmed=False,
        )
        TransactionClient.objects.create(
            transaction=pending_transaction, client=pending_client,
        )
        pending_record = AttendanceRecord.objects.get(client=pending_client, group=self.group)
        self.assertEqual(pending_record.payment_status, AttendanceRecord.PAYMENT_PENDING)

    def test_admin_list_uses_annotated_payment_status(self):
        self.create_participation(amount='50000', confirmed=True)
        admin_user = User.objects.create_superuser(username='list-admin')
        request = RequestFactory().get('/admin/main/attendancerecord/')
        request.user = admin_user
        model_admin = AttendanceRecordAdmin(AttendanceRecord, AdminSite())

        record = model_admin.get_queryset(request).get(client=self.client_obj)
        with self.assertNumQueries(0):
            self.assertEqual(record.payment_status, AttendanceRecord.PAYMENT_PARTIAL)

    def test_operator_and_owner_receive_expected_default_permissions(self):
        self.assertTrue(self.user.has_perm('main.view_attendancerecord'))
        self.assertTrue(self.user.has_perm('main.change_attendancerecord'))
        self.assertFalse(self.user.has_perm('main.delete_attendancerecord'))

        owner = User.objects.create_user(username='attendance-owner', is_staff=True)
        Operator.objects.create(
            user=owner, full_name='Owner', role=RoleConfiguration.ROLE_OWNER,
        )
        self.assertTrue(owner.has_perm('main.view_attendancerecord'))
        self.assertFalse(owner.has_perm('main.change_attendancerecord'))

    def test_client_profile_displays_attendance_details(self):
        self.create_participation()
        record = AttendanceRecord.objects.get()
        record.status = AttendanceRecord.STATUS_FIRST_DAY_ONLY
        record.absence_reason = 'Safarga ketgan'
        record.operator_note = 'Kelasi oy qayta qo‘ng‘iroq qilish'
        record.save()
        AttendanceLesson.objects.create(attendance=record, date=date(2026, 8, 1))

        admin = User.objects.create_superuser(username='attendance-admin', password='password')
        self.client.force_login(admin)
        response = self.client.get(
            reverse('admin:main_client_detail', args=[self.client_obj.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Birinchi kun keldi, keyin kelmadi')
        self.assertContains(response, 'Safarga ketgan')
        self.assertContains(response, 'Kelasi oy qayta qo‘ng‘iroq qilish')
        self.assertContains(response, '01.08.2026')

    def test_group_detail_has_three_tabs_and_attendance_recreates_missing_clients(self):
        self.create_participation()
        AttendanceRecord.objects.all().delete()
        admin = User.objects.create_superuser(username='group-tabs-admin', password='password')
        self.client.force_login(admin)
        detail_url = reverse('admin:main_group_detail', args=[self.group.pk])

        payments_response = self.client.get(detail_url)
        self.assertContains(payments_response, "To'lovlar")
        self.assertContains(payments_response, 'Davomat')
        self.assertContains(payments_response, 'Anketa')
        self.assertContains(payments_response, 'Statistika')

        attendance_response = self.client.get(detail_url, {'tab': 'attendance'})
        self.assertEqual(attendance_response.status_code, 200)
        self.assertContains(attendance_response, self.client_obj.full_name)
        self.assertContains(attendance_response, 'data-attendance-status')
        self.assertContains(attendance_response, 'data-attendance-status-shell')
        self.assertContains(attendance_response, 'data-status="active"')
        for _, status_label in AttendanceRecord.STATUSES:
            self.assertContains(attendance_response, status_label)
        self.assertTrue(
            AttendanceRecord.objects.filter(client=self.client_obj, group=self.group).exists()
        )

        anketa_response = self.client.get(detail_url, {'tab': 'anketa'})
        self.assertContains(anketa_response, "Bu bo'lim hozircha bo'sh.")

        statistics_response = self.client.get(detail_url, {'tab': 'statistics'})
        self.assertEqual(statistics_response.status_code, 200)
        self.assertContains(statistics_response, "Statistika bo'limi hozircha bo'sh.")

    def test_group_attendance_status_endpoint_saves_and_validates_status(self):
        self.create_participation()
        record = AttendanceRecord.objects.get(client=self.client_obj, group=self.group)
        admin = User.objects.create_superuser(username='status-admin', password='password')
        self.client.force_login(admin)
        status_url = reverse(
            'admin:main_group_attendance_status', args=[self.group.pk, record.pk]
        )

        for status_value, status_label in AttendanceRecord.STATUSES:
            with self.subTest(status=status_value):
                response = self.client.post(status_url, {'status': status_value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['status'], status_value)
                self.assertEqual(response.json()['label'], str(status_label))
                record.refresh_from_db()
                self.assertEqual(record.status, status_value)

        invalid_response = self.client.post(status_url, {'status': 'unknown'})
        self.assertEqual(invalid_response.status_code, 400)

    def test_owner_cannot_change_group_attendance_status(self):
        self.create_participation()
        record = AttendanceRecord.objects.get(client=self.client_obj, group=self.group)
        owner = User.objects.create_user(
            username='group-attendance-owner', password='password', is_staff=True,
        )
        Operator.objects.create(
            user=owner, full_name='Owner', role=RoleConfiguration.ROLE_OWNER,
        )
        self.client.force_login(owner)

        response = self.client.post(
            reverse('admin:main_group_attendance_status', args=[self.group.pk, record.pk]),
            {'status': AttendanceRecord.STATUS_COMPLETED},
        )
        self.assertEqual(response.status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.status, AttendanceRecord.STATUS_ACTIVE)
