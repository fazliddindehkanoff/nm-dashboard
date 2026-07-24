import re
from django import forms
from django.db import models
from django.contrib import admin, messages
from django.contrib.auth.models import User, Permission
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.widgets import UnfoldAdminTextInputWidget, UnfoldAdminPasswordInput, UnfoldAdminCheckboxSelectMultiple

from .models import Course, Group, Client, Operator, Discount, Transaction, Teacher
from .services.amocrm import (
    sync_contacts,
    link_client_to_amocrm,
    close_lead,
    LeadMatch,
    AmoCRMNotConfigured,
    AmoCRMError,
)
from .services.telegram import send_payment_qr, TelegramNotConfigured


@admin.register(Course)
class CourseAdmin(ModelAdmin):
    list_display = ('name', 'price')
    search_fields = ('name',)


@admin.register(Teacher)
class TeacherAdmin(ModelAdmin):
    list_display = ('full_name',)
    search_fields = ('full_name',)


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = '__all__'

    def clean_teachers(self):
        teachers = self.cleaned_data.get('teachers')
        if teachers and teachers.count() > 2:
            raise forms.ValidationError(_("Bir guruhga eng ko'pi bilan 2 ta o'qituvchi biriktirish mumkin."))
        return teachers


@admin.register(Group)
class GroupAdmin(ModelAdmin):
    form = GroupForm
    list_display = ('group_link', 'course', 'get_teachers', 'start_date', 'active_badge')
    list_display_links = None
    search_fields = ('course__name', 'teachers__full_name')
    list_filter = ('is_active', 'course', 'start_date')
    autocomplete_fields = ('teachers',)

    # ---- Guruh ustiga bosilganda o'zgartirish emas, detail sahifa ochiladi ----
    def get_urls(self):
        urls = super().get_urls()
        info = self.model._meta.app_label, self.model._meta.model_name
        custom = [
            path(
                "<path:object_id>/detail/",
                self.admin_site.admin_view(self.group_detail_view),
                name="%s_%s_detail" % info,
            ),
        ]
        return custom + urls

    @display(description=_("Guruh"))
    def group_link(self, obj):
        url = reverse("admin:main_group_detail", args=[obj.pk])
        return format_html(
            '<a href="{}" class="text-primary-600 dark:text-primary-500 font-medium">{}</a>',
            url,
            str(obj),
        )

    def group_detail_view(self, request, object_id):
        group = self.get_object(request, object_id)
        if group is None:
            self.message_user(request, _("Guruh topilmadi."), level=messages.ERROR)
            return redirect("admin:main_group_changelist")

        transactions = (
            Transaction.objects.filter(group=group)
            .select_related("client", "operator")
            .order_by("-date", "-id")
        )

        context = {
            **self.admin_site.each_context(request),
            "title": str(group),
            "group": group,
            "transactions": transactions,
            "teachers": group.teachers.all(),
            "change_url": reverse("admin:main_group_change", args=[group.pk]),
            "delete_url": reverse("admin:main_group_delete", args=[group.pk]),
            "changelist_url": reverse("admin:main_group_changelist"),
            "has_change_permission": self.has_change_permission(request, group),
            "has_delete_permission": self.has_delete_permission(request, group),
        }
        return TemplateResponse(request, "admin/main/group/detail.html", context)

    @display(description=_("O'qituvchilar"))
    def get_teachers(self, obj):
        return ", ".join([t.full_name for t in obj.teachers.all()])

    @display(description=_("Holati"), label={_("Faol"): "success", _("Nofaol"): "danger"})
    def active_badge(self, obj):
        return _("Faol") if obj.is_active else _("Nofaol")


