import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from main.models import Course, Discount


class Command(BaseCommand):
    help = 'Import the 13 courses and standard discount rules from Kurs_narxlari.xlsx.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--health-family-discount', type=int, choices=(100000, 200000), default=100000,
            help='Resolve conflicting health-course family prices; defaults to the main table.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(__file__).resolve().parents[2] / 'data' / 'course_prices.json'
        rows = json.loads(path.read_text(encoding='utf-8'))['courses']
        created_count = 0
        for index, row in enumerate(rows):
            matches = Course.objects.filter(name__in=[row['name'], *row.get('aliases', [])])
            if matches.count() > 1:
                raise CommandError(f"Multiple courses match {row['name']!r}; resolve duplicates before importing.")
            course = matches.first()
            created = course is None
            if created:
                course = Course.objects.create(name=row['name'], price=row['price'])
            else:
                course.name = row['name']
                course.price = row['price']
                course.save(update_fields=('name', 'price'))
            created_count += int(created)
            family_amount = options['health_family_discount'] if index == 0 else row['participant_discount']
            for name, amount, booking, minimum in (
                ('Oldindan bron', row['booking_discount'], True, None),
                ('Bir oiladan 2 va undan ortiq kishi', family_amount, False, 2),
            ):
                if amount:
                    Discount.objects.update_or_create(
                        course=course, name=name,
                        defaults={'amount': amount, 'is_booking': booking,
                                  'min_participants': minimum, 'is_active': True},
                    )
            self.stdout.write(f"{'Created' if created else 'Updated'}: {course.name} — {course.price} UZS")
        if options['dry_run']:
            transaction.set_rollback(True)
            self.stdout.write('Dry run: no changes saved.')
        self.stdout.write(self.style.SUCCESS(
            f'{len(rows)} courses: {created_count} new, {len(rows) - created_count} existing. No groups created.',
        ))
