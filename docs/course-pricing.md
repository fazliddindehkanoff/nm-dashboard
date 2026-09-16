# Course catalog and discount rules

Run `python manage.py migrate`, then `python manage.py import_course_prices --dry-run`
to review the import. Run `python manage.py import_course_prices` to save it.
The command imports the 13 courses in `Kurs_narxlari.xlsx`, sheet `Курс нархлари`,
rows 3–15, with their original names and prices. It matches exact names and the
explicit legacy Latin-script aliases verified on production. Matching records
retain their IDs, durations and group links and adopt the workbook name and price.
Unrelated records are preserved, and ambiguous matches stop the import.
Repeated runs update those same records. It does not create groups or invent dates
or durations. Set actual durations and upcoming groups in the admin.

The main table lists a 100,000 UZS family discount for the first health course.
The later family section instead gives a 1,800,000 UZS price, implying 200,000 off.
The import defaults to the main table; use `--health-family-discount 200000` if the
special family price is intended. The later social concession and its different
booking amount are not automatically imported or inferred from participant count.

The web catalog and new purchases require at least one active group whose start
date is strictly after today in Asia/Tashkent. Groups starting today are already
started for this purpose. A course remains visible while another qualifying group
exists. Existing purchases and attendance remain accessible.

In Discounts, select a course (blank means all courses), an amount, and the minimum
participant count, e.g. 2. Only active rules apply. The amount is deducted for each
person in the same purchase, including the buyer. If several rules qualify, the
largest discount applies once. No minimum means a manual discount. Booking rules are separate. They apply to admin reservations and, after the first
confirmed installment, to Mini App booking purchases (see multicard.md). Full-payment
Mini App purchases retain their existing participant-discount rules. The workbook's standard booking discounts and three family rules are
imported. Existing global rules still apply to all courses.

Mini App totals are calculated on the server using validated participants, ignoring
submitted prices. The effective unit price, discount amount, and rule name are
saved with the purchase and used for contracts and payment invoices. Later rule
edits do not change that saved quote. The web checkout displays the current rule
estimate and the final saved discount in the payment summary.

Admin transactions also apply participant rules after saving participant rows.
They use the larger of the manual additional discount or the automatic participant
discount, plus any booking discount. Course-specific booking amounts apply per
person; older global booking amounts retain their per-transaction behavior.
Top-up payments (`doplata`) do not grant the automatic participant discount again.
