"""
Unit and integration tests for Admin Dashboard Integration with Shops.
Verifies dynamic database metrics, recent shops display, navigation links,
role-based access controls, and single source of truth with the Shop model.
"""

from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from shops.models import Shop
from customers.models import Customer
from applications.models import FinanceApplication, ApplicationHistory
from payments.models import FinancierPayment

User = get_user_model()


class AdminDashboardIntegrationTests(TestCase):
    """
    Tests connecting the Admin Dashboard directly to the existing Shop model.
    """

    def setUp(self):
        self.client = Client()
        # Administrator user
        self.admin_user = User.objects.create_superuser(
            username="admin_tester",
            password="AdminPass123!",
            role=User.Role.ADMIN
        )
        # Shop owner user
        self.shop_owner_user = User.objects.create_user(
            username="shop_owner_tester",
            password="OwnerPass123!",
            role=User.Role.SHOP_OWNER
        )
        # Follow-up employee user
        self.employee_user = User.objects.create_user(
            username="employee_tester",
            password="EmpPass123!",
            role=User.Role.EMPLOYEE
        )

    def test_dashboard_displays_zero_shops_when_empty(self):
        """1. Dashboard displays zero shops when the database contains no shops."""
        Shop.objects.all().delete()
        self.client.login(username="admin_tester", password="AdminPass123!")
        response = self.client.get(reverse("admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_shops"], 0)
        self.assertEqual(response.context["active_shops"], 0)
        self.assertEqual(response.context["inactive_shops"], 0)
        self.assertEqual(len(response.context["recent_shops"]), 0)
        self.assertContains(response, "No Shops in Database")
        self.assertContains(response, reverse("add_shop"))

    def test_adding_shop_through_shops_page_updates_dashboard(self):
        """2. Adding a shop through the Shops page updates the dashboard automatically."""
        self.client.login(username="admin_tester", password="AdminPass123!")

        # Verify initial count
        self.assertEqual(Shop.objects.count(), 0)

        # Submit new shop through existing Shops add form
        new_shop_data = {
            "name": "Reliance Digital Hub",
            "owner_name": "Mukesh Ambani",
            "phone": "9876543210",
            "email": "mukesh@reliance.com",
            "address": "Maker Chambers IV, Nariman Point, Mumbai",
            "is_active": True,
        }
        res_post = self.client.post(reverse("add_shop"), new_shop_data)
        self.assertEqual(res_post.status_code, 302)

        # Check dashboard reflection
        res_dash = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(res_dash.status_code, 200)
        self.assertEqual(res_dash.context["total_shops"], 1)
        self.assertEqual(res_dash.context["active_shops"], 1)
        self.assertEqual(res_dash.context["inactive_shops"], 0)
        self.assertEqual(len(res_dash.context["recent_shops"]), 1)
        self.assertContains(res_dash, "Reliance Digital Hub")
        self.assertContains(res_dash, "Mukesh Ambani")

    def test_editing_shop_updates_dashboard_information(self):
        """3. Editing a shop updates the dashboard information."""
        shop = Shop.objects.create(
            name="Original Store Name",
            owner_name="Original Owner",
            phone="9876540000",
            address="Original Address 100",
            is_active=True,
        )
        self.client.login(username="admin_tester", password="AdminPass123!")

        # Verify initial display
        res1 = self.client.get(reverse("admin_dashboard"))
        self.assertContains(res1, "Original Store Name")

        # Edit the shop
        edit_data = {
            "name": "Updated Prime Store",
            "owner_name": "Updated Owner Name",
            "phone": "9876549999",
            "address": "Updated Address 200",
            "is_active": True,
        }
        res_edit = self.client.post(reverse("edit_shop", kwargs={"pk": shop.pk}), edit_data)
        self.assertEqual(res_edit.status_code, 302)

        # Verify dashboard shows updated information
        res2 = self.client.get(reverse("admin_dashboard"))
        self.assertContains(res2, "Updated Prime Store")
        self.assertNotContains(res2, "Original Store Name")

    def test_active_and_inactive_shop_counts_accurate(self):
        """4. Active and inactive shop counts are accurate and update dynamically."""
        Shop.objects.create(name="Active Shop 1", owner_name="A1", phone="9000000001", address="Addr 1", is_active=True)
        Shop.objects.create(name="Active Shop 2", owner_name="A2", phone="9000000002", address="Addr 2", is_active=True)
        Shop.objects.create(name="Active Shop 3", owner_name="A3", phone="9000000003", address="Addr 3", is_active=True)
        inactive_shop = Shop.objects.create(name="Inactive Shop 1", owner_name="I1", phone="9000000004", address="Addr 4", is_active=False)

        self.client.login(username="admin_tester", password="AdminPass123!")
        res = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(res.context["total_shops"], 4)
        self.assertEqual(res.context["active_shops"], 3)
        self.assertEqual(res.context["inactive_shops"], 1)

        # Deactivate an active shop via status toggle
        active_shop = Shop.objects.get(name="Active Shop 1")
        self.client.post(reverse("shop_status", kwargs={"pk": active_shop.pk}))
        active_shop.refresh_from_db()
        self.assertFalse(active_shop.is_active)

        # Verify updated counts
        res_after = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(res_after.context["total_shops"], 4)
        self.assertEqual(res_after.context["active_shops"], 2)
        self.assertEqual(res_after.context["inactive_shops"], 2)

    def test_recent_shops_appear_in_correct_order(self):
        """5. Recent shops appear newest-first, limited to the latest 5 shops."""
        Shop.objects.all().delete()
        shops = []
        for i in range(1, 8):
            s = Shop.objects.create(
                name=f"Shop #{i}",
                owner_name=f"Owner #{i}",
                phone=f"90000000{i:02d}",
                address=f"Street {i}",
                is_active=True,
            )
            shops.append(s)

        self.client.login(username="admin_tester", password="AdminPass123!")
        res = self.client.get(reverse("admin_dashboard"))

        # Must limit to 5
        recent = res.context["recent_shops"]
        self.assertEqual(len(recent), 5)

        # Must be newest first: Shop #7, #6, #5, #4, #3
        self.assertEqual(recent[0].name, "Shop #7")
        self.assertEqual(recent[1].name, "Shop #6")
        self.assertEqual(recent[2].name, "Shop #5")
        self.assertEqual(recent[3].name, "Shop #4")
        self.assertEqual(recent[4].name, "Shop #3")

        # Older shops #1 and #2 must not be in the recent 5 slice
        names_in_recent = [s.name for s in recent]
        self.assertNotIn("Shop #1", names_in_recent)
        self.assertNotIn("Shop #2", names_in_recent)

    def test_view_all_shops_button_and_url(self):
        """6. View All Shops opens the existing Shops page."""
        self.client.login(username="admin_tester", password="AdminPass123!")
        res = self.client.get(reverse("admin_dashboard"))
        self.assertContains(res, reverse("shop_list"))

        # Verify existing shop_list view works
        res_list = self.client.get(reverse("shop_list"))
        self.assertEqual(res_list.status_code, 200)

    def test_view_details_opens_correct_shop(self):
        """7. View Details opens the correct shop detail page."""
        shop = Shop.objects.create(
            name="Detail Check Store",
            owner_name="Detail Owner",
            phone="9876543219",
            address="123 Detail Ave",
            is_active=True,
        )
        self.client.login(username="admin_tester", password="AdminPass123!")
        res = self.client.get(reverse("admin_dashboard"))
        expected_url = reverse("shop_detail", kwargs={"pk": shop.pk})
        self.assertContains(res, expected_url)

        # Verify existing shop_detail view works for this shop
        res_detail = self.client.get(expected_url)
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, "Detail Check Store")

    def test_unauthorized_users_cannot_access_admin_dashboard(self):
        """8. Unauthorized users cannot access the Admin Dashboard."""
        # Unauthenticated user -> redirected to login
        res_anon = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(res_anon.status_code, 302)
        self.assertIn("/accounts/login/", res_anon.url)

        # Shop owner user -> 403 Forbidden Access Denied
        self.client.login(username="shop_owner_tester", password="OwnerPass123!")
        res_owner = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(res_owner.status_code, 403)
        self.assertContains(res_owner, "Administrator privileges required", status_code=403)

        # Follow-up employee -> 403 Forbidden Access Denied
        self.client.login(username="employee_tester", password="EmpPass123!")
        res_emp = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(res_emp.status_code, 403)

        # Admin user -> 200 OK
        self.client.login(username="admin_tester", password="AdminPass123!")
        res_admin = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(res_admin.status_code, 200)

    def test_existing_shop_functionality_continues_working(self):
        """9. Existing shop functionality (add, list, detail, edit, status) continues working."""
        self.client.login(username="admin_tester", password="AdminPass123!")

        # Create shop
        shop = Shop.objects.create(
            name="Smoke Test Shop",
            owner_name="Smoke Tester",
            phone="9876543211",
            address="Smoke Road 1",
            is_active=True,
        )

        # Check detail
        res_detail = self.client.get(reverse("shop_detail", kwargs={"pk": shop.pk}))
        self.assertEqual(res_detail.status_code, 200)

        # Check edit GET
        res_edit_get = self.client.get(reverse("edit_shop", kwargs={"pk": shop.pk}))
        self.assertEqual(res_edit_get.status_code, 200)

        # Check list GET
        res_list = self.client.get(reverse("shop_list"))
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, "Smoke Test Shop")


