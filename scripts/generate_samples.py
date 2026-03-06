"""Generate synthetic bank statement sample data for finance-intelligence-agent."""

import logging

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logger = logging.getLogger(__name__)

OUT = "./data"

# ── Shared transaction data ──────────────────────────────────────────────────

# Itaú - Cuenta Corriente (UYU) — XLSX
ITAU_TRANSACTIONS = [
    ("2026-01-02", "SUPERMERCADO DISCO",         -2840.00, "UYU", "Groceries"),
    ("2026-01-03", "ANTEL TELEFONIA",             -890.00,  "UYU", "Utilities"),
    ("2026-01-05", "FARMACIA DEL PUEBLO",         -420.00,  "UYU", "Healthcare"),
    ("2026-01-06", "SUPERMERCADO TIENDA INGLESA", -3120.00, "UYU", "Groceries"),
    ("2026-01-07", "YPF COMBUSTIBLES",            -1800.00, "UYU", "Transport"),
    ("2026-01-08", "NETFLIX",                     -720.00,  "UYU", "Entertainment"),
    ("2026-01-09", "UBER",                        -380.00,  "UYU", "Transport"),
    ("2026-01-10", "RESTAURANT LA PASCUALA",      -1560.00, "UYU", "Dining"),
    ("2026-01-12", "SUPERMERCADO DISCO",          -1950.00, "UYU", "Groceries"),
    ("2026-01-13", "OSE AGUA",                    -310.00,  "UYU", "Utilities"),
    ("2026-01-14", "UTE ELECTRICIDAD",            -1240.00, "UYU", "Utilities"),
    ("2026-01-15", "SALARIO ENERO",               52000.00, "UYU", "Salary"),
    ("2026-01-15", "FARMACIA DEL PUEBLO",         -580.00,  "UYU", "Healthcare"),
    ("2026-01-16", "GIMNASIO SMART FIT",          -990.00,  "UYU", "Healthcare"),
    ("2026-01-17", "SUPERMERCADO TIENDA INGLESA", -2340.00, "UYU", "Groceries"),
    ("2026-01-18", "RAPPI DELIVERY",              -640.00,  "UYU", "Dining"),
    ("2026-01-19", "YPF COMBUSTIBLES",            -1800.00, "UYU", "Transport"),  # duplicate of 07 (cross-account)
    ("2026-01-20", "LIBRERIA PAPELERIA DON",      -450.00,  "UYU", "Shopping"),
    ("2026-01-21", "RESTAURANT EL PALENQUE",      -2100.00, "UYU", "Dining"),
    ("2026-01-22", "TRANSFER TO BROU",            -8000.00, "UYU", "Transfer"),
    ("2026-01-23", "SUPERMERCADO DISCO",          -1780.00, "UYU", "Groceries"),
    ("2026-01-24", "SPOTIFY",                     -360.00,  "UYU", "Entertainment"),
    ("2026-01-25", "UBER",                        -420.00,  "UYU", "Transport"),
    ("2026-01-26", "FARMACIA URUGUAYA",           -890.00,  "UYU", "Healthcare"),
    ("2026-01-27", "SUPERMERCADO TIENDA INGLESA", -2680.00, "UYU", "Groceries"),
    ("2026-01-28", "CINE LIFE ALFAVILLE",         -780.00,  "UYU", "Entertainment"),
    ("2026-01-29", "RESTAURANT LA CIGALE",        -3200.00, "UYU", "Dining"),  # suspicious: large dining
    ("2026-01-30", "YPF COMBUSTIBLES",            -1800.00, "UYU", "Transport"),
    ("2026-01-31", "RAPPI DELIVERY",              -520.00,  "UYU", "Dining"),
]

