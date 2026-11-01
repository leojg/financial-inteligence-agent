"""Build realistic statement PDFs for tests (reportlab, absolute positions).

Real statements differ from the synthetic samples in data/: day-first dates,
comma decimals, separate Débito/Crédito columns, a running Saldo, wrapped
descriptions, several pages with the header repeated. This helper draws those
on purpose so readers are tested against them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


@dataclass
class Column:
    """A table column: header label, x position, and alignment of its values."""

    label: str
    x: float
    align: str = "left"  # "left" or "right" (numbers are usually right-aligned)


@dataclass
class StatementLayout:
    """What to draw: header lines, table columns and rows, footer lines."""

    title_lines: list[str]
    columns: list[Column]
    rows: list[
        list[str]
    ]  # one cell per column; "\n" in a cell wraps it onto a second line
    footer_lines: list[str] = field(default_factory=list)
    rows_per_page: int = 30
    page_header: str | None = None  # printed on pages 2+ (e.g. bank and account)
    page_numbers: bool = True  # "Página n de m" at the bottom of each page


def build_statement_pdf(path: Path, layout: StatementLayout) -> Path:
    """Draw the statement and return its path."""
    width, height = A4
    pdf = canvas.Canvas(str(path), pagesize=A4)
    chunks = [
        layout.rows[i : i + layout.rows_per_page]
        for i in range(0, max(len(layout.rows), 1), layout.rows_per_page)
    ] or [[]]
    line_height = 14

    for page_number, chunk in enumerate(chunks, start=1):
        y = height - 50
        pdf.setFont("Helvetica", 9)
        lines = (
            layout.title_lines
            if page_number == 1
            else [layout.page_header]
            if layout.page_header
            else []
        )
        for text in lines:
            pdf.drawString(40, y, text)
            y -= line_height
        y -= 8
        pdf.setFont("Helvetica-Bold", 9)
        for column in layout.columns:
            draw = pdf.drawRightString if column.align == "right" else pdf.drawString
            draw(column.x, y, column.label)
        y -= line_height
        pdf.setFont("Helvetica", 9)
        for row in chunk:
            extra_lines = 0
            for column, cell in zip(layout.columns, row):
                parts = cell.split("\n") if cell else [""]
                draw = (
                    pdf.drawRightString if column.align == "right" else pdf.drawString
                )
                for offset, part in enumerate(parts):
                    if part:
                        draw(column.x, y - offset * line_height, part)
                extra_lines = max(extra_lines, len(parts) - 1)
            y -= line_height * (1 + extra_lines)
        if page_number == len(chunks):
            y -= 8
            for text in layout.footer_lines:
                pdf.drawString(40, y, text)
                y -= line_height
        if layout.page_numbers:
            pdf.drawString(width - 120, 30, f"Página {page_number} de {len(chunks)}")
        pdf.showPage()
    pdf.save()
    return path


# ── Ready-made layouts ────────────────────────────────────────────────────────


DEPOSIT_COLUMNS = [
    Column("Fecha", 40),
    Column("Descripción", 100),
    Column("Débito", 380, "right"),
    Column("Crédito", 460, "right"),
    Column("Saldo", 545, "right"),
]


def deposit_layout(rows_per_page: int = 30) -> StatementLayout:
    """Return a Uruguayan-style savings account statement that closes exactly.

    Opening 15.000,00; movements below; closing 5.459,50. The rent payment
    description wraps onto a second line; dates are dd/mm/yyyy; a running Saldo.
    """
    rows = [
        ["01/01/2026", "SALDO ANTERIOR", "", "", "15.000,00"],
        ["03/01/2026", "PAGO ALQUILER\nAPTO 302 ENERO", "8.500,00", "", "6.500,00"],
        ["05/01/2026", "TRANSFERENCIA RECIBIDA ITAU", "", "4.000,00", "10.500,00"],
        ["08/01/2026", "UTE FACTURA", "2.340,50", "", "8.159,50"],
        ["12/01/2026", "COMPRA DISCO", "1.200,00", "", "6.959,50"],
        ["20/01/2026", "RETIRO CAJERO", "1.500,00", "", "5.459,50"],
    ]
    return StatementLayout(
        title_lines=[
            "Banco de la República Oriental del Uruguay",
            "Caja de Ahorros N° ****-7203   Moneda: $",
            "Titular: TITULAR DE PRUEBA",
            "Período: 01/01/2026 al 31/01/2026",
        ],
        columns=DEPOSIT_COLUMNS,
        rows=rows,
        footer_lines=[
            "SALDO FINAL 5.459,50",
            "Total débitos 13.540,50   Total créditos 4.000,00",
        ],
        rows_per_page=rows_per_page,
        page_header="BROU - Caja de Ahorros ****-7203",
    )
