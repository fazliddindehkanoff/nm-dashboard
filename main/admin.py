import re
from decimal import Decimal

from django import forms
from django.db import models, transaction as db_transaction
from django.contrib import admin, messages
from django.contrib.auth.models import User, Permission
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.widgets import (
    UnfoldAdminTextInputWidget,
    UnfoldAdminPasswordInput,
    UnfoldAdminCheckboxSelectMultiple,
    UnfoldAdminSingleDateWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminDecimalFieldWidget,
    UnfoldAdminImageFieldWidget,
)

from .models import (
    Course, Group, Client, Operator, Discount, Transaction, TransactionClient, SubTransaction, Teacher,
    _recalc_transaction_participants, sub_transaction_shares,
)
from .services.amocrm import (
    sync_contacts,
    link_client_to_amocrm,
    close_lead,
    LeadMatch,
    AmoCRMNotConfigured,
    AmoCRMError,
)
from .services.telegram import send_payment_qr, TelegramNotConfigured


def _is_plain_operator(request):
    return not request.user.is_superuser and hasattr(request.user, 'operator')


def _get_or_create_client(request, client_name, client_phone):
    client, created = Client.objects.get_or_create(
        phone_number=client_phone,
        defaults={'full_name': client_name}
    )
    if not created and client.full_name != client_name:
        client.full_name = client_name
        client.save(update_fields=['full_name'])

    if created and _is_plain_operator(request):
        client.operator = request.user.operator
        client.save(update_fields=['operator'])

    return client


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
            '<a href="{}" class="text-base-700 dark:text-base-300 font-medium hover:underline">{}</a>',
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
            .prefetch_related("clients")
            .select_related("operator")
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

    @display(description=_("Holati"), label={_("Faol"): "default", _("Nofaol"): "default"})
    def active_badge(self, obj):
        return _("Faol") if obj.is_active else _("Nofaol")


class QuickAddClientForm(forms.Form):
    client_name = forms.CharField(label=_("Mijoz ismi"), max_length=255)
    client_phone = forms.CharField(label=_("Telefon raqami"), max_length=20)


@admin.register(Client)
class ClientAdmin(ModelAdmin):
    list_display = (
        'client_link', 'phone_number', 'operator', 'joined_groups_count',
        'loan_amount', 'amocrm_badge', 'synced_at',
    )
    list_display_links = None
    search_fields = ('full_name', 'phone_number', 'amocrm_id')
    list_filter = ('synced_at', 'operator')
    readonly_fields = ('amocrm_id', 'synced_at')
    actions_list = ('import_from_amocrm',)
    list_before_template = "admin/main/client/toolbar_extra.html"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if _is_plain_operator(request):
            qs = qs.filter(operator=request.user.operator)
        return qs.annotate(
            joined_groups_count=models.Count(
                'participations__transaction__group',
                filter=models.Q(
                    participations__transaction__is_confirmed=True,
                    participations__transaction__is_refunded=False,
                ),
                distinct=True,
            ),
            loan_amount=models.Sum(
                'participations__debt',
                filter=models.Q(
                    participations__transaction__is_confirmed=True,
                    participations__transaction__is_refunded=False,
                ),
            ),
        )

    @display(description=_("Mijoz"))
    def client_link(self, obj):
        url = reverse("admin:main_client_detail", args=[obj.pk])
        return format_html(
            '<a href="{}" class="text-base-700 dark:text-base-300 font-medium hover:underline">{}</a>',
            url,
            obj.full_name,
        )

    @display(description=_("Guruhlar soni"))
    def joined_groups_count(self, obj):
        return obj.joined_groups_count or 0

    @display(description=_("Qarzi"))
    def loan_amount(self, obj):
        return obj.loan_amount or 0

    # ---- Mijoz detail sahifasi (faqat o'qish uchun) ----
    def get_urls(self):
        urls = super().get_urls()
        info = self.model._meta.app_label, self.model._meta.model_name
        custom = [
            path(
                "<path:object_id>/detail/",
                self.admin_site.admin_view(self.client_detail_view),
                name="%s_%s_detail" % info,
            ),
            path(
                "add-quick/",
                self.admin_site.admin_view(self.add_client_quick),
                name="%s_%s_add_quick" % info,
            ),
        ]
        return custom + urls

    def client_detail_view(self, request, object_id):
        client = self.get_object(request, object_id)
        if client is None:
            self.message_user(request, _("Mijoz topilmadi."), level=messages.ERROR)
            return redirect("admin:main_client_changelist")

        transactions = (
            TransactionClient.objects.filter(client=client)
            .select_related("transaction", "transaction__group", "transaction__group__course", "transaction__operator")
            .order_by("-transaction__date", "-transaction_id")
        )
        confirmed_transactions = transactions.filter(
            transaction__is_confirmed=True,
            transaction__is_refunded=False,
        )
        joined_groups_count = (
            confirmed_transactions
            .values("transaction__group").distinct().count()
        )
        loan_amount = (
            confirmed_transactions.aggregate(total=models.Sum("debt"))["total"] or 0
        )
        total_paid = (
            confirmed_transactions.aggregate(total=models.Sum("share_amount"))["total"] or 0
        )

        context = {
            **self.admin_site.each_context(request),
            "title": client.full_name,
            "client": client,
            "transactions": transactions,
            "joined_groups_count": joined_groups_count,
            "loan_amount": loan_amount,
            "total_paid": total_paid,
            "change_url": reverse("admin:main_client_change", args=[client.pk]),
            "delete_url": reverse("admin:main_client_delete", args=[client.pk]),
            "changelist_url": reverse("admin:main_client_changelist"),
            "has_change_permission": self.has_change_permission(request, client),
            "has_delete_permission": self.has_delete_permission(request, client),
        }
        return TemplateResponse(request, "admin/main/client/detail.html", context)

    # ---- Tezkor mijoz qo'shish (modal) ----
    def add_client_quick(self, request):
        if request.method != 'POST':
            return redirect('admin:main_client_changelist')

        form = QuickAddClientForm(request.POST)
        if not form.is_valid():
            for field, errors in form.errors.items():
                for err in errors:
                    self.message_user(request, f"{field}: {err}", level=messages.ERROR)
            return redirect('admin:main_client_changelist')

        client = Client(
            full_name=form.cleaned_data['client_name'],
            phone_number=form.cleaned_data['client_phone'],
        )
        if _is_plain_operator(request):
            client.operator = request.user.operator
        client.save()
        self._amocrm_link(request, client)

        self.message_user(request, _("Mijoz qo'shildi: %(name)s.") % {'name': client.full_name}, level=messages.SUCCESS)
        return redirect('admin:main_client_changelist')

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if _is_plain_operator(request):
            fields = [f for f in fields if f != 'operator']
        return fields

    def save_model(self, request, obj, form, change):
        if _is_plain_operator(request):
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

    @display(description=_("amoCRM"), label={_("amoCRM"): "default", _("Qo'lda"): "default"})
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
        'add_transactionclient', 'change_transactionclient', 'view_transactionclient', 'delete_transactionclient',
        'view_client', 'add_client', 'change_client',
        'view_group', 'view_course', 'view_discount',
        # Operator o'zi qabul qilgan ichki to'lovning holatini ko'ra olishi kerak.
        # Tasdiqlash/rad etish esa faqat superuser uchun (has_review_permission).
        'view_subtransaction',
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

    @display(description=_("Turi"), label={_("Bron (avto)"): "default", _("Qo'shimcha"): "default"})
    def kind_badge(self, obj):
        return _("Bron (avto)") if obj.is_booking else _("Qo'shimcha")

    @display(description=_("Holati"), label={_("Faol"): "default", _("Nofaol"): "default"})
    def active_badge(self, obj):
        return _("Faol") if obj.is_active else _("Nofaol")


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = '__all__'
        exclude = ('clients',)


