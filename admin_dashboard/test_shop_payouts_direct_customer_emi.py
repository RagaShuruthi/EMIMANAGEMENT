from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from shops.models import Shop
from customers.models import Customer
from applications.models import FinanceApplication
from payments.models import CustomerEMISchedule, CustomerEMIPayment, FinancierPayment

User = get_user_model()


class ShopPayoutsDirectCustomerEMITests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin_payout_user",
            password="AdminPassword123!",
            email="admin_payout@example.com"
        )
        self.client.login(username="admin_payout_user", password="AdminPassword123!")

        self.shop = Shop.objects.create(
            name="Apex Mobile Hub",
            owner_name="Rajesh Kumar",
            phone="9876543210",
            address="123 Market Road"
        )

        self.customer = Customer.objects.create(
            shop=self.shop,
            name="Suresh Raina",
            primary_mobile="9888877771",
            home_address="45 Cricket Colony"
        )

        self.application = FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.customer,
            mobile_model="OnePlus 12R",
            mobile_price=Decimal("40000.00"),
            down_payment=Decimal("10000.00"),
            financier_request_amount=Decimal("30000.00"),
            monthly_emi=Decimal("5000.00"),
            emi_duration=6,
            monthly_due_day=15,
            status=FinanceApplication.Status.APPROVED
        )

        self.sched1 = self.application.emi_schedules.get(installment_number=1)
        self.sched2 = self.application.emi_schedules.get(installment_number=2)

    def test_shop_collected_emi_reflected_in_payout_audit_and_pending_loans(self):
        """
        Verify that when a shop collects EMI directly from customer:
        1. In Admin Shop Payouts, the loan paid directly to shop is NOT in pending loans (pending not needed)
        2. Another loan where customer has NOT paid directly to shop appears in the pending loans list (admin has to pay)
        3. The shop payout details history modal has the audit transaction marked 'Directly Paid by Customer'
        4. Financier remaining due is reduced by the collected amount
        """
        # Create second application for customer who has NOT paid directly to shop
        customer2 = Customer.objects.create(
            shop=self.shop,
            name="Rohit Sharma",
            primary_mobile="9888877772",
            home_address="10 Stadium Road"
        )
        app2 = FinanceApplication.objects.create(
            shop=self.shop,
            customer=customer2,
            mobile_model="Samsung S24",
            mobile_price=Decimal("50000.00"),
            down_payment=Decimal("15000.00"),
            financier_request_amount=Decimal("35000.00"),
            monthly_emi=Decimal("7000.00"),
            emi_duration=5,
            monthly_due_day=10,
            status=FinanceApplication.Status.APPROVED
        )

        # Shop directly collects 5000 EMI from first customer (self.customer)
        direct_payment = CustomerEMIPayment.objects.create(
            application=self.application,
            customer=self.customer,
            schedule=self.sched1,
            amount=Decimal("5000.00"),
            payment_date=timezone.localdate(),
            payment_mode=CustomerEMIPayment.PaymentMode.UPI,
            transaction_reference="UPI-DIRECT-8899",
            collected_by_shop=True,
            remarks="Direct shop collection"
        )

        res = self.client.get(reverse("admin_shop_payments"))
        self.assertEqual(res.status_code, 200)

        # 1. Check pending_apps:
        # self.application was directly paid to shop -> pending is NOT needed (excluded)
        # app2 was NOT directly paid to shop -> admin has to pay, pending list appears with app2
        row = res.context["payment_rows"][0]
        self.assertEqual(row["pending_apps_count"], 1)
        self.assertEqual(len(row["pending_apps"]), 1)
        self.assertEqual(row["pending_apps"][0].id, app2.id)

        # self.application should be in direct_paid_apps, NOT in pending_apps
        self.assertEqual(row["direct_paid_apps_count"], 1)
        self.assertEqual(row["direct_paid_apps"][0].id, self.application.id)

        # 2. Check audit transactions inside shopHistoryModal
        audit_txs = row["transactions"]
        direct_tx = next((t for t in audit_txs if t["is_direct_customer_pay"]), None)
        self.assertIsNotNone(direct_tx)
        self.assertEqual(direct_tx["amount"], Decimal("5000.00"))
        self.assertEqual(direct_tx["type_label"], "Directly Paid by Customer")
        self.assertContains(res, "Directly Paid by Customer")

        # 3. Check financials reflect deduction
        # Total payable = 30000 (app1) + 35000 (app2) = 65000
        # shop_collected_emi = 5000 -> remaining_due = 60000
        self.assertEqual(row["financials"]["total_payable"], Decimal("65000.00"))
        self.assertEqual(row["financials"]["shop_collected_emi"], Decimal("5000.00"))
        self.assertEqual(row["financials"]["remaining_due"], Decimal("60000.00"))

    def test_admin_update_installment_partial_carry_forward(self):
        """
        Tests that when admin sets installment to PARTIAL and selects carry_forward='yes',
        the unpaid balance moves to the next month's installment.
        """
        initial_s1_amt = self.sched1.amount
        initial_s2_amt = self.sched2.amount

        # Sched 1 is 5000, customer pays 3000 (unpaid 2000)
        post_data = {
            "amount": str(initial_s1_amt),
            "amount_paid": "3000.00",
            "status": "PARTIAL",
            "due_date": self.sched1.due_date.strftime("%Y-%m-%d"),
            "update_note": "Customer paid partial 3000",
            "carry_forward": "yes"
        }
        res = self.client.post(
            reverse("admin_update_installment", kwargs={"pk": self.sched1.pk}),
            post_data,
            follow=True
        )
        self.assertEqual(res.status_code, 200)

        self.sched1.refresh_from_db()
        self.sched2.refresh_from_db()

        self.assertEqual(self.sched1.status, CustomerEMISchedule.InstallmentStatus.PARTIAL)
        self.assertEqual(self.sched1.amount_paid, Decimal("3000.00"))
        # Present amount due is preserved (not subtracted)
        self.assertEqual(self.sched1.amount, initial_s1_amt)
        # Unpaid (initial_s1_amt - 3000) was moved to sched2 -> sched2 amount is now initial_s2_amt + unpaid
        expected_unpaid = initial_s1_amt - Decimal("3000.00")
        self.assertEqual(self.sched2.amount, initial_s2_amt + expected_unpaid)
