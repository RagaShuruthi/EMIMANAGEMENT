"""
Comprehensive Views for Admin Dashboard and Integrated System Administration.
Integrates directly with the existing Shop Dashboard, Customer Registry,
Finance Applications, and Financier Payments.
"""

from decimal import Decimal
from datetime import timedelta
import re
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from shops.models import Shop
from customers.models import Customer, CustomerUpdateHistory
from applications.models import FinanceApplication, ApplicationHistory
from payments.models import FinancierPayment, CustomerEMISchedule, CustomerEMIPayment
from .reports_export import export_customers_to_excel, export_customers_to_pdf

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


def get_shop_customer_emi_stats(shop, today=None):
    """
    Live customer-to-shop EMI statistics for a specific shop:
    1. Total Registered Customers: count of customers under this shop
    2. Total Amount Paid by Customers: sum of customer EMI payments
    3. Total Pending This Month: sum of unpaid installments due in current calendar month
    4. Total Pending Including All EMIs: sum of all unpaid installments across all customers
    """
    if today is None:
        today = timezone.localdate()

    total_customers = Customer.objects.filter(shop=shop).count()

    # Sum of customer EMI payments
    total_paid = CustomerEMIPayment.objects.filter(customer__shop=shop).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    # Ensure all applications have default schedules initialized
    for app in FinanceApplication.objects.filter(shop=shop):
        app.generate_default_schedule_if_missing()

    # Total pending this month: installments due in current calendar month that are unpaid
    month_unpaid = CustomerEMISchedule.objects.filter(
        customer__shop=shop,
        due_date__year=today.year,
        due_date__month=today.month,
        is_paid=False
    )
    total_pending_this_month = sum((s.remaining_amount for s in month_unpaid), Decimal("0.00"))

    # Total pending including all EMIs: all unpaid installments
    all_unpaid = CustomerEMISchedule.objects.filter(
        customer__shop=shop,
        is_paid=False
    )
    total_pending_all_emis = sum((s.remaining_amount for s in all_unpaid), Decimal("0.00"))

    return {
        "total_customers": total_customers,
        "total_amount_paid": total_paid,
        "total_pending_this_month": total_pending_this_month,
        "total_pending_all_emis": total_pending_all_emis,
    }


