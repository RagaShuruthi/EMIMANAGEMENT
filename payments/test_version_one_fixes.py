import calendar
from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from applications.models import FinanceApplication
from customers.models import Customer
from payments.models import CustomerEMIPayment, CustomerEMISchedule, FinancierPayment
from shops.models import Shop


class VersionOneFixesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_user(
            username="admin_v1",
            password="adminpassword123",
            role=User.Role.ADMIN,
            is_staff=True,
            is_superuser=True
        )
        self.shop_owner = User.objects.create_user(
            username="shopowner_v1",
            password="shoppassword123",
            role=User.Role.SHOP_OWNER
        )
        self.shop = Shop.objects.create(
            name="V1 Test Shop",
            owner_name="V1 Owner",
            phone="9876543210",
            owner_account=self.shop_owner
        )
        self.customer = Customer.objects.create(
            shop=self.shop,
            name="Ravi Kumar",
            primary_mobile="9876543211",
            secondary_mobile="9876543212",
            home_address="123 Test Street, Chennai",
            work_address="456 Office Road, Chennai",
        )
        self.app = FinanceApplication.objects.create(
            application_number="APP-TEST-V1-01",
            shop=self.shop,
            customer=self.customer,
            mobile_model="Samsung S24",
            mobile_price=Decimal("60000.00"),
            down_payment=Decimal("10000.00"),
            financier_request_amount=Decimal("50000.00"),
            monthly_emi=Decimal("5000.00"),
            status=FinanceApplication.Status.UNDER_REVIEW,
            application_date=timezone.localdate(),
        )
        self.client.force_login(self.admin_user)

    def test_customer_approval_logic_in_analytics(self):
        """
        Unapproved applications (UNDER_REVIEW, REJECTED) must NOT be included in Business Analytics.
        Only when status is APPROVED / funded are they counted.
        """
        from admin_dashboard.views import get_analytics_dataset
        today = timezone.localdate()

        # Under Review: Not included in analytics
        dataset = get_analytics_dataset(today.year, today.month, "all")
        self.assertEqual(dataset["total_customers"], 0)
        self.assertEqual(dataset["total_financed"], Decimal("0.00"))

        # Reject application: Still not included
        self.app.status = FinanceApplication.Status.REJECTED
        self.app.save(update_fields=["status"])
        dataset = get_analytics_dataset(today.year, today.month, "all")
        self.assertEqual(dataset["total_customers"], 0)
        self.assertEqual(dataset["total_financed"], Decimal("0.00"))

        # Approve application: Now included!
        approval_url = reverse("admin_customer_change_status", kwargs={"pk": self.customer.pk})
        res = self.client.post(approval_url, {"status": FinanceApplication.Status.APPROVED})
        self.assertEqual(res.status_code, 302)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, FinanceApplication.Status.APPROVED)

        dataset = get_analytics_dataset(today.year, today.month, "all")
        self.assertEqual(dataset["total_customers"], 1)
        self.assertEqual(dataset["total_financed"], Decimal("50000.00"))

    def test_partial_payment_calculation_and_no_double_counting(self):
        """
        EMI Due: 5000.
        First payment: 2000 -> Remaining: 3000, status PARTIAL.
        Second payment: 3000 -> Remaining: 0, status PAID.
        Total collected: 5000, NOT 7000.
        """
        self.app.status = FinanceApplication.Status.APPROVED
        self.app.save(update_fields=["status"])
        self.app.generate_default_schedule_if_missing()

        # 1. First partial payment: 2000
        pay1 = self.app.record_customer_payment(
            amount=Decimal("2000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.CASH,
            transaction_reference="TX-PARTIAL-1",
            recorded_by=self.admin_user
        )
        self.assertIsNotNone(pay1)

        sched1 = self.app.emi_schedules.order_by("installment_number").first()
        self.assertEqual(sched1.amount_paid, Decimal("2000.00"))
        self.assertEqual(sched1.remaining_amount, Decimal("3000.00"))
        self.assertEqual(sched1.status, CustomerEMISchedule.InstallmentStatus.PARTIAL)
        self.assertFalse(sched1.is_paid)

        # 2. Second payment: 3000
        pay2 = self.app.record_customer_payment(
            amount=Decimal("3000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.UPI,
            transaction_reference="TX-PARTIAL-2",
            recorded_by=self.admin_user
        )
        self.assertIsNotNone(pay2)

        sched1.refresh_from_db()
        self.assertEqual(sched1.amount_paid, Decimal("5000.00"))
        self.assertEqual(sched1.remaining_amount, Decimal("0.00"))
        self.assertEqual(sched1.status, CustomerEMISchedule.InstallmentStatus.PAID)
        self.assertTrue(sched1.is_paid)

        # Check total payments recorded
        all_payments = CustomerEMIPayment.objects.filter(application=self.app)
        self.assertEqual(all_payments.count(), 2)
        total_collected = sum((p.amount for p in all_payments), Decimal("0.00"))
        self.assertEqual(total_collected, Decimal("5000.00"))

        # Verify Business Analytics report receives exactly 5000, NOT 7000
        from admin_dashboard.views import get_analytics_dataset
        today = timezone.localdate()
        dataset = get_analytics_dataset(today.year, today.month, "all")
        self.assertEqual(dataset["total_collections"], Decimal("5000.00"))

        # Verify page refresh / multiple GET requests do not alter or inflate collected amount
        rep_res1 = self.client.get(reverse("admin_reports"))
        self.assertEqual(rep_res1.status_code, 200)
        rep_res2 = self.client.get(reverse("admin_reports"))
        self.assertEqual(rep_res2.status_code, 200)

        dataset_after_refresh = get_analytics_dataset(today.year, today.month, "all")
        self.assertEqual(dataset_after_refresh["total_collections"], Decimal("5000.00"))
        self.assertEqual(CustomerEMIPayment.objects.filter(application=self.app).count(), 2)

    def test_move_to_next_due_action(self):
        """
        Advance current unpaid EMI due date forward by 1 monthly cycle.
        Maintain sequential chronological order of subsequent unpaid schedules.
        Do not create duplicate EMI records or alter amounts.
        """
        self.app.status = FinanceApplication.Status.APPROVED
        self.app.save(update_fields=["status"])
        self.app.generate_default_schedule_if_missing()

        schedules = list(self.app.emi_schedules.order_by("installment_number"))
        sched1 = schedules[0]
        sched2 = schedules[1]
        sched3 = schedules[2]

        orig_sched1_due = sched1.due_date
        orig_sched2_due = sched2.due_date
        orig_sched1_amt = sched1.amount
        total_sched_count = len(schedules)

        # Fast-forward updated_at past the 2-second debounce threshold
        CustomerEMISchedule.objects.filter(application=self.app).update(
            updated_at=timezone.now() - timedelta(seconds=10)
        )
        sched1.refresh_from_db()

        # Post move to next due
        move_url = reverse("admin_move_to_next_due", kwargs={"pk": self.customer.pk})
        res = self.client.post(move_url, {"schedule_id": sched1.pk})
        self.assertEqual(res.status_code, 302)

        sched1.refresh_from_db()
        sched2.refresh_from_db()
        sched3.refresh_from_db()

        # Check sched1 moved +1 month
        self.assertGreater(sched1.due_date, orig_sched1_due)
        # Check sched2 was shifted forward if needed to remain strictly after sched1
        self.assertGreater(sched2.due_date, sched1.due_date)
        self.assertGreater(sched3.due_date, sched2.due_date)

        # Verify no duplicate records created and amounts unchanged
        self.assertEqual(self.app.emi_schedules.count(), total_sched_count)
        self.assertEqual(sched1.amount, orig_sched1_amt)

    def test_save_or_mark_paid_creates_zero_duplicate_transactions(self):
        """
        Marking an installment as PAID or updating existing installment amounts
        must not duplicate transactions or inflate totals in analytics.
        """
        self.app.status = FinanceApplication.Status.APPROVED
        self.app.save(update_fields=["status"])
        self.app.generate_default_schedule_if_missing()

        sched = self.app.emi_schedules.order_by("installment_number").first()
        update_url = reverse("admin_update_installment", kwargs={"pk": sched.pk})

        # Save installment as 5000 PAID
        res1 = self.client.post(
            update_url,
            {
                "amount": "5000.00",
                "amount_paid": "5000.00",
                "status": "PAID",
                "due_date": sched.due_date.strftime("%Y-%m-%d"),
            }
        )
        self.assertEqual(res1.status_code, 302)
        sched.refresh_from_db()
        self.assertTrue(sched.is_paid)
        self.assertEqual(sched.amount_paid, Decimal("5000.00"))
        self.assertEqual(CustomerEMIPayment.objects.filter(schedule=sched).count(), 1)

        # Repeatedly saving without changing amount must create ZERO new payments
        res2 = self.client.post(
            update_url,
            {
                "amount": "5000.00",
                "amount_paid": "5000.00",
                "status": "PAID",
                "due_date": sched.due_date.strftime("%Y-%m-%d"),
            }
        )
        self.assertEqual(res2.status_code, 302)
        self.assertEqual(CustomerEMIPayment.objects.filter(schedule=sched).count(), 1)

        # Checking analytics
        from admin_dashboard.views import get_analytics_dataset
        today = timezone.localdate()
        dataset = get_analytics_dataset(today.year, today.month, "all")
        self.assertEqual(dataset["total_collections"], Decimal("5000.00"))
