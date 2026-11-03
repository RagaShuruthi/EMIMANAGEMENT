"""
Models for Finance Applications, Uploaded Documents, and Status Audit Trails.
"""

import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
from shops.models import Shop
from customers.models import Customer


class FinanceApplication(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        READY = "READY", "Ready to Submit"
        SUBMITTED = "SUBMITTED", "Submitted to Financier"
        UNDER_REVIEW = "UNDER_REVIEW", "Under Financier Review"
        APPROVED = "APPROVED", "Financier Approved"
        REJECTED = "REJECTED", "Financier Rejected"
        PAID = "PAID", "Financier Paid"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Partially Paid"
        SUBMISSION_FAILED = "SUBMISSION_FAILED", "Submission Failed"

    class TenureMonths(models.IntegerChoices):
        THREE_MONTHS = 3, "3 Months"
        SIX_MONTHS = 6, "6 Months"

    application_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Unique application reference identifier (e.g., APP-20261028-0001)."
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="applications",
        help_text="Retail shop originating the financing application."
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="applications",
        help_text="Customer purchasing the device under EMI financing."
    )
    application_date = models.DateField(
        default=timezone.now,
        help_text="Date when application was created."
    )

    # Device & EMI Fields
    mobile_model = models.CharField(
        max_length=200,
        help_text="Exact brand, model, and storage variant of the phone (e.g., Samsung Galaxy S24 256GB)."
    )
    mobile_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Total retail selling price of the mobile phone in INR."
    )
    down_payment = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Initial upfront cash payment received from the customer."
    )
    monthly_emi = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Agreed monthly EMI installment amount (Calculated: Total Financed / Due Months)."
    )
    emi_duration = models.PositiveIntegerField(
        choices=TenureMonths.choices,
        default=TenureMonths.SIX_MONTHS,
        help_text="Tenure duration in months (Exclusively 3 and 6 months permitted)."
    )
    monthly_due_day = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Monthly EMI payment due day (5, 10, 15, 20, 25, 30) based on purchase date."
    )
    financier_request_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Disbursement amount requested from financier: (Mobile Price - Down Payment)."
    )

    # Workflow & External Integration Fields
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        help_text="Current lifecycle status of the finance application."
    )
    external_reference = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="External identifier assigned by financier system upon electronic submission."
    )
    submission_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when application was electronically dispatched to the financier."
    )
    remarks = models.TextField(
        blank=True,
        default="",
        help_text="Underwriter notes, rejection reason, or status remarks."
    )
    idempotency_key = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        help_text="Unique key preventing duplicate submissions to financier."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Finance Application"
        verbose_name_plural = "Finance Applications"
        indexes = [
            models.Index(fields=["shop", "status", "-created_at"]),
            models.Index(fields=["application_number"]),
            models.Index(fields=["status"]),
        ]

    @staticmethod
    def calculate_monthly_due_day(purchase_date):
        """
        Determines the monthly EMI due day based on phone purchase date:
        - 1 to 5 or 31 -> 5th
        - 6 to 10 -> 10th
        - 11 to 15 -> 15th
        - 16 to 20 -> 20th
        - 21 to 25 -> 25th
        - 26 to 30 -> 30th
        """
        if hasattr(purchase_date, "day"):
            day = purchase_date.day
        elif isinstance(purchase_date, str) and "-" in purchase_date:
            day = int(purchase_date.split("-")[-1][:2])
        else:
            day = int(purchase_date)

        if day == 31 or 1 <= day <= 5:
            return 5
        elif 6 <= day <= 10:
            return 10
        elif 11 <= day <= 15:
            return 15
        elif 16 <= day <= 20:
            return 20
        elif 21 <= day <= 25:
            return 25
        else:
            return 30

    @staticmethod
    def round_emi_to_tens(amount):
        """
        Rounds EMI amount to the nearest 10s:
        e.g. 3333.33 -> 3330.00
             3336.30 -> 3340.00
        """
        if amount is None or amount <= Decimal("0"):
            return Decimal("0.00")
        val = float(amount)
        rounded = int(round(val / 10.0) * 10)
        return Decimal(f"{rounded:.2f}")

    def save(self, *args, **kwargs):
        # Business rule:
        # Financier Request Amount = Total Selling Price - Down Payment
        # e.g., if total selling price is 25,000 and down payment is 5,000, financier request is 20,000.
        # Monthly EMI = Main Payment (Mobile Price) / Due Months (3 or 6), rounded to 10s.
        if self.mobile_price is not None:
            down = self.down_payment if self.down_payment is not None else Decimal("0.00")
            self.financier_request_amount = max(Decimal("0.00"), self.mobile_price - down)

            if not self.monthly_emi or self.monthly_emi <= Decimal("0"):
                if self.emi_duration and int(self.emi_duration) in (3, 6):
                    raw_emi = self.financier_request_amount / Decimal(self.emi_duration)
                    self.monthly_emi = self.round_emi_to_tens(raw_emi)
            else:
                # If shop owner customized EMI, round it to nearest 10s as well
                self.monthly_emi = self.round_emi_to_tens(self.monthly_emi)

        # Calculate monthly due day from purchase application date if not manually chosen
        if not self.monthly_due_day and self.application_date:
            self.monthly_due_day = self.calculate_monthly_due_day(self.application_date)

        # Generate human-readable application number if not present
        if not self.application_number:
            date_prefix = timezone.now().strftime("%Y%m%d")
            random_suffix = uuid.uuid4().hex[:6].upper()
            self.application_number = f"APP-{date_prefix}-{random_suffix}"

        is_new = self.pk is None
        super().save(*args, **kwargs)

        if is_new:
            self.generate_default_schedule_if_missing()

    def generate_default_schedule_if_missing(self, force=False):
        """
        Generates customer EMI schedule installments:
        - Exactly tenure months (3 or 6)
        - Due dates calculated on monthly_due_day for each month
        - Financed amount / tenure with final installment settling paise rounding
        """
        import calendar
        from datetime import datetime, date
        from payments.models import CustomerEMISchedule

        if not force and self.emi_schedules.exists():
            return

        if force:
            self.emi_schedules.all().delete()

        tenure = int(self.emi_duration or 6)
        financed = self.financier_request_amount or Decimal("0.00")
        if financed <= Decimal("0.00") or tenure <= 0:
            return

        app_date = self.application_date or timezone.localdate()
        if isinstance(app_date, str):
            try:
                app_date = datetime.strptime(app_date[:10], "%Y-%m-%d").date()
            except Exception:
                app_date = timezone.localdate()

        due_day = self.monthly_due_day or self.calculate_monthly_due_day(app_date)

        base_inst = (financed / Decimal(tenure)).quantize(Decimal("0.01"))
        remainder = financed - (base_inst * (tenure - 1))

        cur_year = app_date.year
        cur_month = app_date.month

        # Start first installment on next due cycle
        if app_date.day >= due_day:
            cur_month += 1
            if cur_month > 12:
                cur_month = 1
                cur_year += 1

        schedules = []
        for i in range(1, tenure + 1):
            _, last_day = calendar.monthrange(cur_year, cur_month)
            inst_due_date = date(cur_year, cur_month, min(due_day, last_day))
            inst_amount = remainder if i == tenure else base_inst

            schedules.append(
                CustomerEMISchedule(
                    application=self,
                    customer=self.customer,
                    installment_number=i,
                    due_date=inst_due_date,
                    amount=inst_amount,
                    amount_paid=Decimal("0.00"),
                    is_paid=False,
                    status=CustomerEMISchedule.InstallmentStatus.PENDING,
                )
            )

            cur_month += 1
            if cur_month > 12:
                cur_month = 1
                cur_year += 1

        CustomerEMISchedule.objects.bulk_create(schedules)

    def record_customer_payment(self, amount, payment_date=None, payment_mode="UPI", transaction_reference="", recorded_by=None, remarks=""):
        """
        Records a customer EMI payment and applies it sequentially across unpaid schedule installments.
        """
        from payments.models import CustomerEMIPayment, CustomerEMISchedule
        if payment_date is None:
            payment_date = timezone.localdate()
        amt = Decimal(str(amount))
        if amt <= Decimal("0.00"):
            return None

        self.generate_default_schedule_if_missing()

        payment = CustomerEMIPayment.objects.create(
            application=self,
            customer=self.customer,
            amount=amt,
            payment_date=payment_date,
            payment_mode=payment_mode,
            transaction_reference=transaction_reference,
            recorded_by=recorded_by,
            remarks=remarks,
        )

        remaining_to_distribute = amt
        unpaid_schedules = self.emi_schedules.filter(is_paid=False).order_by("installment_number")

        for sched in unpaid_schedules:
            if remaining_to_distribute <= Decimal("0.00"):
                break
            needed = sched.amount - sched.amount_paid
            if remaining_to_distribute >= needed:
                sched.amount_paid = sched.amount
                sched.is_paid = True
                sched.paid_date = payment_date
                sched.status = CustomerEMISchedule.InstallmentStatus.PAID
                remaining_to_distribute -= needed
            else:
                sched.amount_paid += remaining_to_distribute
                sched.status = CustomerEMISchedule.InstallmentStatus.PARTIAL
                remaining_to_distribute = Decimal("0.00")
            sched.save(update_fields=["amount_paid", "is_paid", "paid_date", "status", "updated_at"])

        return payment

    @property
    def total_customer_paid(self):
        """Sum of all customer EMI payments received."""
        total = self.customer_emi_payments.aggregate(total=models.Sum("amount"))["total"]
        return total or Decimal("0.00")

    @property
    def customer_remaining_balance(self):
        """Remaining EMI balance: Financed Amount - Total Customer Paid."""
        financed = self.financier_request_amount or Decimal("0.00")
        return max(Decimal("0.00"), financed - self.total_customer_paid)

    @property
    def next_unpaid_schedule(self):
        """Next unpaid EMI installment."""
        return self.emi_schedules.filter(is_paid=False).order_by("installment_number").first()

    @property
    def next_unpaid_due_date(self):
        """Due date of the next unpaid installment."""
        unpaid = self.next_unpaid_schedule
        return unpaid.due_date if unpaid else None

    def due_classification(self, today=None):
        """
        Classifies due status:
        - PAID: When customer has no remaining balance or all installments paid
        - DUE_TODAY: Next unpaid installment due date == today
        - UPCOMING_DUE: Next unpaid installment due within next 2 days (excluding today)
        - OVERDUE: Next unpaid installment due date < today
        - NORMAL: More than 2 days in the future
        """
        from datetime import timedelta
        if today is None:
            today = timezone.localdate()

        if self.customer_remaining_balance <= Decimal("0.00") or not self.next_unpaid_schedule:
            return "PAID"

        next_date = self.next_unpaid_due_date
        if not next_date:
            return "NORMAL"

        if next_date == today:
            return "DUE_TODAY"
        elif today < next_date <= today + timedelta(days=2):
            return "UPCOMING_DUE"
        elif next_date < today:
            return "OVERDUE"
        return "NORMAL"

    def __str__(self):
        return f"{self.application_number} — {self.customer.name} ({self.mobile_model})"

    @property
    def total_payments_received(self):
        """Aggregate total cash disbursements received from financier for this application."""
        total = self.financier_payments.aggregate(total=models.Sum("amount_received"))["total"]
        return total or Decimal("0.00")

    @property
    def outstanding_amount(self):
        """Outstanding amount expected from financier: Requested - Received."""
        return max(Decimal("0.00"), self.financier_request_amount - self.total_payments_received)


