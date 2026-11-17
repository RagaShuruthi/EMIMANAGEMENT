from django.contrib import admin
from .models import (
    FinancierPayment,
    CustomerEMIPayment,
    CustomerEMISchedule,
    ShopCollectionRecord,
    ShopFinancialTransaction,
)


@admin.register(FinancierPayment)
class FinancierPaymentAdmin(admin.ModelAdmin):
    list_display = ("payment_reference", "shop", "amount_received", "payment_date", "is_discrepant")
    list_filter = ("shop", "payment_date", "is_discrepant")
    search_fields = ("payment_reference", "shop__name", "remarks")


@admin.register(CustomerEMISchedule)
class CustomerEMIScheduleAdmin(admin.ModelAdmin):
    list_display = ("application", "customer", "installment_number", "due_date", "amount", "amount_paid", "status", "is_paid")
    list_filter = ("status", "is_paid", "due_date")
    search_fields = ("customer__name", "application__application_number")


@admin.register(CustomerEMIPayment)
class CustomerEMIPaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "amount", "payment_date", "payment_mode", "collected_by_shop")
    list_filter = ("collected_by_shop", "payment_mode", "payment_date")
    search_fields = ("customer__name", "transaction_reference")


@admin.register(ShopCollectionRecord)
class ShopCollectionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "shop",
        "customer",
        "amount_collected",
        "adjusted_to_loan",
        "collectible_amount",
        "amount_received_by_admin",
        "status",
        "collection_date",
    )
    list_filter = ("status", "shop", "collection_date")
    search_fields = ("shop__name", "customer__name", "notes")


@admin.register(ShopFinancialTransaction)
class ShopFinancialTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "transaction_id",
        "shop",
        "customer",
        "transaction_type",
        "amount",
        "transaction_date",
        "status",
    )
    list_filter = ("transaction_type", "status", "shop", "transaction_date")
    search_fields = ("transaction_id", "shop__name", "customer__name", "remarks")
