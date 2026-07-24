from django.urls import path
from .views import salaries, qr_verify

app_name = 'main'

urlpatterns = [
    path('salaries/', salaries, name='salaries'),
    path('qr-verify/', qr_verify, name='qr_verify'),
]