class CustomerDocument(models.Model):
    class DocType(models.TextChoices):
        PHOTOGRAPH = "PHOTOGRAPH", "Customer Photograph"
        AADHAAR = "AADHAAR", "Aadhaar Card"
        PAN = "PAN", "PAN Card"
        VOTER_ID = "VOTER_ID", "Voter ID / Other Proof"

    application = models.ForeignKey(
        FinanceApplication,
        on_delete=models.CASCADE,
        related_name="documents"
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="documents"
    )
    document_type = models.CharField(
        max_length=32,
        choices=DocType.choices
    )
    file = models.FileField(
        upload_to="documents/%Y/%m/"
    )
    original_filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(help_text="Size in bytes")
    mime_type = models.CharField(max_length=100)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Customer Document"
        verbose_name_plural = "Customer Documents"

    def __str__(self):
        return f"{self.get_document_type_display()} for {self.customer.name}"


class ApplicationHistory(models.Model):
    application = models.ForeignKey(
        FinanceApplication,
        on_delete=models.CASCADE,
        related_name="history"
    )
    previous_status = models.CharField(max_length=32)
    new_status = models.CharField(max_length=32)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    notes = models.TextField(blank=True, default="")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Application History"
        verbose_name_plural = "Application History Records"

    def __str__(self):
        return f"{self.application.application_number}: {self.previous_status} -> {self.new_status}"
