import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import Permission, User


class RoleConfiguration(models.Model):
    """Platformadagi tizim rollari va ularga berilgan ruxsatlar matritsasi."""

    ROLE_ADMIN = 'admin'
    ROLE_OPERATOR = 'operator'
    ROLE_OWNER = 'owner'
    ROLE_ACCOUNTANT = 'accountant'
    ROLE_CHOICES = (
        (ROLE_ADMIN, _("Admin")),
        (ROLE_OPERATOR, _("Operator / sotuvchi")),
        (ROLE_OWNER, _("Biznes egasi / tadbirkor")),
        (ROLE_ACCOUNTANT, _("Buxgalter / kassir")),
    )

    code = models.CharField(_("Rol"), max_length=20, choices=ROLE_CHOICES, unique=True)
    permissions = models.ManyToManyField(
        Permission,
        blank=True,
        verbose_name=_("Ochiq bo'lim va funksiyalar"),
        help_text=_("Ushbu rol foydalana oladigan bo'lim va amallarni belgilang."),
    )

    class Meta:
        verbose_name = _("Rol ruxsatlari")
        verbose_name_plural = _("Rollar va ruxsatlar")
        ordering = ("code",)
        permissions = (
            ("access_dashboard", "Dashboardni ko'rish"),
            ("access_salary_report", "Maosh hisobotini ko'rish"),
            ("access_qr_scanner", "QR skanerdan foydalanish"),
            ("access_cashflow", "Kirim-chiqim va kassa balansini ko'rish"),
        )

    def __str__(self):
        return self.get_code_display()

class Course(models.Model):
    name = models.CharField(_("Nomi"), max_length=255)
    price = models.DecimalField(_("Narxi"), max_digits=12, decimal_places=2)
    number_of_days = models.PositiveSmallIntegerField(
        _("Dars kunlari soni"),
        default=1,
        validators=(MinValueValidator(1), MaxValueValidator(365)),
        help_text=_("Ushbu kurs odatda necha kun davom etishini kiriting."),
    )

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
    start_date = models.DateField(_("Boshlanish sanasi"))
    number_of_days = models.PositiveSmallIntegerField(
        _("Dars kunlari soni"),
        default=1,
        validators=(MinValueValidator(1), MaxValueValidator(365)),
        help_text=_("Kursdan avtomatik olinadi; ushbu guruh uchun o'zgartirish mumkin."),
    )
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
    ROLE_CHOICES = RoleConfiguration.ROLE_CHOICES

    user = models.OneToOneField(User, on_delete=models.CASCADE, verbose_name=_("Foydalanuvchi"), null=True, blank=True)
    full_name = models.CharField(_("Familiya-Ism"), max_length=255)
    phone_number = models.CharField(_("Telefon raqam"), max_length=20, null=True, blank=True)
    role = models.CharField(
        _("Rol"),
        max_length=20,
        choices=ROLE_CHOICES,
        default=RoleConfiguration.ROLE_OPERATOR,
    )

    class Meta:
        verbose_name = _("Foydalanuvchi")
        verbose_name_plural = _("Foydalanuvchilar")

    def __str__(self):
        return self.full_name