# BROU - Caja de Ahorros (UYU) — XLSX
BROU_TRANSACTIONS = [
    ("2026-01-01", "SALDO ANTERIOR",              15000.00, "UYU", "Other Income"),
    ("2026-01-03", "PAGO ALQUILER",               -18500.00,"UYU", "Utilities"),
    ("2026-01-05", "TRANSFER FROM ITAU",           8000.00, "UYU", "Transfer"),   # matches Itaú transfer
    ("2026-01-06", "SUPERMERCADO DISCO",          -1950.00, "UYU", "Groceries"),  # duplicate of Itaú 12 (1 day diff)
    ("2026-01-08", "ABITAB PAGO SERVICIOS",       -1200.00, "UYU", "Utilities"),
    ("2026-01-10", "RETIRO CAJERO ATM",           -3000.00, "UYU", "Other"),
    ("2026-01-12", "COBRO FREELANCE WEB",          9500.00, "UYU", "Freelance"),
    ("2026-01-14", "FARMACIA DEL PUEBLO",          -420.00, "UYU", "Healthcare"), # duplicate of Itaú 05
    ("2026-01-15", "PAGO TARJETA CREDITO",        -6500.00, "UYU", "Fees & Charges"),
    ("2026-01-17", "RETIRO CAJERO ATM",           -2000.00, "UYU", "Other"),
    ("2026-01-19", "YPF COMBUSTIBLES",            -1800.00, "UYU", "Transport"),  # duplicate of Itaú 19
    ("2026-01-20", "ANTEL FIBRA OPTICA",           -650.00, "UYU", "Utilities"),
    ("2026-01-21", "SUPERMERCADO DISCO",          -2100.00, "UYU", "Groceries"),
    ("2026-01-22", "COBRO FREELANCE APP",          7200.00, "UYU", "Freelance"),
    ("2026-01-24", "RETIRO CAJERO ATM",           -2500.00, "UYU", "Other"),
    ("2026-01-25", "RESTAURANT LA PASCUALA",      -1560.00, "UYU", "Dining"),     # duplicate of Itaú 10
    ("2026-01-27", "ABITAB PAGO DGI",             -3200.00, "UYU", "Fees & Charges"),
    ("2026-01-28", "SUPERMERCADO TIENDA INGLESA", -1890.00, "UYU", "Groceries"),
    ("2026-01-29", "RETIRO CAJERO ATM",           -2000.00, "UYU", "Other"),
    ("2026-01-30", "PAGO MUTUAL MEDICA",          -1100.00, "UYU", "Healthcare"),
    ("2026-01-31", "SALDO FINAL",                    0.00,  "UYU", "Other"),
]

# Wise - USD Account (USD) — PDF
WISE_TRANSACTIONS = [
    ("2026-01-02", "GITHUB COPILOT",               -10.00,  "USD", "Education"),
    ("2026-01-03", "DIGITAL OCEAN DROPLET",        -24.00,  "USD", "Utilities"),
    ("2026-01-05", "REMOTE JOB PAYMENT JAN",      1800.00,  "USD", "Salary"),
    ("2026-01-06", "ADOBE CREATIVE CLOUD",         -54.99,  "USD", "Shopping"),
    ("2026-01-07", "NAMECHEAP DOMAIN",             -15.88,  "USD", "Utilities"),
    ("2026-01-08", "CHATGPT PLUS",                 -20.00,  "USD", "Education"),
    ("2026-01-10", "AMAZON WEB SERVICES",          -38.42,  "USD", "Utilities"),
    ("2026-01-12", "UDEMY COURSE PURCHASE",        -29.99,  "USD", "Education"),
    ("2026-01-14", "FIGMA PROFESSIONAL",           -15.00,  "USD", "Education"),
    ("2026-01-15", "TRANSFER TO LOCAL BANK",      -500.00,  "USD", "Transfer"),
    ("2026-01-16", "NOTION TEAM PLAN",             -16.00,  "USD", "Education"),
    ("2026-01-17", "DIGITAL OCEAN DROPLET",        -24.00,  "USD", "Utilities"),  # suspicious: same merchant Jan 3
    ("2026-01-18", "UPWORK FREELANCE INCOME",      620.00,  "USD", "Freelance"),
    ("2026-01-20", "GITHUB COPILOT",               -10.00,  "USD", "Education"),  # duplicate of Jan 2
    ("2026-01-21", "ANTHROPIC API USAGE",          -42.80,  "USD", "Education"),
    ("2026-01-22", "ZOOM SUBSCRIPTION",            -15.99,  "USD", "Utilities"),
    ("2026-01-23", "AMAZON WEB SERVICES",          -41.17,  "USD", "Utilities"),
    ("2026-01-24", "1PASSWORD FAMILY",              -4.99,  "USD", "Utilities"),
    ("2026-01-25", "UPWORK FREELANCE INCOME",      480.00,  "USD", "Freelance"),
    ("2026-01-27", "ANTHROPIC API USAGE",         -189.40,  "USD", "Education"),  # suspicious: 4x normal spend
    ("2026-01-28", "TAILSCALE VPN",                -18.00,  "USD", "Utilities"),
    ("2026-01-30", "DIGITAL OCEAN DROPLET",        -24.00,  "USD", "Utilities"),
    ("2026-01-31", "GITHUB COPILOT",               -10.00,  "USD", "Education"),  # suspicious: 3rd charge in month
]

