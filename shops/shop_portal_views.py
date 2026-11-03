"""
Shop Dashboard and Portal Views for Private Finance EMI Management System.
Implements customer registration, live calculation, document management,
electronic application submission, payment tracking, and shop statistics.
"""

from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum, Q
from django.http import HttpResponseForbidden, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from shops.models import Shop
from customers.models import Customer
from applications.models import FinanceApplication, CustomerDocument, ApplicationHistory
from payments.models import FinancierPayment


def get_authenticated_shop(request):
    """
    Retrieves the shop associated with the current user.
    Enforces tenant scoping:
    - Shop owners only access their own shop.
    - Admins can pass ?shop_id= or default to the first active shop.
    """
    user = request.user
    if not user.is_authenticated:
        return None

    # Check direct shop assignment
    shop = user.shops.filter(is_active=True).first() or user.shops.first()

    # Admin fallback or shop switcher
    if not shop and (user.is_admin_role or user.is_superuser):
        shop_id = request.GET.get("shop_id") or request.session.get("admin_active_shop_id")
        if shop_id:
            shop = Shop.objects.filter(pk=shop_id).first()
        if not shop:
            shop = Shop.objects.filter(is_active=True).first() or Shop.objects.first()
        if shop:
            request.session["admin_active_shop_id"] = shop.id

    return shop


@login_required
def shop_dashboard_view(request):
    """
    Shop Portal Overview Dashboard.
    Displays dynamic, database-driven statistics and recent activity for the shop.
    URL: /shop-dashboard/ or /shop/
    """
    shop = get_authenticated_shop(request)
    if not shop:
        if request.user.is_admin_role or request.user.is_superuser:
            messages.info(request, "No registered shops found. Create a shop first in the Admin Dashboard.")
            return redirect("shop_list")
        return HttpResponseForbidden("No retail shop is linked to your user account. Please contact the administrator.")

    # 1. Total Customers
    total_customers = Customer.objects.filter(shop=shop).count()

    # 2. Total Applications
    total_applications = FinanceApplication.objects.filter(shop=shop).count()

    # 3. Applications Submitted
    submitted_applications = FinanceApplication.objects.filter(
        shop=shop, status=FinanceApplication.Status.SUBMITTED
    ).count()

    # 4. Applications Pending (Draft, Ready, Under Review)
    pending_applications = FinanceApplication.objects.filter(
        shop=shop,
        status__in=[
            FinanceApplication.Status.DRAFT,
            FinanceApplication.Status.READY,
            FinanceApplication.Status.UNDER_REVIEW,
        ]
    ).count()

    # 5. Applications Approved
    approved_applications = FinanceApplication.objects.filter(
        shop=shop, status=FinanceApplication.Status.APPROVED
    ).count()

    # 6. Applications Rejected
    rejected_applications = FinanceApplication.objects.filter(
        shop=shop, status=FinanceApplication.Status.REJECTED
    ).count()

    # 7. Total Amount Requested from Financier
    amount_requested_agg = FinanceApplication.objects.filter(shop=shop).aggregate(
        total=Sum("financier_request_amount")
    )["total"] or Decimal("0.00")

    # 8. Total Amount Received from Financier
    amount_received_agg = FinancierPayment.objects.filter(
        application__shop=shop
    ).aggregate(total=Sum("amount_received"))["total"] or Decimal("0.00")

    # 9. Outstanding Amount Yet to Be Received
    outstanding_amount = max(Decimal("0.00"), amount_requested_agg - amount_received_agg)

    # Recent Applications
    recent_applications = (
        FinanceApplication.objects.filter(shop=shop)
        .select_related("customer")
        .order_by("-created_at")[:8]
    )

    all_shops = None
    if request.user.is_admin_role or request.user.is_superuser:
        all_shops = Shop.objects.all().order_by("name")

    context = {
        "shop": shop,
        "all_shops": all_shops,
        "active_nav": "dashboard",
        "total_customers": total_customers,
        "total_applications": total_applications,
        "submitted_applications": submitted_applications,
        "pending_applications": pending_applications,
        "approved_applications": approved_applications,
        "rejected_applications": rejected_applications,
        "total_amount_requested": amount_requested_agg,
        "total_amount_received": amount_received_agg,
        "outstanding_amount": outstanding_amount,
        "recent_applications": recent_applications,
    }
    return render(request, "shops/portal/dashboard.html", context)


