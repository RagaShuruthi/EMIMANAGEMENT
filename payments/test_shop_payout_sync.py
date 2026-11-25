from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from applications.models import FinanceApplication
from customers.models import Customer
from followups.models import FollowUpAssignment, FollowUpActivity
from payments.models import CustomerEMIPayment, CustomerEMISchedule, ShopCollectionRecord, ShopFinancialTransaction
from payments.services import get_shop_financials, process_shop_collection, recalculate_shop_financials
from shops.models import Shop


class ShopPayoutSyncTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Admin user
        self.admin = User.objects.create_user(
            username="admin_sync",
            password="adminpassword123",
            role=User.Role.ADMIN,
            is_staff=True,
            is_superuser=True
        )

        # Shop owner user & Shop
        self.shop_user = User.objects.create_user(
            username="shopowner_sync",
            password="shoppassword123",
            role=User.Role.SHOP_OWNER
        )
        self.shop = Shop.objects.create(
            name="Mega Mobile Hub",
            owner_name="Suresh Kumar",
            phone="9876543200",
            owner_account=self.shop_user
        )

        # Follow-up staff user & assignment
        self.followup_user = User.objects.create_user(
            username="followup_staff",
            password="followuppassword123",
            role=User.Role.FOLLOWUP
        )
        self.assignment = FollowUpAssignment.objects.create(
            employee=self.followup_user,
            shop=self.shop,
            assigned_by=self.admin,
            is_active=True
        )

        # Customer & Finance Application (Financier Amount = ₹50,000, 10 months x ₹5,000)
        self.customer = Customer.objects.create(
            shop=self.shop,
            name="Anand Sharma",
            primary_mobile="9876543201",
            secondary_mobile="9876543202",
            home_address="Flat 101, Green Heights, Chennai",
            work_address="Tech Park, Chennai",
        )
        self.app = FinanceApplication.objects.create(
            application_number="APP-SYNC-50K",
            shop=self.shop,
            customer=self.customer,
            mobile_model="iPhone 15 Pro",
            mobile_price=Decimal("50000.00"),
            total_emi_amount=Decimal("50000.00"),
            down_payment=Decimal("0.00"),
            financier_request_amount=Decimal("50000.00"),
            monthly_emi=Decimal("5000.00"),
            emi_duration=10,
            status=FinanceApplication.Status.APPROVED,
            application_date=timezone.localdate(),
        )
        self.app.generate_default_schedule_if_missing()

    def test_01_shop_collects_full_emi(self):
        """Scenario 1: Shop collects a full EMI (₹5,000)."""
        sched1 = self.app.emi_schedules.order_by("installment_number").first()
        payment = self.app.record_customer_payment(
            amount=Decimal("5000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            transaction_reference="SHOP-PAY-01",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(payment, user=self.shop_user)

        sched1.refresh_from_db()
        self.assertTrue(sched1.is_paid)
        self.assertEqual(sched1.amount_paid, Decimal("5000.00"))
        self.assertEqual(sched1.remaining_amount, Decimal("0.00"))

        fin = get_shop_financials(self.shop)
        self.assertEqual(fin["total_payable"], Decimal("50000.00"))
        self.assertEqual(fin["total_shop_collected"], Decimal("5000.00"))
        self.assertEqual(fin["shop_collection_adjusted"], Decimal("5000.00"))
        self.assertEqual(fin["remaining_due"], Decimal("45000.00"))
        self.assertEqual(fin["shop_collectible_balance"], Decimal("0.00"))

    def test_02_shop_collects_partial_emi(self):
        """Scenario 2: Shop collects a partial EMI (₹2,000 out of ₹5,000)."""
        sched1 = self.app.emi_schedules.order_by("installment_number").first()
        payment = self.app.record_customer_payment(
            amount=Decimal("2000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.UPI,
            transaction_reference="SHOP-PAY-PARTIAL-1",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(payment, user=self.shop_user)

        sched1.refresh_from_db()
        self.assertFalse(sched1.is_paid)
        self.assertEqual(sched1.amount_paid, Decimal("2000.00"))
        self.assertEqual(sched1.remaining_amount, Decimal("3000.00"))
        self.assertEqual(sched1.status, CustomerEMISchedule.InstallmentStatus.PARTIAL)

        fin = get_shop_financials(self.shop)
        self.assertEqual(fin["total_shop_collected"], Decimal("2000.00"))
        self.assertEqual(fin["remaining_due"], Decimal("48000.00"))

    def test_03_shop_collects_multiple_partial_payments(self):
        """Scenario 3: Shop collects multiple partial payments (₹2,000 + ₹3,000 = ₹5,000)."""
        sched1 = self.app.emi_schedules.order_by("installment_number").first()
        pay1 = self.app.record_customer_payment(
            amount=Decimal("2000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.UPI,
            transaction_reference="SHOP-MUL-1",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(pay1, user=self.shop_user)

        pay2 = self.app.record_customer_payment(
            amount=Decimal("3000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            transaction_reference="SHOP-MUL-2",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(pay2, user=self.shop_user)

        sched1.refresh_from_db()
        self.assertTrue(sched1.is_paid)
        self.assertEqual(sched1.amount_paid, Decimal("5000.00"))
        self.assertEqual(sched1.status, CustomerEMISchedule.InstallmentStatus.PAID)

        fin = get_shop_financials(self.shop)
        self.assertEqual(fin["total_shop_collected"], Decimal("5000.00"))
        self.assertEqual(fin["remaining_due"], Decimal("45000.00"))

    def test_04_shop_collection_reaches_financier_amount_exactly(self):
        """Scenario 4: Shop collection reaches financier amount exactly (₹50,000)."""
        pay = self.app.record_customer_payment(
            amount=Decimal("50000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.BANK,
            transaction_reference="SHOP-EXACT-50K",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(pay, user=self.shop_user)

        fin = get_shop_financials(self.shop)
        self.assertEqual(fin["total_payable"], Decimal("50000.00"))
        self.assertEqual(fin["total_shop_collected"], Decimal("50000.00"))
        self.assertEqual(fin["shop_collection_adjusted"], Decimal("50000.00"))
        self.assertEqual(fin["remaining_due"], Decimal("0.00"))
        self.assertEqual(fin["excess_collection"], Decimal("0.00"))
        self.assertEqual(fin["shop_collectible_balance"], Decimal("0.00"))
        self.assertEqual(fin["payment_status"], "PAID")

    def test_05_shop_collection_exceeds_financier_amount(self):
        """
        Scenario 5: Shop collection exceeds financier amount:
        Financier Amount = ₹50,000.
        Collected by Shop: ₹40,000 + ₹20,000 = ₹60,000.
        Financier Adjustment: ₹50,000.
        Remaining Financier: ₹0.
        Excess Collection: ₹10,000.
        Collected by Shop must remain ₹60,000 (NOT ₹70,000).
        """
        # Configure application so customer has ₹60,000 total EMI while financier request is ₹50,000
        self.app.mobile_price = Decimal("70000.00")
        self.app.down_payment = Decimal("20000.00")
        self.app.total_emi_amount = Decimal("60000.00")
        self.app.emi_duration = 12
        self.app.monthly_emi = Decimal("5000.00")
        self.app.save()
        self.app.emi_schedules.all().delete()
        self.app.generate_default_schedule_if_missing()

        # Step 1: ₹40,000 collected
        pay1 = self.app.record_customer_payment(
            amount=Decimal("40000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            transaction_reference="SHOP-40K",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(pay1, user=self.shop_user)

        fin1 = get_shop_financials(self.shop)
        self.assertEqual(fin1["total_shop_collected"], Decimal("40000.00"))
        self.assertEqual(fin1["shop_collection_adjusted"], Decimal("40000.00"))
        self.assertEqual(fin1["remaining_due"], Decimal("10000.00"))
        self.assertEqual(fin1["excess_collection"], Decimal("0.00"))

        # Step 2: Another ₹20,000 collected
        pay2 = self.app.record_customer_payment(
            amount=Decimal("20000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            transaction_reference="SHOP-20K",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(pay2, user=self.shop_user)

        fin2 = get_shop_financials(self.shop)
        # Total Collected by Shops MUST remain ₹60,000 (NOT ₹70,000!)
        self.assertEqual(fin2["total_shop_collected"], Decimal("60000.00"))
        self.assertEqual(fin2["shop_collection_adjusted"], Decimal("50000.00"))
        self.assertEqual(fin2["remaining_due"], Decimal("0.00"))
        self.assertEqual(fin2["excess_collection"], Decimal("10000.00"))
        self.assertEqual(fin2["shop_collectible_balance"], Decimal("10000.00"))

    def test_06_existing_collection_amount_corrected(self):
        """
        Scenario 6: Existing collection amount corrected:
        Previous collection: ₹5,000.
        Corrected collection: ₹3,000.
        Must replace ₹5,000 with ₹3,000 (not add on top to make ₹8,000).
        """
        pay = self.app.record_customer_payment(
            amount=Decimal("5000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            transaction_reference="SHOP-EDIT-01",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(pay, user=self.shop_user)
        self.assertEqual(get_shop_financials(self.shop)["total_shop_collected"], Decimal("5000.00"))

        # Correct amount from ₹5,000 to ₹3,000
        self.client.force_login(self.admin)
        edit_url = reverse("admin_edit_customer_payment", kwargs={"pk": pay.pk})
        res = self.client.post(edit_url, {
            "amount": "3000.00",
            "payment_date": timezone.localdate().strftime("%Y-%m-%d"),
            "payment_mode": "CASH",
            "transaction_reference": "SHOP-EDIT-01",
            "remarks": "Corrected collection amount",
        })
        self.assertEqual(res.status_code, 302)

        fin = get_shop_financials(self.shop)
        # Must be exactly ₹3,000, NOT ₹8,000!
        self.assertEqual(fin["total_shop_collected"], Decimal("3000.00"))
        self.assertEqual(fin["shop_collection_adjusted"], Decimal("3000.00"))
        self.assertEqual(fin["remaining_due"], Decimal("47000.00"))

    def test_07_verify_no_duplicate_amount_is_added(self):
        """Scenario 7: Multiple page refreshes or recalculations must not add duplicate amounts."""
        pay = self.app.record_customer_payment(
            amount=Decimal("5000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            transaction_reference="SHOP-NODUP",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(pay, user=self.shop_user)

        self.client.force_login(self.admin)
        self.client.get(reverse("admin_shop_payments"))
        self.client.get(reverse("admin_shop_payments"))
        recalculate_shop_financials(self.shop)

        fin = get_shop_financials(self.shop)
        self.assertEqual(fin["total_shop_collected"], Decimal("5000.00"))
        self.assertEqual(CustomerEMIPayment.objects.filter(application=self.app).count(), 1)

    def test_08_verify_customer_details_updates(self):
        """Scenario 8: Customer Details displays updated total paid, remaining balance, schedule and payment history."""
        pay = self.app.record_customer_payment(
            amount=Decimal("5000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.UPI,
            transaction_reference="SHOP-CUST-SYNC-01",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(pay, user=self.shop_user)

        self.client.force_login(self.admin)
        detail_url = reverse("admin_customer_detail", kwargs={"pk": self.customer.pk})
        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, 200)

        # Check total paid and remaining balance
        self.assertContains(res, "5000.00")
        self.assertContains(res, "SHOP-CUST-SYNC-01")
        self.assertContains(res, "Shop Collection")
        self.assertEqual(self.app.total_customer_paid, Decimal("5000.00"))
        self.assertEqual(self.app.customer_remaining_balance, Decimal("45000.00"))

    def test_09_verify_admin_followups_updates(self):
        """Scenario 9: Admin Dashboard Follow-ups reflects updated customer payment status and activities."""
        self.client.force_login(self.shop_user)
        # Record payment through shop portal
        res = self.client.post(reverse("shop_emi_collection"), {
            "customer_id": self.customer.id,
            "amount": "5000.00",
            "payment_mode": "UPI",
            "reference": "SHOP-FOLLOWUP-ADMIN-TEST",
            "remarks": "Paid at shop counter",
        })
        self.assertEqual(res.status_code, 302)

        # Admin inspects follow-up employee detail page
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse("admin_followup_detail", kwargs={"pk": self.followup_user.pk}))
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "Anand Sharma")
        self.assertContains(res_admin, "Mega Mobile Hub")

    def test_10_verify_followup_dashboard_updates(self):
        """Scenario 10: Follow-up Dashboard updates outstanding amount and removes customer from pending once settled."""
        # Create an initial pending activity for customer
        act = FollowUpActivity.objects.create(
            employee=self.followup_user,
            customer=self.customer,
            shop=self.shop,
            activity_type=FollowUpActivity.ActivityType.CALL,
            status=FollowUpActivity.Status.PENDING,
            next_followup_date=timezone.localdate(),
            notes="Call customer for today's EMI"
        )

        # Before payment: Outstanding is 50,000
        self.client.force_login(self.followup_user)
        dash_res1 = self.client.get(reverse("followup_dashboard"))
        self.assertEqual(dash_res1.status_code, 200)
        self.assertEqual(dash_res1.context["total_outstanding_sum"], Decimal("50000.00"))
        self.assertEqual(dash_res1.context["total_pending"], 1)

        # Shop records payment of ₹5,000 for today's due
        self.client.force_login(self.shop_user)
        self.client.post(reverse("shop_emi_collection"), {
            "customer_id": self.customer.id,
            "amount": "5000.00",
            "payment_mode": "CASH",
            "reference": "SHOP-AUTO-COMPLETE",
        })

        # After payment: Pending activity is auto-completed!
        act.refresh_from_db()
        self.assertEqual(act.status, FollowUpActivity.Status.COMPLETED)
        self.assertEqual(act.outcome, FollowUpActivity.Outcome.PAID)

        # Follow-up staff refreshes dashboard: outstanding is now 45,000 and pending drops to 0
        self.client.force_login(self.followup_user)
        dash_res2 = self.client.get(reverse("followup_dashboard"))
        self.assertEqual(dash_res2.status_code, 200)
        self.assertEqual(dash_res2.context["total_outstanding_sum"], Decimal("45000.00"))
        self.assertEqual(dash_res2.context["total_pending"], 0)

    def test_11_verify_shop_payouts_transaction_audit_does_not_display_customer_names(self):
        """Scenario 11: Shop Payouts Transaction Audit does NOT display customer names."""
        pay = self.app.record_customer_payment(
            amount=Decimal("5000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            transaction_reference="SHOP-AUDIT-NAMELESS",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(pay, user=self.shop_user)

        self.client.force_login(self.admin)
        payouts_url = reverse("admin_shop_payments") + "?tab=transactions"
        res = self.client.get(payouts_url)
        self.assertEqual(res.status_code, 200)

        # Customer name "Anand Sharma" must NOT be in the response content of the transaction audit tab
        content = res.content.decode("utf-8")
        self.assertNotIn("Anand Sharma", content)

        # Financial information must be displayed
        self.assertIn("Mega Mobile Hub", content)
        self.assertIn("5000.00", content)

    def test_12_verify_all_financial_totals_match_across_dashboards(self):
        """Scenario 12: All financial totals match across Admin Dashboard, Shop Payouts, and Shop Portal."""
        pay = self.app.record_customer_payment(
            amount=Decimal("15000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.BANK,
            transaction_reference="SHOP-TOTAL-SYNC",
            recorded_by=self.shop_user,
            collected_by_shop=True
        )
        process_shop_collection(pay, user=self.shop_user)

        fin = get_shop_financials(self.shop)
        # Expected: Disbursed = 15,000, Remaining = 35,000
        self.assertEqual(fin["amount_disbursed"], Decimal("15000.00"))
        self.assertEqual(fin["remaining_due"], Decimal("35000.00"))

        # 1. Admin Dashboard overview
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "15000.00")
        self.assertContains(res_admin, "35000.00")

        # 2. Shop Payouts page
        res_payouts = self.client.get(reverse("admin_shop_payments"))
        self.assertEqual(res_payouts.status_code, 200)
        self.assertContains(res_payouts, "15000.00")
        self.assertContains(res_payouts, "35000.00")
