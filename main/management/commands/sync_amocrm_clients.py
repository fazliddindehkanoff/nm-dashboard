from django.core.management.base import BaseCommand, CommandError

from main.services.amocrm import sync_contacts, AmoCRMNotConfigured


class Command(BaseCommand):
    help = "amoCRM kontaktlarini Mijozlar (Client) ro'yxatiga sinxronlaydi."

    def handle(self, *args, **options):
        try:
            result = sync_contacts(logger=lambda msg: self.stdout.write("  " + msg))
        except AmoCRMNotConfigured as exc:
            raise CommandError(str(exc))
        except Exception as exc:  # amoCRM API xatolari
            raise CommandError(f"amoCRM bilan bog'lanishda xatolik: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Tayyor: {result['created']} ta yangi, {result['updated']} ta yangilandi."
            )
        )
