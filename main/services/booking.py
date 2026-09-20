"""Server-side prices and installment validation for the Telegram checkout."""
from decimal import Decimal, InvalidOperation

from django.db.models import F

from main.models import Discount


def booking_discount_for(unit_price, participant_count, course_id):
    discount = Discount.for_course(course_id).filter(is_booking=True).order_by(
        F('course_id').desc(nulls_last=True), '-amount', 'pk',
    ).first()
    per_person = max(Decimal(discount.amount), Decimal(0)) if discount else Decimal(0)
    return min(per_person, Decimal(unit_price)) * participant_count


def payment_amount(purchase, value=None, *, minimum=None):
    maximum = purchase.payable_amount
    minimum = purchase.minimum_payment if minimum is None else minimum
    try:
        if purchase.is_booking and value is None:
            raise ValueError
        amount = maximum if value is None else Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("To'lov summasini so'mda, ko'pi bilan 2 kasr xonasi bilan kiriting.") from None
    if amount > maximum:
        raise ValueError("To'lov summasi qolgan qarzdan oshmasligi kerak.")
    if amount < minimum:
        raise ValueError(f"Eng kam to'lov: {minimum:,.0f} so'm.")
    return amount


def check_payment_version(purchase, expected_paid):
    """A stale retry must not create a second installment after settlement."""
    try:
        matches = Decimal(str(expected_paid)) == purchase.paid_amount
    except (InvalidOperation, TypeError, ValueError):
        matches = False
    if not matches:
        raise ValueError("To'lov holati o'zgargan. Holatni yangilab, summani qayta tekshiring.")