class Expense(models.Model):
    CATEGORY_RENT = 'rent'
    CATEGORY_SALARY = 'salary'
    CATEGORY_MARKETING = 'marketing'
    CATEGORY_SUPPLIES = 'supplies'
    CATEGORY_TAX = 'tax'
    CATEGORY_UTILITIES = 'utilities'
    CATEGORY_OTHER = 'other'
    CATEGORIES = (
        (CATEGORY_RENT, _("Ijara")),
        (CATEGORY_SALARY, _("Maosh")),
        (CATEGORY_MARKETING, _("Marketing")),
        (CATEGORY_SUPPLIES, _("Xo'jalik xarajatlari")),
        (CATEGORY_TAX, _("Soliq")),
        (CATEGORY_UTILITIES, _("Kommunal xizmatlar")),
        (CATEGORY_OTHER, _("Boshqa")),
    )

    date = models.DateField(_("Sana"))
    category = models.CharField(_("Kategoriya"), max_length=20, choices=CATEGORIES)
    amount = models.DecimalField(_("Summa"), max_digits=14, decimal_places=2)
    description = models.CharField(_("Izoh"), max_length=500, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='recorded_expenses',
        verbose_name=_("Kiritdi"),
        editable=False,
    )
    created_at = models.DateTimeField(_("Kiritilgan vaqt"), auto_now_add=True)

    class Meta:
        verbose_name = _("Xarajat")
        verbose_name_plural = _("Xarajatlar")
        ordering = ('-date', '-id')

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({'amount': _("Xarajat summasi noldan katta bo'lishi kerak.")})

    def __str__(self):
        return f"{self.get_category_display()} — {self.amount}"


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

    @property
    def approved_sub_transactions(self):
        return _sub_transactions_with_status(self, SubTransaction.STATUS_APPROVED)

    @property
    def pending_sub_transactions(self):
        return _sub_transactions_with_status(self, SubTransaction.STATUS_PENDING)

    @property
    def initial_amount(self):
        """Qo'shimcha qabullardan oldingi asosiy to'lov summasi.

        `amount` faqat tasdiqlangan ichki to'lovlar bilan o'sadi, shuning uchun
        bu yerda ham faqat o'shalar ayiriladi.
        """
        sub_total = sum((item.amount for item in self.approved_sub_transactions), Decimal(0))
        return max(Decimal(str(self.amount or 0)) - sub_total, Decimal(0))

    @property
    def pending_sub_total(self):
        """Tasdiq kutayotgan ichki to'lovlarning jami summasi (qarzga ta'sir qilmaydi)."""
        return sum((item.amount for item in self.pending_sub_transactions), Decimal(0))

    @property
    def pending_sub_count(self):
        return len(self.pending_sub_transactions)

    @property
    def total_due(self):
        """Ushbu transactiondagi barcha mijozlar uchun jami majburiyat."""
        prefetched = getattr(self, '_prefetched_objects_cache', {}).get('participants')
        clients_count = len(prefetched) if prefetched is not None else self.participants.count()
        gross = Decimal(str(self.course_price or 0)) * clients_count
        return max(gross - Decimal(str(self.discount_total or 0)), Decimal(0))

    @property
    def total_remaining(self):
        prefetched = getattr(self, '_prefetched_objects_cache', {}).get('participants')
        participants = prefetched if prefetched is not None else self.participants.select_related('transaction')
        return sum((participant.remaining_amount for participant in participants), Decimal(0))


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

    @property
    def amount_due(self):
        return max(
            Decimal(str(self.transaction.course_price or 0)) - Decimal(str(self.share_discount or 0)),
            Decimal(0),
        )

    @property
    def remaining_amount(self):
        return max(self.amount_due - Decimal(str(self.share_amount or 0)), Decimal(0))


