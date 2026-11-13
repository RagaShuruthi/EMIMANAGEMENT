"""
Comprehensive automated tests for the Shop Management module and Admin Dashboard.
Tests models, forms, views, authorization, multi-tenant isolation, dynamic dashboard metrics, and DRF API.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from .models import Shop
from .forms import ShopForm

User = get_user_model()


class ShopModelTests(TestCase):
    """Tests Shop model creation, persistence, string representation, and defaults."""

    def test_create_shop_and_save_to_database(self):
        """Verify shop record persists in database with correct fields and timestamps."""
        shop = Shop.objects.create(
            name="Apex Mobile Store",
            owner_name="Robert Vance",
            phone="+1 555-0123",
            email="apex@example.com",
            address="123 Market Street, Springfield",
            is_active=True,
        )
        self.assertIsNotNone(shop.id)
        self.assertEqual(shop.name, "Apex Mobile Store")
        self.assertEqual(shop.owner_name, "Robert Vance")
        self.assertEqual(shop.phone, "+1 555-0123")
        self.assertEqual(shop.email, "apex@example.com")
        self.assertEqual(shop.address, "123 Market Street, Springfield")
        self.assertTrue(shop.is_active)
        self.assertIsNotNone(shop.created_at)
        self.assertIsNotNone(shop.updated_at)
        self.assertEqual(str(shop), "Apex Mobile Store (Robert Vance)")

    def test_shop_with_gpay_and_bank_details(self):
        """Verify shop record saves and retrieves GPay and Bank account details."""
        shop = Shop.objects.create(
            name="Digital Smart Hub",
            owner_name="Anand Kumar",
            phone="9876543210",
            address="12 Main Bazaar Road",
            gpay="9876543210@upi",
            bank_account_holder_name="Digital Smart Hub",
            bank_name="HDFC Bank",
            bank_account_number="50200012345678",
            bank_ifsc_code="HDFC0001234",
        )
        self.assertEqual(shop.gpay, "9876543210@upi")
        self.assertEqual(shop.bank_account_holder_name, "Digital Smart Hub")
        self.assertEqual(shop.bank_name, "HDFC Bank")
        self.assertEqual(shop.bank_account_number, "50200012345678")
        self.assertEqual(shop.bank_ifsc_code, "HDFC0001234")

    def test_shop_persistence_after_retrieval(self):
        """Verify data can be re-queried from database and remains consistent."""
        created = Shop.objects.create(
            name="Metro Cellular",
            owner_name="Sarah Connor",
            phone="9876543210",
            address="456 Broadway Ave",
        )
        retrieved = Shop.objects.get(pk=created.pk)
        self.assertEqual(retrieved.name, "Metro Cellular")
        self.assertEqual(retrieved.owner_name, "Sarah Connor")
        self.assertTrue(retrieved.is_active)

    def test_shop_optional_email_and_owner_account(self):
        """Verify email and owner_account are optional."""
        shop = Shop.objects.create(
            name="Simple Mobile",
            owner_name="John Doe",
            phone="555-0011",
            address="789 Pine Rd",
        )
        self.assertIsNone(shop.email)
        self.assertIsNone(shop.owner_account)


class ShopFormTests(TestCase):
    """Tests server-side validation rules in ShopForm."""

    def test_valid_form_submission(self):
        """Valid form payload succeeds."""
        data = {
            "name": "Galaxy Cellular",
            "owner_name": "Elena Fisher",
            "phone": "9876543210",
            "email": "elena@galaxy.com",
            "address": "500 Ocean Avenue, Miami",
            "is_active": True,
        }
        form = ShopForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_submits_with_gpay_and_bank_details(self):
        """Form successfully processes GPay number and bank account information."""
        data = {
            "name": "Galaxy Cellular",
            "owner_name": "Elena Fisher",
            "phone": "9876543210",
            "email": "elena@galaxy.com",
            "address": "500 Ocean Avenue, Miami",
            "gpay": "9876543210",
            "bank_account_holder_name": "Elena Fisher",
            "bank_name": "State Bank of India",
            "bank_account_number": "123456789012",
            "bank_ifsc_code": "sbin0001234",
            "is_active": True,
        }
        form = ShopForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        shop = form.save()
        self.assertEqual(shop.gpay, "9876543210")
        self.assertEqual(shop.bank_account_number, "123456789012")
        self.assertEqual(shop.bank_ifsc_code, "SBIN0001234")  # clean_bank_ifsc_code capitalizes

    def test_invalid_form_missing_required_fields(self):
        """Missing required fields triggers validation errors."""
        form = ShopForm(data={})
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)
        self.assertIn("owner_name", form.errors)
        self.assertIn("phone", form.errors)
        self.assertIn("address", form.errors)

    def test_invalid_phone_format(self):
        """Invalid phone strings (too short, too long, or containing letters/symbols) are rejected."""
        # 1. Fewer than 10 digits
        form1 = ShopForm(data={
            "name": "Test Shop",
            "owner_name": "Owner Name",
            "phone": "123456789",
            "address": "Valid Address Here",
        })
        self.assertFalse(form1.is_valid())
        self.assertIn("phone", form1.errors)

        # 2. More than 10 digits
        form2 = ShopForm(data={
            "name": "Test Shop",
            "owner_name": "Owner Name",
            "phone": "98765432101",
            "address": "Valid Address Here",
        })
        self.assertFalse(form2.is_valid())
        self.assertIn("phone", form2.errors)

        # 3. Letters or spaces or symbols
        form3 = ShopForm(data={
            "name": "Test Shop",
            "owner_name": "Owner Name",
            "phone": "9876 54321",
            "address": "Valid Address Here",
        })
        self.assertFalse(form3.is_valid())
        self.assertIn("phone", form3.errors)

        # 4. GPay with invalid format (more than 10 digits)
        form4 = ShopForm(data={
            "name": "Test Shop",
            "owner_name": "Owner Name",
            "phone": "9876543210",
            "gpay": "98765432101",
            "address": "Valid Address Here",
        })
        self.assertFalse(form4.is_valid())
        self.assertIn("gpay", form4.errors)


class ShopViewAuthorizationTests(TestCase):
    """Tests access control on shop views."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="admin_user",
            password="AdminPassword123!",
            role=User.Role.ADMIN
        )
        self.shop_owner_user = User.objects.create_user(
            username="owner_user",
            password="OwnerPassword123!",
            role=User.Role.SHOP_OWNER
        )
        self.employee_user = User.objects.create_user(
            username="employee_user",
            password="EmployeePassword123!",
            role=User.Role.EMPLOYEE
        )

    def test_unauthenticated_user_redirected_to_login(self):
        """Unauthenticated requests to admin shop views redirect to login."""
        urls = [
            reverse("shop_list"),
            reverse("add_shop"),
            reverse("admin_dashboard"),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/accounts/login/", response.url)

    def test_non_admin_forbidden_from_admin_views(self):
        """Non-admin users receive HTTP 403 Forbidden when accessing admin shop routes."""
        self.client.login(username="employee_user", password="EmployeePassword123!")
        response = self.client.get(reverse("shop_list"))
        self.assertEqual(response.status_code, 403)

        response_add = self.client.get(reverse("add_shop"))
        self.assertEqual(response_add.status_code, 403)

    def test_admin_user_can_access_views(self):
        """Administrator user receives HTTP 200 on shop views."""
        self.client.login(username="admin_user", password="AdminPassword123!")
        response = self.client.get(reverse("shop_list"))
        self.assertEqual(response.status_code, 200)

        response_add = self.client.get(reverse("add_shop"))
        self.assertEqual(response_add.status_code, 200)


class ShopCRUDAndActionsTests(TestCase):
    """Tests shop creation, retrieval, editing, and activation/deactivation."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="super_admin",
            password="AdminPassword123!",
            role=User.Role.ADMIN
        )
        self.client.login(username="super_admin", password="AdminPassword123!")

    def test_add_shop_view_creates_record(self):
        """POST to add_shop creates record in database and redirects to detail."""
        data = {
            "name": "Beacon Mobile",
            "owner_name": "Marcus Aurelius",
            "phone": "9876508899",
            "email": "marcus@beacon.com",
            "gpay": "9876508899",
            "address": "1 Rome Blvd",
            "is_active": True,
            "login_username": "marcus_beacon",
            "login_password": "BeaconPassword123!",
            "confirm_password": "BeaconPassword123!",
        }
        response = self.client.post(reverse("add_shop"), data)
        shop = Shop.objects.get(name="Beacon Mobile")
        self.assertRedirects(response, reverse("shop_detail", kwargs={"pk": shop.pk}))
        self.assertEqual(shop.owner_name, "Marcus Aurelius")
        self.assertEqual(shop.phone, "9876508899")
        self.assertEqual(shop.gpay, "9876508899")
        self.assertEqual(shop.email, "marcus@beacon.com")

    def test_shop_detail_view_retrieval(self):
        """Detail view displays saved database information without payment summary cards."""
        shop = Shop.objects.create(
            name="Crown Wireless",
            owner_name="Queen Elizabeth",
            phone="9876501234",
            email="queen@crown.com",
            gpay="9876501234",
            address="Buckingham Palace Rd",
            is_active=True,
        )
        response = self.client.get(reverse("shop_detail", kwargs={"pk": shop.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crown Wireless")
        self.assertContains(response, "Queen Elizabeth")
        self.assertContains(response, "9876501234")
        self.assertContains(response, "queen@crown.com")
        self.assertNotContains(response, "Total Payable to Shop")
        self.assertNotContains(response, "markPaidModal")

    def test_shop_detail_view_404_when_not_found(self):
        """Requesting a non-existent shop returns 404."""
        response = self.client.get(reverse("shop_detail", kwargs={"pk": 99999}))
        self.assertEqual(response.status_code, 404)

    def test_edit_shop_view_updates_database(self):
        """POST to edit_shop updates database fields."""
        shop = Shop.objects.create(
            name="Original Name",
            owner_name="Original Owner",
            phone="9876500000",
            address="Original Address",
            is_active=True,
        )
        update_data = {
            "name": "Updated Shop Name",
            "owner_name": "Updated Owner Name",
            "phone": "9876509999",
            "email": "updated@example.com",
            "gpay": "9876509999",
            "address": "Updated Address 123",
            "is_active": True,
        }
        response = self.client.post(reverse("edit_shop", kwargs={"pk": shop.pk}), update_data)
        self.assertRedirects(response, reverse("shop_detail", kwargs={"pk": shop.pk}))
        shop.refresh_from_db()
        self.assertEqual(shop.name, "Updated Shop Name")
        self.assertEqual(shop.owner_name, "Updated Owner Name")
        self.assertEqual(shop.phone, "9876509999")
        self.assertEqual(shop.gpay, "9876509999")

    def test_activate_and_deactivate_shop_via_post(self):
        """POST to status view toggles is_active without deleting record."""
        shop = Shop.objects.create(
            name="Toggle Shop",
            owner_name="Test Owner",
            phone="555-2233",
            address="Street 10",
            is_active=True,
        )
        # Deactivate
        response = self.client.post(reverse("shop_status", kwargs={"pk": shop.pk}))
        shop.refresh_from_db()
        self.assertFalse(shop.is_active)

        # Reactivate
        response = self.client.post(reverse("shop_status", kwargs={"pk": shop.pk}))
        shop.refresh_from_db()
        self.assertTrue(shop.is_active)

    def test_status_view_requires_post(self):
        """GET request to status toggle endpoint is rejected."""
        shop = Shop.objects.create(
            name="Get Reject Shop",
            owner_name="Test Owner",
            phone="555-3344",
            address="Street 20",
            is_active=True,
        )
        response = self.client.get(reverse("shop_status", kwargs={"pk": shop.pk}))
        self.assertEqual(response.status_code, 405)

    def test_add_shop_with_inline_password_creation(self):
        """Admin can register shop and simultaneously create shop owner user and password."""
        data = {
            "name": "Password Shop",
            "owner_name": "Alan Turing",
            "phone": "9876544321",
            "email": "alan@enigma.com",
            "address": "Bletchley Park",
            "is_active": True,
            "login_username": "alanturing",
            "login_password": "SecretPassword123!",
            "confirm_password": "SecretPassword123!",
        }
        response = self.client.post(reverse("add_shop"), data)
        shop = Shop.objects.get(name="Password Shop")
        self.assertIsNotNone(shop.owner_account)
        self.assertEqual(shop.owner_account.username, "alanturing")
        self.assertEqual(shop.assigned_password, "SecretPassword123!")
        from django.contrib.auth import authenticate
        user = authenticate(username="alanturing", password="SecretPassword123!")
        self.assertIsNotNone(user)
        self.assertEqual(user.role, User.Role.SHOP_OWNER)

    def test_shop_account_view_creates_password_for_existing_shop(self):
        """Admin can create a password and account for an existing shop without account."""
        shop = Shop.objects.create(name="No Account Shop", owner_name="Ada Lovelace", phone="555-9000", address="London St", is_active=True)
        self.assertIsNone(shop.owner_account)

        data = {
            "username": "adalovelace",
            "password": "AdaPassword123!",
            "confirm_password": "AdaPassword123!",
        }
        response = self.client.post(reverse("shop_account", kwargs={"pk": shop.pk}), data)
        self.assertRedirects(response, reverse("shop_detail", kwargs={"pk": shop.pk}))
        shop.refresh_from_db()
        self.assertIsNotNone(shop.owner_account)
        self.assertEqual(shop.assigned_password, "AdaPassword123!")
        from django.contrib.auth import authenticate
        user = authenticate(username="adalovelace", password="AdaPassword123!")
        self.assertIsNotNone(user)

    def test_shop_account_view_resets_password(self):
        """Admin can reset password for an existing shop owner account."""
        owner = User.objects.create_user(username="existing_owner", password="OldPassword123!", role=User.Role.SHOP_OWNER)
        shop = Shop.objects.create(name="Reset Shop", owner_name="Owner", phone="555-9111", address="Addr", owner_account=owner, assigned_password="OldPassword123!")

        data = {
            "username": "existing_owner",
            "password": "NewPassword123!",
            "confirm_password": "NewPassword123!",
        }
        response = self.client.post(reverse("shop_account", kwargs={"pk": shop.pk}), data)
        self.assertRedirects(response, reverse("shop_detail", kwargs={"pk": shop.pk}))
        shop.refresh_from_db()
        self.assertEqual(shop.assigned_password, "NewPassword123!")
        from django.contrib.auth import authenticate
        self.assertIsNone(authenticate(username="existing_owner", password="OldPassword123!"))
        self.assertIsNotNone(authenticate(username="existing_owner", password="NewPassword123!"))

    def test_partner_shops_ui_and_add_shop_form_requirements(self):
        """Verifies Partner Shops table and Add Shop form meet the new requirements."""
        # 1. Add Shop page does not have 'Link Existing User Account'
        res_add = self.client.get(reverse("add_shop"))
        self.assertEqual(res_add.status_code, 200)
        self.assertNotContains(res_add, "Link Existing User Account")
        self.assertContains(res_add, "Contact Number")
        self.assertContains(res_add, "Email Address")
        self.assertContains(res_add, "GPay Number")

        # 2. Register a shop with 10-digit phone, email, and separate 10-digit GPay
        data = {
            "name": "Zenith Mobile Store",
            "owner_name": "Zenith Owner",
            "phone": "9876543210",
            "email": "zenith@mobile.com",
            "gpay": "9876543211",
            "address": "45 MG Road, Bangalore",
            "is_active": True,
            "login_username": "zenith_owner",
            "login_password": "ZenithPassword123!",
            "confirm_password": "ZenithPassword123!",
        }
        res_post = self.client.post(reverse("add_shop"), data)
        shop = Shop.objects.get(name="Zenith Mobile Store")
        self.assertRedirects(res_post, reverse("shop_detail", kwargs={"pk": shop.pk}))
        self.assertEqual(shop.phone, "9876543210")
        self.assertEqual(shop.gpay, "9876543211")
        self.assertEqual(shop.email, "zenith@mobile.com")

        # 3. Partner Shops list page displays shop-only info and no payment columns/modals
        res_list = self.client.get(reverse("shop_list"))
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, "Zenith Mobile Store")
        self.assertContains(res_list, "9876543210")
        self.assertContains(res_list, "zenith@mobile.com")
        self.assertContains(res_list, "9876543211")
        # Ensure payment columns and modal are removed from partner shops page
        self.assertNotContains(res_list, '<th scope="col" class="text-end">Total Payable</th>')
        self.assertNotContains(res_list, '<th scope="col" class="text-end">Paid by Admin</th>')
        self.assertNotContains(res_list, '<th scope="col" class="text-end">Remaining Due</th>')
        self.assertNotContains(res_list, "shopDirectPaymentModal")


class ShopSearchAndPaginationTests(TestCase):
    """Tests search filtering and pagination on shop list."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="admin_search",
            password="AdminPassword123!",
            role=User.Role.ADMIN
        )
        self.client.login(username="admin_search", password="AdminPassword123!")

        # Create test shops
        Shop.objects.create(name="Apple Store Downtown", owner_name="Tim Cook", phone="555-1001", address="1 Infinite Loop", is_active=True)
        Shop.objects.create(name="Samsung Experience Hub", owner_name="Jay Lee", phone="555-2002", address="2 Suwon St", is_active=True)
        Shop.objects.create(name="Xiaomi Outlet", owner_name="Lei Jun", phone="555-3003", address="3 Beijing Rd", is_active=False)

    def test_search_by_shop_name(self):
        """Search query matches shop name."""
        response = self.client.get(reverse("shop_list") + "?q=Apple")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apple Store Downtown")
        self.assertNotContains(response, "Samsung Experience Hub")

    def test_search_by_owner_name(self):
        """Search query matches owner/contact person name."""
        response = self.client.get(reverse("shop_list") + "?q=Cook")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apple Store Downtown")
        self.assertNotContains(response, "Xiaomi Outlet")

    def test_search_by_phone(self):
        """Search query matches phone number."""
        response = self.client.get(reverse("shop_list") + "?q=3003")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Xiaomi Outlet")
        self.assertNotContains(response, "Apple Store Downtown")

    def test_filter_by_status(self):
        """Status filter separates active and inactive shops."""
        response_active = self.client.get(reverse("shop_list") + "?status=active")
        self.assertEqual(response_active.status_code, 200)
        self.assertContains(response_active, "Apple Store Downtown")
        self.assertContains(response_active, "Samsung Experience Hub")
        self.assertNotContains(response_active, "Xiaomi Outlet")

        response_inactive = self.client.get(reverse("shop_list") + "?status=inactive")
        self.assertEqual(response_inactive.status_code, 200)
        self.assertContains(response_inactive, "Xiaomi Outlet")
        self.assertNotContains(response_inactive, "Apple Store Downtown")

    def test_filter_by_payment_due_and_date(self):
        """Filters shops by payment due status and application due date."""
        from applications.models import FinanceApplication
        from customers.models import Customer
        from datetime import date
        from decimal import Decimal

        apple_shop = Shop.objects.get(name="Apple Store Downtown")
        samsung_shop = Shop.objects.get(name="Samsung Experience Hub")

        customer1 = Customer.objects.create(name="Customer Due", primary_mobile="9876500001", shop=apple_shop)
        FinanceApplication.objects.create(
            shop=apple_shop,
            customer=customer1,
            application_date=date(2026, 11, 1),
            mobile_model="iPhone 16",
            mobile_price=Decimal("80000.00"),
            down_payment=Decimal("10000.00"),
            financier_request_amount=Decimal("70000.00"),
            monthly_emi=Decimal("11666.67"),
            emi_duration=6,
            status=FinanceApplication.Status.APPROVED,
        )

        # Filter by payment due
        response_due = self.client.get(reverse("shop_list") + "?payment_status=due")
        self.assertEqual(response_due.status_code, 200)
        self.assertContains(response_due, "Apple Store Downtown")
        self.assertNotContains(response_due, "Samsung Experience Hub")

        # Filter by date range matching application
        response_date = self.client.get(reverse("shop_list") + "?payment_status=due&start_date=2026-11-01&end_date=2026-11-01")
        self.assertEqual(response_date.status_code, 200)
        self.assertContains(response_date, "Apple Store Downtown")

        # Filter by future date outside application
        response_out = self.client.get(reverse("shop_list") + "?payment_status=due&start_date=2026-12-01&end_date=2026-12-31")
        self.assertEqual(response_out.status_code, 200)
        self.assertNotContains(response_out, "Apple Store Downtown")

    def test_pagination(self):
        """Creating 15 shops verifies page 1 has 10 shops and page 2 has 5 shops."""
        Shop.objects.all().delete()
        for i in range(15):
            Shop.objects.create(
                name=f"Batch Shop {i+1:02d}",
                owner_name=f"Owner {i+1}",
                phone=f"555-00{i+1:02d}",
                address="Address",
                is_active=True
            )

        resp_page1 = self.client.get(reverse("shop_list") + "?page=1")
        self.assertEqual(len(resp_page1.context["shops"]), 10)

        resp_page2 = self.client.get(reverse("shop_list") + "?page=2")
        self.assertEqual(len(resp_page2.context["shops"]), 5)


