"""
Root URL configuration for the EMI Management System.

Includes Admin Dashboard, Shop Management, API v1, and modular apps.
"""

from django.contrib import admin
from django.urls import path, include
from accounts.views import role_redirect_view

urlpatterns = [
    # Intelligent Root Redirect based on user role
    path("", role_redirect_view, name="root"),

    # Django built-in admin
    path("admin/", admin.site.urls),

    # Admin Dashboard & Admin Shop Management
    path("admin-dashboard/", include("admin_dashboard.urls")),
    path("admin-dashboard/shops/", include("shops.urls")),

    # Shop Owner Portal & Dashboard (Role-based Portal for retail shops)
    path("shop-dashboard/", include("shops.shop_portal_urls")),
    path("shop/", include("shops.shop_portal_urls")),

    # Future Mobile APK API v1
    path("api/v1/shops/", include("shops.api_urls")),

    # Modular system apps
    path("accounts/", include("accounts.urls")),
    path("shops/", include("shops.urls")),
    path("customers/", include("customers.urls")),
    path("applications/", include("applications.urls")),
    path("payments/", include("payments.urls")),
    path("followups/", include("followups.urls")),
]
