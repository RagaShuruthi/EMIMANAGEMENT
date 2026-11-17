"""
Tests verifying that the 'Amount Paid by Customers' metric card on the Shop-wise Customers
page and specific shop detail view updates immediately whenever customer payments are updated
inside the customer view's Customer EMI Installments Schedule section.
"""

from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from applications.models import FinanceApplication
from customers.models import Customer
from payments.models import CustomerEMISchedule
from shops.models import Shop
from admin_dashboard.views import get_shop_customer_emi_stats

User = get_user_model()


class ShopwiseCustomerPaidUpdateTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="admin_shopwise_tester",
            email="admin_shopwise@example.com",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
        )
        self.client.login(username="admin_shopwise_tester", password="AdminPassword123!")

        self.shop = Shop.objects.create(
            name="Supreme Mobiles",
            owner_name="Suresh Raina",
            phone="9876543210",
            address="Chennai Main Road",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            shop=self.shop,
            name="Murugan Vel",
            primary_mobile="9840123456",
            secondary_mobile="9840123457",
            work_address="Mount Road, Chennai",
            home_address="Anna Nagar, Chennai",
        )

        self.application = FinanceApplication.objects.create(
            shop=self.shop,
            customer=self.customer,
            mobile_model="OnePlus 12 16/512",
            mobile_price=Decimal("60000.00"),
            down_payment=Decimal("10000.00"),
            financier_request_amount=Decimal("50000.00"),
            total_emi_amount=Decimal("60000.00"),
            monthly_emi=Decimal("10000.00"),
            emi_duration=6,
            monthly_due_day=5,
            status=FinanceApplication.Status.APPROVED,
        )
        self.application.generate_default_schedule_if_missing()

    def test_shopwise_amount_paid_updates_when_installment_schedule_updated_in_customer_view(self):
        """
        Verify that updating amount_paid on installment schedules inside customer view
        immediately updates 'total_amount_paid' on shop customer stats and shopwise pages.
        """
        # Initially, 0 paid
        initial_stats = get_shop_customer_emi_stats(self.shop)
        self.assertEqual(initial_stats["total_amount_paid"], Decimal("0.00"))

        # Retrieve first two schedules
        schedules = list(self.application.emi_schedules.order_by("installment_number"))
        sched1 = schedules[0]
        sched2 = schedules[1]

        # 1. Update installment #1 via admin_update_installment_view (Paid ₹10,000)
        url1 = reverse("admin_update_installment", kwargs={"pk": sched1.pk})
        post_data1 = {
            "amount": str(sched1.amount),
            "amount_paid": "10000.00",
            "due_date": sched1.due_date.strftime("%Y-%m-%d"),
            "status": "PAID",
            "update_note": "Customer paid via cash in store",
        }
        res1 = self.client.post(url1, post_data1)
        self.assertEqual(res1.status_code, 302)

        # Verify shop stats immediately reflected ₹10,000
        stats_after_1 = get_shop_customer_emi_stats(self.shop)
        self.assertEqual(stats_after_1["total_amount_paid"], Decimal("10000.00"))

        # 2. Update installment #2 as partial payment (Paid ₹4,500)
        url2 = reverse("admin_update_installment", kwargs={"pk": sched2.pk})
        post_data2 = {
            "amount": str(sched2.amount),
            "amount_paid": "4500.00",
            "due_date": sched2.due_date.strftime("%Y-%m-%d"),
            "status": "PARTIAL",
            "update_note": "Customer paid partial UPI",
        }
        res2 = self.client.post(url2, post_data2)
        self.assertEqual(res2.status_code, 302)

        # Verify shop stats immediately reflected ₹14,500
        stats_after_2 = get_shop_customer_emi_stats(self.shop)
        self.assertEqual(stats_after_2["total_amount_paid"], Decimal("14500.00"))

        # 3. Verify shopwise detail view renders the updated amount on the metric card
        detail_url = reverse("admin_shopwise_detail", kwargs={"shop_id": self.shop.id})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, "14500.00")
        self.assertContains(res_detail, "Amount Paid by Customers")

        # 4. Verify shopwise customers overview page renders the updated amount
        list_url = reverse("admin_shopwise_customers")
        res_list = self.client.get(list_url)
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, "14500.00")
