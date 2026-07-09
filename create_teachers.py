import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from main.models import Teacher

teacher_names = [
    "Mirzakarim Norbekov", "Shohruh Norbekov", "Zokirjon Tojiboyev",
    "Shuhrat Suyundiqon", "Axadjon Qo'shoqov", "Nurjahon Mahammadiyorova",
    "Durbek Mirzayorov", "Bobur Fatullayev", "Sarvinoz Musayeva"
]

for name in teacher_names:
    Teacher.objects.get_or_create(full_name=name)
    print(f"Teacher created: {name}")

print("All teachers created.")
