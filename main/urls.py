from django.urls import path
from .views import salaries

app_name = 'main'

urlpatterns = [
    path('salaries/', salaries, name='salaries'),
]
