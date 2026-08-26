from django.db import migrations


ROLE_PERMISSIONS = {
    'operator': {
        'add_attendancerecord', 'change_attendancerecord', 'view_attendancerecord',
        'add_attendancelesson', 'change_attendancelesson',
        'delete_attendancelesson', 'view_attendancelesson',
    },
    'owner': {'view_attendancerecord', 'view_attendancelesson'},
}


def seed_attendance(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    AttendanceRecord = apps.get_model('main', 'AttendanceRecord')
    RoleConfiguration = apps.get_model('main', 'RoleConfiguration')
    TransactionClient = apps.get_model('main', 'TransactionClient')

    attendance_permissions = []
    for model_name, verbose_name in (
        ('attendancerecord', 'Davomat kartasi'),
        ('attendancelesson', 'Qatnashgan dars'),
    ):
        content_type, _ = ContentType.objects.get_or_create(
            app_label='main', model=model_name,
        )
        for action in ('add', 'change', 'delete', 'view'):
            permission, _ = Permission.objects.get_or_create(
                content_type=content_type,
                codename=f'{action}_{model_name}',
                defaults={'name': f'Can {action} {verbose_name}'},
            )
            attendance_permissions.append(permission)

    admin_role = RoleConfiguration.objects.filter(code='admin').first()
    if admin_role:
        admin_role.permissions.add(*attendance_permissions)

    for role_code, codenames in ROLE_PERMISSIONS.items():
        role = RoleConfiguration.objects.filter(code=role_code).first()
        if role:
            role.permissions.add(
                *Permission.objects.filter(
                    content_type__app_label='main', codename__in=codenames,
                )
            )

    participations = TransactionClient.objects.filter(
        transaction__group__isnull=False,
    ).select_related('transaction__operator')
    for participation in participations.iterator():
        operator = participation.transaction.operator
        AttendanceRecord.objects.get_or_create(
            client_id=participation.client_id,
            group_id=participation.transaction.group_id,
            defaults={
                'created_by_id': operator.user_id if operator else None,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ('main', '0019_attendancerecord_attendancelesson_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_attendance, migrations.RunPython.noop),
    ]