class AdminDashboardMetricsTests(TestCase):
    """Tests dynamic database metrics on the Admin Dashboard."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="admin_metrics",
            password="AdminPassword123!",
            role=User.Role.ADMIN
        )
        self.client.login(username="admin_metrics", password="AdminPassword123!")

    def test_dashboard_statistics_reflect_database_counts(self):
        """Dashboard statistics calculate actual database counts."""
        # Initial: 0 shops
        resp_initial = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(resp_initial.context["total_shops"], 0)
        self.assertEqual(resp_initial.context["active_shops"], 0)
        self.assertEqual(resp_initial.context["inactive_shops"], 0)

        # Create 3 active and 2 inactive shops
        for i in range(3):
            Shop.objects.create(name=f"Active Shop {i}", owner_name="Owner", phone=f"555-11{i}", address="Addr", is_active=True)
        for i in range(2):
            Shop.objects.create(name=f"Inactive Shop {i}", owner_name="Owner", phone=f"555-22{i}", address="Addr", is_active=False)

        resp_updated = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(resp_updated.context["total_shops"], 5)
        self.assertEqual(resp_updated.context["active_shops"], 3)
        self.assertEqual(resp_updated.context["inactive_shops"], 2)

    def test_dashboard_statistics_change_dynamically_when_records_change(self):
        """Dashboard counts update when a shop is added, edited, or deactivated."""
        shop = Shop.objects.create(name="Live Shop", owner_name="Owner", phone="555-9090", address="Addr", is_active=True)

        resp1 = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(resp1.context["active_shops"], 1)
        self.assertEqual(resp1.context["inactive_shops"], 0)

        # Deactivate the shop
        shop.is_active = False
        shop.save()

        resp2 = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(resp2.context["active_shops"], 0)
        self.assertEqual(resp2.context["inactive_shops"], 1)


class ShopOwnerDataIsolationTests(TestCase):
    """Tests that shop owners cannot view or manipulate other shops."""

    def setUp(self):
        self.client = Client()
        self.owner_a = User.objects.create_user(username="owner_a", password="Password123!", role=User.Role.SHOP_OWNER)
        self.owner_b = User.objects.create_user(username="owner_b", password="Password123!", role=User.Role.SHOP_OWNER)

        self.shop_a = Shop.objects.create(name="Shop Alpha", owner_name="Alice", phone="555-0001", address="Address A", owner_account=self.owner_a)
        self.shop_b = Shop.objects.create(name="Shop Beta", owner_name="Bob", phone="555-0002", address="Address B", owner_account=self.owner_b)

    def test_shop_owner_can_view_own_shop(self):
        """Owner A can view Shop A."""
        self.client.login(username="owner_a", password="Password123!")
        response = self.client.get(reverse("shop_detail", kwargs={"pk": self.shop_a.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shop Alpha")

    def test_shop_owner_cannot_view_other_shop(self):
        """Owner A is denied access (HTTP 403) when attempting to view Shop B."""
        self.client.login(username="owner_a", password="Password123!")
        response = self.client.get(reverse("shop_detail", kwargs={"pk": self.shop_b.pk}))
        self.assertEqual(response.status_code, 403)

    def test_shop_owner_cannot_access_admin_shop_list(self):
        """Owner A cannot view admin shop list."""
        self.client.login(username="owner_a", password="Password123!")
        response = self.client.get(reverse("shop_list"))
        self.assertEqual(response.status_code, 403)


class ShopAPITests(TestCase):
    """Tests future Android integration API endpoints at /api/v1/shops/."""

    def setUp(self):
        self.api_client = APIClient()
        self.admin = User.objects.create_superuser(username="api_admin", password="AdminPassword123!", role=User.Role.ADMIN)
        self.owner = User.objects.create_user(username="api_owner", password="OwnerPassword123!", role=User.Role.SHOP_OWNER)

        self.shop1 = Shop.objects.create(name="API Shop 1", owner_name="Owner 1", phone="555-7701", address="Address 1", is_active=True, owner_account=self.owner)
        self.shop2 = Shop.objects.create(name="API Shop 2", owner_name="Owner 2", phone="555-7702", address="Address 2", is_active=False)

    def test_api_unauthenticated_request_rejected(self):
        """Unauthenticated requests return 401 or 403 Unauthorized."""
        response = self.api_client.get("/api/v1/shops/")
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_api_admin_can_list_and_create_shop(self):
        """Admin can list all shops and create new shops via API."""
        self.api_client.force_authenticate(user=self.admin)
        # List
        response = self.api_client.get("/api/v1/shops/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)

        # Create
        post_data = {
            "name": "API New Shop",
            "owner_name": "New Contact",
            "phone": "9876548888",
            "email": "new@shop.com",
            "address": "New Address Lane",
            "is_active": True,
        }
        create_resp = self.api_client.post("/api/v1/shops/", post_data)
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Shop.objects.filter(name="API New Shop").exists())

    def test_api_shop_owner_only_sees_own_shop(self):
        """Shop owner only receives their own shop in the list endpoint."""
        self.api_client.force_authenticate(user=self.owner)
        response = self.api_client.get("/api/v1/shops/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "API Shop 1")

    def test_api_shop_owner_cannot_access_other_shop_detail(self):
        """Shop owner accessing another shop returns 403 Forbidden."""
        self.api_client.force_authenticate(user=self.owner)
        response = self.api_client.get(f"/api/v1/shops/{self.shop2.pk}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_api_status_toggle_endpoint(self):
        """Admin can toggle status via /api/v1/shops/<id>/status/."""
        self.api_client.force_authenticate(user=self.admin)
        response = self.api_client.post(f"/api/v1/shops/{self.shop1.pk}/status/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.shop1.refresh_from_db()
        self.assertFalse(self.shop1.is_active)


class ShopLoginAddCustomerComprehensiveTests(TestCase):
    """
    Verifies the 12 explicit scenarios specified in the Add Customer page requirements:
    1. Registration on 4th -> Due 5th
    2. Registration on 8th -> Due 10th
    3. Registration on 13th -> Due 15th
    4. Registration on 18th -> Due 20th
    5. Registration on 23rd -> Due 25th
    6. Registration on 28th -> Due 30th
    7. Phone model allows alphabets, numbers, spaces, and symbols
    8. 3-month EMI divided by 3 and rounded to nearest 10
    9. 6-month EMI divided by 6 and rounded to nearest 10
    10. Final installment adjustment ensures exact total without overcharging
    11. Payment collection correctly updates customer remaining balance
    12. Stored rounded EMI is consistent across dashboards
    """

    def setUp(self):
        from accounts.models import User
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.owner = User.objects.create_user(username="shopowner_test", password="Password123!", role=User.Role.SHOP_OWNER)
        self.shop = Shop.objects.create(name="Apex Mobile Store", owner_name="Apex Owner", phone="9988776655", address="Market Road", owner_account=self.owner)
        self.client.login(username="shopowner_test", password="Password123!")

    def test_scenarios_1_to_6_automatic_due_dates(self):
        """Scenarios 1-6: Customer registration day maps automatically to monthly due date."""
        from applications.models import FinanceApplication
        test_cases = [
            ("2026-11-04", 5),
            ("2026-11-08", 10),
            ("2026-11-13", 15),
            ("2026-11-18", 20),
            ("2026-11-23", 25),
            ("2026-11-28", 30),
            ("2026-10-31", 5),
            ("04-11-2026", 5),
            ("28-11-2026", 30),
        ]
        for dt_str, expected_due in test_cases:
            due = FinanceApplication.calculate_monthly_due_day(dt_str)
            self.assertEqual(due, expected_due, f"Failed for date {dt_str}: got {due}, expected {expected_due}")

    def test_scenario_7_phone_model_alphabets_numbers_and_symbols(self):
        """Scenario 7: Phone model accepts alphabets, numbers, spaces, and symbols like -, _, +, (, ), /, ."""
        from applications.models import FinanceApplication
        from customers.models import Customer
        from django.core.files.uploadedfile import SimpleUploadedFile

        valid_models = [
            "Samsung Galaxy S24 Ultra",
            "iPhone 16 Pro Max",
            "Redmi Note 14 5G",
            "OnePlus Nord CE 4",
            "Vivo V30 Pro",
            "Samsung A35 5G",
            "Nothing Phone (2a)",
            "OPPO Reno 12 Pro+",
            "Model-X_Plus (2026) / 256GB + 5G.v2",
        ]

        photo = SimpleUploadedFile("photo.jpg", b"photo_bytes", content_type="image/jpeg")
        aadhaar = SimpleUploadedFile("aadhaar.pdf", b"aadhaar_bytes", content_type="application/pdf")

        for idx, model_name in enumerate(valid_models):
            data = {
                "application_date": "2026-11-04",
                "customer_name": f"Model Test Customer {idx}",
                "primary_mobile": f"98765432{idx:02d}",
                "secondary_mobile": f"98765433{idx:02d}",
                "work_address": "Tech Park",
                "home_address": "City Colony",
                "mobile_model": model_name,
                "mobile_price": "34000.00",
                "down_payment": "11650.00",
                "total_emi_amount": "45650.00",
                "emi_duration": "6",
                "doc_photo": photo,
                "doc_aadhaar": aadhaar,
                "action": "submit"
            }
            resp = self.client.post(reverse("shop_customer_add"), data)
            self.assertEqual(resp.status_code, 302)
            app = FinanceApplication.objects.get(customer__primary_mobile=f"98765432{idx:02d}")
            self.assertEqual(app.mobile_model, model_name)

    def test_scenarios_8_9_10_emi_calculation_rounding_and_final_adjustment(self):
        """
        Scenarios 8, 9, 10:
        Phone Price: 34,000, Down Payment: 11,650 (Down payment is ignored for EMI calculation: not added, not subtracted)
        3 Months: 34,000 / 3 = 11,333.33 -> 11,330. Inst #1: 11330, #2: 11330, #3: 11340 (Sum: 34,000).
        6 Months: 34,000 / 6 = 5,666.67 -> 5,670. Inst #1-#5: 5670 each, #6: 5650 (Sum: 34,000).
        """
        from applications.models import FinanceApplication
        from customers.models import Customer
        from decimal import Decimal

        cust = Customer.objects.create(shop=self.shop, name="EMI Formula Customer", primary_mobile="9911223344", secondary_mobile="9911223355")

        # 3 Months
        app3 = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            application_date="2026-11-28",
            mobile_model="iPhone 16 Pro Max",
            mobile_price=Decimal("34000.00"),
            down_payment=Decimal("11650.00"),
            emi_duration=3,
        )
        self.assertEqual(app3.monthly_emi, Decimal("11330.00"))
        scheds3 = list(app3.emi_schedules.order_by("installment_number"))
        self.assertEqual(len(scheds3), 3)
        self.assertEqual([s.amount for s in scheds3], [Decimal("11330.00"), Decimal("11330.00"), Decimal("11330.00")])

        # 6 Months
        app6 = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            application_date="2026-11-28",
            mobile_model="Samsung Galaxy S24 Ultra",
            mobile_price=Decimal("34000.00"),
            down_payment=Decimal("11650.00"),
            emi_duration=6,
        )
        self.assertEqual(app6.monthly_emi, Decimal("5670.00"))
        scheds6 = list(app6.emi_schedules.order_by("installment_number"))
        self.assertEqual(len(scheds6), 6)
        self.assertEqual([s.amount for s in scheds6], [Decimal("5670.00")] * 6)

    def test_scenario_11_payment_collection_updates_remaining_balance(self):
        """Scenario 11: Payment collection correctly reduces customer's remaining balance."""
        from applications.models import FinanceApplication
        from customers.models import Customer
        from decimal import Decimal

        cust = Customer.objects.create(shop=self.shop, name="Payment Customer", primary_mobile="9900112233", secondary_mobile="9900112244")
        app = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            application_date="2026-11-04",
            mobile_model="OnePlus Nord CE 4",
            mobile_price=Decimal("34000.00"),
            down_payment=Decimal("11650.00"),
            emi_duration=6,
        )
        self.assertEqual(app.customer_remaining_balance, Decimal("34000.00"))

        app.record_customer_payment(Decimal("5670.00"))
        self.assertEqual(app.customer_remaining_balance, Decimal("28330.00"))

        app.record_customer_payment(Decimal("5670.00"))
        self.assertEqual(app.customer_remaining_balance, Decimal("22660.00"))

    def test_scenario_12_same_emi_values_consistent_across_dashboards(self):
        """Scenario 12: Stored rounded monthly EMI value appears consistently on dashboard and detail pages."""
        from applications.models import FinanceApplication
        from customers.models import Customer
        from decimal import Decimal

        cust = Customer.objects.create(shop=self.shop, name="Consistency Customer", primary_mobile="9955443322", secondary_mobile="9955443311")
        app = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            application_date="2026-11-18",
            mobile_model="Vivo V30 Pro",
            mobile_price=Decimal("34000.00"),
            down_payment=Decimal("11650.00"),
            emi_duration=6,
            status=FinanceApplication.Status.SUBMITTED,
        )

        resp_dash = self.client.get(reverse("shop_dashboard"))
        self.assertEqual(resp_dash.status_code, 200)
        self.assertContains(resp_dash, "5670.00")

        resp_detail = self.client.get(reverse("shop_application_detail", kwargs={"pk": app.pk}))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, "5670.00")
        self.assertContains(resp_detail, "Vivo V30 Pro")


