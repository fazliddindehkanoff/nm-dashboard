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

class Group(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, verbose_name=_("Kurs"))
    name = models.CharField(_("Nomi"), max_length=255)
    price = models.DecimalField(
        _("Guruh narxi"), 
        max_digits=12, 
        decimal_places=2, 
        null=True, 
        blank=True, 
        help_text=_("Agar bo'sh qolsa, kurs narxi olinadi.")
    )

    class Meta:
        verbose_name = _("Guruh")
        verbose_name_plural = _("Guruhlar")

    def __str__(self):
        return f"{self.course.name} - {self.name}"

class Client(models.Model):
    full_name = models.CharField(_("Familiya-Ism"), max_length=255)
    phone_number = models.CharField(_("Telefon raqam"), max_length=20)

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

class Transaction(models.Model):
    PAYMENT_TYPES = (
        ('bron', _("Bron")),
        ('doplata', _("Doplata")),
        ('to_liq_tolov', _("To'liq to'lov")),
    )
    
    operator = models.ForeignKey(Operator, on_delete=models.SET_NULL, null=True, verbose_name=_("Operator"))
    client = models.ForeignKey(Client, on_delete=models.CASCADE, verbose_name=_("Mijoz"))
    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True, verbose_name=_("Guruh/Kurs nomi"))
    date = models.DateField(_("Sanasi"))
    amount = models.DecimalField(_("To'lov miqdori"), max_digits=12, decimal_places=2)
    payment_type = models.CharField(_("To'lov turi"), max_length=20, choices=PAYMENT_TYPES)
    
    course_price = models.DecimalField(_("Kurs narxi"), max_digits=12, decimal_places=2, editable=False, default=0)
    debt = models.DecimalField(_("Qarzi"), max_digits=12, decimal_places=2, editable=False, default=0)

    class Meta:
        verbose_name = _("To'lov")
        verbose_name_plural = _("To'lovlar")

    def save(self, *args, **kwargs):
        if self.group:
            self.course_price = self.group.price if self.group.price is not None else self.group.course.price
        else:
            self.course_price = 0
            
        if self.payment_type == 'bron':
            self.debt = self.course_price - self.amount
        elif self.payment_type == 'to_liq_tolov':
            self.debt = 0
        else:
            # For 'doplata', we don't automatically calculate debt without knowing previous bron,
            # but we can just set it to 0 or leave it to be updated separately.
            self.debt = 0
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.client.full_name} - {self.amount}"