@login_required
def customer_registration_view(request):
    """
    Dedicated Multi-Section Customer Registration & Finance Application Form.
    Section A: Customer & Shop Info
    Section B: Mobile & EMI Pricing with real-time financier calculation
    Section C: Document Uploads
    URL: /shop-dashboard/customers/add/
    """
    shop = get_authenticated_shop(request)
    if not shop:
        return HttpResponseForbidden("Unauthorized shop access.")

    if request.method == "POST":
        import re

        # Section A: Customer Details
        customer_name = request.POST.get("customer_name", "").strip()
        work_address = request.POST.get("work_address", "").strip()
        home_address = request.POST.get("home_address", "").strip()
        raw_p1 = request.POST.get("primary_mobile", "").strip()
        raw_p2 = request.POST.get("secondary_mobile", "").strip()
        primary_mobile = re.sub(r"\D", "", raw_p1)
        secondary_mobile = re.sub(r"\D", "", raw_p2)
        app_date_str = request.POST.get("application_date") or timezone.now().strftime("%Y-%m-%d")
        monthly_due_day_str = request.POST.get("monthly_due_day", "").strip()

        # Section B: Device & Financing Details
        mobile_model = request.POST.get("mobile_model", "").strip()
        mobile_price_str = request.POST.get("mobile_price", "0").replace(",", "").strip()
        down_payment_str = request.POST.get("down_payment", "0").replace(",", "").strip()
        emi_duration_str = request.POST.get("emi_duration", "6").strip()
        submission_action = request.POST.get("action", "submit")  # "submit" or "draft"

        # Validation
        errors = []
        field_errors = {}

        if not customer_name:
            errors.append("Customer name is required.")
            field_errors["customer_name"] = "Customer name is required."
        if not work_address:
            errors.append("Work address is required.")
            field_errors["work_address"] = "Work address is required."
        if not home_address:
            errors.append("Current home address is required.")
            field_errors["home_address"] = "Current home address is required."

        # Exactly 10 digits phone number validation
        if not primary_mobile:
            errors.append("Primary mobile number 1 is required.")
            field_errors["primary_mobile"] = "Primary mobile number 1 is required."
        elif not re.match(r"^\d{10}$", primary_mobile):
            errors.append("Primary mobile number 1 must be exactly 10 digits without letters, spaces, or symbols.")
            field_errors["primary_mobile"] = "Primary mobile number 1 must be exactly 10 digits without letters, spaces, or symbols."

        if not secondary_mobile:
            errors.append("Secondary mobile number 2 is mandatory.")
            field_errors["secondary_mobile"] = "Secondary mobile number 2 is mandatory (10 digits)."
        elif not re.match(r"^\d{10}$", secondary_mobile):
            errors.append("Secondary mobile number 2 must be exactly 10 digits without letters, spaces, or symbols.")
            field_errors["secondary_mobile"] = "Secondary mobile number 2 must be exactly 10 digits without letters, spaces, or symbols."

        if not mobile_model:
            errors.append("Mobile phone model is required.")
            field_errors["mobile_model"] = "Mobile phone model is required."

        # Mandatory documents validation
        doc_photo = request.FILES.get("doc_photo")
        doc_aadhaar = request.FILES.get("doc_aadhaar")
        if not doc_photo:
            errors.append("Customer Photograph upload is mandatory.")
            field_errors["doc_photo"] = "Customer Photograph upload is mandatory."
        if not doc_aadhaar:
            errors.append("Aadhaar Card document upload is mandatory.")
            field_errors["doc_aadhaar"] = "Aadhaar Card document upload is mandatory."

        try:
            mobile_price = Decimal(mobile_price_str or "0")
            if mobile_price <= Decimal("0"):
                errors.append("Mobile price must be greater than zero.")
                field_errors["mobile_price"] = "Mobile price must be greater than zero."
        except Exception:
            errors.append("Invalid mobile price amount.")
            field_errors["mobile_price"] = "Invalid mobile price amount."
            mobile_price = Decimal("0")

        try:
            down_payment = Decimal(down_payment_str or "0")
            if down_payment < Decimal("0"):
                errors.append("Down payment cannot be negative.")
                field_errors["down_payment"] = "Down payment cannot be negative."
        except Exception:
            errors.append("Invalid down payment amount.")
            field_errors["down_payment"] = "Invalid down payment amount."
            down_payment = Decimal("0")

        try:
            emi_duration = int(emi_duration_str or "6")
            if emi_duration not in (3, 6):
                errors.append("EMI duration scheme is only permitted for 3 or 6 months.")
        except Exception:
            errors.append("Invalid EMI tenure duration. Choose 3 or 6 months.")
            emi_duration = 6

        # Monthly due day (shop owner can customize or default to date cycle)
        try:
            monthly_due_day = int(monthly_due_day_str)
            if not (1 <= monthly_due_day <= 31):
                monthly_due_day = FinanceApplication.calculate_monthly_due_day(app_date_str)
        except Exception:
            monthly_due_day = FinanceApplication.calculate_monthly_due_day(app_date_str)

        # Monthly EMI: Main Payment (Mobile Price) / Due Months, rounded to nearest 10s
        # Editable by shop owner
        monthly_emi_str = request.POST.get("monthly_emi", "").replace(",", "").strip()
        custom_emi = None
        if monthly_emi_str:
            try:
                custom_emi = Decimal(monthly_emi_str)
            except Exception:
                custom_emi = None

        if custom_emi and custom_emi > Decimal("0"):
            calculated_monthly_emi = FinanceApplication.round_emi_to_tens(custom_emi)
        else:
            raw_emi = (mobile_price / Decimal(emi_duration)) if (emi_duration in (3, 6) and mobile_price > Decimal("0")) else Decimal("0.00")
            calculated_monthly_emi = FinanceApplication.round_emi_to_tens(raw_emi)

        # Financier Request Amount = Total Selling Price - Down Payment
        calculated_request_amount = max(Decimal("0.00"), mobile_price - down_payment)

        form_data = {
            "customer_name": customer_name,
            "work_address": work_address,
            "home_address": home_address,
            "primary_mobile": raw_p1,
            "secondary_mobile": raw_p2,
            "application_date": app_date_str,
            "monthly_due_day": str(monthly_due_day),
            "mobile_model": mobile_model,
            "mobile_price": mobile_price_str if mobile_price_str != "0" else "",
            "down_payment": down_payment_str,
            "monthly_emi": monthly_emi_str,
            "emi_duration": str(emi_duration),
        }

        if errors:
            for err in errors:
                messages.error(request, err)
            context = {
                "shop": shop,
                "active_nav": "add_customer",
                "today_date": timezone.now().strftime("%Y-%m-%d"),
                "form_data": form_data,
                "field_errors": field_errors,
            }
            return render(request, "shops/portal/customer_register.html", context)
        else:
            try:
                with transaction.atomic():
                    # 1. Save or reuse Customer record under this shop
                    customer = Customer.objects.create(
                        shop=shop,
                        name=customer_name,
                        work_address=work_address,
                        home_address=home_address,
                        primary_mobile=primary_mobile,
                        secondary_mobile=secondary_mobile,
                    )

                    # Determine initial status
                    initial_status = (
                        FinanceApplication.Status.SUBMITTED
                        if submission_action == "submit"
                        else FinanceApplication.Status.DRAFT
                    )

                    # 3. Create Finance Application
                    application = FinanceApplication(
                        shop=shop,
                        customer=customer,
                        application_date=app_date_str,
                        mobile_model=mobile_model,
                        mobile_price=mobile_price,
                        down_payment=down_payment,
                        monthly_emi=calculated_monthly_emi,
                        emi_duration=emi_duration,
                        monthly_due_day=monthly_due_day,
                        financier_request_amount=calculated_request_amount,
                        status=initial_status,
                    )
                    if initial_status == FinanceApplication.Status.SUBMITTED:
                        application.submission_date = timezone.now()
                        application.external_reference = f"FIN-{timezone.now().strftime('%Y%m%d%H%M')}-{shop.id}"

                    application.save()

                    # 4. Handle Document Uploads
                    doc_files = {
                        CustomerDocument.DocType.PHOTOGRAPH: request.FILES.get("doc_photo"),
                        CustomerDocument.DocType.AADHAAR: request.FILES.get("doc_aadhaar"),
                        CustomerDocument.DocType.PAN: request.FILES.get("doc_pan"),
                        CustomerDocument.DocType.VOTER_ID: request.FILES.get("doc_voter"),
                    }

                    for doc_type, uploaded_file in doc_files.items():
                        if uploaded_file:
                            CustomerDocument.objects.create(
                                application=application,
                                customer=customer,
                                document_type=doc_type,
                                file=uploaded_file,
                                original_filename=uploaded_file.name,
                                file_size=uploaded_file.size,
                                mime_type=uploaded_file.content_type or "application/octet-stream",
                            )

                    # 5. Audit History Log
                    ApplicationHistory.objects.create(
                        application=application,
                        previous_status="NEW",
                        new_status=initial_status,
                        actor=request.user,
                        notes=(
                            f"Application registered by shop '{shop.name}'. "
                            f"Financier Request: ₹{calculated_request_amount:,.2f}"
                        ),
                    )

                if initial_status == FinanceApplication.Status.SUBMITTED:
                    messages.success(
                        request,
                        f"Application {application.application_number} submitted to financier successfully!"
                    )
                else:
                    messages.success(
                        request,
                        f"Application {application.application_number} saved as Draft."
                    )

                return redirect("shop_application_detail", pk=application.pk)

            except Exception as e:
                messages.error(request, f"Error saving customer application: {e}")

    context = {
        "shop": shop,
        "active_nav": "add_customer",
        "today_date": timezone.now().strftime("%Y-%m-%d"),
        "form_data": {},
        "field_errors": {},
    }
    return render(request, "shops/portal/customer_register.html", context)


