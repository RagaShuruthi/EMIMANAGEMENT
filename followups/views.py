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
        activity_count=Count("followup_activities", distinct=True)
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
    activities = employee.followup_activities.select_related("customer", "shop").order_by("-created_at")[:20]

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
        "setup_url": setup_url,
        "active_tab": "followups",
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

    # Determine permitted shops (dynamically query active assignments)
    if user.is_admin_role or user.is_superuser:
        assigned_shops = Shop.objects.filter(is_active=True).order_by("name")
    else:
        assigned_shops = Shop.objects.filter(
            followup_assignments__employee=user,
            followup_assignments__is_active=True,
            is_active=True
        ).order_by("name")

    # Scoped Customers in assigned shops
    customers_qs = Customer.objects.filter(
        shop__in=assigned_shops
    ).select_related("shop").prefetch_related("applications", "followup_activities")

    search_query = request.GET.get("q", "").strip()
    shop_filter = request.GET.get("shop_id", "").strip()
    tab_view = request.GET.get("tab", "customers").strip().lower()
    due_filter = request.GET.get("due_filter", "all").strip().lower()

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

    # Scoped Activities Base
    activities_base = FollowUpActivity.objects.filter(
        shop__in=assigned_shops
    ).select_related("customer", "shop", "employee")

    if shop_filter:
        activities_base = activities_base.filter(shop_id=shop_filter)

    # Pending follow-ups
    pending_followups = activities_base.filter(
        status=FollowUpActivity.Status.PENDING
    ).order_by("next_followup_date", "-created_at")

    # Completed follow-ups
    completed_followups = activities_base.filter(
        status=FollowUpActivity.Status.COMPLETED
    ).order_by("-created_at")

    # Upcoming tasks (Pending with next_followup_date >= today)
    upcoming_tasks = activities_base.filter(
        status=FollowUpActivity.Status.PENDING,
        next_followup_date__isnull=False,
        next_followup_date__gte=today
    ).order_by("next_followup_date")

    # Overdue tasks (Pending with next_followup_date < today)
    overdue_tasks = activities_base.filter(
        status=FollowUpActivity.Status.PENDING,
        next_followup_date__isnull=False,
        next_followup_date__lt=today
    ).order_by("next_followup_date")

    # Process due data for all scoped customers
    customer_list = []
    today_due_count = 0
    pending_due_count = 0
    all_count = 0

    for customer in customers_qs.order_by("-created_at"):
        all_count += 1
        apps = list(customer.applications.all())
        app = apps[0] if apps else None

        mobile_model = app.mobile_model if app else "—"
        monthly_emi = app.monthly_emi if app else Decimal("0.00")
        monthly_due_day = app.monthly_due_day if app else None

        # Calculate next due date from due day
        next_due_date = None
        if monthly_due_day:
            try:
                cur_month_due = today.replace(day=monthly_due_day)
            except ValueError:
                _, last_day = calendar.monthrange(today.year, today.month)
                cur_month_due = today.replace(day=min(monthly_due_day, last_day))

            if cur_month_due >= today:
                next_due_date = cur_month_due
            else:
                next_month = 1 if today.month == 12 else today.month + 1
                next_year = today.year + 1 if today.month == 12 else today.year
                _, last_day = calendar.monthrange(next_year, next_month)
                next_due_date = today.replace(year=next_year, month=next_month, day=min(monthly_due_day, last_day))

        # Retrieve latest activity
        latest_act = customer.followup_activities.first()
        outcome = latest_act.outcome if latest_act else None
        is_flagged = latest_act.is_flagged if latest_act else False
        act_next_date = latest_act.next_followup_date if latest_act else None

        # Check if customer has Today's Due
        is_today_due = False
        if monthly_due_day == today.day and outcome != FollowUpActivity.Outcome.PAID:
            is_today_due = True
        elif act_next_date == today and latest_act and latest_act.status == FollowUpActivity.Status.PENDING:
            is_today_due = True
        elif outcome == FollowUpActivity.Outcome.NEED_TWO_DAYS and act_next_date == today:
            is_today_due = True

        # Check if customer has Pending Due
        is_pending_due = False
        if outcome in [FollowUpActivity.Outcome.NOT_PAID, FollowUpActivity.Outcome.NEED_TWO_DAYS]:
            is_pending_due = True
        elif is_flagged:
            is_pending_due = True
        elif latest_act and latest_act.status == FollowUpActivity.Status.PENDING:
            is_pending_due = True
        elif app and outcome != FollowUpActivity.Outcome.PAID:
            is_pending_due = True

        if is_today_due:
            today_due_count += 1
        if is_pending_due:
            pending_due_count += 1

        customer.mobile_model = mobile_model
        customer.monthly_emi = monthly_emi
        customer.monthly_due_day = monthly_due_day
        customer.next_due_date = next_due_date
        customer.latest_activity = latest_act
        customer.is_today_due = is_today_due
        customer.is_pending_due = is_pending_due

        customer_list.append(customer)

    # Apply the two due filters if selected
    if due_filter == "today":
        filtered_customers = [c for c in customer_list if c.is_today_due]
    elif due_filter == "pending":
        filtered_customers = [c for c in customer_list if c.is_pending_due]
    else:
        filtered_customers = customer_list

    # Scoped Metrics
    total_assigned_shops = assigned_shops.count()
    total_customers = all_count
    total_pending = pending_followups.count()
    total_completed = completed_followups.count()
    total_upcoming = upcoming_tasks.count()
    total_overdue = overdue_tasks.count()

    paginator = Paginator(filtered_customers, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "assigned_shops": assigned_shops,
        "customers": page_obj,
        "pending_followups": pending_followups[:20],
        "completed_followups": completed_followups[:20],
        "upcoming_tasks": upcoming_tasks[:20],
        "overdue_tasks": overdue_tasks[:20],
        "total_assigned_shops": total_assigned_shops,
        "total_customers": total_customers,
        "total_pending": total_pending,
        "total_completed": total_completed,
        "total_upcoming": total_upcoming,
        "total_overdue": total_overdue,
        "search_query": search_query,
        "shop_filter": shop_filter,
        "tab_view": tab_view,
        "due_filter": due_filter,
        "today_due_count": today_due_count,
        "pending_due_count": pending_due_count,
        "all_count": all_count,
        "today": today,
    }
    return render(request, "followups/portal/dashboard.html", context)


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
        activity.next_followup_date = today + timedelta(days=2)
        messages.warning(
            request,
            f"Flagged '{customer.name}' — Requested 2 days time. Scheduled callback for {activity.next_followup_date.strftime('%b %d, %Y')}."
        )
    elif outcome == FollowUpActivity.Outcome.PAID:
        activity.is_flagged = False
        activity.status = FollowUpActivity.Status.COMPLETED
        activity.completed_at = timezone.now()
        messages.success(request, f"Updated '{customer.name}' status to Paid.")
    else:  # NOT_PAID
        activity.is_flagged = False
        activity.status = FollowUpActivity.Status.PENDING
        messages.info(request, f"Recorded '{customer.name}' as Not Paid. Remains in pending follow-up.")

    activity.save()

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
