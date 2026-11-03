"""
Comprehensive Views for Admin Dashboard and Integrated System Administration.
Integrates directly with the existing Shop Dashboard, Customer Registry,
Finance Applications, and Financier Payments.
"""

from decimal import Decimal
import re
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from shops.models import Shop
from customers.models import Customer
from applications.models import FinanceApplication, ApplicationHistory
from payments.models import FinancierPayment

User = get_user_model()


# ==========================================
# Helpers & Financial Calculations
# ==========================================

def admin_required(view_func):
    """Decorator ensuring administrator privileges."""
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")
        if not (request.user.is_admin_role or request.user.is_superuser):
            return HttpResponseForbidden("Access Denied: Administrator privileges required.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def get_shop_financials(shop, start_date=None, end_date=None):
    """
    Authoritative calculation of money payable by admin to a shop:
    - Total Payable = Sum of financier_request_amount across applications of this shop
    - Amount Paid = Sum of confirmed FinancierPayment amounts received by this shop (direct or application-linked)
    - Remaining Due = Total Payable - Amount Paid
    Optional start_date and end_date filter by application / payment obligation date.
    """
    app_filter = Q(shop=shop)
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

    total_paid = FinancierPayment.objects.filter(pay_filter).aggregate(
        total=Sum("amount_received")
    )["total"] or Decimal("0.00")

    remaining_due = max(Decimal("0.00"), total_payable - total_paid)

    if total_payable == Decimal("0.00"):
        payment_status = "NO_OBLIGATIONS"
        payment_status_display = "No Obligations"
        badge_class = "secondary"
    elif remaining_due == Decimal("0.00"):
        payment_status = "PAID"
        payment_status_display = "Paid"
        badge_class = "success"
    elif total_paid > Decimal("0.00"):
        payment_status = "PARTIALLY_PAID"
        payment_status_display = "Partially Paid"
        badge_class = "warning"
    else:
        payment_status = "UNPAID"
        payment_status_display = "Unpaid"
        badge_class = "danger"

    return {
        "total_payable": total_payable,
        "total_paid": total_paid,
        "remaining_due": remaining_due,
        "payment_status": payment_status,
        "payment_status_display": payment_status_display,
        "badge_class": badge_class,
    }


def get_next_due_date(monthly_due_day):
    """Calculates next calendar due date for a monthly due day (5, 10, 15, 20, 25, 30)."""
    if not monthly_due_day:
        return None
    today = timezone.localdate()
    try:
        current_month_due = today.replace(day=int(monthly_due_day))
        if current_month_due >= today:
            return current_month_due
    except ValueError:
        pass

    if today.month == 12:
        next_month = 1
        next_year = today.year + 1
    else:
        next_month = today.month + 1
        next_year = today.year

    try:
        return today.replace(year=next_year, month=next_month, day=int(monthly_due_day))
    except ValueError:
        return today.replace(year=next_year, month=next_month, day=28)


# ==========================================
# 1. Admin Dashboard Overview
# ==========================================

@login_required
@admin_required
def admin_dashboard_view(request):
    """
    Main Administrator Dashboard Overview.
    Aggregates live metrics from the underlying database models.
    URL: /admin-dashboard/
    """
    total_shops = Shop.objects.count()
    active_shops = Shop.objects.filter(is_active=True).count()
    inactive_shops = Shop.objects.filter(is_active=False).count()

    total_customers = Customer.objects.count()
    total_applications = FinanceApplication.objects.count()

    # Financial Aggregates (Money Admin Owes to Shops)
    total_payable_agg = FinanceApplication.objects.aggregate(
        total=Sum("financier_request_amount")
    )["total"] or Decimal("0.00")

    total_paid_agg = FinancierPayment.objects.aggregate(
        total=Sum("amount_received")
    )["total"] or Decimal("0.00")

    total_remaining_due = max(Decimal("0.00"), total_payable_agg - total_paid_agg)

    # Follow-up Staff Metrics
    followup_base = User.objects.filter(role__in=[User.Role.FOLLOWUP, User.Role.EMPLOYEE])
    total_followups = followup_base.count()
    active_followups = followup_base.filter(is_active=True).count()

    # Application Status Counts
    pending_approvals = FinanceApplication.objects.filter(
        status__in=[
            FinanceApplication.Status.SUBMITTED,
            FinanceApplication.Status.UNDER_REVIEW,
            FinanceApplication.Status.READY
        ]
    ).count()

    # Recent Shops with Live Financials
    recent_shops = list(Shop.objects.select_related("owner_account").order_by("-created_at")[:5])
    for s in recent_shops:
        fin = get_shop_financials(s)
        s.financials = fin
        s.customer_count = s.customers.count()

    # Recent Applications / Customers
    recent_applications = (
        FinanceApplication.objects.select_related("customer", "shop")
        .order_by("-created_at")[:6]
    )

    # Recent Follow-up Employees
    recent_followups = followup_base.prefetch_related("followup_assignments__shop").order_by("-date_joined")[:5]

    context = {
        "active_page": "dashboard",
        "total_shops": total_shops,
        "active_shops": active_shops,
        "inactive_shops": inactive_shops,
        "total_customers": total_customers,
        "total_applications": total_applications,
        "total_payable": total_payable_agg,
        "total_paid": total_paid_agg,
        "total_remaining_due": total_remaining_due,
        "total_followups": total_followups,
        "active_followups": active_followups,
        "pending_approvals": pending_approvals,
        "recent_shops": recent_shops,
        "recent_applications": recent_applications,
        "recent_followups": recent_followups,
    }
    return render(request, "admin_dashboard/dashboard.html", context)


# ==========================================
# 2. All Customers Page
# ==========================================

@login_required
@admin_required
def admin_all_customers_view(request):
    """
    Displays customers registered across all shops with full EMI and status details.
    URL: /admin-dashboard/customers/
    """
    search_query = request.GET.get("q", "").strip()
    shop_filter = request.GET.get("shop_id", "").strip()
    status_filter = request.GET.get("status", "").strip()
    payment_status_filter = request.GET.get("payment_status", "").strip()

    customers_qs = Customer.objects.select_related("shop").prefetch_related(
        "applications", "applications__financier_payments"
    ).order_by("-created_at")

    if search_query:
        customers_qs = customers_qs.filter(
            Q(name__icontains=search_query) |
            Q(primary_mobile__icontains=search_query) |
            Q(secondary_mobile__icontains=search_query) |
            Q(home_address__icontains=search_query) |
            Q(work_address__icontains=search_query) |
            Q(applications__mobile_model__icontains=search_query) |
            Q(applications__application_number__icontains=search_query)
        ).distinct()

    if shop_filter:
        customers_qs = customers_qs.filter(shop_id=shop_filter)

    if status_filter:
        customers_qs = customers_qs.filter(applications__status=status_filter).distinct()

    # Wrap customer objects with latest EMI application details
    customers_list = []
    for c in customers_qs:
        app = c.applications.first()
        customer_item = {
            "customer": c,
            "shop": c.shop,
            "application": app,
            "has_app": bool(app),
        }
        if app:
            customer_item["mobile_model"] = app.mobile_model
            customer_item["mobile_price"] = app.mobile_price
            customer_item["down_payment"] = app.down_payment
            customer_item["financier_request_amount"] = app.financier_request_amount
            customer_item["monthly_emi"] = app.monthly_emi
            customer_item["emi_duration"] = app.emi_duration
            customer_item["total_installments"] = app.emi_duration
            customer_item["paid_installments"] = 1 if app.status == FinanceApplication.Status.PAID else 0
            customer_item["outstanding_balance"] = app.outstanding_amount
            customer_item["status"] = app.status
            customer_item["status_display"] = app.get_status_display()
            customer_item["monthly_due_day"] = app.monthly_due_day
            customer_item["next_due_date"] = get_next_due_date(app.monthly_due_day)
            customer_item["payment_status"] = "Paid" if app.outstanding_amount == Decimal("0.00") else ("Partially Paid" if app.total_payments_received > 0 else "Pending")
        else:
            customer_item["mobile_model"] = "—"
            customer_item["mobile_price"] = Decimal("0.00")
            customer_item["down_payment"] = Decimal("0.00")
            customer_item["financier_request_amount"] = Decimal("0.00")
            customer_item["monthly_emi"] = Decimal("0.00")
            customer_item["emi_duration"] = 0
            customer_item["total_installments"] = 0
            customer_item["paid_installments"] = 0
            customer_item["outstanding_balance"] = Decimal("0.00")
            customer_item["status"] = "NONE"
            customer_item["status_display"] = "No Application"
            customer_item["monthly_due_day"] = "—"
            customer_item["next_due_date"] = None
            customer_item["payment_status"] = "N/A"

        if payment_status_filter:
            if payment_status_filter.lower() == "paid" and customer_item["payment_status"] != "Paid":
                continue
            if payment_status_filter.lower() == "pending" and customer_item["payment_status"] != "Pending":
                continue
            if payment_status_filter.lower() == "partially_paid" and customer_item["payment_status"] != "Partially Paid":
                continue

        customers_list.append(customer_item)

    paginator = Paginator(customers_list, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    all_shops = Shop.objects.all().order_by("name")

    context = {
        "active_page": "customers",
        "customers": page_obj,
        "all_shops": all_shops,
        "search_query": search_query,
        "shop_filter": shop_filter,
        "status_filter": status_filter,
        "payment_status_filter": payment_status_filter,
        "total_count": len(customers_list),
        "status_choices": FinanceApplication.Status.choices,
    }
    return render(request, "admin_dashboard/all_customers.html", context)


# ==========================================
# 3. Customer Detail & Application Approval
# ==========================================

@login_required
@admin_required
def admin_customer_detail_view(request, pk):
    """
    Detailed inspector for a customer, their applications, documents, and EMI history.
    URL: /admin-dashboard/customers/<int:pk>/
    """
    customer = get_object_or_404(Customer.objects.select_related("shop"), pk=pk)
    applications = customer.applications.prefetch_related(
        "documents", "history", "financier_payments"
    ).order_by("-created_at")

    app_details = []
    for app in applications:
        app_details.append({
            "app": app,
            "next_due_date": get_next_due_date(app.monthly_due_day),
            "documents": app.documents.all(),
            "history": app.history.select_related("actor").order_by("-timestamp"),
            "payments": app.financier_payments.all().order_by("-payment_date"),
        })

    context = {
        "active_page": "customers",
        "customer": customer,
        "shop": customer.shop,
        "app_details": app_details,
    }
    return render(request, "admin_dashboard/customer_detail.html", context)


@login_required
@admin_required
def admin_applications_list_view(request):
    """
    Dedicated Applications review page with strictly three status filters:
    - Pending (SUBMITTED, UNDER_REVIEW, READY, DRAFT)
    - Approved (APPROVED, PAID, PARTIALLY_PAID)
    - Rejected (REJECTED)
    URL: /admin-dashboard/applications/
    """
    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all").strip().lower()

    # Base queryset
    apps_qs = FinanceApplication.objects.select_related(
        "customer", "shop"
    ).prefetch_related("documents", "history").order_by("-created_at")

    # Overall counts across entire database
    total_count = apps_qs.count()
    pending_count = apps_qs.filter(
        status__in=[
            FinanceApplication.Status.SUBMITTED,
            FinanceApplication.Status.UNDER_REVIEW,
            FinanceApplication.Status.READY,
            FinanceApplication.Status.DRAFT,
        ]
    ).count()
    approved_count = apps_qs.filter(
        status__in=[
            FinanceApplication.Status.APPROVED,
            FinanceApplication.Status.PAID,
            FinanceApplication.Status.PARTIALLY_PAID,
        ]
    ).count()
    rejected_count = apps_qs.filter(status=FinanceApplication.Status.REJECTED).count()

    # Search filter
    if search_query:
        apps_qs = apps_qs.filter(
            Q(application_number__icontains=search_query) |
            Q(customer__name__icontains=search_query) |
            Q(customer__primary_mobile__icontains=search_query) |
            Q(shop__name__icontains=search_query) |
            Q(mobile_model__icontains=search_query)
        )

    # Strictly 3 status filters as requested: Pending, Approved, Rejected
    if status_filter == "pending":
        apps_qs = apps_qs.filter(
            status__in=[
                FinanceApplication.Status.SUBMITTED,
                FinanceApplication.Status.UNDER_REVIEW,
                FinanceApplication.Status.READY,
                FinanceApplication.Status.DRAFT,
            ]
        )
    elif status_filter == "approved":
        apps_qs = apps_qs.filter(
            status__in=[
                FinanceApplication.Status.APPROVED,
                FinanceApplication.Status.PAID,
                FinanceApplication.Status.PARTIALLY_PAID,
            ]
        )
    elif status_filter == "rejected":
        apps_qs = apps_qs.filter(status=FinanceApplication.Status.REJECTED)
    else:
        status_filter = "all"

    paginator = Paginator(apps_qs, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "active_page": "applications",
        "applications": page_obj,
        "search_query": search_query,
        "status_filter": status_filter,
        "total_count": total_count,
        "pending_count": pending_count,
        "approved_count": approved_count,
        "rejected_count": rejected_count,
    }
    return render(request, "admin_dashboard/applications_list.html", context)


@require_POST
@login_required
@admin_required
def admin_application_approval_view(request, pk):
    """
    Handles administrator approval or rejection of a finance application.
    URL: /admin-dashboard/customers/applications/<int:pk>/approval/
    """
    application = get_object_or_404(FinanceApplication, pk=pk)
    decision = request.POST.get("decision", "").strip().upper()
    remarks = request.POST.get("remarks", "").strip()
    next_url = request.POST.get("next", "").strip()

    valid_decisions = ("APPROVED", "REJECTED", "DECLINED", "PENDING", "UNDER_REVIEW")
    if decision not in valid_decisions:
        messages.error(request, "Invalid approval decision specified.")
        if next_url:
            return redirect(next_url)
        return redirect("admin_applications_list")

    old_status = application.status
    if decision == "APPROVED":
        application.status = FinanceApplication.Status.APPROVED
        action_str = "approved"
    elif decision in ("REJECTED", "DECLINED"):
        application.status = FinanceApplication.Status.REJECTED
        action_str = "declined"
    else:  # PENDING or UNDER_REVIEW
        application.status = FinanceApplication.Status.UNDER_REVIEW
        action_str = "kept as pending"

    application.remarks = remarks
    application.save(update_fields=["status", "remarks", "updated_at"])

    # Record in audit history
    ApplicationHistory.objects.create(
        application=application,
        previous_status=old_status,
        new_status=application.status,
        actor=request.user,
        notes=remarks or f"Application {action_str} by Administrator {request.user.username}."
    )

    messages.success(
        request,
        f"Application {application.application_number} for {application.customer.name} has been {action_str}."
    )
    if next_url:
        return redirect(next_url)
    return redirect("admin_applications_list")


# ==========================================
# 4. Shop-wise Customers Page
# ==========================================

@login_required
@admin_required
def admin_shopwise_customers_view(request):
    """
    Dedicated Shop-wise Customers overview.
    Displays a list of shops with their customer counts, loan volumes, and link to shop customer details.
    URL: /admin-dashboard/shopwise-customers/
    """
    search_query = request.GET.get("q", "").strip()
    shops_qs = Shop.objects.all().select_related("owner_account").order_by("name")

    if search_query:
        shops_qs = shops_qs.filter(
            Q(name__icontains=search_query) |
            Q(owner_name__icontains=search_query) |
            Q(phone__icontains=search_query)
        )

    shops_data = []
    for s in shops_qs:
        fin = get_shop_financials(s)
        cust_count = s.customers.count()
        app_count = s.applications.count()
        shops_data.append({
            "shop": s,
            "customer_count": cust_count,
            "application_count": app_count,
            "financials": fin,
        })

    context = {
        "active_page": "shopwise_customers",
        "shops_data": shops_data,
        "search_query": search_query,
        "total_shops": len(shops_data),
    }
    return render(request, "admin_dashboard/shopwise_customers_list.html", context)


@login_required
@admin_required
def admin_shopwise_detail_view(request, shop_id):
    """
    Specific shop view displaying all customers and EMI details registered by that exact shop.
    Guarantees strict shop-level isolation: never mixes records from different shops.
    URL: /admin-dashboard/shopwise-customers/<int:shop_id>/
    """
    shop = get_object_or_404(Shop.objects.select_related("owner_account"), pk=shop_id)

    # Scoped Customers belonging strictly to this shop
    customers = Customer.objects.filter(shop=shop).prefetch_related(
        "applications", "applications__financier_payments"
    ).order_by("-created_at")

    search_query = request.GET.get("q", "").strip()
    if search_query:
        customers = customers.filter(
            Q(name__icontains=search_query) |
            Q(primary_mobile__icontains=search_query) |
            Q(secondary_mobile__icontains=search_query)
        )

    # Wrap customer application details
    customer_rows = []
    for c in customers:
        app = c.applications.first()
        customer_rows.append({
            "customer": c,
            "application": app,
            "next_due_date": get_next_due_date(app.monthly_due_day) if app else None,
            "outstanding": app.outstanding_amount if app else Decimal("0.00"),
        })

    # Financial Summary for this shop
    financials = get_shop_financials(shop)

    # Payment History made by Admin to this shop
    payments = FinancierPayment.objects.filter(
        Q(application__shop=shop) | Q(shop=shop)
    ).select_related("application", "application__customer").order_by("-payment_date", "-created_at")

    # Eligible applications that can receive payment disbursements
    eligible_apps = FinanceApplication.objects.filter(
        shop=shop,
        status__in=[
            FinanceApplication.Status.SUBMITTED,
            FinanceApplication.Status.APPROVED,
            FinanceApplication.Status.PARTIALLY_PAID,
            FinanceApplication.Status.READY,
        ]
    ).select_related("customer")

    context = {
        "active_page": "shopwise_customers",
        "shop": shop,
        "customer_rows": customer_rows,
        "financials": financials,
        "payments": payments,
        "eligible_apps": eligible_apps,
        "search_query": search_query,
        "total_customers": customers.count(),
    }
    return render(request, "admin_dashboard/shopwise_detail.html", context)


# ==========================================
# 5. Customer EMI Details Page
# ==========================================

@login_required
@admin_required
def admin_customer_emi_list_view(request):
    """
    Customer EMI Details page has been removed per administrator requirements.
    Redirects cleanly to All Customers.
    URL: /admin-dashboard/emi-details/
    """
    return redirect("admin_all_customers")


# ==========================================
# 6. Shop Payments (Money Admin Owes to Shops)
# ==========================================

@login_required
@admin_required
def admin_shop_payments_view(request):
    """
    Shop Payments Overview: Money the Admin Owes to Shops.
    Calculates Remaining Due = Total Payable - Confirmed Payments Made by Admin.
    URL: /admin-dashboard/payments/
    """
    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    shops_qs = Shop.objects.all().select_related("owner_account").order_by("name")

    if search_query:
        shops_qs = shops_qs.filter(
            Q(name__icontains=search_query) |
            Q(owner_name__icontains=search_query) |
            Q(phone__icontains=search_query)
        )

    # Calculate live payment data for every shop
    payment_rows = []
    total_system_payable = Decimal("0.00")
    total_system_paid = Decimal("0.00")
    total_system_remaining = Decimal("0.00")

    for s in shops_qs:
        fin = get_shop_financials(s)
        total_system_payable += fin["total_payable"]
        total_system_paid += fin["total_paid"]
        total_system_remaining += fin["remaining_due"]

        if status_filter:
            if status_filter.upper() != fin["payment_status"]:
                continue

        payment_rows.append({
            "shop": s,
            "financials": fin,
            "pending_apps_count": s.applications.filter(
                status__in=[
                    FinanceApplication.Status.SUBMITTED,
                    FinanceApplication.Status.APPROVED,
                    FinanceApplication.Status.PARTIALLY_PAID,
                ]
            ).count()
        })

    # Recent Payment Transactions by Admin
    recent_payments = FinancierPayment.objects.select_related(
        "application", "application__shop", "application__customer"
    ).order_by("-payment_date", "-created_at")[:10]

    context = {
        "active_page": "payments",
        "payment_rows": payment_rows,
        "recent_payments": recent_payments,
        "total_system_payable": total_system_payable,
        "total_system_paid": total_system_paid,
        "total_system_remaining": total_system_remaining,
        "search_query": search_query,
        "status_filter": status_filter,
    }
    return render(request, "admin_dashboard/shop_payments.html", context)


# ==========================================
# 7. Admin Marks a Shop Payment as Paid
# ==========================================

@require_POST
@login_required
@admin_required
def admin_record_shop_payment_view(request):
    """
    Records an admin payout to a shop by amount or for a specific application obligation.
    Supports direct shop payout by amount without requiring customer application selection.
    Ensures transaction integrity, validates payment references, prevents duplicates,
    and updates application & shop payment status immediately.
    URL: /admin-dashboard/payments/record/
    """
    shop_id = request.POST.get("shop_id")
    app_id = request.POST.get("application_id")
    amount_str = request.POST.get("amount", "0").replace(",", "").strip()
    payment_date = request.POST.get("payment_date") or timezone.localdate().strftime("%Y-%m-%d")
    reference = request.POST.get("payment_reference", "").strip()
    remarks = request.POST.get("remarks", "").strip()
    next_url = request.POST.get("next")

    if not shop_id and not app_id:
        messages.error(request, "Please select a shop or financing application to disburse payment.")
        return redirect(next_url or "admin_shop_payments")

    shop = None
    application = None
    if app_id:
        application = get_object_or_404(FinanceApplication.objects.select_related("shop", "customer"), pk=app_id)
        shop = application.shop
    elif shop_id:
        shop = get_object_or_404(Shop, pk=shop_id)

    try:
        amount = Decimal(amount_str)
        if amount <= Decimal("0"):
            messages.error(request, "Payment amount must be greater than zero.")
            return redirect(next_url or "admin_shop_payments")
    except Exception:
        messages.error(request, "Invalid numeric payment amount.")
        return redirect(next_url or "admin_shop_payments")

    if not reference:
        messages.error(request, "Bank UTR or Payment Reference number is mandatory.")
        return redirect(next_url or "admin_shop_payments")

    # Duplicate payment prevention
    dup_filter = Q(payment_reference__iexact=reference, amount_received=amount)
    if application:
        dup_filter &= (Q(application=application) | Q(shop=shop))
    else:
        dup_filter &= (Q(shop=shop) | Q(application__shop=shop))

    existing = FinancierPayment.objects.filter(dup_filter).exists()
    if existing:
        messages.warning(request, f"Duplicate Warning: A payment with reference '{reference}' for ₹{amount:,.2f} already exists for {shop.name}.")
        return redirect(next_url or "admin_shop_payments")

    with transaction.atomic():
        payment = FinancierPayment.objects.create(
            shop=shop,
            application=application,
            amount_received=amount,
            payment_date=payment_date,
            payment_reference=reference,
            remarks=remarks or f"Payment recorded by Administrator {request.user.username}"
        )

        # If payment was made directly to shop by amount, reconcile any pending applications in FIFO order
        if not application and shop:
            unpaid_apps = shop.applications.filter(
                status__in=[
                    FinanceApplication.Status.SUBMITTED,
                    FinanceApplication.Status.APPROVED,
                    FinanceApplication.Status.PARTIALLY_PAID,
                    FinanceApplication.Status.READY
                ]
            ).order_by("created_at")
            rem = amount
            for uapp in unpaid_apps:
                due = uapp.outstanding_amount
                if due <= 0:
                    continue
                if rem >= due:
                    uapp.status = FinanceApplication.Status.PAID
                    uapp.save(update_fields=["status", "updated_at"])
                    rem -= due
                elif rem > 0:
                    uapp.status = FinanceApplication.Status.PARTIALLY_PAID
                    uapp.save(update_fields=["status", "updated_at"])
                    rem = Decimal("0.00")
                    break

    # Success messaging
    if application:
        messages.success(
            request,
            f"Successfully recorded payment of ₹{amount:,.2f} to {shop.name} for application {application.application_number} (Ref: {reference})."
        )
    else:
        messages.success(
            request,
            f"Successfully recorded payout of ₹{amount:,.2f} to {shop.name} (Ref: {reference})."
        )

    return redirect(next_url or "admin_shop_payments")


# ==========================================
# 8. Reports & Performance Analytics
# ==========================================

@login_required
@admin_required
def admin_reports_view(request):
    """
    Executive Reports & Performance Analytics Page.
    URL: /admin-dashboard/reports/
    """
    total_shops = Shop.objects.count()
    active_shops = Shop.objects.filter(is_active=True).count()
    total_customers = Customer.objects.count()
    total_applications = FinanceApplication.objects.count()

    total_requested = FinanceApplication.objects.aggregate(
        total=Sum("financier_request_amount")
    )["total"] or Decimal("0.00")

    total_disbursed = FinancierPayment.objects.aggregate(
        total=Sum("amount_received")
    )["total"] or Decimal("0.00")

    total_liability = max(Decimal("0.00"), total_requested - total_disbursed)

    # Application Status Breakdown
    status_breakdown = (
        FinanceApplication.objects.values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    # Shop Performance Ranking
    shops = list(Shop.objects.all().select_related("owner_account"))
    shop_reports = []
    for s in shops:
        fin = get_shop_financials(s)
        shop_reports.append({
            "shop": s,
            "customers": s.customers.count(),
            "applications": s.applications.count(),
            "financials": fin,
        })
    shop_reports.sort(key=lambda x: x["financials"]["total_payable"], reverse=True)

    context = {
        "active_page": "reports",
        "total_shops": total_shops,
        "active_shops": active_shops,
        "total_customers": total_customers,
        "total_applications": total_applications,
        "total_requested": total_requested,
        "total_disbursed": total_disbursed,
        "total_liability": total_liability,
        "status_breakdown": status_breakdown,
        "shop_reports": shop_reports,
    }
    return render(request, "admin_dashboard/reports.html", context)