# VISA Credit Card — PDF
VISA_TRANSACTIONS = [
    ("2026-01-03", "SUPERMERCADO DISCO",          -3200.00, "UYU", "Groceries"),
    ("2026-01-04", "RESTAURANT PANORAMICO",       -4800.00, "UYU", "Dining"),
    ("2026-01-05", "ZARA PUNTA CARRETAS",         -5600.00, "UYU", "Shopping"),
    ("2026-01-06", "FARMACIA DEL PUEBLO",          -420.00, "UYU", "Healthcare"), # duplicate of Itaú/BROU
    ("2026-01-07", "SPOTIFY",                      -360.00, "UYU", "Entertainment"),  # duplicate of Itaú 24
    ("2026-01-09", "APPLE STORE",                -12800.00, "UYU", "Shopping"),   # suspicious: large purchase
    ("2026-01-10", "UBER",                         -380.00, "UYU", "Transport"),  # duplicate of Itaú 09
    ("2026-01-11", "SUPERMERCADO TIENDA INGLESA", -2890.00, "UYU", "Groceries"),
    ("2026-01-13", "RESTAURANT DON PEPERONE",     -1980.00, "UYU", "Dining"),
    ("2026-01-14", "DECATHLON MONTEVIDEO",        -3400.00, "UYU", "Shopping"),
    ("2026-01-15", "NETFLIX",                      -720.00, "UYU", "Entertainment"), # duplicate of Itaú 08
    ("2026-01-16", "HOTEL COTTAGE COLONIA",       -8900.00, "UYU", "Travel"),
    ("2026-01-17", "YPF COMBUSTIBLES",            -1800.00, "UYU", "Transport"),
    ("2026-01-18", "SUPERMERCADO DISCO",          -2640.00, "UYU", "Groceries"),
    ("2026-01-19", "LIBRERIAS EL CLUB",            -780.00, "UYU", "Shopping"),
    ("2026-01-20", "RAPPI DELIVERY",               -640.00, "UYU", "Dining"),     # duplicate of Itaú 18
    ("2026-01-21", "CINES LIFE ALFAVILLE",         -780.00, "UYU", "Entertainment"), # duplicate of Itaú 28 (1 day)
    ("2026-01-22", "GIMNASIO SMART FIT",           -990.00, "UYU", "Healthcare"), # duplicate of Itaú 16
    ("2026-01-23", "RESTAURANT LA TABLA",         -2200.00, "UYU", "Dining"),
    ("2026-01-24", "FARMACIA URUGUAYA",            -890.00, "UYU", "Healthcare"), # duplicate of Itaú 26
    ("2026-01-25", "IKEA ONLINE",                 -6700.00, "UYU", "Shopping"),
    ("2026-01-26", "UBER",                         -460.00, "UYU", "Transport"),
    ("2026-01-28", "SUPERMERCADO TIENDA INGLESA", -3100.00, "UYU", "Groceries"),
    ("2026-01-29", "ANTEL TELEFONIA",              -890.00, "UYU", "Utilities"),  # duplicate of Itaú 03
    ("2026-01-30", "RESTAURANT LA CIGALE",        -3200.00, "UYU", "Dining"),     # duplicate of Itaú 29
    ("2026-01-31", "PAGO MINIMO VISA",            -5000.00, "UYU", "Fees & Charges"),
]


# ── XLSX generators ──────────────────────────────────────────────────────────

HEADER_FILL  = PatternFill("solid", start_color="003366")
HEADER_FONT  = Font(bold=True, color="FFFFFF", name="Arial", size=10)
CREDIT_FONT  = Font(color="1F7A1F", name="Arial", size=10)
DEBIT_FONT   = Font(color="CC0000", name="Arial", size=10)
ROW_FILL_ALT = PatternFill("solid", start_color="EEF4FF")
THIN         = Side(style="thin", color="CCCCCC")
BORDER       = Border(bottom=Border(bottom=THIN).bottom)


