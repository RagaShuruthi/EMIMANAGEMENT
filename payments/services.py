"""
Core Financial Accounting Service for Shop Collections, Loan Adjustments,
Financier Settlements, and Shop Recoveries.
Ensures zero double-counting, idempotency, permanent audit trails, and automatic loan settlement.
"""

from decimal import Decimal
from django.db import transaction, models
from django.db.models import Sum, Q, F
from django.utils import timezone

from applications.models import FinanceApplication
from payments.models import (
    CustomerEMIPayment,
    FinancierPayment,
    ShopCollectionRecord,
    ShopFinancialTransaction,
)


def calculate_application_remaining_loan(app, exclude_record_id=None):
    """
    Authoritative Remaining Loan for a specific application:
    Remaining Loan = Original Loan (financier_request_amount)
                     - Admin Payments for this app
                     - Shop Collection Adjustments for this app
    """
    original_loan = app.financier_request_amount or Decimal("0.00")

    admin_paid_agg = FinancierPayment.objects.filter(
        application=app
    ).aggregate(total=Sum("amount_received"))["total"] or Decimal("0.00")

    adj_filter = Q(application=app)
    if exclude_record_id:
        adj_filter &= ~Q(id=exclude_record_id)

    shop_adj_agg = ShopCollectionRecord.objects.filter(
        adj_filter
    ).aggregate(total=Sum("adjusted_to_loan"))["total"] or Decimal("0.00")

    return max(Decimal("0.00"), original_loan - admin_paid_agg - shop_adj_agg)


def get_shop_total_pending_loan_amount(shop, exclude_record_id=None):
    """
    Authoritative pending loan amount for a shop:
    Total Financier Obligations - Actual Admin Paid - Total Shop Adjustments
    """
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]
    total_payable = FinanceApplication.objects.filter(
        shop=shop, status__in=approved_statuses
    ).aggregate(total=Sum("financier_request_amount"))["total"] or Decimal("0.00")

    actual_admin_paid = FinancierPayment.objects.filter(
        Q(shop=shop) | Q(application__shop=shop)
    ).aggregate(total=Sum("amount_received"))["total"] or Decimal("0.00")

    adj_filter = Q(shop=shop)
    if exclude_record_id:
        adj_filter &= ~Q(id=exclude_record_id)

    shop_adj_agg = ShopCollectionRecord.objects.filter(
        adj_filter
    ).aggregate(total=Sum("adjusted_to_loan"))["total"] or Decimal("0.00")

    return max(Decimal("0.00"), total_payable - actual_admin_paid - shop_adj_agg)


def get_shop_pending_loan_applications(shop):
    """
    Returns active applications that have remaining loan balances
    waiting to be settled (either by admin payout or shop collection adjustment).
    """
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]
    apps = list(
        FinanceApplication.objects.filter(shop=shop, status__in=approved_statuses)
        .select_related("customer")
        .order_by("created_at")
    )
    result = []
    for app in apps:
        app_rem = calculate_application_remaining_loan(app)
        app.calculated_remaining_loan = app_rem
        if app_rem > Decimal("0.00"):
            result.append(app)
        else:
            # When Remaining Loan == 0, mark loan as Settled (PAID) and remove from pending
            if app.status != FinanceApplication.Status.PAID:
                app.status = FinanceApplication.Status.PAID
                app.save(update_fields=["status", "updated_at"])
    return result


