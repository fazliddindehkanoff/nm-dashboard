import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.test import Client
c = Client()
c.login(username='958446606', password='123') # Assuming password is 123 since I didn't set it, wait I don't know the password!
