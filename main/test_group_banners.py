import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from .models import Course, Group
from .telegram_views import _group_payload


@override_settings(DEBUG=False)
class GroupBannerTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media_root = Path(media.name)
        media_settings = override_settings(MEDIA_ROOT=media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        course = Course.objects.create(name='Intuitsiya', price=Decimal('2000000'), number_of_days=10)
        self.group = Group.objects.create(course=course, start_date=timezone.localdate() + timedelta(days=3))

    def upload(self, size, name='cover.png'):
        buffer = BytesIO()
        Image.new('RGBA', size, (11, 134, 149, 255)).save(buffer, 'PNG')
        self.group.banner.save(name, SimpleUploadedFile(name, buffer.getvalue(), 'image/png'))

    def fetch(self, url):
        response = self.client.get(url)
        body = b''.join(response.streaming_content)
        response.close()
        return response, body

    def test_web_app_loads_a_phone_sized_copy_of_the_cover(self):
        self.upload((3000, 1688))
        url = _group_payload(self.group)['banner_url']
        self.assertTrue(url.startswith(self.group.banner.url + '?web='))

        response, body = self.fetch(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertIn('max-age=604800', response['Cache-Control'])
        copy = Image.open(BytesIO(body))
        self.assertEqual((copy.format, copy.size), ('JPEG', (1280, 720)))

        # The copy is made once and reused.
        self.fetch(url)
        self.assertEqual(len(list((self.media_root / 'group_banners' / 'web').iterdir())), 1)

        # The CRM still serves the original upload.
        response, body = self.fetch(self.group.banner.url)
        self.assertEqual((response['Content-Type'], Image.open(BytesIO(body)).size), ('image/png', (3000, 1688)))

    def test_replacing_the_cover_changes_the_web_app_url(self):
        self.upload((1600, 900), name='first.png')
        first = _group_payload(self.group)['banner_url']
        self.upload((1920, 1080), name='second.png')
        second = _group_payload(self.group)['banner_url']
        self.assertNotEqual(first.split('?web=')[1], second.split('?web=')[1])
        response, body = self.fetch(second)
        self.assertEqual(Image.open(BytesIO(body)).size, (1280, 720))

    def test_unreadable_cover_is_served_as_uploaded(self):
        self.group.banner.save('broken.png', SimpleUploadedFile('broken.png', b'not an image', 'image/png'))
        response, body = self.fetch(_group_payload(self.group)['banner_url'])
        self.assertEqual((response.status_code, body), (200, b'not an image'))

    def test_missing_cover_file_returns_not_found(self):
        self.group.banner = 'group_banners/gone.png'
        self.group.save()
        self.assertEqual(_group_payload(self.group)['banner_url'], self.group.banner.url)
        self.assertEqual(self.client.get(self.group.banner.url + '?web=1').status_code, 404)
