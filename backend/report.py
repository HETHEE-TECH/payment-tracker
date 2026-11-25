"""A PDF report of the payments: totals, pie chart, month-wise numbers, categories and the list.

The filters and the totals here follow the same rules as the page (frontend/script.js), so a report shows the
same numbers as the dashboard for the same filters:
  - Paid and Cancelled count the payments with that status; Delayed counts every delayed payment (also one that is
    Paid or Cancelled as well); Pending counts the rest.
  - The pie chart, the bars and the month rows put each payment in one place (Paid or Cancelled, then Delayed, then Pending).
  - Awaiting income, Awaiting income received, We paid, Excess paid and Part paid pending are typed-in amounts. They
    are listed on their own and are never part of the totals.
"""
import io
import os
from datetime import date
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

STATUSES = [("paid", "Paid"), ("pending", "Pending"), ("delayed", "Delayed"), ("cancelled", "Cancelled")]
STATUS_COLORS = {"paid": "#22a05a", "pending": "#5b7fa6", "delayed": "#e3ad0b", "cancelled": "#d64545"}
ROW_TINTS = {"paid": "#e4f4ea", "delayed": "#fdf3cf", "cancelled": "#fbe1e1", "pending": "#ffffff"}
# the filter key, the column, the name
EXTRAS = [
    ("awaiting", "awaiting_amount", "Awaiting income", "#8a5cd0"),
    ("received", "received_amount", "Awaiting income received", "#c2418a"),
    ("we_paid", "we_paid_amount", "We paid", "#139c96"),
    ("excess", "excess_amount", "Excess paid", "#a8693a"),
    ("part_pending", "part_pending_amount", "Part paid pending", "#d97706"),
]
EXTRA_FIELD = {key: field for key, field, _name, _color in EXTRAS}
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
NO_CATEGORY = "__none__"
MAX_TREND_MONTHS = 12
FONT_DIRS = [os.path.join(os.path.dirname(__file__), "fonts"), "/usr/share/fonts/truetype/dejavu"]


# ---------- the same rules as the page ----------

def has_extra(item: dict) -> bool:
    """True when the payment has a balance amount: Awaiting income, received, We paid, Excess paid or Part paid pending."""
    return any(item.get(field) for _key, field, _name, _color in EXTRAS)


def counts_in_total(item: dict) -> bool:
    """Total amount takes Paid, Delayed and Pending payments without a balance amount; a Paid payment always counts;
    Cancelled never does."""
    status = item.get("status", "pending")
    if status == "paid":
        return True
    if status == "cancelled":
        return False
    return not has_extra(item)


def is_delayed(item: dict) -> bool:
    return bool(item.get("new_date")) or item.get("status") == "delayed"


def main_status(item: dict) -> str:
    return "pending" if item.get("status") == "delayed" else item.get("status", "pending")


def slice_of(item: dict) -> str:
    status = main_status(item)
    if status != "pending":
        return status
    return "delayed" if is_delayed(item) else "pending"


def matches_status(item: dict, wanted: str) -> bool:
    if not wanted:
        return True
    if wanted in EXTRA_FIELD:
        return bool(item.get(EXTRA_FIELD[wanted]))
    if wanted == "delayed":
        return is_delayed(item)
    if wanted == "pending":
        return main_status(item) == "pending" and not is_delayed(item)
    return main_status(item) == wanted


def matches_category(item: dict, wanted: str) -> bool:
    if not wanted:
        return True
    have = (item.get("category") or "").strip().lower()
    return have == "" if wanted == NO_CATEGORY else have == wanted.strip().lower()


def filter_items(items: List[dict], q: str = "", status: str = "", year: str = "", month: str = "", day: str = "",
                 category: str = "") -> List[dict]:
    """The payments the page would list for these filters."""
    q = (q or "").strip().lower()
    return [
        i for i in items
        if (not q or q in i["name"].lower())
        and matches_status(i, status)
        and matches_category(i, category)
        and (not year or i["date"][:4] == year)
        and (not month or i["date"][5:7] == month)
        and (not day or i["date"][8:10] == day)
    ]