class TransactionClientInlineForm(forms.ModelForm):
    """Tranzaksiyaga biriktirilgan bitta mijoz qatori (ism/telefon orqali)."""

    client_name = forms.CharField(label=_("Mijoz ismi"), max_length=255, widget=UnfoldAdminTextInputWidget())
    client_phone = forms.CharField(label=_("Telefon raqami"), max_length=20, widget=UnfoldAdminTextInputWidget())

    class Meta:
        model = TransactionClient
        fields = ('client_name', 'client_phone')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.client_id:
            self.fields['client_name'].initial = self.instance.client.full_name
            self.fields['client_phone'].initial = self.instance.client.phone_number


class TransactionClientInline(TabularInline):
    model = TransactionClient
    form = TransactionClientInlineForm
    fk_name = 'transaction'
    extra = 1
    min_num = 1
    validate_min = True
    can_delete = True
    readonly_fields = ('share_amount', 'share_discount', 'debt')
    fields = ('client_name', 'client_phone', 'share_amount', 'share_discount', 'debt')

    def get_formset(self, request, obj=None, **kwargs):
        BaseFormSet = super().get_formset(request, obj, **kwargs)

        class ClientResolvingFormSet(BaseFormSet):
            def save_new(self, form, commit=True):
                form.instance.client = _get_or_create_client(
                    request, form.cleaned_data['client_name'], form.cleaned_data['client_phone']
                )
                return super().save_new(form, commit=commit)

            def save_existing(self, form, instance, commit=True):
                instance.client = _get_or_create_client(
                    request, form.cleaned_data['client_name'], form.cleaned_data['client_phone']
                )
                return super().save_existing(form, instance, commit=commit)

        return ClientResolvingFormSet


def _group_debt_for_clients(transaction, client_ids):
    """Berilgan mijozlarning shu guruhdagi tasdiqlangan qarzi."""
    if not transaction.group_id or not client_ids:
        return Decimal(0)
    return (
        TransactionClient.objects.filter(
            client_id__in=client_ids,
            transaction__group_id=transaction.group_id,
            transaction__is_confirmed=True,
            transaction__is_refunded=False,
        ).aggregate(total=models.Sum('debt'))['total']
        or Decimal(0)
    )


