from django.db import migrations


ROLE_PERMISSIONS = {
    'operator': {
        'add_transaction', 'change_transaction', 'view_transaction',
        'add_transactionclient', 'change_transactionclient',
        'view_transactionclient', 'delete_transactionclient',
        'add_client', 'change_client', 'view_client',
        'view_group', 'view_course', 'view_discount', 'view_subtransaction',
        'access_salary_report', 'access_qr_scanner',
    },
    'owner': {
        'access_dashboard', 'access_salary_report', 'access_cashflow',
    },
    'accountant': {
        'access_cashflow', 'add_expense', 'change_expense', 'view_expense',
    },
}


def seed_roles(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    RoleConfiguration = apps.get_model('main', 'RoleConfiguration')
    Operator = apps.get_model('main', 'Operator')

    # Fresh install paytida post_migrate hali ishlamagan bo'ladi. Shuning uchun
    # main ilovasining standart va custom permissionlarini shu yerning o'zida yaratamiz.
    for model in apps.get_app_config('main').get_models():
        content_type, _ = ContentType.objects.get_or_create(
            app_label='main', model=model._meta.model_name,
        )
        for action in ('add', 'change', 'delete', 'view'):
            codename = f'{action}_{model._meta.model_name}'
            Permission.objects.get_or_create(
                content_type=content_type,
                codename=codename,
                defaults={'name': f'Can {action} {model._meta.verbose_name}'},
            )
        for codename, name in model._meta.permissions:
            Permission.objects.get_or_create(
                content_type=content_type,
                codename=codename,
                defaults={'name': name},
            )

    roles = {}
    for code in ('admin', 'operator', 'owner', 'accountant'):
        roles[code], _ = RoleConfiguration.objects.get_or_create(code=code)

    main_permissions = Permission.objects.filter(content_type__app_label='main')
    roles['admin'].permissions.set(main_permissions)
    for code, codenames in ROLE_PERMISSIONS.items():
        roles[code].permissions.set(main_permissions.filter(codename__in=codenames))

    # Eski operatorlar individual permission olgan edi. Endi ular markaziy rol
    # matritsasidan olinadi, aks holda admin rol ruxsatini yopganda ham ochiq qolardi.
    legacy_codenames = ROLE_PERMISSIONS['operator'] - {
        'access_salary_report', 'access_qr_scanner',
    }
    for operator in Operator.objects.exclude(user_id=None):
        operator.user.user_permissions.remove(
            *main_permissions.filter(codename__in=legacy_codenames)
        )


class Migration(migrations.Migration):
    dependencies = [
        ('main', '0017_alter_operator_options_operator_role_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_roles, migrations.RunPython.noop),
    ]
