"""
Models for Financier Payments and Discrepancy Tracking.
"""

from decimal import Decimal
from django.db import models
from applications.models import FinanceApplication


class FinancierPayment(models.Model):
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="financier_payouts",
        help_text="Shop to which the admin disbursement was paid directly."
    )
    application = models.ForeignKey(
        FinanceApplication,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="financier_payments",
        help_text="Optional finance application for which financier disbursement was received."
    )
    amount_received = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Amount disbursed by financier into shop account in INR."
    )
    payment_date = models.DateField(
        help_text="Date the disbursement was credited."
    )
    payment_reference = models.CharField(
        max_length=100,
        help_text="UTR / NEFT / IMPS reference number from the financier."
    )
    remarks = models.TextField(
        blank=True,
        default="",
        help_text="Notes, deductions, or remarks from financier statement."
    )
    is_discrepant = models.BooleanField(
        default=False,
        help_text="Flagged True if received amount does not match expected requested balance."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-created_at"]
        verbose_name = "Financier Payment"
        verbose_name_plural = "Financier Payments"
        indexes = [
            models.Index(fields=["shop", "-payment_date"]),
            models.Index(fields=["application", "-payment_date"]),
            models.Index(fields=["payment_reference"]),
        ]

    def save(self, *args, **kwargs):
        # Auto-link shop from application if not set
        if self.application and not self.shop:
            self.shop = self.application.shop

        if self.application:
            # Auto-detect discrepancy: if total received exceeds financier requested amount
            prior_total = self.application.total_payments_received
            if self.pk:
                prior_total -= self.amount_received
            new_total = prior_total + (self.amount_received or Decimal("0.00"))
            if new_total > self.application.financier_request_amount:
                self.is_discrepant = True

            super().save(*args, **kwargs)

            # Update application status if fully paid or partially paid
            app = self.application
            if new_total >= app.financier_request_amount:
                if app.status != FinanceApplication.Status.PAID:
                    app.status = FinanceApplication.Status.PAID
                    app.save(update_fields=["status", "updated_at"])
            elif new_total > 0:
                if app.status not in (FinanceApplication.Status.PAID, FinanceApplication.Status.PARTIALLY_PAID):
                    app.status = FinanceApplication.Status.PARTIALLY_PAID
                    app.save(update_fields=["status", "updated_at"])
        else:
            super().save(*args, **kwargs)

    def __str__(self):
        target = self.application.application_number if self.application else (self.shop.name if self.shop else "Shop")
        return f"{self.payment_reference} — ₹{self.amount_received} for {target}"
