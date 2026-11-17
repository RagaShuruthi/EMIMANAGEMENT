"""
Django Admin configuration for shops app.
"""

from django.contrib import admin
from .models import Shop


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "owner_name",
        "phone",
        "gpay",
        "bank_name",
        "bank_account_number",
        "is_active",
        "owner_account",
        "created_at",
    )
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "owner_name", "phone", "email", "gpay", "bank_account_number")
    raw_id_fields = ("owner_account",)
    readonly_fields = ("created_at", "updated_at")
    