def prepare_customer_management_dataset(customers_qs, request, today=None):
    """
    Prepares, classifies, filters, and sorts customer rows consistently for both
    HTML templates and Excel / PDF exports.
    """
    if today is None:
        today = timezone.localdate()

    all_rows = []
    for c in customers_qs:
        app = c.applications.first()
        if app:
            app.generate_default_schedule_if_missing()
            next_sched = app.next_unpaid_schedule
            next_due = next_sched.due_date if next_sched else None
            paid = app.total_customer_paid
            rem = app.customer_remaining_balance
            due_status = app.due_classification(today=today)
            price = app.mobile_price or Decimal("0.00")
            down = app.down_payment or Decimal("0.00")
            monthly_emi = app.monthly_emi or Decimal("0.00")
            mobile_model = app.mobile_model
            app_id = app.id
            next_sched_id = next_sched.id if next_sched else None
        else:
            app_id = None
            next_sched_id = None
            next_due = None
            paid = Decimal("0.00")
            rem = Decimal("0.00")
            due_status = "NO_APP"
            price = Decimal("0.00")
            down = Decimal("0.00")
            monthly_emi = Decimal("0.00")
            mobile_model = "—"

        # Row highlighting & badge styling
        if due_status == "DUE_TODAY":
            row_class = "table-danger border-start border-danger border-4"
            badge_class = "danger"
            due_status_display = "Due Today"
        elif due_status == "UPCOMING_DUE":
            row_class = "table-warning border-start border-warning border-4"
            badge_class = "warning text-dark"
            due_status_display = "Upcoming Due"
        elif due_status == "OVERDUE":
            row_class = "table-light border-start border-danger border-4"
            badge_class = "danger"
            due_status_display = "Pending Due"
        elif due_status == "PAID":
            row_class = ""
            badge_class = "success"
            due_status_display = "Paid"
        else:
            row_class = ""
            badge_class = "secondary"
            due_status_display = "On Track"

        history_items = list(c.update_history.select_related("user").order_by("-created_at")[:10])

        all_rows.append({
            "customer": c,
            "customer_id": c.id,
            "customer_name": c.name,
            "phone": c.primary_mobile,
            "secondary_mobile": c.secondary_mobile,
            "shop": c.shop,
            "shop_name": c.shop.name if c.shop else "—",
            "application": app,
            "application_id": app_id,
            "mobile_model": mobile_model,
            "mobile_price": price,
            "down_payment": down,
            "monthly_emi": monthly_emi,
            "amount_paid": paid,
            "remaining_balance": rem,
            "next_due_date": next_due,
            "next_due_date_iso": next_due.strftime('%Y-%m-%d') if next_due else "",
            "next_due_date_display": next_due.strftime('%d %b %Y') if next_due else "—",
            "next_unpaid_schedule_id": next_sched_id,
            "due_status": due_status,
            "due_status_display": due_status_display,
            "badge_class": badge_class,
            "row_class": row_class,
            "update_status": c.latest_update_status or "—",
            "latest_update_at": c.latest_update_at,
            "latest_update_by": c.latest_update_by,
            "update_history": history_items,
        })

    # Counts before checkbox filtering
    count_due_today = sum(1 for r in all_rows if r["due_status"] == "DUE_TODAY")
    count_upcoming_due = sum(1 for r in all_rows if r["due_status"] == "UPCOMING_DUE")
    count_pending_due = sum(1 for r in all_rows if r["due_status"] == "OVERDUE")
    count_total = len(all_rows)

    # Checkbox filtering: can select any 1, 2, or all 3
    filter_due_today = request.GET.get("due_today") == "1"
    filter_upcoming_due = request.GET.get("upcoming_due") == "1"
    filter_pending_due = request.GET.get("pending_due") == "1"

    filtered_rows = all_rows
    if filter_due_today or filter_upcoming_due or filter_pending_due:
        selected_statuses = set()
        if filter_due_today:
            selected_statuses.add("DUE_TODAY")
        if filter_upcoming_due:
            selected_statuses.add("UPCOMING_DUE")
        if filter_pending_due:
            selected_statuses.add("OVERDUE")
        filtered_rows = [r for r in all_rows if r["due_status"] in selected_statuses]

    # Sorting
    sort_param = request.GET.get("sort", "").strip()
    if sort_param == "name_asc":
        filtered_rows.sort(key=lambda r: (r["customer_name"] or "").lower())
    elif sort_param == "name_desc":
        filtered_rows.sort(key=lambda r: (r["customer_name"] or "").lower(), reverse=True)
    elif sort_param == "paid_desc":
        filtered_rows.sort(key=lambda r: r["amount_paid"], reverse=True)
    elif sort_param == "paid_asc":
        filtered_rows.sort(key=lambda r: r["amount_paid"])

    return {
        "all_rows": filtered_rows,
        "count_due_today": count_due_today,
        "count_upcoming_due": count_upcoming_due,
        "count_pending_due": count_pending_due,
        "count_total": count_total,
        "filter_due_today": filter_due_today,
        "filter_upcoming_due": filter_upcoming_due,
        "filter_pending_due": filter_pending_due,
        "sort_param": sort_param,
    }


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
    Displays customers registered across all shops with full EMI, update status,
    and automatic due date classifications.
    URL: /admin-dashboard/customers/
    """
    search_query = request.GET.get("q", "").strip()
    shop_filter = request.GET.get("shop_id", "").strip()

    customers_qs = Customer.objects.select_related("shop").prefetch_related(
        "applications", "applications__emi_schedules", "applications__customer_emi_payments", "update_history"
    ).order_by("-created_at")

    if search_query:
        customers_qs = customers_qs.filter(
            Q(name__icontains=search_query) |
            Q(primary_mobile__icontains=search_query) |
            Q(secondary_mobile__icontains=search_query) |
            Q(home_address__icontains=search_query) |
            Q(work_address__icontains=search_query) |
            Q(applications__mobile_model__icontains=search_query) |
            Q(shop__name__icontains=search_query)
        ).distinct()

    if shop_filter:
        customers_qs = customers_qs.filter(shop_id=shop_filter)

    dataset = prepare_customer_management_dataset(customers_qs, request)
    all_rows = dataset["all_rows"]

    paginator = Paginator(all_rows, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    all_shops = Shop.objects.all().order_by("name")

    context = {
        "active_page": "customers",
        "customers": page_obj,
        "all_shops": all_shops,
        "search_query": search_query,
        "shop_filter": shop_filter,
        "total_count": dataset["count_total"],
        "filtered_count": len(all_rows),
        "count_due_today": dataset["count_due_today"],
        "count_upcoming_due": dataset["count_upcoming_due"],
        "count_pending_due": dataset["count_pending_due"],
        "filter_due_today": dataset["filter_due_today"],
        "filter_upcoming_due": dataset["filter_upcoming_due"],
        "filter_pending_due": dataset["filter_pending_due"],
        "sort_param": dataset["sort_param"],
    }
    return render(request, "admin_dashboard/all_customers.html", context)


# ==========================================
# 3. Customer Detail & Application Approval
# ==========================================

@login_required
@admin_required
def admin_customer_detail_view(request, pk):
    """
    Detailed inspector for a customer, their applications, documents, EMI schedule, and update history.
    URL: /admin-dashboard/customers/<int:pk>/
    """
    customer = get_object_or_404(Customer.objects.select_related("shop"), pk=pk)
    applications = customer.applications.prefetch_related(
        "documents", "history", "financier_payments", "emi_schedules", "customer_emi_payments"
    ).order_by("-created_at")

    app_details = []
    for app in applications:
        app.generate_default_schedule_if_missing()
        app_details.append({
            "app": app,
            "next_due_date": app.next_unpaid_due_date,
            "due_status": app.due_classification(),
            "total_customer_paid": app.total_customer_paid,
            "customer_remaining_balance": app.customer_remaining_balance,
            "schedules": app.emi_schedules.order_by("installment_number"),
            "customer_payments": app.customer_emi_payments.order_by("-payment_date"),
            "documents": app.documents.all(),
            "history": app.history.select_related("actor").order_by("-timestamp"),
            "payments": app.financier_payments.all().order_by("-payment_date"),
        })

    update_history = customer.update_history.select_related("user").order_by("-created_at")

    context = {
        "active_page": "customers",
        "customer": customer,
        "shop": customer.shop,
        "app_details": app_details,
        "update_history": update_history,
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
    start_date = request.GET.get("start_date", "").strip()
    end_date = request.GET.get("end_date", "").strip()

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

    # Date-wise filter
    if start_date:
        apps_qs = apps_qs.filter(created_at__date__gte=start_date)
    if end_date:
        apps_qs = apps_qs.filter(created_at__date__lte=end_date)

    paginator = Paginator(apps_qs, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "active_page": "applications",
        "applications": page_obj,
        "search_query": search_query,
        "status_filter": status_filter,
        "start_date": start_date,
        "end_date": end_date,
        "total_count": total_count,
        "pending_count": pending_count,
        "approved_count": approved_count,
        "rejected_count": rejected_count,
    }
    return render(request, "admin_dashboard/applications_list.html", context)


@require_POST
@login_required
@admin_required
def admin_approve_all_pending_applications_view(request):
    """
    Approves all finance applications currently in pending/under review status.
    Generates EMI installment schedules and marks status as APPROVED.
    URL: /admin-dashboard/applications/approve-all-pending/
    """
    pending_statuses = [
        FinanceApplication.Status.SUBMITTED,
        FinanceApplication.Status.UNDER_REVIEW,
        FinanceApplication.Status.READY,
        FinanceApplication.Status.DRAFT,
    ]
    pending_apps = list(FinanceApplication.objects.filter(status__in=pending_statuses))
    count = 0
    with transaction.atomic():
        for app in pending_apps:
            old_status = app.status
            app.status = FinanceApplication.Status.APPROVED
            app.remarks = (app.remarks + " [Bulk Approved]" if app.remarks else "Bulk Approved by Administrator").strip()
            app.save()
            app.generate_default_schedule_if_missing()
            ApplicationHistory.objects.create(
                application=app,
                previous_status=old_status,
                new_status=app.status,
                actor=request.user,
                notes=f"Application approved via Bulk Approval by Administrator {request.user.username}."
            )
            count += 1

    messages.success(request, f"Successfully approved all {count} pending applications!")
    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("admin_applications_list")


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
    Displays partner shops with live customer EMI statistics:
    1. Total Registered Customers
    2. Total Amount Paid by Customers
    3. Total Pending This Month
    4. Total Pending Including All EMIs
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
        stats = get_shop_customer_emi_stats(s)
        app_count = s.applications.count()
        shops_data.append({
            "shop": s,
            "stats": stats,
            "application_count": app_count,
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
    Specific shop view displaying strictly isolated customers and EMI statistics
    for that exact shop.
    URL: /admin-dashboard/shopwise-customers/<int:shop_id>/
    """
    shop = get_object_or_404(Shop.objects.select_related("owner_account"), pk=shop_id)

    # Scoped Customers belonging strictly to this shop
    customers_qs = Customer.objects.filter(shop=shop).prefetch_related(
        "applications", "applications__emi_schedules", "applications__customer_emi_payments", "update_history"
    ).order_by("-created_at")

    search_query = request.GET.get("q", "").strip()
    if search_query:
        customers_qs = customers_qs.filter(
            Q(name__icontains=search_query) |
            Q(primary_mobile__icontains=search_query) |
            Q(secondary_mobile__icontains=search_query) |
            Q(applications__mobile_model__icontains=search_query)
        ).distinct()

    dataset = prepare_customer_management_dataset(customers_qs, request)
    all_rows = dataset["all_rows"]

    # Shop-specific live EMI statistics
    shop_stats = get_shop_customer_emi_stats(shop)

    paginator = Paginator(all_rows, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "active_page": "shopwise_customers",
        "shop": shop,
        "shop_stats": shop_stats,
        "customers": page_obj,
        "search_query": search_query,
        "total_customers": dataset["count_total"],
        "filtered_count": len(all_rows),
        "count_due_today": dataset["count_due_today"],
        "count_upcoming_due": dataset["count_upcoming_due"],
        "count_pending_due": dataset["count_pending_due"],
        "filter_due_today": dataset["filter_due_today"],
        "filter_upcoming_due": dataset["filter_upcoming_due"],
        "filter_pending_due": dataset["filter_pending_due"],
        "sort_param": dataset["sort_param"],
    }
    return render(request, "admin_dashboard/shopwise_detail.html", context)


