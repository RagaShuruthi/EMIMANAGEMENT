from decimal import Decimal
from datetime import date
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from shops.models import Shop
from customers.models import Customer
from applications.models import FinanceApplication
from payments.models import CustomerEMISchedule, CustomerEMIPayment
from admin_dashboard.views import get_analytics_dataset, prepare_customer_management_dataset


class Section12SyncTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin_sec12",
            email="admin_sec12@example.com",
            password="adminpassword123",
            role="ADMIN"
        )
        self.client = Client()
        self.client.force_login(self.admin)

        self.shop = Shop.objects.create(
            name="Alpha Mobile Hub",
            owner_name="John Doe",
            phone="9876543210"
        )
        self.customer = Customer.objects.create(
            shop=self.shop,
            name="Alice Smith",
            primary_mobile="9876543211"
        )
        self.app = FinanceApplication.objects.create(
            customer=self.customer,
            shop=self.shop,
            application_number="APP-SEC12-001",
            mobile_model="iPhone 15",
            mobile_price=Decimal("60000.00"),
            down_payment=Decimal("10000.00"),
            financier_request_amount=Decimal("50000.00"),
            monthly_emi=Decimal("5000.00"),
            emi_duration=10,
            status=FinanceApplication.Status.APPROVED,
            application_date=date.today(),
            monthly_due_day=5
        )
        self.app.generate_default_schedule_if_missing()

    def test_partial_payment_reflects_in_business_analytics(self):
        """Scenario 1: Partial payment of Rs. 2,000 on a Rs. 5,000 EMI reflects immediately in Business Analytics."""
        today = date.today()
        # Record partial payment of 2000
        pay = self.app.record_customer_payment(
            amount=Decimal("2000.00"),
            payment_date=today,
            payment_mode="UPI",
            transaction_reference="UPI-PARTIAL-001",
            recorded_by=self.admin
        )
        self.assertIsNotNone(pay)

        # Check analytics
        ds = get_analytics_dataset(today.year, today.month, self.shop.id)
        self.assertEqual(ds["total_collections"], Decimal("2000.00"))

        # Subsequent payment of 1,000 -> total becomes 3,000, not 4,000 or duplicate
        pay2 = self.app.record_customer_payment(
            amount=Decimal("1000.00"),
            payment_date=today,
            payment_mode="UPI",
            transaction_reference="UPI-PARTIAL-002",
            recorded_by=self.admin
        )
        self.assertIsNotNone(pay2)

        ds2 = get_analytics_dataset(today.year, today.month, self.shop.id)
        self.assertEqual(ds2["total_collections"], Decimal("3000.00"))

    def test_payment_reversal_deducts_from_business_analytics_and_preserves_history(self):
        """Scenario 2: Payment reversal deducts amount from Business Analytics and preserves transaction audit trail."""
        today = date.today()
        pay = self.app.record_customer_payment(
            amount=Decimal("2000.00"),
            payment_date=today,
            payment_mode="CASH",
            transaction_reference="CASH-REV-001",
            recorded_by=self.admin
        )
        ds_before = get_analytics_dataset(today.year, today.month, self.shop.id)
        self.assertEqual(ds_before["total_collections"], Decimal("2000.00"))

        # Reverse the payment via view
        rev_url = reverse("admin_reverse_customer_payment", kwargs={"pk": pay.id})
        resp = self.client.post(rev_url, {
            "reversal_type": "full",
            "reversal_reason": "Customer cancellation request"
        })
        self.assertEqual(resp.status_code, 302)

        # Analytics must now show 0
        ds_after = get_analytics_dataset(today.year, today.month, self.shop.id)
        self.assertEqual(ds_after["total_collections"], Decimal("0.00"))

        # Verify transaction records were NOT deleted
        pay.refresh_from_db()
        self.assertTrue(pay.is_reversed)
        self.assertEqual(pay.reversed_amount, Decimal("2000.00"))
        self.assertEqual(pay.net_amount, Decimal("0.00"))

        # Verify reversal audit record exists
        reversal_record = CustomerEMIPayment.objects.filter(reversal_of=pay).first()
        self.assertIsNotNone(reversal_record)
        self.assertTrue(reversal_record.is_reversed)
        self.assertEqual(reversal_record.amount, Decimal("2000.00"))

    def test_move_to_next_due_and_customer_list_view_display(self):
        """Scenario 3, 4, 5: Partial payment with Move to Next Due advances due date, Keep as Present retains date, and list view displays remaining amount."""
        today = date.today()
        sched1 = self.app.emi_schedules.order_by("installment_number").first()
        orig_due = sched1.due_date

        # Update installment to partial 2000 paid with move_to_next
        update_url = reverse("admin_update_installment", kwargs={"pk": sched1.pk})
        resp = self.client.post(update_url, {
            "amount": "5000.00",
            "amount_paid": "2000.00",
            "due_date": orig_due.strftime("%Y-%m-%d"),
            "status": "PARTIAL",
            "partial_action": "move_to_next",
        })
        self.assertEqual(resp.status_code, 302)

        sched1.refresh_from_db()
        self.assertEqual(sched1.amount_paid, Decimal("2000.00"))
        self.assertEqual(sched1.remaining_amount, Decimal("3000.00"))
        self.assertNotEqual(sched1.due_date, orig_due)
        self.assertGreater(sched1.due_date, orig_due)

        # Check Customer List View row: monthly_emi must reflect remaining amount 3,000, not static 5,000
        from django.test import RequestFactory
        rf = RequestFactory()
        req = rf.get('/admin-dashboard/all-customers/')
        req.user = self.admin
        dataset = prepare_customer_management_dataset(Customer.objects.filter(id=self.customer.id), request=req)
        self.assertEqual(len(dataset["all_rows"]), 1)
        row = dataset["all_rows"][0]
        self.assertEqual(row["monthly_emi"], Decimal("3000.00"))

        # Now test Keep as Present on installment 2
        sched2 = self.app.emi_schedules.filter(installment_number=2).first()
        orig_due2 = sched2.due_date
        update_url2 = reverse("admin_update_installment", kwargs={"pk": sched2.pk})
        resp2 = self.client.post(update_url2, {
            "amount": "5000.00",
            "amount_paid": "1000.00",
            "due_date": orig_due2.strftime("%Y-%m-%d"),
            "status": "PARTIAL",
            "partial_action": "keep_present",
        })
        self.assertEqual(resp2.status_code, 302)
        sched2.refresh_from_db()
        self.assertEqual(sched2.due_date, orig_due2)
        self.assertEqual(sched2.amount_paid, Decimal("1000.00"))
        self.assertEqual(sched2.remaining_amount, Decimal("4000.00"))
