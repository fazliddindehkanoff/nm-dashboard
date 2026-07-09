from django.core.management.base import BaseCommand
from django.utils import timezone
from main.models import Course, Group, Client, Operator, Discount, Transaction
from django.contrib.auth.models import User, Permission
import random
from datetime import timedelta, datetime


def grant_operator_permissions(user):
    """Operator foydalanuvchisiga to'lovlar bilan ishlash uchun ruxsatlar beradi.
    Tasdiqlash faqat superuser uchun (kod darajasida cheklangan)."""
    codenames = [
        'add_transaction', 'change_transaction', 'view_transaction',
        'view_client', 'view_group', 'view_course', 'view_discount',
    ]
    perms = Permission.objects.filter(codename__in=codenames)
    user.user_permissions.add(*perms)

class Command(BaseCommand):
    help = 'Seeds the database with dummy data for Norboyev Markazi'

    def handle(self, *args, **kwargs):
        Transaction.objects.all().delete()
        Client.objects.all().delete()

        # Create user for operators
        user, _ = User.objects.get_or_create(username='op1', defaults={'is_staff': True})
        user.set_password('123')
        user.save()
        op1, _ = Operator.objects.get_or_create(user=user, full_name='Alisher Usmonov', phone_number='+998901234567')
        grant_operator_permissions(user)

        user2, _ = User.objects.get_or_create(username='op2', defaults={'is_staff': True})
        user2.set_password('123')
        user2.save()
        op2, _ = Operator.objects.get_or_create(user=user2, full_name='Doston Rustamov', phone_number='+998901234568')
        grant_operator_permissions(user2)

        # Courses
        c1, _ = Course.objects.get_or_create(name='Ingliz tili (Beginner)', price=300000)
        c2, _ = Course.objects.get_or_create(name='Matematika', price=350000)
        c3, _ = Course.objects.get_or_create(name='Mental Arifmetika', price=250000)

        # Create Teachers
        from main.models import Teacher
        from datetime import date
        
        teacher_names = [
            "Mirzakarim Norbekov", "Shohruh Norbekov", "Zokirjon Tojiboyev",
            "Shuhrat Suyundiqon", "Axadjon Qo'shoqov", "Nurjahon Mahammadiyorova",
            "Durbek Mirzayorov", "Bobur Fatullayev", "Sarvinoz Musayeva"
        ]
        teachers = []
        for name in teacher_names:
            t, _ = Teacher.objects.get_or_create(full_name=name)
            teachers.append(t)

        # Groups (biri nofaol)
        g1, _ = Group.objects.get_or_create(course=c1, start_date=date.today(), defaults={'is_active': True})
        g1.teachers.set([teachers[0], teachers[1]])
        g2, _ = Group.objects.get_or_create(course=c1, start_date=date.today(), defaults={'is_active': True})
        g2.teachers.add(teachers[2])
        g3, _ = Group.objects.get_or_create(course=c2, start_date=date.today(), defaults={'is_active': True})
        g3.teachers.add(teachers[3])
        g4, _ = Group.objects.get_or_create(course=c3, start_date=date.today(), defaults={'is_active': False})
        g4.teachers.add(teachers[4])

        # Chegirmalar (dinamik) — admin orqali tahrirlanadi
        Discount.objects.get_or_create(
            name="Bron uchun", defaults={'amount': 200000, 'is_booking': True, 'is_active': True}
        )
        d_family, _ = Discount.objects.get_or_create(
            name="Bir oiladan 2 kishi", defaults={'amount': 100000, 'is_booking': False, 'is_active': True}
        )
        d_social, _ = Discount.objects.get_or_create(
            name="Pensioner / Student / Nogironligi bor",
            defaults={'amount': 100000, 'is_booking': False, 'is_active': True},
        )

        # Clients (bir qismi amoCRM dan yuklangan deb belgilanadi)
        clients = []
        for i in range(1, 41):
            cl, _ = Client.objects.get_or_create(
                full_name=f'Mijoz ism/familiya {i}',
                phone_number=f'+9989000000{i:02d}',
            )
            if i % 2 == 0:
                cl.amocrm_id = 100000 + i
                cl.synced_at = timezone.now()
                cl.save()
            clients.append(cl)

        # Transactions
        groups = [g1, g2, g3, g4]
        operators = [op1, op2]
        payment_types = ['bron', 'to_liq_tolov', 'doplata']
        sources = ['amocrm_website', 'amocrm_other', 'not_in_amocrm']
        websites = ['norboyev.uz', 'instagram.com', 'telegram', 'facebook.com']
        additional_discounts = [None, None, None, d_family, d_social]

        start_date = datetime.now() - timedelta(days=150)

        for i in range(250):
            cl = random.choice(clients)
            gr = random.choice(groups)
            op = random.choice(operators)
            ptype = random.choice(payment_types)
            source = random.choice(sources)
            amount = random.choice([50000, 100000, 150000, 200000, 300000])

            # Ensure proper amount logic
            c_price = gr.price if gr.price else gr.course.price
            if ptype == 'to_liq_tolov':
                amount = c_price
            elif ptype == 'bron':
                amount = min(amount, float(c_price) - 10000)

            t_date = start_date + timedelta(days=random.randint(0, 150))

            Transaction.objects.create(
                operator=op,
                client=cl,
                group=gr,
                date=t_date.date(),
                amount=amount,
                payment_type=ptype,
                source=source,
                source_detail=random.choice(websites) if source == 'amocrm_website' else None,
                discount=random.choice(additional_discounts),
                # ~85% tasdiqlangan, qolgani tasdiqlanmagan
                is_confirmed=random.random() < 0.85,
                # ~8% qaytarilgan to'lovlar
                is_refunded=random.random() < 0.08,
            )

        self.stdout.write(self.style.SUCCESS('Muvaffaqiyatli: Dummy data yaratildi!'))
