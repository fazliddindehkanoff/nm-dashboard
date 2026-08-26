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
