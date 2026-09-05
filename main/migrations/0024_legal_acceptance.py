from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0023_telegram_campaigns'),
    ]

    operations = [
        migrations.CreateModel(
            name='LegalAcceptance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document_type', models.CharField(choices=[('terms', 'Foydalanish shartlari'), ('contract', "Sog'lomlashtirish xizmatlari shartnomasi")], max_length=16, verbose_name='Hujjat turi')),
                ('version', models.CharField(max_length=32, verbose_name='Hujjat versiyasi')),
                ('document_hash', models.CharField(editable=False, max_length=64, verbose_name='Qabul qilingan matn SHA-256')),
                ('accepted_at', models.DateTimeField(auto_now_add=True, verbose_name='Qabul qilingan vaqt')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP manzil')),
                ('user_agent', models.CharField(blank=True, max_length=255, verbose_name="Qurilma ma'lumoti")),
                ('purchase', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='legal_acceptances', to='main.miniapppurchase', verbose_name='Xarid')),
                ('telegram_user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='legal_acceptances', to='main.telegramuser', verbose_name='Telegram foydalanuvchi')),
            ],
            options={
                'verbose_name': 'Yuridik rozilik',
                'verbose_name_plural': 'Yuridik roziliklar',
                'ordering': ('-accepted_at', '-id'),
            },
        ),
        migrations.AddConstraint(
            model_name='legalacceptance',
            constraint=models.UniqueConstraint(condition=models.Q(('purchase__isnull', True)), fields=('telegram_user', 'document_type', 'version'), name='uniq_account_legal_version'),
        ),
        migrations.AddConstraint(
            model_name='legalacceptance',
            constraint=models.UniqueConstraint(condition=models.Q(('purchase__isnull', False)), fields=('purchase', 'document_type', 'version'), name='uniq_purchase_legal_version'),
        ),
    ]