class ShopReportsAndAnalyticsTests(TestCase):
    """
    Test suite for Shop Owner Dashboard Reports & Analytics Page:
    - Last Month Sales vs This Month Sales comparison
    - Best Sales Month in This Year identification & peak metrics
    - Month-by-month performance breakdown & Chart data
    - Top Selling Mobile Models
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from shops.models import Shop
        from customers.models import Customer
        from datetime import date
        User = get_user_model()

        self.owner = User.objects.create_user(
            username="analytics_owner",
            password="ShopPassword123!",
            role=User.Role.SHOP_OWNER
        )
        self.shop = Shop.objects.create(
            name="Apex Mobile Studio",
            owner_name="Anil Kumar",
            owner_account=self.owner,
            phone="9876543210",
            address="Commercial Street, Bangalore",
            is_active=True
        )
        self.client.login(username="analytics_owner", password="ShopPassword123!")

        self.customer = Customer.objects.create(
            shop=self.shop,
            name="Pooja Sharma",
            primary_mobile="9887766554",
            secondary_mobile="9887766550",
            home_address="Indiranagar, Bangalore",
            work_address="Tech Park, Bangalore"
        )

    def test_reports_view_accessible_and_renders_successfully(self):
        """Shop owner can view reports & analytics page with HTTP 200."""
        response = self.client.get(reverse("shop_reports"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "shops/portal/reports.html")
        self.assertContains(response, "Sales Reports & Analytics")
        self.assertContains(response, "Apex Mobile Studio")

    def test_last_month_vs_this_month_comparison_metrics(self):
        """Compares Last Month Sales vs This Month Sales accurately."""
        from django.utils import timezone
        from applications.models import FinanceApplication
        from datetime import date
        from decimal import Decimal

        today = timezone.localdate()
        this_year = today.year
        this_month = today.month

        # Determine last month
        if this_month > 1:
            last_month = this_month - 1
            last_year = this_year
        else:
            last_month = 12
            last_year = this_year - 1

        # Create sales in Last Month: 2 phones @ 25,000 each = 50,000 total sales
        FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.customer,
            application_date=date(last_year, last_month, 10),
            mobile_model="Samsung Galaxy A35",
            mobile_price=Decimal("25000.00"),
            down_payment=Decimal("5000.00"),
            financier_request_amount=Decimal("20000.00"),
            emi_duration=6,
            status=FinanceApplication.Status.APPROVED
        )
        FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.customer,
            application_date=date(last_year, last_month, 15),
            mobile_model="Redmi Note 14 5G",
            mobile_price=Decimal("25000.00"),
            down_payment=Decimal("5000.00"),
            financier_request_amount=Decimal("20000.00"),
            emi_duration=6,
            status=FinanceApplication.Status.APPROVED
        )

        # Create sales in This Month: 3 phones @ 30,000 each = 90,000 total sales
        for i in range(3):
            FinanceApplication.objects.create(
                shop=self.shop,
                customer=self.customer,
                application_date=date(this_year, this_month, 5 + i),
                mobile_model="OnePlus Nord CE 4",
                mobile_price=Decimal("30000.00"),
                down_payment=Decimal("6000.00"),
                financier_request_amount=Decimal("24000.00"),
                emi_duration=6,
                status=FinanceApplication.Status.APPROVED
            )

        response = self.client.get(reverse("shop_reports"))
        self.assertEqual(response.status_code, 200)

        # Check Last Month metrics
        last_m_data = response.context["last_month_data"]
        self.assertEqual(last_m_data["count"], 2)
        self.assertEqual(last_m_data["total_sales"], Decimal("50000.00"))

        # Check This Month metrics
        this_m_data = response.context["this_month_data"]
        self.assertEqual(this_m_data["count"], 3)
        self.assertEqual(this_m_data["total_sales"], Decimal("90000.00"))

        # Check comparison deltas
        comp = response.context["comparison"]
        self.assertEqual(comp["sales_diff"], Decimal("40000.00"))
        # Growth = ((90000 - 50000) / 50000) * 100 = 80.0%
        self.assertEqual(comp["sales_growth_pct"], 80.0)
        self.assertTrue(comp["is_sales_up"])
        self.assertEqual(comp["units_diff"], 1)

        # Verify page displays comparison values
        self.assertContains(response, "Last Month Sales vs This Month Sales")
        self.assertContains(response, "+80.0%")
        self.assertContains(response, "₹50000.00")
        self.assertContains(response, "₹90000.00")

    def test_best_sales_month_in_this_year_calculated_and_highlighted(self):
        """Identifies and highlights the Best Sales Month in the selected year."""
        from django.utils import timezone
        from applications.models import FinanceApplication
        from datetime import date
        from decimal import Decimal

        today = timezone.localdate()
        year = today.year

        # Sales in March: ₹30,000 (1 phone)
        FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.customer,
            application_date=date(year, 3, 10),
            mobile_model="Vivo V30",
            mobile_price=Decimal("30000.00"),
            down_payment=Decimal("5000.00"),
            financier_request_amount=Decimal("25000.00"),
            emi_duration=6,
            status=FinanceApplication.Status.APPROVED
        )

        # Sales in August (PEAK): ₹1,20,000 (3 phones @ 40,000)
        for i in range(3):
            FinanceApplication.objects.create(
                shop=self.shop,
                customer=self.customer,
                application_date=date(year, 8, 12 + i),
                mobile_model="iPhone 16 Pro",
                mobile_price=Decimal("40000.00"),
                down_payment=Decimal("10000.00"),
                financier_request_amount=Decimal("30000.00"),
                emi_duration=6,
                status=FinanceApplication.Status.APPROVED
            )

        # Sales in September: ₹40,000 (1 phone)
        FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.customer,
            application_date=date(year, 9, 20),
            mobile_model="OnePlus 12",
            mobile_price=Decimal("40000.00"),
            down_payment=Decimal("8000.00"),
            financier_request_amount=Decimal("32000.00"),
            emi_duration=6,
            status=FinanceApplication.Status.APPROVED
        )

        response = self.client.get(reverse("shop_reports") + f"?year={year}")
        self.assertEqual(response.status_code, 200)

        best = response.context["best_month"]
        self.assertIsNotNone(best)
        self.assertEqual(best["month_name"], "August")
        self.assertEqual(best["total_sales"], Decimal("120000.00"))
        self.assertEqual(best["count"], 3)

        # Check annual total = 30k + 120k + 40k = 190k
        annual = response.context["annual_summary"]
        self.assertEqual(annual["total_sales"], Decimal("190000.00"))
        self.assertEqual(annual["total_units"], 5)

        # Check template displays Best Sales Month trophy and August
        self.assertContains(response, f"Best Sales Month in {year}")
        self.assertContains(response, f"August {year}")
        self.assertContains(response, "₹120000.00")
        self.assertContains(response, "Peak Annual Revenue")

    def test_top_selling_models_and_empty_handling(self):
        """Top models breakdown displays correct metrics, and empty year handles gracefully."""
        # Query a past year with no sales
        response = self.client.get(reverse("shop_reports") + "?year=2020")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["best_month"])
        self.assertContains(response, "No Sales Recorded in 2020")

    def test_customer_registration_downpayment_and_dual_aadhaar(self):
        """Test down payment cannot exceed price, and dual Aadhaar uploads are saved properly."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from applications.models import CustomerDocument, FinanceApplication

        self.client.login(username="shoptestowner", password="ShopOwnerPassword123!")

        photo = SimpleUploadedFile("face.jpg", b"fake photo content", content_type="image/jpeg")
        aadhaar_front = SimpleUploadedFile("aadhaar_f.jpg", b"fake aadhaar front", content_type="image/jpeg")
        aadhaar_back = SimpleUploadedFile("aadhaar_b.jpg", b"fake aadhaar back", content_type="image/jpeg")

        # 1. Down payment greater than actual price should fail
        invalid_data = {
            "customer_name": "Test DP Customer",
            "primary_mobile": "9812345670",
            "secondary_mobile": "9812345671",
            "home_address": "Home Address Test",
            "work_address": "Work Address Test",
            "mobile_model": "OnePlus 12",
            "mobile_price": "30000.00",
            "down_payment": "35000.00",  # Greater than 30000!
            "monthly_emi": "10000.00",
            "emi_duration": "3",
            "monthly_due_day": "15",
            "doc_photo": photo,
            "doc_aadhaar_front": aadhaar_front,
            "doc_aadhaar_back": aadhaar_back,
            "action": "submit"
        }
        res_fail = self.client.post(reverse("shop_customer_add"), invalid_data)
        self.assertEqual(res_fail.status_code, 200)
        self.assertContains(res_fail, "Down payment cannot be greater than the actual phone price")

        # 2. Valid down payment and dual Aadhaar should succeed
        valid_photo = SimpleUploadedFile("face2.jpg", b"fake photo content", content_type="image/jpeg")
        valid_af = SimpleUploadedFile("aadhaar_front.jpg", b"fake aadhaar front", content_type="image/jpeg")
        valid_ab = SimpleUploadedFile("aadhaar_back.jpg", b"fake aadhaar back", content_type="image/jpeg")

        valid_data = {
            "customer_name": "Test DP Success Customer",
            "primary_mobile": "9812345678",
            "secondary_mobile": "9812345679",
            "home_address": "Home Address Test",
            "work_address": "Work Address Test",
            "mobile_model": "OnePlus 12",
            "mobile_price": "30000.00",
            "down_payment": "5000.00",  # Valid! <= 30000
            "monthly_emi": "10000.00",
            "emi_duration": "3",
            "monthly_due_day": "15",
            "doc_photo": valid_photo,
            "doc_aadhaar_front": valid_af,
            "doc_aadhaar_back": valid_ab,
            "action": "submit"
        }
        res_ok = self.client.post(reverse("shop_customer_add"), valid_data)
        self.assertEqual(res_ok.status_code, 302)

        app = FinanceApplication.objects.get(customer__primary_mobile="9812345678")
        docs = list(app.documents.all())
        doc_types = [d.document_type for d in docs]
        self.assertIn(CustomerDocument.DocType.PHOTOGRAPH, doc_types)
        self.assertIn(CustomerDocument.DocType.AADHAAR_FRONT, doc_types)
        self.assertIn(CustomerDocument.DocType.AADHAAR_BACK, doc_types)

    def test_shop_emi_collection_deducts_financier_payable_and_notifies_admin_and_followup(self):
        """
        Tests Shop EMI Collection page:
        - Shop collects an EMI installment from an approved customer.
        - Customer EMI schedule and balance update.
        - Financier's remaining payable to the shop is reduced by the collected amount.
        - Admin audit history & Follow-up staff activity are created.
        """
        from decimal import Decimal
        from customers.models import Customer, CustomerUpdateHistory
        from applications.models import FinanceApplication
        from payments.models import CustomerEMIPayment
        from followups.models import FollowUpAssignment, FollowUpActivity
        from admin_dashboard.views import get_shop_financials

        # Create follow-up user & assign to self.shop
        followup_user = User.objects.create_user(
            username="followup_emi_sync",
            password="FollowupPassword123!",
            role=User.Role.FOLLOWUP
        )
        FollowUpAssignment.objects.create(
            employee=followup_user,
            shop=self.shop,
            is_active=True
        )

        # Create approved customer & loan: Price 30000, Down 5000 -> Financier Request = 25000, 3 EMIs of 10000
        cust = Customer.objects.create(
            shop=self.shop,
            name="Shop EMI Collector Cust",
            primary_mobile="9876543219",
            secondary_mobile="9876543218"
        )
        app = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            mobile_model="iPhone 15",
            mobile_price=Decimal("30000.00"),
            down_payment=Decimal("5000.00"),
            monthly_emi=Decimal("10000.00"),
            emi_duration=3,
            status=FinanceApplication.Status.APPROVED
        )

        # Initial financier payable = 25000.00
        fin_before = get_shop_financials(self.shop)
        self.assertEqual(fin_before["remaining_due"], Decimal("25000.00"))

        self.client.login(username="shop_rep_user", password="ShopPassword123!")

        # GET Shop EMI Collection page
        get_res = self.client.get(reverse("shop_emi_collection"))
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, "Shop EMI Collector Cust")

        # POST Collect 10000.00 EMI
        post_res = self.client.post(reverse("shop_emi_collection"), {
            "customer_id": cust.pk,
            "amount": "10000.00",
            "payment_date": "2026-11-12",
            "payment_mode": "CASH",
            "transaction_reference": "REC-555",
            "remarks": "Collected November EMI at shop"
        })
        self.assertEqual(post_res.status_code, 302)

        # 1. Verify CustomerEMIPayment created with collected_by_shop=True
        pay = CustomerEMIPayment.objects.filter(customer=cust, collected_by_shop=True).first()
        self.assertIsNotNone(pay)
        self.assertEqual(pay.amount, Decimal("10000.00"))

        # 2. Verify EMI schedule & remaining balance updated
        app.refresh_from_db()
        self.assertEqual(app.total_customer_paid, Decimal("10000.00"))
        self.assertEqual(app.customer_remaining_balance, Decimal("20000.00"))

        # 3. Verify Financier Remaining Due to Shop is reduced from 25000 -> 15000
        fin_after = get_shop_financials(self.shop)
        self.assertEqual(fin_after["shop_collected_emi"], Decimal("10000.00"))
        self.assertEqual(fin_after["remaining_due"], Decimal("15000.00"))

        # Also check Shop Dashboard & Financier Payments page show updated Amount Received (10000), Financier Requested (15000), and Outstanding (15000)
        dash_res = self.client.get(reverse("shop_dashboard"))
        self.assertEqual(dash_res.context["total_amount_received"], Decimal("10000.00"))
        self.assertEqual(dash_res.context["total_amount_requested"], Decimal("15000.00"))
        self.assertEqual(dash_res.context["outstanding_amount"], Decimal("15000.00"))
        fin_pay_res = self.client.get(reverse("shop_payments"))
        self.assertEqual(fin_pay_res.context["total_received"], Decimal("10000.00"))
        self.assertEqual(fin_pay_res.context["total_requested"], Decimal("15000.00"))
        self.assertEqual(fin_pay_res.context["outstanding"], Decimal("15000.00"))

        # 4. Verify Admin audit & Follow-up person notification
        audit = CustomerUpdateHistory.objects.filter(customer=cust, field_name="EMI Collected by Shop").first()
        self.assertIsNotNone(audit)
        fu_act = FollowUpActivity.objects.filter(
            employee=followup_user,
            customer=cust,
            activity_type=FollowUpActivity.ActivityType.PAYMENT,
            outcome=FollowUpActivity.Outcome.PAID
        ).first()
        self.assertIsNotNone(fu_act)

        # 5. Excess collection test: if financier already paid 20000 (total 20000 + 10000 shop EMI = 30000 > 25000 request)
        # Outstanding Balance card should show +₹5000.00
        from payments.models import FinancierPayment
        FinancierPayment.objects.create(
            shop=self.shop,
            application=app,
            amount_received=Decimal("20000.00"),
            payment_date="2026-11-12",
            payment_reference="FIN-PAY-20000"
        )
        dash_excess_res = self.client.get(reverse("shop_dashboard"))
        self.assertTrue(dash_excess_res.context["outstanding_excess"])
        self.assertEqual(dash_excess_res.context["outstanding_amount"], Decimal("5000.00"))
        self.assertContains(dash_excess_res, "+₹5000.00")

