"""
Export Utilities for Excel (.xlsx) and PDF (.pdf) Reports.
Produces clean, professionally styled documents reflecting currently active
search queries, shop filters, due-date filters, and sorting orders.
"""

from decimal import Decimal
import io
from django.http import HttpResponse
from django.utils import timezone
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def export_customers_to_excel(customer_rows, title="Customers EMI Report", shop_name=None, active_filters_desc=""):
    """
    Generates a professional Excel workbook (.xlsx) containing filtered customer rows.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Customers"
    ws.views.sheetView[0].showGridLines = True

    # Color definitions
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")  # Dark Blue
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    totals_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    totals_font = Font(name="Calibri", size=11, bold=True, color="0F172A")
    data_font = Font(name="Calibri", size=10)
    thin_border = Border(
        left=Side(style='thin', color='E2E8F0'),
        right=Side(style='thin', color='E2E8F0'),
        top=Side(style='thin', color='E2E8F0'),
        bottom=Side(style='thin', color='E2E8F0')
    )

    # Report Title Header Block
    ws.merge_cells("A1:K1")
    title_cell = ws["A1"]
    title_cell.value = f"EMI MANAGEMENT SYSTEM — {title.upper()}"
    title_cell.font = Font(name="Calibri", size=14, bold=True, color="1E3A8A")
    title_cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 25

    meta_row = 2
    if shop_name:
        ws.cell(row=meta_row, column=1, value=f"Shop: {shop_name}").font = Font(bold=True)
        meta_row += 1
    gen_time = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    ws.cell(row=meta_row, column=1, value=f"Generated On: {gen_time} | Filters: {active_filters_desc or 'All Records'}").font = Font(italic=True, size=9, color="64748B")
    ws.row_dimensions[meta_row].height = 18

    # Table Column Headers
    headers = [
        "S.No",
        "Customer Name",
        "Phone Number",
        "Shop Name",
        "Phone Model",
        "Purchase Price (₹)",
        "Down Payment (₹)",
        "Monthly EMI (₹)",
        "Amount Paid (₹)",
        "Remaining Balance (₹)",
        "Next Due Date",
        "Due Status",
        "Latest Update Status"
    ]

    header_row_idx = meta_row + 2
    ws.row_dimensions[header_row_idx].height = 26

    for col_idx, header_text in enumerate(headers, 1):
        cell = ws.cell(row=header_row_idx, column=col_idx, value=header_text)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center" if "Date" in header_text or "Status" in header_text or col_idx == 1 else ("right" if "₹" in header_text else "left"), vertical="center")
        cell.border = thin_border

    # Data Rows
    current_row = header_row_idx + 1
    total_price = Decimal("0.00")
    total_down = Decimal("0.00")
    total_emi = Decimal("0.00")
    total_paid = Decimal("0.00")
    total_remaining = Decimal("0.00")

    for idx, row in enumerate(customer_rows, 1):
        ws.row_dimensions[current_row].height = 20

        price = row.get("mobile_price") or Decimal("0.00")
        down = row.get("down_payment") or Decimal("0.00")
        emi = row.get("monthly_emi") or Decimal("0.00")
        paid = row.get("amount_paid") or Decimal("0.00")
        rem = row.get("remaining_balance") or Decimal("0.00")

        total_price += price
        total_down += down
        total_emi += emi
        total_paid += paid
        total_remaining += rem

        due_date_str = row.get("next_due_date_display") or "—"
        update_status = row.get("update_status") or "—"

        values = [
            idx,
            row.get("customer_name", "—"),
            row.get("phone", "—"),
            row.get("shop_name", "—"),
            row.get("mobile_model", "—"),
            float(price),
            float(down),
            float(emi),
            float(paid),
            float(rem),
            due_date_str,
            row.get("due_status_display", "—"),
            update_status
        ]

        for col_idx, val in enumerate(values, 1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.font = data_font
            cell.border = thin_border

            # Number formatting
            if col_idx in (6, 7, 8, 9, 10):
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col_idx in (1, 11, 12):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

        current_row += 1

    # Totals Row
    ws.row_dimensions[current_row].height = 24
    totals_label_cell = ws.cell(row=current_row, column=1, value="TOTALS")
    totals_label_cell.font = totals_font
    totals_label_cell.alignment = Alignment(horizontal="center", vertical="center")
    totals_label_cell.fill = totals_fill
    totals_label_cell.border = thin_border

    for c in range(2, 6):
        cell = ws.cell(row=current_row, column=c, value="")
        cell.fill = totals_fill
        cell.border = thin_border

    tot_cells = [
        (6, float(total_price)),
        (7, float(total_down)),
        (8, float(total_emi)),
        (9, float(total_paid)),
        (10, float(total_remaining)),
    ]
    for c_idx, tot_val in tot_cells:
        cell = ws.cell(row=current_row, column=c_idx, value=tot_val)
        cell.font = totals_font
        cell.number_format = '#,##0.00'
        cell.alignment = Alignment(horizontal="right", vertical="center")
        cell.fill = totals_fill
        cell.border = thin_border

    for c in range(11, 14):
        cell = ws.cell(row=current_row, column=c, value="")
        cell.fill = totals_fill
        cell.border = thin_border

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if cell.row < header_row_idx:
                continue
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"customers_report_{timezone.localtime().strftime('%Y%m%d_%H%M%S')}.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_customers_to_pdf(customer_rows, title="Customers EMI Report", shop_name=None, active_filters_desc=""):
    """
    Generates a professional PDF document containing filtered customer rows using reportlab.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=20,
        rightMargin=20,
        topMargin=20,
        bottomMargin=20
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'RepTitle',
        parent=styles['Heading1'],
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#1E3A8A"),
        fontName="Helvetica-Bold",
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        'RepMeta',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#64748B"),
        fontName="Helvetica",
        spaceAfter=10,
    )
    th_style = ParagraphStyle(
        'TH',
        parent=styles['Normal'],
        fontSize=7,
        leading=8,
        textColor=colors.white,
        fontName="Helvetica-Bold",
        alignment=1,
    )
    td_style = ParagraphStyle(
        'TD',
        parent=styles['Normal'],
        fontSize=7,
        leading=8,
        textColor=colors.HexColor("#0F172A"),
        fontName="Helvetica",
    )
    td_right_style = ParagraphStyle(
        'TDR',
        parent=styles['Normal'],
        fontSize=7,
        leading=8,
        textColor=colors.HexColor("#0F172A"),
        fontName="Helvetica",
        alignment=2,
    )
    td_bold_right_style = ParagraphStyle(
        'TDBR',
        parent=styles['Normal'],
        fontSize=7,
        leading=8,
        textColor=colors.HexColor("#0F172A"),
        fontName="Helvetica-Bold",
        alignment=2,
    )

    elements = []

    # Title & Metadata
    main_title = f"EMI Management System — {title}"
    if shop_name:
        main_title += f" ({shop_name})"
    elements.append(Paragraph(main_title, title_style))

    gen_time = timezone.localtime().strftime("%b %d, %Y %I:%M %p")
    filter_info = f"Generated: {gen_time} &bull; Filters: {active_filters_desc or 'All Records'} &bull; Total Count: {len(customer_rows)}"
    elements.append(Paragraph(filter_info, meta_style))

    # Table Header
    headers = [
        "S.No", "Customer Name", "Phone", "Phone Model", "Price (₹)",
        "Down (₹)", "EMI (₹)", "Paid (₹)", "Balance (₹)", "Next Due", "Status", "Latest Update"
    ]
    table_data = [[Paragraph(h, th_style) for h in headers]]

    total_price = Decimal("0.00")
    total_down = Decimal("0.00")
    total_emi = Decimal("0.00")
    total_paid = Decimal("0.00")
    total_remaining = Decimal("0.00")

    for idx, row in enumerate(customer_rows, 1):
        price = row.get("mobile_price") or Decimal("0.00")
        down = row.get("down_payment") or Decimal("0.00")
        emi = row.get("monthly_emi") or Decimal("0.00")
        paid = row.get("amount_paid") or Decimal("0.00")
        rem = row.get("remaining_balance") or Decimal("0.00")

        total_price += price
        total_down += down
        total_emi += emi
        total_paid += paid
        total_remaining += rem

        update_status = row.get("update_status") or "—"
        if len(update_status) > 40:
            update_status = update_status[:37] + "..."

        table_data.append([
            Paragraph(str(idx), td_style),
            Paragraph(str(row.get("customer_name", "—")), td_style),
            Paragraph(str(row.get("phone", "—")), td_style),
            Paragraph(str(row.get("mobile_model", "—")), td_style),
            Paragraph(f"{price:,.2f}", td_right_style),
            Paragraph(f"{down:,.2f}", td_right_style),
            Paragraph(f"{emi:,.2f}", td_right_style),
            Paragraph(f"{paid:,.2f}", td_right_style),
            Paragraph(f"{rem:,.2f}", td_right_style),
            Paragraph(str(row.get("next_due_date_display", "—")), td_style),
            Paragraph(str(row.get("due_status_display", "—")), td_style),
            Paragraph(update_status, td_style),
        ])

    # Totals Row
    table_data.append([
        Paragraph("<b>TOTALS</b>", td_style),
        Paragraph("", td_style),
        Paragraph("", td_style),
        Paragraph("", td_style),
        Paragraph(f"<b>{total_price:,.2f}</b>", td_bold_right_style),
        Paragraph(f"<b>{total_down:,.2f}</b>", td_bold_right_style),
        Paragraph(f"<b>{total_emi:,.2f}</b>", td_bold_right_style),
        Paragraph(f"<b>{total_paid:,.2f}</b>", td_bold_right_style),
        Paragraph(f"<b>{total_remaining:,.2f}</b>", td_bold_right_style),
        Paragraph("", td_style),
        Paragraph("", td_style),
        Paragraph("", td_style),
    ])

    col_widths = [24, 75, 60, 75, 48, 45, 45, 48, 52, 52, 54, 180]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
        ('TOPPADDING', (0, 0), (-1, 0), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
        ('TOPPADDING', (0, 1), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 3),
    ]))
    elements.append(t)

    doc.build(elements)
    buffer.seek(0)

    filename = f"customers_report_{timezone.localtime().strftime('%Y%m%d_%H%M%S')}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