# ==========================================
# Export Endpoints (Excel & PDF)
# ==========================================

@login_required
@admin_required
def admin_shopwise_export_view(request, shop_id, export_format):
    """
    Exports strictly shop-scoped customers to Excel or PDF respecting all active filters & sorting.
    """
    shop = get_object_or_404(Shop, pk=shop_id)
    customers_qs = Customer.objects.filter(shop=shop).prefetch_related(
        "applications", "applications__emi_schedules", "applications__customer_emi_payments", "update_history"
    ).order_by("-created_at")

    search_query = request.GET.get("q", "").strip()
    if search_query:
        customers_qs = customers_qs.filter(
            Q(name__icontains=search_query) |
            Q(primary_mobile__icontains=search_query) |
            Q(secondary_mobile__icontains=search_query) |
            Q(applications__mobile_model__icontains=search_query)
        ).distinct()

    dataset = prepare_customer_management_dataset(customers_qs, request)
    rows = dataset["all_rows"]

    filters_desc = []
    if search_query:
        filters_desc.append(f"Search: '{search_query}'")
    if dataset["filter_due_today"]:
        filters_desc.append("Due Today")
    if dataset["filter_upcoming_due"]:
        filters_desc.append("Upcoming Due")
    if dataset["filter_pending_due"]:
        filters_desc.append("Pending Due")
    if dataset["sort_param"]:
        filters_desc.append(f"Sorted: {dataset['sort_param']}")

    desc_str = ", ".join(filters_desc) or "All Records"

    if export_format.lower() == "pdf":
        return export_customers_to_pdf(rows, title=f"Customers EMI Report — {shop.name}", shop_name=shop.name, active_filters_desc=desc_str)
    return export_customers_to_excel(rows, title=f"{shop.name} Customers EMI", shop_name=shop.name, active_filters_desc=desc_str)


