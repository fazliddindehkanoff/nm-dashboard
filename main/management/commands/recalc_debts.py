from django.core.management.base import BaseCommand
from django.db.models import Sum

from main.models import Transaction, _recalc_group_debt


class Command(BaseCommand):
    help = (
        "Barcha mijoz+guruh juftliklari uchun qarzni qayta hisoblaydi. "
        "Qarz hisoblash mantig'i o'zgargach eski to'lovlarni to'g'rilash uchun ishlatiladi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Hech narsani o'zgartirmasdan, jami qarz qanday o'zgarishini ko'rsatadi.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        before = Transaction.objects.aggregate(t=Sum("debt"))["t"] or 0

        pairs = (
            Transaction.objects.exclude(group__isnull=True)
            .values_list("client_id", "group_id")
            .distinct()
        )
        pairs = list(pairs)

        if dry_run:
            self.stdout.write(
                "DRY-RUN: {0} ta mijoz+guruh juftligi. Jami qarz hozir: {1}".format(
                    len(pairs), before
                )
            )
            return

        for client_id, group_id in pairs:
            _recalc_group_debt(client_id, group_id)

        after = Transaction.objects.aggregate(t=Sum("debt"))["t"] or 0
        self.stdout.write(
            self.style.SUCCESS(
                "Tayyor: {0} ta juftlik qayta hisoblandi. Jami qarz: {1} -> {2}".format(
                    len(pairs), before, after
                )
            )
        )
