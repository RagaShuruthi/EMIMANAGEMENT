"""
Models for Follow-up Persons management and customer follow-up tracking.
Enables assigning multiple retail shops to follow-up employees and recording follow-up activity.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class FollowUpAssignment(models.Model):
    """
    Associates a follow-up employee with an authorized retail shop.
    Allows administrators to assign multiple shops to one employee.
    """
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="followup_assignments",
        help_text="Follow-up employee user account."
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="followup_assignments",
        help_text="Retail shop assigned to this follow-up person."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this shop assignment is currently active."
    )
    assigned_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the assignment was created."
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_shop_assignments",
        help_text="Administrator who authorized this assignment."
    )

    class Meta:
        ordering = ["-assigned_at"]
        unique_together = ("employee", "shop")
        verbose_name = "Shop Follow-up Assignment"
        verbose_name_plural = "Shop Follow-up Assignments"

    def __str__(self):
        return f"{self.employee.username} -> {self.shop.name}"


class FollowUpActivity(models.Model):
    """
    Audit log of follow-up interactions (calls, reminders, visits)
    recorded by follow-up employees or administrators for shop customers.
    """
    class ActivityType(models.TextChoices):
        CALL = "CALL", "Phone Call"
        VISIT = "VISIT", "Shop / Field Visit"
        REMINDER = "REMINDER", "Payment Reminder"
        PROMISE = "PROMISE", "Promise to Pay"
        PAYMENT = "PAYMENT", "Payment Received"
        NOTE = "NOTE", "General Note"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Follow-up"
        COMPLETED = "COMPLETED", "Completed"

    class Outcome(models.TextChoices):
        PAID = "PAID", "Paid"
        NOT_PAID = "NOT_PAID", "Not Paid"
        NEED_TWO_DAYS = "NEED_TWO_DAYS", "Need two days time"
        PAID_REPORTED = "PAID_REPORTED", "Payment Reported / Awaiting Admin Verification"
        PROMISED = "PROMISED", "Promised to Pay Later"
        NOT_POSSIBLE = "NOT_POSSIBLE", "Payment Not Possible"

    class ReasonNotPossible(models.TextChoices):
        FINANCIAL_DIFFICULTY = "FINANCIAL_DIFFICULTY", "Financial Difficulty"
        REFUSED_TO_PAY = "REFUSED_TO_PAY", "Refused to Pay"
        UNABLE_TO_CONTACT = "UNABLE_TO_CONTACT", "Unable to Contact"
        OTHER = "OTHER", "Other"

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="followup_activities",
        help_text="Staff member who performed the follow-up."
    )
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.CASCADE,
        related_name="followup_activities",
        help_text="Customer contacted during this follow-up."
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="followup_activities",
        help_text="Associated retail shop."
    )
    activity_type = models.CharField(
        max_length=20,
        choices=ActivityType.choices,
        default=ActivityType.CALL,
        help_text="Channel or type of follow-up interaction."
    )
    outcome = models.CharField(
        max_length=30,
        choices=Outcome.choices,
        default=Outcome.NOT_PAID,
        help_text="Update status logged by follow-up person."
    )
    is_flagged = models.BooleanField(
        default=False,
        help_text="Flagged when customer needs callback or admin review."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.COMPLETED,
        help_text="Task status: Pending or Completed."
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Detailed notes on the conversation, agreement, or status."
    )
    reason_not_possible = models.CharField(
        max_length=50,
        choices=ReasonNotPossible.choices,
        blank=True,
        default="",
        help_text="Mandatory reason when payment is not possible."
    )
    reason_other_notes = models.TextField(
        blank=True,
        default="",
        help_text="Mandatory additional notes when reason is Other."
    )
    payment_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Payment amount reported by employee."
    )
    payment_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of payment reported."
    )
    payment_mode = models.CharField(
        max_length=50,
        blank=True,
        default="UPI",
        help_text="Payment mode reported."
    )
    payment_reference = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Payment transaction reference or UTR."
    )
    payment_verified = models.BooleanField(
        default=False,
        help_text="True if admin verified and confirmed this reported payment."
    )
    payment_rejected = models.BooleanField(
        default=False,
        help_text="True if admin rejected the payment report."
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_followup_activities",
        help_text="Admin user who verified this payment."
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when payment was verified by admin."
    )
    original_due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Original scheduled EMI due date prior to follow-up."
    )
    previous_callback_date = models.DateField(
        null=True,
        blank=True,
        help_text="Previous scheduled callback date if applicable."
    )
    previous_status = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text="Customer follow-up status before this action."
    )
    new_status = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text="Customer follow-up status resulting from this action."
    )
    admin_action = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Decision taken by admin on this record."
    )
    admin_notes = models.TextField(
        blank=True,
        default="",
        help_text="Notes recorded by admin during review or decision."
    )
    next_followup_date = models.DateField(
        null=True,
        blank=True,
        help_text="Scheduled date for the next follow-up action / callback."
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when this follow-up was marked completed."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this follow-up record was entered."
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Follow-up Activity"
        verbose_name_plural = "Follow-up Activities"

    def save(self, *args, **kwargs):
        from datetime import timedelta
        # Handle business logic for the outcomes
        if self.outcome == self.Outcome.NEED_TWO_DAYS:
            self.is_flagged = True
            self.status = self.Status.PENDING
            if not self.next_followup_date:
                self.next_followup_date = timezone.localdate() + timedelta(days=2)
        elif self.outcome == self.Outcome.PROMISED:
            self.is_flagged = True
            self.status = self.Status.PENDING
            if not self.next_followup_date:
                self.next_followup_date = timezone.localdate() + timedelta(days=2)
        elif self.outcome == self.Outcome.NOT_POSSIBLE:
            self.is_flagged = True
            self.status = self.Status.PENDING
        elif self.outcome == self.Outcome.PAID_REPORTED:
            self.is_flagged = False
            self.status = self.Status.PENDING
        elif self.outcome == self.Outcome.PAID:
            self.is_flagged = False
            self.status = self.Status.COMPLETED
            if not self.completed_at:
                self.completed_at = timezone.now()
        elif self.status == self.Status.COMPLETED:
            self.is_flagged = False
        elif self.outcome == self.Outcome.NOT_PAID:
            self.is_flagged = False
            self.status = self.Status.PENDING

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_outcome_display()} - {self.customer.name} by {self.employee.username}"


class FollowUpNotification(models.Model):
    """
    In-app alert for administrators regarding follow-up actions:
    - Employee reported a payment (awaiting verification)
    - Employee scheduled a promised payment / callback
    - Customer marked Payment Not Possible (highlighted red)
    - Customer needs decision for Loss / Review
    - Admin action or payment verification resolves an alert
    """
    class NotificationType(models.TextChoices):
        PAYMENT_REPORTED = "PAYMENT_REPORTED", "Payment Reported"
        PAYMENT_NOT_POSSIBLE = "PAYMENT_NOT_POSSIBLE", "Payment Not Possible"
        PROMISED_PAYMENT = "PROMISED_PAYMENT", "Promised Payment Scheduled"
        LOSS_REVIEW_NEEDED = "LOSS_REVIEW_NEEDED", "Loss Review Needed"
        ADMIN_RESOLVED = "ADMIN_RESOLVED", "Action Completed / Resolved"

    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.CASCADE,
        related_name="followup_notifications"
    )
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_followup_notifications"
    )
    activity = models.ForeignKey(
        FollowUpActivity,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications"
    )
    notification_type = models.CharField(
        max_length=40,
        choices=NotificationType.choices,
        default=NotificationType.PAYMENT_REPORTED
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)
    is_resolved = models.BooleanField(default=False, db_index=True)
    is_highlighted_red = models.BooleanField(default=False)
    action_url = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Follow-up Notification"
        verbose_name_plural = "Follow-up Notifications"

    def __str__(self):
        return f"[{self.get_notification_type_display()}] {self.customer.name} - {self.title}"
