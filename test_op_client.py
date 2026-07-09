import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.test import Client
from django.contrib.auth.models import User

# Make sure we have an operator user with permissions
u, _ = User.objects.get_or_create(username='test_op_2', is_staff=True)
u.set_password('123')
u.save()

from main.models import Operator
Operator.objects.get_or_create(user=u, full_name='Test Op 2', phone_number='+998901112233')

from main.admin import grant_operator_permissions
grant_operator_permissions(u)

c = Client()
c.login(username='test_op_2', password='123')
response = c.get('/admin/main/client/add/')
print("Add client page status:", response.status_code)
from main.models import Client, Operator
op = Operator.objects.get(user__username='test_op_2')
client = Client.objects.create(full_name="Test Edit", phone_number="+1234", operator=op)
resp_edit = c.get(f'/admin/main/client/{client.id}/change/')
print("Edit client page status:", resp_edit.status_code)
post_data = {
    'full_name': 'Test POST Client',
    'phone_number': '+998909998877',
}
resp_post = c.post('/admin/main/client/add/', post_data)
print("Post client page status:", resp_post.status_code)
from main.models import Client
print("Client created?", Client.objects.filter(full_name='Test POST Client').exists())
