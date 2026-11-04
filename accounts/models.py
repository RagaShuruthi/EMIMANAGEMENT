"""
Custom User model for EMI Management System.
Supports role-based authorization for Administrators, Shop Owners, and Employees.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrator"
        SHOP_OWNER = "SHOP_OWNER", "Shop Owner"
        EMPLOYEE = "EMPLOYEE", "Employee"
        FOLLOWUP = "FOLLOWUP", "Follow-up Employee"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.ADMIN,
        help_text="Specifies user role and access level across the platform."
    )
    phone = models.CharField(
        max_length=25,
        blank=True,
        default="",
        help_text="Contact phone number."
    )
    assigned_password = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Password assigned by administrator for staff reference."
    )

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN or self.is_superuser or self.is_staff

    @property
    def is_shop_owner_role(self):
        return self.role == self.Role.SHOP_OWNER

    @property
    def is_followup_role(self):
        return self.role in (self.Role.FOLLOWUP, self.Role.EMPLOYEE)

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"