@login_required
@admin_required
def admin_customers_export_view(request, export_format):
    """
    Exports all customers across shops to Excel or PDF respecting all active filters & sorting.
    """
    search_query = request.GET.get("q", "").strip()
    shop_filter = request.GET.get("shop_id", "").strip()

    customers_qs = Customer.objects.select_related("shop").prefetch_related(
        "applications", "applications__emi_schedules", "applications__customer_emi_payments", "update_history"
    ).order_by("-created_at")

    if search_query:
        customers_qs = customers_qs.filter(
            Q(name__icontains=search_query) |
            Q(primary_mobile__icontains=search_query) |
            Q(secondary_mobile__icontains=search_query) |
            Q(home_address__icontains=search_query) |
            Q(work_address__icontains=search_query) |
            Q(applications__mobile_model__icontains=search_query) |
            Q(shop__name__icontains=search_query)
        ).distinct()

    if shop_filter:
        customers_qs = customers_qs.filter(shop_id=shop_filter)

    dataset = prepare_customer_management_dataset(customers_qs, request)
    rows = dataset["all_rows"]

    filters_desc = []
    if search_query:
        filters_desc.append(f"Search: '{search_query}'")
    if shop_filter:
        filters_desc.append(f"Shop ID: {shop_filter}")
    if dataset["filter_due_today"]:
        filters_desc.append("Due Today")
    if dataset["filter_upcoming_due"]:
        filters_desc.append("Upcoming Due")
    if dataset["filter_pending_due"]:
        filters_desc.append("Pending Due")
    if dataset["sort_param"]:
        filters_desc.append(f"Sorted: {dataset['sort_param']}")

    desc_str = ", ".join(filters_desc) or "All Records"

    if export_format.lower() == "pdf":
        return export_customers_to_pdf(rows, title="Master Customers EMI Report", active_filters_desc=desc_str)
    return export_customers_to_excel(rows, title="Master Customers EMI Report", active_filters_desc=desc_str)


