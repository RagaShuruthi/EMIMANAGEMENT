"""
Customer model for Private Finance EMI Management System.
Stores registered customer contact and address details associated with a retail shop.
"""

from django.conf import settings
from django.db import models
from shops.models import Shop


class Customer(models.Model):
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="customers",
        help_text="The retail mobile shop where this customer was registered."
    )
    name = models.CharField(
        max_length=200,
        help_text="Full legal name of the customer."
    )
    work_address = models.TextField(
        help_text="Office / Workplace address of the customer."
    )
    home_address = models.TextField(
        help_text="Current residential address of the customer."
    )
    primary_mobile = models.CharField(
        max_length=20,
        help_text="Primary mobile contact number."
    )
    secondary_mobile = models.CharField(
        max_length=20,
        default="",
        help_text="Secondary mobile contact number (Mandatory)."
    )
    latest_update_status = models.TextField(
        blank=True,
        default="",
        help_text="Latest update or follow-up note regarding this customer."
    )
    latest_update_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the latest update."
    )
    latest_update_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_latest_updates",
        help_text="User who logged the latest update."
    )
    is_loss = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if customer is moved to the Loss list."
    )
    loss_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date when customer was moved to the Loss list."
    )
    loss_reason = models.TextField(
        blank=True,
        default="",
        help_text="Reason or notes when customer was moved to Loss."
    )
    completed_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date on which the customer's EMI was fully completed."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Customer"
        verbose_name_plural = "Customers"
        indexes = [
            models.Index(fields=["shop", "-created_at"]),
            models.Index(fields=["name"]),
            models.Index(fields=["primary_mobile"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.primary_mobile})"


class CustomerUpdateHistory(models.Model):
    """
    Historical log of updates, notes, and statuses logged for a customer.
    Never overwrites past updates so complete history is preserved.
    """
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="update_history",
        help_text="Customer this note relates to."
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logged_customer_updates",
        help_text="Staff or admin who entered this update."
    )
    notes = models.TextField(
        help_text="Update note message."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Date and time when this note was logged."
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Customer Update History"
        verbose_name_plural = "Customer Update Histories"

    def __str__(self):
        author = self.user.username if self.user else "System"
        return f"{self.customer.name} - {author} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"

