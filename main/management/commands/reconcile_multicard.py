from django.core.management.base import BaseCommand, CommandError

from main.models import MulticardInvoice
from main.services.multicard import InvalidCallback, MulticardError, reconcile_invoice, recover_invoice


class Command(BaseCommand):
    help = 'Check Multicard invoices for missed payments/refunds without creating or charging payments.'

    def add_arguments(self, parser):
        parser.add_argument('--purchase-id', type=int)
        parser.add_argument('--provider-uuid', help='Recover an unknown invoice UUID found in the merchant portal; requires --purchase-id.')
        parser.add_argument('--include-paid', action='store_true', help='Also check paid invoices for refunds.')

    def handle(self, *args, **options):
        if options['provider_uuid'] and not options['purchase_id']:
            raise CommandError('--provider-uuid requires --purchase-id')
        invoices = MulticardInvoice.objects.order_by('id')
        if options['purchase_id']:
            invoices = invoices.filter(purchase_id=options['purchase_id'])
        elif not options['include_paid']:
            invoices = invoices.exclude(state__in=('success', 'revert'))
        failures = 0
        for invoice in invoices.iterator():
            try:
                if options['provider_uuid']:
                    invoice = recover_invoice(invoice, options['provider_uuid'])
                reconcile_invoice(invoice)
                invoice.refresh_from_db()
                self.stdout.write(f'Purchase {invoice.purchase_id}: {invoice.state}')
            except (MulticardError, InvalidCallback, ValueError) as exc:
                failures += 1
                self.stderr.write(f'Purchase {invoice.purchase_id}: {exc}')
        if failures:
            raise CommandError(f'{failures} invoice(s) need attention; no new invoices were created.')