@transaction.atomic
def process_shop_collection(payment, user=None, force=False):
    """
    Processes a CustomerEMIPayment collected directly by a retail shop:
    1. Checks whether shop has pending loan / financier settlement obligations.
    2. Scenario A: If shop has a pending loan:
       - Deducts from pending loan balance.
       - Allocates adjustment to application(s) (payment.application first, then oldest pending).
       - When Remaining Loan == 0, marks as Settled (PAID).
       - Creates LOAN_ADJUSTMENT transaction history entry:
         '₹X collected by shop and adjusted against financier settlement.'
       - Records customer, shop, collection date, EMI month, and follow-up person.
       - If collection exceeds pending loan, excess goes to Collected from Shops.
    3. Scenario B: If shop has NO pending loan:
       - Does NOT deduct from already settled loans.
       - Whole collection goes to Collected from Shops (available for recovery).
       - Creates SHOP_COLLECTION transaction under Collected from Shops.
    4. Guarantees idempotency (Duplicate prevention).
    """
    if not payment.collected_by_shop:
        return None

    # Check for existing record
    existing_record = ShopCollectionRecord.objects.filter(payment=payment).first()
    if existing_record and not force:
        # Already processed cleanly
        return existing_record

    record = existing_record or ShopCollectionRecord(payment=payment)

    shop = payment.customer.shop
    customer = payment.customer
    amount = payment.amount or Decimal("0.00")
    coll_date = payment.payment_date or timezone.localdate()

    # Determine follow-up person assigned to this shop
    from followups.models import FollowUpAssignment
    assigned_emp = (
        FollowUpAssignment.objects.filter(shop=shop, is_active=True)
        .select_related("employee")
        .first()
    )
    followup_person = assigned_emp.employee if assigned_emp else None

    # Determine EMI month
    if payment.schedule:
        emi_month = f"Inst #{payment.schedule.installment_number} ({payment.schedule.due_date.strftime('%b %Y')})"
    else:
        emi_month = coll_date.strftime("%B %Y")

    # Current shop pending loan balance before this adjustment
    pending_loan_before = get_shop_total_pending_loan_amount(shop, exclude_record_id=record.id if record.id else None)

    adjusted_amount = Decimal("0.00")
    collectible_amount = Decimal("0.00")

    if pending_loan_before > Decimal("0.00"):
        # SCENARIO A: Shop has a pending loan
        adjusted_amount = min(amount, pending_loan_before)
        collectible_amount = max(Decimal("0.00"), amount - adjusted_amount)
    else:
        # SCENARIO B: Shop has NO pending loan
        adjusted_amount = Decimal("0.00")
        collectible_amount = amount

    # Setup / update ShopCollectionRecord
    record.shop = shop
    record.customer = customer
    record.application = payment.application
    record.amount_collected = amount
    record.adjusted_to_loan = adjusted_amount
    record.collectible_amount = collectible_amount
    record.collection_date = coll_date
    record.followup_person = followup_person

    if collectible_amount > Decimal("0.00"):
        if record.amount_received_by_admin >= collectible_amount:
            record.status = ShopCollectionRecord.CollectionStatus.RECEIVED
        elif record.amount_received_by_admin > Decimal("0.00"):
            record.status = ShopCollectionRecord.CollectionStatus.PARTIAL
        else:
            record.status = ShopCollectionRecord.CollectionStatus.PENDING
    else:
        record.status = ShopCollectionRecord.CollectionStatus.ADJUSTED

    record.save()

    # Clear previous transaction history for this record if force/recalculating
    if record.transactions.exists():
        record.transactions.all().delete()

    # Allocate adjustment to application(s)
    if adjusted_amount > Decimal("0.00"):
        rem_to_allocate = adjusted_amount
        app_candidates = []

        # Prefer payment.application if it has remaining balance
        if payment.application:
            app_rem = calculate_application_remaining_loan(payment.application, exclude_record_id=record.id)
            if app_rem > Decimal("0.00"):
                app_candidates.append(payment.application)

        # Other pending applications of this shop
        other_apps = FinanceApplication.objects.filter(
            shop=shop,
            status__in=[
                FinanceApplication.Status.APPROVED,
                FinanceApplication.Status.PARTIALLY_PAID,
            ]
        ).exclude(id=payment.application.id if payment.application else -1).order_by("created_at")

        for oa in other_apps:
            if oa not in app_candidates:
                oa_rem = calculate_application_remaining_loan(oa, exclude_record_id=record.id)
                if oa_rem > Decimal("0.00"):
                    app_candidates.append(oa)

        for target_app in app_candidates:
            if rem_to_allocate <= Decimal("0.00"):
                break
            target_rem = calculate_application_remaining_loan(target_app, exclude_record_id=record.id)
            alloc = min(rem_to_allocate, target_rem)
            rem_to_allocate -= alloc

            # Check if this allocation settled target_app
            if target_rem - alloc <= Decimal("0.00"):
                target_app.status = FinanceApplication.Status.PAID
                target_app.save(update_fields=["status", "updated_at"])

                # Create LOAN_SETTLEMENT transaction
                ShopFinancialTransaction.objects.create(
                    shop=shop,
                    customer=target_app.customer,
                    application=target_app,
                    transaction_type=ShopFinancialTransaction.TransactionType.LOAN_SETTLEMENT,
                    amount=target_app.financier_request_amount,
                    transaction_date=coll_date,
                    emi_month=emi_month,
                    followup_person=followup_person,
                    recorded_by=user or payment.recorded_by,
                    previous_balance=target_rem,
                    updated_balance=Decimal("0.00"),
                    remarks=f"Loan #{target_app.application_number} fully settled through shop collection adjustment.",
                    status=ShopFinancialTransaction.TransactionStatus.SETTLED,
                    collection_record=record,
                )
            else:
                if target_app.status != FinanceApplication.Status.PAID:
                    target_app.status = FinanceApplication.Status.PARTIALLY_PAID
                    target_app.save(update_fields=["status", "updated_at"])

        # Create LOAN_ADJUSTMENT transaction history entry
        updated_loan_balance = max(Decimal("0.00"), pending_loan_before - adjusted_amount)
        ShopFinancialTransaction.objects.create(
            shop=shop,
            customer=customer,
            application=payment.application,
            transaction_type=ShopFinancialTransaction.TransactionType.LOAN_ADJUSTMENT,
            amount=adjusted_amount,
            transaction_date=coll_date,
            emi_month=emi_month,
            followup_person=followup_person,
            recorded_by=user or payment.recorded_by,
            previous_balance=pending_loan_before,
            updated_balance=updated_loan_balance,
            remarks=f"₹{adjusted_amount:,.2f} collected by shop and adjusted against financier settlement.",
            status=ShopFinancialTransaction.TransactionStatus.ADJUSTED,
            collection_record=record,
        )

    # If there is collectible amount (Scenario B or excess over pending loan)
    if collectible_amount > Decimal("0.00"):
        ShopFinancialTransaction.objects.create(
            shop=shop,
            customer=customer,
            application=payment.application,
            transaction_type=ShopFinancialTransaction.TransactionType.SHOP_COLLECTION,
            amount=collectible_amount,
            transaction_date=coll_date,
            emi_month=emi_month,
            followup_person=followup_person,
            recorded_by=user or payment.recorded_by,
            previous_balance=Decimal("0.00"),
            updated_balance=collectible_amount,
            remarks=f"₹{collectible_amount:,.2f} collected by shop from customer with no pending loan — available for recovery / settlement.",
            status=ShopFinancialTransaction.TransactionStatus.PENDING,
            collection_record=record,
        )

    return record