@login_required
def customer_list_view(request):
    """
    All Customers page has been removed per dashboard configuration.
    Redirects to the shop dashboard.
    URL: /shop-dashboard/customers/
    """
    return redirect("shop_dashboard")


@login_required
def application_list_view(request):
    """
    Lists finance applications with tabbed filtering:
    All, Submitted, Pending, Approved, Rejected.
    URL: /shop-dashboard/applications/
    """
    shop = get_authenticated_shop(request)
    if not shop:
        return HttpResponseForbidden("Unauthorized shop access.")

    status_filter = request.GET.get("status", "").upper()
    search_query = request.GET.get("q", "").strip()

    applications_qs = (
        FinanceApplication.objects.filter(shop=shop)
        .select_related("customer")
        .prefetch_related("financier_payments")
    )

    if status_filter == "SUBMITTED":
        applications_qs = applications_qs.filter(status=FinanceApplication.Status.SUBMITTED)
        active_nav = "submitted_apps"
    elif status_filter == "PENDING":
        applications_qs = applications_qs.filter(
            status__in=[
                FinanceApplication.Status.DRAFT,
                FinanceApplication.Status.READY,
                FinanceApplication.Status.UNDER_REVIEW,
            ]
        )
        active_nav = "pending_apps"
    elif status_filter == "APPROVED":
        applications_qs = applications_qs.filter(status=FinanceApplication.Status.APPROVED)
        active_nav = "approved_apps"
    elif status_filter == "REJECTED":
        applications_qs = applications_qs.filter(status=FinanceApplication.Status.REJECTED)
        active_nav = "rejected_apps"
    else:
        active_nav = "all_applications"

    if search_query:
        applications_qs = applications_qs.filter(
            Q(application_number__icontains=search_query)
            | Q(customer__name__icontains=search_query)
            | Q(customer__primary_mobile__icontains=search_query)
            | Q(mobile_model__icontains=search_query)
        )

    paginator = Paginator(applications_qs, 15)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "shop": shop,
        "active_nav": active_nav,
        "applications": page_obj,
        "status_filter": status_filter,
        "search_query": search_query,
    }
    return render(request, "shops/portal/application_list.html", context)


