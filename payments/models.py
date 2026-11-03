"""
Models for Financier Payments and Discrepancy Tracking.
"""

from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
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


class CustomerEMISchedule(models.Model):
    """
    Individual EMI installment schedule for a customer's finance application.
    Represents customer-to-shop EMI payment obligations.
    """
    class InstallmentStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PARTIAL = "PARTIAL", "Partially Paid"
        PAID = "PAID", "Paid"

    application = models.ForeignKey(
        FinanceApplication,
        on_delete=models.CASCADE,
        related_name="emi_schedules",
        help_text="The finance application this installment belongs to."
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.CASCADE,
        related_name="emi_schedules",
        help_text="Customer responsible for this EMI."
    )
    installment_number = models.PositiveSmallIntegerField(
        help_text="Installment index (1, 2, ... up to tenure months)."
    )
    due_date = models.DateField(
        db_index=True,
        help_text="Scheduled due date for this installment (editable by Admin / Staff)."
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Expected installment amount in INR."
    )
    amount_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total amount paid towards this installment so far in INR."
    )
    is_paid = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if this installment is completely settled."
    )
    paid_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date when this installment was completely settled."
    )
    status = models.CharField(
        max_length=20,
        choices=InstallmentStatus.choices,
        default=InstallmentStatus.PENDING,
        help_text="Installment payment status: PENDING, PARTIAL, or PAID."
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Notes or comments regarding this installment."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["installment_number"]
        verbose_name = "Customer EMI Schedule"
        verbose_name_plural = "Customer EMI Schedules"
        indexes = [
            models.Index(fields=["application", "installment_number"]),
            models.Index(fields=["customer", "due_date"]),
            models.Index(fields=["is_paid", "due_date"]),
        ]

    @property
    def remaining_amount(self):
        """Remaining unpaid balance for this installment."""
        return max(Decimal("0.00"), self.amount - (self.amount_paid or Decimal("0.00")))

    def __str__(self):
        return f"{self.customer.name} - Inst #{self.installment_number} (Due: {self.due_date}, ₹{self.amount})"


class CustomerEMIPayment(models.Model):
    """
    Actual payment transaction made by a customer towards their EMI loan.
    Completely independent from Admin-to-Shop payouts.
    """
    class PaymentMode(models.TextChoices):
        UPI = "UPI", "UPI / GPay / PhonePe"
        CASH = "CASH", "Cash"
        BANK = "BANK", "Bank Transfer / NEFT / IMPS"
        CARD = "CARD", "Debit / Credit Card"
        OTHER = "OTHER", "Other"

    application = models.ForeignKey(
        FinanceApplication,
        on_delete=models.CASCADE,
        related_name="customer_emi_payments",
        help_text="Finance application for which customer payment was received."
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.CASCADE,
        related_name="customer_emi_payments",
        help_text="Customer who made the payment."
    )
    schedule = models.ForeignKey(
        CustomerEMISchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
        help_text="Specific EMI installment this payment was allocated to (optional)."
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Payment amount received from customer in INR."
    )
    payment_date = models.DateField(
        default=timezone.now,
        help_text="Date when customer made the payment."
    )
    payment_mode = models.CharField(
        max_length=50,
        choices=PaymentMode.choices,
        default=PaymentMode.UPI,
        help_text="Payment method used by the customer."
    )
    transaction_reference = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Transaction reference / UPI ref / receipt number."
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="User who recorded this customer payment in the system."
    )
    remarks = models.TextField(
        blank=True,
        default="",
        help_text="Payment notes or remarks."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-created_at"]
        verbose_name = "Customer EMI Payment"
        verbose_name_plural = "Customer EMI Payments"
        indexes = [
            models.Index(fields=["customer", "-payment_date"]),
            models.Index(fields=["application", "-payment_date"]),
        ]

    def __str__(self):
        return f"₹{self.amount} by {self.customer.name} on {self.payment_date} ({self.payment_mode})"

