"""
Comprehensive Views for Admin Dashboard and Integrated System Administration.
Integrates directly with the existing Shop Dashboard, Customer Registry,
Finance Applications, and Financier Payments.
"""

from decimal import Decimal
from datetime import timedelta
import calendar
import json
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

    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]

    total_customers = (
        Customer.objects.filter(shop=shop, applications__status__in=approved_statuses)
        .distinct()
        .count()
    )

    # Sum of customer EMI payments
    total_paid = CustomerEMIPayment.objects.filter(
        customer__shop=shop,
        application__status__in=approved_statuses
    ).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    # Ensure all approved applications have default schedules initialized
    for app in FinanceApplication.objects.filter(shop=shop, status__in=approved_statuses):
        app.generate_default_schedule_if_missing()

    # Total pending this month: installments due in current calendar month that are unpaid
    month_unpaid = CustomerEMISchedule.objects.filter(
        customer__shop=shop,
        application__status__in=approved_statuses,
        due_date__year=today.year,
        due_date__month=today.month,
        is_paid=False
    )
    total_pending_this_month = sum((s.remaining_amount for s in month_unpaid), Decimal("0.00"))

    # Total pending including all EMIs: all unpaid installments
    all_unpaid = CustomerEMISchedule.objects.filter(
        customer__shop=shop,
        application__status__in=approved_statuses,
        is_paid=False
    )
    total_pending_all_emis = sum((s.remaining_amount for s in all_unpaid), Decimal("0.00"))

    return {
        "total_customers": total_customers,
        "total_amount_paid": total_paid,
        "total_pending_this_month": total_pending_this_month,
        "total_pending_all_emis": total_pending_all_emis,
    }