class AttendanceRecord(models.Model):
    """Mijozning bitta guruhdagi doimiy davomat kartasi.

    To'lov yoki tranzaksiya o'chirilsa ham bu yozuv saqlanadi. Shu sababli
    mijoz va guruh PROTECT bilan bog'langan va davomat kartasi admin orqali
    o'chirilmaydi.
    """

    STATUS_ACTIVE = 'active'
    STATUS_PARTIAL = 'partial'
    STATUS_FIRST_DAY_ONLY = 'first_day_only'
    STATUS_NEVER_ATTENDED = 'never_attended'
    STATUS_COMPLETED = 'completed'
    STATUSES = (
        (STATUS_ACTIVE, _("Kursda qatnashyapti")),
        (STATUS_PARTIAL, _("Qisman qatnashdi")),
        (STATUS_FIRST_DAY_ONLY, _("Birinchi kun keldi, keyin kelmadi")),
        (STATUS_NEVER_ATTENDED, _("Umuman kelmadi")),
        (STATUS_COMPLETED, _("Kursni yakunladi")),
    )

    PAYMENT_UNPAID = 'unpaid'
    PAYMENT_PENDING = 'pending'
    PAYMENT_PARTIAL = 'partial'
    PAYMENT_PAID = 'paid'
    PAYMENT_LABELS = {
        PAYMENT_UNPAID: _("To'lanmagan"),
        PAYMENT_PENDING: _("Tasdiq kutilmoqda"),
        PAYMENT_PARTIAL: _("Qisman to'langan"),
        PAYMENT_PAID: _("To'langan"),
    }

    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name='attendance_records',
        verbose_name=_("Mijoz"),
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name='attendance_records',
        verbose_name=_("Guruh"),
    )
    status = models.CharField(
        _("Davomat holati"),
        max_length=20,
        choices=STATUSES,
        default=STATUS_ACTIVE,
    )
    last_attended_at = models.DateField(_("Oxirgi kelgan sana"), null=True, blank=True, editable=False)
    absence_reason = models.TextField(
        _("Kelmaslik sababi"),
        blank=True,
        help_text=_("Mijoz aytgan sabab yoki bog'lanish natijasini yozing."),
    )
    operator_note = models.TextField(
        _("Operator izohi"),
        blank=True,
        help_text=_("Keyingi bog'lanish uchun muhim tafsilotlar."),
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_attendance_records',
        verbose_name=_("Kartani yaratdi"),
        editable=False,
    )
    created_at = models.DateTimeField(_("Yaratilgan vaqt"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Yangilangan vaqt"), auto_now=True)

    class Meta:
        verbose_name = _("Davomat kartasi")
        verbose_name_plural = _("Davomat / kelmagan mijozlar")
        ordering = ('-updated_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=('client', 'group'), name='uniq_attendance_client_group',
            ),
        ]

    def __str__(self):
        return f"{self.client.full_name} — {self.group}"

    @property
    def attended_lessons_count(self):
        prefetched = getattr(self, '_prefetched_objects_cache', {}).get('lessons')
        attended_statuses = {
            AttendanceLesson.STATUS_ATTENDED,
            AttendanceLesson.STATUS_LATE,
        }
        if prefetched is not None:
            return sum(lesson.status in attended_statuses for lesson in prefetched)
        return self.lessons.filter(status__in=attended_statuses).count()

    def _payment_participations(self):
        return self.client.participations.filter(
            transaction__group=self.group,
            transaction__is_refunded=False,
        )

    @property
    def payment_status(self):
        annotated_confirmed = getattr(self, '_confirmed_payment_count', None)
        if annotated_confirmed is not None:
            if annotated_confirmed:
                debt = getattr(self, '_confirmed_payment_debt', None) or Decimal(0)
                return self.PAYMENT_PARTIAL if debt > 0 else self.PAYMENT_PAID
            if getattr(self, '_pending_payment_count', 0):
                return self.PAYMENT_PENDING
            return self.PAYMENT_UNPAID

        participations = self._payment_participations()
        confirmed = participations.filter(transaction__is_confirmed=True)
        if confirmed.exists():
            debt = confirmed.aggregate(total=models.Sum('debt'))['total'] or Decimal(0)
            return self.PAYMENT_PARTIAL if debt > 0 else self.PAYMENT_PAID
        if participations.filter(transaction__is_confirmed=False).exists():
            return self.PAYMENT_PENDING
        return self.PAYMENT_UNPAID

    @property
    def payment_status_display(self):
        return self.PAYMENT_LABELS[self.payment_status]


class AttendanceLesson(models.Model):
    STATUS_UNMARKED = 'unmarked'
    STATUS_ATTENDED = 'attended'
    STATUS_ABSENT = 'absent'
    STATUS_EXCUSED = 'excused'
    STATUS_LATE = 'late'
    STATUSES = (
        (STATUS_UNMARKED, _("Belgilanmagan")),
        (STATUS_ATTENDED, _("Keldi")),
        (STATUS_ABSENT, _("Kelmadi")),
        (STATUS_EXCUSED, _("Sababli kelmadi")),
        (STATUS_LATE, _("Kechikdi")),
    )

    attendance = models.ForeignKey(
        AttendanceRecord,
        on_delete=models.CASCADE,
        related_name='lessons',
        verbose_name=_("Davomat kartasi"),
    )
    date = models.DateField(_("Dars sanasi"))
    status = models.CharField(
        _("Darsdagi holati"),
        max_length=16,
        choices=STATUSES,
        default=STATUS_ATTENDED,
    )
    reason = models.CharField(
        _("Kelmaslik sababi"),
        max_length=500,
        blank=True,
    )
    note = models.CharField(_("Dars bo'yicha izoh"), max_length=255, blank=True)
    marked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marked_attendance_lessons',
        verbose_name=_("Belgiladi"),
        editable=False,
    )
    created_at = models.DateTimeField(_("Belgilangan vaqt"), auto_now_add=True)

    class Meta:
        verbose_name = _("Dars davomati")
        verbose_name_plural = _("Dars davomati")
        ordering = ('-date', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=('attendance', 'date'), name='uniq_attendance_lesson_date',
            ),
        ]

    def __str__(self):
        return f"{self.attendance.client} — {self.date:%d.%m.%Y}"


