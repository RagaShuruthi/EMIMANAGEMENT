"""
URL routing for Admin Dashboard, Customer Management, Shop Payments, and Reports.
"""

from django.urls import path
from . import views
import followups.views as followup_views

urlpatterns = [
    # Main Admin Dashboard Overview
    path("", views.admin_dashboard_view, name="admin_dashboard"),

    # Applications Review & Approval (Pending, Approved, Rejected)
    path("applications/", views.admin_applications_list_view, name="admin_applications_list"),
    path("applications/approve-all-pending/", views.admin_approve_all_pending_applications_view, name="admin_approve_all_pending_applications"),

    # All Customers & Customer Management
    path("customers/", views.admin_all_customers_view, name="admin_all_customers"),
    path("customers/<int:pk>/", views.admin_customer_detail_view, name="admin_customer_detail"),
    path("customers/<int:pk>/update-status/", views.admin_customer_update_status_view, name="admin_customer_update_status"),
    path("customers/<int:pk>/edit-due-date/", views.admin_customer_edit_due_date_view, name="admin_customer_edit_due_date"),
    path("customers/<int:pk>/record-payment/", views.admin_customer_record_payment_view, name="admin_customer_record_payment"),
    path("customers/export/<str:export_format>/", views.admin_customers_export_view, name="admin_customers_export"),
    path("customers/applications/<int:pk>/approval/", views.admin_application_approval_view, name="admin_application_approval"),

    # Shop-wise Customers
    path("shopwise-customers/", views.admin_shopwise_customers_view, name="admin_shopwise_customers"),
    path("shopwise-customers/<int:shop_id>/", views.admin_shopwise_detail_view, name="admin_shopwise_detail"),
    path("shopwise-customers/<int:shop_id>/export/<str:export_format>/", views.admin_shopwise_export_view, name="admin_shopwise_export"),

    # Customer EMI Details
    path("emi-details/", views.admin_customer_emi_list_view, name="admin_customer_emi_list"),

    # Shop Payments & Payout Transactions (Money Admin Owes to Shops)
    path("payments/", views.admin_shop_payments_view, name="admin_shop_payments"),
    path("payments/record/", views.admin_record_shop_payment_view, name="admin_record_shop_payment"),

    # Executive Reports & Performance Analytics
    path("reports/", views.admin_reports_view, name="admin_reports"),

    # Follow-up Staff Management (Admin)
    path("followups/", followup_views.admin_followup_list_view, name="admin_followup_list"),
    path("followups/add/", followup_views.admin_followup_add_view, name="admin_followup_add"),
    path("followups/<int:pk>/", followup_views.admin_followup_detail_view, name="admin_followup_detail"),
    path("followups/<int:pk>/edit/", followup_views.admin_followup_edit_view, name="admin_followup_edit"),
    path("followups/<int:pk>/status/", followup_views.admin_followup_status_view, name="admin_followup_status"),
    path("followups/<int:pk>/reset-password/", followup_views.admin_followup_reset_password_view, name="admin_followup_reset_password"),
]
