from django.db.models import Max
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import AttendanceLesson, AttendanceRecord, Transaction, TransactionClient


def ensure_attendance_record(participation):
    transaction = participation.transaction
    if not transaction.group_id:
        return None
    created_by = transaction.operator.user if transaction.operator and transaction.operator.user_id else None
    record, _ = AttendanceRecord.objects.get_or_create(
        client_id=participation.client_id,
        group_id=transaction.group_id,
        defaults={'created_by': created_by},
    )
    return record


@receiver(post_save, sender=TransactionClient)
def create_attendance_for_participation(sender, instance, **kwargs):
    ensure_attendance_record(instance)


@receiver(post_save, sender=Transaction)
def create_attendance_after_group_change(sender, instance, **kwargs):
    if not instance.group_id:
        return
    for participation in instance.participants.select_related('transaction__operator__user'):
        ensure_attendance_record(participation)


def refresh_last_attended(record_id):
    last_date = AttendanceLesson.objects.filter(attendance_id=record_id).aggregate(
        last=Max('date')
    )['last']
    AttendanceRecord.objects.filter(pk=record_id).update(
        last_attended_at=last_date,
        updated_at=timezone.now(),
    )


@receiver(post_save, sender=AttendanceLesson)
def update_last_attended_on_save(sender, instance, **kwargs):
    refresh_last_attended(instance.attendance_id)


@receiver(post_delete, sender=AttendanceLesson)
def update_last_attended_on_delete(sender, instance, **kwargs):
    refresh_last_attended(instance.attendance_id)
