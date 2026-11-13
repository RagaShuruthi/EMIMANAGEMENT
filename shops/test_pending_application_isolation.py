from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from shops.models import Shop
from customers.models import Customer
from applications.models import FinanceApplication
from followups.models import FollowUpAssignment, FollowUpActivity
from payments.models import FinancierPayment, CustomerEMIPayment

User = get_user_model()


class PendingApplicationIsolationTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Admin user
        self.admin = User.objects.create_superuser(
            username="admin_tester",
            role=User.Role.ADMIN,
            phone="9999999999",
            password="adminpassword123",
            is_active=True,
        )

        # Shop and shop owner
        self.shop_owner = User.objects.create_user(
            username="shopowner_test",
            role=User.Role.SHOP_OWNER,
            phone="9888888888",
            password="shoppassword123",
            is_active=True,
        )
        self.shop = Shop.objects.create(
            owner_account=self.shop_owner,
            name="Apex Mobile Store",
            owner_name="Rajesh Sharma",
            phone="9888888888",
            address="100 Main Road",
            is_active=True,
        )

        # Follow-up officer
        self.followup_user = User.objects.create_user(
            username="followup_officer",
            role=User.Role.FOLLOWUP,
            phone="9777777777",
            password="followuppassword123",
            is_active=True,
        )
        self.assignment = FollowUpAssignment.objects.create(
            employee=self.followup_user,
            shop=self.shop,
            assigned_by=self.admin,
            is_active=True,
        )

        # Customer with PENDING / UNDER_REVIEW application
        self.pending_cust = Customer.objects.create(
            shop=self.shop,
            name="Pending Customer John",
            primary_mobile="9811100001",
            home_address="Pending Street 1",
            work_address="Pending Office 1",
        )
        self.pending_app = FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.pending_cust,
            application_number="APP-PEND-001",
            mobile_model="iPhone 15",
            mobile_price=Decimal("80000.00"),
            down_payment=Decimal("20000.00"),
            financier_request_amount=Decimal("60000.00"),
            emi_duration=6,
            monthly_emi=Decimal("13330.00"),
            monthly_due_day=10,
            status=FinanceApplication.Status.UNDER_REVIEW,
        )

        # Customer with APPROVED application
        self.approved_cust = Customer.objects.create(
            shop=self.shop,
            name="Approved Customer Alice",
            primary_mobile="9822200002",
            home_address="Approved Street 2",
            work_address="Approved Office 2",
        )
        self.approved_app = FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.approved_cust,
            application_number="APP-APPR-002",
            mobile_model="Samsung S24",
            mobile_price=Decimal("60000.00"),
            down_payment=Decimal("10000.00"),
            financier_request_amount=Decimal("50000.00"),
            emi_duration=6,
            monthly_emi=Decimal("10000.00"),
            monthly_due_day=15,
            status=FinanceApplication.Status.APPROVED,
        )

    def test_shop_applications_page_statuses_and_badges(self):
        """Shop application list page provides only Under Review, Approved, and Rejected filters."""
        self.client.login(username="shopowner_test", password="shoppassword123")
        res = self.client.get(reverse("shop_application_list"))
        self.assertEqual(res.status_code, 200)

        # Verify Navigation Tabs have Under Review, Approved, Rejected
        self.assertContains(res, "Under Review")
        self.assertContains(res, "Approved")
        self.assertContains(res, "Rejected")
        # Ensure 'Submitted' tab is removed from filter pills
        self.assertNotContains(res, "?status=SUBMITTED")

        # Check badges in table: Under Review for pending_app, Approved for approved_app
        self.assertContains(res, "Under Review</span>")
        self.assertContains(res, "Approved</span>")

        # Filter by UNDER_REVIEW
        res_review = self.client.get(reverse("shop_application_list") + "?status=UNDER_REVIEW")
        self.assertContains(res_review, "APP-PEND-001")
        self.assertNotContains(res_review, "APP-APPR-002")

        # Filter by APPROVED
        res_approved = self.client.get(reverse("shop_application_list") + "?status=APPROVED")
        self.assertContains(res_approved, "APP-APPR-002")
        self.assertNotContains(res_approved, "APP-PEND-001")

    def test_pending_customer_excluded_from_admin_customer_lists(self):
        """Pending customers must NOT appear in Admin All Customers or Shop-wise customer lists."""
        self.client.login(username="admin_tester", password="adminpassword123")

        # 1. Admin All Customers view
        res_all = self.client.get(reverse("admin_all_customers"))
        self.assertEqual(res_all.status_code, 200)
        self.assertContains(res_all, "Approved Customer Alice")
        self.assertNotContains(res_all, "Pending Customer John")

        # 2. Admin Shop-wise detail view
        res_shop = self.client.get(reverse("admin_shopwise_detail", kwargs={"shop_id": self.shop.id}))
        self.assertEqual(res_shop.status_code, 200)
        self.assertContains(res_shop, "Approved Customer Alice")
        self.assertNotContains(res_shop, "Pending Customer John")

    def test_pending_customer_excluded_from_financier_payments(self):
        """Pending application is excluded from financier payments and shop payouts."""
        self.client.login(username="admin_tester", password="adminpassword123")

        # Admin Shop Payments
        res_pay = self.client.get(reverse("admin_shop_payments"))
        self.assertEqual(res_pay.status_code, 200)

        # Find row for self.shop
        shop_row = next((r for r in res_pay.context["payment_rows"] if r["shop"].id == self.shop.id), None)
        self.assertIsNotNone(shop_row)
        # Pending loans should only include approved applications (50,000), not pending (60,000)
        self.assertEqual(shop_row["financials"]["total_payable"], Decimal("50000.00"))
        pending_app_numbers = [a.application_number for a in shop_row["pending_apps"]]
        self.assertIn("APP-APPR-002", pending_app_numbers)
        self.assertNotIn("APP-PEND-001", pending_app_numbers)

        # Shop portal Financier Payments
        self.client.login(username="shopowner_test", password="shoppassword123")
        res_shop_pay = self.client.get(reverse("shop_payments"))
        self.assertEqual(res_shop_pay.status_code, 200)
        self.assertEqual(res_shop_pay.context["total_requested"], Decimal("50000.00"))

    def test_pending_customer_excluded_from_followup_person_list(self):
        """Pending customers must NOT appear in follow-up person dashboard, tasks, or shop details."""
        self.client.login(username="followup_officer", password="followuppassword123")

        # 1. Followup Dashboard
        res_dash = self.client.get(reverse("followup_dashboard"))
        self.assertEqual(res_dash.status_code, 200)
        self.assertContains(res_dash, "Approved Customer Alice")
        self.assertNotContains(res_dash, "Pending Customer John")

        # 2. Followup Tasks
        res_tasks = self.client.get(reverse("followup_tasks"))
        self.assertEqual(res_tasks.status_code, 200)
        self.assertContains(res_tasks, "Approved Customer Alice")
        self.assertNotContains(res_tasks, "Pending Customer John")

        # 3. Followup Shop Detail
        res_shop_detail = self.client.get(reverse("followup_shop_detail", kwargs={"shop_id": self.shop.id}))
        self.assertEqual(res_shop_detail.status_code, 200)
        self.assertContains(res_shop_detail, "Approved Customer Alice")
        self.assertNotContains(res_shop_detail, "Pending Customer John")

    def test_approving_pending_application_immediately_updates_all_lists(self):
        """Once approved, the customer immediately becomes visible in all lists."""
        self.client.login(username="admin_tester", password="adminpassword123")

        # Approve the application via admin_customer_change_status
        res_change = self.client.post(
            reverse("admin_customer_change_status", kwargs={"pk": self.pending_cust.pk}),
            {"status": "APPROVED"}
        )
        self.assertEqual(res_change.status_code, 302)

        # 1. Now visible in Admin All Customers
        res_all = self.client.get(reverse("admin_all_customers"))
        self.assertContains(res_all, "Pending Customer John")

        # 2. Now reflected in financier payment total (50k + 60k = 110k)
        res_pay = self.client.get(reverse("admin_shop_payments"))
        shop_row = next((r for r in res_pay.context["payment_rows"] if r["shop"].id == self.shop.id), None)
        self.assertEqual(shop_row["financials"]["total_payable"], Decimal("110000.00"))
        pending_app_numbers = [a.application_number for a in shop_row["pending_apps"]]
        self.assertIn("APP-PEND-001", pending_app_numbers)

        # 3. Now visible to Follow-up officer
        self.client.login(username="followup_officer", password="followuppassword123")
        res_tasks = self.client.get(reverse("followup_tasks"))
        self.assertContains(res_tasks, "Pending Customer John")
