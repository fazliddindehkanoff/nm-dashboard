import uuid
from decimal import Decimal, ROUND_HALF_UP

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
    uuid = models.UUIDField(
        _("UUID"),
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text=_("Mijozning yagona identifikatori (QR kod uchun ishlatiladi)."),
    )
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
    amocrm_lead_id = models.BigIntegerField(
        _("amoCRM Lead ID"),
        null=True,
        blank=True,
        help_text=_("Mijozga mos keluvchi amoCRM lead (bitim) ID raqami."),
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
        ('naqd', _("Naqd pul")),
    )

    # Sotuv manbasi ikkiga bo'linadi: amoCRM da bor / amoCRM da yo'q.
    # "amoCRM da bor" o'z navbatida ikkiga: sayt orqali kelgan yoki boshqa.
    SOURCE_TYPES = (
        ('amocrm_website', _("amoCRM'da bor — sayt orqali")),
        ('amocrm_other', _("amoCRM'da bor — boshqa")),
        ('not_in_amocrm', _("amoCRM'da yo'q")),
    )

    operator = models.ForeignKey(Operator, on_delete=models.SET_NULL, null=True, verbose_name=_("Operator"))
    clients = models.ManyToManyField(
        Client, through='TransactionClient', related_name='transactions', verbose_name=_("Mijozlar"),
    )
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

        # Chegirmani hisoblash: bron to'lovi bo'lsa bron chegirmasi avtomatik
        # qo'llanadi, ustiga qo'shimcha bitta chegirma qo'shilishi mumkin.
        booking_discount = Decimal(0)
        if self.payment_type == 'bron':
            booking = Discount.objects.filter(is_booking=True, is_active=True).first()
            booking_discount = _dec(booking.amount) if booking else Decimal(0)
        additional_discount = _dec(self.discount.amount) if self.discount else Decimal(0)
        self.discount_total = booking_discount + additional_discount

        super().save(*args, **kwargs)

        # Mijozlar (va ularning ulushlari/qarzi) shu tranzaksiyaga biriktirilgan
        # bo'lsa — qayta hisoblaymiz. Yangi (hali mijozsiz) tranzaksiya uchun
        # bu no-op: mijozlar keyinroq (inline formset orqali) biriktiriladi.
        if self.pk:
            _recalc_transaction_participants(self)

    def __str__(self):
        names = ", ".join(c.full_name for c in self.clients.all()) if self.pk else ""
        return f"{names or '—'} - {self.amount}"


class TransactionClient(models.Model):
    """Bitta to'lovdagi bitta mijozning ulushi (summa/chegirma/qarz).

    Bir nechta mijoz bitta to'lovga biriktirilganda (masalan opa-uka bitta
    chekda), to'lov summasi ular orasida teng bo'linadi — har birining ulushi
    shu yerda saqlanadi va o'z guruhidagi qarzini mustaqil belgilaydi.
    """

    transaction = models.ForeignKey(
        Transaction, on_delete=models.CASCADE, related_name='participants', verbose_name=_("To'lov"),
    )
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name='participations', verbose_name=_("Mijoz"),
    )
    share_amount = models.DecimalField(_("Ulush miqdori"), max_digits=12, decimal_places=2, default=0, editable=False)
    share_discount = models.DecimalField(_("Ulush chegirmasi"), max_digits=12, decimal_places=2, default=0, editable=False)
    debt = models.DecimalField(_("Qarzi"), max_digits=12, decimal_places=2, default=0, editable=False)

    class Meta:
        verbose_name = _("To'lov ishtirokchisi")
        verbose_name_plural = _("To'lov ishtirokchilari")
        constraints = [
            models.UniqueConstraint(fields=['transaction', 'client'], name='uniq_transaction_client'),
        ]

    def __str__(self):
        return f"{self.client.full_name} — #{self.transaction_id}"


def _split_amount(total, n):
    """`total` ni `n` ta ulushga aniq (tiyingacha) bo'lib beradi.

    Yig'indi har doim `total` ga teng bo'ladi — qoldiq tiyinlar birinchi
    qatorlarga bittadan qo'shiladi.
    """
    total = Decimal(str(total or 0))
    cents_total = int((total * 100).to_integral_value(rounding=ROUND_HALF_UP))
    base_cents, remainder_cents = divmod(cents_total, n)
    return [
        Decimal(base_cents + (1 if i < remainder_cents else 0)) / Decimal(100)
        for i in range(n)
    ]


def _recalc_transaction_participants(transaction):
    """Tranzaksiyaga biriktirilgan har bir mijozning ulushini (summa/chegirma)
    qayta hisoblaydi, so'ng har biriga tegishli guruh qarzini yangilaydi."""
    participants = list(
        TransactionClient.objects.filter(transaction_id=transaction.pk).order_by('id')
    )
    if not participants:
        return

    n = len(participants)
    amount_shares = _split_amount(transaction.amount, n)
    discount_shares = _split_amount(transaction.discount_total, n)

    for tc, a_share, d_share in zip(participants, amount_shares, discount_shares):
        if tc.share_amount != a_share or tc.share_discount != d_share:
            TransactionClient.objects.filter(pk=tc.pk).update(
                share_amount=a_share, share_discount=d_share,
            )

    group_id = transaction.group_id
    for client_id in {tc.client_id for tc in participants}:
        _recalc_group_debt(client_id, group_id)


def _recalc_group_debt(client_id, group_id):
    """Bitta mijoz+guruh uchun qarzni barcha (qaytarilmagan) ulushlar bo'yicha
    qayta taqsimlaydi.

    Yakuniy narx = kurs narxi - jami chegirmalar. Qolgan qarz = yakuniy narx -
    jami to'langan summa. Qolgan qarz eng oxirgi ulushga (tranzaksiya+mijoz
    juftligiga) yoziladi, qolganlari 0 bo'ladi — shunda dashboarddagi
    Sum(debt) haqiqiy qarzni beradi. Qaytarilgan tranzaksiyalar hisobga
    olinmaydi va ularning qarzi 0 ga tushiriladi.
    """
    if not client_id or not group_id:
        return

    def _dec(value):
        return Decimal(str(value or 0))

    rows = list(
        TransactionClient.objects.filter(
            client_id=client_id, transaction__group_id=group_id, transaction__is_refunded=False,
        ).select_related('transaction__group__course').order_by('transaction__date', 'transaction_id')
    )

    # Qaytarilgan tranzaksiyalar qarzni saqlab qolmasligi kerak.
    TransactionClient.objects.filter(
        client_id=client_id, transaction__group_id=group_id, transaction__is_refunded=True,
    ).exclude(debt=0).update(debt=0)

    if not rows:
        return

    course = rows[0].transaction.group.course
    course_price = _dec(course.price)
    total_discount = sum((_dec(r.share_discount) for r in rows), Decimal(0))
    total_paid = sum((_dec(r.share_amount) for r in rows), Decimal(0))

    net_price = max(course_price - total_discount, Decimal(0))
    remaining = max(net_price - total_paid, Decimal(0))

    latest = rows[-1]
    for r in rows:
        new_debt = remaining if r.pk == latest.pk else Decimal(0)
        if _dec(r.debt) != new_debt:
            TransactionClient.objects.filter(pk=r.pk).update(debt=new_debt)
