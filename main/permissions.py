def is_not_plain_operator(request):
    """
    Returns True if the user is a superuser or is not linked to any Operator model.
    This hides links from plain operators while keeping them visible for admins.
    """
    return request.user.is_superuser or not hasattr(request.user, 'operator')