def prepare_customer_management_dataset(customers_qs, request, today=None, exclude_completed=True, completed_only=False, exclude_loss=True, loss_only=False):
    """
    Prepares, classifies, filters, and sorts customer rows consistently for both
    HTML templates and Excel / PDF exports.
    Automatically excludes completed EMI customers and Loss customers from active customer lists,
    and supports completed_only=True and loss_only=True.
    """
    if today is None:
        today = timezone.localdate()

    if request.GET.get("include_completed") == "1":
        exclude_completed = False
    if request.GET.get("completed_only") == "1":
        completed_only = True
    if request.GET.get("loss_only") == "1":
        loss_only = True

    all_rows = []
    for c in customers_qs:
        # Filter by Loss classification
        if loss_only:
            if not getattr(c, "is_loss", False):
                continue
        elif exclude_loss:
            if getattr(c, "is_loss", False):
                continue

        app = c.applications.first()

        # After the application is APPROVED only it has to be added in any customer list
        if not app or app.status not in [
            FinanceApplication.Status.APPROVED,
            FinanceApplication.Status.PAID,
            FinanceApplication.Status.PARTIALLY_PAID,
        ]:
            continue
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
            is_completed = bool(app.emi_schedules.exists() and (rem <= Decimal("0.00") or due_status == "PAID" or not next_sched))
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
            is_completed = False

        # Filter by completion status
        if completed_only:
            if not is_completed or getattr(c, "is_loss", False):
                continue
        elif exclude_completed:
            if is_completed:
                continue

        # Completed Date determination and persistence
        comp_date = c.completed_date or (app.completed_date if app else None)
        if is_completed and not comp_date:
            last_pay = app.customer_emi_payments.order_by("-payment_date", "-created_at").first() if app else None
            comp_date = last_pay.payment_date if last_pay else (c.updated_at.date() if c.updated_at else today)
            c.completed_date = comp_date
            c.save(update_fields=["completed_date"])
            if app and not app.completed_date:
                app.completed_date = comp_date
                app.save(update_fields=["completed_date"])

        # Row highlighting & badge styling: clean white rows, distinctive color badges (Red, Yellow, Purple)
        if due_status == "DUE_TODAY":
            row_class = ""
            badge_class = "danger"
            due_status_display = "Due Today"
        elif due_status == "UPCOMING_DUE":
            row_class = ""
            badge_class = "warning"
            due_status_display = "Upcoming Due"
        elif due_status == "OVERDUE":
            row_class = ""
            badge_class = "purple"
            due_status_display = "Pending Due"
        elif due_status == "PAID" or is_completed:
            row_class = ""
            badge_class = "success"
            due_status_display = "Completed"
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
            "secondary_mobile": c.secondary_mobile or "",
            "home_address": c.home_address or "",
            "work_address": c.work_address or "",
            "shop": c.shop,
            "shop_name": c.shop.name if c.shop else "—",
            "application": app,
            "application_id": app_id,
            "application_number": app.application_number if app else "—",
            "mobile_model": mobile_model,
            "mobile_price": price,
            "down_payment": down,
            "monthly_emi": monthly_emi,
            "duration_months": app.emi_duration if app else 0,
            "amount_paid": paid,
            "remaining_balance": rem,
            "original_loss": down - rem,
            "next_due_date": next_due,
            "next_due_date_iso": next_due.strftime('%Y-%m-%d') if next_due else "",
            "next_due_date_display": next_due.strftime('%d %b %Y') if next_due else "—",
            "next_unpaid_schedule_id": next_sched_id,
            "due_status": due_status,
            "due_status_display": due_status_display,
            "badge_class": badge_class,
            "row_class": row_class,
            "is_completed": is_completed,
            "completed_date": comp_date,
            "completed_date_display": comp_date.strftime("%d-%m-%Y") if comp_date else "—",
            "is_loss": getattr(c, "is_loss", False),
            "loss_date": getattr(c, "loss_date", None),
            "loss_date_display": c.loss_date.strftime("%d-%m-%Y") if getattr(c, "loss_date", None) else "—",
            "loss_reason": getattr(c, "loss_reason", ""),
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
    elif sort_param == "loss_desc":
        filtered_rows.sort(key=lambda r: r["original_loss"], reverse=True)
    elif sort_param == "loss_asc":
        filtered_rows.sort(key=lambda r: r["original_loss"])

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

    total_paid = FinancierPayment.objects.filter(pay_filter).aggregate(
        total=Sum("amount_received")
    )["total"] or Decimal("0.00")

    emi_filter = Q(customer__shop=shop, collected_by_shop=True)
    if start_date:
        emi_filter &= (Q(payment_date__gte=start_date) | Q(created_at__date__gte=start_date))
    if end_date:
        emi_filter &= (Q(payment_date__lte=end_date) | Q(created_at__date__lte=end_date))
    shop_collected_emi = CustomerEMIPayment.objects.filter(emi_filter).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    remaining_due = max(Decimal("0.00"), total_payable - total_paid - shop_collected_emi)

    if total_payable == Decimal("0.00"):
        payment_status = "NO_OBLIGATIONS"
        payment_status_display = "No Obligations"
        badge_class = "secondary"
    elif remaining_due == Decimal("0.00"):
        payment_status = "PAID"
        payment_status_display = "Paid"
        badge_class = "success"
    elif (total_paid + shop_collected_emi) > Decimal("0.00"):
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
        "shop_collected_emi": shop_collected_emi,
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

    # Financial Aggregates (Money Admin Owes to Shops for Approved Applications)
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]

    total_customers = (
        Customer.objects.filter(applications__status__in=approved_statuses)
        .distinct()
        .count()
    )
    total_applications = FinanceApplication.objects.count()

    total_payable_agg = FinanceApplication.objects.filter(
        status__in=approved_statuses
    ).aggregate(
        total=Sum("financier_request_amount")
    )["total"] or Decimal("0.00")

    total_paid_agg = FinancierPayment.objects.aggregate(
        total=Sum("amount_received")
    )["total"] or Decimal("0.00")

    total_shop_collected_emi = CustomerEMIPayment.objects.filter(
        collected_by_shop=True
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    total_remaining_due = max(Decimal("0.00"), total_payable_agg - total_paid_agg - total_shop_collected_emi)

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
        s.customer_count = s.customers.filter(applications__status__in=approved_statuses).distinct().count()

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

    customers_qs = Customer.objects.filter(
        applications__status__in=[
            FinanceApplication.Status.APPROVED,
            FinanceApplication.Status.PAID,
            FinanceApplication.Status.PARTIALLY_PAID,
        ]
    ).distinct().select_related("shop").prefetch_related(
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

    paginator = Paginator(all_rows, 50)
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

    update_history = customer.update_history.exclude(
        notes__icontains="recorded via"
    ).exclude(
        notes__startswith="Payment of "
    ).select_related("user").order_by("-created_at")

    customer_photo = None
    for item in app_details:
        for doc in item["documents"]:
            if doc.document_type == "PHOTOGRAPH" and doc.file:
                customer_photo = doc
                break
        if customer_photo:
            break

    context = {
        "active_page": "customers",
        "customer": customer,
        "customer_photo": customer_photo,
        "shop": customer.shop,
        "app_details": app_details,
        "update_history": update_history,
    }
    return render(request, "admin_dashboard/customer_detail.html", context)


@require_POST
@login_required
@admin_required
def admin_customer_delete_view(request, pk):
    """
    Permanently deletes a customer and all their associated records from the database.
    URL: /admin-dashboard/customers/<int:pk>/delete/
    """
    customer = get_object_or_404(Customer, pk=pk)
    cust_name = customer.name
    with transaction.atomic():
        customer.delete()

    messages.success(request, f"Customer '{cust_name}' has been permanently deleted from the database.")
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if next_url and ("customers/" + str(pk)) not in next_url:
        return redirect(next_url)
    return redirect("admin_all_customers")


@require_POST
@login_required
@admin_required
def admin_customer_move_to_loss_view(request, pk):
    """
    Moves an active customer to the Loss list.
    Preserves all records, payment history, audit notes, and EMI information.
    Prevents duplicates if customer is already in Loss list.
    URL: /admin-dashboard/customers/<int:pk>/move-to-loss/
    """
    customer = get_object_or_404(Customer, pk=pk)
    reason = request.POST.get("reason", "").strip()
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("admin_all_customers")

    if customer.is_loss:
        messages.info(request, f"Customer '{customer.name}' is already in the Loss list.")
        return redirect(next_url)

    customer.is_loss = True
    customer.loss_date = timezone.localdate()
    customer.loss_reason = reason or "Moved to Loss list by Administrator"
    customer.save(update_fields=["is_loss", "loss_date", "loss_reason", "updated_at"])

    # Log audit note in update history
    CustomerUpdateHistory.objects.create(
        customer=customer,
        user=request.user,
        notes=f"Customer moved to Loss list by Administrator {request.user.username}." + (f" Reason: {reason}" if reason else "")
    )

    messages.success(request, f"Customer '{customer.name}' has been successfully moved to the Loss list.")
    return redirect(next_url)


@require_POST
@login_required
@admin_required
def admin_customer_restore_from_loss_view(request, pk):
    """
    Restores a customer from Loss list back to the active customer list.
    URL: /admin-dashboard/customers/<int:pk>/restore-from-loss/
    """
    customer = get_object_or_404(Customer, pk=pk)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("admin_loss_customers")

    customer.is_loss = False
    customer.loss_date = None
    customer.save(update_fields=["is_loss", "loss_date", "updated_at"])

    CustomerUpdateHistory.objects.create(
        customer=customer,
        user=request.user,
        notes=f"Customer restored from Loss list to Active customers by Administrator {request.user.username}."
    )

    messages.success(request, f"Customer '{customer.name}' has been restored back to the active customer directory.")
    return redirect(next_url)


@login_required
@admin_required
def admin_loss_customers_view(request):
    """
    Dedicated view for customers moved to Loss list.
    Displays only customers marked as Loss with complete details:
    Customer Name, Phone Number, Shop Name, Phone Model, Total Amount,
    Amount Paid, Amount Due, EMI Details, Loss Date.
    URL: /admin-dashboard/loss-customers/
    """
    search_query = request.GET.get("q", "").strip()
    shop_filter = request.GET.get("shop_id", "").strip()

    customers_qs = Customer.objects.filter(is_loss=True).select_related("shop").prefetch_related(
        "applications", "applications__emi_schedules", "applications__customer_emi_payments", "update_history"
    ).order_by("-loss_date", "-created_at")

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

    dataset = prepare_customer_management_dataset(customers_qs, request, loss_only=True)
    all_rows = dataset["all_rows"]

    # Statistics for loss customers
    total_loss_count = len(all_rows)
    total_loss_due = sum((r["remaining_balance"] for r in all_rows), Decimal("0.00"))
    total_loss_paid = sum((r["amount_paid"] for r in all_rows), Decimal("0.00"))
    total_original_loss = sum((r["original_loss"] for r in all_rows), Decimal("0.00"))
    total_outstanding_finance = sum(
        (r["mobile_price"] - r["down_payment"] for r in all_rows),
        Decimal("0.00")
    )
    total_loss_volume = total_original_loss

    paginator = Paginator(all_rows, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    all_shops = Shop.objects.all().order_by("name")

    context = {
        "active_page": "loss_customers",
        "customers": page_obj,
        "all_shops": all_shops,
        "search_query": search_query,
        "shop_filter": shop_filter,
        "total_loss_count": total_loss_count,
        "total_loss_due": total_loss_due,
        "total_loss_paid": total_loss_paid,
        "total_loss_volume": total_loss_volume,
        "total_original_loss": total_original_loss,
        "total_outstanding_finance": total_outstanding_finance,
        "sort_param": dataset["sort_param"],
    }
    return render(request, "admin_dashboard/loss_customers.html", context)


@require_POST
@login_required
@admin_required
def admin_update_installment_view(request, pk):
    """
    Updates an individual EMI installment schedule:
    - Amount Due (editable)
    - Amount Paid (editable)
    - EMI Due Date (editable with datepicker)
    - EMI Status (editable dropdown: PENDING, PARTIAL, PAID)
    - Update note (editable input field under Update column)
    Validates numeric amounts, non-negative, and updates database immediately.
    Logs individual audit history entries for every modified field in Customer Update Audit.
    URL: /admin-dashboard/installments/<int:pk>/update/
    """
    schedule = get_object_or_404(CustomerEMISchedule.objects.select_related("customer", "application"), pk=pk)
    app = schedule.application
    customer = schedule.customer

    amount_str = request.POST.get("amount", "").replace(",", "").strip()
    amount_paid_str = request.POST.get("amount_paid", "").replace(",", "").strip()
    due_date_str = request.POST.get("due_date", "").strip()
    status_str = request.POST.get("status", "").strip().upper()
    update_note = request.POST.get("update_note", "").strip()
    carry_forward = request.POST.get("carry_forward", "").strip().lower()
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("admin_customer_detail", kwargs={"pk": customer.pk})

    try:
        new_amount = Decimal(amount_str) if amount_str else schedule.amount
        new_amount_paid = Decimal(amount_paid_str) if amount_paid_str else schedule.amount_paid

        if new_amount < Decimal("0.00"):
            messages.error(request, "Amount Due cannot be negative.")
            return redirect(next_url)
        if new_amount_paid < Decimal("0.00"):
            messages.error(request, "Amount Paid cannot be negative.")
            return redirect(next_url)
    except Exception:
        messages.error(request, "Invalid numeric amounts provided.")
        return redirect(next_url)

    old_amount = schedule.amount
    old_paid = schedule.amount_paid
    old_due_date = schedule.due_date
    old_status = schedule.status
    old_note = schedule.update_note

    # Parse and validate new due date
    new_due_date = old_due_date
    if due_date_str:
        try:
            from datetime import datetime
            new_due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
        except Exception:
            messages.error(request, "Invalid due date format. Please use YYYY-MM-DD.")
            return redirect(next_url)

    # Determine status & is_paid
    valid_statuses = [
        CustomerEMISchedule.InstallmentStatus.PENDING,
        CustomerEMISchedule.InstallmentStatus.PARTIAL,
        CustomerEMISchedule.InstallmentStatus.PAID,
    ]
    amount_changed = (old_paid != new_amount_paid) or (old_amount != new_amount)
    if new_amount_paid >= new_amount and new_amount > Decimal("0.00") and not (
        status_str == CustomerEMISchedule.InstallmentStatus.PENDING and status_str != old_status and old_paid == new_amount_paid
    ):
        # EMI is fully paid or paid more than due -> automatically mark as PAID
        new_status = CustomerEMISchedule.InstallmentStatus.PAID
        is_paid = True
    elif status_str in valid_statuses and (status_str != old_status or not amount_changed):
        new_status = status_str
        if new_status == CustomerEMISchedule.InstallmentStatus.PAID:
            is_paid = True
            if new_amount_paid < new_amount and new_amount > Decimal("0.00"):
                new_amount_paid = new_amount
        elif new_status == CustomerEMISchedule.InstallmentStatus.PENDING:
            is_paid = False
            if new_amount_paid >= new_amount and new_amount > Decimal("0.00"):
                new_amount_paid = Decimal("0.00")
        else:  # PARTIAL
            is_paid = False
    else:
        # Auto-compute status from amounts
        if new_amount_paid >= new_amount and new_amount > Decimal("0.00"):
            new_status = CustomerEMISchedule.InstallmentStatus.PAID
            is_paid = True
        elif new_amount_paid > Decimal("0.00"):
            new_status = CustomerEMISchedule.InstallmentStatus.PARTIAL
            is_paid = False
        else:
            new_status = CustomerEMISchedule.InstallmentStatus.PENDING
            is_paid = False

    with transaction.atomic():
        schedule.amount = new_amount
        schedule.amount_paid = new_amount_paid
        schedule.due_date = new_due_date
        schedule.status = new_status
        schedule.is_paid = is_paid
        schedule.update_note = update_note
        if is_paid:
            if not schedule.paid_date:
                schedule.paid_date = timezone.localdate()
        else:
            schedule.paid_date = None

        schedule.save()

        # Handle Partial Payment Carry-Forward to Next Month if requested
        carried_forward_amount = Decimal("0.00")
        carried_to_inst_num = None
        if new_status == CustomerEMISchedule.InstallmentStatus.PARTIAL and carry_forward == "yes":
            unpaid = max(Decimal("0.00"), new_amount - new_amount_paid)
            if unpaid > Decimal("0.00"):
                next_sched = (
                    app.emi_schedules
                    .filter(installment_number__gt=schedule.installment_number)
                    .order_by("installment_number")
                    .first()
                )
                if next_sched:
                    next_sched.amount = (next_sched.amount or Decimal("0.00")) + unpaid
                    if next_sched.is_paid and next_sched.amount_paid < next_sched.amount:
                        next_sched.is_paid = False
                        next_sched.paid_date = None
                        next_sched.status = (
                            CustomerEMISchedule.InstallmentStatus.PARTIAL
                            if next_sched.amount_paid > Decimal("0.00")
                            else CustomerEMISchedule.InstallmentStatus.PENDING
                        )
                    next_sched.save()
                else:
                    # Current is the final installment: create a new next-month installment
                    from datetime import date as dt_date
                    m = schedule.due_date.month + 1
                    y = schedule.due_date.year
                    if m > 12:
                        m = 1
                        y += 1
                    last_day = calendar.monthrange(y, m)[1]
                    next_due_dt = dt_date(y, m, min(schedule.due_date.day, last_day))
                    next_sched = CustomerEMISchedule.objects.create(
                        application=app,
                        customer=customer,
                        installment_number=schedule.installment_number + 1,
                        due_date=next_due_dt,
                        amount=unpaid,
                        amount_paid=Decimal("0.00"),
                        is_paid=False,
                        status=CustomerEMISchedule.InstallmentStatus.PENDING,
                    )

                # Reduce current installment due amount to the paid amount so remaining is 0.00
                # (prevents double-counting in total outstanding balance while preserving payment & PARTIAL status)
                schedule.amount = new_amount_paid
                schedule.save(update_fields=["amount", "updated_at"])
                new_amount = new_amount_paid
                carried_forward_amount = unpaid
                carried_to_inst_num = next_sched.installment_number

                CustomerUpdateHistory.objects.create(
                    customer=customer,
                    user=request.user,
                    section="EMI Installments",
                    field_name="Partial Due Carry-Forward",
                    previous_value=f"Inst #{schedule.installment_number} Remaining: ₹{unpaid:,.2f}",
                    new_value=f"Inst #{next_sched.installment_number} Due: ₹{next_sched.amount:,.2f}",
                    description=f"Moved remaining unpaid ₹{unpaid:,.2f} from Inst #{schedule.installment_number} to Inst #{next_sched.installment_number}",
                    notes=f"Partial Payment Carry-Forward — Moved ₹{unpaid:,.2f} from Inst #{schedule.installment_number} to Inst #{next_sched.installment_number} (New Due: ₹{next_sched.amount:,.2f})"
                )

        # Sync manual Amount Due changes with app.total_emi_amount so Remaining Balance updates on All Customers page
        if old_amount != new_amount and carried_forward_amount == Decimal("0.00"):
            base_total = (
                app.total_emi_amount
                if (app.total_emi_amount and app.total_emi_amount > Decimal("0.00"))
                else (app.mobile_price or Decimal("0.00"))
            )
            app.total_emi_amount = max(Decimal("0.00"), base_total + (new_amount - old_amount))
            app.save(update_fields=["total_emi_amount", "updated_at"])

        # Recheck if application is completed or completion is reversed
        rem_balance = app.customer_remaining_balance
        from django.db.models import F as models_F
        has_unpaid_scheds = app.emi_schedules.filter(is_paid=False, amount__gt=models_F("amount_paid")).exists()
        if rem_balance <= Decimal("0.00") or not has_unpaid_scheds:
            today_date = timezone.localdate()
            if not app.completed_date:
                app.completed_date = today_date
                app.save(update_fields=["completed_date"])
            if not customer.completed_date:
                customer.completed_date = today_date
                customer.save(update_fields=["completed_date"])
        else:
            if customer.completed_date and rem_balance > Decimal("0.00"):
                customer.completed_date = None
                customer.save(update_fields=["completed_date"])
            if app.completed_date and rem_balance > Decimal("0.00"):
                app.completed_date = None
                app.save(update_fields=["completed_date"])

    # Log individual audit entries preserving full historical audit trail
    if old_paid != new_amount_paid:
        CustomerUpdateHistory.objects.create(
            customer=customer,
            user=request.user,
            section="EMI Installments",
            field_name="Amount Paid",
            previous_value=f"₹{old_paid:,.2f}",
            new_value=f"₹{new_amount_paid:,.2f}",
            description=f"Installment #{schedule.installment_number} Amount Paid updated",
            notes=f"EMI Installment Updated — Field: Amount Paid, Previous Value: ₹{old_paid:,.2f}, New Value: ₹{new_amount_paid:,.2f} (Inst #{schedule.installment_number})"
        )

    if old_amount != new_amount and carried_forward_amount == Decimal("0.00"):
        CustomerUpdateHistory.objects.create(
            customer=customer,
            user=request.user,
            section="EMI Installments",
            field_name="Amount Due",
            previous_value=f"₹{old_amount:,.2f}",
            new_value=f"₹{new_amount:,.2f}",
            description=f"Installment #{schedule.installment_number} Amount Due updated",
            notes=f"EMI Installment Updated — Field: Amount Due, Previous Value: ₹{old_amount:,.2f}, New Value: ₹{new_amount:,.2f} (Inst #{schedule.installment_number})"
        )

    if old_due_date != new_due_date:
        CustomerUpdateHistory.objects.create(
            customer=customer,
            user=request.user,
            section="EMI Installments",
            field_name="EMI Due Date",
            previous_value=old_due_date.strftime("%d-%m-%Y"),
            new_value=new_due_date.strftime("%d-%m-%Y"),
            description=f"Installment #{schedule.installment_number} Due Date rescheduled",
            notes=f"EMI Installment Updated — Field: EMI Due Date, Previous Value: {old_due_date.strftime('%d-%m-%Y')}, New Value: {new_due_date.strftime('%d-%m-%Y')} (Inst #{schedule.installment_number})"
        )

    if old_status != new_status:
        old_status_display = dict(CustomerEMISchedule.InstallmentStatus.choices).get(old_status, old_status)
        new_status_display = dict(CustomerEMISchedule.InstallmentStatus.choices).get(new_status, new_status)
        CustomerUpdateHistory.objects.create(
            customer=customer,
            user=request.user,
            section="EMI Installments",
            field_name="EMI Status",
            previous_value=old_status_display,
            new_value=new_status_display,
            description=f"Installment #{schedule.installment_number} Status changed from {old_status_display} to {new_status_display}",
            notes=f"EMI Installment Updated — Field: EMI Status, Previous Value: {old_status_display}, New Value: {new_status_display} (Inst #{schedule.installment_number})"
        )

    if old_note != update_note and update_note:
        CustomerUpdateHistory.objects.create(
            customer=customer,
            user=request.user,
            section="EMI Installments",
            field_name="Update Column",
            previous_value=old_note or "—",
            new_value=update_note,
            description=f"Installment #{schedule.installment_number} custom note updated",
            notes=f"EMI Installment Updated — Field: Update Column, Previous Value: '{old_note or ''}', New Value: '{update_note}' (Inst #{schedule.installment_number})"
        )

    # Sync latest_update_status so the All Customers page Update Status column reflects the change
    latest_summary = ""
    if old_note != update_note and update_note:
        latest_summary = update_note
    elif carried_forward_amount > Decimal("0.00"):
        latest_summary = f"Inst #{schedule.installment_number} partial (₹{new_amount_paid:,.2f}), ₹{carried_forward_amount:,.2f} moved to Inst #{carried_to_inst_num}"
    elif old_paid != new_amount_paid:
        latest_summary = f"Inst #{schedule.installment_number} paid updated to ₹{new_amount_paid:,.2f}"
    elif old_due_date != new_due_date:
        latest_summary = f"Inst #{schedule.installment_number} due date updated to {new_due_date.strftime('%d-%m-%Y')}"
    elif old_status != new_status:
        latest_summary = f"Inst #{schedule.installment_number} status updated to {new_status}"
    elif old_amount != new_amount:
        latest_summary = f"Inst #{schedule.installment_number} due updated to ₹{new_amount:,.2f}"

    if latest_summary:
        customer.latest_update_status = latest_summary
        customer.latest_update_at = timezone.now()
        customer.latest_update_by = request.user
        customer.save(update_fields=["latest_update_status", "latest_update_at", "latest_update_by", "updated_at"])

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("format") == "json":
        return JsonResponse({
            "success": True,
            "installment_id": schedule.id,
            "amount": str(schedule.amount),
            "amount_paid": str(schedule.amount_paid),
            "due_date": schedule.due_date.strftime("%Y-%m-%d"),
            "due_date_display": schedule.due_date.strftime("%b %d, %Y"),
            "remaining": str(schedule.remaining_amount),
            "status": schedule.status,
            "is_paid": schedule.is_paid,
            "update_note": schedule.update_note,
            "customer_total_paid": str(app.total_customer_paid),
            "customer_remaining_balance": str(app.customer_remaining_balance),
            "message": f"Installment #{schedule.installment_number} updated successfully."
        })

    messages.success(request, f"Installment #{schedule.installment_number} updated successfully.")
    return redirect(next_url)


@require_POST
@login_required
@admin_required
def admin_customer_change_application_status_view(request, pk):
    """
    Directly updates the customer's application status (e.g. APPROVED, PAID, PARTIALLY_PAID, etc.).
    Records modification in Customer Update Audit with previous value, new value, admin user, and timestamp.
    URL: /admin-dashboard/customers/<int:pk>/change-status/
    """
    customer = get_object_or_404(Customer, pk=pk)
    app = customer.applications.first()
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("admin_customer_detail", kwargs={"pk": customer.pk})

    if not app:
        messages.error(request, "No active application found for this customer.")
        return redirect(next_url)

    new_status = request.POST.get("status", "").strip()
    valid_statuses = [choice[0] for choice in FinanceApplication.Status.choices]
    if new_status not in valid_statuses:
        messages.error(request, f"Invalid status '{new_status}' provided.")
        return redirect(next_url)

    old_status = app.status
    if old_status != new_status:
        app.status = new_status
        app.save(update_fields=["status", "updated_at"])

        old_display = dict(FinanceApplication.Status.choices).get(old_status, old_status)
        new_display = dict(FinanceApplication.Status.choices).get(new_status, new_status)
        CustomerUpdateHistory.objects.create(
            customer=customer,
            user=request.user,
            section="Customer Details",
            field_name="Customer Status",
            previous_value=old_display,
            new_value=new_display,
            description=f"Customer Status changed from {old_display} to {new_display}",
            notes=f"Customer Status Updated — Field: Customer Status, Previous Value: {old_display}, New Value: {new_display}"
        )
        messages.success(request, f"Customer status updated to '{new_display}'.")
    else:
        messages.info(request, "Status remained unchanged.")

    return redirect(next_url)


@require_POST
@login_required
@admin_required
def admin_customer_edit_financials_view(request, pk):
    """
    Directly updates the customer's overall Amount Paid and Amount Due.
    Validates non-negative amounts, distributes across installment schedules,
    and updates database immediately.
    URL: /admin-dashboard/customers/<int:pk>/edit-financials/
    """
    customer = get_object_or_404(Customer, pk=pk)
    app = customer.applications.first()
    next_url = request.POST.get("next") or reverse("admin_customer_detail", kwargs={"pk": customer.pk})

    if not app:
        messages.error(request, "Customer does not have an active finance application.")
        return redirect(next_url)

    amount_paid_str = request.POST.get("amount_paid", "").replace(",", "").strip()
    amount_due_str = request.POST.get("amount_due", "").replace(",", "").strip()

    try:
        new_paid = Decimal(amount_paid_str)
        new_due = Decimal(amount_due_str)
        if new_paid < Decimal("0.00") or new_due < Decimal("0.00"):
            messages.error(request, "Amounts cannot be negative.")
            return redirect(next_url)
    except Exception:
        messages.error(request, "Please enter valid numeric amounts.")
        return redirect(next_url)

    total_payable = app.get_total_emi_amount
    if (new_paid + new_due) > (total_payable * Decimal("1.10")):
        messages.error(request, f"Amount Paid (₹{new_paid:,.2f}) + Amount Due (₹{new_due:,.2f}) exceeds total payable amount (₹{total_payable:,.2f}).")
        return redirect(next_url)

    with transaction.atomic():
        schedules = list(app.emi_schedules.order_by("installment_number"))
        rem_paid = new_paid
        for s in schedules:
            if rem_paid >= s.amount:
                s.amount_paid = s.amount
                s.is_paid = True
                s.status = CustomerEMISchedule.InstallmentStatus.PAID
                if not s.paid_date:
                    s.paid_date = timezone.localdate()
                rem_paid -= s.amount
            elif rem_paid > Decimal("0.00"):
                s.amount_paid = rem_paid
                s.is_paid = False
                s.status = CustomerEMISchedule.InstallmentStatus.PARTIAL
                s.paid_date = None
                rem_paid = Decimal("0.00")
            else:
                s.amount_paid = Decimal("0.00")
                s.is_paid = False
                s.status = CustomerEMISchedule.InstallmentStatus.PENDING
                s.paid_date = None
            s.save()

        # Update completed_date
        if new_due <= Decimal("0.00") or not app.emi_schedules.filter(is_paid=False).exists():
            today_date = timezone.localdate()
            if not customer.completed_date:
                customer.completed_date = today_date
                customer.save(update_fields=["completed_date"])
            if not app.completed_date:
                app.completed_date = today_date
                app.save(update_fields=["completed_date"])
        else:
            if customer.completed_date:
                customer.completed_date = None
                customer.save(update_fields=["completed_date"])
            if app.completed_date:
                app.completed_date = None
                app.save(update_fields=["completed_date"])

        CustomerUpdateHistory.objects.create(
            customer=customer,
            user=request.user,
            notes=f"Financial balances updated by Administrator: Amount Paid = ₹{new_paid:,.2f}, Amount Due = ₹{new_due:,.2f}."
        )

    messages.success(request, f"Financial balances for '{customer.name}' updated successfully (Paid: ₹{new_paid:,.2f}, Due: ₹{new_due:,.2f}).")
    return redirect(next_url)



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
        application.generate_default_schedule_if_missing()
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
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]
    for s in shops_qs:
        stats = get_shop_customer_emi_stats(s)
        app_count = s.applications.filter(status__in=approved_statuses).count()
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

    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]

    # Scoped Customers belonging strictly to this shop with approved applications
    customers_qs = (
        Customer.objects.filter(shop=shop, applications__status__in=approved_statuses)
        .distinct()
        .prefetch_related(
            "applications", "applications__emi_schedules", "applications__customer_emi_payments", "update_history"
        )
        .order_by("-created_at")
    )

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

    paginator = Paginator(all_rows, 50)
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
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]
    customers_qs = (
        Customer.objects.filter(shop=shop, applications__status__in=approved_statuses)
        .distinct()
        .prefetch_related(
            "applications", "applications__emi_schedules", "applications__customer_emi_payments", "update_history"
        )
        .order_by("-created_at")
    )

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

    customers_qs = Customer.objects.filter(
        applications__status__in=[
            FinanceApplication.Status.APPROVED,
            FinanceApplication.Status.PAID,
            FinanceApplication.Status.PARTIALLY_PAID,
        ]
    ).distinct().select_related("shop").prefetch_related(
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
# Completed Customers Module (Completed List)
# ==========================================

@login_required
@admin_required
def admin_completed_customers_view(request):
    """
    Dedicated Completed List view for customers who have completed their EMI repayments.
    These customers are excluded from active customer lists and tracked with complete statistics here.
    URL: /admin-dashboard/completed-customers/
    """
    search_query = request.GET.get("q", "").strip()
    shop_filter = request.GET.get("shop_id", "").strip()

    customers_qs = Customer.objects.filter(
        applications__status__in=[
            FinanceApplication.Status.APPROVED,
            FinanceApplication.Status.PAID,
            FinanceApplication.Status.PARTIALLY_PAID,
        ]
    ).distinct().select_related("shop").prefetch_related(
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

    dataset = prepare_customer_management_dataset(customers_qs, request, completed_only=True)
    all_rows = dataset["all_rows"]

    # Statistics for completed customers
    total_completed_count = len(all_rows)
    total_cleared_volume = sum((r["mobile_price"] for r in all_rows), Decimal("0.00"))
    total_collected_amount = sum((r["amount_paid"] for r in all_rows), Decimal("0.00"))

    # Pagination: exactly 50 per page with chevron arrows
    paginator = Paginator(all_rows, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    all_shops = Shop.objects.all().order_by("name")

    context = {
        "active_page": "completed_customers",
        "customers": page_obj,
        "search_query": search_query,
        "shop_filter": shop_filter,
        "sort_param": dataset["sort_param"],
        "total_completed_count": total_completed_count,
        "total_cleared_volume": total_cleared_volume,
        "total_collected_amount": total_collected_amount,
        "all_shops": all_shops,
    }
    return render(request, "admin_dashboard/completed_customers.html", context)


@login_required
@admin_required
def admin_completed_customers_export_view(request, export_format):
    """
    Exports completed customers dataset to Excel (.xlsx) or PDF (.pdf).
    URL: /admin-dashboard/completed-customers/export/<str:export_format>/
    """
    search_query = request.GET.get("q", "").strip()
    shop_filter = request.GET.get("shop_id", "").strip()

    customers_qs = Customer.objects.filter(
        applications__status__in=[
            FinanceApplication.Status.APPROVED,
            FinanceApplication.Status.PAID,
            FinanceApplication.Status.PARTIALLY_PAID,
        ]
    ).distinct().select_related("shop").prefetch_related(
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

    dataset = prepare_customer_management_dataset(customers_qs, request, completed_only=True)
    rows = dataset["all_rows"]

    title = "Completed EMI Customers Report"
    desc_str = f"Completed Customers ({len(rows)} records)"
    if export_format.lower() == "pdf":
        return export_customers_to_pdf(rows, title=title, active_filters_desc=desc_str)
    return export_customers_to_excel(rows, title=title, active_filters_desc=desc_str)


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

    payment = app.record_customer_payment(
        amount=amt,
        payment_date=payment_date,
        payment_mode=payment_mode,
        transaction_reference=ref,
        recorded_by=request.user,
        remarks=remarks
    )

    if payment:
        CustomerUpdateHistory.objects.create(
            customer=customer,
            user=request.user,
            section="Customer Payments",
            field_name="Payment Recorded",
            previous_value="—",
            new_value=f"₹{amt:,.2f}",
            description=f"EMI Payment #{payment.id} of ₹{amt:,.2f} ({payment_mode}) logged by {request.user.username}",
            notes=f"Payment Recorded — ₹{amt:,.2f} ({payment_mode}) [Payment #{payment.id}]"
        )

    messages.success(request, f"Payment of ₹{amt:,.2f} recorded for '{customer.name}'.")
    return redirect(next_url)


@require_POST
@login_required
@admin_required
def admin_edit_customer_payment_view(request, pk):
    """
    Edits a previously recorded CustomerEMIPayment transaction.
    Synchronizes the amount delta across the customer's EMI installment schedules,
    recalculates completion status, and logs detailed audit history.
    URL: /admin-dashboard/customer-payments/<int:pk>/edit/
    """
    payment = get_object_or_404(CustomerEMIPayment.objects.select_related("customer", "application"), pk=pk)
    app = payment.application
    customer = payment.customer
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("admin_customer_detail", kwargs={"pk": customer.pk})

    amount_str = request.POST.get("amount", "").replace(",", "").strip()
    payment_date_str = request.POST.get("payment_date", "").strip()
    payment_mode = request.POST.get("payment_mode", payment.payment_mode).strip()
    transaction_reference = request.POST.get("transaction_reference", "").strip()
    remarks = request.POST.get("remarks", "").strip()

    try:
        new_amount = Decimal(amount_str)
        if new_amount <= Decimal("0.00"):
            messages.error(request, "Payment amount must be greater than zero.")
            return redirect(next_url)
    except Exception:
        messages.error(request, "Invalid payment amount provided.")
        return redirect(next_url)

    new_date = payment.payment_date
    if payment_date_str:
        try:
            from datetime import datetime
            new_date = datetime.strptime(payment_date_str, "%Y-%m-%d").date()
        except Exception:
            messages.error(request, "Invalid payment date format.")
            return redirect(next_url)

    old_amount = payment.amount
    old_date = payment.payment_date
    old_mode = payment.payment_mode
    old_ref = payment.transaction_reference or ""
    old_remarks = payment.remarks or ""

    with transaction.atomic():
        payment.amount = new_amount
        payment.payment_date = new_date
        payment.payment_mode = payment_mode
        payment.transaction_reference = transaction_reference
        payment.remarks = remarks
        payment.save()

        # Synchronize amount delta with EMI schedules
        delta = new_amount - old_amount
        if delta > Decimal("0.00"):
            remaining_to_add = delta
            for sched in app.emi_schedules.filter(is_paid=False).order_by("installment_number"):
                if remaining_to_add <= Decimal("0.00"):
                    break
                needed = sched.amount - sched.amount_paid
                if needed <= Decimal("0.00"):
                    continue
                if remaining_to_add >= needed:
                    sched.amount_paid = sched.amount
                    sched.is_paid = True
                    sched.paid_date = new_date
                    sched.status = CustomerEMISchedule.InstallmentStatus.PAID
                    remaining_to_add -= needed
                else:
                    sched.amount_paid += remaining_to_add
                    sched.status = CustomerEMISchedule.InstallmentStatus.PARTIAL
                    remaining_to_add = Decimal("0.00")
                sched.save(update_fields=["amount_paid", "is_paid", "paid_date", "status", "updated_at"])
        elif delta < Decimal("0.00"):
            remaining_to_deduct = abs(delta)
            for sched in app.emi_schedules.filter(amount_paid__gt=Decimal("0.00")).order_by("-installment_number"):
                if remaining_to_deduct <= Decimal("0.00"):
                    break
                if sched.amount_paid >= remaining_to_deduct:
                    sched.amount_paid -= remaining_to_deduct
                    remaining_to_deduct = Decimal("0.00")
                else:
                    remaining_to_deduct -= sched.amount_paid
                    sched.amount_paid = Decimal("0.00")
                sched.is_paid = (sched.amount_paid >= sched.amount and sched.amount > Decimal("0.00"))
                if sched.is_paid:
                    sched.status = CustomerEMISchedule.InstallmentStatus.PAID
                elif sched.amount_paid > Decimal("0.00"):
                    sched.status = CustomerEMISchedule.InstallmentStatus.PARTIAL
                    sched.paid_date = None
                else:
                    sched.status = CustomerEMISchedule.InstallmentStatus.PENDING
                    sched.paid_date = None
                sched.save(update_fields=["amount_paid", "is_paid", "paid_date", "status", "updated_at"])

        # Recheck application/customer completion status
        rem_balance = app.customer_remaining_balance
        from django.db.models import F as models_F
        has_unpaid_scheds = app.emi_schedules.filter(is_paid=False, amount__gt=models_F("amount_paid")).exists()
        if rem_balance <= Decimal("0.00") or not has_unpaid_scheds:
            today_date = timezone.localdate()
            if not app.completed_date:
                app.completed_date = today_date
                app.save(update_fields=["completed_date", "updated_at"])
            if not customer.completed_date:
                customer.completed_date = today_date
                customer.save(update_fields=["completed_date", "updated_at"])
        else:
            if customer.completed_date and rem_balance > Decimal("0.00"):
                customer.completed_date = None
                customer.save(update_fields=["completed_date", "updated_at"])
            if app.completed_date and rem_balance > Decimal("0.00"):
                app.completed_date = None
                app.save(update_fields=["completed_date", "updated_at"])

        # Log audit entries for every changed field
        if old_amount != new_amount:
            CustomerUpdateHistory.objects.create(
                customer=customer,
                user=request.user,
                section="Customer Payments",
                field_name="Payment Amount",
                previous_value=f"₹{old_amount:,.2f}",
                new_value=f"₹{new_amount:,.2f}",
                description=f"Recorded payment #{payment.id} amount updated by {request.user.username}",
                notes=f"Payment Updated — Field: Payment Amount, Previous Value: ₹{old_amount:,.2f}, New Value: ₹{new_amount:,.2f} (Payment #{payment.id})"
            )
        if old_date != new_date:
            CustomerUpdateHistory.objects.create(
                customer=customer,
                user=request.user,
                section="Customer Payments",
                field_name="Payment Date",
                previous_value=old_date.strftime("%d-%m-%Y"),
                new_value=new_date.strftime("%d-%m-%Y"),
                description=f"Recorded payment #{payment.id} date updated by {request.user.username}",
                notes=f"Payment Updated — Field: Payment Date, Previous Value: {old_date.strftime('%d-%m-%Y')}, New Value: {new_date.strftime('%d-%m-%Y')} (Payment #{payment.id})"
            )
        if old_mode != payment_mode:
            CustomerUpdateHistory.objects.create(
                customer=customer,
                user=request.user,
                section="Customer Payments",
                field_name="Payment Mode",
                previous_value=old_mode,
                new_value=payment_mode,
                description=f"Recorded payment #{payment.id} mode updated by {request.user.username}",
                notes=f"Payment Updated — Field: Payment Mode, Previous Value: {old_mode}, New Value: {payment_mode} (Payment #{payment.id})"
            )
        if old_ref != transaction_reference:
            CustomerUpdateHistory.objects.create(
                customer=customer,
                user=request.user,
                section="Customer Payments",
                field_name="Transaction Reference",
                previous_value=old_ref or "—",
                new_value=transaction_reference or "—",
                description=f"Recorded payment #{payment.id} reference updated by {request.user.username}",
                notes=f"Payment Updated — Field: Transaction Reference, Previous Value: '{old_ref or '—'}', New Value: '{transaction_reference or '—'}' (Payment #{payment.id})"
            )
        if old_remarks != remarks:
            CustomerUpdateHistory.objects.create(
                customer=customer,
                user=request.user,
                section="Customer Payments",
                field_name="Payment Remarks",
                previous_value=old_remarks or "—",
                new_value=remarks or "—",
                description=f"Recorded payment #{payment.id} remarks updated by {request.user.username}",
                notes=f"Payment Updated — Field: Payment Remarks, Previous Value: '{old_remarks or '—'}', New Value: '{remarks or '—'}' (Payment #{payment.id})"
            )

    messages.success(request, f"Payment #{payment.id} updated successfully.")
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

        pending_apps = list(
            s.applications.filter(
                status__in=[
                    FinanceApplication.Status.APPROVED,
                    FinanceApplication.Status.PARTIALLY_PAID,
                ]
            ).select_related("customer").order_by("-created_at")
        )

        if status_filter:
            if status_filter.upper() == "PENDING_LOANS":
                if len(pending_apps) == 0:
                    continue
            elif status_filter.upper() != fin["payment_status"]:
                continue

        # Fetch full history of payout transactions for this shop with optional date filter
        tx_filter = Q(shop=s) | Q(application__shop=s)
        if start_date:
            tx_filter &= Q(payment_date__gte=start_date)
        if end_date:
            tx_filter &= Q(payment_date__lte=end_date)

        shop_txs = list(FinancierPayment.objects.filter(tx_filter).select_related(
            "application", "application__customer", "shop"
        ).order_by("-payment_date", "-created_at"))

        filtered_paid_sum = sum((t.amount_received for t in shop_txs), Decimal("0.00"))

        payment_rows.append({
            "shop": s,
            "financials": fin,
            "transactions": shop_txs,
            "filtered_paid_sum": filtered_paid_sum,
            "pending_apps": pending_apps,
            "pending_apps_count": len(pending_apps),
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

        # If payment was made directly to shop by amount, reconcile any approved applications in FIFO order
        if not application and shop:
            unpaid_apps = shop.applications.filter(
                status__in=[
                    FinanceApplication.Status.APPROVED,
                    FinanceApplication.Status.PARTIALLY_PAID,
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

def get_analytics_dataset(year_val, month_val, shop_id_val):
    """
    Computes analytics dataset for a given Year, Month, and Shop.
    month_val can be int (1..12) or 'all'.
    shop_id_val can be int or 'all'.
    """
    now = timezone.now()
    try:
        year = int(year_val)
    except (ValueError, TypeError):
        year = now.year

    if str(month_val).lower() == "all" or not month_val:
        month = "all"
        month_name = "All Months"
    else:
        try:
            month = int(month_val)
            if month < 1 or month > 12:
                month = now.month
            month_name = calendar.month_name[month]
        except (ValueError, TypeError):
            month = now.month
            month_name = calendar.month_name[month]

    selected_shop = None
    if str(shop_id_val).lower() != "all" and shop_id_val:
        try:
            selected_shop = Shop.objects.get(pk=int(shop_id_val))
            shop_name = selected_shop.name
            shop_id = selected_shop.pk
        except (Shop.DoesNotExist, ValueError, TypeError):
            shop_name = "All Partner Shops"
            shop_id = "all"
    else:
        shop_name = "All Partner Shops"
        shop_id = "all"

    # Base QuerySets
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]
    cust_qs = Customer.objects.all()
    payout_qs = FinancierPayment.objects.all()
    coll_qs = CustomerEMIPayment.objects.all()
    app_qs = FinanceApplication.objects.filter(status__in=approved_statuses)

    # Shop filtering
    if selected_shop:
        cust_qs = cust_qs.filter(shop=selected_shop)
        payout_qs = payout_qs.filter(Q(shop=selected_shop) | Q(application__shop=selected_shop))
        coll_qs = coll_qs.filter(customer__shop=selected_shop)
        app_qs = app_qs.filter(shop=selected_shop)

    # Date filtering
    # Customer acquisitions based on created_at
    cust_qs = cust_qs.filter(created_at__year=year)
    # Shop disbursements based on actual payout transaction date
    payout_qs = payout_qs.filter(payment_date__year=year)
    # Customer collections based on actual payment date
    coll_qs = coll_qs.filter(payment_date__year=year)
    # Customer financing date for New Finance Margin
    app_qs = app_qs.filter(
        Q(application_date__year=year) | (Q(application_date__isnull=True) & Q(created_at__year=year))
    )

    if month != "all":
        cust_qs = cust_qs.filter(created_at__month=month)
        payout_qs = payout_qs.filter(payment_date__month=month)
        coll_qs = coll_qs.filter(payment_date__month=month)
        app_qs = app_qs.filter(
            Q(application_date__month=month) | (Q(application_date__isnull=True) & Q(created_at__month=month))
        )

    total_customers = cust_qs.count()
    # 1. Total Financed This Month = Total amount paid out to shops during selected month
    total_payouts = payout_qs.aggregate(s=Sum("amount_received"))["s"] or Decimal("0.00")
    # 2. Total Collected This Month = Total customer payments actually received during selected month
    total_collections = coll_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
    # 3. Cash Flow Difference = Customer Collections minus Shop Payouts
    cash_flow_diff = total_collections - total_payouts
    total_financed = app_qs.aggregate(s=Sum("financier_request_amount"))["s"] or Decimal("0.00")
    net_profit = cash_flow_diff

    # 4. Monthly Finance Margin = Sum of finance margins for customers financed during that month
    # Finance Margin = Phone Price - Amount Paid to Shop (mobile_price - financier_request_amount)
    monthly_finance_margin = Decimal("0.00")
    for app in app_qs:
        app_margin = (app.mobile_price or Decimal("0.00")) - (app.financier_request_amount or Decimal("0.00"))
        if app_margin <= Decimal("0.00") and app.down_payment:
            app_margin = app.down_payment
        monthly_finance_margin += max(Decimal("0.00"), app_margin)

    # Total Finance Margin across all financed customers
    all_time_app_qs = FinanceApplication.objects.filter(status__in=approved_statuses)
    if selected_shop:
        all_time_app_qs = all_time_app_qs.filter(shop=selected_shop)
    total_finance_margin = Decimal("0.00")
    for app in all_time_app_qs:
        app_margin = (app.mobile_price or Decimal("0.00")) - (app.financier_request_amount or Decimal("0.00"))
        if app_margin <= Decimal("0.00") and app.down_payment:
            app_margin = app.down_payment
        total_finance_margin += max(Decimal("0.00"), app_margin)

    # Generate Chart Data (01 to 30/31 if specific month, Jan to Dec if all)
    if month != "all":
        num_days = calendar.monthrange(year, month)[1]
        chart_labels = [f"{d:02d}" for d in range(1, num_days + 1)]
        chart_customers = [0] * num_days
        chart_payouts = [0.0] * num_days
        chart_collections = [0.0] * num_days

        for c_day in cust_qs.values_list("created_at__day", flat=True):
            if c_day and 1 <= c_day <= num_days:
                chart_customers[c_day - 1] += 1

        for p_day, p_amt in payout_qs.values_list("payment_date__day", "amount_received"):
            if p_day and 1 <= p_day <= num_days:
                chart_payouts[p_day - 1] += float(p_amt or 0)

        for c_day, c_amt in coll_qs.values_list("payment_date__day", "amount"):
            if c_day and 1 <= c_day <= num_days:
                chart_collections[c_day - 1] += float(c_amt or 0)
    else:
        chart_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        chart_customers = [0] * 12
        chart_payouts = [0.0] * 12
        chart_collections = [0.0] * 12

        for c_m in cust_qs.values_list("created_at__month", flat=True):
            if c_m and 1 <= c_m <= 12:
                chart_customers[c_m - 1] += 1

        for p_m, p_amt in payout_qs.values_list("payment_date__month", "amount_received"):
            if p_m and 1 <= p_m <= 12:
                chart_payouts[p_m - 1] += float(p_amt or 0)

        for c_m, c_amt in coll_qs.values_list("payment_date__month", "amount"):
            if c_m and 1 <= c_m <= 12:
                chart_collections[c_m - 1] += float(c_amt or 0)

    return {
        "year": year,
        "month": month,
        "month_name": month_name,
        "shop_id": shop_id,
        "shop_name": shop_name,
        "total_customers": total_customers,
        "total_payouts": total_payouts,
        "total_collections": total_collections,
        "total_financed": total_financed,
        "cash_flow_diff": cash_flow_diff,
        "monthly_finance_margin": monthly_finance_margin,
        "total_finance_margin": total_finance_margin,
        "net_profit": net_profit,
        "chart_labels": chart_labels,
        "chart_customers": chart_customers,
        "chart_payouts": chart_payouts,
        "chart_collections": chart_collections,
    }


@login_required
@admin_required
def admin_reports_view(request):
    """
    Business Analytics & Shop Performance Dashboard.
    URL: /admin-dashboard/reports/
    Matches the modern Atlas Finance / EMI SAFE Business Analytics specification:
    - 4 Overall KPI Stat Cards (Customers Added, Payout to Shops, Collections, Total Profit) with % vs last month
    - Single consolidated graph incorporating both Shops and Sales for shopwise & monthly comparison
    - Comparative matrix: Last Month vs This Month
    - Shop Performance Ranking Table with Best Performing Shop highlight
    - Key Insights panel with automated intelligence
    """
    now = timezone.now()
    current_year = now.year
    current_month = now.month

    # Support 'period' parameter (e.g. '2026-10' or '2026-11') or standard year/month params
    period_str = request.GET.get("period", "").strip()
    if period_str and "-" in period_str:
        try:
            parts = period_str.split("-")
            sel_year = int(parts[0])
            sel_month = int(parts[1])
        except Exception:
            sel_year = current_year
            sel_month = current_month
    else:
        try:
            sel_year = int(request.GET.get("year_a", current_year))
        except (ValueError, TypeError):
            sel_year = current_year

        month_param = request.GET.get("month_a", current_month)
        if str(month_param).lower() == "all":
            sel_month = current_month
        else:
            try:
                sel_month = int(month_param)
            except (ValueError, TypeError):
                sel_month = current_month

    # Selected shop filter (optional)
    shop_a = request.GET.get("shop_a", "all")

    # Determine previous month for comparison
    if sel_month > 1:
        prev_month = sel_month - 1
        prev_year = sel_year
    else:
        prev_month = 12
        prev_year = sel_year - 1

    # Override with manual year_b/month_b if provided
    year_b = request.GET.get("year_b", prev_year)
    month_b = request.GET.get("month_b", prev_month)
    shop_b = request.GET.get("shop_b", "all")

    # Datasets for Selected Month (A) and Prior Month (B)
    dataset_a = get_analytics_dataset(sel_year, sel_month, shop_a)
    dataset_b = get_analytics_dataset(year_b, month_b, shop_b)

    # Calculate percentage changes (A vs B)
    def calc_pct(curr, prev):
        c = float(curr or 0)
        p = float(prev or 0)
        if p == 0:
            return 100.0 if c > 0 else 0.0
        return round(((c - p) / p) * 100.0, 1)

    pct_customers = calc_pct(dataset_a["total_customers"], dataset_b["total_customers"])
    pct_payouts = calc_pct(dataset_a["total_payouts"], dataset_b["total_payouts"])
    pct_collections = calc_pct(dataset_a["total_collections"], dataset_b["total_collections"])
    pct_cash_flow = calc_pct(dataset_a["cash_flow_diff"], dataset_b["cash_flow_diff"])
    pct_finance_margin = calc_pct(dataset_a["monthly_finance_margin"], dataset_b["monthly_finance_margin"])
    pct_profit = pct_cash_flow

    # Comparison Deltas
    diff_customers = dataset_a["total_customers"] - dataset_b["total_customers"]
    diff_payouts = dataset_a["total_payouts"] - dataset_b["total_payouts"]
    diff_collections = dataset_a["total_collections"] - dataset_b["total_collections"]
    diff_cash_flow = dataset_a["cash_flow_diff"] - dataset_b["cash_flow_diff"]
    diff_finance_margin = dataset_a["monthly_finance_margin"] - dataset_b["monthly_finance_margin"]
    diff_profit = diff_cash_flow

    # Available filter choices
    available_years = [current_year - 2, current_year - 1, current_year, current_year + 1]
    available_months = [(i, calendar.month_name[i]) for i in range(1, 13)]
    all_shops = list(Shop.objects.all().order_by("name"))

    # Available monthly dropdown periods (e.g. past 12 months)
    available_periods = []
    for offset in range(12):
        m_calc = current_month - offset
        y_calc = current_year
        while m_calc <= 0:
            m_calc += 12
            y_calc -= 1
        available_periods.append({
            "val": f"{y_calc}-{m_calc:02d}",
            "label": f"{calendar.month_name[m_calc]} {y_calc}",
            "year": y_calc,
            "month": m_calc,
            "is_selected": (y_calc == sel_year and m_calc == sel_month),
        })

    # Shop Performance Ranking for Selected Month
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]
    all_shops_qs = Shop.objects.all()
    shop_rankings = []
    for s in all_shops_qs:
        s_cust = Customer.objects.filter(shop=s, created_at__year=sel_year, created_at__month=sel_month).count()
        s_sales = FinanceApplication.objects.filter(
            shop=s,
            status__in=approved_statuses,
            created_at__year=sel_year,
            created_at__month=sel_month
        ).aggregate(
            tot=Sum("financier_request_amount")
        )["tot"] or Decimal("0.00")
        s_payout = FinancierPayment.objects.filter(
            Q(shop=s) | Q(application__shop=s),
            payment_date__year=sel_year,
            payment_date__month=sel_month
        ).aggregate(tot=Sum("amount_received"))["tot"] or Decimal("0.00")
        s_coll = CustomerEMIPayment.objects.filter(
            customer__shop=s,
            payment_date__year=sel_year,
            payment_date__month=sel_month
        ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")
        s_profit = s_coll - s_payout

        shop_rankings.append({
            "shop": s,
            "customers": s_cust,
            "sales": s_sales,
            "payout": s_payout,
            "collections": s_coll,
            "profit": s_profit,
        })

    shop_rankings.sort(key=lambda x: (x["profit"], x["sales"], x["customers"]), reverse=True)
    best_shop = shop_rankings[0] if shop_rankings else None

    # Chart 1: Shop-wise Performance & Sales Data (comparing all shops side-by-side)
    top_shops = shop_rankings[:10]  # Show top 10 shops in chart
    shop_chart_labels = [item["shop"].name for item in top_shops]
    shop_chart_sales = [float(item["sales"]) for item in top_shops]
    shop_chart_payouts = [float(item["payout"]) for item in top_shops]
    shop_chart_collections = [float(item["collections"]) for item in top_shops]
    shop_chart_profit = [float(item["profit"]) for item in top_shops]
    shop_chart_customers = [item["customers"] for item in top_shops]

    # Chart 2: Monthly Trend Data (past 6 months)
    trend_labels = []
    trend_customers = []
    trend_payouts = []
    trend_collections = []
    trend_profit = []

    for offset in range(5, -1, -1):
        m_calc = sel_month - offset
        y_calc = sel_year
        while m_calc <= 0:
            m_calc += 12
            y_calc -= 1

        t_cust = Customer.objects.filter(created_at__year=y_calc, created_at__month=m_calc).count()
        t_payout = FinancierPayment.objects.filter(payment_date__year=y_calc, payment_date__month=m_calc).aggregate(
            tot=Sum("amount_received")
        )["tot"] or Decimal("0.00")
        t_coll = CustomerEMIPayment.objects.filter(payment_date__year=y_calc, payment_date__month=m_calc).aggregate(
            tot=Sum("amount")
        )["tot"] or Decimal("0.00")
        t_profit = t_coll - t_payout

        trend_labels.append(f"{calendar.month_abbr[m_calc]} {y_calc}")
        trend_customers.append(t_cust)
        trend_payouts.append(float(t_payout))
        trend_collections.append(float(t_coll))
        trend_profit.append(float(t_profit))

    # Month-by-month Financial & Profit matrix for Year A
    monthly_matrix = []
    base_cust = Customer.objects.filter(created_at__year=sel_year)
    base_payout = FinancierPayment.objects.filter(payment_date__year=sel_year)
    base_coll = CustomerEMIPayment.objects.filter(payment_date__year=sel_year)
    base_app = FinanceApplication.objects.filter(
        created_at__year=sel_year,
        status__in=approved_statuses,
    )

    if shop_a != "all":
        base_cust = base_cust.filter(shop_id=shop_a)
        base_payout = base_payout.filter(Q(shop_id=shop_a) | Q(application__shop_id=shop_a))
        base_coll = base_coll.filter(customer__shop_id=shop_a)
        base_app = base_app.filter(shop_id=shop_a)

    prev_net_profit = None
    tot_year_customers = 0
    tot_year_financed = Decimal("0.00")
    tot_year_payouts = Decimal("0.00")
    tot_year_collections = Decimal("0.00")
    tot_year_cash_flow = Decimal("0.00")
    tot_year_margin = Decimal("0.00")

    for m in range(1, 13):
        m_cust = base_cust.filter(created_at__month=m).count()
        m_fin = base_app.filter(created_at__month=m).aggregate(s=Sum("financier_request_amount"))["s"] or Decimal("0.00")
        m_payout = base_payout.filter(payment_date__month=m).aggregate(s=Sum("amount_received"))["s"] or Decimal("0.00")
        m_coll = base_coll.filter(payment_date__month=m).aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        m_cash_flow = m_coll - m_payout

        # New Finance Margin for customers financed in month m
        m_apps = base_app.filter(
            Q(application_date__month=m) | (Q(application_date__isnull=True) & Q(created_at__month=m))
        )
        m_margin = Decimal("0.00")
        for app in m_apps:
            margin = (app.mobile_price or Decimal("0.00")) - (app.financier_request_amount or Decimal("0.00"))
            if margin <= Decimal("0.00") and app.down_payment:
                margin = app.down_payment
            m_margin += max(Decimal("0.00"), margin)

        tot_year_customers += m_cust
        tot_year_financed += m_fin
        tot_year_payouts += m_payout
        tot_year_collections += m_coll
        tot_year_cash_flow += m_cash_flow
        tot_year_margin += m_margin

        # Month over month delta
        if prev_net_profit is not None and prev_net_profit != 0:
            mom_growth_pct = round(((float(m_cash_flow) - float(prev_net_profit)) / abs(float(prev_net_profit))) * 100.0, 1)
        elif prev_net_profit == 0 and m_cash_flow > 0:
            mom_growth_pct = 100.0
        else:
            mom_growth_pct = 0.0

        prev_net_profit = m_cash_flow

        # Margin %
        margin_pct = round((float(m_cash_flow) / float(m_coll) * 100.0), 1) if m_coll > 0 else 0.0

        monthly_matrix.append({
            "month_num": m,
            "month_name": calendar.month_name[m],
            "month_abbr": calendar.month_abbr[m],
            "customers": m_cust,
            "financed": m_fin,
            "payouts": m_payout,
            "collections": m_coll,
            "cash_flow_diff": m_cash_flow,
            "net_profit": m_cash_flow,
            "finance_margin": m_margin,
            "margin_pct": margin_pct,
            "mom_growth_pct": mom_growth_pct,
            "is_selected": (m == sel_month),
        })

    year_totals = {
        "customers": tot_year_customers,
        "financed": tot_year_financed,
        "payouts": tot_year_payouts,
        "collections": tot_year_collections,
        "cash_flow_diff": tot_year_cash_flow,
        "profit": tot_year_cash_flow,
        "finance_margin": tot_year_margin,
        "margin_pct": round((float(tot_year_cash_flow) / float(tot_year_collections) * 100.0), 1) if tot_year_collections > 0 else 0.0,
    }

    # High-level system stats
    total_shops = Shop.objects.count()
    active_shops = Shop.objects.filter(is_active=True).count()
    total_system_customers = Customer.objects.count()
    total_system_applications = FinanceApplication.objects.count()

    total_requested = FinanceApplication.objects.aggregate(
        total=Sum("financier_request_amount")
    )["total"] or Decimal("0.00")

    total_disbursed = FinancierPayment.objects.aggregate(
        total=Sum("amount_received")
    )["total"] or Decimal("0.00")

    total_liability = max(Decimal("0.00"), total_requested - total_disbursed)

    # Key Insights List
    insights = []
    if best_shop and best_shop["profit"] > 0:
        insights.append({
            "icon": "bi-star-fill",
            "theme": "success",
            "bg": "#dcfce7",
            "color": "#16a34a",
            "title": "Top Performing Partner Shop",
            "text": f"<strong>{best_shop['shop'].name}</strong> generated the highest profit with <strong>₹{best_shop['profit']:,.2f}</strong> in {dataset_a['month_name']}."
        })
    elif best_shop:
        insights.append({
            "icon": "bi-star-fill",
            "theme": "success",
            "bg": "#dcfce7",
            "color": "#16a34a",
            "title": "Top Volume Partner Shop",
            "text": f"<strong>{best_shop['shop'].name}</strong> led sales volume with <strong>₹{best_shop['sales']:,.2f}</strong> in financed customer loans."
        })

    if pct_collections >= 0:
        insights.append({
            "icon": "bi-arrow-up-right-circle-fill",
            "theme": "primary",
            "bg": "#dbeafe",
            "color": "#2563eb",
            "title": "Collection Performance",
            "text": f"Total collections increased by <strong>{pct_collections}%</strong> compared to last month."
        })
    else:
        insights.append({
            "icon": "bi-arrow-down-right-circle-fill",
            "theme": "danger",
            "bg": "#fee2e2",
            "color": "#dc2626",
            "title": "Collection Performance",
            "text": f"Total collections changed by <strong>{pct_collections}%</strong> compared to last month."
        })

    if pct_profit >= 0:
        insights.append({
            "icon": "bi-info-circle-fill",
            "theme": "warning",
            "bg": "#fef3c7",
            "color": "#d97706",
            "title": "Finance Margin Growth",
            "text": f"Overall profit grew by <strong>{pct_profit}%</strong> this month."
        })
    else:
        insights.append({
            "icon": "bi-info-circle-fill",
            "theme": "danger",
            "bg": "#fee2e2",
            "color": "#dc2626",
            "title": "Finance Margin Shift",
            "text": f"Overall profit variance changed by <strong>{pct_profit}%</strong> vs prior month."
        })

    context = {
        "active_page": "reports",
        "sel_year": sel_year,
        "sel_month": sel_month,
        "sel_month_name": dataset_a["month_name"],
        "prev_month_name": dataset_b["month_name"],
        "available_periods": available_periods,
        "available_years": available_years,
        "available_months": available_months,
        "all_shops": all_shops,
        "dataset_a": dataset_a,
        "dataset_b": dataset_b,
        "pct_customers": pct_customers,
        "pct_payouts": pct_payouts,
        "pct_collections": pct_collections,
        "pct_cash_flow": pct_cash_flow,
        "pct_finance_margin": pct_finance_margin,
        "pct_profit": pct_profit,
        "diff_customers": diff_customers,
        "diff_payouts": diff_payouts,
        "diff_collections": diff_collections,
        "diff_cash_flow": diff_cash_flow,
        "diff_finance_margin": diff_finance_margin,
        "diff_profit": diff_profit,
        "total_finance_margin": dataset_a["total_finance_margin"],
        "shop_rankings": shop_rankings,
        "best_shop": best_shop,
        "insights": insights,
        "monthly_matrix": monthly_matrix,
        "year_totals": year_totals,
        # JSON Payloads for consolidated interactive Chart
        "shop_chart_labels_json": json.dumps(shop_chart_labels),
        "shop_chart_sales_json": json.dumps(shop_chart_sales),
        "shop_chart_payouts_json": json.dumps(shop_chart_payouts),
        "shop_chart_collections_json": json.dumps(shop_chart_collections),
        "shop_chart_profit_json": json.dumps(shop_chart_profit),
        "shop_chart_customers_json": json.dumps(shop_chart_customers),
        "trend_labels_json": json.dumps(trend_labels),
        "trend_customers_json": json.dumps(trend_customers),
        "trend_payouts_json": json.dumps(trend_payouts),
        "trend_collections_json": json.dumps(trend_collections),
        "trend_profit_json": json.dumps(trend_profit),
        # Legacy chart payloads for backwards-compatibility with tests
        "chart_labels_a_json": json.dumps(dataset_a["chart_labels"]),
        "chart_customers_a_json": json.dumps(dataset_a["chart_customers"]),
        "chart_payouts_a_json": json.dumps(dataset_a["chart_payouts"]),
        "chart_collections_a_json": json.dumps(dataset_a["chart_collections"]),
        "chart_labels_b_json": json.dumps(dataset_b["chart_labels"]),
        "chart_customers_b_json": json.dumps(dataset_b["chart_customers"]),
        "chart_payouts_b_json": json.dumps(dataset_b["chart_payouts"]),
        "chart_collections_b_json": json.dumps(dataset_b["chart_collections"]),
        # System totals
        "total_shops": total_shops,
        "active_shops": active_shops,
        "total_customers": total_system_customers,
        "total_applications": total_system_applications,
        "total_requested": total_requested,
        "total_disbursed": total_disbursed,
        "total_liability": total_liability,
    }
    return render(request, "admin_dashboard/reports.html", context)
