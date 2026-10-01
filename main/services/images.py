"""Phone-sized copies of group banners for the Telegram web app.

Staff upload camera photos and exports of several megabytes; the web app
shows them at most ~520 CSS pixels wide, so it loads a JPEG copy instead.
"""

import hashlib
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

WEB_WIDTH = 1280  # Twice the widest card, for high-density phone screens.


def web_version(field):
    """Fingerprint of the uploaded file; it changes when the banner is replaced."""
    return hashlib.sha1(f'{field.name}:{field.size}:{WEB_WIDTH}'.encode()).hexdigest()[:12]


def web_banner_url(field):
    """Banner URL that serves the phone-sized copy, or the original if the file is missing."""
    try:
        return f'{field.url}?web={web_version(field)}'
    except OSError:
        return field.url


def web_banner_copy(field):
    """Storage name of the phone-sized JPEG copy, created on first use."""
    name = f'group_banners/web/{web_version(field)}.jpg'
    if default_storage.exists(name):
        return name
    with field.open('rb') as source:
        image = Image.open(source)
        image.draft('RGB', (WEB_WIDTH, WEB_WIDTH))  # JPEGs decode straight at a smaller size.
        image = ImageOps.exif_transpose(image)
        image.thumbnail((WEB_WIDTH, WEB_WIDTH * 3))
        if image.mode != 'RGB':
            rgba = image.convert('RGBA')
            image = Image.new('RGB', rgba.size, 'white')
            image.paste(rgba, mask=rgba.getchannel('A'))
        buffer = BytesIO()
        image.save(buffer, 'JPEG', quality=82, optimize=True, progressive=True)
    return default_storage.save(name, ContentFile(buffer.getvalue()))