def summarize(items: List[dict]) -> dict:
    blank = lambda: {"count": 0, "amount": 0.0}  # noqa: E731
    s = {k: blank() for k in ("pending", "paid", "delayed", "cancelled", "total", "to_pay")}
    s["slices"] = {k: blank() for k in ("pending", "paid", "delayed", "cancelled")}
    s["extras"] = {field: blank() for _key, field, _name, _color in EXTRAS}

    def add(bucket, item):
        bucket["count"] += 1
        bucket["amount"] += item["amount"]

    for i in items:
        status = main_status(i)
        if counts_in_total(i):
            add(s["total"], i)
        add(s["slices"][slice_of(i)], i)
        if status in ("paid", "cancelled"):
            add(s[status], i)
        elif not is_delayed(i):
            add(s["pending"], i)
        if is_delayed(i):
            add(s["delayed"], i)
        if status == "pending" and not has_extra(i):
            add(s["to_pay"], i)
        for _key, field, _name, _color in EXTRAS:
            if i.get(field):
                s["extras"][field]["count"] += 1
                s["extras"][field]["amount"] += i[field]
    return s


def month_rows(items: List[dict]) -> List[dict]:
    """One row per month (oldest first) with each payment counted once."""
    months: Dict[str, dict] = {}
    for i in items:
        key = i["date"][:7]
        row = months.setdefault(key, {"key": key, "total": 0.0, "count": 0, "paid": 0.0, "pending": 0.0, "delayed": 0.0, "cancelled": 0.0})
        row["total"] += i["amount"]
        row["count"] += 1
        row[slice_of(i)] += i["amount"]
    return [months[k] for k in sorted(months)]


def category_rows(items: List[dict]) -> List[dict]:
    """The amount of each category (biggest first, 'No category' last)."""
    names: Dict[str, dict] = {}
    for i in items:
        label = (i.get("category") or "").strip()
        key = label.lower()
        row = names.setdefault(key, {"label": label or "No category", "count": 0, "amount": 0.0, "none": not label})
        row["count"] += 1
        row["amount"] += i["amount"]
    return sorted(names.values(), key=lambda r: (r["none"], -r["amount"], r["label"].lower()))


def scope_label(q: str = "", status: str = "", year: str = "", month: str = "", day: str = "", category: str = "") -> str:
    """What the numbers cover, for example 'November 2026 - Paid'."""
    parts = []
    when = " ".join(str(p) for p in (int(day) if day else "", MONTH_NAMES[int(month) - 1] if month else "", year) if p)
    if when:
        parts.append(when)
    if status:
        names = dict(STATUSES)
        names.update({key: name for key, _field, name, _color in EXTRAS})
        parts.append(names.get(status, status))
    if category:
        parts.append("No category" if category == NO_CATEGORY else category)
    if (q or "").strip():
        parts.append("\u201c" + q.strip() + "\u201d")
    return " \u00b7 ".join(parts) if parts else "All payments"


# ---------- the PDF ----------

