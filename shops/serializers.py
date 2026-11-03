"""
REST API Serializers for the Shop Management module.
Used for future mobile Android APK and external service integrations.
"""

import re
from rest_framework import serializers
from .models import Shop


class ShopSerializer(serializers.ModelSerializer):
    """
    Serializer for Shop model. Excludes sensitive internals while providing
    full validation for mobile phone shops.
    """
    owner_username = serializers.CharField(source="owner_account.username", read_only=True)

    class Meta:
        model = Shop
        fields = [
            "id",
            "name",
            "owner_name",
            "phone",
            "email",
            "gpay",
            "address",
            "is_active",
            "owner_account",
            "owner_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "owner_username"]

    def validate_name(self, value):
        name = value.strip()
        if not name or len(name) < 2:
            raise serializers.ValidationError("Shop name must be at least 2 characters.")
        return name

    def validate_owner_name(self, value):
        owner_name = value.strip()
        if not owner_name or len(owner_name) < 2:
            raise serializers.ValidationError("Owner name must be at least 2 characters.")
        return owner_name

    def validate_phone(self, value):
        phone = value.strip()
        if not re.match(r"^\d{10}$", phone):
            raise serializers.ValidationError("Phone number must be exactly 10 digits without letters, spaces, or symbols.")
        return phone

    def validate_gpay(self, value):
        if value:
            gpay = value.strip()
            if not re.match(r"^\d{10}$", gpay):
                raise serializers.ValidationError("GPay number must be exactly 10 digits without letters, spaces, or symbols.")
            return gpay
        return ""

    def validate_address(self, value):
        address = value.strip()
        if not address or len(address) < 5:
            raise serializers.ValidationError("Please provide a valid address with at least 5 characters.")
        return address

