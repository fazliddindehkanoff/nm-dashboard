from django.core.management.base import BaseCommand
from main.models import Course, Group, Client, Operator, Transaction
from django.contrib.auth.models import User
import random
from datetime import timedelta, datetime

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
        
        user2, _ = User.objects.get_or_create(username='op2', defaults={'is_staff': True})
        user2.set_password('123')
        user2.save()
        op2, _ = Operator.objects.get_or_create(user=user2, full_name='Doston Rustamov', phone_number='+998901234568')

        # Courses
        c1, _ = Course.objects.get_or_create(name='Ingliz tili (Beginner)', price=300000)
        c2, _ = Course.objects.get_or_create(name='Matematika', price=350000)
        c3, _ = Course.objects.get_or_create(name='Mental Arifmetika', price=250000)
        
        # Groups
        g1, _ = Group.objects.get_or_create(course=c1, name='Beginner A-1', price=300000)
        g2, _ = Group.objects.get_or_create(course=c1, name='Beginner A-2')
        g3, _ = Group.objects.get_or_create(course=c2, name='Matematika 10-sinf', price=400000)
        g4, _ = Group.objects.get_or_create(course=c3, name='Mental 1-guruh')

        # Clients
        clients = []
        for i in range(1, 41):
            cl, _ = Client.objects.get_or_create(full_name=f'Mijoz ism/familiya {i}', phone_number=f'+9989000000{i:02d}')
            clients.append(cl)

        # Transactions
        groups = [g1, g2, g3, g4]
        operators = [op1, op2]
        payment_types = ['bron', 'to_liq_tolov', 'doplata']
        sources = ['crm_existing', 'website', 'phone_self', 'manual', 'bot']
        websites = ['norboyev.uz', 'instagram.com', 'telegram', 'facebook.com']

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
                source_detail=random.choice(websites) if source == 'website' else None,
                # ~8% qaytarilgan to'lovlar
                is_refunded=random.random() < 0.08,
            )
            
        self.stdout.write(self.style.SUCCESS('Muvaffaqiyatli: Dummy data yaratildi!'))
