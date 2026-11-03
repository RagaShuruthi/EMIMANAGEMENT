"""
Views for Shop Management module.
Provides secure, database-driven operations for shop listing, creation,
details inspection, updating, activation/deactivation, and shop-owner account/password management.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .forms import ShopForm, ShopAccountForm
from .models import Shop

User = get_user_model()


def admin_required(view_func):
    """
    Decorator ensuring the authenticated user possesses Administrator privileges.
    """
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")
        if not (request.user.is_admin_role or request.user.is_superuser):
            return HttpResponseForbidden("Access Denied: Administrator privileges required.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


@login_required
@admin_required
def shop_list_view(request):
    """
    Displays paginated list of shops with search, status, and payment due filters.
    Supports filtering by Payment Due status and Date range against payment obligations.
    URL: /admin-dashboard/shops/
    """
    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all").strip().lower()
    payment_status_filter = request.GET.get("payment_status", "all").strip().lower()
    start_date = request.GET.get("start_date", "").strip()
    end_date = request.GET.get("end_date", "").strip()

    # Query all shops from database
    queryset = Shop.objects.all().select_related("owner_account").order_by("name")

    # Search filter: by shop name, contact person (owner_name), or phone
    if search_query:
        queryset = queryset.filter(
            Q(name__icontains=search_query) |
            Q(owner_name__icontains=search_query) |
            Q(phone__icontains=search_query)
        )

    # Status filter (Active / Inactive)
    if status_filter == "active":
        queryset = queryset.filter(is_active=True)
    elif status_filter == "inactive":
        queryset = queryset.filter(is_active=False)
    else:
        status_filter = "all"

    # Date-wise filter for payment due / application obligations
    if start_date or end_date:
        app_date_q = Q()
        if start_date:
            app_date_q &= (Q(applications__application_date__gte=start_date) | Q(applications__created_at__date__gte=start_date))
        if end_date:
            app_date_q &= (Q(applications__application_date__lte=end_date) | Q(applications__created_at__date__lte=end_date))

        pay_date_q = Q()
        if start_date:
            pay_date_q &= (Q(financier_payouts__payment_date__gte=start_date) | Q(financier_payouts__created_at__date__gte=start_date))
        if end_date:
            pay_date_q &= (Q(financier_payouts__payment_date__lte=end_date) | Q(financier_payouts__created_at__date__lte=end_date))

        queryset = queryset.filter(app_date_q | pay_date_q).distinct()

    # Attach live financial and customer metrics to each shop and apply payment due filter
    from admin_dashboard.views import get_shop_financials
    all_shops = []
    for s in queryset:
        s.financials = get_shop_financials(s, start_date=start_date, end_date=end_date)
        s.customer_count = s.customers.count()

        # Payment due filter
        # 'due': Has remaining payment due (> 0)
        # 'paid': Completely cleared (remaining_due == 0 and total_payable > 0)
        # 'unpaid': Completely unpaid
        # 'partially_paid': Partially paid
        # 'no_obligations': No obligations (total_payable == 0)
        if payment_status_filter == "due" and s.financials["remaining_due"] <= 0:
            continue
        elif payment_status_filter == "paid" and (s.financials["remaining_due"] > 0 or s.financials["total_payable"] == 0):
            continue
        elif payment_status_filter == "unpaid" and s.financials["payment_status"] != "UNPAID":
            continue
        elif payment_status_filter == "partially_paid" and s.financials["payment_status"] != "PARTIALLY_PAID":
            continue
        elif payment_status_filter == "no_obligations" and s.financials["payment_status"] != "NO_OBLIGATIONS":
            continue

        all_shops.append(s)

    # Pagination: 10 shops per page
    paginator = Paginator(all_shops, 10)
    page_number = request.GET.get("page", 1)
    try:
        shops = paginator.page(page_number)
    except PageNotAnInteger:
        shops = paginator.page(1)
    except EmptyPage:
        shops = paginator.page(paginator.num_pages)

    context = {
        "active_page": "shops",
        "shops": shops,
        "search_query": search_query,
        "status_filter": status_filter,
        "payment_status_filter": payment_status_filter,
        "start_date": start_date,
        "end_date": end_date,
        "total_count": paginator.count,
    }
    return render(request, "shops/shop_list.html", context)


@login_required
@admin_required
def add_shop_view(request):
    """
    Registers a new shop in the database, with optional simultaneous
    shop-owner login account and password creation.
    URL: /admin-dashboard/shops/add/
    """
    if request.method == "POST":
        form = ShopForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    shop = form.save(commit=False)

                    # Inline creation of shop owner login account & password
                    create_credentials = form.cleaned_data.get("create_credentials")
                    login_username = form.cleaned_data.get("login_username", "").strip()
                    login_password = form.cleaned_data.get("login_password", "")

                    if (create_credentials or login_username) and login_password:
                        owner_user = User.objects.create_user(
                            username=login_username,
                            password=login_password,
                            email=shop.email or "",
                            role=User.Role.SHOP_OWNER,
                            is_active=True,
                            is_staff=False,
                            is_superuser=False,
                        )
                        shop.owner_account = owner_user
                        shop.assigned_password = login_password

                    shop.save()

                if shop.owner_account:
                    messages.success(
                        request,
                        f"Shop '{shop.name}' and shop-owner login account '{shop.owner_account.username}' were created successfully!"
                    )
                else:
                    messages.success(request, f"Shop '{shop.name}' was created successfully.")

                return redirect("shop_detail", pk=shop.pk)

            except Exception as e:
                messages.error(request, f"Error saving shop record: {e}")
        else:
            messages.error(request, "Please correct the errors indicated below.")
    else:
        form = ShopForm()

    context = {
        "form": form,
        "action_title": "Register New Shop",
    }
    return render(request, "shops/shop_form.html", context)


@login_required
def shop_detail_view(request, pk):
    """
    Displays full details of a specific shop.
    Enforces tenant authorization:
    - Administrators can view any shop.
    - Shop owners may strictly view their own associated shop.
    URL: /admin-dashboard/shops/<int:pk>/
    """
    shop = get_object_or_404(Shop.objects.select_related("owner_account"), pk=pk)

    # Authorization verification
    is_admin = request.user.is_admin_role or request.user.is_superuser
    is_assigned_owner = (shop.owner_account_id == request.user.id)

    if not (is_admin or is_assigned_owner):
        return HttpResponseForbidden("Access Denied: You are not authorized to view this shop's information.")

    from admin_dashboard.views import get_shop_financials, get_next_due_date
    from payments.models import FinancierPayment
    from applications.models import FinanceApplication

    financials = get_shop_financials(shop)

    # Customers registered under this shop
    customers_qs = shop.customers.prefetch_related("applications", "applications__financier_payments").order_by("-created_at")
    customer_rows = []
    for c in customers_qs:
        app = c.applications.first()
        customer_rows.append({
            "customer": c,
            "application": app,
            "next_due_date": get_next_due_date(app.monthly_due_day) if app else None,
            "outstanding": app.outstanding_amount if app else 0,
        })

    # Payment transactions made by admin to this shop
    payments = FinancierPayment.objects.filter(
        Q(application__shop=shop) | Q(shop=shop)
    ).select_related("application", "application__customer").order_by("-payment_date", "-created_at")

    # Eligible applications for recording new payments
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
        "active_page": "shops",
        "shop": shop,
        "is_admin": is_admin,
        "financials": financials,
        "customer_rows": customer_rows,
        "payments": payments,
        "eligible_apps": eligible_apps,
        "total_customers": customers_qs.count(),
    }
    return render(request, "shops/shop_detail.html", context)


@login_required
@admin_required
def edit_shop_view(request, pk):
    """
    Edits existing shop details in the database.
    URL: /admin-dashboard/shops/<int:pk>/edit/
    """
    shop = get_object_or_404(Shop, pk=pk)

    if request.method == "POST":
        form = ShopForm(request.POST, instance=shop)
        if form.is_valid():
            try:
                with transaction.atomic():
                    shop = form.save(commit=False)

                    # Inline creation of shop owner account if shop didn't have one
                    create_credentials = form.cleaned_data.get("create_credentials")
                    login_username = form.cleaned_data.get("login_username", "").strip()
                    login_password = form.cleaned_data.get("login_password", "")

                    if (create_credentials or login_username) and login_password:
                        owner_user = User.objects.create_user(
                            username=login_username,
                            password=login_password,
                            email=shop.email or "",
                            role=User.Role.SHOP_OWNER,
                            is_active=True,
                            is_staff=False,
                            is_superuser=False,
                        )
                        shop.owner_account = owner_user
                        shop.assigned_password = login_password

                    shop.save()

                messages.success(request, f"Shop '{shop.name}' was updated successfully.")
                return redirect("shop_detail", pk=shop.pk)
            except Exception as e:
                messages.error(request, f"Error updating shop: {e}")
        else:
            messages.error(request, "Please correct the errors indicated below.")
    else:
        form = ShopForm(instance=shop)

    context = {
        "form": form,
        "shop": shop,
        "action_title": f"Edit Shop — {shop.name}",
    }
    return render(request, "shops/shop_form.html", context)


@login_required
@admin_required
def shop_account_view(request, pk):
    """
    Allows administrator to create or reset the login password for a shop owner.
    URL: /admin-dashboard/shops/<int:pk>/account/
    """
    shop = get_object_or_404(Shop.objects.select_related("owner_account"), pk=pk)
    has_account = bool(shop.owner_account)

    if request.method == "POST":
        form = ShopAccountForm(
            request.POST,
            shop=shop,
            is_existing_account=has_account
        )
        if form.is_valid():
            password = form.cleaned_data["password"]
            with transaction.atomic():
                if has_account:
                    # Reset password for existing account
                    user = shop.owner_account
                    user.set_password(password)
                    user.save()
                    shop.assigned_password = password
                    shop.save(update_fields=["assigned_password", "updated_at"])
                    messages.success(
                        request,
                        f"Password for shop owner '{user.username}' ({shop.name}) was updated successfully!"
                    )
                else:
                    # Create new account and link to shop
                    username = form.cleaned_data["username"].strip()
                    user = User.objects.create_user(
                        username=username,
                        password=password,
                        email=shop.email or "",
                        role=User.Role.SHOP_OWNER,
                        is_active=True,
                        is_staff=False,
                        is_superuser=False,
                    )
                    shop.owner_account = user
                    shop.assigned_password = password
                    shop.save(update_fields=["owner_account", "assigned_password", "updated_at"])
                    messages.success(
                        request,
                        f"Login account '{user.username}' created and linked to '{shop.name}' with the specified password!"
                    )

            return redirect("shop_detail", pk=shop.pk)
        else:
            messages.error(request, "Please fix the validation errors below.")
    else:
        form = ShopAccountForm(
            shop=shop,
            is_existing_account=has_account
        )

    context = {
        "shop": shop,
        "form": form,
        "has_account": has_account,
    }
    return render(request, "shops/shop_account.html", context)


@login_required
@admin_required
@require_POST
def shop_status_view(request, pk):
    """
    Toggles the active/inactive status of a shop.
    Requires POST with CSRF protection. Does NOT delete records.
    URL: /admin-dashboard/shops/<int:pk>/status/
    """
    shop = get_object_or_404(Shop, pk=pk)

    # Toggle status
    shop.is_active = not shop.is_active
    shop.save(update_fields=["is_active", "updated_at"])

    status_str = "activated" if shop.is_active else "deactivated"
    messages.success(request, f"Shop '{shop.name}' was successfully {status_str}.")

    # Redirect back or to shop details
    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("shop_detail", pk=shop.pk)
