"""
Unit and integration tests for EMI Manager: Financial Logic Bug Fixes & Dashboard Synchronization.
Tests all 6 issues:
1. Shop-wise Customers - Missing Loss Action Icon
2. Customer EMI Collection - Financial Reports updating correctly on partials, edits, reversals
3. Shop Payouts - Pending Loan Clearance
4. Shop Login - Outstanding Balance and Amount Received
5. Automatic Adjustment of Collected Amount when a New Loan is Processed
6. Shop Login - Reports and Analytics - Fully Paid by Financier
"""

from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from applications.models import FinanceApplication
from customers.models import Customer
from payments.models import (
    CustomerEMIPayment,
    CustomerEMISchedule,
    FinancierPayment,
    ShopCollectionRecord,
    ShopFinancialTransaction,
)
from payments.services import (
    auto_adjust_shop_available_collections_for_new_loan,
    calculate_application_remaining_loan,
    get_shop_available_collected_balance,
    get_shop_financials,
    get_shop_pending_loan_applications,
    process_shop_collection,
    record_admin_shop_receipt,
)
from shops.models import Shop

User = get_user_model()


class FinancialBugFixesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_user",
            password="adminpassword123",
            email="admin@test.com",
            is_staff=True,
            is_superuser=True,
            role="ADMIN",
        )
        self.shop_user = User.objects.create_user(
            username="shop_owner",
            password="shoppassword123",
            email="shop@test.com",
            role="SHOP",
        )
        self.shop = Shop.objects.create(
            name="Galaxy Mobiles",
            owner_name="Karmeganathan",
            phone="8148032310",
            address="100 North Car Street",
        )
        self.shop_user.shops.add(self.shop)

        self.customer = Customer.objects.create(
            shop=self.shop,
            name="Murugan",
            primary_mobile="9840011223",
            home_address="Chennai",
            work_address="Chennai",
        )

        self.app = FinanceApplication.objects.create(
            application_number="APP-TEST-GALAXY-01",
            shop=self.shop,
            customer=self.customer,
            mobile_model="Samsung Galaxy",
            mobile_price=Decimal("35000.00"),
            down_payment=Decimal("5000.00"),
            financier_request_amount=Decimal("30000.00"),
            monthly_emi=Decimal("5000.00"),
            status=FinanceApplication.Status.APPROVED,
        )

        self.client = Client()

    def test_issue_1_loss_action_icon_in_shopwise_customers(self):
        """Issue 1: Loss action icon and modal exist in Shop-wise Customers view for active customers."""
        self.client.force_login(self.admin)
        url = reverse("admin_shopwise_detail", kwargs={"shop_id": self.shop.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Check Loss button and modal presence
        self.assertContains(response, f"shopwiseMoveToLossModal{self.customer.id}")
        self.assertContains(response, "Move Customer to Loss")
        self.assertContains(response, "bi-exclamation-triangle")

    def test_issue_2_customer_emi_collection_financial_reports_updating(self):
        """Issue 2: Recording, editing, or reversing EMI payment updates Financial Reports Total Collected This Month."""
        self.client.force_login(self.admin)
        schedule = CustomerEMISchedule.objects.create(
            application=self.app,
            customer=self.customer,
            installment_number=1,
            due_date=timezone.now().date(),
            amount=Decimal("5000.00"),
            amount_paid=Decimal("0.00"),
            status=CustomerEMISchedule.InstallmentStatus.PENDING,
        )

        # 1. First collection: partial payment of 2000
        update_url = reverse("admin_update_installment", kwargs={"pk": schedule.id})
        res = self.client.post(
            update_url,
            {
                "amount_paid": "2000.00",
                "payment_mode": "CASH",
                "payment_date": timezone.now().strftime("%Y-%m-%d"),
                "collected_by_shop": "0",
            },
        )
        self.assertEqual(res.status_code, 302)
        schedule.refresh_from_db()
        self.assertEqual(schedule.amount_paid, Decimal("2000.00"))

        # Verify CustomerEMIPayment was created with actual collected amount
        payments = CustomerEMIPayment.objects.filter(customer=self.customer)
        self.assertEqual(payments.count(), 1)
        self.assertEqual(payments.first().amount, Decimal("2000.00"))

        # Check financial report dataset
        analytics_url = reverse("admin_reports")
        rep_res = self.client.get(analytics_url)
        self.assertEqual(rep_res.status_code, 200)
        # Total collected this month should reflect ₹2,000
        self.assertContains(rep_res, "2000")

        # 2. Second collection / edit: total paid becomes 3000
        res2 = self.client.post(
            update_url,
            {
                "amount_paid": "3000.00",
                "payment_mode": "CASH",
                "payment_date": timezone.now().strftime("%Y-%m-%d"),
                "collected_by_shop": "0",
            },
        )
        self.assertEqual(res2.status_code, 302)
        payments.first().refresh_from_db()
        self.assertEqual(payments.first().amount, Decimal("3000.00"))

        # 3. Reversal: amount set to 0.00 removes payment from report
        res3 = self.client.post(
            update_url,
            {
                "amount_paid": "0.00",
                "payment_mode": "CASH",
                "payment_date": timezone.now().strftime("%Y-%m-%d"),
                "collected_by_shop": "0",
            },
        )
        self.assertEqual(res3.status_code, 302)
        self.assertEqual(CustomerEMIPayment.objects.filter(customer=self.customer).count(), 0)

    def test_issue_3_shop_payouts_pending_loan_clearance(self):
        """
        Issue 3: When shop collects from customer, amount is adjusted against pending loan.
        When remaining loan reaches 0, loan status becomes Settled (PAID) and drops from pending loans list.
        """
        # Admin pays 20,000 against the 30,000 loan
        FinancierPayment.objects.create(
            shop=self.shop,
            application=self.app,
            amount_received=Decimal("20000.00"),
            payment_date=timezone.now().date(),
        )

        # Pending loan before shop collection: 10,000
        self.assertEqual(calculate_application_remaining_loan(self.app), Decimal("10000.00"))
        pending_apps = get_shop_pending_loan_applications(self.shop)
        self.assertEqual(len(pending_apps), 1)

        # Shop collects 10,000 directly from customer
        pay = CustomerEMIPayment.objects.create(
            application=self.app,
            customer=self.customer,
            amount=Decimal("10000.00"),
            payment_date=timezone.now().date(),
            collected_by_shop=True,
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            recorded_by=self.admin,
        )
        process_shop_collection(pay)

        # Remaining loan must be 0 and application status must be PAID
        self.app.refresh_from_db()
        rem = calculate_application_remaining_loan(self.app)
        self.assertEqual(rem, Decimal("0.00"))
        self.assertEqual(self.app.status, FinanceApplication.Status.PAID)

        # Pending loan list must now be empty
        pending_apps_after = get_shop_pending_loan_applications(self.shop)
        self.assertEqual(len(pending_apps_after), 0)

    def test_issue_4_shop_login_outstanding_balance_and_amount_received(self):
        """
        Issue 4: When admin marks shop collection as Received:
        1. Reduce collectible balance
        2. Reduce amount pending from shop
        3. Synchronize Shop Dashboard and Admin Dashboard
        """
        # First fully pay the initial application so there is no outstanding loan
        FinancierPayment.objects.create(
            shop=self.shop,
            application=self.app,
            amount_received=Decimal("30000.00"),
            payment_date=timezone.now().date(),
        )
        self.app.status = FinanceApplication.Status.PAID
        self.app.save()

        # Shop collected 15,000 with NO pending loan
        pay = CustomerEMIPayment.objects.create(
            application=self.app,
            customer=self.customer,
            amount=Decimal("15000.00"),
            payment_date=timezone.now().date(),
            collected_by_shop=True,
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            recorded_by=self.admin,
        )
        record = process_shop_collection(pay)
        self.assertEqual(record.collectible_amount, Decimal("15000.00"))

        fin_before = get_shop_financials(self.shop)
        self.assertEqual(fin_before["shop_collectible_balance"], Decimal("15000.00"))

        # Admin marks 10,000 as received from shop
        record_admin_shop_receipt(
            record=record,
            action="yes",
            amount_received=Decimal("10000.00"),
            received_date=timezone.now().date(),
            remarks="Received cash installment from shop owner",
            user=self.admin,
        )

        fin_after = get_shop_financials(self.shop)
        # Remaining amount pending from shop must be 5,000
        self.assertEqual(fin_after["shop_collectible_balance"], Decimal("5000.00"))

        # Check Shop Dashboard view
        self.client.force_login(self.shop_user)
        dash_res = self.client.get(reverse("shop_dashboard"))
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.context["outstanding_amount"], Decimal("5000.00"))
        self.assertTrue(dash_res.context["outstanding_excess"])

    def test_issue_5_automatic_adjustment_when_new_loan_is_processed(self):
        """
        Issue 5: When a shop has available unadjusted collection (e.g. 10,000),
        and a new loan is processed (e.g. 30,000):
        - Available 10,000 is auto-adjusted against the new loan
        - Net amount payable becomes 20,000
        - Shop available collected balance becomes 0
        - Transaction logged as NEW_LOAN_ADJUSTMENT
        """
        # First fully pay the initial application
        FinancierPayment.objects.create(
            shop=self.shop,
            application=self.app,
            amount_received=Decimal("30000.00"),
            payment_date=timezone.now().date(),
        )
        self.app.status = FinanceApplication.Status.PAID
        self.app.save()

        # 1. Shop collects 10,000 with no loans
        pay = CustomerEMIPayment.objects.create(
            application=self.app,
            customer=self.customer,
            amount=Decimal("10000.00"),
            payment_date=timezone.now().date(),
            collected_by_shop=True,
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            recorded_by=self.admin,
        )
        process_shop_collection(pay)
        self.assertEqual(get_shop_available_collected_balance(self.shop), Decimal("10000.00"))

        # 2. New loan processed for shop: 30,000
        new_app = FinanceApplication.objects.create(
            application_number="APP-TEST-GALAXY-02",
            shop=self.shop,
            customer=self.customer,
            mobile_model="OnePlus 12",
            mobile_price=Decimal("35000.00"),
            down_payment=Decimal("5000.00"),
            financier_request_amount=Decimal("30000.00"),
            monthly_emi=Decimal("5000.00"),
            status=FinanceApplication.Status.APPROVED,
        )

        # Trigger auto adjustment
        adj_amount = auto_adjust_shop_available_collections_for_new_loan(new_app, user=self.admin)
        self.assertEqual(adj_amount, Decimal("10000.00"))

        # Net amount payable should now be 20,000
        net_payable = calculate_application_remaining_loan(new_app)
        self.assertEqual(net_payable, Decimal("20000.00"))

        # Available collected balance is now 0
        self.assertEqual(get_shop_available_collected_balance(self.shop), Decimal("0.00"))

        # Verify transaction log
        txn = ShopFinancialTransaction.objects.filter(
            shop=self.shop,
            transaction_type=ShopFinancialTransaction.TransactionType.NEW_LOAN_ADJUSTMENT,
        ).first()
        self.assertIsNotNone(txn)
        self.assertEqual(txn.amount, Decimal("10000.00"))

    def test_issue_6_shop_reports_fully_paid_by_financier(self):
        """
        Issue 6: Shop reports accurately reflect loans completely cleared by adjustments and payouts.
        Status count for Fully Paid by Financier is incremented and loan status is PAID.
        """
        # Payout the entire 30,000 for self.app
        FinancierPayment.objects.create(
            shop=self.shop,
            application=self.app,
            amount_received=Decimal("30000.00"),
            payment_date=timezone.now().date(),
        )

        self.client.force_login(self.shop_user)
        rep_res = self.client.get(reverse("shop_reports"))
        self.assertEqual(rep_res.status_code, 200)

        # Status breakdown should show Fully Paid by Financier count = 1
        breakdown = rep_res.context["status_breakdown"]
        paid_entry = next((item for item in breakdown if item["key"] == "PAID"), None)
        self.assertIsNotNone(paid_entry)
        self.assertEqual(paid_entry["count"], 1)

        # Financial sums
        self.assertEqual(rep_res.context["total_requested"], Decimal("30000.00"))
        self.assertEqual(rep_res.context["total_received"], Decimal("30000.00"))
        self.assertEqual(rep_res.context["outstanding"], Decimal("0.00"))
