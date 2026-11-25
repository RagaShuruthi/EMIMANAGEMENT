"""
URL patterns for the Follow-up Employee Portal and Password Setup Workflow.
"""

from django.urls import path
from . import views

urlpatterns = [
    # 1. Follow-up Person Homepage
    path("", views.followup_dashboard_view, name="followup_dashboard"),
    path("dashboard/", views.followup_dashboard_view, name="followup_portal_dashboard"),

    # 2. View All Assigned Shops
    path("shops/", views.followup_shops_list_view, name="followup_shops_list"),

    # 3. Individual Shop Details Page
    path("shops/<int:shop_id>/", views.followup_shop_detail_view, name="followup_shop_detail"),

    # 4. Daily Payment Tasks & Scoped Customers
    path("tasks/", views.followup_tasks_view, name="followup_tasks"),
    path("customers-tasks/", views.followup_tasks_view, name="followup_customers"),

    # 5. Dedicated Pending Follow-ups Page
    path("pending/", views.followup_pending_view, name="followup_pending"),

    # Customer Details & Follow-up Actions
    path("customers/<int:pk>/", views.followup_customer_detail_view, name="followup_customer_detail"),
    path("customers/<int:pk>/quick-update/", views.followup_quick_update_view, name="followup_quick_update"),
    path("customers/<int:pk>/edit-due-date/", views.followup_edit_due_date_view, name="followup_edit_due_date"),
    path("customers/<int:pk>/move-to-next-due/", views.followup_move_to_next_due_view, name="followup_move_to_next_due"),
    path("activities/<int:pk>/complete/", views.followup_complete_activity_view, name="followup_complete_activity"),

    # Secure Password Setup Workflow for Employees
    path("setup-password/<str:uidb64>/<str:token>/", views.followup_setup_password_view, name="followup_setup_password"),
]
