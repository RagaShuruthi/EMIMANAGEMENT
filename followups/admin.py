from django.contrib import admin
from .models import FollowUpAssignment, FollowUpActivity


@admin.register(FollowUpAssignment)
class FollowUpAssignmentAdmin(admin.ModelAdmin):
    list_display = ("employee", "shop", "is_active", "assigned_at", "assigned_by")
    list_filter = ("is_active", "assigned_at")
    search_fields = ("employee__username", "employee__first_name", "employee__last_name", "shop__name")
    raw_id_fields = ("employee", "shop", "assigned_by")


@admin.register(FollowUpActivity)
class FollowUpActivityAdmin(admin.ModelAdmin):
    list_display = ("customer", "shop", "employee", "activity_type", "next_followup_date", "created_at")
    list_filter = ("activity_type", "created_at")
    search_fields = ("customer__name", "customer__primary_mobile", "employee__username", "notes")
    raw_id_fields = ("customer", "shop", "employee")