class SubTransaction(models.Model):
    """Mavjud to'lov ichida keyinroq qabul qilingan qo'shimcha to'lov.

    Har bir ichki to'lov admin tomonidan alohida tasdiqlanadi. Faqat
    tasdiqlangan (`approved`) ichki to'lovlar mijozning qarzini kamaytiradi —
    kutilayotgan yozuvlar hisob-kitobga umuman kirmaydi.
    """

    METHOD_CASH = 'naqd'
    METHODS = (
        (METHOD_CASH, _("Naqd pul")),
        ('karta', _("Plastik karta")),
        ('bank', _("Bank o'tkazmasi")),
        ('online', _("Online (Payme / Click)")),
    )

    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUSES = (
        (STATUS_PENDING, _("Tasdiq kutilmoqda")),
        (STATUS_APPROVED, _("Tasdiqlangan")),
        (STATUS_REJECTED, _("Rad etilgan")),
    )

    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name='sub_transactions',
        verbose_name=_("Asosiy to'lov"),
    )
    clients = models.ManyToManyField(
        Client,
        related_name='sub_transactions',
        verbose_name=_("Mijozlar"),
    )
    amount = models.DecimalField(_("To'lov miqdori"), max_digits=12, decimal_places=2)
    payment_method = models.CharField(
        _("To'lov turi"),
        max_length=20,
        choices=METHODS,
        default=METHOD_CASH,
        help_text=_("Naqd pulda chek talab qilinmaydi, qolgan turlarda majburiy."),
    )
    screenshot = models.ImageField(
        _("Chek / skrinshot"),
        upload_to='subtransaction_receipts/%Y/%m/',
        null=True,
        blank=True,
    )
    received_at = models.DateTimeField(_("Qabul qilingan vaqt"), auto_now_add=True)
    received_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_sub_transactions',
        verbose_name=_("Qabul qildi"),
    )

    status = models.CharField(
        _("Holati"), max_length=10, choices=STATUSES, default=STATUS_PENDING,
    )
    reviewed_at = models.DateTimeField(_("Ko'rib chiqilgan vaqt"), null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_sub_transactions',
        verbose_name=_("Ko'rib chiqdi"),
    )
    review_note = models.CharField(_("Izoh"), max_length=255, blank=True, default='')

    class Meta:
        verbose_name = _("Ichki to'lov")
        verbose_name_plural = _("Ichki to'lovlar")
        ordering = ('-received_at', '-id')
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gt=0), name='subtransaction_amount_gt_zero'),
        ]

    def __str__(self):
        return f"#{self.transaction_id} + {self.amount}"

    @staticmethod
    def receipt_required_for(payment_method):
        """Naqd pulda chek majburiy emas, qolgan barcha turlarda majburiy."""
        return payment_method != SubTransaction.METHOD_CASH

    @property
    def receipt_required(self):
        return self.receipt_required_for(self.payment_method)

    @property
    def is_approved(self):
        return self.status == self.STATUS_APPROVED

    @property
    def is_pending(self):
        return self.status == self.STATUS_PENDING

    def clean(self):
        super().clean()
        if self.receipt_required and not self.screenshot:
            raise ValidationError({
                'screenshot': _("Naqd puldan boshqa to'lov turlarida chek majburiy."),
            })

    @property
    def receiver_name(self):
        if not self.received_by:
            return "—"
        try:
            return self.received_by.operator.full_name
        except Operator.DoesNotExist:
            return self.received_by.get_full_name() or self.received_by.username

    @property
    def reviewer_name(self):
        if not self.reviewed_by:
            return "—"
        try:
            return self.reviewed_by.operator.full_name
        except Operator.DoesNotExist:
            return self.reviewed_by.get_full_name() or self.reviewed_by.username