# ==========================================
# Customer Update Status & Next Due Date Endpoints
# ==========================================

@require_POST
@login_required
@admin_required
def admin_customer_update_status_view(request, pk):
    """
    Updates the customer's Update Status note, saves to CustomerUpdateHistory,
    and synchronizes with FollowUpActivity so both Admin and Follow-up Staff dashboards match.
    """
    customer = get_object_or_404(Customer.objects.select_related("shop"), pk=pk)
    note_text = request.POST.get("notes", "").strip()
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("admin_all_customers")

    if not note_text:
        messages.warning(request, "Update note cannot be empty.")
        return redirect(next_url)

    now = timezone.now()
    customer.latest_update_status = note_text
    customer.latest_update_at = now
    customer.latest_update_by = request.user
    customer.save(update_fields=["latest_update_status", "latest_update_at", "latest_update_by", "updated_at"])

    CustomerUpdateHistory.objects.create(
        customer=customer,
        user=request.user,
        notes=note_text
    )

    # Sync to FollowUpActivity
    from followups.models import FollowUpActivity
    FollowUpActivity.objects.create(
        employee=request.user,
        customer=customer,
        shop=customer.shop,
        activity_type=FollowUpActivity.ActivityType.NOTE,
        outcome=FollowUpActivity.Outcome.NOT_PAID,
        status=FollowUpActivity.Status.COMPLETED,
        notes=note_text,
    )

    # Check if next due date was optionally supplied alongside the note
    new_due_date_str = request.POST.get("next_due_date", "").strip()
    if new_due_date_str:
        app = customer.applications.first()
        if app:
            app.generate_default_schedule_if_missing()
            next_sched = app.next_unpaid_schedule
            if next_sched:
                next_sched.due_date = new_due_date_str
                next_sched.save(update_fields=["due_date", "updated_at"])

    messages.success(request, f"Update status recorded for customer '{customer.name}'.")
    return redirect(next_url)


@require_POST
@login_required
@admin_required
def admin_customer_edit_due_date_view(request, pk):
    """
    Modifies the Next Due Date for a customer by updating their next unpaid EMI installment due date.
    Recalculates due date classifications immediately.
    """
    customer = get_object_or_404(Customer, pk=pk)
    new_date = request.POST.get("due_date", "").strip()
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("admin_all_customers")

    if not new_date:
        messages.error(request, "Please provide a valid due date.")
        return redirect(next_url)

    app = customer.applications.first()
    if not app:
        messages.error(request, "No finance application found for this customer.")
        return redirect(next_url)

    app.generate_default_schedule_if_missing()
    next_sched = app.next_unpaid_schedule
    if not next_sched:
        messages.info(request, "All installments for this customer are already settled.")
        return redirect(next_url)

    old_date = next_sched.due_date
    next_sched.due_date = new_date
    next_sched.save(update_fields=["due_date", "updated_at"])

    # Log in update history
    log_msg = f"Due date updated from {old_date} to {new_date}."
    customer.latest_update_status = log_msg
    customer.latest_update_at = timezone.now()
    customer.latest_update_by = request.user
    customer.save(update_fields=["latest_update_status", "latest_update_at", "latest_update_by", "updated_at"])

    CustomerUpdateHistory.objects.create(
        customer=customer,
        user=request.user,
        notes=log_msg
    )

    messages.success(request, f"Next due date for '{customer.name}' updated to {new_date}.")
    return redirect(next_url)