@admin.register(Client)
class ClientAdmin(ModelAdmin):
    list_display = ('full_name', 'phone_number', 'operator', 'amocrm_badge', 'synced_at')
    search_fields = ('full_name', 'phone_number', 'amocrm_id')
    list_filter = ('synced_at', 'operator')
    readonly_fields = ('amocrm_id', 'synced_at')
    actions_list = ('import_from_amocrm',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and hasattr(request.user, 'operator'):
            return qs.filter(operator=request.user.operator)
        return qs

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if not request.user.is_superuser and hasattr(request.user, 'operator'):
            fields = [f for f in fields if f != 'operator']
        return fields

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and hasattr(request.user, 'operator'):
            obj.operator = request.user.operator
        super().save_model(request, obj, form, change)
        # Faqat yangi mijoz yaratilganda amoCRM'dan lead qidiramiz.
        if not change:
            self._amocrm_link(request, obj)

    def _amocrm_link(self, request, client):
        """Mijozni amoCRM lead bilan bog'laydi. Xatolar saqlashni bloklamaydi."""
        try:
            match = link_client_to_amocrm(client)
        except AmoCRMNotConfigured as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
            return
        except AmoCRMError as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
            return

        if match is None:
            self.message_user(request, _("Mijoz amoCRM'da topilmadi."), level=messages.INFO)
        elif match.contact_conflict:
            self.message_user(
                request,
                _("amoCRM'da topildi, lekin kontakt ID boshqa mijozda — faqat lead bog'landi."),
                level=messages.WARNING,
            )
        else:
            self.message_user(request, _("Mijoz amoCRM'da topildi va bog'landi."), level=messages.SUCCESS)

    @display(description=_("amoCRM"), label={_("amoCRM"): "info", _("Qo'lda"): "warning"})
    def amocrm_badge(self, obj):
        return _("amoCRM") if (obj.amocrm_id or obj.amocrm_lead_id) else _("Qo'lda")

    @action(description=_("amoCRM'dan yuklab olish"), url_path="import-amocrm", icon="cloud_download")
    def import_from_amocrm(self, request):
        try:
            result = sync_contacts()
        except AmoCRMNotConfigured as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
        except Exception as exc:  # amoCRM API xatolari
            self.message_user(request, _("amoCRM xatosi: %s") % exc, level=messages.ERROR)
        else:
            self.message_user(
                request,
                _("amoCRM: %(created)d ta yangi, %(updated)d ta yangilandi.")
                % result,
                level=messages.SUCCESS,
            )
        return redirect('admin:main_client_changelist')


def grant_operator_permissions(user):
    codenames = [
        'add_transaction', 'change_transaction', 'view_transaction',
        'view_client', 'add_client', 'change_client',
        'view_group', 'view_course', 'view_discount',
    ]
    perms = Permission.objects.filter(codename__in=codenames)
    user.user_permissions.add(*perms)


class OperatorForm(forms.ModelForm):
    password = forms.CharField(
        label=_("Parol"),
        widget=UnfoldAdminPasswordInput(render_value=False),
        required=False,
        help_text=_("Yangi foydalanuvchi paroli (yoki mavjud foydalanuvchi parolini yangilash).")
    )

    class Meta:
        model = Operator
        fields = ('full_name', 'phone_number', 'password')

    def clean(self):
        cleaned_data = super().clean()
        phone_number = cleaned_data.get('phone_number')
        password = cleaned_data.get('password')

        if not self.instance.pk or not self.instance.user:
            if not phone_number:
                raise forms.ValidationError(
                    _("Yangi foydalanuvchi yaratish uchun telefon raqami kiritilishi shart.")
                )
            if not password:
                raise forms.ValidationError(
                    _("Yangi foydalanuvchi yaratish uchun parol kiritilishi shart.")
                )
            
            # Sanitize phone_number for username (only digits and +)
            username = re.sub(r'[^\d+]', '', phone_number)
            if User.objects.filter(username=username).exists():
                raise forms.ValidationError(
                    _("Ushbu telefon raqamiga (username: %(username)s) ega foydalanuvchi allaqachon mavjud.") % {'username': username}
                )
        return cleaned_data


@admin.register(Operator)
class OperatorAdmin(ModelAdmin):
    form = OperatorForm
    list_display = ('full_name', 'phone_number', 'user')
    search_fields = ('full_name', 'phone_number')
    fields = ('full_name', 'phone_number', 'password')

    def save_model(self, request, obj, form, change):
        if not obj.user:
            password = form.cleaned_data.get('password')
            if obj.phone_number and password:
                username = re.sub(r'[^\d+]', '', obj.phone_number)
                user = User.objects.create_user(username=username, password=password, is_staff=True)
                grant_operator_permissions(user)
                obj.user = user
        else:
            password = form.cleaned_data.get('password')
            if password:
                obj.user.set_password(password)
                obj.user.save()

        super().save_model(request, obj, form, change)


@admin.register(Discount)
class DiscountAdmin(ModelAdmin):
    list_display = ('name', 'amount', 'kind_badge', 'active_badge')
    list_filter = ('is_active', 'is_booking')
    search_fields = ('name',)

    @display(description=_("Turi"), label={_("Bron (avto)"): "info", _("Qo'shimcha"): "primary"})
    def kind_badge(self, obj):
        return _("Bron (avto)") if obj.is_booking else _("Qo'shimcha")

    @display(description=_("Holati"), label={_("Faol"): "success", _("Nofaol"): "danger"})
    def active_badge(self, obj):
        return _("Faol") if obj.is_active else _("Nofaol")


class TransactionForm(forms.ModelForm):
    client_name = forms.CharField(
        label=_("Mijoz ismi"),
        max_length=255,
        widget=UnfoldAdminTextInputWidget()
    )
    client_phone = forms.CharField(
        label=_("Telefon raqami"),
        max_length=20,
        widget=UnfoldAdminTextInputWidget()
    )

    class Meta:
        model = Transaction
        fields = '__all__'
        exclude = ('client',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and getattr(self.instance, 'client', None):
            self.fields['client_name'].initial = self.instance.client.full_name
            self.fields['client_phone'].initial = self.instance.client.phone_number


@admin.register(Transaction)
class TransactionAdmin(ModelAdmin):
    form = TransactionForm
    list_display = (
        'client', 'operator', 'group', 'amount', 'payment_type',
        'discount_total', 'source', 'confirmed_badge', 'refunded_badge', 'debt', 'date',
    )
    list_filter = ('is_confirmed', 'is_refunded', 'payment_type', 'source', 'operator', 'group')
    search_fields = ('client__full_name', 'operator__full_name')
    readonly_fields = (
        'course_price', 'discount_total', 'debt',
        'is_confirmed', 'confirmed_at', 'confirmed_by',
        'is_refunded', 'refunded_at', 'screenshot_preview',
    )
    date_hierarchy = 'date'
    
    list_before_template = "admin/main/transaction/month_filter_badges.html"

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        
        qs = self.get_queryset(request)
        
        for key, val in request.GET.items():
            if key not in ['date__month', 'date__year', 'date__day', 'date__gte', 'date__lte', 'date__exact', 'date'] and not key.startswith('date__'):
                if '__' in key or key in ['is_confirmed', 'is_refunded', 'payment_type', 'source', 'operator', 'group', 'q']:
                    if key == 'q':
                        q = val
                        if q:
                            from django.db.models import Q
                            qs = qs.filter(Q(client__full_name__icontains=q) | Q(operator__full_name__icontains=q))
                    else:
                        try:
                            qs = qs.filter(**{key: val})
                        except Exception:
                            pass

        from django.db.models import Count
        from django.db.models.functions import ExtractMonth
        monthly_counts = {
            item['month']: item['count']
            for item in qs.annotate(month=ExtractMonth('date')).values('month').annotate(count=Count('id'))
        }

        months = [
            (1, "Yanvar", monthly_counts.get(1, 0)),
            (2, "Fevral", monthly_counts.get(2, 0)),
            (3, "Mart", monthly_counts.get(3, 0)),
            (4, "Aprel", monthly_counts.get(4, 0)),
            (5, "May", monthly_counts.get(5, 0)),
            (6, "Iyun", monthly_counts.get(6, 0)),
            (7, "Iyul", monthly_counts.get(7, 0)),
            (8, "Avgust", monthly_counts.get(8, 0)),
            (9, "Sentabr", monthly_counts.get(9, 0)),
            (10, "Oktabr", monthly_counts.get(10, 0)),
            (11, "Noyabr", monthly_counts.get(11, 0)),
            (12, "Dekabr", monthly_counts.get(12, 0))
        ]

        selected_month = request.GET.get('date__month')

        extra_context.update({
            'months_filter': months,
            'selected_month_filter': int(selected_month) if selected_month else None,
            'query_params': {k: v for k, v in request.GET.items() if k != 'date__month'}
        })

        return super().changelist_view(request, extra_context=extra_context)

    actions_row = ('confirm_transaction', 'refund_transaction')
    actions_detail = ('confirm_transaction_detail', 'refund_transaction_detail')

    fieldsets = (
        (None, {
            'fields': (
                'operator', 'client_name', 'client_phone', 'group', 'date', 'amount', 'payment_type',
                'discount', 'screenshot',
            ),
        }),
        (_("Tasdiqlash / qaytarish"), {
            'fields': ('is_confirmed', 'confirmed_at', 'confirmed_by', 'is_refunded', 'refunded_at'),
        }),
    )

    # ---- Operator roli uchun "Operator" maydonini berkitish ----
    def _is_plain_operator(self, request):
        return not request.user.is_superuser and hasattr(request.user, 'operator')

    def get_exclude(self, request, obj=None):
        if self._is_plain_operator(request):
            return ('operator',)
        return super().get_exclude(request, obj) or ()

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if not self._is_plain_operator(request):
            return fieldsets
        # Operator uchun 'operator' maydonini fieldsetsdan olib tashlaymiz.
        cleaned = []
        for name, opts in fieldsets:
            fields = tuple(f for f in opts.get('fields', ()) if f != 'operator')
            cleaned.append((name, {**opts, 'fields': fields}))
        return cleaned

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if self._is_plain_operator(request):
            return qs.filter(operator=request.user.operator)
        return qs

    def save_model(self, request, obj, form, change):
        # Operator to'lov kiritsa, operator avtomatik o'ziga biriktiriladi.
        if self._is_plain_operator(request):
            obj.operator = request.user.operator

        client_phone = form.cleaned_data.get('client_phone')
        client_name = form.cleaned_data.get('client_name')
        if client_phone and client_name:
            client, created = Client.objects.get_or_create(
                phone_number=client_phone,
                defaults={'full_name': client_name}
            )
            if not created and client.full_name != client_name:
                client.full_name = client_name
                client.save(update_fields=['full_name'])

            if created and self._is_plain_operator(request):
                client.operator = request.user.operator
                client.save(update_fields=['operator'])

            obj.client = client

        # Yangi to'lov uchun amoCRM lead'ni aniqlab, manbani belgilaymiz.
        lead_match = None
        if not change:
            lead_match = self._amocrm_set_source(request, obj)

        super().save_model(request, obj, form, change)

        # To'lov saqlangach lead'ni "Muvaffaqiyatli yakunlandi" ga o'tkazamiz.
        if not change and lead_match and lead_match.lead_id:
            self._amocrm_close_lead(request, lead_match.lead_id)

    def _amocrm_set_source(self, request, obj):
        """Yangi to'lov uchun `source` ni amoCRM natijasidan belgilaydi.

        Mos lead topilsa `LeadMatch` qaytaradi, aks holda None. amoCRM
        muammolari saqlashni bloklamaydi — faqat ogohlantirish ko'rsatiladi.
        """
        client = getattr(obj, 'client', None)
        if client is None:
            obj.source = 'not_in_amocrm'
            return None

        try:
            # Mijoz oldin bog'langan bo'lsa, qayta qidirmaymiz.
            if client.amocrm_lead_id:
                obj.source = 'amocrm_other'
                return LeadMatch(client.amocrm_id, client.amocrm_lead_id, True)
            match = link_client_to_amocrm(client)
        except AmoCRMNotConfigured as exc:
            obj.source = 'not_in_amocrm'
            self.message_user(request, str(exc), level=messages.WARNING)
            return None
        except AmoCRMError as exc:
            obj.source = 'not_in_amocrm'
            self.message_user(request, str(exc), level=messages.WARNING)
            return None

        if match and match.lead_id:
            obj.source = 'amocrm_other'
            if match.contact_conflict:
                self.message_user(
                    request,
                    _("amoCRM kontakt ID boshqa mijozda mavjud — faqat lead bog'landi."),
                    level=messages.WARNING,
                )
            return match

        obj.source = 'not_in_amocrm'
        self.message_user(
            request,
            _("amoCRM'da lead topilmadi — manba \"amoCRM'da yo'q\" deb belgilandi."),
            level=messages.WARNING,
        )
        return None

    def _amocrm_close_lead(self, request, lead_id):
        try:
            close_lead(lead_id)
        except (AmoCRMNotConfigured, AmoCRMError) as exc:
            self.message_user(request, _("amoCRM lead yopilmadi: %s") % exc, level=messages.WARNING)
            return
        self.message_user(
            request,
            _("amoCRM lead 'Muvaffaqiyatli yakunlandi' bosqichiga o'tkazildi."),
            level=messages.SUCCESS,
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Yangi to'lovlarda faqat faol guruhlar ko'rsatiladi.
        if db_field.name == 'group':
            kwargs['queryset'] = Group.objects.filter(is_active=True).select_related('course')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # ---- Ustunlar / ko'rinish ----
    @display(description=_("Chek"))
    def screenshot_preview(self, obj):
        if obj.screenshot:
            return format_html(
                '<a href="{0}" target="_blank">'
                '<img src="{0}" style="max-height:220px;border-radius:8px;border:1px solid #e5e7eb;" />'
                '</a>',
                obj.screenshot.url,
            )
        return _("Chek yuklanmagan")

    @display(description=_("Tasdiq"), label={_("Tasdiqlangan"): "success", _("Kutilmoqda"): "warning"})
    def confirmed_badge(self, obj):
        return _("Tasdiqlangan") if obj.is_confirmed else _("Kutilmoqda")

    @display(description=_("Qaytarilgan"), label={_("Ha"): "danger", _("Yo'q"): "success"})
    def refunded_badge(self, obj):
        return _("Ha") if obj.is_refunded else _("Yo'q")

    # ---- Tasdiqlash (faqat admin) ----
    def has_confirm_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        if obj and obj.is_confirmed and self._is_plain_operator(request):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_confirmed and self._is_plain_operator(request):
            return False
        return super().has_delete_permission(request, obj)

    def _confirm(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            self.message_user(request, _("To'lov topilmadi."), level=messages.ERROR)
            return
        if obj.is_confirmed:
            self.message_user(request, _("Bu to'lov allaqachon tasdiqlangan."), level=messages.WARNING)
            return
        obj.is_confirmed = True
        obj.confirmed_at = timezone.now()
        obj.confirmed_by = request.user
        obj.save(update_fields=['is_confirmed', 'confirmed_at', 'confirmed_by'])
        self.message_user(request, _("To'lov tasdiqlandi."), level=messages.SUCCESS)
        self._notify_telegram(request, obj)

    def _notify_telegram(self, request, transaction):
        """To'lov tasdiqlangach guruh Telegram chatiga QR kod yuboradi.

        Xatolik yuz bersa tasdiqlash bekor qilinmaydi — foydalanuvchiga ogohlantirish
        xabari ko'rsatiladi.
        """
        try:
            ok, detail = send_payment_qr(transaction)
        except TelegramNotConfigured as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
            return
        except Exception as exc:  # tarmoq / Telegram API xatolari
            self.message_user(
                request,
                _("Telegramga yuborishda xatolik: %s") % exc,
                level=messages.WARNING,
            )
            return

        if ok:
            self.message_user(
                request, _("QR kod Telegram guruhiga yuborildi."), level=messages.SUCCESS
            )
        else:
            self.message_user(
                request,
                _("QR kod yuborilmadi: %s") % detail,
                level=messages.WARNING,
            )

    @action(description=_("Tasdiqlash"), url_path="confirm-row", permissions=["confirm"], variant=ActionVariant.SUCCESS)
    def confirm_transaction(self, request, object_id):
        self._confirm(request, object_id)
        return redirect(request.META.get('HTTP_REFERER', 'admin:main_transaction_changelist'))

    @action(description=_("Tasdiqlash"), url_path="confirm-detail", permissions=["confirm"], variant=ActionVariant.SUCCESS)
    def confirm_transaction_detail(self, request, object_id):
        self._confirm(request, object_id)
        return redirect('admin:main_transaction_change', object_id)

    # ---- Qaytarish ----
    def has_refund_permission(self, request, obj=None):
        return request.user.is_superuser

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

    @action(description=_("Qaytarish"), url_path="refund-row", permissions=["refund"], variant=ActionVariant.DANGER)
    def refund_transaction(self, request, object_id):
        self._refund(request, object_id)
        return redirect(request.META.get('HTTP_REFERER', 'admin:main_transaction_changelist'))

    @action(description=_("Qaytarish"), url_path="refund-detail", permissions=["refund"], variant=ActionVariant.DANGER)
    def refund_transaction_detail(self, request, object_id):
        self._refund(request, object_id)
        return redirect('admin:main_transaction_change', object_id)