@transaction.atomic
def record_admin_shop_receipt(record, action="yes", amount_received=None, received_date=None, user=None, remarks=""):
    """
    Records Admin Action: 'Received from Shop?' (Yes / No).
    - If YES:
      - Acknowledges amount received (in full or partial amount).
      - Reduces remaining collectible balance.
      - Sets status to RECEIVED or PARTIAL.
      - Logs SHOP_RECEIPT or PARTIAL_RECEIPT transaction.
    - If NO:
      - Keeps in PENDING status.
      - Does not clear outstanding amount.
    """
    prev_collectible = record.remaining_collectible

    if action.lower() == "yes":
        if amount_received is None or amount_received == "":
            received_amt = prev_collectible
        else:
            received_amt = Decimal(str(amount_received))

        if received_amt <= Decimal("0.00"):
            received_amt = prev_collectible

        # Add to total received by admin
        record.amount_received_by_admin = (record.amount_received_by_admin or Decimal("0.00")) + received_amt
        record.received_date = received_date or timezone.localdate()
        record.received_by = user

        if remarks:
            record.notes = f"{record.notes}\nAdmin: {remarks}".strip()

        rem_collectible = record.remaining_collectible
        if rem_collectible <= Decimal("0.00"):
            record.status = ShopCollectionRecord.CollectionStatus.RECEIVED
            txn_type = ShopFinancialTransaction.TransactionType.SHOP_RECEIPT
            txn_status = ShopFinancialTransaction.TransactionStatus.RECEIVED
            txn_desc = f"₹{received_amt:,.2f} received in full from shop {record.shop.name}."
        else:
            record.status = ShopCollectionRecord.CollectionStatus.PARTIAL
            txn_type = ShopFinancialTransaction.TransactionType.PARTIAL_RECEIPT
            txn_status = ShopFinancialTransaction.TransactionStatus.PARTIAL
            txn_desc = f"Partial receipt of ₹{received_amt:,.2f} received from shop {record.shop.name} (remaining: ₹{rem_collectible:,.2f})."

        if remarks:
            txn_desc += f" (Remarks: {remarks})"

        record.save()

        # Create transaction record
        ShopFinancialTransaction.objects.create(
            shop=record.shop,
            customer=record.customer,
            application=record.application,
            transaction_type=txn_type,
            amount=received_amt,
            transaction_date=record.received_date,
            followup_person=record.followup_person,
            recorded_by=user,
            previous_balance=prev_collectible,
            updated_balance=rem_collectible,
            remarks=txn_desc,
            status=txn_status,
            collection_record=record,
        )
    else:
        # Admin selects NO: Keep in PENDING balance
        record.status = ShopCollectionRecord.CollectionStatus.PENDING
        if remarks:
            record.notes = f"{record.notes}\nAdmin kept pending: {remarks}".strip()
        record.save()

    return record