def _write_xlsx(path, sheet_name, bank_header_lines, columns, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name

    # Bank header block
    for i, line in enumerate(bank_header_lines, 1):
        ws.cell(row=i, column=1, value=line).font = Font(bold=(i == 1), name="Arial", size=11 if i == 1 else 9)
    ws.merge_cells("A1:F1")

    header_row = len(bank_header_lines) + 2

    # Column headers
    for col_idx, col_name in enumerate(columns, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=col_name)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")

    # Data rows
    for r_idx, row in enumerate(rows, header_row + 1):
        alt = r_idx % 2 == 0
        for c_idx, val in enumerate(row, 1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.font = Font(name="Arial", size=10)
            if alt:
                cell.fill = ROW_FILL_ALT
            # Color amount column
            if c_idx == 3:
                cell.font = CREDIT_FONT if (val or 0) >= 0 else DEBIT_FONT
                cell.number_format = '#,##0.00'

    # Totals row
    total_row = header_row + len(rows) + 1
    ws.cell(total_row, 1, "TOTAL").font = Font(bold=True, name="Arial", size=10)
    amt_col = get_column_letter(3)
    ws.cell(total_row, 3, f"=SUM({amt_col}{header_row+1}:{amt_col}{header_row+len(rows)})").font = Font(bold=True, name="Arial", size=10)
    ws.cell(total_row, 3).number_format = '#,##0.00'

    # Column widths
    widths = [14, 36, 14, 10, 24, 16]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    wb.save(path)


def generate_itau_xlsx():
    path = f"{OUT}/itau_cuenta_corriente_enero_2026.xlsx"
    _write_xlsx(
        path,
        sheet_name="Cuenta Corriente",
        bank_header_lines=[
            "Banco Itaú Uruguay S.A.",
            "Cuenta Corriente N° ****-4821",
            "Titular: JUAN CARLOS RODRIGUEZ",
            "Período: 01/01/2026 - 31/01/2026",
        ],
        columns=["Fecha", "Descripción", "Importe (UYU)", "Moneda", "Cuenta", "Referencia"],
        rows=[
            (d, m, a, c, "Itaú Cuenta Corriente ****4821", f"REF{1000+i:04d}")
            for i, (d, m, a, c, _) in enumerate(ITAU_TRANSACTIONS)
        ],
    )
    logger.info("Generated %s", path)


def generate_brou_xlsx():
    path = f"{OUT}/brou_caja_ahorros_enero_2026.xlsx"
    _write_xlsx(
        path,
        sheet_name="Caja de Ahorros",
        bank_header_lines=[
            "Banco de la República Oriental del Uruguay (BROU)",
            "Caja de Ahorros N° ****-7203",
            "Titular: JUAN CARLOS RODRIGUEZ",
            "Período: 01/01/2026 - 31/01/2026",
        ],
        columns=["Fecha", "Descripción", "Importe (UYU)", "Moneda", "Cuenta", "Comprobante"],
        rows=[
            (d, m, a, c, "BROU Caja de Ahorros ****7203", f"BROU{2000+i:04d}")
            for i, (d, m, a, c, _) in enumerate(BROU_TRANSACTIONS)
        ],
    )
    logger.info("Generated %s", path)


# ── PDF generators ───────────────────────────────────────────────────────────

def _build_pdf(path, title, subtitle, account_info, transactions, currency_note):
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    title_style = ParagraphStyle("BankTitle", fontSize=16, fontName="Helvetica-Bold",
                                  textColor=colors.HexColor("#003366"), spaceAfter=4)
    sub_style   = ParagraphStyle("BankSub",   fontSize=10, fontName="Helvetica",
                                  textColor=colors.HexColor("#555555"), spaceAfter=2)
    note_style  = ParagraphStyle("Note",      fontSize=8,  fontName="Helvetica-Oblique",
                                  textColor=colors.grey, spaceAfter=12)

    story = []
    story.append(Paragraph(title, title_style))
    story.append(Paragraph(subtitle, sub_style))
    for line in account_info:
        story.append(Paragraph(line, sub_style))
    story.append(Paragraph(currency_note, note_style))
    story.append(Spacer(1, 0.3*cm))

    # Table header + rows
    header = ["Date", "Description", "Amount", "Currency", "Account"]
    table_data = [header]
    for d, m, a, c, _ in transactions:
        fmt_amount = f"+{a:,.2f}" if a >= 0 else f"{a:,.2f}"
        table_data.append([d, m, fmt_amount, c, _get_account_short(title)])

    col_widths = [2.4*cm, 7.5*cm, 2.6*cm, 2.0*cm, 3.0*cm]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0),  colors.HexColor("#003366")),
        ("TEXTCOLOR",    (0,0), (-1,0),  colors.white),
        ("FONTNAME",     (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,0),  9),
        ("ALIGN",        (0,0), (-1,0),  "CENTER"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, colors.HexColor("#EEF4FF")]),
        ("FONTNAME",     (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",     (0,1), (-1,-1), 8),
        ("ALIGN",        (2,1), (2,-1),  "RIGHT"),
        ("GRID",         (0,0), (-1,-1), 0.3, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",   (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
    ]))

    # Color amounts
    for row_idx, (_, _, amount, _, _) in enumerate(transactions, 1):
        color = colors.HexColor("#1F7A1F") if amount >= 0 else colors.HexColor("#CC0000")
        t.setStyle(TableStyle([("TEXTCOLOR", (2, row_idx), (2, row_idx), color)]))

    story.append(t)
    story.append(Spacer(1, 0.5*cm))

    # Summary
    total = sum(a for _, _, a, _, _ in transactions)
    credits = sum(a for _, _, a, _, _ in transactions if a > 0)
    debits  = sum(a for _, _, a, _, _ in transactions if a < 0)
    currency = transactions[0][3]

    summary_data = [
        ["Total Credits:", f"+{credits:,.2f} {currency}"],
        ["Total Debits:",  f"{debits:,.2f} {currency}"],
        ["Net Balance:",   f"{total:,.2f} {currency}"],
    ]
    st = Table(summary_data, colWidths=[4*cm, 5*cm])
    st.setStyle(TableStyle([
        ("FONTNAME",  (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE",  (0,0), (-1,-1), 9),
        ("FONTNAME",  (0,0), (0,-1),  "Helvetica-Bold"),
        ("ALIGN",     (1,0), (1,-1),  "RIGHT"),
        ("LINEABOVE", (0,0), (-1,0),  0.5, colors.HexColor("#003366")),
        ("TOPPADDING",(0,0), (-1,-1), 3),
    ]))
    story.append(st)
    doc.build(story)
    logger.info("Generated %s", path)


def _get_account_short(title):
    if "Wise" in title:
        return "Wise USD ****9134"
    return "VISA ****2847"


def generate_wise_pdf():
    _build_pdf(
        path=f"{OUT}/wise_usd_account_january_2026.pdf",
        title="Wise – USD Account Statement",
        subtitle="Account Number: ****9134  |  Statement Period: January 1–31, 2026",
        account_info=[
            "Account Holder: Juan Carlos Rodriguez",
            "Account Type: USD Borderless Account",
            "IBAN: BE30 9670 3766 9134",
        ],
        transactions=WISE_TRANSACTIONS,
        currency_note="All amounts in USD. This statement is generated automatically by Wise Europe SA.",
    )


def generate_visa_pdf():
    _build_pdf(
        path=f"{OUT}/visa_credit_card_january_2026.pdf",
        title="VISA Credit Card – Monthly Statement",
        subtitle="Card Number: **** **** **** 2847  |  Period: 01/01/2026 – 31/01/2026",
        account_info=[
            "Cardholder: JUAN CARLOS RODRIGUEZ",
            "Issuer: Banco Itaú Uruguay S.A. – Tarjetas de Crédito",
            "Credit Limit: UYU 80,000  |  Available Credit: UYU 21,841",
        ],
        transactions=VISA_TRANSACTIONS,
        currency_note="All amounts in UYU (Uruguayan Peso). Minimum payment due: UYU 5,000 by 15/02/2026.",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generate_itau_xlsx()
    generate_brou_xlsx()
    generate_wise_pdf()
    generate_visa_pdf()
    logger.info("All 4 sample files generated.")