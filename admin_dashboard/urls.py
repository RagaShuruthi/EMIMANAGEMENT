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
    path("customers/<int:pk>/change-status/", views.admin_customer_change_application_status_view, name="admin_customer_change_status"),
    path("customers/<int:pk>/update-status/", views.admin_customer_update_status_view, name="admin_customer_update_status"),
    path("customers/<int:pk>/edit-due-date/", views.admin_customer_edit_due_date_view, name="admin_customer_edit_due_date"),
    path("customers/<int:pk>/move-to-next-due/", views.admin_move_to_next_due_view, name="admin_move_to_next_due"),
    path("customers/<int:pk>/record-payment/", views.admin_customer_record_payment_view, name="admin_customer_record_payment"),
    path("customer-payments/<int:pk>/edit/", views.admin_edit_customer_payment_view, name="admin_edit_customer_payment"),
    path("customer-payments/<int:pk>/reverse/", views.admin_reverse_customer_payment_view, name="admin_reverse_customer_payment"),
    path("customers/<int:pk>/move-to-loss/", views.admin_customer_move_to_loss_view, name="admin_customer_move_to_loss"),
    path("customers/<int:pk>/restore-from-loss/", views.admin_customer_restore_from_loss_view, name="admin_customer_restore_from_loss"),
    path("customers/<int:pk>/edit-financials/", views.admin_customer_edit_financials_view, name="admin_customer_edit_financials"),
    path("customers/<int:pk>/delete/", views.admin_customer_delete_view, name="admin_customer_delete"),
    path("customers/export/<str:export_format>/", views.admin_customers_export_view, name="admin_customers_export"),
    path("customers/applications/<int:pk>/approval/", views.admin_application_approval_view, name="admin_application_approval"),

    # Loss Customers List
    path("loss-customers/", views.admin_loss_customers_view, name="admin_loss_customers"),

    # Installment Updates (Amounts & Update Note)
    path("installments/<int:pk>/update/", views.admin_update_installment_view, name="admin_update_installment"),

    # Completed Customers List
    path("completed-customers/", views.admin_completed_customers_view, name="admin_completed_customers"),
    path("completed-customers/export/<str:export_format>/", views.admin_completed_customers_export_view, name="admin_completed_customers_export"),

    # Shop-wise Customers
    path("shopwise-customers/", views.admin_shopwise_customers_view, name="admin_shopwise_customers"),
    path("shopwise-customers/<int:shop_id>/", views.admin_shopwise_detail_view, name="admin_shopwise_detail"),
    path("shopwise-customers/<int:shop_id>/export/<str:export_format>/", views.admin_shopwise_export_view, name="admin_shopwise_export"),

    # Customer EMI Details
    path("emi-details/", views.admin_customer_emi_list_view, name="admin_customer_emi_list"),

    # Shop Payments & Payout Transactions (Money Admin Owes to Shops)
    path("payments/", views.admin_shop_payments_view, name="admin_shop_payments"),
    path("payments/record/", views.admin_record_shop_payment_view, name="admin_record_shop_payment"),
    path("payments/acknowledge-collection/", views.admin_acknowledge_shop_collection_view, name="admin_acknowledge_shop_collection"),

    # Executive Reports & Performance Analytics
    path("reports/", views.admin_reports_view, name="admin_reports"),

    # Follow-up Staff Management (Admin)
    path("followups/", followup_views.admin_followup_list_view, name="admin_followup_list"),
    path("followups/add/", followup_views.admin_followup_add_view, name="admin_followup_add"),
    path("followups/<int:pk>/", followup_views.admin_followup_detail_view, name="admin_followup_detail"),
    path("followups/<int:pk>/edit/", followup_views.admin_followup_edit_view, name="admin_followup_edit"),
    path("followups/<int:pk>/status/", followup_views.admin_followup_status_view, name="admin_followup_status"),
    path("followups/<int:pk>/reset-password/", followup_views.admin_followup_reset_password_view, name="admin_followup_reset_password"),
    path("followups/<int:pk>/delete/", followup_views.admin_followup_delete_view, name="admin_followup_delete"),
]
