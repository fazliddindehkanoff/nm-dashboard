from django.contrib.auth.backends import ModelBackend

from .models import RoleConfiguration


class RolePermissionBackend(ModelBackend):
    """Django ruxsatlariga foydalanuvchining platforma roli ruxsatlarini qo'shadi."""

    def get_group_permissions(self, user_obj, obj=None):
        permissions = set(super().get_group_permissions(user_obj, obj=obj))
        if obj is not None or not user_obj.is_active or user_obj.is_anonymous:
            return permissions

        profile = getattr(user_obj, 'operator', None)
        if profile is None:
            return permissions

        if profile.role == RoleConfiguration.ROLE_ADMIN:
            return permissions | {
                f"{permission.content_type.app_label}.{permission.codename}"
                for permission in self._all_permissions()
            }

        try:
            role = RoleConfiguration.objects.get(code=profile.role)
        except RoleConfiguration.DoesNotExist:
            return permissions

        permissions.update(
            f"{app_label}.{codename}"
            for app_label, codename in role.permissions.values_list(
                'content_type__app_label', 'codename'
            )
        )
        return permissions

    @staticmethod
    def _all_permissions():
        from django.contrib.auth.models import Permission

        return Permission.objects.select_related('content_type').all()
