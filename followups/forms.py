"""
Forms for Follow-up Persons management, secure password setup, and follow-up logging.
"""

import re
from django import forms
from django.contrib.auth import get_user_model
from shops.models import Shop
from .models import FollowUpActivity

User = get_user_model()


class FollowUpEmployeeForm(forms.ModelForm):
    """
    Form used by administrators to add and edit follow-up employees
    and assign them to multiple retail shops.
    Supports secure activation link generation when password is omitted.
    """
    first_name = forms.CharField(
        max_length=150,
        required=True,
        label="Employee Name / First Name",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. Rahul",
        })
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        label="Last Name / Surname",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g. Sharma (optional)",
        })
    )
    email = forms.EmailField(
        required=True,
        label="Email Address",
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "employee@example.com",
        }),
        help_text="Required for account identification and password setup link."
    )
    phone = forms.CharField(
        max_length=10,
        min_length=10,
        required=True,
        label="Contact Phone Number",
        widget=forms.TextInput(attrs={
            "class": "form-control font-monospace",
            "placeholder": "10-digit mobile number",
            "maxlength": "10",
            "minlength": "10",
            "pattern": r"\d{10}",
            "inputmode": "numeric",
            "oninput": "this.value = this.value.replace(/[^0-9]/g, '').slice(0, 10);",
        }),
        help_text="Direct 10-digit mobile number for staff coordination."
    )
    username = forms.CharField(
        max_length=150,
        required=False,
        label="Username",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Leave blank to auto-generate from email",
        }),
        help_text="Optional. If left blank, a unique username will be automatically derived from email."
    )
    password = forms.CharField(
        required=False,
        label="Initial Password (Optional)",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Leave blank to generate secure setup link (Recommended)",
            "autocomplete": "new-password",
        }),
        help_text="Recommended: Leave blank to generate a secure activation link for the employee to set their own password."
    )
    confirm_password = forms.CharField(
        required=False,
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Confirm password if entering above",
            "autocomplete": "new-password",
        }),
    )
    assigned_shops = forms.ModelMultipleChoiceField(
        queryset=Shop.objects.all(),
        required=True,
        label="Assigned Retail Shops",
        widget=forms.CheckboxSelectMultiple(attrs={
            "class": "form-check-input",
        }),
        help_text="Select one or more retail shops this employee is authorized to monitor."
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone", "username", "is_active"]
        widgets = {
            "is_active": forms.CheckboxInput(attrs={
                "class": "form-check-input",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # Populate currently assigned shops
            current_shop_ids = self.instance.followup_assignments.filter(
                is_active=True
            ).values_list("shop_id", flat=True)
            self.fields["assigned_shops"].initial = current_shop_ids
            self.fields["assigned_shops"].required = False
            self.fields["password"].help_text = "Leave blank to keep existing password."
            self.fields["username"].required = True
        else:
            self.fields["is_active"].initial = True
            self.fields["assigned_shops"].required = True

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if not email:
            raise forms.ValidationError("Email address is required.")
        qs = User.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"A user with email '{email}' already exists.")
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        if not phone:
            raise forms.ValidationError("Contact phone number is required.")
        digits_only = re.sub(r"[^\d]", "", phone)
        if len(digits_only) != 10 or not re.match(r"^\d{10}$", phone):
            raise forms.ValidationError("Contact phone number must be exactly 10 digits without letters, spaces, or symbols.")
        return digits_only

    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if not username:
            # Auto-generate from email prefix
            email = self.cleaned_data.get("email", "").strip().lower()
            if email:
                base_username = re.sub(r"[^a-zA-Z0-9_]", "_", email.split("@")[0])[:25]
            else:
                base_username = "followup_user"
            
            candidate = base_username
            counter = 1
            qs = User.objects.filter(username__iexact=candidate)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            while qs.exists():
                candidate = f"{base_username}_{counter}"
                counter += 1
                qs = User.objects.filter(username__iexact=candidate)
                if self.instance and self.instance.pk:
                    qs = qs.exclude(pk=self.instance.pk)
            username = candidate

        qs = User.objects.filter(username__iexact=username)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"Username '{username}' is already taken.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")

        if password:
            if len(password) < 6:
                self.add_error("password", "Password must be at least 6 characters long.")
            if password != confirm_password:
                self.add_error("confirm_password", "Passwords do not match.")

        # Ensure at least one shop is selected when creating an employee
        assigned_shops = cleaned_data.get("assigned_shops")
        if not self.instance.pk and not assigned_shops:
            self.add_error("assigned_shops", "You must assign at least one retail shop to this employee.")

        return cleaned_data


class FollowUpPasswordSetupForm(forms.Form):
    """
    Form used by follow-up employees to securely set their password
    using the one-time activation link generated by the administrator.
    """
    new_password = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control form-control-lg",
            "placeholder": "Enter a strong password (min 6 characters)",
            "autocomplete": "new-password",
        }),
        help_text="Password must be at least 6 characters long."
    )
    confirm_password = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control form-control-lg",
            "placeholder": "Re-enter new password to confirm",
            "autocomplete": "new-password",
        }),
    )

    def clean(self):
        cleaned_data = super().clean()
        pwd = cleaned_data.get("new_password")
        confirm = cleaned_data.get("confirm_password")

        if pwd:
            if len(pwd) < 6:
                self.add_error("new_password", "Password must be at least 6 characters long.")
            if pwd != confirm:
                self.add_error("confirm_password", "Passwords do not match.")

        return cleaned_data


class FollowUpActivityForm(forms.ModelForm):
    """
    Form for logging follow-up actions (phone call, reminder, field visit)
    with outcome (Paid, Not Paid, Need two days time) and notes.
    """
    class Meta:
        model = FollowUpActivity
        fields = ["outcome", "activity_type", "status", "notes", "next_followup_date"]
        widgets = {
            "outcome": forms.Select(attrs={
                "class": "form-select fw-bold",
            }),
            "activity_type": forms.Select(attrs={
                "class": "form-select",
            }),
            "status": forms.Select(attrs={
                "class": "form-select",
            }),
            "notes": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Details of call, customer's commitment, payment promises, etc.",
            }),
            "next_followup_date": forms.DateInput(attrs={
                "class": "form-control",
                "type": "date",
            }),
        }

    def clean(self):
        from datetime import timedelta
        from django.utils import timezone
        cleaned_data = super().clean()
        outcome = cleaned_data.get("outcome")
        next_followup_date = cleaned_data.get("next_followup_date")

        if outcome == FollowUpActivity.Outcome.NEED_TWO_DAYS:
            if not next_followup_date:
                cleaned_data["next_followup_date"] = timezone.localdate() + timedelta(days=2)
            cleaned_data["status"] = FollowUpActivity.Status.PENDING

        elif outcome == FollowUpActivity.Outcome.PAID:
            cleaned_data["status"] = FollowUpActivity.Status.COMPLETED

        elif outcome == FollowUpActivity.Outcome.NOT_PAID:
            cleaned_data["status"] = FollowUpActivity.Status.PENDING

        return cleaned_data


# Backward-compatible alias
FollowUpStaffForm = FollowUpEmployeeForm

