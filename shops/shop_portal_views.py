"""
Shop Dashboard and Portal Views for Private Finance EMI Management System.
Implements customer registration, live calculation, document management,
electronic application submission, payment tracking, and shop statistics.
"""

from decimal import Decimal
import io
import os
import calendar
import json
from PIL import Image
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum, Q, Count
from django.http import HttpResponseForbidden, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from shops.models import Shop
from customers.models import Customer, CustomerUpdateHistory
from applications.models import FinanceApplication, CustomerDocument, ApplicationHistory
from payments.models import FinancierPayment, CustomerEMISchedule, CustomerEMIPayment


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

    # Check direct shop assignment: retail shop owners strictly access only active shops
    if user.is_admin_role or user.is_superuser:
        shop = user.shops.filter(is_active=True).first() or user.shops.first()
    else:
        shop = user.shops.filter(is_active=True).first()

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


def compress_image_if_needed(uploaded_file, max_size_bytes=1024 * 1024):
    """
    Compresses uploaded images so that the file size stored in the database/disk
    is guaranteed to be <= 1MB (1,048,576 bytes).
    Non-image files (such as PDFs) or images already <= 1MB are preserved intact.
    """
    if not uploaded_file:
        return uploaded_file

    if uploaded_file.size <= max_size_bytes:
        return uploaded_file

    try:
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
    except Exception:
        # Not a valid image file (e.g. PDF), leave intact
        uploaded_file.seek(0)
        return uploaded_file

    try:
        format_name = "JPEG"
        ext = ".jpg"
        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Clamp max dimension to 2000px if excessively large
        max_dim = 2000
        if max(img.width, img.height) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        quality = 85
        buffer = io.BytesIO()
        img.save(buffer, format=format_name, quality=quality, optimize=True)

        # Progressively lower quality if still > 1MB
        while buffer.tell() > max_size_bytes and quality > 30:
            quality -= 10
            buffer = io.BytesIO()
            img.save(buffer, format=format_name, quality=quality, optimize=True)

        # If still > 1MB, progressively downscale dimensions
        while buffer.tell() > max_size_bytes:
            new_w = int(img.width * 0.8)
            new_h = int(img.height * 0.8)
            if new_w < 300 or new_h < 300:
                break
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format=format_name, quality=quality, optimize=True)

        buffer.seek(0)
        output_size = buffer.getbuffer().nbytes

        orig_base, _ = os.path.splitext(uploaded_file.name)
        new_filename = f"{orig_base}{ext}"

        compressed_file = InMemoryUploadedFile(
            file=buffer,
            field_name=getattr(uploaded_file, "field_name", None),
            name=new_filename,
            content_type="image/jpeg",
            size=output_size,
            charset=None
        )
        return compressed_file
    except Exception:
        uploaded_file.seek(0)
        return uploaded_file


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

    # Approved statuses constant
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]

    # 1. Total Customers (Only count customers who have approved applications)
    total_customers = (
        Customer.objects.filter(shop=shop, applications__status__in=approved_statuses)
        .distinct()
        .count()
    )

    # 2. Total Applications
    total_applications = FinanceApplication.objects.filter(shop=shop).count()

    # 3. Applications Under Review (Draft, Ready, Submitted, Under Review)
    under_review_applications = FinanceApplication.objects.filter(
        shop=shop,
        status__in=[
            FinanceApplication.Status.DRAFT,
            FinanceApplication.Status.READY,
            FinanceApplication.Status.SUBMITTED,
            FinanceApplication.Status.UNDER_REVIEW,
            FinanceApplication.Status.SUBMISSION_FAILED,
        ]
    ).count()

    # 4. Applications Approved
    approved_applications = FinanceApplication.objects.filter(
        shop=shop, status__in=approved_statuses
    ).count()

    # 5. Applications Rejected
    rejected_applications = FinanceApplication.objects.filter(
        shop=shop, status=FinanceApplication.Status.REJECTED
    ).count()

    # 6. Total Amount Requested from Financier (Only for Approved Applications)
    amount_requested_agg = FinanceApplication.objects.filter(
        shop=shop, status__in=approved_statuses
    ).aggregate(
        total=Sum("financier_request_amount")
    )["total"] or Decimal("0.00")

    # 7. Total Amount Received from Financier (Direct Shop Payout or Application Payout)
    amount_received_agg = FinancierPayment.objects.filter(
        Q(shop=shop) | Q(application__shop=shop)
    ).aggregate(total=Sum("amount_received"))["total"] or Decimal("0.00")

    # 7b. EMI Collected Directly by Shop (added to amount received and deducted from outstanding)
    shop_collected_emi = CustomerEMIPayment.objects.filter(
        customer__shop=shop, collected_by_shop=True
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    total_amount_received = amount_received_agg + shop_collected_emi

    # 8. Outstanding Amount Yet to Be Received (or +Excess if customer EMI collected exceeds payable)
    raw_outstanding = amount_requested_agg - amount_received_agg - shop_collected_emi
    outstanding_excess = raw_outstanding < Decimal("0.00")
    outstanding_amount = abs(raw_outstanding) if outstanding_excess else raw_outstanding

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
        "under_review_applications": under_review_applications,
        "submitted_applications": under_review_applications,
        "pending_applications": under_review_applications,
        "approved_applications": approved_applications,
        "rejected_applications": rejected_applications,
        "total_amount_requested": amount_requested_agg,
        "total_amount_received": total_amount_received,
        "outstanding_amount": outstanding_amount,
        "outstanding_excess": outstanding_excess,
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
        variant = request.POST.get("variant", "").strip()
        custom_variant = request.POST.get("custom_variant", "").strip()
        device_type = request.POST.get("device_type", "android").strip().lower()
        if variant == "custom" and custom_variant:
            variant = custom_variant

        mobile_price_str = request.POST.get("mobile_price", "0").replace(",", "").strip()
        down_payment_str = request.POST.get("down_payment", "0").replace(",", "").strip()
        total_emi_str = request.POST.get("total_emi_amount", "").replace(",", "").strip()
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

        # Phone model validation: Alphabets, Numbers, Spaces, and Symbols (- _ + ( ) / .)
        if not mobile_model:
            errors.append("Mobile phone model is required.")
            field_errors["mobile_model"] = "Mobile phone model is required."
        elif not re.match(r"^[A-Za-z0-9\s\-_\+\(\)\/\.]+$", mobile_model):
            errors.append("Phone model contains invalid characters. Only letters, numbers, spaces, and symbols (- _ + ( ) / .) are allowed.")
            field_errors["mobile_model"] = "Only letters, numbers, spaces, and symbols (- _ + ( ) / .) are allowed."

        # Mandatory documents validation (Photo + Aadhaar Front & Back)
        doc_photo = request.FILES.get("doc_photo")
        doc_aadhaar_front = request.FILES.get("doc_aadhaar_front") or request.FILES.get("doc_aadhaar")
        doc_aadhaar_back = request.FILES.get("doc_aadhaar_back")
        legacy_aadhaar = request.FILES.get("doc_aadhaar")

        if not doc_photo:
            errors.append("Customer Photograph upload is mandatory.")
            field_errors["doc_photo"] = "Customer Photograph upload is mandatory."
        if not doc_aadhaar_front:
            errors.append("Aadhaar Card Front document upload is mandatory.")
            field_errors["doc_aadhaar_front"] = "Aadhaar Card Front document upload is mandatory."
        if not doc_aadhaar_back and not legacy_aadhaar:
            errors.append("Aadhaar Card Back document upload is mandatory.")
            field_errors["doc_aadhaar_back"] = "Aadhaar Card Back document upload is mandatory."

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
            elif mobile_price > Decimal("0") and down_payment > mobile_price:
                errors.append("Down payment cannot be greater than the actual phone price.")
                field_errors["down_payment"] = "Down payment cannot be greater than the actual phone price."
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

        # Monthly due day: automatically calculated from application date, but editable by shop owner
        try:
            monthly_due_day = int(monthly_due_day_str)
            if not (1 <= monthly_due_day <= 31):
                monthly_due_day = FinanceApplication.calculate_monthly_due_day(app_date_str)
        except Exception:
            monthly_due_day = FinanceApplication.calculate_monthly_due_day(app_date_str)

        # Financier Request Amount = Total Selling Price - Down Payment
        calculated_request_amount = max(Decimal("0.00"), mobile_price - down_payment)

        # Total EMI Amount: Phone Price (Down payment is ignored for EMI calculation)
        try:
            if total_emi_str:
                total_emi = Decimal(total_emi_str)
                if total_emi <= Decimal("0.00"):
                    total_emi = mobile_price
            else:
                total_emi = mobile_price
        except Exception:
            total_emi = mobile_price

        # Monthly EMI = Total EMI Amount / Selected Tenure (3 or 6), rounded to nearest 10s
        monthly_emi_str = request.POST.get("monthly_emi", "").replace(",", "").strip()
        custom_emi = None
        if monthly_emi_str:
            try:
                custom_emi = Decimal(monthly_emi_str)
            except Exception:
                custom_emi = None

        if custom_emi and custom_emi > Decimal("0"):
            calculated_monthly_emi = FinanceApplication.round_emi_to_tens(custom_emi)
        elif emi_duration in (3, 6) and total_emi > Decimal("0.00"):
            raw_emi = total_emi / Decimal(emi_duration)
            calculated_monthly_emi = FinanceApplication.round_emi_to_tens(raw_emi)
        else:
            calculated_monthly_emi = Decimal("0.00")

        form_data = {
            "customer_name": customer_name,
            "work_address": work_address,
            "home_address": home_address,
            "primary_mobile": raw_p1,
            "secondary_mobile": raw_p2,
            "application_date": app_date_str,
            "monthly_due_day": str(monthly_due_day),
            "mobile_model": mobile_model,
            "variant": variant,
            "custom_variant": custom_variant,
            "device_type": device_type,
            "mobile_price": mobile_price_str if mobile_price_str != "0" else "",
            "down_payment": down_payment_str,
            "total_emi_amount": str(total_emi) if total_emi > Decimal("0.00") else "",
            "monthly_emi": str(calculated_monthly_emi) if calculated_monthly_emi > Decimal("0.00") else "",
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
                    # Compose full mobile model if variant is provided and not already present
                    if variant and variant.lower() not in mobile_model.lower():
                        full_mobile_model = f"{mobile_model} {variant}".strip()
                    else:
                        full_mobile_model = mobile_model

                    application = FinanceApplication(
                        shop=shop,
                        customer=customer,
                        application_date=app_date_str,
                        mobile_model=full_mobile_model,
                        variant=variant,
                        mobile_price=mobile_price,
                        down_payment=down_payment,
                        total_emi_amount=total_emi,
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

                    # 4. Handle Document Uploads (compressed to <= 1MB)
                    doc_files = [
                        (CustomerDocument.DocType.PHOTOGRAPH, request.FILES.get("doc_photo")),
                        (
                            CustomerDocument.DocType.AADHAAR_FRONT
                            if request.FILES.get("doc_aadhaar_front")
                            else CustomerDocument.DocType.AADHAAR,
                            doc_aadhaar_front,
                        ),
                        (CustomerDocument.DocType.AADHAAR_BACK, doc_aadhaar_back),
                        (CustomerDocument.DocType.PAN, request.FILES.get("doc_pan")),
                        (CustomerDocument.DocType.VOTER_ID, request.FILES.get("doc_voter")),
                    ]

                    for doc_type, uploaded_file in doc_files:
                        if uploaded_file:
                            final_file = compress_image_if_needed(uploaded_file)
                            CustomerDocument.objects.create(
                                application=application,
                                customer=customer,
                                document_type=doc_type,
                                file=final_file,
                                original_filename=uploaded_file.name,
                                file_size=final_file.size,
                                mime_type=getattr(final_file, "content_type", None) or uploaded_file.content_type or "application/octet-stream",
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

    initial_due_day = str(FinanceApplication.calculate_monthly_due_day(timezone.now().date()))
    context = {
        "shop": shop,
        "active_nav": "add_customer",
        "today_date": timezone.now().strftime("%Y-%m-%d"),
        "form_data": {"monthly_due_day": initial_due_day, "device_type": "android", "variant": ""},
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
    All, Under Review, Approved, Rejected.
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

    if status_filter in ["UNDER_REVIEW", "PENDING", "SUBMITTED"]:
        applications_qs = applications_qs.filter(
            status__in=[
                FinanceApplication.Status.DRAFT,
                FinanceApplication.Status.READY,
                FinanceApplication.Status.SUBMITTED,
                FinanceApplication.Status.UNDER_REVIEW,
                FinanceApplication.Status.SUBMISSION_FAILED,
            ]
        )
        status_filter = "UNDER_REVIEW"
        active_nav = "under_review_apps"
    elif status_filter in ["APPROVED", "PAID", "PARTIALLY_PAID"]:
        applications_qs = applications_qs.filter(
            status__in=[
                FinanceApplication.Status.APPROVED,
                FinanceApplication.Status.PAID,
                FinanceApplication.Status.PARTIALLY_PAID,
            ]
        )
        status_filter = "APPROVED"
        active_nav = "approved_apps"
    elif status_filter == "REJECTED":
        applications_qs = applications_qs.filter(status=FinanceApplication.Status.REJECTED)
        active_nav = "rejected_apps"
    else:
        status_filter = ""
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

    # Query all payments for this shop (both direct shop payouts and application payouts)
    payments_qs = (
        FinancierPayment.objects.filter(Q(shop=shop) | Q(application__shop=shop))
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

    # Global shop aggregates (Strictly only Approved applications)
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]
    total_requested_gross = FinanceApplication.objects.filter(
        shop=shop, status__in=approved_statuses
    ).aggregate(
        total=Sum("financier_request_amount")
    )["total"] or Decimal("0.00")
    total_received_payouts = payments_qs.aggregate(total=Sum("amount_received"))["total"] or Decimal("0.00")
    shop_collected_emi = CustomerEMIPayment.objects.filter(
        customer__shop=shop, collected_by_shop=True
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    total_requested = total_requested_gross
    total_received = total_received_payouts + shop_collected_emi
    raw_outstanding = total_requested_gross - total_received_payouts - shop_collected_emi
    outstanding_excess = raw_outstanding < Decimal("0.00")
    outstanding = abs(raw_outstanding) if outstanding_excess else raw_outstanding

    today_received = payments_qs.filter(payment_date=today).aggregate(
        total=Sum("amount_received")
    )["total"] or Decimal("0.00")
    month_received = payments_qs.filter(
        payment_date__year=today.year, payment_date__month=today.month
    ).aggregate(total=Sum("amount_received"))["total"] or Decimal("0.00")

    # Eligible applications for recording new payments (only approved & unpaid obligations)
    eligible_apps = FinanceApplication.objects.filter(
        shop=shop,
        status__in=[
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
        "outstanding_excess": outstanding_excess,
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
    Shop Sales Performance, Reports & Analytics Dashboard.
    Features:
    1. Last Month Sales vs This Month Sales comparison with deltas and growth %
    2. Best Sales Month in This Year with peak metrics and annual contribution
    3. 12-Month Month-by-Month breakdown table with MoM growth
    4. Interactive Chart.js visualization for monthly sales revenue and units
    5. Top Selling Phone Models in the shop
    6. Application lifecycle pipeline breakdown
    URL: /shop-dashboard/reports/
    """
    shop = get_authenticated_shop(request)
    if not shop:
        return HttpResponseForbidden("Unauthorized shop access.")

    today = timezone.localdate() if hasattr(timezone, "localdate") else timezone.now().date()
    current_year = today.year
    current_month = today.month

    # Selected year filter (default: current year)
    try:
        selected_year = int(request.GET.get("year", current_year))
    except (ValueError, TypeError):
        selected_year = current_year

    # Base QuerySet: All eligible sales applications for this shop (excluding rejected)
    base_sales_qs = FinanceApplication.objects.filter(shop=shop).exclude(
        status=FinanceApplication.Status.REJECTED
    )

    # Helper function: Retrieve sales metrics for any specific year and month
    def get_month_sales(y, m):
        m_qs = base_sales_qs.filter(
            Q(application_date__year=y, application_date__month=m) |
            (Q(application_date__isnull=True) & Q(created_at__year=y, created_at__month=m))
        )
        agg = m_qs.aggregate(
            total_sales=Sum("mobile_price"),
            total_financed=Sum("financier_request_amount"),
            total_down=Sum("down_payment")
        )
        sales_val = agg["total_sales"] or Decimal("0.00")
        fin_val = agg["total_financed"] or Decimal("0.00")
        down_val = agg["total_down"] or Decimal("0.00")
        return {
            "year": y,
            "month": m,
            "month_name": calendar.month_name[m],
            "month_abbr": calendar.month_abbr[m],
            "full_label": f"{calendar.month_name[m]} {y}",
            "short_label": f"{calendar.month_abbr[m]} {y}",
            "count": m_qs.count(),
            "total_sales": sales_val,
            "total_financed": fin_val,
            "total_down": down_val,
        }

    # ---------------------------------------------------------
    # 1. Last Month Sales vs This Month Sales
    # ---------------------------------------------------------
    if selected_year == current_year:
        this_m_num = current_month
        this_m_year = current_year
    else:
        this_m_num = 12
        this_m_year = selected_year

    if this_m_num > 1:
        last_m_num = this_m_num - 1
        last_m_year = this_m_year
    else:
        last_m_num = 12
        last_m_year = this_m_year - 1

    this_month_data = get_month_sales(this_m_year, this_m_num)
    last_month_data = get_month_sales(last_m_year, last_m_num)

    # Calculate differences & growth percentages
    sales_diff = this_month_data["total_sales"] - last_month_data["total_sales"]
    t_sales_flt = float(this_month_data["total_sales"])
    l_sales_flt = float(last_month_data["total_sales"])
    if l_sales_flt > 0:
        sales_growth_pct = round(((t_sales_flt - l_sales_flt) / l_sales_flt) * 100.0, 1)
    else:
        sales_growth_pct = 100.0 if t_sales_flt > 0 else 0.0

    units_diff = this_month_data["count"] - last_month_data["count"]
    l_units = last_month_data["count"]
    if l_units > 0:
        units_growth_pct = round(((this_month_data["count"] - l_units) / l_units) * 100.0, 1)
    else:
        units_growth_pct = 100.0 if this_month_data["count"] > 0 else 0.0

    financed_diff = this_month_data["total_financed"] - last_month_data["total_financed"]
    down_diff = this_month_data["total_down"] - last_month_data["total_down"]

    comparison = {
        "sales_diff": sales_diff,
        "sales_growth_pct": sales_growth_pct,
        "is_sales_up": sales_diff > 0,
        "is_sales_down": sales_diff < 0,
        "units_diff": units_diff,
        "units_growth_pct": units_growth_pct,
        "is_units_up": units_diff > 0,
        "is_units_down": units_diff < 0,
        "financed_diff": financed_diff,
        "down_diff": down_diff,
    }

    # ---------------------------------------------------------
    # 2. 12 Months Breakdown & Best Sales Month in This Year
    # ---------------------------------------------------------
    months_data = []
    annual_total_sales = Decimal("0.00")
    annual_total_units = 0
    annual_total_financed = Decimal("0.00")
    annual_total_down = Decimal("0.00")

    prev_month_sales_val = None

    for m in range(1, 13):
        m_data = get_month_sales(selected_year, m)
        annual_total_sales += m_data["total_sales"]
        annual_total_units += m_data["count"]
        annual_total_financed += m_data["total_financed"]
        annual_total_down += m_data["total_down"]

        # Month-over-month growth within the year
        curr_sales_f = float(m_data["total_sales"])
        if prev_month_sales_val is not None:
            if prev_month_sales_val > 0:
                mom_growth = round(((curr_sales_f - prev_month_sales_val) / prev_month_sales_val) * 100.0, 1)
            else:
                mom_growth = 100.0 if curr_sales_f > 0 else 0.0
        else:
            mom_growth = None

        prev_month_sales_val = curr_sales_f

        m_data["mom_growth"] = mom_growth
        m_data["is_current"] = (selected_year == current_year and m == current_month)
        months_data.append(m_data)

    # Determine Best Sales Month in This Year
    months_with_sales = [m for m in months_data if m["total_sales"] > 0]
    best_month = None
    if months_with_sales:
        best_month = max(months_with_sales, key=lambda x: (x["total_sales"], x["count"]))
        for m in months_data:
            m["is_best"] = (m["month"] == best_month["month"])
    else:
        for m in months_data:
            m["is_best"] = False

    annual_avg_monthly_sales = annual_total_sales / Decimal("12") if annual_total_sales > 0 else Decimal("0.00")
    for m in months_data:
        if annual_total_sales > 0:
            m["annual_share_pct"] = round((float(m["total_sales"]) / float(annual_total_sales)) * 100.0, 1)
        else:
            m["annual_share_pct"] = 0.0

    if best_month:
        if annual_total_sales > 0:
            best_month["annual_share_pct"] = round((float(best_month["total_sales"]) / float(annual_total_sales)) * 100.0, 1)
        else:
            best_month["annual_share_pct"] = 0.0

        if annual_avg_monthly_sales > 0:
            best_month["above_avg_diff"] = best_month["total_sales"] - annual_avg_monthly_sales
            best_month["above_avg_pct"] = round(
                ((float(best_month["total_sales"]) - float(annual_avg_monthly_sales)) / float(annual_avg_monthly_sales)) * 100.0, 1
            )
        else:
            best_month["above_avg_diff"] = Decimal("0.00")
            best_month["above_avg_pct"] = 0.0

    annual_summary = {
        "year": selected_year,
        "total_sales": annual_total_sales,
        "total_units": annual_total_units,
        "total_financed": annual_total_financed,
        "total_down": annual_total_down,
        "avg_monthly_sales": annual_avg_monthly_sales,
    }

    # ---------------------------------------------------------
    # 3. Chart Datasets (Labels, Sales ₹, Units Count)
    # ---------------------------------------------------------
    chart_labels = [m["month_abbr"] for m in months_data]
    chart_sales = [float(m["total_sales"]) for m in months_data]
    chart_units = [m["count"] for m in months_data]
    best_month_idx = (best_month["month"] - 1) if best_month else -1

    # ---------------------------------------------------------
    # 4. Top Selling Mobile Models in Selected Year
    # ---------------------------------------------------------
    top_models_qs = (
        base_sales_qs.filter(
            Q(application_date__year=selected_year) |
            (Q(application_date__isnull=True) & Q(created_at__year=selected_year))
        )
        .values("mobile_model")
        .annotate(units_sold=Count("id"), total_revenue=Sum("mobile_price"))
        .order_by("-units_sold", "-total_revenue")[:5]
    )
    top_models = []
    for item in top_models_qs:
        units = item["units_sold"] or 0
        rev = item["total_revenue"] or Decimal("0.00")
        avg_p = (rev / units) if units > 0 else Decimal("0.00")
        top_models.append({
            "mobile_model": item["mobile_model"],
            "units_sold": units,
            "total_revenue": rev,
            "avg_price": avg_p,
            "share_pct": round((float(rev) / float(annual_total_sales)) * 100.0, 1) if annual_total_sales > 0 else 0.0,
        })

    # Available years list for filter (past 2 years, current year, next year)
    available_years = [current_year - 2, current_year - 1, current_year, current_year + 1]
    if selected_year not in available_years:
        available_years.append(selected_year)
    available_years = sorted(list(set(available_years)))

    # ---------------------------------------------------------
    # 5. Status counts breakdown & Financial Sums (Preserved)
    # ---------------------------------------------------------
    statuses = [
        ("SUBMITTED", "Submitted Applications", FinanceApplication.Status.SUBMITTED, "primary"),
        ("APPROVED", "Approved Applications", FinanceApplication.Status.APPROVED, "success"),
        ("PENDING", "Pending Review", [FinanceApplication.Status.DRAFT, FinanceApplication.Status.READY, FinanceApplication.Status.UNDER_REVIEW], "warning"),
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
    total_req = FinanceApplication.objects.filter(shop=shop).exclude(
        status=FinanceApplication.Status.REJECTED
    ).aggregate(total=Sum("financier_request_amount"))["total"] or Decimal("0.00")
    total_rec = FinancierPayment.objects.filter(
        Q(shop=shop) | Q(application__shop=shop)
    ).aggregate(total=Sum("amount_received"))["total"] or Decimal("0.00")
    outstanding = max(Decimal("0.00"), total_req - total_rec)

    context = {
        "shop": shop,
        "active_nav": "reports",
        "selected_year": selected_year,
        "current_year": current_year,
        "current_month": current_month,
        "available_years": available_years,
        "this_month_data": this_month_data,
        "last_month_data": last_month_data,
        "comparison": comparison,
        "best_month": best_month,
        "annual_summary": annual_summary,
        "months_table": months_data,
        "top_models": top_models,
        "chart_labels_json": json.dumps(chart_labels),
        "chart_sales_json": json.dumps(chart_sales),
        "chart_units_json": json.dumps(chart_units),
        "best_month_idx": best_month_idx,
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


@login_required
def shop_emi_collection_view(request):
    """
    Shop EMI Installment Collection Page.
    Allows retail shops to collect monthly EMI installments directly from customers.
    Collected amounts are:
    1. Recorded permanently against the customer's EMI schedule & payment ledger.
    2. Deducted automatically from the remaining amount the financier owes the shop.
    3. Synced to the Admin Dashboard and the assigned Follow-up Person.
    URL: /shop-dashboard/emi-collections/
    """
    shop = get_authenticated_shop(request)
    if not shop:
        return HttpResponseForbidden("Unauthorized shop access.")

    today = timezone.localdate() if hasattr(timezone, "localdate") else timezone.now().date()

    if request.method == "POST":
        customer_id = request.POST.get("customer_id")
        amount_str = request.POST.get("amount", "0").replace(",", "").strip()
        payment_date_str = request.POST.get("payment_date", "").strip()
        payment_mode = request.POST.get("payment_mode", "CASH").strip()
        ref = request.POST.get("transaction_reference", "").strip()
        remarks = request.POST.get("remarks", "").strip()

        try:
            amt = Decimal(amount_str)
            if amt <= Decimal("0.00"):
                raise ValueError("Amount must be positive.")
        except Exception:
            messages.error(request, "Please enter a valid positive EMI collection amount.")
            return redirect("shop_emi_collection")

        customer = get_object_or_404(Customer, pk=customer_id, shop=shop)
        app = customer.applications.filter(
            status__in=[
                FinanceApplication.Status.APPROVED,
                FinanceApplication.Status.PAID,
                FinanceApplication.Status.PARTIALLY_PAID,
            ]
        ).first()
        if not app:
            messages.error(request, "No approved finance application found for this customer.")
            return redirect("shop_emi_collection")

        payment_date = today
        if payment_date_str:
            try:
                from datetime import datetime
                payment_date = datetime.strptime(payment_date_str, "%Y-%m-%d").date()
            except Exception:
                payment_date = today

        with transaction.atomic():
            payment = app.record_customer_payment(
                amount=amt,
                payment_date=payment_date,
                payment_mode=payment_mode,
                transaction_reference=ref or f"SHOP-COL-{shop.id}",
                recorded_by=request.user,
                remarks=remarks or f"Collected directly by shop ({shop.name})"
            )
            if payment:
                payment.collected_by_shop = True
                payment.save(update_fields=["collected_by_shop"])

                # Process shop collection through financial accounting service
                from payments.services import process_shop_collection
                process_shop_collection(payment, user=request.user)

                # Update customer status & audit history for Admin Dashboard
                status_msg = f"EMI of ₹{amt:,.2f} ({payment_mode}) collected by shop ({shop.name}) on {payment_date.strftime('%d-%m-%Y')}."
                customer.latest_update_status = status_msg
                customer.latest_update_at = timezone.now()
                customer.latest_update_by = request.user
                customer.save(update_fields=["latest_update_status", "latest_update_at", "latest_update_by", "updated_at"])

                CustomerUpdateHistory.objects.create(
                    customer=customer,
                    user=request.user,
                    section="Shop EMI Collection",
                    field_name="EMI Collected by Shop",
                    previous_value="—",
                    new_value=f"₹{amt:,.2f}",
                    description=f"Shop ({shop.name}) collected ₹{amt:,.2f} ({payment_mode}) — deducted from financier payable",
                    notes=status_msg
                )

                # Notify corresponding Follow-up person assigned to this shop
                from followups.models import FollowUpAssignment, FollowUpActivity
                assigned_emp_id = (
                    FollowUpAssignment.objects.filter(shop=shop, is_active=True)
                    .values_list("employee_id", flat=True)
                    .first()
                )
                FollowUpActivity.objects.create(
                    employee_id=assigned_emp_id or request.user.id,
                    customer=customer,
                    shop=shop,
                    activity_type=FollowUpActivity.ActivityType.PAYMENT,
                    outcome=FollowUpActivity.Outcome.PAID,
                    status=FollowUpActivity.Status.COMPLETED,
                    notes=status_msg,
                )

        messages.success(
            request,
            f"Collected ₹{amt:,.2f} from '{customer.name}'. Deducted from financier payable and synced with Admin & Follow-up staff."
        )
        return redirect("shop_emi_collection")

    search_query = request.GET.get("q", "").strip()
    approved_statuses = [
        FinanceApplication.Status.APPROVED,
        FinanceApplication.Status.PAID,
        FinanceApplication.Status.PARTIALLY_PAID,
    ]
    customers_qs = (
        Customer.objects.filter(shop=shop, is_loss=False, applications__status__in=approved_statuses)
        .distinct()
        .prefetch_related("applications", "applications__emi_schedules", "applications__customer_emi_payments")
        .order_by("-created_at")
    )
    if search_query:
        customers_qs = customers_qs.filter(
            Q(name__icontains=search_query)
            | Q(primary_mobile__icontains=search_query)
            | Q(applications__mobile_model__icontains=search_query)
        ).distinct()

    customer_rows = []
    customers_list = list(customers_qs)
    Customer.annotate_duplicates(customers_list)
    for c in customers_list:
        app = c.applications.filter(status__in=approved_statuses).first()
        if not app:
            continue
        app.generate_default_schedule_if_missing()
        rem = app.customer_remaining_balance
        if rem <= Decimal("0.00"):
            continue
        next_sched = app.next_unpaid_schedule
        customer_rows.append({
            "customer": c,
            "app": app,
            "is_duplicate": getattr(c, "_is_duplicate_number", c.is_duplicate_number),
            "next_sched": next_sched,
            "next_due_amount": next_sched.remaining_amount if next_sched else Decimal("0.00"),
            "next_due_date": next_sched.due_date if next_sched else None,
            "due_status": app.due_classification(today=today),
            "total_paid": app.total_customer_paid,
            "remaining_balance": rem,
        })

    shop_collections = (
        CustomerEMIPayment.objects.filter(customer__shop=shop, collected_by_shop=True)
        .select_related("customer", "application", "recorded_by")
        .order_by("-payment_date", "-created_at")
    )

    total_collected_by_shop = shop_collections.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    month_collected_by_shop = shop_collections.filter(
        payment_date__year=today.year, payment_date__month=today.month
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    from payments.services import get_shop_financials
    fin = get_shop_financials(shop)

    context = {
        "shop": shop,
        "active_nav": "emi_collection",
        "search_query": search_query,
        "customer_rows": customer_rows,
        "shop_collections": shop_collections[:50],
        "total_collected_by_shop": fin["total_shop_collected"],
        "month_collected_by_shop": month_collected_by_shop,
        "total_payable_from_financier": fin["total_payable"],
        "total_financier_paid": fin["actual_admin_paid"],
        "shop_collection_adjusted": fin["shop_collection_adjusted"],
        "shop_collectible_balance": fin["shop_collectible_balance"],
        "net_financier_remaining_due": fin["remaining_due"],
        "outstanding_excess": fin["shop_collectible_balance"] > Decimal("0.00"),
        "today_iso": today.strftime("%Y-%m-%d"),
    }
    return render(request, "shops/portal/emi_collection.html", context)
