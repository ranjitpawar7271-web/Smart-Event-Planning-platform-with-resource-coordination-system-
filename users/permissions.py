"""
Role-based access control helpers.

These are intentionally generic so every future module (venues, resources,
vendors, staff, budgets, tickets, ...) can reuse the same permission layer
instead of re-implementing role checks per-view.

Usage (function-based views):

    from users.permissions import role_required
    from users.models import User

    @role_required(User.SUPER_ADMIN, User.ORGANIZER)
    def some_view(request):
        ...

Usage (class-based views):

    from users.permissions import RoleRequiredMixin
    from users.models import User

    class SomeView(RoleRequiredMixin, View):
        allowed_roles = (User.SUPER_ADMIN, User.STAFF)
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse


def _has_role(user, allowed_roles):
    if not user.is_authenticated:
        return False
    # Superusers (Django admin / `createsuperuser`) always pass role
    # checks so ops/dev staff are never locked out of their own system.
    if user.is_superuser:
        return True
    return user.role in allowed_roles


def role_required(*allowed_roles, redirect_url='dashboard:dashboard'):
    """Function-view decorator restricting access to specific roles.

    Unauthenticated users are sent to login (like @login_required).
    Authenticated users without a matching role are redirected back
    with an error message rather than getting a raw 403, since most
    of these views are reached via in-app navigation.
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if not _has_role(request.user, allowed_roles):
                messages.error(request, "You don't have permission to access that page.")
                return redirect(redirect_url)
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def role_required_api(*allowed_roles):
    """Same as role_required but raises PermissionDenied (403) instead of
    redirecting — for API / JSON endpoints where a redirect makes no sense.
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if not _has_role(request.user, allowed_roles):
                raise PermissionDenied("You don't have permission to perform this action.")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


class RoleRequiredMixin:
    """Class-based-view mixin restricting access to `allowed_roles`."""

    allowed_roles = ()
    permission_redirect_url = 'dashboard:dashboard'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('users:login')}?next={request.path}")
        if not _has_role(request.user, self.allowed_roles):
            messages.error(request, "You don't have permission to access that page.")
            return redirect(self.permission_redirect_url)
        return super().dispatch(request, *args, **kwargs)
