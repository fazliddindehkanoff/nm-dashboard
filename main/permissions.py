from functools import wraps

from django.core.exceptions import PermissionDenied

from .models import RoleConfiguration


def user_role(user):
    if user.is_superuser:
        return RoleConfiguration.ROLE_ADMIN
    profile = getattr(user, 'operator', None)
    return profile.role if profile else None


def has_role(user, *roles):
    return user_role(user) in roles


def is_operator(user):
    return has_role(user, RoleConfiguration.ROLE_OPERATOR)


def is_owner(user):
    return has_role(user, RoleConfiguration.ROLE_OWNER)


def is_accountant(user):
    return has_role(user, RoleConfiguration.ROLE_ACCOUNTANT)


def _has_permission(request, permission):
    return request.user.is_active and request.user.has_perm(permission)


def can_access_dashboard(request):
    return _has_permission(request, 'main.access_dashboard')


def can_access_salaries(request):
    return _has_permission(request, 'main.access_salary_report')


def can_access_qr(request):
    return _has_permission(request, 'main.access_qr_scanner')


def can_access_cashflow(request):
    return _has_permission(request, 'main.access_cashflow')


def can_view_courses(request):
    return _has_permission(request, 'main.view_course')


def can_view_groups(request):
    return _has_permission(request, 'main.view_group')


def can_view_clients(request):
    return _has_permission(request, 'main.view_client')


def can_manage_users(request):
    return has_role(request.user, RoleConfiguration.ROLE_ADMIN)


def can_view_discounts(request):
    return _has_permission(request, 'main.view_discount')


def can_view_transactions(request):
    return _has_permission(request, 'main.view_transaction')


def can_view_subtransactions(request):
    return _has_permission(request, 'main.view_subtransaction')


def can_view_expenses(request):
    return _has_permission(request, 'main.view_expense')


def can_view_attendance(request):
    return _has_permission(request, 'main.view_attendancerecord')


def permission_required(permission):
    """Custom hisobot sahifalarini ham server tomonda himoyalaydi."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.has_perm(permission):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def is_not_plain_operator(request):
    """Eski sozlamalar bilan moslik uchun: operator bo'lmagan foydalanuvchi."""
    return not is_operator(request.user)