@login_required
def application_detail_view(request, pk):
    """
    Comprehensive view of a specific Finance Application.
    Displays customer info, pricing calculation, uploaded documents, status timeline, and payments.
    URL: /shop-dashboard/applications/<int:pk>/
    """
    shop = get_authenticated_shop(request)
    if not shop:
        return HttpResponseForbidden("Unauthorized shop access.")

    # Enforce tenant isolation: application must belong to the user's shop unless admin
    application = get_object_or_404(
        FinanceApplication.objects.select_related("customer", "shop")
        .prefetch_related("documents", "history", "financier_payments"),
        pk=pk
    )

    if application.shop_id != shop.id and not (request.user.is_admin_role or request.user.is_superuser):
        return HttpResponseForbidden("Access Denied: You cannot view applications from another shop.")

    context = {
        "shop": shop,
        "active_nav": "all_applications",
        "application": application,
        "documents": application.documents.all(),
        "history": application.history.order_by("-timestamp"),
        "payments": application.financier_payments.order_by("-payment_date"),
    }
    return render(request, "shops/portal/application_detail.html", context)


@login_required
def financier_payments_view(request):
    """
    Financier Payments & Day-by-Day Settlement History View.
    Tracks money requested vs received, outstanding balances, and daily payment history
    showing how much the administrator/financier pays day by day.
    URL: /shop-dashboard/payments/
    """
    shop = get_authenticated_shop(request)
    if not shop:
        return HttpResponseForbidden("Unauthorized shop access.")

    if request.method == "POST":
        # Record a new disbursement payment from financier / admin
        app_id = request.POST.get("application_id")
        amount_str = request.POST.get("amount_received", "0").replace(",", "").strip()
        payment_date = request.POST.get("payment_date") or timezone.now().strftime("%Y-%m-%d")
        reference = request.POST.get("payment_reference", "").strip()
        remarks = request.POST.get("remarks", "").strip()

        try:
            app = get_object_or_404(FinanceApplication, pk=app_id, shop=shop)
            amount = Decimal(amount_str)
            if amount <= Decimal("0"):
                messages.error(request, "Amount received must be greater than zero.")
            elif not reference:
                messages.error(request, "UTR / Payment reference number is required.")
            else:
                payment = FinancierPayment.objects.create(
                    application=app,
                    amount_received=amount,
                    payment_date=payment_date,
                    payment_reference=reference,
                    remarks=remarks,
                )
                if payment.is_discrepant:
                    messages.warning(
                        request,
                        f"Payment recorded with DISCREPANCY: Total received exceeds requested amount of ₹{app.financier_request_amount:,.2f}."
                    )
                else:
                    messages.success(
                        request,
                        f"Financier payment of ₹{amount:,.2f} recorded for {app.application_number}."
                    )
                return redirect("shop_payments")
        except Exception as e:
            messages.error(request, f"Error recording payment: {e}")

    # Query all payments for this shop ordered by date desc
    payments_qs = (
        FinancierPayment.objects.filter(application__shop=shop)
        .select_related("application", "application__customer")
        .order_by("-payment_date", "-created_at")
    )

    today = timezone.localdate() if hasattr(timezone, "localdate") else timezone.now().date()

    # Filter params
    date_filter = request.GET.get("date", "").strip()
    period_filter = request.GET.get("period", "").strip()
    search_query = request.GET.get("search", "").strip()

    filtered_qs = payments_qs
    if date_filter:
        filtered_qs = filtered_qs.filter(payment_date=date_filter)
    elif period_filter == "today":
        filtered_qs = filtered_qs.filter(payment_date=today)
    elif period_filter == "month":
        filtered_qs = filtered_qs.filter(payment_date__year=today.year, payment_date__month=today.month)

    if search_query:
        filtered_qs = filtered_qs.filter(
            Q(payment_reference__icontains=search_query)
            | Q(application__application_number__icontains=search_query)
            | Q(application__customer__name__icontains=search_query)
            | Q(remarks__icontains=search_query)
        )

    payments_list = list(filtered_qs)

    # Group payments day by day
    daily_groups = []
    current_date = None
    current_group = None

    for p in payments_list:
        if p.payment_date != current_date:
            if current_group:
                daily_groups.append(current_group)
            current_date = p.payment_date
            current_group = {
                "date": current_date,
                "is_today": (current_date == today),
                "total_day_amount": Decimal("0.00"),
                "count": 0,
                "payments": [],
            }
        current_group["total_day_amount"] += p.amount_received
        current_group["count"] += 1
        current_group["payments"].append(p)

    if current_group:
        daily_groups.append(current_group)

    # Global shop aggregates
    total_requested = FinanceApplication.objects.filter(shop=shop).aggregate(
        total=Sum("financier_request_amount")
    )["total"] or Decimal("0.00")
    total_received = payments_qs.aggregate(total=Sum("amount_received"))["total"] or Decimal("0.00")
    outstanding = max(Decimal("0.00"), total_requested - total_received)

    today_received = payments_qs.filter(payment_date=today).aggregate(
        total=Sum("amount_received")
    )["total"] or Decimal("0.00")
    month_received = payments_qs.filter(
        payment_date__year=today.year, payment_date__month=today.month
    ).aggregate(total=Sum("amount_received"))["total"] or Decimal("0.00")

    # Eligible applications for recording new payments (used in admin modal)
    eligible_apps = FinanceApplication.objects.filter(
        shop=shop,
        status__in=[
            FinanceApplication.Status.SUBMITTED,
            FinanceApplication.Status.APPROVED,
            FinanceApplication.Status.PARTIALLY_PAID,
        ]
    ).select_related("customer")

    context = {
        "shop": shop,
        "active_nav": "financier_payments",
        "payments": payments_list,
        "daily_groups": daily_groups,
        "eligible_apps": eligible_apps,
        "total_requested": total_requested,
        "total_received": total_received,
        "outstanding": outstanding,
        "today_received": today_received,
        "month_received": month_received,
        "date_filter": date_filter,
        "period_filter": period_filter,
        "search_query": search_query,
        "today_date": today.strftime("%Y-%m-%d"),
        "is_admin_user": bool(request.user.is_admin_role or request.user.is_superuser),
    }
    return render(request, "shops/portal/financier_payments.html", context)


