"""
Forms for the Shop Management module.
Includes server-side validation for phone numbers, mandatory fields,
digital payment (GPay/UPI), bank account details,
and integrated Shop Owner account and password creation.
"""

import re
from django import forms
from django.contrib.auth import get_user_model
from .models import Shop

User = get_user_model()


class ShopForm(forms.ModelForm):
    """
    Form for registering and editing retail shop records.
    Provides fields for shop profile, GPay and bank account disbursement details,
    as well as optional inline shop-owner login account creation.
    """
    create_credentials = forms.BooleanField(
        required=False,
        initial=False,
        label="Create Shop Owner Login Account",
        help_text="Check this box to set up portal login credentials (username and password)."
    ) 
    login_username = forms.CharField(
        max_length=150,
        required=False,
        label="Portal Username",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g., atlas_shop or sowndar123",
            "autocomplete": "off",
            "id": "id_login_username",
        }),
        help_text="Unique username the shop owner will use to sign in."
    )
    login_password = forms.CharField(
        required=False,
        label="Portal Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Enter password (at least 6 characters)",
            "autocomplete": "new-password",
            "id": "id_login_password",
        }),
        help_text="Secure password for the shop owner account."
    )
    confirm_password = forms.CharField(
        required=False,
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Re-enter password",
            "autocomplete": "new-password",
            "id": "id_confirm_password",
        })
    )

    class Meta:
        model = Shop
        fields = [
            "name",
            "owner_name",
            "phone",
            "email",
            "gpay",
            "address",
            "bank_account_holder_name",
            "bank_name",
            "bank_account_number",
            "bank_ifsc_code",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g., Summit Mobile Hub",
                "required": True,
            }),
            "owner_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g., John Smith",
                "required": True,
            }),
            "phone": forms.TextInput(attrs={
                "class": "form-control font-monospace",
                "placeholder": "10-digit mobile number",
                "required": True,
                "maxlength": "10",
                "minlength": "10",
                "pattern": r"\d{10}",
                "inputmode": "numeric",
                "oninput": "this.value = this.value.replace(/[^0-9]/g, '').slice(0, 10);",
            }),
            "email": forms.EmailInput(attrs={
                "class": "form-control",
                "placeholder": "e.g., contact@summitmobile.com (optional)",
            }),
            "gpay": forms.TextInput(attrs={
                "class": "form-control font-monospace",
                "placeholder": "10-digit GPay number (optional)",
                "maxlength": "10",
                "pattern": r"\d{10}",
                "inputmode": "numeric",
                "oninput": "this.value = this.value.replace(/[^0-9]/g, '').slice(0, 10);",
            }),
            "address": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Enter complete physical shop address",
                "required": True,
            }),
            "bank_account_holder_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g., Summit Mobile Store / John Smith",
            }),
            "bank_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g., State Bank of India, HDFC Bank, ICICI Bank",
            }),
            "bank_account_number": forms.TextInput(attrs={
                "class": "form-control font-monospace",
                "placeholder": "e.g., 10293847561029",
            }),
            "bank_ifsc_code": forms.TextInput(attrs={
                "class": "form-control text-uppercase font-monospace",
                "placeholder": "e.g., SBIN0001234 or HDFC0000123",
            }),
            "is_active": forms.CheckboxInput(attrs={
                "class": "form-check-input",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = False
        self.fields["gpay"].required = False
        self.fields["bank_account_holder_name"].required = False
        self.fields["bank_name"].required = False
        self.fields["bank_account_number"].required = False
        self.fields["bank_ifsc_code"].required = False

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Shop name is required.")
        if len(name) < 2:
            raise forms.ValidationError("Shop name must be at least 2 characters.")
        return name

    def clean_owner_name(self):
        owner_name = self.cleaned_data.get("owner_name", "").strip()
        if not owner_name:
            raise forms.ValidationError("Owner or contact person's name is required.")
        if len(owner_name) < 2:
            raise forms.ValidationError("Owner name must be at least 2 characters.")
        return owner_name

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        if not phone:
            raise forms.ValidationError("Contact phone number is required.")

        digits_only = re.sub(r"[^\d]", "", phone)
        if len(digits_only) != 10 or not re.match(r"^\d{10}$", phone):
            raise forms.ValidationError("Contact phone number must be exactly 10 digits without letters, spaces, or symbols.")

        return digits_only

    def clean_address(self):
        address = self.cleaned_data.get("address", "").strip()
        if not address:
            raise forms.ValidationError("Shop address is required.")
        if len(address) < 5:
            raise forms.ValidationError("Please provide a complete address (at least 5 characters).")
        return address

    def clean_gpay(self):
        gpay = self.cleaned_data.get("gpay", "").strip()
        if gpay:
            digits_only = re.sub(r"[^\d]", "", gpay)
            if len(digits_only) != 10 or not re.match(r"^\d{10}$", gpay):
                raise forms.ValidationError("GPay mobile number must be exactly 10 digits without letters, spaces, or symbols.")
            return digits_only
        return ""

    def clean_bank_ifsc_code(self):
        ifsc = self.cleaned_data.get("bank_ifsc_code", "").strip().upper()
        if ifsc and len(ifsc) < 4:
            raise forms.ValidationError("Please enter a valid IFSC code (e.g. SBIN0001234).")
        return ifsc

    def clean(self):
        cleaned_data = super().clean()
        create_credentials = cleaned_data.get("create_credentials")
        login_username = cleaned_data.get("login_username", "").strip()
        login_password = cleaned_data.get("login_password", "")
        confirm_password = cleaned_data.get("confirm_password", "")

        # If user checked create_credentials OR entered a username/password
        if create_credentials or login_username or login_password:
            if not login_username:
                self.add_error("login_username", "Username is required when creating a login account.")
            elif User.objects.filter(username__iexact=login_username).exists():
                self.add_error("login_username", f"Username '{login_username}' is already taken. Please choose another.")

            if not login_password:
                self.add_error("login_password", "Password is required when creating a login account.")
            elif len(login_password) < 6:
                self.add_error("login_password", "Password must be at least 6 characters long.")

            if login_password and confirm_password and login_password != confirm_password:
                self.add_error("confirm_password", "Passwords do not match.")

        return cleaned_data


class ShopAccountForm(forms.Form):
    """
    Dedicated form for creating or resetting a Shop Owner's portal credentials.
    """
    username = forms.CharField(
        max_length=150,
        required=True,
        label="Portal Username",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "e.g., atlas_shop",
        })
    )
    password = forms.CharField(
        required=True,
        label="Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Enter password (min 6 characters)",
            "autocomplete": "new-password",
        })
    )
    confirm_password = forms.CharField(
        required=True,
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Re-enter password",
            "autocomplete": "new-password",
        })
    )

    def __init__(self, *args, shop=None, is_existing_account=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.shop = shop
        self.is_existing_account = is_existing_account
        if is_existing_account and shop and shop.owner_account:
            self.fields["username"].initial = shop.owner_account.username
            self.fields["username"].widget.attrs["readonly"] = True
            self.fields["username"].help_text = "Username cannot be changed here; setting a new password below."

    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if not self.is_existing_account:
            if User.objects.filter(username__iexact=username).exists():
                raise forms.ValidationError(f"Username '{username}' is already in use.")
        return username

    def clean_password(self):
        password = self.cleaned_data.get("password", "")
        if len(password) < 6:
            raise forms.ValidationError("Password must be at least 6 characters long.")
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")
        if password and confirm_password and password != confirm_password:
            self.add_error("confirm_password", "Passwords do not match.")
        return cleaned_data
