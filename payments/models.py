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
    payment_screenshot = models.FileField(
        upload_to="payouts/screenshots/%Y/%m/",
        null=True,
        blank=True,
        help_text="Screenshot or proof of shop payout payment."
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
    update_note = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Custom admin update notes or information for this installment."
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
    collected_by_shop = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if this EMI installment was directly collected by the retail shop (deducted from financier payable)."
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


class ShopCollectionRecord(models.Model):
    """
    Authoritative record for an EMI installment collected directly by a retail shop.
    Tracks adjustment against the shop's pending loans versus any unadjusted
    amount that is collectible/recoverable from the shop by the admin.
    """
    class CollectionStatus(models.TextChoices):
        ADJUSTED = "ADJUSTED", "Adjusted against Loan"
        PENDING = "PENDING", "Pending Admin Receipt"
        PARTIAL = "PARTIAL", "Partially Received"
        RECEIVED = "RECEIVED", "Received in Full"

    payment = models.OneToOneField(
        CustomerEMIPayment,
        on_delete=models.CASCADE,
        related_name="shop_collection_record",
        help_text="The underlying customer EMI payment collected by the shop."
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="collection_records",
        help_text="The shop that collected the payment."
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.CASCADE,
        related_name="shop_collection_records",
        help_text="Customer who paid the EMI."
    )
    application = models.ForeignKey(
        FinanceApplication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shop_collection_records",
        help_text="Application this payment was made against."
    )
    amount_collected = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Total amount collected by the shop from customer in INR."
    )
    adjusted_to_loan = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Portion adjusted against shop's pending loan / financier settlement."
    )
    collectible_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Portion not adjusted against any loan; recoverable by admin from shop."
    )
    amount_received_by_admin = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Amount acknowledged as received by admin from the shop."
    )
    status = models.CharField(
        max_length=32,
        choices=CollectionStatus.choices,
        default=CollectionStatus.PENDING,
        db_index=True,
        help_text="Status of the collection / recovery."
    )
    collection_date = models.DateField(
        default=timezone.now,
        help_text="Date shop collected the payment from customer."
    )
    received_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date admin received funds from shop."
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="acknowledged_shop_collections",
        help_text="Admin user who acknowledged receipt."
    )
    followup_person = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="followup_shop_collections",
        help_text="Follow-up staff member assigned to the shop."
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Notes, audit trail, or explanation."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-collection_date", "-created_at"]
        verbose_name = "Shop Collection Record"
        verbose_name_plural = "Shop Collection Records"
        indexes = [
            models.Index(fields=["shop", "status"]),
            models.Index(fields=["customer", "-collection_date"]),
        ]

    @property
    def remaining_collectible(self):
        """Unreceived balance that admin has yet to recover from the shop."""
        return max(Decimal("0.00"), self.collectible_amount - (self.amount_received_by_admin or Decimal("0.00")))

    def __str__(self):
        return f"{self.shop.name} - ₹{self.amount_collected} ({self.get_status_display()})"


class ShopFinancialTransaction(models.Model):
    """
    Transparent transaction ledger for all shop payout and collection operations.
    Maintains a permanent audit trail of every financial movement.
    """
    class TransactionType(models.TextChoices):
        ADMIN_PAYMENT = "ADMIN_PAYMENT", "Admin Payment"
        SHOP_COLLECTION = "SHOP_COLLECTION", "Shop Customer Collection"
        LOAN_ADJUSTMENT = "LOAN_ADJUSTMENT", "Loan Adjustment"
        FINANCIER_SETTLEMENT_ADJUSTMENT = "FINANCIER_SETTLEMENT_ADJUSTMENT", "Financier Settlement Adjustment"
        SHOP_RECEIPT = "SHOP_RECEIPT", "Amount Received from Shop"
        PARTIAL_RECEIPT = "PARTIAL_RECEIPT", "Partial Receipt"
        LOAN_SETTLEMENT = "LOAN_SETTLEMENT", "Loan Settlement"
        NEW_LOAN_ADJUSTMENT = "NEW_LOAN_ADJUSTMENT", "New Loan Adjustment"

    class TransactionStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        RECEIVED = "RECEIVED", "Received"
        PARTIAL = "PARTIAL", "Partial Receipt"
        ADJUSTED = "ADJUSTED", "Adjusted"
        SETTLED = "SETTLED", "Settled"

    transaction_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Unique transaction reference (e.g., TXN-20261117-XXXX)."
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="financial_transactions",
        help_text="Shop involved in this financial transaction."
    )
    customer = models.ForeignKey(
        "customers.Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shop_financial_transactions",
        help_text="Associated customer if applicable."
    )
    application = models.ForeignKey(
        FinanceApplication,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shop_financial_transactions",
        help_text="Associated finance application if applicable."
    )
    transaction_type = models.CharField(
        max_length=40,
        choices=TransactionType.choices,
        db_index=True,
        help_text="Type of financial movement."
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Transaction amount in INR."
    )
    transaction_date = models.DateField(
        default=timezone.now,
        db_index=True,
        help_text="Date of transaction."
    )
    emi_month = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="EMI month or installment index if applicable."
    )
    followup_person = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="followup_transactions",
        help_text="Follow-up staff member assigned."
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recorded_shop_transactions",
        help_text="User who logged or triggered this transaction."
    )
    previous_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Shop loan or collectible balance before this transaction."
    )
    updated_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Shop loan or collectible balance after this transaction."
    )
    remarks = models.TextField(
        blank=True,
        default="",
        help_text="Notes and audit details."
    )
    status = models.CharField(
        max_length=32,
        choices=TransactionStatus.choices,
        default=TransactionStatus.COMPLETED,
        db_index=True,
        help_text="Status of the transaction."
    )
    collection_record = models.ForeignKey(
        ShopCollectionRecord,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="transactions",
        help_text="Linked shop collection record."
    )
    financier_payment = models.ForeignKey(
        FinancierPayment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transactions",
        help_text="Linked admin financier payment."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-transaction_date", "-created_at"]
        verbose_name = "Shop Financial Transaction"
        verbose_name_plural = "Shop Financial Transactions"
        indexes = [
            models.Index(fields=["shop", "-transaction_date"]),
            models.Index(fields=["transaction_type", "-transaction_date"]),
            models.Index(fields=["status"]),
        ]

    def save(self, *args, **kwargs):
        if not self.transaction_id:
            import uuid
            t_date = self.transaction_date or timezone.localdate()
            if isinstance(t_date, str):
                date_prefix = t_date.replace("-", "")[:8]
            else:
                date_prefix = t_date.strftime("%Y%m%d")
            rand_suffix = uuid.uuid4().hex[:6].upper()
            self.transaction_id = f"TXN-{date_prefix}-{rand_suffix}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.transaction_id} — {self.get_transaction_type_display()}: ₹{self.amount} ({self.shop.name})"