@login_required
def shop_reports_view(request):
    """
    Shop Finance & Application Performance Reports.
    Provides aggregated metrics, date-range filtering, and status summaries.
    URL: /shop-dashboard/reports/
    """
    shop = get_authenticated_shop(request)
    if not shop:
        return HttpResponseForbidden("Unauthorized shop access.")

    # Status counts breakdown
    statuses = [
        ("SUBMITTED", "Submitted Applications", FinanceApplication.Status.SUBMITTED, "primary"),
        ("APPROVED", "Approved Applications", FinanceApplication.Status.APPROVED, "success"),
        ("PENDING", "Pending Applications", [FinanceApplication.Status.DRAFT, FinanceApplication.Status.READY, FinanceApplication.Status.UNDER_REVIEW], "warning"),
        ("REJECTED", "Rejected Applications", FinanceApplication.Status.REJECTED, "danger"),
        ("PAID", "Fully Paid by Financier", FinanceApplication.Status.PAID, "info"),
    ]

    status_breakdown = []
    for key, label, st_val, badge_color in statuses:
        if isinstance(st_val, list):
            count = FinanceApplication.objects.filter(shop=shop, status__in=st_val).count()
        else:
            count = FinanceApplication.objects.filter(shop=shop, status=st_val).count()
        status_breakdown.append({
            "key": key,
            "label": label,
            "count": count,
            "badge_color": badge_color,
        })

    # Financial sums
    total_req = FinanceApplication.objects.filter(shop=shop).aggregate(total=Sum("financier_request_amount"))["total"] or Decimal("0.00")
    total_rec = FinancierPayment.objects.filter(application__shop=shop).aggregate(total=Sum("amount_received"))["total"] or Decimal("0.00")
    outstanding = max(Decimal("0.00"), total_req - total_rec)

    context = {
        "shop": shop,
        "active_nav": "reports",
        "status_breakdown": status_breakdown,
        "total_requested": total_req,
        "total_received": total_rec,
        "outstanding": outstanding,
    }
    return render(request, "shops/portal/reports.html", context)


@login_required
def shop_profile_view(request):
    """
    Displays the authenticated Shop's profile details and account status.
    URL: /shop-dashboard/profile/
    """
    shop = get_authenticated_shop(request)
    if not shop:
        return HttpResponseForbidden("Unauthorized shop access.")

    context = {
        "shop": shop,
        "active_nav": "profile",
    }
    return render(request, "shops/portal/profile.html", context)