class AdminDashboardReworkTests(TestCase):
    """
    Comprehensive tests for the Reworked Admin Dashboard:
    - All Shops page with live financials
    - All Customers page with EMI metrics & filters
    - Customer Detail & Application Approval/Rejection
    - Shop-wise Customer isolation
    - Customer EMI Details view
    - Shop Payments calculation & 'Mark as Paid' transaction
    - Duplicate payment prevention
    - Shop Dashboard real-time synchronization
    """

    def setUp(self):
        from decimal import Decimal
        from customers.models import Customer
        from applications.models import FinanceApplication
        from payments.models import FinancierPayment

        self.client = Client()

        # Admin user
        self.admin = User.objects.create_superuser(
            username="admin_rework",
            password="AdminPassword123!",
            role=User.Role.ADMIN
        )

        # Shop 1 & Owner
        self.shop1_owner = User.objects.create_user(
            username="owner_alpha",
            password="OwnerAlpha123!",
            role=User.Role.SHOP_OWNER
        )
        self.shop1 = Shop.objects.create(
            name="Alpha Mobile World",
            owner_name="Alpha Owner",
            phone="9876543210",
            email="alpha@mobile.com",
            address="123 Alpha Road, City",
            is_active=True,
            owner_account=self.shop1_owner
        )

        # Shop 2 & Owner
        self.shop2_owner = User.objects.create_user(
            username="owner_beta",
            password="OwnerBeta123!",
            role=User.Role.SHOP_OWNER
        )
        self.shop2 = Shop.objects.create(
            name="Beta Gadget Plaza",
            owner_name="Beta Owner",
            phone="9876500000",
            email="beta@gadgets.com",
            address="456 Beta Lane, City",
            is_active=True,
            owner_account=self.shop2_owner
        )

        # Customer 1 for Shop 1
        self.customer1 = Customer.objects.create(
            shop=self.shop1,
            name="Ramesh Sharma",
            primary_mobile="9988776655",
            home_address="Flat 101, Sunshine Heights"
        )
        # Application 1: Total 30000, Down 5000, Financed 25000, 3 months, 8330/mo, due day 15
        self.app1 = FinanceApplication.objects.create(
            shop=self.shop1,
            customer=self.customer1,
            application_number="APP-ALPHA-001",
            mobile_model="Samsung Galaxy S24",
            mobile_price=Decimal("30000.00"),
            down_payment=Decimal("5000.00"),
            financier_request_amount=Decimal("25000.00"),
            emi_duration=3,
            monthly_emi=Decimal("8330.00"),
            monthly_due_day=15,
            status=FinanceApplication.Status.SUBMITTED
        )

        # Customer 2 for Shop 2
        self.customer2 = Customer.objects.create(
            shop=self.shop2,
            name="Suresh Verma",
            primary_mobile="9123456789",
            home_address="House 202, Green Park"
        )
        # Application 2: Total 20000, Down 2000, Financed 18000, 6 months, 3000/mo, due day 5
        self.app2 = FinanceApplication.objects.create(
            shop=self.shop2,
            customer=self.customer2,
            application_number="APP-BETA-002",
            mobile_model="OnePlus 12R",
            mobile_price=Decimal("20000.00"),
            down_payment=Decimal("2000.00"),
            financier_request_amount=Decimal("18000.00"),
            emi_duration=6,
            monthly_emi=Decimal("3000.00"),
            monthly_due_day=5,
            status=FinanceApplication.Status.APPROVED
        )

    def test_all_shops_page_renders_financials_and_customer_counts(self):
        """Shop Payouts page shows payable, paid, remaining, and status, while Partner Shops shows shop info."""
        self.client.login(username="admin_rework", password="AdminPassword123!")
        # Partner shops page
        response = self.client.get(reverse("shop_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha Mobile World")
        self.assertContains(response, "Beta Gadget Plaza")
        self.assertContains(response, f"#{self.shop1.id}")

        # Dedicated Shop Payouts page
        pay_response = self.client.get(reverse("admin_shop_payments"))
        self.assertEqual(pay_response.status_code, 200)
        self.assertContains(pay_response, "₹25000.00")  # Alpha payable
        self.assertContains(pay_response, "₹18000.00")  # Beta payable
        self.assertContains(pay_response, "Unpaid")

    def test_all_customers_page_filters_and_emi_details(self):
        """All Customers page lists customers across all shops with EMI details and filters."""
        self.client.login(username="admin_rework", password="AdminPassword123!")

        # All customers
        res = self.client.get(reverse("admin_all_customers"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Ramesh Sharma")
        self.assertContains(res, "Suresh Verma")
        self.assertContains(res, "Samsung Galaxy S24")
        self.assertContains(res, "OnePlus 12R")

        # Filter by shop 1
        res_shop1 = self.client.get(reverse("admin_all_customers"), {"shop_id": self.shop1.id})
        self.assertContains(res_shop1, "Ramesh Sharma")
        self.assertNotContains(res_shop1, "Suresh Verma")

        # Search by query
        res_search = self.client.get(reverse("admin_all_customers"), {"q": "9123456789"})
        self.assertContains(res_search, "Suresh Verma")
        self.assertNotContains(res_search, "Ramesh Sharma")

    def test_customer_detail_and_admin_approval(self):
        """Admin can view customer detail and approve a pending application."""
        from applications.models import FinanceApplication, ApplicationHistory
        self.client.login(username="admin_rework", password="AdminPassword123!")

        # View customer detail
        res = self.client.get(reverse("admin_customer_detail", kwargs={"pk": self.customer1.pk}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Ramesh Sharma")
        self.assertContains(res, "APP-ALPHA-001")

        # Admin approves the application
        post_url = reverse("admin_application_approval", kwargs={"pk": self.app1.pk})
        res_post = self.client.post(post_url, {"decision": "APPROVED", "remarks": "Documents verified successfully."})
        self.assertEqual(res_post.status_code, 302)

        self.app1.refresh_from_db()
        self.assertEqual(self.app1.status, FinanceApplication.Status.APPROVED)
        self.assertEqual(self.app1.remarks, "Documents verified successfully.")

        # Verify audit history logged
        history = ApplicationHistory.objects.filter(application=self.app1).latest("timestamp")
        self.assertEqual(history.new_status, FinanceApplication.Status.APPROVED)
        self.assertEqual(history.actor, self.admin)

    def test_shopwise_customers_strict_isolation(self):
        """Shop-wise customer detail guarantees strict data isolation between shops."""
        self.client.login(username="admin_rework", password="AdminPassword123!")

        # Shop 1 detail
        res1 = self.client.get(reverse("admin_shopwise_detail", kwargs={"shop_id": self.shop1.id}))
        self.assertEqual(res1.status_code, 200)
        self.assertContains(res1, "Ramesh Sharma")
        self.assertNotContains(res1, "Suresh Verma")

        # Shop 2 detail
        res2 = self.client.get(reverse("admin_shopwise_detail", kwargs={"shop_id": self.shop2.id}))
        self.assertEqual(res2.status_code, 200)
        self.assertContains(res2, "Suresh Verma")
        self.assertNotContains(res2, "Ramesh Sharma")

    def test_admin_marks_shop_payment_as_paid_and_prevents_duplicate(self):
        """Admin records payment to shop; transaction updates application and prevents duplicate UTR."""
        from decimal import Decimal
        from applications.models import FinanceApplication
        from payments.models import FinancierPayment

        self.client.login(username="admin_rework", password="AdminPassword123!")

        # Post payment of 10000 towards app1
        pay_data = {
            "application_id": self.app1.id,
            "amount": "10000.00",
            "payment_date": "2026-10-30",
            "payment_reference": "UTR123456789",
            "remarks": "First installment payment from admin"
        }
        res = self.client.post(reverse("admin_record_shop_payment"), pay_data)
        self.assertEqual(res.status_code, 302)

        # Check payment record exists
        payment = FinancierPayment.objects.get(payment_reference="UTR123456789")
        self.assertEqual(payment.amount_received, Decimal("10000.00"))

        # Verify application status became PARTIALLY_PAID
        self.app1.refresh_from_db()
        self.assertEqual(self.app1.status, FinanceApplication.Status.PARTIALLY_PAID)
        self.assertEqual(self.app1.outstanding_amount, Decimal("15000.00"))

        # Attempt duplicate payment with same reference and amount
        res_dup = self.client.post(reverse("admin_record_shop_payment"), pay_data)
        self.assertEqual(res_dup.status_code, 302)
        # Should not create a second payment record
        self.assertEqual(FinancierPayment.objects.filter(payment_reference="UTR123456789").count(), 1)

    def test_shop_dashboard_syncs_with_admin_payments(self):
        """Shop owner viewing their Shop Dashboard payments ledger sees admin payouts in real-time."""
        from decimal import Decimal
        from payments.models import FinancierPayment

        # Admin makes payment to Shop 1's application
        FinancierPayment.objects.create(
            application=self.app1,
            amount_received=Decimal("25000.00"),
            payment_date="2026-10-30",
            payment_reference="UTR-ADMIN-FULL-01",
            remarks="Full disbursement by admin"
        )
        self.app1.refresh_from_db()
        self.assertEqual(self.app1.status, FinanceApplication.Status.PAID)
        self.assertEqual(self.app1.outstanding_amount, Decimal("0.00"))

        # Shop 1 owner logs into Shop Dashboard payments page
        self.client.login(username="owner_alpha", password="OwnerAlpha123!")
        res = self.client.get(reverse("shop_payments"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "UTR-ADMIN-FULL-01")
        self.assertContains(res, "25000")

    def test_admin_applications_list_three_filters(self):
        """Admin applications list page filters by strictly Pending, Approved, and Rejected."""
        from applications.models import FinanceApplication
        self.client.login(username="admin_rework", password="AdminPassword123!")

        # 1. View all applications
        res_all = self.client.get(reverse("admin_applications_list"))
        self.assertEqual(res_all.status_code, 200)
        self.assertContains(res_all, "Finance Applications")
        self.assertContains(res_all, "APP-ALPHA-001")
        self.assertContains(res_all, "APP-BETA-002")

        # 2. Filter by Pending
        res_pending = self.client.get(reverse("admin_applications_list") + "?status=pending")
        self.assertEqual(res_pending.status_code, 200)
        self.assertContains(res_pending, "APP-ALPHA-001")

        # 3. Approve APP-ALPHA-001 from Applications page with next parameter
        post_url = reverse("admin_application_approval", kwargs={"pk": self.app1.pk})
        res_post = self.client.post(
            post_url,
            {"decision": "APPROVED", "remarks": "Approved from Applications Page", "next": reverse("admin_applications_list")}
        )
        self.assertEqual(res_post.status_code, 302)
        self.assertRedirects(res_post, reverse("admin_applications_list"))

        self.app1.refresh_from_db()
        self.assertEqual(self.app1.status, FinanceApplication.Status.APPROVED)

        # 4. Filter by Approved
        res_approved = self.client.get(reverse("admin_applications_list") + "?status=approved")
        self.assertEqual(res_approved.status_code, 200)
        self.assertContains(res_approved, "APP-ALPHA-001")

        # 5. Filter by Rejected
        res_rejected = self.client.get(reverse("admin_applications_list") + "?status=rejected")
        self.assertEqual(res_rejected.status_code, 200)
        self.assertNotContains(res_rejected, "APP-ALPHA-001")

        # 6. Revert to Pending decision
        res_post_pending = self.client.post(
            post_url,
            {"decision": "PENDING", "remarks": "Reverted back to review", "next": reverse("admin_applications_list")}
        )
        self.assertEqual(res_post_pending.status_code, 302)
        self.app1.refresh_from_db()
        self.assertEqual(self.app1.status, FinanceApplication.Status.UNDER_REVIEW)

        # 7. Decline / Reject decision
        res_post_reject = self.client.post(
            post_url,
            {"decision": "REJECTED", "remarks": "Declined by admin", "next": reverse("admin_applications_list")}
        )
        self.assertEqual(res_post_reject.status_code, 302)
        self.app1.refresh_from_db()
        self.assertEqual(self.app1.status, FinanceApplication.Status.REJECTED)

    def test_sidebar_has_shopwise_customers_and_customer_list_has_no_approval_column(self):
        """Verify Shop-wise Customers is in sidebar and customer registry has no approval status column/filter."""
        self.client.login(username="admin_rework", password="AdminPassword123!")

        # 1. Check sidebar has Shop-wise Customers link
        res_dash = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(res_dash.status_code, 200)
        self.assertContains(res_dash, "Shop-wise Customers")
        self.assertContains(res_dash, reverse("admin_shopwise_customers"))

        # 2. Check Customer Registry does not display Approval Status column or filter
        res_cust = self.client.get(reverse("admin_all_customers"))
        self.assertEqual(res_cust.status_code, 200)
        self.assertNotContains(res_cust, "Approval Status (All)")
        self.assertNotContains(res_cust, '<th scope="col">Approval Status</th>')

        # 3. Check Applications page has detail modal with 3 decision buttons and does not link View to customer page
        res_apps = self.client.get(reverse("admin_applications_list"))
        self.assertEqual(res_apps.status_code, 200)
        self.assertContains(res_apps, f'id="appDetailModal{self.app1.id}"')
        self.assertContains(res_apps, 'value="APPROVED"')
        self.assertContains(res_apps, 'value="REJECTED"')
        self.assertContains(res_apps, 'value="PENDING"')
        self.assertContains(res_apps, 'Keep as Pending')
        # View Details button opens modal, does not link away to customer detail
        self.assertNotContains(res_apps, f'href="/admin-dashboard/customers/{self.customer1.pk}/"')

    def test_admin_approve_all_pending_applications(self):
        """Verify the 'Approve All Pending' button approves every pending application."""
        from applications.models import FinanceApplication
        self.client.login(username="admin_rework", password="AdminPassword123!")

        # Ensure app1 and app2 are in pending status
        self.app1.status = FinanceApplication.Status.SUBMITTED
        self.app1.save()
        self.app2.status = FinanceApplication.Status.UNDER_REVIEW
        self.app2.save()

        # Call approve all pending endpoint
        res = self.client.post(reverse("admin_approve_all_pending_applications"))
        self.assertEqual(res.status_code, 302)

        self.app1.refresh_from_db()
        self.app2.refresh_from_db()
        self.assertEqual(self.app1.status, FinanceApplication.Status.APPROVED)
        self.assertEqual(self.app2.status, FinanceApplication.Status.APPROVED)
        # Verify EMI installment schedules exist
        self.assertGreater(self.app1.emi_schedules.count(), 0)
        self.assertGreater(self.app2.emi_schedules.count(), 0)

    def test_admin_mark_as_paid_with_screenshot_and_rupees(self):
        """Verify 'Mark as Paid' modal handles rupee amount, screenshot file upload, and updates status."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from payments.models import FinancierPayment
        self.client.login(username="admin_rework", password="AdminPassword123!")

        screenshot_file = SimpleUploadedFile(
            "transfer_receipt.png",
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRdummy",
            content_type="image/png"
        )
        pay_data = {
            "shop_id": self.shop1.id,
            "amount": "15000.00",
            "payment_date": "2026-11-04",
            "payment_reference": "UTR-SCREENSHOT-TEST-01",
            "payment_screenshot": screenshot_file,
            "remarks": "Paid via GPay with screenshot attached"
        }
        res = self.client.post(reverse("admin_record_shop_payment"), pay_data)
        self.assertEqual(res.status_code, 302)

        payment = FinancierPayment.objects.get(payment_reference="UTR-SCREENSHOT-TEST-01")
        self.assertEqual(payment.amount_received, Decimal("15000.00"))
        self.assertEqual(payment.shop, self.shop1)
        self.assertTrue(payment.payment_screenshot)

    def test_date_wise_filtering_applications_and_payouts(self):
        """Verify date-wise filters on both applications page and shop payments ledger."""
        self.client.login(username="admin_rework", password="AdminPassword123!")

        # 1. Applications page date filter
        res_apps = self.client.get(reverse("admin_applications_list") + "?start_date=2026-10-01&end_date=2026-11-30")
        self.assertEqual(res_apps.status_code, 200)

        # 2. Shop payments page date filter
        res_pays = self.client.get(reverse("admin_shop_payments") + "?start_date=2026-10-01&end_date=2026-11-30")
        self.assertEqual(res_pays.status_code, 200)
        self.assertContains(res_pays, "Shop Payout Balances")

    def test_shop_payout_transaction_details_history_modal_and_date_filter(self):
        """Verify shop payouts does not navigate to shop details, but renders transaction details history modal with date filtering."""
        from payments.models import FinancierPayment
        self.client.login(username="admin_rework", password="AdminPassword123!")

        # Create two payments for shop1 on different dates
        FinancierPayment.objects.create(
            shop=self.shop1,
            amount_received=Decimal("5000.00"),
            payment_date="2026-10-15",
            payment_reference="OCT-PAY-123",
            remarks="October payment"
        )
        FinancierPayment.objects.create(
            shop=self.shop1,
            amount_received=Decimal("7000.00"),
            payment_date="2026-11-02",
            payment_reference="NOV-PAY-456",
            remarks="November payment"
        )

        # GET Shop Payouts page
        res = self.client.get(reverse("admin_shop_payments"))
        self.assertEqual(res.status_code, 200)
        # Modal exists for shop1
        self.assertContains(res, f'id="shopHistoryModal{self.shop1.id}"')
        self.assertContains(res, "Payout Transaction Details History")
        self.assertContains(res, "OCT-PAY-123")
        self.assertContains(res, "NOV-PAY-456")
        # Ensure 'shop_detail' navigation is not used in the Action column or shop link in payout table
        self.assertNotContains(res, f'href="/admin-dashboard/shops/{self.shop1.pk}/"')

        # GET Shop Payouts page with date filter for November only
        res_filtered = self.client.get(reverse("admin_shop_payments") + "?start_date=2026-11-01&end_date=2026-11-30")
        self.assertEqual(res_filtered.status_code, 200)
        # Only November payment should appear in filtered transaction history
        content = res_filtered.content.decode("utf-8")
        # Find modal section for shop1
        modal_start = content.find(f'id="shopHistoryModal{self.shop1.id}"')
        modal_end = content.find(f'id="markPaidModal{self.shop1.id}"', modal_start)
        if modal_end == -1:
            modal_end = modal_start + 15000
        modal_content = content[modal_start:modal_end]
        self.assertIn("NOV-PAY-456", modal_content)
        self.assertNotIn("OCT-PAY-123", modal_content)

    def test_followup_staff_detail_shows_customer_updations_and_no_shop_details(self):
        """Verify follow-up staff detail displays customer updation details and avoids shop details redirection."""
        from customers.models import CustomerUpdateHistory
        self.client.login(username="admin_rework", password="AdminPassword123!")

        employee = User.objects.create_user(
            username="followup_agent1",
            password="AgentPass123!",
            role=User.Role.FOLLOWUP
        )

        # Log customer updates by the employee
        CustomerUpdateHistory.objects.create(
            customer=self.customer1,
            user=employee,
            notes="Customer promised to make full payment on Friday."
        )

        res = self.client.get(reverse("admin_followup_detail", kwargs={"pk": employee.pk}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Customer Updation Details Logged by")
        self.assertContains(res, "Customer promised to make full payment on Friday.")
        self.assertContains(res, self.customer1.name)
        # Ensure shop_detail is not linked in follow-up staff view
        self.assertNotContains(res, f'href="/admin-dashboard/shops/{self.shop1.pk}/"')

    def test_completed_customers_excluded_from_active_list_and_present_in_completed_list(self):
        """Verify customers who completed their EMI are excluded from the active customer list and shown in Completed List."""
        self.client.login(username="admin_rework", password="AdminPassword123!")

        # Customer 1 pays off their entire loan balance
        self.app1.generate_default_schedule_if_missing()
        full_amount = self.app1.customer_remaining_balance
        self.app1.record_customer_payment(
            amount=full_amount,
            payment_date="2026-11-04",
            payment_mode="UPI",
            transaction_reference="FULL-CLEAR-001"
        )
        self.assertEqual(self.app1.customer_remaining_balance, Decimal("0.00"))

        # In Active Customer List: Customer 1 should NOT be present (excluded because completed)
        res_active = self.client.get(reverse("admin_all_customers"))
        self.assertEqual(res_active.status_code, 200)
        self.assertNotContains(res_active, self.customer1.name)

        # In Completed List: Customer 1 MUST be present with Settled / Completed badge
        res_completed = self.client.get(reverse("admin_completed_customers"))
        self.assertEqual(res_completed.status_code, 200)
        self.assertContains(res_completed, self.customer1.name)
        self.assertContains(res_completed, "Completed")
        self.assertContains(res_completed, "Fully Settled Customers")
        self.assertContains(res_completed, "Completed List")

        # Verify Excel and PDF export for completed list
        res_excel = self.client.get(reverse("admin_completed_customers_export", kwargs={"export_format": "excel"}))
        self.assertEqual(res_excel.status_code, 200)
        res_pdf = self.client.get(reverse("admin_completed_customers_export", kwargs={"export_format": "pdf"}))
        self.assertEqual(res_pdf.status_code, 200)


