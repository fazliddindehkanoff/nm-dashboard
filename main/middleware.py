from django.shortcuts import redirect

class OperatorRedirectMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            is_plain_op = not request.user.is_superuser and hasattr(request.user, 'operator')
            if is_plain_op:
                # Redirect if attempting to access restricted admin home or operator page
                if request.path in ['/admin', '/admin/'] or request.path.startswith('/admin/main/operator/'):
                    return redirect('/admin/main/transaction/')

        response = self.get_response(request)
        return response
