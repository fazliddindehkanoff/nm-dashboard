import datetime

import django.core.validators
from django.db import migrations, models


def backfill_group_schedule(apps, schema_editor):
    Group = apps.get_model('main', 'Group')
    Transaction = apps.get_model('main', 'Transaction')

    for group in Group.objects.select_related('course').all().iterator():
        update_fields = []
        if group.number_of_days != group.course.number_of_days:
            group.number_of_days = group.course.number_of_days
            update_fields.append('number_of_days')
        if group.start_date is None:
            group.start_date = (
                Transaction.objects.filter(group_id=group.pk)
                .order_by('date')
                .values_list('date', flat=True)
                .first()
                or datetime.date.today()
            )
            update_fields.append('start_date')
        if update_fields:
            group.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0020_seed_attendance_records_and_permissions'),
    ]

    operations = [
        migrations.AddField(
            model_name='course',
            name='number_of_days',
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text='Ushbu kurs odatda necha kun davom etishini kiriting.',
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(365),
                ],
                verbose_name='Dars kunlari soni',
            ),
        ),
        migrations.AddField(
            model_name='group',
            name='number_of_days',
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text="Kursdan avtomatik olinadi; ushbu guruh uchun o'zgartirish mumkin.",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(365),
                ],
                verbose_name='Dars kunlari soni',
            ),
        ),
        migrations.AddField(
            model_name='attendancelesson',
            name='reason',
            field=models.CharField(blank=True, max_length=500, verbose_name='Kelmaslik sababi'),
        ),
        migrations.AddField(
            model_name='attendancelesson',
            name='status',
            field=models.CharField(
                choices=[
                    ('unmarked', 'Belgilanmagan'),
                    ('attended', 'Keldi'),
                    ('absent', 'Kelmadi'),
                    ('excused', 'Sababli kelmadi'),
                    ('late', 'Kechikdi'),
                ],
                default='attended',
                max_length=16,
                verbose_name='Darsdagi holati',
            ),
        ),
        migrations.RunPython(backfill_group_schedule, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='group',
            name='start_date',
            field=models.DateField(verbose_name='Boshlanish sanasi'),
        ),
        migrations.AlterModelOptions(
            name='attendancelesson',
            options={
                'ordering': ('-date', '-id'),
                'verbose_name': 'Dars davomati',
                'verbose_name_plural': 'Dars davomati',
            },
        ),
    ]