@require_POST
@login_required
@admin_required
def admin_customer_record_payment_view(request, pk):
    """
    Records a customer-to-shop EMI payment transaction.
    Applies the amount across unpaid EMI installments in sequence.
    """
    customer = get_object_or_404(Customer, pk=pk)
    amount_str = request.POST.get("amount", "").replace(",", "").strip()
    payment_date = request.POST.get("payment_date") or None
    payment_mode = request.POST.get("payment_mode", "UPI").strip()
    ref = request.POST.get("transaction_reference", "").strip()
    remarks = request.POST.get("remarks", "").strip()
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("admin_all_customers")

    try:
        amt = Decimal(amount_str)
        if amt <= Decimal("0.00"):
            raise ValueError()
    except Exception:
        messages.error(request, "Please enter a valid positive payment amount.")
        return redirect(next_url)

    app = customer.applications.first()
    if not app:
        messages.error(request, "Customer does not have an active finance application.")
        return redirect(next_url)

    app.record_customer_payment(
        amount=amt,
        payment_date=payment_date,
        payment_mode=payment_mode,
        transaction_reference=ref,
        recorded_by=request.user,
        remarks=remarks
    )

    # Log note
    note_msg = f"Payment of ₹{amt:,.2f} recorded via {payment_mode}. {remarks}".strip()
    customer.latest_update_status = note_msg
    customer.latest_update_at = timezone.now()
    customer.latest_update_by = request.user
    customer.save(update_fields=["latest_update_status", "latest_update_at", "latest_update_by", "updated_at"])

    CustomerUpdateHistory.objects.create(
        customer=customer,
        user=request.user,
        notes=note_msg
    )

    messages.success(request, f"Payment of ₹{amt:,.2f} recorded for '{customer.name}'.")
    return redirect(next_url)


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
    start_date = request.GET.get("start_date", "").strip()
    end_date = request.GET.get("end_date", "").strip()

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

    # Recent Payment Transactions by Admin with date filtering
    recent_payments = FinancierPayment.objects.select_related(
        "shop", "application", "application__shop", "application__customer"
    )
    if start_date:
        recent_payments = recent_payments.filter(payment_date__gte=start_date)
    if end_date:
        recent_payments = recent_payments.filter(payment_date__lte=end_date)
    recent_payments = recent_payments.order_by("-payment_date", "-created_at")

    context = {
        "active_page": "payments",
        "payment_rows": payment_rows,
        "recent_payments": recent_payments,
        "total_system_payable": total_system_payable,
        "total_system_paid": total_system_paid,
        "total_system_remaining": total_system_remaining,
        "search_query": search_query,
        "status_filter": status_filter,
        "start_date": start_date,
        "end_date": end_date,
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
    supports payment screenshot upload, and updates application & shop payment status immediately.
    URL: /admin-dashboard/payments/record/
    """
    shop_id = request.POST.get("shop_id")
    app_id = request.POST.get("application_id")
    amount_str = request.POST.get("amount", "0").replace(",", "").strip()
    payment_date = request.POST.get("payment_date") or timezone.localdate().strftime("%Y-%m-%d")
    reference = request.POST.get("payment_reference", "").strip()
    remarks = request.POST.get("remarks", "").strip()
    screenshot = request.FILES.get("payment_screenshot")
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
        reference = f"PAY-{shop.id if shop else 'ADMIN'}-{timezone.now().strftime('%Y%m%d%H%M%S')}"

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
            remarks=remarks or f"Payment recorded by Administrator {request.user.username}",
            payment_screenshot=screenshot
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