def _sub_transaction_block_reason(sub_transaction):
    """Ichki to'lovni tasdiqlab bo'lmasligining sababi (yoki None)."""
    transaction = sub_transaction.transaction
    if sub_transaction.status != SubTransaction.STATUS_PENDING:
        return _("Bu ichki to'lov allaqachon ko'rib chiqilgan.")
    if transaction.is_refunded:
        return _("Qaytarilgan to'lovning ichki to'lovini tasdiqlab bo'lmaydi.")
    if not transaction.is_confirmed:
        return _("Avval asosiy to'lovni tasdiqlang.")
    if sub_transaction.receipt_required and not sub_transaction.screenshot:
        return _("Bu to'lov turida chek majburiy, lekin yuklanmagan.")

    client_ids = list(sub_transaction.clients.values_list('pk', flat=True))
    if not client_ids:
        return _("Ichki to'lovga mijoz biriktirilmagan.")

    # Tasdiqlash paytida faqat haqiqiy (tasdiqlangan to'lovlardan keyingi) qarz
    # bilan solishtiriladi. Boshqa kutayotgan yozuvlar hali pul emas — ular
    # navbat bilan tasdiqlanadi va qarz har safar qayta hisoblanadi, shuning
    # uchun ortiqchasi o'z navbatida shu yerda bloklanadi.
    debt = _group_debt_for_clients(transaction, client_ids)
    if sub_transaction.amount > debt:
        return _("Summa mijozlarning joriy qarzidan oshib ketdi (mumkin: %(available)s UZS).") % {
            'available': debt,
        }
    return None


def _review_sub_transaction(sub_transaction, user, approve, note=''):
    """Ichki to'lovni tasdiqlaydi yoki rad etadi. `(ok, xabar)` qaytaradi.

    Tasdiqlangandagina pul asosiy to'lov summasiga qo'shiladi va mijozlarning
    qarzi qayta hisoblanadi.
    """
    with db_transaction.atomic():
        locked = (
            SubTransaction.objects.select_for_update()
            .select_related('transaction')
            .get(pk=sub_transaction.pk)
        )
        if locked.status != SubTransaction.STATUS_PENDING:
            return False, _("Bu ichki to'lov allaqachon ko'rib chiqilgan.")

        if approve:
            block_reason = _sub_transaction_block_reason(locked)
            if block_reason:
                return False, block_reason

        locked.status = (
            SubTransaction.STATUS_APPROVED if approve else SubTransaction.STATUS_REJECTED
        )
        locked.reviewed_at = timezone.now()
        locked.reviewed_by = user
        locked.review_note = note or ''
        locked.save(update_fields=['status', 'reviewed_at', 'reviewed_by', 'review_note'])

        if approve:
            transaction = locked.transaction
            # Eski prefetch cache yangi statusni bilmaydi — qayta hisoblashdan
            # oldin tozalanadi.
            getattr(transaction, '_prefetched_objects_cache', {}).pop('sub_transactions', None)
            transaction.amount = (transaction.amount or Decimal(0)) + locked.amount
            transaction.save()

    if approve:
        return True, _("Ichki to'lov tasdiqlandi va mijoz qarzidan ayirildi.")
    return True, _("Ichki to'lov rad etildi.")


class SubTransactionReviewForm(forms.Form):
    note = forms.CharField(
        label=_("Izoh"),
        required=False,
        max_length=255,
        widget=UnfoldAdminTextInputWidget(),
        help_text=_("Ixtiyoriy. Rad etish sababini yozib qoldirish tavsiya etiladi."),
    )