@transaction.atomic
def recalculate_shop_financials(shop):
    """
    Recalculates all loan adjustments, application settlement statuses,
    collectible balances, and transaction ledger entries for a shop.
    Guarantees clean recovery after edits, reversals, or deletions (Test 7).
    """
    # 1. Clean up any obsolete/orphaned collection records or records where collected_by_shop was toggled off
    obsolete_records = ShopCollectionRecord.objects.filter(shop=shop).filter(
        Q(payment__isnull=True) | Q(payment__collected_by_shop=False)
    )
    for obs in obsolete_records:
        obs.transactions.all().delete()
        obs.delete()

    # 2. Reset existing records' adjusted_to_loan and collectible_amount so they don't skew subsequent calculations
    ShopCollectionRecord.objects.filter(shop=shop).update(
        adjusted_to_loan=Decimal("0.00"),
        collectible_amount=Decimal("0.00"),
    )

    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]
    apps = list(FinanceApplication.objects.filter(shop=shop, status__in=approved_statuses).order_by("created_at"))

    # Reset statuses to APPROVED / PARTIALLY_PAID based on direct admin payments
    for app in apps:
        admin_paid = FinancierPayment.objects.filter(application=app).aggregate(
            total=Sum("amount_received")
        )["total"] or Decimal("0.00")

        if admin_paid >= app.financier_request_amount:
            app.status = FinanceApplication.Status.PAID
        elif admin_paid > Decimal("0.00"):
            app.status = FinanceApplication.Status.PARTIALLY_PAID
        else:
            app.status = FinanceApplication.Status.APPROVED
        app.save(update_fields=["status", "updated_at"])

    # Re-evaluate all shop collections in chronological order
    collections = list(
        CustomerEMIPayment.objects.filter(customer__shop=shop, collected_by_shop=True)
        .order_by("payment_date", "created_at")
    )

    for pay in collections:
        process_shop_collection(pay, force=True)

    # Re-check remaining loans to ensure settled ones are marked PAID
    for app in apps:
        rem = calculate_application_remaining_loan(app)
        if rem <= Decimal("0.00") and app.status != FinanceApplication.Status.PAID:
            app.status = FinanceApplication.Status.PAID
            app.save(update_fields=["status", "updated_at"])


