from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .permissions import is_operator

class OperatorRedirectMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            if (
                is_operator(request.user)
                and request.path.startswith('/admin/main/operator/')
            ):
                return redirect('/admin/main/transaction/')
            if request.path in ['/admin', '/admin/'] and not request.user.has_perm('main.access_dashboard'):
                if request.user.has_perm('main.access_cashflow'):
                    return redirect('/cashflow/')
                if request.user.has_perm('main.view_transaction'):
                    return redirect('/admin/main/transaction/')
                if request.user.has_perm('main.view_expense'):
                    return redirect('/admin/main/expense/')
                raise PermissionDenied

        response = self.get_response(request)
        return response
