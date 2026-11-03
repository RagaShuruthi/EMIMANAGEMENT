"""
URL patterns for the Follow-up Employee Portal and Password Setup Workflow.
"""

from django.urls import path
from . import views

urlpatterns = [
    # Dedicated Follow-up Employee Dashboard & Customer Details
    path("", views.followup_dashboard_view, name="followup_dashboard"),
    path("dashboard/", views.followup_dashboard_view, name="followup_portal_dashboard"),
    path("customers/<int:pk>/", views.followup_customer_detail_view, name="followup_customer_detail"),
    path("customers/<int:pk>/quick-update/", views.followup_quick_update_view, name="followup_quick_update"),
    path("activities/<int:pk>/complete/", views.followup_complete_activity_view, name="followup_complete_activity"),

    # Secure Password Setup Workflow for Employees
    path("setup-password/<str:uidb64>/<str:token>/", views.followup_setup_password_view, name="followup_setup_password"),
]
