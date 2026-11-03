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
        max_length=20,
        choices=Outcome.choices,
        default=Outcome.NOT_PAID,
        help_text="Update status logged by follow-up person (Paid, Not Paid, Need two days time)."
    )
    is_flagged = models.BooleanField(
        default=False,
        help_text="Flagged when customer needs two days time and requires follow-up callback."
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
    next_followup_date = models.DateField(
        null=True,
        blank=True,
        help_text="Scheduled date for the next follow-up action."
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
        elif self.outcome == self.Outcome.PAID:
            self.is_flagged = False
            self.status = self.Status.COMPLETED
            if not self.completed_at:
                self.completed_at = timezone.now()
        elif self.status == self.Status.COMPLETED:
            # Respect completed status if explicitly marked completed
            self.is_flagged = False
        elif self.outcome == self.Outcome.NOT_PAID:
            self.is_flagged = False
            self.status = self.Status.PENDING

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_outcome_display()} - {self.customer.name} by {self.employee.username}"
