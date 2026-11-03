"""
URL patterns for the Shop Owner Portal and Dashboard.
"""

from django.urls import path
from . import shop_portal_views

urlpatterns = [
    # Shop Dashboard Overview
    path("", shop_portal_views.shop_dashboard_view, name="shop_dashboard"),

    # Customer Registration & Directory
    path("customers/add/", shop_portal_views.customer_registration_view, name="shop_customer_add"),
    path("customers/", shop_portal_views.customer_list_view, name="shop_customer_list"),

    # Applications (with status filter options)
    path("applications/", shop_portal_views.application_list_view, name="shop_application_list"),
    path("applications/<int:pk>/", shop_portal_views.application_detail_view, name="shop_application_detail"),

    # Financier Payments & Reconciliation
    path("payments/", shop_portal_views.financier_payments_view, name="shop_payments"),

    # Reports & Analytics
    path("reports/", shop_portal_views.shop_reports_view, name="shop_reports"),

    # Shop Profile
    path("profile/", shop_portal_views.shop_profile_view, name="shop_profile"),
]
