from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Course, Group, Teacher


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False)
class GroupCatalogueTests(TestCase):
    def test_all_active_groups_of_one_course_keep_their_own_details(self):
        course = Course.objects.create(name='Multiple groups', price=1500000, number_of_days=10)
        first = Group.objects.create(course=course, start_date=timezone.localdate() + timedelta(days=1),
                                     number_of_days=7, banner='group_banners/first.jpg')
        second = Group.objects.create(course=course, start_date=timezone.localdate() + timedelta(days=3),
                                      number_of_days=12, banner='group_banners/second.jpg')
        teacher_one = Teacher.objects.create(full_name='First Teacher')
        teacher_two = Teacher.objects.create(full_name='Second Teacher')
        first.teachers.add(teacher_one)
        second.teachers.add(teacher_two)
        Group.objects.create(course=course, start_date=timezone.localdate() + timedelta(days=2), is_active=False)
        response = self.client.get(reverse('main:telegram_app_bootstrap'), HTTP_X_TELEGRAM_DEMO='1')
        self.assertEqual(response.status_code, 200)
        courses = response.json()['courses']
        self.assertEqual(len(courses), 1)
        groups = courses[0]['active_groups']
        self.assertEqual([group['id'] for group in groups], [first.pk, second.pk])
        self.assertEqual([group['number_of_days'] for group in groups], [7, 12])
        self.assertEqual([group['teachers'] for group in groups], [['First Teacher'], ['Second Teacher']])
        self.assertTrue(groups[0]['banner_url'].endswith('/group_banners/first.jpg'))
        self.assertTrue(groups[1]['banner_url'].endswith('/group_banners/second.jpg'))


@override_settings(SECURE_SSL_REDIRECT=False)
class GroupArchiveTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        from .models import Client, AttendanceRecord, AttendanceLesson, Transaction, TransactionClient
        self.admin = User.objects.create_superuser(username='group-admin')
        self.client.force_login(self.admin)
        self.course = Course.objects.create(name='History course', price=1500000)
        self.group = Group.objects.create(course=self.course, start_date=timezone.localdate(), number_of_days=3)
        self.person = Client.objects.create(full_name='Test learner', phone_number='+998901111111')
        self.payment = Transaction.objects.create(group=self.group, date=timezone.localdate(), amount=100000,
                                                  payment_type='bron', is_confirmed=True)
        TransactionClient.objects.create(transaction=self.payment, client=self.person)
        self.record = AttendanceRecord.objects.get(group=self.group, client=self.person)
        self.lesson = AttendanceLesson.objects.create(attendance=self.record, date=timezone.localdate(), marked_by=self.admin)
        self.list_url = reverse('admin:main_group_changelist')
        self.delete_url = reverse('admin:main_group_delete', args=[self.group.pk])

    def assert_history_kept(self):
        self.group.refresh_from_db()
        self.assertFalse(self.group.is_active)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.group_id, self.group.pk)
        self.record.refresh_from_db()
        self.lesson.refresh_from_db()
        self.assertEqual(self.record.group_id, self.group.pk)
        self.assertEqual(self.payment.amount, 100000)

    def test_superuser_delete_page_explains_archive_and_preserves_records(self):
        response = self.client.get(self.delete_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'confirm_archive')
        self.assertNotContains(response, "doesn't have permission")
        self.group.refresh_from_db()
        self.assertTrue(self.group.is_active)
        response = self.client.post(self.delete_url, {'confirm_archive': 'yes'})
        self.assertRedirects(response, self.list_url)
        self.assert_history_kept()

    def test_bulk_delete_of_mixed_groups_requires_explicit_archive_confirmation(self):
        empty = Group.objects.create(course=self.course, start_date=timezone.localdate())
        payload = {'action': 'delete_selected', '_selected_action': [self.group.pk, empty.pk]}
        response = self.client.post(self.list_url, payload)
        self.assertContains(response, 'confirm_archive')
        self.assertEqual(len(response.context['groups']), 2)
        # A stale generic delete confirmation must not silently archive anything.
        self.client.post(self.list_url, {**payload, 'post': 'yes'})
        self.group.refresh_from_db()
        self.assertTrue(self.group.is_active)
        response = self.client.post(self.list_url, {**payload, 'confirm_archive': 'yes'})
        self.assertRedirects(response, self.list_url)
        self.assert_history_kept()
        empty.refresh_from_db()
        self.assertFalse(empty.is_active)

    def test_empty_group_still_supports_normal_delete(self):
        empty = Group.objects.create(course=self.course, start_date=timezone.localdate())
        response = self.client.post(reverse('admin:main_group_delete', args=[empty.pk]), {'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Group.objects.filter(pk=empty.pk).exists())

    def test_archive_action_hides_group_from_catalogue(self):
        response = self.client.post(self.list_url, {'action': 'archive_groups',
            '_selected_action': [self.group.pk], 'confirm_archive': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assert_history_kept()
        with override_settings(DEBUG=True):
            response = self.client.get(reverse('main:telegram_app_bootstrap'), HTTP_X_TELEGRAM_DEMO='1')
        self.assertEqual(response.json()['courses'], [])

    def test_staff_without_change_permission_cannot_archive(self):
        from django.contrib.auth.models import User, Permission
        staff = User.objects.create_user(username='delete-only-staff', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='main', codename='delete_group'), Permission.objects.get(content_type__app_label='main', codename='view_group'))
        self.client.force_login(staff)
        response = self.client.post(reverse('admin:main_group_archive', args=[self.group.pk]), {'confirm_archive': 'yes'})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(self.list_url, {'action': 'delete_selected', '_selected_action': [self.group.pk], 'confirm_archive': 'yes'})
        self.assertEqual(response.status_code, 403)
        self.group.refresh_from_db()
        self.assertTrue(self.group.is_active)

    def test_archive_post_requires_csrf(self):
        from django.test import Client as TestClient
        client = TestClient(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(reverse('admin:main_group_archive', args=[self.group.pk]), {'confirm_archive': 'yes'})
        self.assertEqual(response.status_code, 403)
        self.group.refresh_from_db()
        self.assertTrue(self.group.is_active)
