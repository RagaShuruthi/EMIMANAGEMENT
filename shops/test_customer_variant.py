"""
Tests for mobile phone variants (Android & iPhone) on Shop Portal Customer Registration.
"""

from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse

from applications.models import FinanceApplication
from customers.models import Customer
from shops.models import Shop

User = get_user_model()


class CustomerVariantTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.shop_user = User.objects.create_user(
            username="shopowner_variant",
            password="Password123!",
            role=User.Role.SHOP_OWNER,
        )
        self.shop = Shop.objects.create(
            name="Mega Mobile Store",
            owner_name="Test Owner",
            phone="9876543210",
            address="123 High Street",
            owner_account=self.shop_user,
            is_active=True,
        )
        self.client.login(username="shopowner_variant", password="Password123!")

        self.photo = SimpleUploadedFile("cust_photo.jpg", b"fake_photo", content_type="image/jpeg")
        self.aadhaar_front = SimpleUploadedFile("front.jpg", b"fake_front", content_type="image/jpeg")
        self.aadhaar_back = SimpleUploadedFile("back.jpg", b"fake_back", content_type="image/jpeg")

    def test_customer_register_with_android_variant(self):
        """Test registering a customer with an Android variant (e.g. 8/256)."""
        data = {
            "customer_name": "Ramesh Kumar",
            "primary_mobile": "9876543210",
            "secondary_mobile": "9876543211",
            "work_address": "Work Address 101",
            "home_address": "Home Address 202",
            "device_type": "android",
            "mobile_model": "Samsung Galaxy S24 Ultra",
            "variant": "8/256",
            "mobile_price": "85000.00",
            "down_payment": "15000.00",
            "emi_duration": "6",
            "doc_photo": self.photo,
            "doc_aadhaar_front": self.aadhaar_front,
            "doc_aadhaar_back": self.aadhaar_back,
            "action": "submit",
        }
        resp = self.client.post(reverse("shop_customer_add"), data)
        self.assertEqual(resp.status_code, 302)

        app = FinanceApplication.objects.get(customer__primary_mobile="9876543210")
        self.assertEqual(app.variant, "8/256")
        self.assertIn("Samsung Galaxy S24 Ultra", app.mobile_model)
        self.assertIn("8/256", app.mobile_model)

    def test_customer_register_with_iphone_variant(self):
        """Test registering a customer with an iPhone variant (e.g. 256)."""
        data = {
            "customer_name": "Priya Sharma",
            "primary_mobile": "9876543220",
            "secondary_mobile": "9876543221",
            "work_address": "Apple Park",
            "home_address": "Green Avenue",
            "device_type": "iphone",
            "mobile_model": "iPhone 16 Pro",
            "variant": "256",
            "mobile_price": "120000.00",
            "down_payment": "20000.00",
            "emi_duration": "6",
            "doc_photo": self.photo,
            "doc_aadhaar_front": self.aadhaar_front,
            "doc_aadhaar_back": self.aadhaar_back,
            "action": "submit",
        }
        resp = self.client.post(reverse("shop_customer_add"), data)
        self.assertEqual(resp.status_code, 302)

        app = FinanceApplication.objects.get(customer__primary_mobile="9876543220")
        self.assertEqual(app.variant, "256")
        self.assertIn("iPhone 16 Pro", app.mobile_model)
        self.assertIn("256", app.mobile_model)

    def test_customer_register_page_get_contains_variant_field(self):
        """Verify the customer registration page HTML contains the variant dropdown and device toggle."""
        resp = self.client.get(reverse("shop_customer_add"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn('id="id_variant"', content)
        self.assertIn('name="variant"', content)
        self.assertIn('device_type_android', content)
        self.assertIn('device_type_iphone', content)
        self.assertIn('ANDROID_VARIANTS', content)
        self.assertIn('IPHONE_VARIANTS', content)
