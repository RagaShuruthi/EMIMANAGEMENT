from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from shops.models import Shop
from customers.models import Customer
from applications.models import FinanceApplication
from followups.models import FollowUpAssignment

User = get_user_model()


class CustomerDuplicateNumberTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Admin user
        self.admin = User.objects.create_superuser(
            username="admin_dup_tester",
            role=User.Role.ADMIN,
            phone="9990001111",
            password="adminpassword123",
            is_active=True,
        )

        # Shop
        self.shop_owner = User.objects.create_user(
            username="shop_owner_dup",
            role=User.Role.SHOP_OWNER,
            phone="9880002222",
            password="shoppassword123",
            is_active=True,
        )
        self.shop = Shop.objects.create(
            owner_account=self.shop_owner,
            name="Apex Mobile Store",
            owner_name="Rajesh Sharma",
            phone="9880002222",
            address="100 Main Road",
            is_active=True,
        )

        # Follow-up employee
        self.followup_user = User.objects.create_user(
            username="followup_dup_officer",
            role=User.Role.FOLLOWUP,
            phone="9770003333",
            password="followuppassword123",
            is_active=True,
        )
        self.assignment = FollowUpAssignment.objects.create(
            employee=self.followup_user,
            shop=self.shop,
            assigned_by=self.admin,
            is_active=True,
        )

        # Customer 1 (original / registered first)
        self.cust1 = Customer.objects.create(
            shop=self.shop,
            name="Original Customer Rahul",
            primary_mobile="9876543210",
            secondary_mobile="9876543211",
            home_address="Address 1",
            work_address="Office 1",
        )
        self.app1 = FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.cust1,
            application_number="APP-DUP-001",
            mobile_model="Vivo V30",
            mobile_price=Decimal("30000.00"),
            down_payment=Decimal("6000.00"),
            financier_request_amount=Decimal("24000.00"),
            emi_duration=6,
            monthly_emi=Decimal("5000.00"),
            monthly_due_day=5,
            status=FinanceApplication.Status.APPROVED,
        )

        # Customer 2 (registered later with the same primary mobile as Customer 1)
        self.cust2 = Customer.objects.create(
            shop=self.shop,
            name="Duplicate Customer Amit",
            primary_mobile="9876543210",
            secondary_mobile="9876543299",
            home_address="Address 2",
            work_address="Office 2",
        )
        self.app2 = FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.cust2,
            application_number="APP-DUP-002",
            mobile_model="OnePlus 12R",
            mobile_price=Decimal("40000.00"),
            down_payment=Decimal("10000.00"),
            financier_request_amount=Decimal("30000.00"),
            emi_duration=6,
            monthly_emi=Decimal("6670.00"),
            monthly_due_day=10,
            status=FinanceApplication.Status.APPROVED,
        )

        # Customer 3 (distinct number, no duplicate)
        self.cust3 = Customer.objects.create(
            shop=self.shop,
            name="Unique Customer Sneha",
            primary_mobile="9812300000",
            secondary_mobile="9812300001",
            home_address="Address 3",
            work_address="Office 3",
        )
        self.app3 = FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.cust3,
            application_number="APP-DUP-003",
            mobile_model="Samsung A35",
            mobile_price=Decimal("25000.00"),
            down_payment=Decimal("5000.00"),
            financier_request_amount=Decimal("20000.00"),
            emi_duration=6,
            monthly_emi=Decimal("4170.00"),
            monthly_due_day=15,
            status=FinanceApplication.Status.APPROVED,
        )

    def test_model_duplicate_detection_logic(self):
        """Cust1 is original; Cust2 registered later with same number is marked duplicate; Cust3 is unique."""
        # Cust1 was registered first
        self.assertFalse(self.cust1.is_duplicate_number)

        # Cust2 was registered after Cust1 with same number
        self.assertTrue(self.cust2.is_duplicate_number)

        # Cust3 has a completely different number
        self.assertFalse(self.cust3.is_duplicate_number)

    def test_batch_duplicate_annotation(self):
        """Customer.annotate_duplicates precomputes duplicate flag correctly in 1 batch."""
        customers = [self.cust1, self.cust2, self.cust3]
        Customer.annotate_duplicates(customers)

        self.assertFalse(self.cust1.is_duplicate_number)
        self.assertTrue(self.cust2.is_duplicate_number)
        self.assertFalse(self.cust3.is_duplicate_number)

    def test_admin_customer_list_shows_duplicate_badge_below_name(self):
        """Admin All Customers table shows Duplicate badge below the name of the later registered customer."""
        self.client.login(username="admin_dup_tester", password="adminpassword123")
        res = self.client.get(reverse("admin_all_customers"))
        self.assertEqual(res.status_code, 200)

        content = res.content.decode("utf-8")

        # Duplicate Customer Amit must have Duplicate badge below their name
        self.assertIn("Duplicate Customer Amit", content)
        self.assertIn("Original Customer Rahul", content)
        self.assertIn("Unique Customer Sneha", content)

        # Check badge HTML
        self.assertIn("Duplicate</span>", content)

        # Context rows check
        rows = {r["customer_id"]: r for r in res.context["customers"]}
        self.assertFalse(rows[self.cust1.id]["is_duplicate"])
        self.assertTrue(rows[self.cust2.id]["is_duplicate"])
        self.assertFalse(rows[self.cust3.id]["is_duplicate"])

    def test_followup_tasks_shows_duplicate_badge(self):
        """Follow-up portal tasks list shows Duplicate badge for the duplicate customer."""
        self.client.login(username="followup_dup_officer", password="followuppassword123")
        res = self.client.get(reverse("followup_tasks"))
        self.assertEqual(res.status_code, 200)

        content = res.content.decode("utf-8")
        self.assertIn("Duplicate Customer Amit", content)
        self.assertIn("Duplicate</span>", content)

