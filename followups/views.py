"""
Views for Follow-up Persons management and the Follow-up Employee Portal.
Provides:
1. Administrator views for creating, editing, viewing, and activating/deactivating follow-up staff.
2. Secure password setup workflow with cryptographic token generation (no plain-text passwords).
3. Dedicated Employee Portal where follow-up persons access ONLY their assigned shops,
   authorized customers, pending follow-ups, completed follow-ups, and upcoming tasks.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Count
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.views.decorators.http import require_POST

from shops.models import Shop
from customers.models import Customer
from applications.models import FinanceApplication
from .models import FollowUpAssignment, FollowUpActivity
from .forms import FollowUpEmployeeForm, FollowUpActivityForm, FollowUpPasswordSetupForm

User = get_user_model()


# ==========================================
# Helpers & Security Utilities
# ==========================================

def generate_password_setup_url(request, user):
    """
    Generates a secure, cryptographically signed one-time activation / password-setup URL
    using Django's default_token_generator and urlsafe base64 encoding.
    """
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    relative_url = reverse("followup_setup_password", kwargs={"uidb64": uidb64, "token": token})
    return request.build_absolute_uri(relative_url)


# ==========================================
# Permission Decorators
# ==========================================

def admin_required(view_func):
    """Decorator ensuring administrator privileges."""
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")
        if not (request.user.is_admin_role or request.user.is_superuser):
            return HttpResponseForbidden("Access Denied: Administrator privileges required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def followup_required(view_func):
    """Decorator ensuring user is a Follow-up Employee or Administrator."""
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")
        if not (request.user.is_followup_role or request.user.is_admin_role or request.user.is_superuser):
            return HttpResponseForbidden("Access Denied: Follow-up Employee authorization required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


# ==========================================
# Part A: Administrator Follow-up Staff Views
# ==========================================

@login_required
@admin_required
def admin_followup_list_view(request):
    """
    Administrator view: Lists all follow-up staff with assigned shop counts and search/filter.
    URL: /admin-dashboard/followups/
    """
    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all").strip().lower()

    # Query all users with FOLLOWUP or EMPLOYEE role
    queryset = User.objects.filter(
        role__in=[User.Role.FOLLOWUP, User.Role.EMPLOYEE]
    ).annotate(
        assigned_count=Count("followup_assignments", filter=Q(followup_assignments__is_active=True), distinct=True),
        activity_count=Count("followup_activities", distinct=True),
        updates_count=Count("logged_customer_updates", distinct=True)
    ).order_by("-date_joined")

    if search_query:
        queryset = queryset.filter(
            Q(username__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(email__icontains=search_query)
        )

    if status_filter == "active":
        queryset = queryset.filter(is_active=True)
    elif status_filter == "inactive":
        queryset = queryset.filter(is_active=False)

    paginator = Paginator(queryset, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Dynamic totals for summary stats
    base_qs = User.objects.filter(role__in=[User.Role.FOLLOWUP, User.Role.EMPLOYEE])
    total_count = base_qs.count()
    active_count = base_qs.filter(is_active=True).count()
    inactive_count = base_qs.filter(is_active=False).count()

    context = {
        "employees": page_obj,
        "search_query": search_query,
        "status_filter": status_filter,
        "total_count": total_count,
        "active_count": active_count,
        "inactive_count": inactive_count,
        "active_tab": "followups",
    }
    return render(request, "admin_dashboard/followup_list.html", context)


@login_required
@admin_required
def admin_followup_add_view(request):
    """
    Administrator view: Register a new follow-up employee and assign multiple shops.
    Generates a secure password-setup workflow so the employee can set their own password.
    URL: /admin-dashboard/followups/add/
    """
    if request.method == "POST":
        form = FollowUpEmployeeForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                employee = form.save(commit=False)
                employee.role = User.Role.FOLLOWUP
                password = form.cleaned_data.get("password")
                if password:
                    employee.set_password(password)
                    employee.assigned_password = password
                else:
                    employee.set_unusable_password()
                employee.save()

                # Assign selected shops
                selected_shops = form.cleaned_data.get("assigned_shops", [])
                for shop in selected_shops:
                    FollowUpAssignment.objects.create(
                        employee=employee,
                        shop=shop,
                        assigned_by=request.user,
                        is_active=True
                    )

            # Generate secure password setup URL
            setup_url = generate_password_setup_url(request, employee)
            request.session[f"setup_url_{employee.pk}"] = setup_url

            messages.success(
                request,
                f"Follow-up employee '{employee.get_full_name() or employee.username}' created with {len(selected_shops)} assigned shop(s). Secure password setup link generated."
            )
            return redirect("admin_followup_detail", pk=employee.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = FollowUpEmployeeForm()

    all_shops = Shop.objects.all().order_by("name")
    context = {
        "form": form,
        "all_shops": all_shops,
        "is_create": True,
        "active_tab": "followups",
    }
    return render(request, "admin_dashboard/followup_form.html", context)


@login_required
@admin_required
def admin_followup_detail_view(request, pk):
    """
    Administrator view: Inspect follow-up employee profile, assigned shops, password setup link, and activity timeline.
    URL: /admin-dashboard/followups/<int:pk>/
    """
    employee = get_object_or_404(
        User.objects.filter(role__in=[User.Role.FOLLOWUP, User.Role.EMPLOYEE]),
        pk=pk
    )

    # Active shop assignments
    assignments = employee.followup_assignments.select_related("shop").order_by("-assigned_at")
    assigned_shops = [a.shop for a in assignments if a.is_active]

    # Customers in assigned shops
    total_customers = Customer.objects.filter(shop__in=assigned_shops).count()

    # Activities logged by this employee
    activities = employee.followup_activities.select_related("customer", "shop").order_by("-created_at")

    # Complete history of customer updations done by this follow-up employee
    from customers.models import CustomerUpdateHistory
    customer_updates = CustomerUpdateHistory.objects.filter(
        user=employee
    ).select_related("customer", "customer__shop").prefetch_related("customer__applications").order_by("-created_at")

    total_updates_count = customer_updates.count()
    unique_customers_count = customer_updates.values("customer_id").distinct().count()

    # Generate live password setup / reset URL
    setup_url = request.session.pop(f"setup_url_{employee.pk}", None)
    if not setup_url:
        setup_url = generate_password_setup_url(request, employee)

    context = {
        "employee": employee,
        "assignments": assignments,
        "assigned_shops": assigned_shops,
        "total_customers": total_customers,
        "activities": activities,
        "customer_updates": customer_updates,
        "total_updates_count": total_updates_count,
        "unique_customers_count": unique_customers_count,
        "setup_url": setup_url,
        "active_tab": "followups",
        "active_page": "followups",
    }
    return render(request, "admin_dashboard/followup_detail.html", context)


@login_required
@admin_required
def admin_followup_edit_view(request, pk):
    """
    Administrator view: Edit follow-up employee profile, phone, password, and shop assignments.
    Immediately reflects new shop assignments on the employee's dashboard.
    URL: /admin-dashboard/followups/<int:pk>/edit/
    """
    employee = get_object_or_404(
        User.objects.filter(role__in=[User.Role.FOLLOWUP, User.Role.EMPLOYEE]),
        pk=pk
    )

    if request.method == "POST":
        form = FollowUpEmployeeForm(request.POST, instance=employee)
        if form.is_valid():
            with transaction.atomic():
                employee = form.save(commit=False)
                new_password = form.cleaned_data.get("password")
                if new_password:
                    employee.set_password(new_password)
                    employee.assigned_password = new_password
                employee.save()

                # Synchronize assigned shops
                selected_shops = set(form.cleaned_data.get("assigned_shops", []))
                current_assignments = {a.shop: a for a in employee.followup_assignments.all()}

                # Deactivate removed shops
                for shop, assignment in current_assignments.items():
                    if shop not in selected_shops and assignment.is_active:
                        assignment.is_active = False
                        assignment.save(update_fields=["is_active"])

                # Add or reactivate newly selected shops
                for shop in selected_shops:
                    if shop in current_assignments:
                        assignment = current_assignments[shop]
                        if not assignment.is_active:
                            assignment.is_active = True
                            assignment.save(update_fields=["is_active"])
                    else:
                        FollowUpAssignment.objects.create(
                            employee=employee,
                            shop=shop,
                            assigned_by=request.user,
                            is_active=True
                        )

            messages.success(request, f"Updated profile and shop assignments for '{employee.username}'.")
            return redirect("admin_followup_detail", pk=employee.pk)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = FollowUpEmployeeForm(instance=employee)

    all_shops = Shop.objects.all().order_by("name")
    context = {
        "form": form,
        "employee": employee,
        "all_shops": all_shops,
        "is_create": False,
        "active_tab": "followups",
    }
    return render(request, "admin_dashboard/followup_form.html", context)


@require_POST
@login_required
@admin_required
def admin_followup_status_view(request, pk):
    """
    Administrator view: Toggle active/inactive status of a follow-up employee.
    URL: /admin-dashboard/followups/<int:pk>/status/
    """
    employee = get_object_or_404(
        User.objects.filter(role__in=[User.Role.FOLLOWUP, User.Role.EMPLOYEE]),
        pk=pk
    )
    employee.is_active = not employee.is_active
    employee.save(update_fields=["is_active"])

    status_str = "activated" if employee.is_active else "deactivated"
    messages.success(request, f"Follow-up employee '{employee.username}' has been {status_str}.")

    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("admin_followup_detail", pk=employee.pk)


@require_POST
@login_required
@admin_required
def admin_followup_reset_password_view(request, pk):
    """
    Administrator view: Re-generate and refresh the secure password-setup / reset link.
    URL: /admin-dashboard/followups/<int:pk>/reset-password/
    """
    employee = get_object_or_404(
        User.objects.filter(role__in=[User.Role.FOLLOWUP, User.Role.EMPLOYEE]),
        pk=pk
    )
    setup_url = generate_password_setup_url(request, employee)
    request.session[f"setup_url_{employee.pk}"] = setup_url
    messages.success(request, f"Generated fresh secure password setup link for '{employee.username}'.")
    return redirect("admin_followup_detail", pk=employee.pk)


@require_POST
@login_required
@admin_required
def admin_followup_delete_view(request, pk):
    """
    Administrator view: Permanently deletes a follow-up staff user and all their assignments from the database.
    URL: /admin-dashboard/followups/<int:pk>/delete/
    """
    employee = get_object_or_404(
        User.objects.filter(role__in=[User.Role.FOLLOWUP, User.Role.EMPLOYEE]),
        pk=pk
    )
    staff_name = employee.get_full_name() or employee.username
    with transaction.atomic():
        employee.delete()

    messages.success(request, f"Follow-up person '{staff_name}' has been permanently deleted from the database.")
    return redirect("admin_followup_list")



# =======================================================
# Part B: Public Employee Password Setup Workflow
# =======================================================

def followup_setup_password_view(request, uidb64, token):
    """
    Public employee activation view:
    Validates cryptographic token and allows employee to set their own password securely.
    URL: /followups/setup-password/<uidb64>/<token>/
    """
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        target_user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        target_user = None

    if target_user is None or not default_token_generator.check_token(target_user, token):
        return render(request, "followups/setup_password_invalid.html", status=400)

    if request.method == "POST":
        form = FollowUpPasswordSetupForm(request.POST)
        if form.is_valid():
            new_password = form.cleaned_data["new_password"]
            target_user.set_password(new_password)
            target_user.assigned_password = new_password
            target_user.is_active = True
            target_user.save()

            login(request, target_user)
            messages.success(
                request,
                f"Welcome, {target_user.get_full_name() or target_user.username}! Your password has been configured successfully."
            )
            return redirect("followup_dashboard")
    else:
        form = FollowUpPasswordSetupForm()

    context = {
        "form": form,
        "target_user": target_user,
    }
    return render(request, "followups/setup_password.html", context)


# =======================================================
# Part C: Follow-up Employee Portal / Scoped Dashboard
# =======================================================

@login_required
@followup_required
def followup_dashboard_view(request):
    """
    Dedicated Follow-up Employee Dashboard.
    Enforces strict scoping: Follow-up staff can access ONLY their assigned shops
    and the authorized customer details belonging to those shops.
    Displays:
    - Customer Due Data (Device, Monthly EMI, Due Day, Next Due Date, Follow-up Status)
    - Two distinct clickable filters: Today's Due and Pending Dues (plus All)
    - Quick status update logging (Paid, Not Paid, Need two days time [flagged])
    - Assigned shops and Scoped Metrics
    URL: /followups/ or /followups/dashboard/
    """
    import calendar
    from datetime import timedelta
    from decimal import Decimal

    user = request.user
    today = timezone.localdate()

def _get_followup_assigned_shops(user):
    """Returns queryset of active retail shops assigned to the given user."""
    if user.is_admin_role or user.is_superuser:
        return Shop.objects.filter(is_active=True).order_by("name")
    return Shop.objects.filter(
        followup_assignments__employee=user,
        followup_assignments__is_active=True,
        is_active=True
    ).order_by("name")


def _calculate_customer_followup_data(customer, today, upcoming_days=7):
    """
    Computes accurate payment metrics and categorization for a customer.
    - months_unpaid: Count of unpaid schedule installments whose due_date < today
    - outstanding_amount: Total unpaid customer balance
    - original_due_date: Scheduled due date of next unpaid installment
    - calling_priority: 1 for Today's Due, 2 for Upcoming Due, 3 for Overdue/Pending, 4 for On Track
    """
    from datetime import timedelta
    from decimal import Decimal

    apps = list(customer.applications.all())
    app = apps[0] if apps else None

    if not app:
        return {
            "mobile_model": "—",
            "monthly_emi": Decimal("0.00"),
            "monthly_due_day": None,
            "next_due_date": None,
            "original_due_date": None,
            "outstanding_amount": Decimal("0.00"),
            "months_unpaid": 0,
            "months_unpaid_text": "0 Months (On Track)",
            "is_today_due": False,
            "is_upcoming_due": False,
            "is_pending_due": False,
            "calling_priority": 4,
        }

    app.generate_default_schedule_if_missing()
    next_unpaid = app.next_unpaid_schedule
    next_due_date = next_unpaid.due_date if next_unpaid else None
    outstanding_amount = app.customer_remaining_balance

    # Accurate count of overdue unpaid installments
    overdue_scheds = app.emi_schedules.filter(is_paid=False, due_date__lt=today)
    months_unpaid = overdue_scheds.count()

    due_today_scheds = app.emi_schedules.filter(is_paid=False, due_date=today)
    is_due_today_sched = due_today_scheds.exists()

    upcoming_scheds = app.emi_schedules.filter(
        is_paid=False,
        due_date__gt=today,
        due_date__lte=today + timedelta(days=upcoming_days)
    )
    is_upcoming_sched = upcoming_scheds.exists()

    if months_unpaid > 0:
        months_unpaid_text = f"{months_unpaid} Month{'s' if months_unpaid > 1 else ''} Pending"
    elif is_due_today_sched:
        months_unpaid_text = "Due Today (1 Month)"
    else:
        months_unpaid_text = "0 Months (On Track)"

    # Latest FollowUpActivity outcome & scheduled callback
    latest_act = customer.followup_activities.first()
    outcome = latest_act.outcome if latest_act else None
    act_status = latest_act.status if latest_act else None
    act_next_date = latest_act.next_followup_date if latest_act else None
    is_flagged = latest_act.is_flagged if latest_act else False

    # 1. Today's Due
    is_today_due = False
    if is_due_today_sched and outcome != FollowUpActivity.Outcome.PAID:
        is_today_due = True
    elif act_next_date == today and act_status == FollowUpActivity.Status.PENDING:
        is_today_due = True
    elif outcome == FollowUpActivity.Outcome.NEED_TWO_DAYS and act_next_date == today:
        is_today_due = True

    # 2. Upcoming Dues (next 7 days, configurable)
    is_upcoming_due = False
    if not is_today_due:
        if is_upcoming_sched and outcome != FollowUpActivity.Outcome.PAID:
            is_upcoming_due = True
        elif act_next_date and today < act_next_date <= today + timedelta(days=upcoming_days) and act_status == FollowUpActivity.Status.PENDING:
            is_upcoming_due = True

    # 3. Pending Dues (overdue installments or flagged for callback)
    is_pending_due = False
    if months_unpaid > 0 and outcome != FollowUpActivity.Outcome.PAID:
        is_pending_due = True
    elif outcome in (FollowUpActivity.Outcome.NOT_PAID, FollowUpActivity.Outcome.NEED_TWO_DAYS):
        is_pending_due = True
    elif is_flagged:
        is_pending_due = True
    elif act_next_date and act_next_date < today and act_status == FollowUpActivity.Status.PENDING:
        is_pending_due = True

    # Priority Calling Order:
    # Priority 1: Today's Due
    # Priority 2: Upcoming Dues
    # Priority 3: Pending Dues
    # Priority 4: On Track / Normal
    if is_today_due:
        calling_priority = 1
    elif is_upcoming_due:
        calling_priority = 2
    elif is_pending_due:
        calling_priority = 3
    else:
        calling_priority = 4

    return {
        "mobile_model": app.mobile_model,
        "monthly_emi": app.monthly_emi,
        "monthly_due_day": app.monthly_due_day,
        "next_due_date": next_due_date,
        "original_due_date": next_due_date,
        "outstanding_amount": outstanding_amount,
        "months_unpaid": months_unpaid,
        "months_unpaid_text": months_unpaid_text,
        "is_today_due": is_today_due,
        "is_upcoming_due": is_upcoming_due,
        "is_pending_due": is_pending_due,
        "calling_priority": calling_priority,
    }


@login_required
@followup_required
def followup_dashboard_view(request):
    """
    1. FOLLOW-UP PERSON HOMEPAGE
    Clean, minimal fintech homepage displaying:
    - Welcome section
    - Assigned shops summary
    - Customer count
    - Pending follow-ups count
    - Today's Due count
    - Upcoming Dues count
    - Pending Dues count
    - Completed Follow-ups count
    - Top 5 Assigned Shops (dynamically sorted by highest customer count)
    - Daily Task Priority Section (high priority calling tasks)
    URL: /followups/ or /followups/dashboard/
    """
    from datetime import date, timedelta
    from decimal import Decimal

    user = request.user
    today = timezone.localdate()
    assigned_shops = _get_followup_assigned_shops(user)

    # Scoped Customers in assigned shops
    customers_qs = Customer.objects.filter(
        shop__in=assigned_shops
    ).select_related("shop").prefetch_related("applications", "followup_activities")

    # Scoped Activities Base
    activities_base = FollowUpActivity.objects.filter(
        shop__in=assigned_shops
    ).select_related("customer", "shop", "employee")

    # Pending follow-ups
    pending_followups = activities_base.filter(
        status=FollowUpActivity.Status.PENDING
    ).order_by("next_followup_date", "-created_at")

    # Completed follow-ups
    completed_followups = activities_base.filter(
        status=FollowUpActivity.Status.COMPLETED
    ).order_by("-created_at")

    # Upcoming tasks
    upcoming_tasks = activities_base.filter(
        status=FollowUpActivity.Status.PENDING,
        next_followup_date__isnull=False,
        next_followup_date__gte=today
    ).order_by("next_followup_date")

    # Process due data and rank shops
    customer_list = []
    today_due_count = 0
    upcoming_due_count = 0
    pending_due_count = 0
    total_outstanding_sum = Decimal("0.00")

    # Calculate Top 5 Assigned Shops by customer count
    shop_stats = []
    for s in assigned_shops:
        s_customers = Customer.objects.filter(shop=s)
        c_count = s_customers.count()
        shop_stats.append({
            "shop": s,
            "customer_count": c_count,
        })
    shop_stats.sort(key=lambda x: (x["customer_count"], x["shop"].name), reverse=True)
    top_5_shops = [item["shop"] for item in shop_stats[:5]]

    for customer in customers_qs.order_by("-created_at"):
        metrics = _calculate_customer_followup_data(customer, today, upcoming_days=7)

        customer.mobile_model = metrics["mobile_model"]
        customer.monthly_emi = metrics["monthly_emi"]
        customer.monthly_due_day = metrics["monthly_due_day"]
        customer.next_due_date = metrics["next_due_date"]
        customer.original_due_date = metrics["original_due_date"]
        customer.outstanding_amount = metrics["outstanding_amount"]
        customer.months_unpaid = metrics["months_unpaid"]
        customer.months_unpaid_text = metrics["months_unpaid_text"]
        customer.is_today_due = metrics["is_today_due"]
        customer.is_upcoming_due = metrics["is_upcoming_due"]
        customer.is_pending_due = metrics["is_pending_due"]
        customer.calling_priority = metrics["calling_priority"]
        customer.latest_activity = customer.followup_activities.first()

        total_outstanding_sum += metrics["outstanding_amount"]

        if metrics["is_today_due"]:
            today_due_count += 1
        if metrics["is_upcoming_due"]:
            upcoming_due_count += 1
        if metrics["is_pending_due"]:
            pending_due_count += 1

        customer_list.append(customer)

    # Sort customers by Calling Priority: Today's Due first, then Upcoming Due, then Pending Due
    max_date = date(2099, 12, 31)
    customer_list.sort(key=lambda c: (c.calling_priority, c.next_due_date or max_date, c.name))

    # Daily Task Priority queue for the homepage (Top urgent tasks)
    priority_tasks = [c for c in customer_list if c.calling_priority in (1, 2, 3)][:15]
    if not priority_tasks:
        priority_tasks = customer_list[:10]

    # Scoped Metrics
    total_assigned_shops = assigned_shops.count()
    total_customers = len(customer_list)
    total_pending = pending_followups.count()
    total_completed = completed_followups.count()
    total_upcoming = upcoming_tasks.count()

    context = {
        "assigned_shops": assigned_shops,
        "total_assigned_shops": total_assigned_shops,
        "total_customers": total_customers,
        "total_pending": total_pending,
        "total_completed": total_completed,
        "total_upcoming": total_upcoming,
        "today_due_count": today_due_count,
        "upcoming_due_count": upcoming_due_count,
        "pending_due_count": pending_due_count,
        "total_outstanding_sum": total_outstanding_sum,
        "top_5_shops": top_5_shops,
        "priority_tasks": priority_tasks,
        "today": today,
        # Backwards compatibility context keys for existing tests
        "customers": priority_tasks,
        "pending_followups": pending_followups[:10],
        "completed_followups": completed_followups[:10],
    }
    return render(request, "followups/portal/dashboard.html", context)


@login_required
@followup_required
def followup_shops_list_view(request):
    """
    2. ASSIGNED SHOPS – SEPARATE PAGE
    Displays every shop assigned to the logged-in follow-up person (5, 20, 50+ shops).
    Features:
    - Search by shop name and phone number
    - Number of assigned customers
    - Outstanding payment summary
    - Pagination and sorting by customer count
    URL: /followups/shops/
    """
    from decimal import Decimal

    user = request.user
    today = timezone.localdate()
    assigned_shops = _get_followup_assigned_shops(user)

    search_query = request.GET.get("q", "").strip()
    if search_query:
        assigned_shops = assigned_shops.filter(
            Q(name__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(address__icontains=search_query) |
            Q(owner_name__icontains=search_query)
        )

    # Calculate metrics for each assigned shop
    shop_items = []
    for s in assigned_shops:
        customers = Customer.objects.filter(shop=s).prefetch_related("applications")
        c_count = customers.count()
        today_due_c = 0
        overdue_c = 0
        shop_outstanding = Decimal("0.00")

        for c in customers:
            app = c.applications.first()
            if app:
                shop_outstanding += app.customer_remaining_balance
                if app.emi_schedules.filter(is_paid=False, due_date=today).exists():
                    today_due_c += 1
                if app.emi_schedules.filter(is_paid=False, due_date__lt=today).exists():
                    overdue_c += 1

        shop_items.append({
            "shop": s,
            "customer_count": c_count,
            "today_due_count": today_due_c,
            "overdue_count": overdue_c,
            "total_outstanding": shop_outstanding,
        })

    # Sort descending by customer count
    shop_items.sort(key=lambda x: (x["customer_count"], x["shop"].name), reverse=True)

    paginator = Paginator(shop_items, 12)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "shops_page": page_obj,
        "total_assigned_count": len(shop_items),
        "search_query": search_query,
        "today": today,
    }
    return render(request, "followups/portal/shops_list.html", context)


@login_required
@followup_required
def followup_shop_detail_view(request, shop_id):
    """
    3. INDIVIDUAL SHOP DETAILS PAGE
    Opens when clicking any shop from Top 5 on homepage or from All Assigned Shops page.
    Displays:
    - Shop contact and owner information
    - Shop-wise metrics (Total customers, Today's Due, Upcoming Dues, Pending Dues, Outstanding Amount)
    - Shop-wise customer list with search and payment category filters
    URL: /followups/shops/<int:shop_id>/
    """
    from datetime import date
    from decimal import Decimal

    user = request.user
    today = timezone.localdate()
    assigned_shops = _get_followup_assigned_shops(user)

    # Server-side tenant security check: Employee cannot access unassigned shops
    shop = get_object_or_404(Shop, pk=shop_id)
    if shop not in assigned_shops:
        return HttpResponseForbidden("Access Denied: You are not authorized to view this shop's data.")

    customers_qs = Customer.objects.filter(shop=shop).prefetch_related("applications", "followup_activities")

    search_query = request.GET.get("q", "").strip()
    if search_query:
        customers_qs = customers_qs.filter(
            Q(name__icontains=search_query) |
            Q(primary_mobile__icontains=search_query) |
            Q(secondary_mobile__icontains=search_query)
        )

    # Multi-select dues filter
    filter_due_today = (request.GET.get("due_today") == "1" or request.GET.get("due_filter") == "today")
    filter_upcoming_due = (request.GET.get("upcoming_due") == "1" or request.GET.get("due_filter") == "upcoming")
    filter_pending_due = (request.GET.get("pending_due") == "1" or request.GET.get("due_filter") == "pending")

    customer_list = []
    today_due_count = 0
    upcoming_due_count = 0
    pending_due_count = 0
    total_shop_outstanding = Decimal("0.00")

    for customer in customers_qs.order_by("-created_at"):
        metrics = _calculate_customer_followup_data(customer, today, upcoming_days=7)

        customer.mobile_model = metrics["mobile_model"]
        customer.monthly_emi = metrics["monthly_emi"]
        customer.monthly_due_day = metrics["monthly_due_day"]
        customer.next_due_date = metrics["next_due_date"]
        customer.original_due_date = metrics["original_due_date"]
        customer.outstanding_amount = metrics["outstanding_amount"]
        customer.months_unpaid = metrics["months_unpaid"]
        customer.months_unpaid_text = metrics["months_unpaid_text"]
        customer.is_today_due = metrics["is_today_due"]
        customer.is_upcoming_due = metrics["is_upcoming_due"]
        customer.is_pending_due = metrics["is_pending_due"]
        customer.calling_priority = metrics["calling_priority"]
        customer.latest_activity = customer.followup_activities.first()

        total_shop_outstanding += metrics["outstanding_amount"]

        if metrics["is_today_due"]:
            today_due_count += 1
        if metrics["is_upcoming_due"]:
            upcoming_due_count += 1
        if metrics["is_pending_due"]:
            pending_due_count += 1

        customer_list.append(customer)

    max_date = date(2099, 12, 31)
    customer_list.sort(key=lambda c: (c.calling_priority, c.next_due_date or max_date, c.name))

    has_dues_filter = (filter_due_today or filter_upcoming_due or filter_pending_due)
    if has_dues_filter:
        filtered_customers = [
            c for c in customer_list
            if (filter_due_today and c.is_today_due) or
               (filter_upcoming_due and c.is_upcoming_due) or
               (filter_pending_due and c.is_pending_due)
        ]
    else:
        filtered_customers = customer_list

    paginator = Paginator(filtered_customers, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "shop": shop,
        "customers": page_obj,
        "total_shop_customers": len(customer_list),
        "today_due_count": today_due_count,
        "upcoming_due_count": upcoming_due_count,
        "pending_due_count": pending_due_count,
        "total_shop_outstanding": total_shop_outstanding,
        "filter_due_today": filter_due_today,
        "filter_upcoming_due": filter_upcoming_due,
        "filter_pending_due": filter_pending_due,
        "has_dues_filter": has_dues_filter,
        "search_query": search_query,
        "today": today,
    }
    return render(request, "followups/portal/shop_detail.html", context)


@login_required
@followup_required
def followup_tasks_view(request):
    """
    4. DAILY PAYMENT TASKS & CUSTOMER DIRECTORY
    Organises follow-up work into 3 priority categories:
    1. Today's Due
    2. Upcoming Dues (next 7 days)
    3. Pending Dues (overdue unpaid)
    Features:
    - Simultaneous multi-select payment category filters (can select all 3 without duplicates)
    - Shop filter dropdown
    - Search by customer name, phone, address
    - Redesigned customer table (No 'Due Today' column)
    URL: /followups/tasks/
    """
    from datetime import date
    from decimal import Decimal

    user = request.user
    today = timezone.localdate()
    assigned_shops = _get_followup_assigned_shops(user)

    customers_qs = Customer.objects.filter(
        shop__in=assigned_shops
    ).select_related("shop").prefetch_related("applications", "followup_activities")

    search_query = request.GET.get("q", "").strip()
    shop_filter = request.GET.get("shop_id", "").strip()

    if shop_filter:
        customers_qs = customers_qs.filter(shop_id=shop_filter)

    if search_query:
        customers_qs = customers_qs.filter(
            Q(name__icontains=search_query) |
            Q(primary_mobile__icontains=search_query) |
            Q(secondary_mobile__icontains=search_query) |
            Q(home_address__icontains=search_query) |
            Q(work_address__icontains=search_query)
        )

    # Multi-select dues filter
    due_filter = request.GET.get("due_filter", "").strip().lower()
    filter_due_today = (
        request.GET.get("due_today") == "1" or
        "today" in request.GET.getlist("due_filter") or
        due_filter == "today"
    )
    filter_upcoming_due = (
        request.GET.get("upcoming_due") == "1" or
        "upcoming" in request.GET.getlist("due_filter") or
        due_filter == "upcoming"
    )
    filter_pending_due = (
        request.GET.get("pending_due") == "1" or
        "pending" in request.GET.getlist("due_filter") or
        due_filter == "pending"
    )

    customer_list = []
    today_due_count = 0
    upcoming_due_count = 0
    pending_due_count = 0
    all_count = 0
    total_outstanding_sum = Decimal("0.00")

    for customer in customers_qs.order_by("-created_at"):
        all_count += 1
        metrics = _calculate_customer_followup_data(customer, today, upcoming_days=7)

        customer.mobile_model = metrics["mobile_model"]
        customer.monthly_emi = metrics["monthly_emi"]
        customer.monthly_due_day = metrics["monthly_due_day"]
        customer.next_due_date = metrics["next_due_date"]
        customer.original_due_date = metrics["original_due_date"]
        customer.outstanding_amount = metrics["outstanding_amount"]
        customer.months_unpaid = metrics["months_unpaid"]
        customer.months_unpaid_text = metrics["months_unpaid_text"]
        customer.is_today_due = metrics["is_today_due"]
        customer.is_upcoming_due = metrics["is_upcoming_due"]
        customer.is_pending_due = metrics["is_pending_due"]
        customer.calling_priority = metrics["calling_priority"]
        customer.latest_activity = customer.followup_activities.first()

        total_outstanding_sum += metrics["outstanding_amount"]

        if metrics["is_today_due"]:
            today_due_count += 1
        if metrics["is_upcoming_due"]:
            upcoming_due_count += 1
        if metrics["is_pending_due"]:
            pending_due_count += 1

        customer_list.append(customer)

    # Sort customers by Calling Priority: Today's Due first, then Upcoming Due, then Pending Due
    max_date = date(2099, 12, 31)
    customer_list.sort(key=lambda c: (c.calling_priority, c.next_due_date or max_date, c.name))

    # Apply simultaneous multi-select filter
    has_dues_filter = (filter_due_today or filter_upcoming_due or filter_pending_due)
    if has_dues_filter:
        filtered_customers = [
            c for c in customer_list
            if (filter_due_today and c.is_today_due) or
               (filter_upcoming_due and c.is_upcoming_due) or
               (filter_pending_due and c.is_pending_due)
        ]
    else:
        filtered_customers = customer_list

    paginator = Paginator(filtered_customers, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "customers": page_obj,
        "assigned_shops": assigned_shops,
        "shop_filter": shop_filter,
        "search_query": search_query,
        "filter_due_today": filter_due_today,
        "filter_upcoming_due": filter_upcoming_due,
        "filter_pending_due": filter_pending_due,
        "has_dues_filter": has_dues_filter,
        "today_due_count": today_due_count,
        "upcoming_due_count": upcoming_due_count,
        "pending_due_count": pending_due_count,
        "all_count": all_count,
        "total_outstanding_sum": total_outstanding_sum,
        "today": today,
    }
    return render(request, "followups/portal/tasks.html", context)


@login_required
@followup_required
def followup_pending_view(request):
    """
    5. DEDICATED PENDING FOLLOW-UPS PAGE
    Shows only customers who require a follow-up action.
    Columns:
    1. Customer name
    2. Shop name
    3. Primary phone number
    4. Secondary phone number
    5. Device/model
    6. Monthly EMI
    7. Total outstanding amount
    8. Number of months unpaid
    9. Original EMI due date
    10. Last follow-up date
    11. Next follow-up date
    12. Last follow-up outcome
    13. Action button (Update Status)
    URL: /followups/pending/
    """
    user = request.user
    today = timezone.localdate()
    assigned_shops = _get_followup_assigned_shops(user)

    activities_qs = FollowUpActivity.objects.filter(
        shop__in=assigned_shops,
        status=FollowUpActivity.Status.PENDING
    ).select_related("customer", "shop", "employee").order_by("next_followup_date", "-created_at")

    shop_filter = request.GET.get("shop_id", "").strip()
    if shop_filter:
        activities_qs = activities_qs.filter(shop_id=shop_filter)

    search_query = request.GET.get("q", "").strip()
    if search_query:
        activities_qs = activities_qs.filter(
            Q(customer__name__icontains=search_query) |
            Q(customer__primary_mobile__icontains=search_query) |
            Q(customer__secondary_mobile__icontains=search_query) |
            Q(shop__name__icontains=search_query)
        )

    # Date filter: today, overdue, upcoming, all
    date_filter = request.GET.get("date_filter", "").strip().lower()
    if date_filter == "today":
        activities_qs = activities_qs.filter(next_followup_date=today)
    elif date_filter == "overdue":
        activities_qs = activities_qs.filter(next_followup_date__lt=today)
    elif date_filter == "upcoming":
        from datetime import timedelta
        activities_qs = activities_qs.filter(next_followup_date__gt=today, next_followup_date__lte=today + timedelta(days=7))

    # Enrich each pending activity with accurate application and schedule data
    pending_items = []
    for act in activities_qs:
        cust = act.customer
        metrics = _calculate_customer_followup_data(cust, today, upcoming_days=7)
        cust.mobile_model = metrics["mobile_model"]
        cust.monthly_emi = metrics["monthly_emi"]
        cust.monthly_due_day = metrics["monthly_due_day"]
        cust.next_due_date = metrics["next_due_date"]
        cust.original_due_date = metrics["original_due_date"]
        cust.outstanding_amount = metrics["outstanding_amount"]
        cust.months_unpaid = metrics["months_unpaid"]
        cust.months_unpaid_text = metrics["months_unpaid_text"]
        cust.latest_activity = act

        pending_items.append({
            "activity": act,
            "customer": cust,
            "shop": act.shop,
            "mobile_model": metrics["mobile_model"],
            "monthly_emi": metrics["monthly_emi"],
            "outstanding_amount": metrics["outstanding_amount"],
            "months_unpaid": metrics["months_unpaid"],
            "months_unpaid_text": metrics["months_unpaid_text"],
            "original_due_date": metrics["original_due_date"],
            "last_followup_date": act.created_at,
            "next_followup_date": act.next_followup_date,
            "outcome_display": act.get_outcome_display(),
        })

    paginator = Paginator(pending_items, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "pending_page": page_obj,
        "assigned_shops": assigned_shops,
        "total_pending_count": len(pending_items),
        "shop_filter": shop_filter,
        "search_query": search_query,
        "date_filter": date_filter,
        "today": today,
    }
    return render(request, "followups/portal/pending_list.html", context)



@require_POST
@login_required
@followup_required
def followup_quick_update_view(request, pk):
    """
    Follow-up Employee Portal: Quick log update from the dashboard table.
    Supports outcomes:
    - PAID: Mark as Paid, completed
    - NOT_PAID: Mark as Not Paid, pending
    - NEED_TWO_DAYS: Need two days time (flags customer for callback in 2 days)
    URL: /followups/customers/<int:pk>/quick-update/
    """
    from datetime import timedelta
    user = request.user
    customer = get_object_or_404(Customer.objects.select_related("shop"), pk=pk)

    # Server-side tenant authorization check
    if not (user.is_admin_role or user.is_superuser):
        is_assigned = FollowUpAssignment.objects.filter(
            employee=user,
            shop=customer.shop,
            is_active=True
        ).exists()
        if not is_assigned:
            return HttpResponseForbidden("Access Denied: You are not authorized to update records for this customer.")

    outcome = request.POST.get("outcome", "").strip()
    notes = request.POST.get("notes", "").strip()
    promised_date_str = request.POST.get("promised_date", "").strip()

    valid_outcomes = [FollowUpActivity.Outcome.PAID, FollowUpActivity.Outcome.NOT_PAID, FollowUpActivity.Outcome.NEED_TWO_DAYS]
    if outcome not in valid_outcomes:
        outcome = FollowUpActivity.Outcome.NOT_PAID

    today = timezone.localdate()

    activity = FollowUpActivity(
        employee=user,
        customer=customer,
        shop=customer.shop,
        activity_type=FollowUpActivity.ActivityType.CALL,
        outcome=outcome,
        notes=notes,
    )
    if outcome == FollowUpActivity.Outcome.NEED_TWO_DAYS:
        activity.is_flagged = True
        activity.status = FollowUpActivity.Status.PENDING
        target_date = None
        if promised_date_str:
            try:
                from datetime import datetime as dt
                target_date = dt.strptime(promised_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                target_date = today + timedelta(days=2)
        else:
            target_date = today + timedelta(days=2)

        activity.next_followup_date = target_date
        days_needed = max(1, (target_date - today).days)
        update_msg = f"Need {days_needed} Days (Call on: {activity.next_followup_date.strftime('%b %d, %Y')})"
        if notes:
            update_msg += f" - {notes}"
        messages.warning(
            request,
            f"Flagged '{customer.name}' — Requested {days_needed} day{'s' if days_needed != 1 else ''}. Scheduled callback for {activity.next_followup_date.strftime('%b %d, %Y')}."
        )
    elif outcome == FollowUpActivity.Outcome.PAID:
        activity.is_flagged = False
        activity.status = FollowUpActivity.Status.COMPLETED
        activity.completed_at = timezone.now()
        update_msg = "Paid" + (f": {notes}" if notes else "")
        messages.success(request, f"Updated '{customer.name}' status to Paid.")
    else:  # NOT_PAID
        activity.is_flagged = False
        activity.status = FollowUpActivity.Status.PENDING
        update_msg = "Not Paid" + (f": {notes}" if notes else "")
        messages.info(request, f"Recorded '{customer.name}' as Not Paid. Remains in pending follow-up.")

    activity.save()

    # Synchronize Customer latest update status and CustomerUpdateHistory
    from customers.models import CustomerUpdateHistory
    customer.latest_update_status = update_msg
    customer.latest_update_at = timezone.now()
    customer.latest_update_by = user
    customer.save(update_fields=["latest_update_status", "latest_update_at", "latest_update_by", "updated_at"])

    CustomerUpdateHistory.objects.create(
        customer=customer,
        user=user,
        notes=update_msg
    )

    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("followup_dashboard")


@require_POST
@login_required
@followup_required
def followup_complete_activity_view(request, pk):
    """
    Follow-up Employee Portal: Mark a pending follow-up task as completed.
    Enforces shop-level authorization on the server side.
    URL: /followups/activities/<int:pk>/complete/
    """
    user = request.user
    activity = get_object_or_404(FollowUpActivity, pk=pk)

    # Server-side shop permission check
    if not (user.is_admin_role or user.is_superuser):
        is_assigned = FollowUpAssignment.objects.filter(
            employee=user,
            shop=activity.shop,
            is_active=True
        ).exists()
        if not is_assigned:
            return HttpResponseForbidden("Access Denied: You are not authorized to modify records for this shop.")

    activity.status = FollowUpActivity.Status.COMPLETED
    activity.outcome = FollowUpActivity.Outcome.PAID
    activity.is_flagged = False
    activity.completed_at = timezone.now()
    activity.save(update_fields=["status", "outcome", "is_flagged", "completed_at"])

    messages.success(request, f"Follow-up for '{activity.customer.name}' marked as completed.")
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("followup_dashboard")


@login_required
@followup_required
def followup_customer_detail_view(request, pk):
    """
    Follow-up Employee Portal: Customer Detail & Follow-up Action Log.
    Strict tenant enforcement: Rejects requests with 403 Forbidden if customer's shop
    is not in the employee's assigned shops.
    URL: /followups/customers/<int:pk>/
    """
    customer = get_object_or_404(Customer.objects.select_related("shop"), pk=pk)
    user = request.user

    # Tenant verification
    if not (user.is_admin_role or user.is_superuser):
        is_assigned = FollowUpAssignment.objects.filter(
            employee=user,
            shop=customer.shop,
            is_active=True
        ).exists()
        if not is_assigned:
            return HttpResponseForbidden("Access Denied: You are not assigned to monitor this customer's shop.")

    # Handle logging new follow-up interaction
    if request.method == "POST":
        form = FollowUpActivityForm(request.POST)
        if form.is_valid():
            activity = form.save(commit=False)
            activity.employee = user
            activity.customer = customer
            activity.shop = customer.shop
            if activity.outcome == FollowUpActivity.Outcome.NEED_TWO_DAYS:
                activity.is_flagged = True
                activity.status = FollowUpActivity.Status.PENDING
                if not activity.next_followup_date:
                    from datetime import timedelta
                    activity.next_followup_date = timezone.localdate() + timedelta(days=2)
                messages.warning(
                    request,
                    f"Customer '{customer.name}' flagged for 2-day callback on {activity.next_followup_date.strftime('%b %d, %Y')}."
                )
            elif activity.outcome == FollowUpActivity.Outcome.PAID:
                activity.is_flagged = False
                activity.status = FollowUpActivity.Status.COMPLETED
                if not activity.completed_at:
                    activity.completed_at = timezone.now()
                messages.success(request, f"Follow-up recorded: Marked '{customer.name}' as Paid.")
            else:
                activity.is_flagged = False
                activity.status = FollowUpActivity.Status.PENDING
                messages.info(request, f"Follow-up recorded: Marked '{customer.name}' as Not Paid.")
            activity.save()

            # Synchronize customer update status
            from customers.models import CustomerUpdateHistory
            note_str = f"{activity.get_outcome_display()}"
            if activity.notes:
                note_str += f": {activity.notes}"
            customer.latest_update_status = note_str
            customer.latest_update_at = timezone.now()
            customer.latest_update_by = user
            customer.save(update_fields=["latest_update_status", "latest_update_at", "latest_update_by", "updated_at"])

            CustomerUpdateHistory.objects.create(
                customer=customer,
                user=user,
                notes=note_str
            )

            return redirect("followup_customer_detail", pk=customer.pk)
    else:
        form = FollowUpActivityForm(initial={"outcome": FollowUpActivity.Outcome.NOT_PAID})

    applications = customer.applications.prefetch_related("documents", "financier_payments").order_by("-created_at")
    activities = customer.followup_activities.select_related("employee").order_by("-created_at")
    latest_activity = activities.first()
    is_flagged = latest_activity.is_flagged if latest_activity else False

    context = {
        "customer": customer,
        "shop": customer.shop,
        "applications": applications,
        "activities": activities,
        "latest_activity": latest_activity,
        "is_flagged": is_flagged,
        "form": form,
    }
    return render(request, "followups/portal/customer_detail.html", context)


@require_POST
@login_required
@followup_required
def followup_edit_due_date_view(request, pk):
    """
    Follow-up Employee Portal: Edit customer next due date for assigned shop customers.
    """
    user = request.user
    customer = get_object_or_404(Customer.objects.select_related("shop"), pk=pk)

    if not (user.is_admin_role or user.is_superuser):
        is_assigned = FollowUpAssignment.objects.filter(
            employee=user,
            shop=customer.shop,
            is_active=True
        ).exists()
        if not is_assigned:
            return HttpResponseForbidden("Access Denied: You are not authorized to update records for this customer.")

    new_date = request.POST.get("due_date", "").strip()
    next_url = request.POST.get("next") or reverse("followup_dashboard")

    if not new_date:
        messages.error(request, "Please provide a valid due date.")
        return redirect(next_url)

    app = customer.applications.first()
    if not app:
        messages.error(request, "No active finance application for this customer.")
        return redirect(next_url)

    app.generate_default_schedule_if_missing()
    next_sched = app.next_unpaid_schedule
    if not next_sched:
        messages.info(request, "All installments are already settled.")
        return redirect(next_url)

    old_date = next_sched.due_date
    next_sched.due_date = new_date
    next_sched.save(update_fields=["due_date", "updated_at"])

    from customers.models import CustomerUpdateHistory
    log_msg = f"Next due date changed from {old_date} to {new_date} by {user.username}."
    customer.latest_update_status = log_msg
    customer.latest_update_at = timezone.now()
    customer.latest_update_by = user
    customer.save(update_fields=["latest_update_status", "latest_update_at", "latest_update_by", "updated_at"])

    CustomerUpdateHistory.objects.create(
        customer=customer,
        user=user,
        notes=log_msg
    )

    messages.success(request, f"Updated next due date to {new_date} for '{customer.name}'.")
    return redirect(next_url)

