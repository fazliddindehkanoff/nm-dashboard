from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import User

class Course(models.Model):
    name = models.CharField(_("Nomi"), max_length=255)
    price = models.DecimalField(_("Narxi"), max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = _("Kurs")
        verbose_name_plural = _("Kurslar")

    def __str__(self):
        return self.name

class Teacher(models.Model):
    full_name = models.CharField(_("Ism-familiya"), max_length=255)

    class Meta:
        verbose_name = _("O'qituvchi")
        verbose_name_plural = _("O'qituvchilar")

    def __str__(self):
        return self.full_name

class Group(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, verbose_name=_("Kurs"))
    teachers = models.ManyToManyField(Teacher, verbose_name=_("O'qituvchilar"), blank=True)
    start_date = models.DateField(_("Boshlanish sanasi"), null=True)
    is_active = models.BooleanField(
        _("Faol"),
        default=True,
        help_text=_("Faol bo'lmagan guruhlar yangi to'lovlarda ko'rsatilmaydi."),
    )

    class Meta:
        verbose_name = _("Guruh")
        verbose_name_plural = _("Guruhlar")

    def __str__(self):
        teachers_list = ", ".join([t.full_name for t in self.teachers.all()]) if self.pk else ""
        t_str = f" ({teachers_list})" if teachers_list else ""
        date_str = self.start_date.strftime("%d.%m.%Y") if self.start_date else ""
        return f"{self.course.name}{t_str} - {date_str}"

class Client(models.Model):
    full_name = models.CharField(_("Familiya-Ism"), max_length=255)
    phone_number = models.CharField(_("Telefon raqam"), max_length=20)
    operator = models.ForeignKey(
        'Operator',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Operator"),
        help_text=_("Mijozni yaratgan operator.")
    )

    # amoCRM integratsiyasi: mijozlar amoCRM dan yuklanadi.
    amocrm_id = models.BigIntegerField(
        _("amoCRM ID"),
        unique=True,
        null=True,
        blank=True,
        help_text=_("amoCRM dagi kontakt ID raqami."),
    )
    synced_at = models.DateTimeField(_("amoCRM sinxron sanasi"), null=True, blank=True)

    class Meta:
        verbose_name = _("Mijoz")
        verbose_name_plural = _("Mijozlar")

    def __str__(self):
        return self.full_name

class Operator(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, verbose_name=_("Foydalanuvchi"), null=True, blank=True)
    full_name = models.CharField(_("Familiya-Ism"), max_length=255)
    phone_number = models.CharField(_("Telefon raqam"), max_length=20, null=True, blank=True)

    class Meta:
        verbose_name = _("Operator")
        verbose_name_plural = _("Operatorlar")

    def __str__(self):
        return self.full_name


class Discount(models.Model):
    """Dinamik chegirmalar. Miqdorlar admin orqali boshqariladi.

    `is_booking=True` bo'lgan chegirma bron to'lovlarda avtomatik qo'llanadi
    (masalan bron uchun -200 000 so'm). Qolgan (qo'shimcha) chegirmalardan
    to'lovga faqat bittasi qo'lda tanlanadi.
    """

    name = models.CharField(_("Nomi"), max_length=255)
    amount = models.DecimalField(_("Chegirma miqdori"), max_digits=12, decimal_places=2)
    is_booking = models.BooleanField(
        _("Bron chegirmasi"),
        default=False,
        help_text=_("Belgilansa, bron to'lovlarida avtomatik qo'llanadi."),
    )
    is_active = models.BooleanField(_("Faol"), default=True)

    class Meta:
        verbose_name = _("Chegirma")
        verbose_name_plural = _("Chegirmalar")
        ordering = ("-is_booking", "name")

    def __str__(self):
        return f"{self.name} (-{self.amount})"

class Transaction(models.Model):
    PAYMENT_TYPES = (
        ('bron', _("Bron")),
        ('doplata', _("Doplata")),
        ('to_liq_tolov', _("To'liq to'lov")),
    )

    # Sotuv manbasi ikkiga bo'linadi: amoCRM da bor / amoCRM da yo'q.
    # "amoCRM da bor" o'z navbatida ikkiga: sayt orqali kelgan yoki boshqa.
    SOURCE_TYPES = (
        ('amocrm_website', _("amoCRM'da bor — sayt orqali")),
        ('amocrm_other', _("amoCRM'da bor — boshqa")),
        ('not_in_amocrm', _("amoCRM'da yo'q")),
    )

    operator = models.ForeignKey(Operator, on_delete=models.SET_NULL, null=True, verbose_name=_("Operator"))
    client = models.ForeignKey(Client, on_delete=models.CASCADE, verbose_name=_("Mijoz"))
    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True, verbose_name=_("Guruh/Kurs nomi"))
    date = models.DateField(_("Sanasi"))
    amount = models.DecimalField(_("To'lov miqdori"), max_digits=12, decimal_places=2)
    payment_type = models.CharField(_("To'lov turi"), max_length=20, choices=PAYMENT_TYPES)

    source = models.CharField(
        _("Sotuv manbasi"),
        max_length=20,
        choices=SOURCE_TYPES,
        default='not_in_amocrm',
    )
    source_detail = models.CharField(
        _("Manba tafsiloti"),
        max_length=255,
        null=True,
        blank=True,
        help_text=_("Masalan, sayt nomi (sayt orqali kelgan bo'lsa)."),
    )

    # To'lovni tasdiqlovchi chek/skrinshot
    screenshot = models.ImageField(
        _("Chek / skrinshot"),
        upload_to='payment_screenshots/%Y/%m/',
        null=True,
        blank=True,
        help_text=_("To'lov chekining rasmi yoki skrinshoti."),
    )

    # Qo'shimcha chegirma (bron chegirmasidan tashqari faqat bittasi tanlanadi)
    discount = models.ForeignKey(
        Discount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Qo'shimcha chegirma"),
        limit_choices_to={'is_active': True, 'is_booking': False},
    )

    # To'lovni admin tasdiqlashi kerak.
    is_confirmed = models.BooleanField(_("Tasdiqlangan"), default=False)
    confirmed_at = models.DateTimeField(_("Tasdiqlangan sana"), null=True, blank=True)
    confirmed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='confirmed_transactions',
        verbose_name=_("Tasdiqladi"),
    )

    is_refunded = models.BooleanField(_("Qaytarilgan"), default=False)
    refunded_at = models.DateField(_("Qaytarilgan sana"), null=True, blank=True)

    course_price = models.DecimalField(_("Kurs narxi"), max_digits=12, decimal_places=2, editable=False, default=0)
    discount_total = models.DecimalField(_("Jami chegirma"), max_digits=12, decimal_places=2, editable=False, default=0)
    debt = models.DecimalField(_("Qarzi"), max_digits=12, decimal_places=2, editable=False, default=0)

    class Meta:
        verbose_name = _("To'lov")
        verbose_name_plural = _("To'lovlar")

    def save(self, *args, **kwargs):
        def _dec(value):
            return Decimal(str(value or 0))

        if self.group:
            self.course_price = self.group.course.price
        else:
            self.course_price = 0
        course_price = _dec(self.course_price)
        amount = _dec(self.amount)

        # Chegirmani hisoblash: bron to'lovi bo'lsa bron chegirmasi avtomatik
        # qo'llanadi, ustiga qo'shimcha bitta chegirma qo'shilishi mumkin.
        booking_discount = Decimal(0)
        if self.payment_type == 'bron':
            booking = Discount.objects.filter(is_booking=True, is_active=True).first()
            booking_discount = _dec(booking.amount) if booking else Decimal(0)
        additional_discount = _dec(self.discount.amount) if self.discount else Decimal(0)
        self.discount_total = booking_discount + additional_discount

        # Chegirma hisobga olingan yakuniy narx
        net_price = max(course_price - self.discount_total, Decimal(0))

        if self.payment_type == 'bron':
            self.debt = max(net_price - amount, Decimal(0))
        else:
            # 'to_liq_tolov' to'liq to'langan; 'doplata' uchun oldingi bron
            # ma'lum bo'lmagani sababli qarz avtomatik hisoblanmaydi.
            self.debt = Decimal(0)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.client.full_name} - {self.amount}"
