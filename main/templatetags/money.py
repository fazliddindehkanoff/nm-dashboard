from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def money(value):
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return value
    return f"{amount:,.0f}".replace(',', ' ')


@register.filter
def plain_amount(value):
    """A form value: no grouping, a dot for decimals and no ",00" for whole sums."""
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return value
    return f"{amount:.0f}" if amount == amount.to_integral_value() else f"{amount:.2f}"
