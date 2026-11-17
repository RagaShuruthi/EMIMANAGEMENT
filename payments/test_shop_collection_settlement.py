"""
Unit and integration tests for EMI Manager:
Shop collection, loan adjustment, financier settlement, and shop recoveries.
Tests all 7 scenarios specified in requirements.
"""

from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
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
    calculate_application_remaining_loan,
    get_shop_financials,
    get_shop_pending_loan_applications,
    get_shop_total_pending_loan_amount,
    process_shop_collection,
    recalculate_shop_financials,
    record_admin_shop_receipt,
)
from shops.models import Shop

User = get_user_model()


class ShopCollectionAndSettlementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin_tester",
            password="testpassword123",
            email="admin@test.com",
            is_staff=True,
        )
        self.shop = Shop.objects.create(
            name="Apex Mobile Gallery",
            owner_name="Vikram Seth",
            phone="9876543210",
            address="123 Market Road, City",
        )
        self.customer = Customer.objects.create(
            shop=self.shop,
            name="Rahul Sharma",
            primary_mobile="9876500001",
            secondary_mobile="9876500002",
            home_address="Flat 101, Lake View Apts",
            work_address="Tech Park, Block B",
        )

    def test_scenario_1_shop_with_pending_loan(self):
        """
        Test 1 – Shop with pending loan:
        - Pending loan: ₹30,000
        - Shop collection: ₹10,000
        - Expected loan balance: ₹20,000
        - Expected loan adjustment transaction: ₹10,000
        - Amount Disbursed includes ₹10,000 adjustment, but NOT as cash paid.
        """
        app = FinanceApplication.objects.create(
            application_number="APP-TEST-001",
            shop=self.shop,
            customer=self.customer,
            mobile_model="Samsung S24",
            mobile_price=Decimal("35000.00"),
            down_payment=Decimal("5000.00"),
            financier_request_amount=Decimal("30000.00"),
            monthly_emi=Decimal("5000.00"),
            status=FinanceApplication.Status.APPROVED,
        )

        # Verify initial pending loan is ₹30,000
        self.assertEqual(get_shop_total_pending_loan_amount(self.shop), Decimal("30000.00"))

        # Customer pays EMI ₹10,000 directly to Shop
        payment = CustomerEMIPayment.objects.create(
            application=app,
            customer=self.customer,
            amount=Decimal("10000.00"),
            payment_date=timezone.localdate(),
            collected_by_shop=True,
            recorded_by=self.user,
        )

        record = process_shop_collection(payment, user=self.user)
        self.assertIsNotNone(record)
        self.assertEqual(record.adjusted_to_loan, Decimal("10000.00"))
        self.assertEqual(record.collectible_amount, Decimal("0.00"))
        self.assertEqual(record.status, ShopCollectionRecord.CollectionStatus.ADJUSTED)

        # Expected loan balance: ₹20,000
        self.assertEqual(get_shop_total_pending_loan_amount(self.shop), Decimal("20000.00"))
        self.assertEqual(calculate_application_remaining_loan(app), Decimal("20000.00"))

        # Expected loan adjustment transaction: ₹10,000
        txn = ShopFinancialTransaction.objects.filter(
            shop=self.shop,
            transaction_type=ShopFinancialTransaction.TransactionType.LOAN_ADJUSTMENT,
        ).first()
        self.assertIsNotNone(txn)
        self.assertEqual(txn.amount, Decimal("10000.00"))
        self.assertEqual(txn.previous_balance, Decimal("30000.00"))
        self.assertEqual(txn.updated_balance, Decimal("20000.00"))
        self.assertIn("collected by shop and adjusted against financier settlement", txn.remarks)

        # Financials verification
        financials = get_shop_financials(self.shop)
        self.assertEqual(financials["total_payable"], Decimal("30000.00"))
        self.assertEqual(financials["actual_admin_paid"], Decimal("0.00"))  # Not cash paid
        self.assertEqual(financials["shop_collection_adjusted"], Decimal("10000.00"))
        self.assertEqual(financials["amount_disbursed"], Decimal("10000.00"))  # Total Disbursed = Cash + Adj
        self.assertEqual(financials["remaining_due"], Decimal("20000.00"))
        self.assertEqual(financials["shop_collectible_balance"], Decimal("0.00"))

    def test_scenario_2_shop_without_pending_loan(self):
        """
        Test 2 – Shop without pending loan:
        - Pending loan: ₹0
        - Shop collection: ₹10,000
        - Expected collected from shops balance: ₹10,000
        - Expected status: Pending admin receipt
        - Do not deduct from settled loans; do not include in cash paid.
        """
        # Customer has an already-settled loan (full admin payment made previously)
        app = FinanceApplication.objects.create(
            application_number="APP-SETTLED-002",
            shop=self.shop,
            customer=self.customer,
            mobile_model="iPhone 13",
            mobile_price=Decimal("40000.00"),
            down_payment=Decimal("10000.00"),
            financier_request_amount=Decimal("30000.00"),
            monthly_emi=Decimal("5000.00"),
            status=FinanceApplication.Status.PAID,
        )
        FinancierPayment.objects.create(
            shop=self.shop,
            application=app,
            amount_received=Decimal("30000.00"),
            payment_date=timezone.localdate(),
            payment_reference="UTR-SETTLED-002",
        )

        # Shop pending loan is ₹0
        self.assertEqual(get_shop_total_pending_loan_amount(self.shop), Decimal("0.00"))

        # Customer pays EMI ₹10,000 to Shop
        payment = CustomerEMIPayment.objects.create(
            application=app,
            customer=self.customer,
            amount=Decimal("10000.00"),
            payment_date=timezone.localdate(),
            collected_by_shop=True,
            recorded_by=self.user,
        )

        record = process_shop_collection(payment, user=self.user)
        self.assertIsNotNone(record)
        self.assertEqual(record.adjusted_to_loan, Decimal("0.00"))
        self.assertEqual(record.collectible_amount, Decimal("10000.00"))
        self.assertEqual(record.status, ShopCollectionRecord.CollectionStatus.PENDING)

        # Financials check: ₹10,000 in Collected from Shops
        financials = get_shop_financials(self.shop)
        self.assertEqual(financials["shop_collectible_balance"], Decimal("10000.00"))
        self.assertEqual(financials["actual_admin_paid"], Decimal("30000.00"))
        self.assertEqual(financials["shop_collection_adjusted"], Decimal("0.00"))

        # Transaction created under Collected from Shops
        txn = ShopFinancialTransaction.objects.filter(
            shop=self.shop,
            transaction_type=ShopFinancialTransaction.TransactionType.SHOP_COLLECTION,
        ).first()
        self.assertIsNotNone(txn)
        self.assertEqual(txn.amount, Decimal("10000.00"))
        self.assertEqual(txn.status, ShopFinancialTransaction.TransactionStatus.PENDING)

    def test_scenario_3_admin_receives_shop_collection(self):
        """
        Test 3 – Admin receives shop collection:
        - Shop collection pending: ₹10,000
        - Admin acknowledges receipt: ₹10,000 (YES)
        - Expected remaining collectible balance: ₹0
        - Expected status: Received
        """
        app = FinanceApplication.objects.create(
            application_number="APP-SETTLED-003",
            shop=self.shop,
            customer=self.customer,
            mobile_model="iPhone 14",
            mobile_price=Decimal("45000.00"),
            down_payment=Decimal("15000.00"),
            financier_request_amount=Decimal("30000.00"),
            monthly_emi=Decimal("5000.00"),
            status=FinanceApplication.Status.PAID,
        )
        FinancierPayment.objects.create(
            shop=self.shop,
            application=app,
            amount_received=Decimal("30000.00"),
            payment_date=timezone.localdate(),
            payment_reference="UTR-SETTLED-003",
        )

        payment = CustomerEMIPayment.objects.create(
            application=app,
            customer=self.customer,
            amount=Decimal("10000.00"),
            payment_date=timezone.localdate(),
            collected_by_shop=True,
            recorded_by=self.user,
        )
        record = process_shop_collection(payment, user=self.user)

        self.assertEqual(record.remaining_collectible, Decimal("10000.00"))
        self.assertEqual(record.status, ShopCollectionRecord.CollectionStatus.PENDING)

        # Admin clicks YES on Received from Shop
        record_admin_shop_receipt(
            record=record,
            action="yes",
            amount_received=Decimal("10000.00"),
            received_date=timezone.localdate(),
            user=self.user,
            remarks="Full cash handed over at office",
        )

        record.refresh_from_db()
        self.assertEqual(record.remaining_collectible, Decimal("0.00"))
        self.assertEqual(record.amount_received_by_admin, Decimal("10000.00"))
        self.assertEqual(record.status, ShopCollectionRecord.CollectionStatus.RECEIVED)

        # Financials check: collectible balance cleared to 0
        financials = get_shop_financials(self.shop)
        self.assertEqual(financials["shop_collectible_balance"], Decimal("0.00"))

        # Transaction ledger entry check
        receipt_txn = ShopFinancialTransaction.objects.filter(
            shop=self.shop,
            transaction_type=ShopFinancialTransaction.TransactionType.SHOP_RECEIPT,
        ).first()
        self.assertIsNotNone(receipt_txn)
        self.assertEqual(receipt_txn.amount, Decimal("10000.00"))
        self.assertEqual(receipt_txn.updated_balance, Decimal("0.00"))
        self.assertEqual(receipt_txn.status, ShopFinancialTransaction.TransactionStatus.RECEIVED)

    def test_scenario_4_combined_loan_settlement(self):
        """
        Test 4 – Combined loan settlement:
        - Original loan: ₹25,000
        - Admin pays: ₹10,000
        - Shop collection adjusted: ₹10,000
        - Remaining loan: ₹5,000
        - Final admin payment: ₹5,000
        - Loan status must change to Settled (PAID).
        - Pending loan balance becomes ₹0.
        - Application disappears from pending loans list while staying in database.
        """
        app = FinanceApplication.objects.create(
            application_number="APP-COMBINED-001",
            shop=self.shop,
            customer=self.customer,
            mobile_model="OnePlus 12",
            mobile_price=Decimal("30000.00"),
            down_payment=Decimal("5000.00"),
            financier_request_amount=Decimal("25000.00"),
            monthly_emi=Decimal("4167.00"),
            status=FinanceApplication.Status.APPROVED,
        )

        # Step 1: Admin pays ₹10,000 directly to shop
        FinancierPayment.objects.create(
            shop=self.shop,
            application=app,
            amount_received=Decimal("10000.00"),
            payment_date=timezone.localdate(),
            payment_reference="UTR-ADMIN-001",
        )
        self.assertEqual(calculate_application_remaining_loan(app), Decimal("15000.00"))

        # Step 2: Shop collection of ₹10,000 adjusted against loan
        payment = CustomerEMIPayment.objects.create(
            application=app,
            customer=self.customer,
            amount=Decimal("10000.00"),
            payment_date=timezone.localdate(),
            collected_by_shop=True,
            recorded_by=self.user,
        )
        process_shop_collection(payment, user=self.user)

        # Remaining loan: ₹5,000
        self.assertEqual(calculate_application_remaining_loan(app), Decimal("5000.00"))
        self.assertEqual(get_shop_total_pending_loan_amount(self.shop), Decimal("5000.00"))

        pending_apps = get_shop_pending_loan_applications(self.shop)
        self.assertEqual(len(pending_apps), 1)
        self.assertEqual(pending_apps[0].id, app.id)

        # Step 3: Admin pays final ₹5,000
        FinancierPayment.objects.create(
            shop=self.shop,
            application=app,
            amount_received=Decimal("5000.00"),
            payment_date=timezone.localdate(),
            payment_reference="UTR-ADMIN-FINAL",
        )

        # Calculation check
        self.assertEqual(calculate_application_remaining_loan(app), Decimal("0.00"))
        self.assertEqual(get_shop_total_pending_loan_amount(self.shop), Decimal("0.00"))

        # Pending loan list should now automatically mark as PAID and exclude it
        pending_apps_after = get_shop_pending_loan_applications(self.shop)
        self.assertEqual(len(pending_apps_after), 0)

        # Record is preserved in database with status PAID (Settled)
        app.refresh_from_db()
        self.assertEqual(app.status, FinanceApplication.Status.PAID)

        # Financials verification
        financials = get_shop_financials(self.shop)
        self.assertEqual(financials["total_payable"], Decimal("25000.00"))
        self.assertEqual(financials["actual_admin_paid"], Decimal("15000.00"))
        self.assertEqual(financials["shop_collection_adjusted"], Decimal("10000.00"))
        self.assertEqual(financials["amount_disbursed"], Decimal("25000.00"))
        self.assertEqual(financials["remaining_due"], Decimal("0.00"))

    def test_scenario_5_partial_shop_receipt(self):
        """
        Test 5 – Partial shop receipt:
        - Shop collectible balance: ₹10,000
        - Admin receives: ₹4,000
        - Expected remaining collectible balance: ₹6,000
        - Expected status: Partial
        """
        app = FinanceApplication.objects.create(
            application_number="APP-SETTLED-005",
            shop=self.shop,
            customer=self.customer,
            mobile_model="Vivo V30",
            mobile_price=Decimal("40000.00"),
            down_payment=Decimal("10000.00"),
            financier_request_amount=Decimal("30000.00"),
            monthly_emi=Decimal("5000.00"),
            status=FinanceApplication.Status.PAID,
        )
        FinancierPayment.objects.create(
            shop=self.shop,
            application=app,
            amount_received=Decimal("30000.00"),
            payment_date=timezone.localdate(),
            payment_reference="UTR-SETTLED-005",
        )

        payment = CustomerEMIPayment.objects.create(
            application=app,
            customer=self.customer,
            amount=Decimal("10000.00"),
            payment_date=timezone.localdate(),
            collected_by_shop=True,
            recorded_by=self.user,
        )
        record = process_shop_collection(payment, user=self.user)
        self.assertEqual(record.remaining_collectible, Decimal("10000.00"))

        # Admin receives partial amount of ₹4,000
        record_admin_shop_receipt(
            record=record,
            action="yes",
            amount_received=Decimal("4000.00"),
            received_date=timezone.localdate(),
            user=self.user,
            remarks="Partial deposit made by shop",
        )

        record.refresh_from_db()
        self.assertEqual(record.amount_received_by_admin, Decimal("4000.00"))
        self.assertEqual(record.remaining_collectible, Decimal("6000.00"))
        self.assertEqual(record.status, ShopCollectionRecord.CollectionStatus.PARTIAL)

        # Financials check
        financials = get_shop_financials(self.shop)
        self.assertEqual(financials["shop_collectible_balance"], Decimal("6000.00"))

        # Ledger entry check
        partial_txn = ShopFinancialTransaction.objects.filter(
            shop=self.shop,
            transaction_type=ShopFinancialTransaction.TransactionType.PARTIAL_RECEIPT,
        ).first()
        self.assertIsNotNone(partial_txn)
        self.assertEqual(partial_txn.amount, Decimal("4000.00"))
        self.assertEqual(partial_txn.previous_balance, Decimal("10000.00"))
        self.assertEqual(partial_txn.updated_balance, Decimal("6000.00"))

    def test_scenario_6_duplicate_prevention(self):
        """
        Test 6 – Duplicate prevention (Idempotency):
        - Same payment processed twice.
        - Assert single adjustment only.
        - Balances and transaction history must not be duplicated.
        """
        app = FinanceApplication.objects.create(
            application_number="APP-DUP-001",
            shop=self.shop,
            customer=self.customer,
            mobile_model="Vivo V29",
            mobile_price=Decimal("20000.00"),
            down_payment=Decimal("0.00"),
            financier_request_amount=Decimal("20000.00"),
            monthly_emi=Decimal("3333.00"),
            status=FinanceApplication.Status.APPROVED,
        )

        payment = CustomerEMIPayment.objects.create(
            application=app,
            customer=self.customer,
            amount=Decimal("5000.00"),
            payment_date=timezone.localdate(),
            collected_by_shop=True,
            recorded_by=self.user,
        )

        # Call 1
        rec1 = process_shop_collection(payment, user=self.user)
        # Call 2 (Duplicate trigger)
        rec2 = process_shop_collection(payment, user=self.user)

        self.assertEqual(rec1.id, rec2.id)
        self.assertEqual(ShopCollectionRecord.objects.filter(payment=payment).count(), 1)
        self.assertEqual(
            ShopFinancialTransaction.objects.filter(
                shop=self.shop,
                transaction_type=ShopFinancialTransaction.TransactionType.LOAN_ADJUSTMENT,
            ).count(),
            1,
        )

        # Loan balance is deducted exactly once
        self.assertEqual(get_shop_total_pending_loan_amount(self.shop), Decimal("15000.00"))

    def test_scenario_7_edit_and_reversal(self):
        """
        Test 7 – Edit and reversal:
        - Edit customer payment amount.
        - Recalculate shop financials.
        - Assert loan balance, collectible balance, and dashboard metrics update dynamically.
        """
        app = FinanceApplication.objects.create(
            application_number="APP-REVERSAL-001",
            shop=self.shop,
            customer=self.customer,
            mobile_model="Redmi Note 13",
            mobile_price=Decimal("30000.00"),
            down_payment=Decimal("0.00"),
            financier_request_amount=Decimal("30000.00"),
            monthly_emi=Decimal("5000.00"),
            status=FinanceApplication.Status.APPROVED,
        )

        payment = CustomerEMIPayment.objects.create(
            application=app,
            customer=self.customer,
            amount=Decimal("10000.00"),
            payment_date=timezone.localdate(),
            collected_by_shop=True,
            recorded_by=self.user,
        )
        process_shop_collection(payment, user=self.user)

        # Initial check
        self.assertEqual(get_shop_total_pending_loan_amount(self.shop), Decimal("20000.00"))

        # Payment was mistakenly recorded as ₹10,000, actual payment was ₹6,000. Admin edits it.
        payment.amount = Decimal("6000.00")
        payment.save()

        # Recalculate shop financials
        recalculate_shop_financials(self.shop)

        # Balances must reflect ₹6,000 adjustment, meaning pending loan is ₹24,000
        self.assertEqual(get_shop_total_pending_loan_amount(self.shop), Decimal("24000.00"))
        financials = get_shop_financials(self.shop)
        self.assertEqual(financials["shop_collection_adjusted"], Decimal("6000.00"))
        self.assertEqual(financials["amount_disbursed"], Decimal("6000.00"))
        self.assertEqual(financials["remaining_due"], Decimal("24000.00"))

        # Reversal test: Payment is deleted / un-flagged from collected_by_shop
        payment.collected_by_shop = False
        payment.save()
        recalculate_shop_financials(self.shop)

        # Balances must restore to original ₹30,000 pending loan
        self.assertEqual(get_shop_total_pending_loan_amount(self.shop), Decimal("30000.00"))
        fin_after = get_shop_financials(self.shop)
        self.assertEqual(fin_after["shop_collection_adjusted"], Decimal("0.00"))
        self.assertEqual(fin_after["amount_disbursed"], Decimal("0.00"))
        self.assertEqual(fin_after["remaining_due"], Decimal("30000.00"))
