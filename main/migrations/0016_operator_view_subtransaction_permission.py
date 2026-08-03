"""Mavjud operatorlarga `view_subtransaction` huquqini beradi.

Yangi operatorlar buni `grant_operator_permissions()` orqali oladi, lekin
allaqachon yaratilgan foydalanuvchilar uchun bir martalik yangilash kerak.
"""
from django.db import migrations


def grant_view_subtransaction(apps, schema_editor):
    Operator = apps.get_model('main', 'Operator')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    content_type = ContentType.objects.filter(
        app_label='main', model='subtransaction',
    ).first()
    if content_type is None:
        return
    permission = Permission.objects.filter(
        content_type=content_type, codename='view_subtransaction',
    ).first()
    if permission is None:
        return

    for operator in Operator.objects.exclude(user=None).select_related('user'):
        operator.user.user_permissions.add(permission)


def revoke_view_subtransaction(apps, schema_editor):
    Operator = apps.get_model('main', 'Operator')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    content_type = ContentType.objects.filter(
        app_label='main', model='subtransaction',
    ).first()
    if content_type is None:
        return
    permission = Permission.objects.filter(
        content_type=content_type, codename='view_subtransaction',
    ).first()
    if permission is None:
        return

    for operator in Operator.objects.exclude(user=None).select_related('user'):
        operator.user.user_permissions.remove(permission)


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0015_subtransaction_payment_method_and_more'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(grant_view_subtransaction, revoke_view_subtransaction),
    ]
