from django.contrib import admin, messages
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import action
from unfold.enums import ActionVariant
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
    list_display = ('client', 'operator', 'group', 'amount', 'payment_type', 'source', 'is_refunded', 'debt', 'date')
    list_filter = ('is_refunded', 'payment_type', 'source', 'date', 'operator', 'group')
    search_fields = ('client__full_name', 'operator__full_name')
    readonly_fields = ('course_price', 'debt', 'refunded_at')
    date_hierarchy = 'date'

    actions_row = ('refund_transaction',)
    actions_detail = ('refund_transaction_detail',)

    def _refund(self, request, object_id):
        transaction = self.get_object(request, object_id)
        if transaction is None:
            self.message_user(request, _("To'lov topilmadi."), level=messages.ERROR)
            return
        if transaction.is_refunded:
            self.message_user(request, _("Bu to'lov allaqachon qaytarilgan."), level=messages.WARNING)
            return
        transaction.is_refunded = True
        transaction.refunded_at = timezone.now().date()
        transaction.save(update_fields=['is_refunded', 'refunded_at'])
        self.message_user(request, _("To'lov qaytarildi."), level=messages.SUCCESS)

    @action(description=_("Qaytarish"), url_path="refund-row", variant=ActionVariant.DANGER)
    def refund_transaction(self, request, object_id):
        self._refund(request, object_id)
        return redirect(request.META.get('HTTP_REFERER', 'admin:main_transaction_changelist'))

    @action(description=_("Qaytarish"), url_path="refund-detail", variant=ActionVariant.DANGER)
    def refund_transaction_detail(self, request, object_id):
        self._refund(request, object_id)
        return redirect('admin:main_transaction_change', object_id)
