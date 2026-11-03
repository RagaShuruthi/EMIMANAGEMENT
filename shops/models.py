"""
Shop model for EMI Management System.
Stores registered retail mobile phone shops and their owners.
"""

from django.conf import settings
from django.db import models


class Shop(models.Model):
    """
    Represents a mobile phone retail shop authorized to register EMI customers.
    """
    name = models.CharField(
        max_length=200,
        help_text="Registered commercial name of the mobile phone retail shop."
    )
    owner_name = models.CharField(
        max_length=150,
        help_text="Full legal name of the shop owner or primary authorized contact person."
    )
    phone = models.CharField(
        max_length=25,
        help_text="Primary contact phone number for the shop."
    )
    email = models.EmailField(
        blank=True,
        null=True,
        help_text="Optional official email address for correspondence."
    )
    address = models.TextField(
        help_text="Physical retail address of the shop."
    )

    # Digital & Bank Payout Details
    gpay = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="GPay / UPI Number",
        help_text="Google Pay (GPay) mobile number or UPI ID for direct payouts."
    )
    bank_account_holder_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name="Account Holder Name",
        help_text="Name of account holder as per bank records."
    )
    bank_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name="Bank Name",
        help_text="Bank name (e.g. State Bank of India, HDFC, ICICI, Axis)."
    )
    bank_account_number = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Bank Account Number",
        help_text="Bank account number for NEFT/IMPS/RTGS payouts."
    )
    bank_ifsc_code = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name="Bank IFSC Code",
        help_text="11-character bank IFSC code (e.g. SBIN0001234)."
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Indicates whether this shop is currently authorized to conduct business."
    )
    owner_account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shops",
        help_text="Associated user account for shop-owner portal login."
    )
    assigned_password = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Password assigned by administrator for shop owner login."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the shop record was registered."
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when the shop record was last modified."
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Shop"
        verbose_name_plural = "Shops"
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["owner_name"]),
            models.Index(fields=["phone"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.owner_name})"
