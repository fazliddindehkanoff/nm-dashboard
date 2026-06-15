from django.urls import path
from .views import kpi_calculator

app_name = 'main'

urlpatterns = [
    path('kpi-calculator/', kpi_calculator, name='kpi_calculator'),
]