class ReceivePaymentForm(forms.Form):
    clients = forms.ModelMultipleChoiceField(
        label=_("Mijozlar"),
        queryset=Client.objects.none(),
        widget=UnfoldAdminCheckboxSelectMultiple(),
        help_text=_("Qo'shimcha to'lov qaysi mijozlar uchun qabul qilinayotganini tanlang."),
    )
    payment_method = forms.ChoiceField(
        label=_("To'lov turi"),
        choices=SubTransaction.METHODS,
        initial=SubTransaction.METHOD_CASH,
        widget=UnfoldAdminSelectWidget(attrs={'data-receipt-toggle': 'true'}),
        help_text=_("Naqd pulda chek shart emas, qolgan turlarda chek majburiy."),
    )
    amount = forms.DecimalField(
        label=_("Qo'shiladigan summa"),
        max_digits=12,
        decimal_places=2,
        min_value=Decimal('0.01'),
        widget=UnfoldAdminDecimalFieldWidget(),
    )
    screenshot = forms.ImageField(
        label=_("Chek / skrinshot"),
        required=False,
        widget=UnfoldAdminImageFieldWidget(),
        help_text=_("Naqd pul bo'lmasa, to'lov chekining rasmi majburiy."),
    )

    def __init__(self, *args, clients=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['clients'].queryset = clients if clients is not None else Client.objects.none()

    def clean(self):
        cleaned_data = super().clean()
        payment_method = cleaned_data.get('payment_method')
        # Chek majburiyligini to'lov turi belgilaydi (naqd pul — istisno).
        if (
            payment_method
            and SubTransaction.receipt_required_for(payment_method)
            and not cleaned_data.get('screenshot')
        ):
            self.add_error(
                'screenshot',
                _("\"%(method)s\" to'lov turida chek yuklash majburiy.")
                % {'method': dict(SubTransaction.METHODS).get(payment_method, payment_method)},
            )
        return cleaned_data


@admin.register(Transaction)
class TransactionAdmin(ModelAdmin):
    form = TransactionForm
    inlines = (TransactionClientInline,)
    list_display = (
        'transaction_link', 'operator', 'group', 'amount', 'payment_type',
        'discount_total', 'source', 'confirmed_badge', 'refunded_badge', 'total_debt_display', 'date',
    )
    list_display_links = None
    list_filter = ('is_confirmed', 'is_refunded', 'payment_type', 'source', 'operator', 'group')
    search_fields = ('clients__full_name', 'operator__full_name')
    readonly_fields = (
        'course_price', 'discount_total',
        'is_confirmed', 'confirmed_at', 'confirmed_by',
        'is_refunded', 'refunded_at', 'screenshot_preview',
    )
    date_hierarchy = 'date'

    list_before_template = "admin/main/transaction/month_filter_badges.html"
    change_form_outer_after_template = "admin/main/transaction/subtransactions_table.html"

    # To'lov satri faqat o'qiladigan detail sahifani ochadi. Asl Django
    # tahrirlash formasi detail sahifadagi alohida tugma orqali ochiladi.
    def get_urls(self):
        urls = super().get_urls()
        info = self.model._meta.app_label, self.model._meta.model_name
        custom = [
            path(
                "<path:object_id>/detail/",
                self.admin_site.admin_view(self.transaction_detail_view),
                name="%s_%s_detail" % info,
            ),
        ]
        return custom + urls

    def transaction_detail_view(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            self.message_user(request, _("To'lov topilmadi."), level=messages.ERROR)
            return redirect("admin:main_transaction_changelist")

        summary = self._receive_payment_summary(obj)
        context = {
            **self.admin_site.each_context(request),
            **summary,
            "title": _("To'lov #%(id)s") % {"id": obj.pk},
            "transaction": obj,
            "original": obj,
            "change_url": reverse("admin:main_transaction_change", args=[obj.pk]),
            "changelist_url": reverse("admin:main_transaction_changelist"),
            "confirm_url": reverse(
                "admin:main_transaction_confirm_transaction_detail", args=[obj.pk]
            ),
            "refund_url": reverse(
                "admin:main_transaction_refund_transaction_detail", args=[obj.pk]
            ),
            "receive_payment_url": reverse(
                "admin:main_transaction_receive_payment_detail", args=[obj.pk]
            ),
            "has_change_permission": self.has_change_permission(request, obj),
            "can_confirm": (
                self.has_confirm_permission(request, obj)
                and not obj.is_confirmed
                and not obj.is_refunded
            ),
            "can_refund": self.has_refund_permission(request, obj) and not obj.is_refunded,
            "can_receive_payment": (
                self.has_receive_payment_permission(request, obj)
                and summary["can_receive_payment"]
            ),
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/main/transaction/detail.html", context)

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
                            qs = qs.filter(Q(clients__full_name__icontains=q) | Q(operator__full_name__icontains=q)).distinct()
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
            'query_params': {k: v for k, v in request.GET.items() if k != 'date__month'},
        })

        return super().changelist_view(request, extra_context=extra_context)

    actions_row = ('confirm_transaction', 'refund_transaction')
    actions_detail = ('confirm_transaction_detail', 'refund_transaction_detail', 'receive_payment_detail')

    fieldsets = (
        (None, {
            'fields': (
                'operator', 'group', 'date', 'amount', 'payment_type',
                'discount', 'screenshot',
            ),
        }),
        (_("Tasdiqlash / qaytarish"), {
            'fields': ('is_confirmed', 'confirmed_at', 'confirmed_by', 'is_refunded', 'refunded_at'),
        }),
    )

    # ---- Operator roli uchun "Operator" maydonini berkitish ----
    def get_exclude(self, request, obj=None):
        if _is_plain_operator(request):
            return ('operator',)
        return super().get_exclude(request, obj) or ()

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if not _is_plain_operator(request):
            return fieldsets
        # Operator uchun 'operator' maydonini fieldsetsdan olib tashlaymiz.
        cleaned = []
        for name, opts in fieldsets:
            fields = tuple(f for f in opts.get('fields', ()) if f != 'operator')
            cleaned.append((name, {**opts, 'fields': fields}))
        return cleaned

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if _is_plain_operator(request):
            qs = qs.filter(operator=request.user.operator)
        return qs.prefetch_related(
            'participants__client',
            'sub_transactions__clients',
            'sub_transactions__received_by',
            'sub_transactions__reviewed_by',
        ).annotate(total_debt=models.Sum('participants__debt'))

    def save_model(self, request, obj, form, change):
        # Operator to'lov kiritsa, operator avtomatik o'ziga biriktiriladi.
        if _is_plain_operator(request):
            obj.operator = request.user.operator
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        # Ota obyekt + inline'dagi TransactionClient qatorlari saqlangach,
        # ulushlarni/qarzni qayta hisoblaymiz va (yangi to'lov bo'lsa) amoCRM
        # manbasini belgilaymiz.
        super().save_related(request, form, formsets, change)
        obj = form.instance
        _recalc_transaction_participants(obj)

        if not change:
            lead_matches = self._amocrm_set_source(request, obj)
            Transaction.objects.filter(pk=obj.pk).update(source=obj.source)
            for match in lead_matches:
                if match and match.lead_id:
                    self._amocrm_close_lead(request, match.lead_id)

    def _amocrm_set_source(self, request, obj):
        """To'lovga biriktirilgan mijozlar bo'yicha `source` ni belgilaydi.

        Kamida bitta mijozning mos lead'i topilsa 'amocrm_other', aks holda
        'not_in_amocrm'. amoCRM muammolari saqlashni bloklamaydi — faqat
        ogohlantirish ko'rsatiladi. Har bir mijoz uchun topilgan moslikni
        ro'yxat qilib qaytaradi (chaqiruvchi har birining lead'ini yopishi
        uchun).
        """
        clients = list(obj.clients.all())
        if not clients:
            obj.source = 'not_in_amocrm'
            return []

        matches = []
        for client in clients:
            try:
                if client.amocrm_lead_id:
                    matches.append(LeadMatch(client.amocrm_id, client.amocrm_lead_id, True))
                    continue
                match = link_client_to_amocrm(client)
            except AmoCRMNotConfigured as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
                matches.append(None)
                continue
            except AmoCRMError as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
                matches.append(None)
                continue

            if match and match.contact_conflict:
                self.message_user(
                    request,
                    _("amoCRM kontakt ID boshqa mijozda mavjud — faqat lead bog'landi."),
                    level=messages.WARNING,
                )
            matches.append(match)

        if any(m and m.lead_id for m in matches):
            obj.source = 'amocrm_other'
        else:
            obj.source = 'not_in_amocrm'
            self.message_user(
                request,
                _("amoCRM'da lead topilmadi — manba \"amoCRM'da yo'q\" deb belgilandi."),
                level=messages.WARNING,
            )
        return matches

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

    # ---- Pul qabul qilish (admin va operatorlar, mavjud to'lovga summa qo'shadi) ----
    def has_receive_payment_permission(self, request, obj=None):
        return request.user.is_superuser or _is_plain_operator(request)

    def _current_group_debt_for_participants(self, obj, participants):
        if not obj.group_id:
            return Decimal(0)
        total = Decimal(0)
        for participant in participants:
            total += (
                TransactionClient.objects.filter(
                    client_id=participant.client_id,
                    transaction__group_id=obj.group_id,
                    transaction__is_confirmed=True,
                    transaction__is_refunded=False,
                ).aggregate(total=models.Sum('debt'))['total']
                or Decimal(0)
            )
        return total

    def _pending_shares(self, obj):
        """Tasdiq kutayotgan ichki to'lovlarning mijozlar bo'yicha ulushi.

        Bu pul hali qarzni kamaytirmagan, lekin bir xil summani ikki marta
        qabul qilib yubormaslik uchun yangi qabulda band deb hisoblanadi.
        """
        return sub_transaction_shares(obj.pending_sub_transactions)

    def _available_for_participants(self, obj, participants, pending_shares=None):
        """Tanlangan mijozlardan hali qabul qilinishi mumkin bo'lgan summa."""
        pending_shares = self._pending_shares(obj) if pending_shares is None else pending_shares
        debt = self._current_group_debt_for_participants(obj, participants)
        reserved = sum(
            (pending_shares.get(participant.client_id, Decimal(0)) for participant in participants),
            Decimal(0),
        )
        return max(debt - reserved, Decimal(0))

    def _receive_payment_block_reason(self, obj, available):
        if obj.is_refunded:
            return _("Qaytarilgan to'lovga qo'shimcha pul qabul qilib bo'lmaydi.")
        if not obj.is_confirmed:
            return _("Avval to'lovni tasdiqlang, keyin qo'shimcha pul qabul qiling.")
        if available <= 0:
            return _("Bu mijoz/guruh bo'yicha qarz yopilgan yoki tasdiq kutayotgan to'lovlar bilan band.")
        return None

    def _receive_payment_summary(self, obj):
        participants = list(obj.participants.select_related('client').order_by('id'))
        pending_shares = self._pending_shares(obj)
        current_debt = self._current_group_debt_for_participants(obj, participants)
        available = self._available_for_participants(obj, participants, pending_shares)
        for participant in participants:
            participant.current_group_debt = self._current_group_debt_for_participants(obj, [participant])
            participant.pending_amount = pending_shares.get(participant.client_id, Decimal(0))
            participant.available_amount = max(
                participant.current_group_debt - participant.pending_amount, Decimal(0)
            )
        block_reason = self._receive_payment_block_reason(obj, available)
        return {
            'participants': participants,
            'clients_count': len(participants),
            'current_debt': current_debt,
            'available_amount': available,
            'pending_sub_total': obj.pending_sub_total,
            'pending_sub_count': obj.pending_sub_count,
            'course_price': obj.course_price,
            'discount_total': obj.discount_total,
            'initial_amount': obj.initial_amount,
            'receive_payment_block_reason': block_reason,
            'can_receive_payment': block_reason is None,
        }

    def _receive_payment_form(self, obj, data=None, files=None):
        clients = obj.clients.order_by('full_name', 'id')
        initial = {'clients': list(clients.values_list('pk', flat=True))} if data is None else None
        return ReceivePaymentForm(data=data, files=files, clients=clients, initial=initial)

    def _render_receive_payment(self, request, obj, form, summary=None):
        summary = summary or self._receive_payment_summary(obj)
        context = {
            **self.admin_site.each_context(request),
            **summary,
            'title': _("Pul qabul qilish"),
            'object': obj,
            'form': form,
            'change_url': reverse('admin:main_transaction_detail', args=[obj.pk]),
            'opts': self.model._meta,
        }
        return TemplateResponse(request, "admin/main/transaction/receive_payment.html", context)

    @action(
        description=_("Pul qabul qilish"), url_path="receive-payment",
        permissions=["receive_payment"], variant=ActionVariant.DEFAULT,
    )
    def receive_payment_detail(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            self.message_user(request, _("To'lov topilmadi."), level=messages.ERROR)
            return redirect('admin:main_transaction_changelist')

        summary = self._receive_payment_summary(obj)
        if request.method != 'POST':
            form = self._receive_payment_form(obj)
            return self._render_receive_payment(request, obj, form, summary=summary)

        form = self._receive_payment_form(obj, data=request.POST, files=request.FILES)
        if not form.is_valid():
            return self._render_receive_payment(request, obj, form, summary=summary)

        if summary['receive_payment_block_reason']:
            form.add_error(None, summary['receive_payment_block_reason'])
            return self._render_receive_payment(request, obj, form, summary=summary)

        amount = form.cleaned_data['amount']
        selected_clients = list(form.cleaned_data['clients'])

        with db_transaction.atomic():
            obj = self.get_queryset(request).select_for_update().get(pk=obj.pk)
            locked_summary = self._receive_payment_summary(obj)
            selected_ids = {client.pk for client in selected_clients}
            selected_participants = [
                participant for participant in locked_summary['participants']
                if participant.client_id in selected_ids
            ]
            available = self._available_for_participants(obj, selected_participants)
            if amount > available:
                form.add_error(
                    'amount',
                    _("Qo'shiladigan summa tanlangan mijozlarning qolgan qarzidan oshmasligi kerak "
                      "(%(available)s UZS — tasdiq kutayotgan to'lovlar hisobga olingan).")
                    % {'available': available},
                )
                return self._render_receive_payment(request, obj, form, summary=locked_summary)

            # Ichki to'lov tasdiq kutish holatida yaratiladi — admin tasdiqlamaguncha
            # `transaction.amount` va mijoz qarzi o'zgarmaydi.
            sub_transaction = SubTransaction.objects.create(
                transaction=obj,
                amount=amount,
                payment_method=form.cleaned_data['payment_method'],
                screenshot=form.cleaned_data.get('screenshot'),
                received_by=request.user,
                status=SubTransaction.STATUS_PENDING,
            )
            sub_transaction.clients.set(selected_clients)

        self.message_user(
            request,
            _("Ichki to'lov qabul qilindi va admin tasdig'iga yuborildi."),
            level=messages.SUCCESS,
        )
        return redirect('admin:main_transaction_detail', object_id)

    # ---- Ustunlar / ko'rinish ----
    @display(description=_("Mijozlar"))
    def transaction_link(self, obj):
        url = reverse("admin:main_transaction_detail", args=[obj.pk])
        names = [client.full_name for client in obj.clients.all()]
        return format_html(
            '<a href="{}" class="text-base-700 dark:text-base-300 font-medium hover:underline">{}</a>',
            url,
            ", ".join(names) if names else _("To'lov #%(id)s") % {"id": obj.pk},
        )

    @display(description=_("Mijozlar"))
    def clients_display(self, obj):
        names = [c.full_name for c in obj.clients.all()]
        return ", ".join(names) if names else "—"

    @display(description=_("Qarzi"))
    def total_debt_display(self, obj):
        return obj.total_debt or 0

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

    @display(description=_("Tasdiq"), label={_("Tasdiqlangan"): "default", _("Kutilmoqda"): "default"})
    def confirmed_badge(self, obj):
        return _("Tasdiqlangan") if obj.is_confirmed else _("Kutilmoqda")

    @display(description=_("Qaytarilgan"), label={_("Ha"): "default", _("Yo'q"): "default"})
    def refunded_badge(self, obj):
        return _("Ha") if obj.is_refunded else _("Yo'q")

    # ---- Tasdiqlash (faqat admin) ----
    def has_confirm_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        if obj and obj.is_confirmed and _is_plain_operator(request):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_confirmed and _is_plain_operator(request):
            return False
        return super().has_delete_permission(request, obj)

    def _confirm(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            self.message_user(request, _("To'lov topilmadi."), level=messages.ERROR)
            return
        if obj.is_refunded:
            self.message_user(request, _("Qaytarilgan to'lovni tasdiqlab bo'lmaydi."), level=messages.WARNING)
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

    def _safe_redirect_url(self, request, url, fallback):
        if url and url_has_allowed_host_and_scheme(
            url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return url
        return fallback

    def _confirmation_cancel_url(self, request, object_id, detail=False):
        fallback = (
            reverse('admin:main_transaction_detail', args=[object_id])
            if detail else reverse('admin:main_transaction_changelist')
        )
        return self._safe_redirect_url(request, request.META.get('HTTP_REFERER'), fallback)

    def _render_transaction_confirmation(
        self, request, obj, template_name, title, action_label, cancel_url, warning=None
    ):
        context = {
            **self.admin_site.each_context(request),
            'title': title,
            'object': obj,
            'transaction': obj,
            'participants': obj.participants.select_related('client').order_by('id'),
            'action_label': action_label,
            'cancel_url': cancel_url,
            'next_url': cancel_url,
            'post_url': request.path,
            'warning': warning,
            'change_url': reverse('admin:main_transaction_change', args=[obj.pk]),
            'opts': self.model._meta,
        }
        return TemplateResponse(request, template_name, context)

    def _confirm_response(self, request, object_id, detail=False):
        obj = self.get_object(request, object_id)
        cancel_url = self._confirmation_cancel_url(request, object_id, detail=detail)
        if obj is None:
            self.message_user(request, _("To'lov topilmadi."), level=messages.ERROR)
            return redirect('admin:main_transaction_changelist')
        if request.method != 'POST':
            if obj.is_confirmed:
                self.message_user(
                    request,
                    _("Bu to'lov allaqachon tasdiqlangan."),
                    level=messages.WARNING,
                )
                return redirect(cancel_url)
            return self._render_transaction_confirmation(
                request,
                obj,
                "admin/main/transaction/confirm_confirm.html",
                _("To'lovni tasdiqlash"),
                _("Tasdiqlash"),
                cancel_url,
                warning=_(
                    "Tasdiqlashdan keyin Telegram guruhiga mijoz QR kodi yuboriladi."
                ),
            )
        self._confirm(request, object_id)
        return redirect(self._safe_redirect_url(request, request.POST.get('next'), cancel_url))

    @action(
        description=_("Tasdiqlash"),
        url_path="confirm-row",
        permissions=["confirm"],
        variant=ActionVariant.DEFAULT,
    )
    def confirm_transaction(self, request, object_id):
        return self._confirm_response(request, object_id, detail=False)

    @action(
        description=_("Tasdiqlash"),
        url_path="confirm-detail",
        permissions=["confirm"],
        variant=ActionVariant.DEFAULT,
    )
    def confirm_transaction_detail(self, request, object_id):
        return self._confirm_response(request, object_id, detail=True)

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

    def _refund_response(self, request, object_id, detail=False):
        transaction = self.get_object(request, object_id)
        cancel_url = self._confirmation_cancel_url(request, object_id, detail=detail)
        if transaction is None:
            self.message_user(request, _("To'lov topilmadi."), level=messages.ERROR)
            return redirect('admin:main_transaction_changelist')
        if request.method != 'POST':
            if transaction.is_refunded:
                self.message_user(
                    request,
                    _("Bu to'lov allaqachon qaytarilgan."),
                    level=messages.WARNING,
                )
                return redirect(cancel_url)
            return self._render_transaction_confirmation(
                request,
                transaction,
                "admin/main/transaction/refund_confirm.html",
                _("To'lovni qaytarish"),
                _("Qaytarish"),
                cancel_url,
                warning=_(
                    "Qaytarilgandan keyin ushbu to'lov qarz va hisobotlarda hisobga olinmaydi."
                ),
            )
        self._refund(request, object_id)
        return redirect(self._safe_redirect_url(request, request.POST.get('next'), cancel_url))

    @action(
        description=_("Qaytarish"),
        url_path="refund-row",
        permissions=["refund"],
        variant=ActionVariant.DEFAULT,
    )
    def refund_transaction(self, request, object_id):
        return self._refund_response(request, object_id, detail=False)

    @action(
        description=_("Qaytarish"),
        url_path="refund-detail",
        permissions=["refund"],
        variant=ActionVariant.DEFAULT,
    )
    def refund_transaction_detail(self, request, object_id):
        return self._refund_response(request, object_id, detail=True)


@admin.register(SubTransaction)
class SubTransactionAdmin(ModelAdmin):
    """Ichki to'lovlar navbati — admin har birini alohida tasdiqlaydi."""

    list_display = (
        'transaction_link', 'clients_display', 'amount', 'payment_method_display',
        'receipt_display', 'received_by_display', 'received_at', 'status_badge',
    )
    list_filter = ('status', 'payment_method', 'transaction__group', 'received_by')
    search_fields = ('clients__full_name', 'transaction__id')
    date_hierarchy = 'received_at'
    ordering = ('-received_at', '-id')
    actions_row = ('approve_sub_transaction', 'reject_sub_transaction')
    actions_detail = ('approve_sub_transaction_detail', 'reject_sub_transaction_detail')

    readonly_fields = (
        'transaction', 'clients_display', 'amount', 'payment_method', 'receipt_preview',
        'received_by', 'received_at', 'status', 'reviewed_by', 'reviewed_at', 'review_note',
    )
    fieldsets = (
        (None, {
            'fields': ('transaction', 'clients_display', 'amount', 'payment_method', 'receipt_preview'),
        }),
        (_("Qabul qilish"), {'fields': ('received_by', 'received_at')}),
        (_("Ko'rib chiqish"), {'fields': ('status', 'reviewed_by', 'reviewed_at', 'review_note')}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            'transaction__group__course', 'received_by', 'reviewed_by',
        ).prefetch_related('clients')
        if _is_plain_operator(request):
            qs = qs.filter(transaction__operator=request.user.operator)
        return qs

    # Ichki to'lovlar faqat "Pul qabul qilish" oqimi orqali yaratiladi va
    # yaratilgach o'zgartirilmaydi — audit izi buzilmasligi uchun.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_review_permission(self, request, obj=None):
        return request.user.is_superuser

    # ---- Ustunlar ----
    @display(description=_("Asosiy to'lov"))
    def transaction_link(self, obj):
        return format_html(
            '<a href="{}" class="text-primary-600 dark:text-primary-400 hover:underline">#{}</a>',
            reverse('admin:main_transaction_detail', args=[obj.transaction_id]),
            obj.transaction_id,
        )

    @display(description=_("Mijozlar"))
    def clients_display(self, obj):
        names = [client.full_name for client in obj.clients.all()]
        return ", ".join(names) if names else "—"

    @display(description=_("To'lov turi"))
    def payment_method_display(self, obj):
        return obj.get_payment_method_display()

    @display(description=_("Chek"))
    def receipt_display(self, obj):
        if obj.screenshot:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener" class="text-primary-600 dark:text-primary-400 hover:underline">{}</a>',
                obj.screenshot.url,
                _("Ko'rish"),
            )
        return _("Naqd — shart emas") if not obj.receipt_required else _("Yuklanmagan")

    @display(description=_("Chek"))
    def receipt_preview(self, obj):
        if obj.screenshot:
            return format_html(
                '<a href="{0}" target="_blank" rel="noopener">'
                '<img src="{0}" style="max-height:220px;border-radius:8px;border:1px solid #e5e7eb;" />'
                '</a>',
                obj.screenshot.url,
            )
        return _("Chek yuklanmagan")

    @display(description=_("Qabul qildi"))
    def received_by_display(self, obj):
        return obj.receiver_name

    @display(
        description=_("Holati"),
        label={
            _("Tasdiq kutilmoqda"): "warning",
            _("Tasdiqlangan"): "success",
            _("Rad etilgan"): "danger",
        },
    )
    def status_badge(self, obj):
        return obj.get_status_display()

    # ---- Tasdiqlash / rad etish ----
    def _review_cancel_url(self, request, object_id, detail=False):
        fallback = (
            reverse('admin:main_subtransaction_change', args=[object_id])
            if detail else reverse('admin:main_subtransaction_changelist')
        )
        if url_has_allowed_host_and_scheme(
            request.META.get('HTTP_REFERER') or '',
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return request.META['HTTP_REFERER']
        return fallback

    def _review_response(self, request, object_id, approve, detail=False):
        obj = self.get_object(request, object_id)
        cancel_url = self._review_cancel_url(request, object_id, detail=detail)
        if obj is None:
            self.message_user(request, _("Ichki to'lov topilmadi."), level=messages.ERROR)
            return redirect('admin:main_subtransaction_changelist')

        if request.method != 'POST':
            if obj.status != SubTransaction.STATUS_PENDING:
                self.message_user(
                    request, _("Bu ichki to'lov allaqachon ko'rib chiqilgan."), level=messages.WARNING,
                )
                return redirect(cancel_url)
            context = {
                **self.admin_site.each_context(request),
                'title': _("Ichki to'lovni tasdiqlash") if approve else _("Ichki to'lovni rad etish"),
                'object': obj,
                'sub_transaction': obj,
                'approve': approve,
                'form': SubTransactionReviewForm(),
                'block_reason': _sub_transaction_block_reason(obj) if approve else None,
                'action_label': _("Tasdiqlash") if approve else _("Rad etish"),
                'cancel_url': cancel_url,
                'next_url': cancel_url,
                'post_url': request.path,
                'opts': self.model._meta,
            }
            return TemplateResponse(
                request, "admin/main/subtransaction/review_confirm.html", context,
            )

        form = SubTransactionReviewForm(request.POST)
        note = form.cleaned_data['note'] if form.is_valid() else ''
        ok, message = _review_sub_transaction(obj, request.user, approve, note=note)
        self.message_user(request, message, level=messages.SUCCESS if ok else messages.WARNING)

        redirect_to = request.POST.get('next') or cancel_url
        if not url_has_allowed_host_and_scheme(
            redirect_to, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
        ):
            redirect_to = cancel_url
        return redirect(redirect_to)

    @action(
        description=_("Tasdiqlash"), url_path="approve-row",
        permissions=["review"], variant=ActionVariant.SUCCESS,
    )
    def approve_sub_transaction(self, request, object_id):
        return self._review_response(request, object_id, approve=True, detail=False)

    @action(
        description=_("Rad etish"), url_path="reject-row",
        permissions=["review"], variant=ActionVariant.DANGER,
    )
    def reject_sub_transaction(self, request, object_id):
        return self._review_response(request, object_id, approve=False, detail=False)

    @action(
        description=_("Tasdiqlash"), url_path="approve-detail",
        permissions=["review"], variant=ActionVariant.SUCCESS,
    )
    def approve_sub_transaction_detail(self, request, object_id):
        return self._review_response(request, object_id, approve=True, detail=True)

    @action(
        description=_("Rad etish"), url_path="reject-detail",
        permissions=["review"], variant=ActionVariant.DANGER,
    )
    def reject_sub_transaction_detail(self, request, object_id):
        return self._review_response(request, object_id, approve=False, detail=True)