def _fonts():
    """(regular, bold, rupee sign): DejaVu Sans has the rupee sign. Without the font files it is Helvetica and 'Rs.'."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for folder in FONT_DIRS:
        regular, bold = os.path.join(folder, "DejaVuSans.ttf"), os.path.join(folder, "DejaVuSans-Bold.ttf")
        if os.path.exists(regular) and os.path.exists(bold):
            if "ReportSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("ReportSans", regular))
                pdfmetrics.registerFont(TTFont("ReportSans-Bold", bold))
            return "ReportSans", "ReportSans-Bold", "\u20b9"
    return "Helvetica", "Helvetica-Bold", "Rs. "


def indian(amount: float) -> str:
    """12,34,567.50: the last three digits, then groups of two."""
    amount = round(float(amount), 2)
    whole, _, fraction = f"{abs(amount):.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    text = ",".join(groups + [tail])
    if fraction != "00":
        text += "." + fraction
    return ("-" if amount < 0 else "") + text


def build_report_pdf(selected: List[dict], trend_items: List[dict], scope: str, today: Optional[date] = None) -> bytes:
    """selected: the payments of the filters. trend_items: the same without the date filters (for the month rows)."""
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold, rupee = _fonts()
    money = lambda n: rupee + indian(n)  # noqa: E731
    today = today or date.today()
    text = ParagraphStyle("t", fontName=regular, fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=text, fontSize=8, leading=10, textColor=colors.HexColor("#5b6862"))
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=20, leading=24, spaceAfter=2)
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=12, leading=15, spaceBefore=12, spaceAfter=5, keepWithNext=1)
    cell = ParagraphStyle("c", parent=text, fontSize=8.5, leading=10.5)
    right = ParagraphStyle("r", parent=cell, alignment=2)
    para = lambda value, style=cell: Paragraph(escape(str(value)), style)  # noqa: E731
    line = colors.HexColor("#cdd5d1")
    head_bg = colors.HexColor("#eef2f0")

    def table(rows, widths, tints=None, header=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        style = [
            ("FONTNAME", (0, 0), (-1, -1), regular), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        if header:
            style += [("BACKGROUND", (0, 0), (-1, 0), head_bg), ("FONTNAME", (0, 0), (-1, 0), bold)]
        for n, tint in enumerate(tints or [], start=1 if header else 0):
            if tint:
                style.append(("BACKGROUND", (0, n), (-1, n), colors.HexColor(tint)))
        t.setStyle(TableStyle(style))
        return t

    s = summarize(selected)
    total = s["total"]["amount"]
    base = sum(s["slices"][key]["amount"] for key, _name in STATUSES)  # every payment, for the shares and the pie chart
    def pct(part):
        if not base or part <= 0:
            return "0%"
        return f"{round(part / base * 100)}%" if part / base >= 0.005 else "<1%"
    story = [
        Paragraph("Payment report", h1),
        Paragraph(escape(scope), ParagraphStyle("sc", parent=text, fontSize=11, leading=14)),
        Paragraph(f"Made on {today.strftime('%d %B %Y')}", small),
        Spacer(1, 8),
    ]

    # totals
    big = ParagraphStyle("big", fontName=bold, fontSize=17, leading=21)
    story.append(table(
        [[para("Total amount (Paid, Delayed, Pending)", small), para("Still to pay", small)],
         [Paragraph(escape(money(total)), big), Paragraph(escape(money(s["to_pay"]["amount"])), big)],
         [para(f"{s['total']['count']} payments", small), para(f"{s['to_pay']['count']} payments", small)]],
        [90 * mm, 90 * mm], header=False))

    # the four statuses
    story.append(Paragraph("Status", h2))
    rows = [["Status", "Payments", "Amount", "Share of total"]]
    for key, name in STATUSES:
        rows.append([name, s[key]["count"], money(s[key]["amount"]), pct(s[key]["amount"])])
    t = table(rows, [60 * mm, 30 * mm, 50 * mm, 40 * mm])
    t.setStyle(TableStyle([("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
    story += [t, Spacer(1, 3), Paragraph(
        "A payment that is both Paid and Delayed is in the Paid row and in the Delayed row, so the rows can add up to more than the total.", small)]

    # the typed-in amounts
    story.append(Paragraph("Typed-in amounts (not part of the total)", h2))
    rows = [["Amount", "Payments", "Amount"]]
    for _key, field, name, _color in EXTRAS:
        rows.append([name, s["extras"][field]["count"], money(s["extras"][field]["amount"])])
    t = table(rows, [90 * mm, 40 * mm, 50 * mm])
    t.setStyle(TableStyle([("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
    story.append(t)

    # pie chart: each payment in one slice
    slices = [(name, s["slices"][key]["amount"], STATUS_COLORS[key]) for key, name in STATUSES]
    if base > 0:
        story.append(Paragraph("Pie chart", h2))
        drawing = Drawing(180 * mm, 62 * mm)
        pie = Pie()
        pie.x, pie.y, pie.width, pie.height = 8 * mm, 4 * mm, 54 * mm, 54 * mm
        shown = [d for d in slices if d[1] > 0]
        pie.data = [d[1] for d in shown]
        pie.labels = [f"{round(d[1] / base * 100)}%" if d[1] / base >= 0.06 else "" for d in shown]  # no label on a thin slice
        pie.sideLabels = False
        pie.simpleLabels = False
        pie.slices.strokeColor = colors.white
        pie.slices.strokeWidth = 1
        pie.slices.fontName = bold
        pie.slices.fontSize = 8
        pie.slices.labelRadius = 0.68
        for n, d in enumerate(shown):
            pie.slices[n].fillColor = colors.HexColor(d[2])
            pie.slices[n].fontColor = colors.white
        drawing.add(pie)
        for n, (name, amount, color) in enumerate(slices):
            y = 50 * mm - n * 11 * mm
            drawing.add(Rect(78 * mm, y, 4 * mm, 4 * mm, fillColor=colors.HexColor(color), strokeColor=None))
            drawing.add(String(85 * mm, y + 0.6 * mm, name, fontName=bold, fontSize=9))
            drawing.add(String(85 * mm, y - 4 * mm, f"{money(amount)}  \u00b7  {pct(amount)}", fontName=regular, fontSize=8.5, fillColor=colors.HexColor("#5b6862")))
        story.append(drawing)

    # month-wise: numbers and, for two or more months, stacked bars
    months = month_rows(trend_items)
    if months:
        story.append(Paragraph("Month-wise total", h2))
        shown_months = months[-MAX_TREND_MONTHS:]
        if len(months) > MAX_TREND_MONTHS:
            story.append(Paragraph(f"Showing the latest {MAX_TREND_MONTHS} months of {len(months)}.", small))
        if len(shown_months) >= 2:
            chart = Drawing(180 * mm, 65 * mm)
            bc = VerticalBarChart()
            bc.x, bc.y, bc.width, bc.height = 22 * mm, 10 * mm, 150 * mm, 46 * mm
            bc.data = [[m[key] for m in shown_months] for key, _name in STATUSES]  # paid at the bottom
            bc.categoryAxis.categoryNames = [f"{MONTH_NAMES[int(m['key'][5:7]) - 1][:3]} {m['key'][2:4]}" for m in shown_months]
            bc.categoryAxis.style = "stacked"
            bc.categoryAxis.labels.fontName = regular
            bc.categoryAxis.labels.fontSize = 7
            bc.valueAxis.labels.fontName = regular
            bc.valueAxis.labels.fontSize = 7
            bc.valueAxis.valueMin = 0
            bc.valueAxis.labelTextFormat = lambda v: indian(v)
            bc.groupSpacing = 6
            for n, (key, _name) in enumerate(STATUSES):
                bc.bars[n].fillColor = colors.HexColor(STATUS_COLORS[key])
                bc.bars[n].strokeColor = None
            chart.add(bc)
            for n, (key, name) in enumerate(STATUSES):  # what the colours mean
                x = 24 * mm + n * 30 * mm
                chart.add(Rect(x, 59 * mm, 3 * mm, 3 * mm, fillColor=colors.HexColor(STATUS_COLORS[key]), strokeColor=None))
                chart.add(String(x + 4.5 * mm, 59.4 * mm, name, fontName=regular, fontSize=7.5))
            story.append(chart)
        rows = [["Month", "Total", "Paid", "Pending", "Delayed", "Cancelled", "Payments"]]
        for m in reversed(shown_months):
            rows.append([f"{MONTH_NAMES[int(m['key'][5:7]) - 1]} {m['key'][:4]}", money(m["total"]), money(m["paid"]), money(m["pending"]),
                         money(m["delayed"]), money(m["cancelled"]), m["count"]])
        grand = {k: sum(m[k] for m in months) for k in ("total", "paid", "pending", "delayed", "cancelled", "count")}
        rows.append(["All months", money(grand["total"]), money(grand["paid"]), money(grand["pending"]), money(grand["delayed"]),
                     money(grand["cancelled"]), grand["count"]])
        t = table(rows, [32 * mm, 28 * mm, 25 * mm, 25 * mm, 24 * mm, 26 * mm, 20 * mm])
        t.setStyle(TableStyle([("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("FONTNAME", (0, len(rows) - 1), (-1, len(rows) - 1), bold),
                               ("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), head_bg), ("FONTSIZE", (0, 0), (-1, -1), 7.5)]))
        story.append(t)

    # the payments
    story.append(Paragraph(f"Payments ({len(selected)})", h2))
    if selected:
        rows, tints = [["Date", "Name", "Amount", "Status", "Details"]], []
        for i in selected:
            notes = []
            for _key, field, name, _color in EXTRAS:
                if i.get(field):
                    notes.append(f"{name} {money(i[field])}")
            if i.get("description"):
                notes.append(i["description"])
            if is_delayed(i) and main_status(i) == "pending" and i.get("new_date"):
                notes.insert(0, f"Delayed to {date.fromisoformat(i['new_date']).strftime('%d %b %Y')}")
            status = main_status(i).capitalize() + (" + Delayed" if is_delayed(i) and main_status(i) != "pending" else "")
            if main_status(i) == "pending" and is_delayed(i):
                status = "Delayed"
            rows.append([date.fromisoformat(i["date"]).strftime("%d %b %Y"), para(i["name"]), para(money(i["amount"]), right),
                         para(status), para(" \u00b7 ".join(notes))])
            tints.append(ROW_TINTS[slice_of(i)])
        story.append(table(rows, [22 * mm, 38 * mm, 25 * mm, 24 * mm, 71 * mm], tints=tints))
    else:
        story.append(Paragraph("No payments for these filters.", text))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(colors.HexColor("#5b6862"))
        canvas.drawString(15 * mm, 9 * mm, f"Payment report \u00b7 {scope}")
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                      title="Payment report", author="Payment Tracker").build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()