class TelegramUser(models.Model):
    STEP_NAME = 'name'
    STEP_CONTACT = 'contact'
    STEP_READY = 'ready'
    ONBOARDING_STEPS = (
        (STEP_NAME, _("Ism kutilmoqda")),
        (STEP_CONTACT, _("Kontakt kutilmoqda")),
        (STEP_READY, _("Tayyor")),
    )

    telegram_id = models.BigIntegerField(_("Telegram ID"), unique=True)
    username = models.CharField(_("Telegram username"), max_length=64, blank=True)
    full_name = models.CharField(_("To'liq ism"), max_length=255, blank=True)
    phone_number = models.CharField(_("Telefon raqami"), max_length=20, blank=True)
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='telegram_accounts',
        verbose_name=_("CRM mijoz"),
    )
    onboarding_step = models.CharField(
        _("Onboarding bosqichi"),
        max_length=12,
        choices=ONBOARDING_STEPS,
        default=STEP_NAME,
    )
    created_at = models.DateTimeField(_("Yaratilgan vaqt"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Yangilangan vaqt"), auto_now=True)

    class Meta:
        verbose_name = _("Telegram foydalanuvchi")
        verbose_name_plural = _("Telegram foydalanuvchilar")
        ordering = ('-updated_at', '-id')

    def __str__(self):
        return self.full_name or self.username or str(self.telegram_id)


class TelegramCampaign(models.Model):
    AUDIENCE_READY = 'ready'
    AUDIENCE_ALL = 'all'
    AUDIENCES = (
        (AUDIENCE_READY, _("Onboardingni yakunlaganlar")),
        (AUDIENCE_ALL, _("Barcha Telegram foydalanuvchilar")),
    )

    STATUS_DRAFT = 'draft'
    STATUS_QUEUED = 'queued'
    STATUS_SENDING = 'sending'
    STATUS_COMPLETED = 'completed'
    STATUS_COMPLETED_ERRORS = 'completed_errors'
    STATUSES = (
        (STATUS_DRAFT, _("Qoralama")),
        (STATUS_QUEUED, _("Navbatda")),
        (STATUS_SENDING, _("Yuborilmoqda")),
        (STATUS_COMPLETED, _("Yakunlandi")),
        (STATUS_COMPLETED_ERRORS, _("Xatolar bilan yakunlandi")),
    )

    title = models.CharField(
        _("Kampaniya nomi"), max_length=160,
        help_text=_("Faqat CRM ichida ko'rinadigan nom."),
    )
    message = models.TextField(
        _("Xabar matni"), max_length=4096,
        help_text=_("Telegram HTML formatidan foydalanish mumkin."),
    )
    image = models.ImageField(
        _("Reklama rasmi"), upload_to='telegram_campaigns/%Y/%m/', blank=True, null=True,
    )
    button_text = models.CharField(_("Tugma matni"), max_length=64, blank=True)
    button_url = models.URLField(_("Tugma havolasi"), blank=True)
    button_opens_mini_app = models.BooleanField(
        _("Tugma Mini App'ni ochadi"), default=False,
        help_text=_("Belgilanganda Telegram foydalanuvchini tasdiqlangan Mini App oynasida ochadi."),
    )
    audience = models.CharField(
        _("Qabul qiluvchilar"), max_length=12, choices=AUDIENCES, default=AUDIENCE_READY,
    )
    status = models.CharField(
        _("Holati"), max_length=24, choices=STATUSES, default=STATUS_DRAFT, editable=False,
    )
    total_recipients = models.PositiveIntegerField(_("Jami"), default=0, editable=False)
    queued_count = models.PositiveIntegerField(_("Navbatda"), default=0, editable=False)
    sent_count = models.PositiveIntegerField(_("Yuborildi"), default=0, editable=False)
    failed_count = models.PositiveIntegerField(_("Xato"), default=0, editable=False)
    blocked_count = models.PositiveIntegerField(_("Bot bloklangan"), default=0, editable=False)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='telegram_campaigns_created',
        verbose_name=_("Yaratdi"), editable=False,
    )
    queued_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='telegram_campaigns_queued', verbose_name=_("Navbatga qo'ydi"), editable=False,
    )
    created_at = models.DateTimeField(_("Yaratilgan vaqt"), auto_now_add=True)
    queued_at = models.DateTimeField(_("Navbatga qo'yilgan vaqt"), null=True, blank=True, editable=False)
    started_at = models.DateTimeField(_("Boshlangan vaqt"), null=True, blank=True, editable=False)
    completed_at = models.DateTimeField(_("Yakunlangan vaqt"), null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(_("Yangilangan vaqt"), auto_now=True)

    class Meta:
        verbose_name = _("Telegram xabari")
        verbose_name_plural = _("Telegram reklama va xabarlar")
        ordering = ('-created_at', '-id')

    def clean(self):
        super().clean()
        if bool(self.button_text) != bool(self.button_url):
            raise ValidationError(_("Tugma matni va havolasini birga kiriting."))
        if self.button_opens_mini_app and not self.button_url:
            raise ValidationError({
                'button_url': _("Mini App tugmasi uchun havola majburiy."),
            })

    @property
    def processed_count(self):
        return self.sent_count + self.failed_count + self.blocked_count

    @property
    def progress_percent(self):
        if not self.total_recipients:
            return 0
        return round(self.processed_count * 100 / self.total_recipients)

    def __str__(self):
        return self.title


class TelegramCampaignRecipient(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_SENDING = 'sending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_BLOCKED = 'blocked'
    STATUSES = (
        (STATUS_QUEUED, _("Navbatda")),
        (STATUS_SENDING, _("Yuborilmoqda")),
        (STATUS_SENT, _("Yuborildi")),
        (STATUS_FAILED, _("Xato")),
        (STATUS_BLOCKED, _("Bot bloklangan")),
    )

    campaign = models.ForeignKey(
        TelegramCampaign, on_delete=models.CASCADE, related_name='recipients',
        verbose_name=_("Kampaniya"),
    )
    telegram_user = models.ForeignKey(
        TelegramUser, on_delete=models.PROTECT, related_name='campaign_deliveries',
        verbose_name=_("Telegram foydalanuvchi"),
    )
    status = models.CharField(
        _("Holati"), max_length=12, choices=STATUSES, default=STATUS_QUEUED,
    )
    attempts = models.PositiveSmallIntegerField(_("Urinishlar"), default=0)
    error_message = models.CharField(_("Xato tafsiloti"), max_length=500, blank=True)
    last_attempt_at = models.DateTimeField(_("Oxirgi urinish"), null=True, blank=True)
    sent_at = models.DateTimeField(_("Yuborilgan vaqt"), null=True, blank=True)

    class Meta:
        verbose_name = _("Telegram xabar qabul qiluvchisi")
        verbose_name_plural = _("Telegram xabar qabul qiluvchilar")
        ordering = ('id',)
        constraints = [
            models.UniqueConstraint(
                fields=('campaign', 'telegram_user'), name='uniq_campaign_telegram_user',
            ),
        ]

    def __str__(self):
        return f"{self.campaign} — {self.telegram_user}"


class MiniAppPurchase(models.Model):
    TYPE_SELF = 'self'
    TYPE_FAMILY = 'family'
    PURCHASE_TYPES = (
        (TYPE_SELF, _("O'zim uchun")),
        (TYPE_FAMILY, _("Oila uchun")),
    )

    PAYMENT_PENDING = 'pending'
    PAYMENT_SUCCESS = 'success'
    PAYMENT_FAILED = 'failed'
    PAYMENT_REFUNDED = 'refunded'
    PAYMENT_STATUSES = (
        (PAYMENT_PENDING, _("To'lov kutilmoqda")),
        (PAYMENT_SUCCESS, _("To'langan")),
        (PAYMENT_FAILED, _("To'lov amalga oshmadi")),
        (PAYMENT_REFUNDED, _("To'lov qaytarilgan")),
    )

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    telegram_user = models.ForeignKey(
        TelegramUser,
        on_delete=models.PROTECT,
        related_name='purchases',
        verbose_name=_("Telegram foydalanuvchi"),
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.PROTECT,
        related_name='mini_app_purchases',
        verbose_name=_("Kurs"),
    )
    purchase_type = models.CharField(
        _("Xarid turi"), max_length=10, choices=PURCHASE_TYPES, default=TYPE_SELF,
    )
    unit_price = models.DecimalField(_("Bir kishi uchun narx"), max_digits=12, decimal_places=2)
    participant_count = models.PositiveSmallIntegerField(_("Ishtirokchilar soni"), default=1)
    total_amount = models.DecimalField(_("Jami summa"), max_digits=14, decimal_places=2)
    payment_status = models.CharField(
        _("To'lov holati"), max_length=10, choices=PAYMENT_STATUSES, default=PAYMENT_PENDING,
    )
    payment_provider = models.CharField(
        _("To'lov provayderi"), max_length=30, blank=True, default='',
    )
    payment_reference = models.CharField(_("To'lov identifikatori"), max_length=100, blank=True)
    paid_at = models.DateTimeField(_("To'langan vaqt"), null=True, blank=True)
    questionnaire_completed = models.BooleanField(_("Anketa yakunlangan"), default=False)
    created_at = models.DateTimeField(_("Yaratilgan vaqt"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Yangilangan vaqt"), auto_now=True)

    class Meta:
        verbose_name = _("Mini App xaridi")
        verbose_name_plural = _("Mini App xaridlari")
        ordering = ('-created_at', '-id')

    def __str__(self):
        return f"{self.telegram_user} - {self.course} - {self.total_amount}"

    def mark_paid(self, reference=''):
        self.payment_status = self.PAYMENT_SUCCESS
        self.payment_reference = reference or f"DEMO-{self.pk}"
        self.paid_at = timezone.now()
        self.save(update_fields=('payment_status', 'payment_reference', 'paid_at', 'updated_at'))


class MulticardInvoice(models.Model):
    """One durable invoice per purchase; never recreate an ambiguous API request."""

    purchase = models.OneToOneField(MiniAppPurchase, on_delete=models.PROTECT, related_name='multicard_invoice')
    invoice_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    provider_uuid = models.UUIDField(null=True, blank=True, unique=True)
    payment_uuid = models.UUIDField(null=True, blank=True, unique=True)
    store_id = models.CharField(max_length=100)
    amount = models.PositiveBigIntegerField(help_text='UZS tiyin')
    checkout_url = models.URLField(max_length=2000, blank=True)
    receipt_url = models.URLField(max_length=2000, blank=True)
    state = models.CharField(max_length=20, default='creating', choices=(
        ('creating', 'Creating'), ('ready', 'Ready'), ('uncertain', 'Needs reconciliation'),
        ('success', 'Paid'), ('error', 'Failed'), ('revert', 'Refunded'),
    ))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class MiniAppPurchaseMember(models.Model):
    RELATION_SELF = 'self'
    RELATION_FAMILY = 'family'
    RELATIONSHIPS = (
        (RELATION_SELF, _("Xaridor")),
        (RELATION_FAMILY, _("Oila a'zosi")),
    )

    purchase = models.ForeignKey(
        MiniAppPurchase,
        on_delete=models.CASCADE,
        related_name='members',
        verbose_name=_("Xarid"),
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mini_app_enrollments',
        verbose_name=_("CRM mijoz"),
    )
    full_name = models.CharField(_("To'liq ism"), max_length=255)
    phone_number = models.CharField(_("Telefon raqami"), max_length=20)
    relationship = models.CharField(
        _("Ishtirokchi turi"), max_length=10, choices=RELATIONSHIPS, default=RELATION_FAMILY,
    )
    created_at = models.DateTimeField(_("Yaratilgan vaqt"), auto_now_add=True)

    class Meta:
        verbose_name = _("Xarid ishtirokchisi")
        verbose_name_plural = _("Xarid ishtirokchilari")
        ordering = ('id',)
        constraints = [
            models.UniqueConstraint(
                fields=('purchase', 'phone_number'), name='uniq_purchase_member_phone',
            ),
        ]

    def __str__(self):
        return self.full_name


class LegalAcceptance(models.Model):
    DOCUMENT_TERMS = 'terms'
    DOCUMENT_CONTRACT = 'contract'
    DOCUMENT_TYPES = (
        (DOCUMENT_TERMS, _("Foydalanish shartlari")),
        (DOCUMENT_CONTRACT, _("Sog'lomlashtirish xizmatlari shartnomasi")),
    )

    telegram_user = models.ForeignKey(
        TelegramUser,
        on_delete=models.PROTECT,
        related_name='legal_acceptances',
        verbose_name=_("Telegram foydalanuvchi"),
    )
    purchase = models.ForeignKey(
        MiniAppPurchase,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='legal_acceptances',
        verbose_name=_("Xarid"),
    )
    document_type = models.CharField(
        _("Hujjat turi"), max_length=16, choices=DOCUMENT_TYPES,
    )
    version = models.CharField(_("Hujjat versiyasi"), max_length=32)
    document_hash = models.CharField(
        _("Qabul qilingan matn SHA-256"), max_length=64, editable=False,
    )
    accepted_at = models.DateTimeField(_("Qabul qilingan vaqt"), auto_now_add=True)
    ip_address = models.GenericIPAddressField(_("IP manzil"), null=True, blank=True)
    user_agent = models.CharField(_("Qurilma ma'lumoti"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("Yuridik rozilik")
        verbose_name_plural = _("Yuridik roziliklar")
        ordering = ('-accepted_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=('telegram_user', 'document_type', 'version'),
                condition=models.Q(purchase__isnull=True),
                name='uniq_account_legal_version',
            ),
            models.UniqueConstraint(
                fields=('purchase', 'document_type', 'version'),
                condition=models.Q(purchase__isnull=False),
                name='uniq_purchase_legal_version',
            ),
        ]

    def clean(self):
        super().clean()
        if self.document_type == self.DOCUMENT_TERMS and self.purchase_id:
            raise ValidationError({'purchase': _("Foydalanish shartlari xaridga bog'lanmaydi.")})
        if self.document_type == self.DOCUMENT_CONTRACT and not self.purchase_id:
            raise ValidationError({'purchase': _("Shartnoma uchun xarid majburiy.")})
        if self.purchase_id and self.purchase.telegram_user_id != self.telegram_user_id:
            raise ValidationError({'purchase': _("Xarid boshqa Telegram foydalanuvchiga tegishli.")})

    def __str__(self):
        return f"{self.telegram_user} — {self.get_document_type_display()} ({self.version})"


class EnrollmentQuestionnaire(models.Model):
    member = models.OneToOneField(
        MiniAppPurchaseMember,
        on_delete=models.CASCADE,
        related_name='questionnaire',
        verbose_name=_("Ishtirokchi"),
    )
    birth_date = models.DateField(_("Tug'ilgan sana"))
    city = models.CharField(_("Shahar / tuman"), max_length=120)
    occupation = models.CharField(_("Kasb / faoliyat"), max_length=160, blank=True)
    learning_goal = models.TextField(_("Kursdan maqsad"))
    prior_experience = models.TextField(_("Oldingi tajriba"), blank=True)
    health_notes = models.TextField(_("Muhim sog'liq izohlari"), blank=True)
    consent = models.BooleanField(_("Ma'lumotlar to'g'riligini tasdiqladi"), default=False)
    completed_at = models.DateTimeField(_("Yakunlangan vaqt"), default=timezone.now)

    class Meta:
        verbose_name = _("Kurs anketasi")
        verbose_name_plural = _("Kurs anketalari")
        ordering = ('-completed_at', '-id')

    def __str__(self):
        return f"{self.member.full_name} - {self.member.purchase.course}"


def _sub_transactions_with_status(transaction, status):
    """Prefetch cache mavjud bo'lsa undan, aks holda DB dan filtrlaydi."""
    prefetched = getattr(transaction, '_prefetched_objects_cache', {}).get('sub_transactions')
    if prefetched is None:
        return list(transaction.sub_transactions.filter(status=status).prefetch_related('clients'))
    return [item for item in prefetched if item.status == status]


def sub_transaction_shares(sub_transactions, allowed_client_ids=None):
    """Ichki to'lovlar summasini tanlangan mijozlar orasida teng taqsimlaydi.

    `allowed_client_ids` berilsa, o'sha ro'yxatdagi mijozlargina hisobga
    olinadi (masalan tranzaksiyadan chiqarib yuborilgan mijoz uchun pul
    yo'qolib qolmasligi uchun).
    """
    shares = {}
    for sub_transaction in sub_transactions:
        selected_ids = sorted(
            client.pk for client in sub_transaction.clients.all()
            if allowed_client_ids is None or client.pk in allowed_client_ids
        )
        if not selected_ids:
            continue
        for client_id, share in zip(selected_ids, _split_amount(sub_transaction.amount, len(selected_ids))):
            shares[client_id] = shares.get(client_id, Decimal(0)) + share
    return shares


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
    # Faqat tasdiqlangan ichki to'lovlar pul sifatida hisobga olinadi.
    sub_transactions = _sub_transactions_with_status(transaction, SubTransaction.STATUS_APPROVED)
    sub_total = sum((item.amount for item in sub_transactions), Decimal(0))
    base_amount = max(Decimal(str(transaction.amount or 0)) - sub_total, Decimal(0))
    amount_by_client = {
        participant.client_id: share
        for participant, share in zip(participants, _split_amount(base_amount, n))
    }

    # Asosiy summa barcha ishtirokchilarga, har bir ichki to'lov esa faqat
    # o'sha qabulda tanlangan mijozlarga teng taqsimlanadi.
    for client_id, share in sub_transaction_shares(sub_transactions, set(amount_by_client)).items():
        amount_by_client[client_id] += share

    discount_shares = _split_amount(transaction.discount_total, n)

    for tc, d_share in zip(participants, discount_shares):
        a_share = amount_by_client[tc.client_id]
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
            client_id=client_id,
            transaction__group_id=group_id,
            transaction__is_confirmed=True,
            transaction__is_refunded=False,
        ).select_related('transaction__group__course').order_by('transaction__date', 'transaction_id')
    )

    # Tasdiqlanmagan yoki qaytarilgan tranzaksiyalar qarzni saqlab qolmasligi kerak.
    TransactionClient.objects.filter(
        client_id=client_id,
        transaction__group_id=group_id,
    ).filter(
        models.Q(transaction__is_confirmed=False) | models.Q(transaction__is_refunded=True)
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
