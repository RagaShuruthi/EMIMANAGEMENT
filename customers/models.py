"""
Customer model for Private Finance EMI Management System.
Stores registered customer contact and address details associated with a retail shop.
"""

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
