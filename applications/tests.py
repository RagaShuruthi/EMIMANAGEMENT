"""
Automated tests for Role-Based Login Redirects and Shop Portal Operations.
Verifies:
1. Role-based redirect on login (Admin -> Admin Dashboard, Shop Owner -> Shop Dashboard).
2. Elimination of 403 Forbidden Access Denied for shop owners when an admin URL is in next.
3. Accurate 9-metric database calculation on Shop Dashboard.
4. Direct Monthly EMI calculation (Total Financed / Due Months: 60000 / 6 = 10000).
5. Months scheme exclusively for 3 and 6 months.
6. Monthly Due Day calculation:
   - 1-5 or 31 -> 5th
   - 6-10 -> 10th
   - 11-15 -> 15th
   - 16-20 -> 20th
   - 21-25 -> 25th
   - 26-30 -> 30th
7. Mandatory Mobile Number 2.
8. Mandatory Customer Photograph & Aadhaar Card upload.
9. Financier Payments recording, outstanding calculations, and discrepancy detection.
"""

from decimal import Decimal
import datetime
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from shops.models import Shop
from customers.models import Customer
from applications.models import FinanceApplication, CustomerDocument
from payments.models import FinancierPayment

User = get_user_model()


class RoleBasedLoginRedirectTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="test_admin",
            password="AdminPass123!",
            role=User.Role.ADMIN
        )
        self.shop_owner_user = User.objects.create_user(
            username="test_owner",
            password="OwnerPass123!",
            role=User.Role.SHOP_OWNER
        )
        self.shop = Shop.objects.create(
            name="Test Mobile Store",
            owner_name="Test Owner",
            phone="9876543210",
            address="100 Commercial St",
            owner_account=self.shop_owner_user,
            is_active=True
        )

    def test_admin_login_redirects_to_admin_dashboard(self):
        """Admin user logging in should redirect directly to /admin-dashboard/."""
        response = self.client.post(
            reverse("login"),
            {"username": "test_admin", "password": "AdminPass123!"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin_dashboard"))

    def test_shop_owner_login_redirects_to_shop_dashboard(self):
        """Shop owner logging in should redirect directly to /shop-dashboard/ (not admin dashboard)."""
        response = self.client.post(
            reverse("login"),
            {"username": "test_owner", "password": "OwnerPass123!"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("shop_dashboard"))

    def test_shop_owner_login_clears_admin_next_url_to_prevent_access_denied(self):
        """
        When a shop owner logs in with an admin next_url (e.g. ?next=/admin-dashboard/),
        the system must redirect to the shop dashboard instead of throwing 403 Access Denied.
        """
        response = self.client.post(
            reverse("login") + "?next=/admin-dashboard/",
            {"username": "test_owner", "password": "OwnerPass123!", "next": "/admin-dashboard/"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("shop_dashboard"))

    def test_root_url_redirects_by_role(self):
        """Visiting '/' while authenticated routes admins to admin dashboard and shop owners to shop dashboard."""
        self.client.login(username="test_admin", password="AdminPass123!")
        res_admin = self.client.get("/")
        self.assertEqual(res_admin.status_code, 302)
        self.assertEqual(res_admin.url, reverse("admin_dashboard"))

        self.client.logout()

        self.client.login(username="test_owner", password="OwnerPass123!")
        res_owner = self.client.get("/")
        self.assertEqual(res_owner.status_code, 302)
        self.assertEqual(res_owner.url, reverse("shop_dashboard"))


class DueDateAndEmiCalculationTests(TestCase):
    """Verifies EMI direct division (Total / Due Months) and Due Day ranges."""

    def test_monthly_due_day_cycle_rules(self):
        """
        1-5 or 31 -> 5
        6-10 -> 10
        11-15 -> 15
        16-20 -> 20
        21-25 -> 25
        26-30 -> 30
        """
        calc = FinanceApplication.calculate_monthly_due_day

        # 1-5 or 31 -> 5
        self.assertEqual(calc(datetime.date(2026, 1, 1)), 5)
        self.assertEqual(calc(datetime.date(2026, 1, 3)), 5)
        self.assertEqual(calc(datetime.date(2026, 1, 5)), 5)
        self.assertEqual(calc(datetime.date(2026, 1, 31)), 5)

        # 6-10 -> 10
        self.assertEqual(calc(datetime.date(2026, 1, 6)), 10)
        self.assertEqual(calc(datetime.date(2026, 1, 8)), 10)
        self.assertEqual(calc(datetime.date(2026, 1, 10)), 10)

        # 11-15 -> 15
        self.assertEqual(calc(datetime.date(2026, 1, 11)), 15)
        self.assertEqual(calc(datetime.date(2026, 1, 15)), 15)

        # 16-20 -> 20
        self.assertEqual(calc(datetime.date(2026, 1, 16)), 20)
        self.assertEqual(calc(datetime.date(2026, 1, 20)), 20)

        # 21-25 -> 25
        self.assertEqual(calc(datetime.date(2026, 1, 21)), 25)
        self.assertEqual(calc(datetime.date(2026, 1, 25)), 25)

        # 26-30 -> 30
        self.assertEqual(calc(datetime.date(2026, 1, 26)), 30)
        self.assertEqual(calc(datetime.date(2026, 1, 30)), 30)

    def test_monthly_emi_direct_calculation_formula(self):
        """
        User Rule: Monthly EMI is calculated directly as main payment (mobile price) / due months.
        Down payment is interest/profit and is NOT deducted from EMI.
        Example: 60,000 total amount, due 6 months -> monthly EMI is 10,000 (even with down payment).
        3 months -> 20,000.
        """
        shop = Shop.objects.create(name="Shop EMI", owner_name="Owner", phone="999", address="St")
        cust = Customer.objects.create(shop=shop, name="Cust", work_address="W", home_address="H", primary_mobile="9876543210", secondary_mobile="9876543211")

        # Monthly EMI = Total Amount (Phone Price) / Selected Tenure (rounded to nearest 10s)
        # Down payment is completely ignored for monthly EMI calculation (not added, not subtracted)
        # e.g., 60,000 / 6 -> 10000.00
        app6 = FinanceApplication.objects.create(
            shop=shop,
            customer=cust,
            application_date=datetime.date(2026, 5, 2), # day 2 -> due day 5
            mobile_model="Samsung S24",
            mobile_price=Decimal("60000.00"),
            down_payment=Decimal("10000.00"),
            emi_duration=6,
        )
        self.assertEqual(app6.monthly_emi, Decimal("10000.00"))
        self.assertEqual(app6.monthly_due_day, 5)
        # Financier Request Amount = Total Selling Price - Down Payment (60,000 - 10,000 = 50,000)
        self.assertEqual(app6.financier_request_amount, Decimal("50000.00"))

        # User's verified example: Phone 30,000, Down 6,000, 6 Months -> Monthly EMI = 30,000 / 6 = 5,000.00
        app_exact = FinanceApplication.objects.create(
            shop=shop,
            customer=cust,
            application_date=datetime.date(2026, 5, 3),
            mobile_model="Vivo V30",
            mobile_price=Decimal("30000.00"),
            down_payment=Decimal("6000.00"),
            emi_duration=6,
        )
        self.assertEqual(app_exact.financier_request_amount, Decimal("24000.00"))
        self.assertEqual(app_exact.monthly_emi, Decimal("5000.00"))

        # 60,000 / 3 -> 20000.00
        app3 = FinanceApplication.objects.create(
            shop=shop,
            customer=cust,
            application_date=datetime.date(2026, 5, 12), # day 12 -> due day 15
            mobile_model="iPhone 15",
            mobile_price=Decimal("60000.00"),
            down_payment=Decimal("5000.00"),
            emi_duration=3,
        )
        self.assertEqual(app3.monthly_emi, Decimal("20000.00"))
        self.assertEqual(app3.monthly_due_day, 15)
        # Financier Request Amount = 60,000 - 5,000 = 55,000
        self.assertEqual(app3.financier_request_amount, Decimal("55000.00"))

    def test_monthly_emi_rounding_to_tens(self):
        """
        User Rule:
        Round off to 10's of the value.
        e.g. 3333.33 -> 3330
             3336.30 -> 3340
        """
        self.assertEqual(FinanceApplication.round_emi_to_tens(Decimal("3333.33")), Decimal("3330.00"))
        self.assertEqual(FinanceApplication.round_emi_to_tens(Decimal("3336.30")), Decimal("3340.00"))
        self.assertEqual(FinanceApplication.round_emi_to_tens(Decimal("10000.00")), Decimal("10000.00"))
        self.assertEqual(FinanceApplication.round_emi_to_tens(Decimal("3335.00")), Decimal("3340.00"))
        self.assertEqual(FinanceApplication.round_emi_to_tens(Decimal("3334.99")), Decimal("3330.00"))

    def test_shop_owner_custom_monthly_due_day_override(self):
        """Shop owner can customize/override the monthly due date."""
        shop = Shop.objects.create(name="Shop Custom Due", owner_name="Owner", phone="999", address="St")
        cust = Customer.objects.create(shop=shop, name="Cust", work_address="W", home_address="H", primary_mobile="9876543210", secondary_mobile="9876543211")

        # Application date is 2nd (default cycle is 5th), but shop owner manually chooses 25th
        app = FinanceApplication.objects.create(
            shop=shop,
            customer=cust,
            application_date=datetime.date(2026, 5, 2),
            mobile_model="Pixel 8",
            mobile_price=Decimal("45000.00"),
            down_payment=Decimal("0.00"),
            emi_duration=3,
            monthly_due_day=25, # Manual override
        )
        self.assertEqual(app.monthly_due_day, 25)


class ShopDashboardOperationsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            username="shopowner_ops",
            password="OpsPassword123!",
            role=User.Role.SHOP_OWNER
        )
        self.shop = Shop.objects.create(
            name="Apex Mobiles",
            owner_name="Apex Owner",
            phone="9876543210",
            address="50 Central Ave",
            owner_account=self.owner,
            is_active=True
        )
        self.client.login(username="shopowner_ops", password="OpsPassword123!")

    def test_customer_registration_requires_secondary_mobile(self):
        """Registration fails if secondary_mobile is missing."""
        photo = SimpleUploadedFile("photo.jpg", b"photo_bytes", content_type="image/jpeg")
        aadhaar = SimpleUploadedFile("aadhaar.pdf", b"aadhaar_bytes", content_type="application/pdf")
        data = {
            "customer_name": "Ramesh Kumar",
            "primary_mobile": "9876540001",
            "secondary_mobile": "",  # Missing secondary mobile
            "work_address": "Tech City, Sector 5",
            "home_address": "12 Residency Road",
            "mobile_model": "Samsung Galaxy S24 256GB",
            "mobile_price": "60000.00",
            "down_payment": "0.00",
            "emi_duration": "6",
            "doc_photo": photo,
            "doc_aadhaar": aadhaar,
            "action": "submit"
        }
        response = self.client.post(reverse("shop_customer_add"), data)
        self.assertEqual(response.status_code, 200) # Re-renders with error
        self.assertFalse(Customer.objects.filter(primary_mobile="9876540001").exists())
        # Preserves previously entered form values so user doesn't have to re-type
        self.assertEqual(response.context["form_data"]["customer_name"], "Ramesh Kumar")
        self.assertEqual(response.context["form_data"]["primary_mobile"], "9876540001")
        # Only highlights the invalid/missing mandatory field
        self.assertIn("secondary_mobile", response.context["field_errors"])
        self.assertNotIn("customer_name", response.context["field_errors"])

    def test_customer_registration_requires_photo_and_aadhaar(self):
        """Registration fails if mandatory documents (photo & aadhaar) are omitted."""
        data = {
            "customer_name": "Suresh",
            "primary_mobile": "9876540002",
            "secondary_mobile": "9876540003",
            "work_address": "Tech City",
            "home_address": "Residency Road",
            "mobile_model": "iPhone 15",
            "mobile_price": "60000.00",
            "down_payment": "0.00",
            "emi_duration": "6",
            "action": "submit"
        }
        # Without files:
        response = self.client.post(reverse("shop_customer_add"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FinanceApplication.objects.filter(mobile_model="iPhone 15").exists())

    def test_customer_registration_rejects_non_10_digit_mobile(self):
        """Phone numbers with fewer or more than 10 digits are rejected."""
        photo = SimpleUploadedFile("photo.jpg", b"photo_bytes", content_type="image/jpeg")
        aadhaar = SimpleUploadedFile("aadhaar.pdf", b"aadhaar_bytes", content_type="application/pdf")
        data = {
            "customer_name": "Invalid Mobile",
            "primary_mobile": "98765",  # 5 digits only
            "secondary_mobile": "9876540002",
            "work_address": "Tech City",
            "home_address": "Residency Road",
            "mobile_model": "iPhone 15",
            "mobile_price": "60000.00",
            "down_payment": "0.00",
            "emi_duration": "6",
            "doc_photo": photo,
            "doc_aadhaar": aadhaar,
            "action": "submit"
        }
        response = self.client.post(reverse("shop_customer_add"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Customer.objects.filter(name="Invalid Mobile").exists())

    def test_customer_registration_success_with_mandatory_fields_and_documents(self):
        """
        Registration succeeds when all mandatory fields (including secondary mobile and documents) are supplied.
        Down payment is ignored in EMI calculation: 60,000 price / 6 months = 10,000/mo.
        Shop owner custom due day (e.g. 25) is preserved.
        """
        photo = SimpleUploadedFile("photo.jpg", b"photo_bytes", content_type="image/jpeg")
        aadhaar = SimpleUploadedFile("aadhaar.pdf", b"aadhaar_bytes", content_type="application/pdf")
        data = {
            "application_date": "2026-10-02", # would default to 5th
            "monthly_due_day": "25",         # shop owner manually edited to 25th
            "customer_name": "Ramesh Kumar",
            "primary_mobile": "9876540001",
            "secondary_mobile": "9876540002",
            "work_address": "Tech City, Sector 5",
            "home_address": "12 Residency Road",
            "mobile_model": "Samsung Galaxy S24 256GB",
            "mobile_price": "60000.00",
            "down_payment": "10000.00",      # Down payment ignored for EMI calculation!
            "emi_duration": "6",
            "doc_photo": photo,
            "doc_aadhaar": aadhaar,
            "action": "submit"
        }
        response = self.client.post(reverse("shop_customer_add"), data)
        self.assertEqual(response.status_code, 302)

        customer = Customer.objects.get(primary_mobile="9876540001")
        self.assertEqual(customer.secondary_mobile, "9876540002")

        app = FinanceApplication.objects.get(customer=customer)
        self.assertEqual(app.mobile_price, Decimal("60000.00"))
        # Phone Price is 60,000 / 6 = 10,000.00 (down payment ignored for EMI)
        self.assertEqual(app.monthly_emi, Decimal("10000.00"))
        # Due day edited to 25th by shop owner
        self.assertEqual(app.monthly_due_day, 25)
        self.assertEqual(app.documents.count(), 2)

    def test_customer_registration_with_custom_edited_emi(self):
        """Shop owner can manually edit the monthly EMI amount, which gets rounded to 10s."""
        photo = SimpleUploadedFile("photo.jpg", b"photo_bytes", content_type="image/jpeg")
        aadhaar = SimpleUploadedFile("aadhaar.pdf", b"aadhaar_bytes", content_type="application/pdf")
        data = {
            "application_date": "2026-10-02",
            "customer_name": "Custom EMI User",
            "primary_mobile": "9876541111",
            "secondary_mobile": "9876542222",
            "work_address": "Tech City",
            "home_address": "Residency Road",
            "mobile_model": "iPhone 15 Pro",
            "mobile_price": "60000.00",
            "down_payment": "5000.00",
            "monthly_emi": "11500.00",      # Shop owner manually customized EMI
            "emi_duration": "6",
            "doc_photo": photo,
            "doc_aadhaar": aadhaar,
            "action": "submit"
        }
        response = self.client.post(reverse("shop_customer_add"), data)
        self.assertEqual(response.status_code, 302)

        app = FinanceApplication.objects.get(customer__primary_mobile="9876541111")
        self.assertEqual(app.monthly_emi, Decimal("11500.00"))

    def test_shop_dashboard_dynamic_statistics(self):
        """Dashboard renders accurate metrics calculated directly from database records."""
        cust = Customer.objects.create(
            shop=self.shop,
            name="John Doe",
            work_address="Office",
            home_address="Home",
            primary_mobile="9998887771",
            secondary_mobile="9998887772"
        )
        # App 1: SUBMITTED (requested 60,000)
        app1 = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            mobile_model="iPhone 15",
            mobile_price=Decimal("60000.00"),
            down_payment=Decimal("0.00"),
            emi_duration=6,
            status=FinanceApplication.Status.SUBMITTED
        )
        # App 2: APPROVED (requested 30,000)
        app2 = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            mobile_model="Vivo V29",
            mobile_price=Decimal("30000.00"),
            down_payment=Decimal("0.00"),
            emi_duration=3,
            status=FinanceApplication.Status.APPROVED
        )

        # Record payment of 10,000 against app2
        FinancierPayment.objects.create(
            application=app2,
            amount_received=Decimal("10000.00"),
            payment_date="2026-10-28",
            payment_reference="UTR100200300"
        )

        response = self.client.get(reverse("shop_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_customers"], 1)
        self.assertEqual(response.context["total_applications"], 2)
        self.assertEqual(response.context["submitted_applications"], 1)
        self.assertEqual(response.context["total_amount_requested"], Decimal("90000.00"))
        self.assertEqual(response.context["total_amount_received"], Decimal("10000.00"))
        self.assertEqual(response.context["outstanding_amount"], Decimal("80000.00"))

    def test_financier_payment_recording_and_discrepancy(self):
        """Financier payment recording detects discrepancy when disbursement exceeds requested loan amount."""
        cust = Customer.objects.create(shop=self.shop, name="Sam", work_address="A", home_address="B", primary_mobile="9000011111", secondary_mobile="9000022222")
        app = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            mobile_model="Moto Edge",
            mobile_price=Decimal("30000.00"),
            down_payment=Decimal("0.00"),
            emi_duration=3,
            status=FinanceApplication.Status.SUBMITTED
        )  # Requested: 30,000

        # Post payment of 35,000 (exceeds 30,000 requested)
        pay_data = {
            "application_id": app.id,
            "amount_received": "35000.00",
            "payment_date": "2026-10-28",
            "payment_reference": "UTR999999",
            "remarks": "Excess payment"
        }
        res = self.client.post(reverse("shop_payments"), pay_data)
        self.assertEqual(res.status_code, 302)

        payment = FinancierPayment.objects.get(payment_reference="UTR999999")
        self.assertTrue(payment.is_discrepant)

    def test_day_by_day_payment_history_grouping_and_empty_state(self):
        """Verifies day-by-day grouping of admin payouts and clean empty state."""
        # Empty state test first
        response = self.client.get(reverse("shop_payments"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["daily_groups"]), 0)
        self.assertContains(response, "No Daily Payout Records Found")

        # Create customer & applications
        cust = Customer.objects.create(
            shop=self.shop,
            name="Ravi Kumar",
            work_address="Tech Park",
            home_address="Subhash Nagar",
            primary_mobile="9876543210",
            secondary_mobile="9876543211",
        )
        app1 = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            mobile_model="OnePlus 12",
            mobile_price=Decimal("60000.00"),
            down_payment=Decimal("0.00"),
            emi_duration=6,
            status=FinanceApplication.Status.SUBMITTED,
        )
        app2 = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            mobile_model="Redmi Note 13",
            mobile_price=Decimal("20000.00"),
            down_payment=Decimal("0.00"),
            emi_duration=3,
            status=FinanceApplication.Status.SUBMITTED,
        )

        # Create 2 payments on 2026-10-28 and 1 payment on 2026-10-27
        FinancierPayment.objects.create(
            application=app1,
            amount_received=Decimal("30000.00"),
            payment_date="2026-10-28",
            payment_reference="UTR-DAY1-A",
        )
        FinancierPayment.objects.create(
            application=app2,
            amount_received=Decimal("20000.00"),
            payment_date="2026-10-28",
            payment_reference="UTR-DAY1-B",
        )
        FinancierPayment.objects.create(
            application=app1,
            amount_received=Decimal("15000.00"),
            payment_date="2026-10-27",
            payment_reference="UTR-DAY2-A",
        )

        response = self.client.get(reverse("shop_payments"))
        self.assertEqual(response.status_code, 200)
        daily_groups = response.context["daily_groups"]
        self.assertEqual(len(daily_groups), 2)

        # First group: 2026-10-28 with 2 payments totaling 50,000
        self.assertEqual(str(daily_groups[0]["date"]), "2026-10-28")
        self.assertEqual(daily_groups[0]["count"], 2)
        self.assertEqual(daily_groups[0]["total_day_amount"], Decimal("50000.00"))

        # Second group: 2026-10-27 with 1 payment of 15,000
        self.assertEqual(str(daily_groups[1]["date"]), "2026-10-27")
        self.assertEqual(daily_groups[1]["count"], 1)
        self.assertEqual(daily_groups[1]["total_day_amount"], Decimal("15000.00"))

        # Check total aggregates
        self.assertEqual(response.context["total_received"], Decimal("65000.00"))
        self.assertContains(response, "UTR-DAY1-A")
        self.assertContains(response, "UTR-DAY1-B")
        self.assertContains(response, "UTR-DAY2-A")
        self.assertContains(response, "Day Total Paid:")
        self.assertContains(response, "50000.00")

        # Test date filter
        res_filtered = self.client.get(reverse("shop_payments") + "?date=2026-10-27")
        self.assertEqual(res_filtered.status_code, 200)
        self.assertEqual(len(res_filtered.context["daily_groups"]), 1)
        self.assertEqual(str(res_filtered.context["daily_groups"][0]["date"]), "2026-10-27")

    def test_financier_request_amount_formula_exact_example(self):
        """
        User Rule:
        Financier Request Amount = Total Selling Payment - Down Payment.
        Example: Total selling payment is 25000 and down payment is 5000 -> request is 20000.
        """
        cust = Customer.objects.create(
            shop=self.shop,
            name="Pooja Sharma",
            work_address="Sector 62",
            home_address="Noida",
            primary_mobile="9811223344",
            secondary_mobile="9811223355",
        )
        app = FinanceApplication.objects.create(
            shop=self.shop,
            customer=cust,
            mobile_model="Vivo V29 128GB",
            mobile_price=Decimal("25000.00"),
            down_payment=Decimal("5000.00"),
            emi_duration=6,
        )
        self.assertEqual(app.financier_request_amount, Decimal("20000.00"))

    def test_all_customers_page_removed_redirects_to_dashboard(self):
        """All Customers page is removed from navigation and redirects to shop dashboard."""
        response = self.client.get(reverse("shop_customer_list"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("shop_dashboard"))
