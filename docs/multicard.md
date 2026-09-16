# Multicard payments for the Telegram Mini App

The Mini App uses Multicard hosted checkout. Prices come from the saved purchase;
UZS is converted to integer tiyin (1 UZS = 100 tiyin). Multicard collects card details.
A signed success callback commits the payment and links each participant to a CRM
client. The questionnaire remains locked until payment is confirmed. Purchases
and references are visible under **Mini App xaridlari** in the admin.

## Booking and installments

New checkouts offer booking or the existing full-payment flow. Booking requires
100,000 UZS per participant (including the buyer): two people must pay at least
200,000 UZS. The buyer can enter a larger amount up to the discounted balance.
Later installments use the same minimum, except that the entire final balance
can be paid when it is smaller. The CRM transaction/payment workflow is unchanged.

The active **Bron chegirmasi** amount is snapshotted per participant when creating
the purchase, following the same course-specific-first selection as the CRM. Existing automatic
participant discounts remain in the saved course price; the booking discount is
additional and is capped at that price. It is
applied exactly once after the first confirmed payment, not when checkout opens.
For a 1,500,000 UZS course and 200,000 UZS booking discount, a 100,000 UZS payment
leaves 1,200,000 UZS due. Later edits to prices or discounts do not change that
purchase. Inactive/non-booking discounts are ignored. A booking whose discounted
price is below the minimum is rejected; full payment remains available.

Each installment has its own immutable Multicard invoice; only one invoice can
be open per purchase. Repeated callbacks cannot add the payment twice. The API
requires the client's last `expected_paid` balance for booking payments, so a
stale retry after payment cannot create another installment. Uncertain invoices
must be reconciled before proceeding. An installment receipt contains the amount
actually being paid rather than the full course fee.

Confirmed booking payments unlock the questionnaire. The purchase remains
`partial` until the discounted balance reaches zero, and the home screen keeps
the remaining balance accessible for later payments. Partial refunds preserve
the other settled installments; refunding all payments removes the discount and
marks the purchase refunded. Existing paid purchases retain their paid balance
through migration `0028`; no historical booking discounts are added.

Booking contracts have their own version; existing full-payment acceptances stay
valid. Apply migration `0028`, restart the app, and collect static assets together
when deploying. Local verification uses mocked provider responses and demo
payments; no real cards are charged by tests.

## Configuration

Set these environment variables on the Django server (or in the ignored `.env`).
Use credentials issued to your merchant; the public documentation's example
credentials are not used by this integration.

```dotenv
MULTICARD_ENABLED=false
MULTICARD_BASE_URL=https://dev-mesh.multicard.uz
MULTICARD_APPLICATION_ID=
MULTICARD_SECRET=
MULTICARD_STORE_ID=
MULTICARD_CALLBACK_URL=https://crm.norbekovmarkazi.uz/payments/multicard/callback/
MULTICARD_RETURN_URL=https://t.me/YOUR_BOT?start=payment
MULTICARD_OFD_MXIK=
MULTICARD_OFD_PACKAGE_CODE=
MULTICARD_OFD_VAT=
```

- `STORE_ID`: the **numeric** cash register/store ID issued by Multicard.
- `OFD_MXIK` and `OFD_PACKAGE_CODE`: the correct course/service fiscal codes from
  your merchant setup. A single receipt line uses the participant count, unit
  price and course name. If courses require different fiscal classifications,
  configure per-course codes before selling them through this integration.
- `OFD_VAT`: optional integer VAT percentage; blank omits it. Do not assume zero
  unless that matches the merchant's tax configuration.
- `CALLBACK_URL`: a public HTTPS endpoint accessible without login, redirects or
  CSRF enforcement. The trailing slash is required. Authentication uses the
  documented MD5 signature and matches the stored invoice, store and amount.
- `RETURN_URL`: an HTTPS bot deep link or configured Mini App deep link. With
  `?start=payment`, the existing bot welcome message lets the payer reopen the
  Mini App and resume their purchase. A return redirect itself never confirms payment.

Configure Multicard for **Callback (success)** mode. This endpoint deliberately
rejects the separate SHA1 status-webhook format. Keeping the two protocols
separate prevents accepting an unsigned status transition.

Apply the migration and restart the application:

```sh
python manage.py migrate
python manage.py check
```

Then set `MULTICARD_ENABLED=true`. Sandbox remains the default endpoint. Once the
sandbox checkout, cancellation, repeated callback, and receipt have been verified
with the merchant account, use production credentials and
`MULTICARD_BASE_URL=https://mesh.multicard.uz`.

## Verification and recovery

```sh
python manage.py test main.test_multicard main.test_telegram_app
python manage.py reconcile_multicard
python manage.py reconcile_multicard --include-paid
python manage.py reconcile_multicard --purchase-id 123
```

The reconciliation command only reads provider state; it never charges cards or
issues refunds. Run it periodically using your deployment scheduler. Include paid
invoices when checking for refunds performed in the merchant portal. With success
callback mode, refunds are reflected locally at the next reconciliation, not by a
refund webhook. The user's **To‘lov holatini tekshirish** button also reconciles the
selected invoice. Automatic UI polling reads local state only.

A timeout or malformed response during invoice creation leaves a durable
`uncertain` record. Repeated clicks do not create another invoice. Find the invoice
in the Multicard merchant portal using the local `invoice_id` shown in the admin,
then attach its provider UUID with:

```sh
python manage.py reconcile_multicard --purchase-id 123 --provider-uuid PROVIDER_UUID
```

If the purchase has multiple installments, select the exact local invoice row:

```sh
python manage.py reconcile_multicard --invoice-id 456 --provider-uuid PROVIDER_UUID
```

Recovery verifies the provider's invoice ID, store and amount before saving the
checkout URL. If the provider has no invoice, resolve that with Multicard support
before starting another purchase. An expired checkout requires a new purchase
after confirming the original was not paid. Invoice POST requests are never
blindly retried; no undocumented idempotency behavior is assumed.

The demo endpoint is separate (`/demo-payment/`) and only accepts the fixed local
demo account while Django `DEBUG=True`. Real Telegram users cannot simulate payment.

## API references

- [Overview and environments](https://docs.multicard.uz/)
- [Authentication](https://docs.multicard.uz/получение-токена-19729295e0)
- [Create invoice](https://docs.multicard.uz/создание-инвойса-19729296e0)
- [Read invoice](https://docs.multicard.uz/получение-информации-о-созданном-инвойсе-19729297e0)
- [Success callback and retry behavior](https://docs.multicard.uz/callback-success-19729300e0)
