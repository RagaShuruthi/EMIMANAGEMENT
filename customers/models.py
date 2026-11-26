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
    failed_payment_reason = models.TextField(
        blank=True,
        default="",
        help_text="Preserved previous failed payment reason when customer is reopened."
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

    @property
    def is_duplicate_number(self):
        """
        True if another customer was registered prior to this customer
        using the same primary or secondary mobile number.
        """
        if hasattr(self, "_is_duplicate_number"):
            return self._is_duplicate_number

        if not self.primary_mobile:
            return False

        clean_p = self.primary_mobile.strip()
        q = models.Q(primary_mobile=clean_p) | models.Q(secondary_mobile=clean_p)
        if self.secondary_mobile and self.secondary_mobile.strip():
            clean_s = self.secondary_mobile.strip()
            q |= models.Q(primary_mobile=clean_s) | models.Q(secondary_mobile=clean_s)

        qs = Customer.objects.filter(q)
        if self.pk:
            if self.created_at:
                qs = qs.filter(
                    models.Q(created_at__lt=self.created_at) |
                    (models.Q(created_at=self.created_at) & models.Q(pk__lt=self.pk))
                )
            else:
                qs = qs.filter(pk__lt=self.pk)
        return qs.exists()

    @classmethod
    def annotate_duplicates(cls, customer_list):
        """
        Efficiently precomputes `_is_duplicate_number = True/False` on a list of Customer objects
        using a single batch query.
        """
        customers = list(customer_list)
        numbers = set()
        for c in customers:
            if c.primary_mobile and c.primary_mobile.strip():
                numbers.add(c.primary_mobile.strip())
            if c.secondary_mobile and c.secondary_mobile.strip():
                numbers.add(c.secondary_mobile.strip())

        if not numbers or not customers:
            for c in customers:
                c._is_duplicate_number = False
            return customers

        # Find earliest registered customer (by created_at, id) for each phone number
        earliest_by_number = {}
        earlier_matches = cls.objects.filter(
            models.Q(primary_mobile__in=numbers) | models.Q(secondary_mobile__in=numbers)
        ).values("id", "primary_mobile", "secondary_mobile", "created_at").order_by("created_at", "id")

        for rec in earlier_matches:
            rec_id = rec["id"]
            rec_time = rec["created_at"]
            p = rec["primary_mobile"].strip() if rec["primary_mobile"] else ""
            s = rec["secondary_mobile"].strip() if rec["secondary_mobile"] else ""
            for num in (p, s):
                if num and num in numbers and num not in earliest_by_number:
                    earliest_by_number[num] = (rec_time, rec_id)

        for c in customers:
            is_dup = False
            c_time = c.created_at
            c_id = c.id or 0
            p = c.primary_mobile.strip() if c.primary_mobile else ""
            s = c.secondary_mobile.strip() if c.secondary_mobile else ""

            for num in (p, s):
                if num and num in earliest_by_number:
                    earliest_time, earliest_id = earliest_by_number[num]
                    if c_time and earliest_time:
                        if earliest_time < c_time or (earliest_time == c_time and earliest_id < c_id):
                            is_dup = True
                            break
                    elif c_id and earliest_id < c_id:
                        is_dup = True
                        break
            c._is_duplicate_number = is_dup

        return customers


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
    section = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Section where the update occurred (e.g. EMI Installments, Customer Profile)."
    )
    field_name = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Specific field updated (e.g. Amount Paid, Amount Due, EMI Due Date, Status)."
    )
    previous_value = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Value prior to modification."
    )
    new_value = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="New updated value."
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Detailed description of the modification."
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

