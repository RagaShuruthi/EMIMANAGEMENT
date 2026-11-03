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
            "create_credentials": True,
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
