from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Course, Group, Client, Operator, Transaction

@admin.register(Course)
class CourseAdmin(ModelAdmin):
    list_display = ('name', 'price')
    search_fields = ('name',)

@admin.register(Group)
class GroupAdmin(ModelAdmin):
    list_display = ('name', 'course', 'price')
    search_fields = ('name', 'course__name')
    list_filter = ('course',)

@admin.register(Client)
class ClientAdmin(ModelAdmin):
    list_display = ('full_name', 'phone_number')
    search_fields = ('full_name', 'phone_number')

@admin.register(Operator)
class OperatorAdmin(ModelAdmin):
    list_display = ('full_name', 'phone_number', 'user')
    search_fields = ('full_name', 'phone_number')

@admin.register(Transaction)
class TransactionAdmin(ModelAdmin):
    list_display = ('client', 'operator', 'group', 'amount', 'payment_type', 'debt', 'date')
    list_filter = ('payment_type', 'date', 'operator', 'group')
    search_fields = ('client__full_name', 'operator__full_name')
    readonly_fields = ('course_price', 'debt')
    date_hierarchy = 'date'
