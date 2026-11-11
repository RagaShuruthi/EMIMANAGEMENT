from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from shops.models import Shop


class RoleBasedLoginTests(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Admin user
        self.admin_user = User.objects.create_superuser(
            username="test_admin_portal",
            email="admin@test.com",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
        )

        # 2. Shop owner user + Shop
        self.shop_owner_user = User.objects.create_user(
            username="test_shop_portal",
            email="shop@test.com",
            password="ShopPassword123!",
            role=User.Role.SHOP_OWNER,
        )
        self.shop = Shop.objects.create(
            name="Alpha Mobile Retail",
            owner_name="Alpha Owner",
            phone="9876543210",
            address="123 Market St",
            owner_account=self.shop_owner_user,
            is_active=True,
        )

        # 3. Follow-up Staff user
        self.followup_user = User.objects.create_user(
            username="test_followup_portal",
            email="followup@test.com",
            password="StaffPassword123!",
            role=User.Role.FOLLOWUP,
        )

    def test_login_page_renders_fintech_branding_and_3d_wallet(self):
        """GET /accounts/login/ renders the new EMI Manager fintech design with 3D wallet elements."""
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EMI Manager")
        self.assertContains(response, "Track")
        self.assertContains(response, "Follow Up")
        self.assertContains(response, "Collect")
        self.assertContains(response, "wallet-showcase-stage")
        self.assertContains(response, "wallet-3d-assembly")
        self.assertContains(response, "coin-3d")
        self.assertContains(response, "emi-calendar-badge")
        self.assertContains(response, "selected_role")

    def test_admin_login_auto_detect_redirects_to_admin_dashboard(self):
        """Admin logging in without specifying role auto-detects and redirects to admin dashboard."""
        response = self.client.post(
            reverse("login"),
            {"username": "test_admin_portal", "password": "AdminPassword123!"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin_dashboard"))

    def test_admin_login_with_explicit_role_redirects_to_admin_dashboard(self):
        """Admin selecting Administrator role redirects to admin dashboard."""
        response = self.client.post(
            reverse("login"),
            {"username": "test_admin_portal", "password": "AdminPassword123!", "selected_role": "ADMIN"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin_dashboard"))

    def test_shop_owner_login_auto_detect_redirects_to_shop_dashboard(self):
        """Shop owner logging in redirects directly to shop dashboard."""
        response = self.client.post(
            reverse("login"),
            {"username": "test_shop_portal", "password": "ShopPassword123!"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("shop_dashboard"))

    def test_shop_owner_login_with_explicit_role_redirects_to_shop_dashboard(self):
        """Shop owner selecting Partner Retail Shop redirects to shop dashboard."""
        response = self.client.post(
            reverse("login"),
            {"username": "test_shop_portal", "password": "ShopPassword123!", "selected_role": "SHOP_OWNER"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("shop_dashboard"))

    def test_followup_staff_login_auto_detect_redirects_to_followup_dashboard(self):
        """Follow-up employee logging in redirects directly to followup dashboard."""
        response = self.client.post(
            reverse("login"),
            {"username": "test_followup_portal", "password": "StaffPassword123!"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("followup_dashboard"))

    def test_followup_staff_login_with_explicit_role_redirects_to_followup_dashboard(self):
        """Follow-up employee selecting Follow-up role redirects to followup dashboard."""
        response = self.client.post(
            reverse("login"),
            {"username": "test_followup_portal", "password": "StaffPassword123!", "selected_role": "FOLLOWUP"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("followup_dashboard"))

    def test_role_mismatch_shows_friendly_error(self):
        """Shop owner trying to log in as Administrator is rejected with role mismatch error."""
        response = self.client.post(
            reverse("login"),
            {"username": "test_shop_portal", "password": "ShopPassword123!", "selected_role": "ADMIN"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Access Denied: Your account is not authorized as an Administrator.")

    def test_deactivated_shop_cannot_login(self):
        """If a shop is deactivated, its owner account cannot log in and receives an access denied error."""
        self.shop.is_active = False
        self.shop.save()
        if self.shop.owner_account:
            self.shop.owner_account.is_active = False
            self.shop.owner_account.save()

        response = self.client.post(
            reverse("login"),
            {"username": "test_shop_portal", "password": "ShopPassword123!"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_reactivated_shop_can_login(self):
        """When a shop is reactivated, the owner account can log in again."""
        self.shop.is_active = False
        self.shop.save()
        if self.shop.owner_account:
            self.shop.owner_account.is_active = False
            self.shop.owner_account.save()

        # Reactivate
        self.shop.is_active = True
        self.shop.save()
        if self.shop.owner_account:
            self.shop.owner_account.is_active = True
            self.shop.owner_account.save()

        response = self.client.post(
            reverse("login"),
            {"username": "test_shop_portal", "password": "ShopPassword123!"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("shop_dashboard"))
