"""
Authentication views with role-based routing for EMI Management System.
"""

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.db import transaction
from django.shortcuts import render, redirect
from django.urls import reverse

from shops.models import Shop
from .models import User


class RoleBasedLoginView(auth_views.LoginView):
    """
    Custom Login View with intelligent role-based redirection.
    - Administrators are redirected to the Admin Dashboard.
    - Shop Owners are redirected directly to their Shop Dashboard.
    - Follow-up staff are redirected to their Follow-up Portal.
    Prevents 403 Forbidden redirects when a non-admin logs in with an admin next URL.
    Supports optional explicit role verification from the login dropdown.
    """
    template_name = "registration/login.html"

    def form_valid(self, form):
        selected_role = self.request.POST.get("selected_role", "").strip()
        user = form.get_user()
        if selected_role:
            if selected_role == "ADMIN" and not (user.is_admin_role or user.is_superuser):
                form.add_error(None, "Access Denied: Your account is not authorized as an Administrator.")
                return self.form_invalid(form)
            elif selected_role == "FOLLOWUP" and not user.is_followup_role:
                form.add_error(None, "Access Denied: Your account is not authorized as Follow-up Staff.")
                return self.form_invalid(form)
            elif selected_role == "SHOP_OWNER" and not user.is_shop_owner_role:
                form.add_error(None, "Access Denied: Your account is not authorized as a Retail Partner Shop.")
                return self.form_invalid(form)

        # Handle Remember Me checkbox
        remember_me = self.request.POST.get("remember_me")
        if remember_me:
            self.request.session.set_expiry(1209600)  # 2 weeks
        else:
            self.request.session.set_expiry(0)  # Browser session only

        return super().form_valid(form)

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


def shop_signup_view(request):
    """
    Public registration portal for partner retail mobile shops.
    Creates a User account (Role: SHOP_OWNER) and registers the Shop entity.
    """
    if request.user.is_authenticated:
        return redirect("root")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        password_confirm = request.POST.get("password_confirm", "").strip()
        shop_name = request.POST.get("shop_name", "").strip()
        owner_name = request.POST.get("owner_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        email = request.POST.get("email", "").strip()
        address = request.POST.get("address", "").strip()
        gpay = request.POST.get("gpay", "").strip()

        errors = []
        if not username:
            errors.append("Username is required.")
        elif User.objects.filter(username__iexact=username).exists():
            errors.append("This username is already taken. Please choose another.")

        if not password:
            errors.append("Password is required.")
        elif len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        elif password != password_confirm:
            errors.append("Passwords do not match.")

        if not shop_name:
            errors.append("Shop name is required.")
        if not owner_name:
            errors.append("Owner / contact person name is required.")
        if not phone:
            errors.append("Contact phone number is required.")
        if not address:
            errors.append("Shop retail address is required.")

        if errors:
            return render(request, "registration/signup.html", {
                "errors": errors,
                "form_data": request.POST,
            })

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    email=email,
                    role=User.Role.SHOP_OWNER,
                    phone=phone,
                    first_name=owner_name,
                )
                shop = Shop.objects.create(
                    name=shop_name,
                    owner_name=owner_name,
                    phone=phone,
                    email=email,
                    address=address,
                    gpay=gpay,
                    owner_account=user,
                    assigned_password=password,
                    is_active=True,
                )

            auth_login(request, user)
            messages.success(request, f"Welcome to EMI SAFE, {shop.name}! Your partner store account is now active.")
            return redirect("shop_dashboard")
        except Exception as e:
            return render(request, "registration/signup.html", {
                "errors": [f"An error occurred while creating your account: {str(e)}"],
                "form_data": request.POST,
            })

    return render(request, "registration/signup.html")