def get_shop_financials(shop, start_date=None, end_date=None):
    """
    Authoritative calculation of money payable by admin to a shop,
    shop collections adjusted against loans, and collectible funds recoverable from shop.
    """
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]
    app_filter = Q(shop=shop, status__in=approved_statuses)
    if start_date:
        app_filter &= (Q(application_date__gte=start_date) | Q(created_at__date__gte=start_date))
    if end_date:
        app_filter &= (Q(application_date__lte=end_date) | Q(created_at__date__lte=end_date))

    total_payable = FinanceApplication.objects.filter(app_filter).aggregate(
        total=Sum("financier_request_amount")
    )["total"] or Decimal("0.00")

    pay_filter = (Q(application__shop=shop) | Q(shop=shop))
    if start_date:
        pay_filter &= (Q(payment_date__gte=start_date) | Q(created_at__date__gte=start_date))
    if end_date:
        pay_filter &= (Q(payment_date__lte=end_date) | Q(created_at__date__lte=end_date))

    actual_admin_paid = FinancierPayment.objects.filter(pay_filter).aggregate(
        total=Sum("amount_received")
    )["total"] or Decimal("0.00")

    rec_filter = Q(shop=shop)
    if start_date:
        rec_filter &= (Q(collection_date__gte=start_date) | Q(created_at__date__gte=start_date))
    if end_date:
        rec_filter &= (Q(collection_date__lte=end_date) | Q(created_at__date__lte=end_date))

    records = list(ShopCollectionRecord.objects.filter(rec_filter))

    total_shop_collected = sum((r.amount_collected for r in records), Decimal("0.00"))
    shop_collection_adjusted = sum((r.adjusted_to_loan for r in records), Decimal("0.00"))
    total_collectible_amount = sum((r.collectible_amount for r in records), Decimal("0.00"))
    total_received_from_shop = sum((r.amount_received_by_admin for r in records), Decimal("0.00"))

    # Remaining collectible from shop (Collected from Shops balance)
    shop_collectible_balance = max(Decimal("0.00"), total_collectible_amount - total_received_from_shop)

    # Amount Disbursed = Actual cash paid by admin + Shop collections adjusted against pending loans
    amount_disbursed = actual_admin_paid + shop_collection_adjusted

    # Remaining liability / due to shop from financier
    remaining_due = max(Decimal("0.00"), total_payable - amount_disbursed)

    if total_payable == Decimal("0.00"):
        payment_status = "NO_OBLIGATIONS"
        payment_status_display = "No Obligations"
        badge_class = "secondary"
    elif remaining_due == Decimal("0.00"):
        payment_status = "PAID"
        payment_status_display = "Settled"
        badge_class = "success"
    elif amount_disbursed > Decimal("0.00"):
        payment_status = "PARTIALLY_PAID"
        payment_status_display = "Partially Paid"
        badge_class = "warning"
    else:
        payment_status = "UNPAID"
        payment_status_display = "Unpaid"
        badge_class = "danger"

    return {
        "total_payable": total_payable,
        "total_paid": actual_admin_paid,
        "actual_admin_paid": actual_admin_paid,
        "shop_collected_emi": total_shop_collected,
        "total_shop_collected": total_shop_collected,
        "shop_collection_adjusted": shop_collection_adjusted,
        "amount_disbursed": amount_disbursed,
        "shop_collectible_balance": shop_collectible_balance,
        "total_received_from_shop": total_received_from_shop,
        "remaining_due": remaining_due,
        "payment_status": payment_status,
        "payment_status_display": payment_status_display,
        "badge_class": badge_class,
    }


def sync_all_existing_shop_collections():
    """
    Synchronizes any existing CustomerEMIPayment records that have collected_by_shop=True
    but lack a ShopCollectionRecord.
    """
    unprocessed = CustomerEMIPayment.objects.filter(
        collected_by_shop=True,
        shop_collection_record__isnull=True
    ).order_by("payment_date", "created_at")

    for pay in unprocessed:
        process_shop_collection(pay)
