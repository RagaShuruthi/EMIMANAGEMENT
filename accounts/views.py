"""
Authentication views with role-based routing for EMI Management System.
"""

from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.urls import reverse


class RoleBasedLoginView(auth_views.LoginView):
    """
    Custom Login View with intelligent role-based redirection.
    - Administrators are redirected to the Admin Dashboard.
    - Shop Owners are redirected directly to their Shop Dashboard.
    Prevents 403 Forbidden redirects when a non-admin logs in with an admin next URL.
    """
    template_name = "registration/login.html"

    def get_success_url(self):
        user = self.request.user
        next_url = self.request.POST.get(self.redirect_field_name) or self.request.GET.get(self.redirect_field_name)

        # If a non-admin was redirected from an admin page, clear it so they go to shop dashboard
        if next_url and "/admin-dashboard/" in next_url:
            if not (user.is_admin_role or user.is_superuser):
                next_url = None

        if next_url:
            return next_url

        if user.is_admin_role or user.is_superuser:
            return reverse("admin_dashboard")
        if user.is_followup_role:
            return reverse("followup_dashboard")

        return reverse("shop_dashboard")


def role_redirect_view(request):
    """
    Routes users arriving at root '/' to their respective dashboard based on authenticated role.
    """
    if not request.user.is_authenticated:
        return redirect("login")
    if request.user.is_admin_role or request.user.is_superuser:
        return redirect("admin_dashboard")
    if request.user.is_followup_role:
        return redirect("followup_dashboard")
    return redirect("shop_dashboard")
