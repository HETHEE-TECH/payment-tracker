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
import math
import os
from datetime import date
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

STATUSES = [("paid", "Paid"), ("pending", "Pending"), ("delayed", "Delayed"), ("cancelled", "Cancelled")]
MONTH_STATUSES = [s for s in STATUSES if s[0] != "cancelled"]
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
        return main_status(item) == "pending" and not is_delayed(item) and not has_extra(item)
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
        if not (slice_of(i) == "pending" and has_extra(i)):  # an amount-only payment is not Pending: it is in its own amount's card
            add(s["slices"][slice_of(i)], i)
        if status in ("paid", "cancelled"):
            add(s[status], i)
        elif not is_delayed(i) and not has_extra(i):  # a pending payment that has an amount is not "Pending"
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
        row["count"] += 1
        if counts_in_total(i):  # the same rule as the Total amount: nothing with a balance amount (unless Paid), no Cancelled
            row["total"] += i["amount"]
            row[slice_of(i)] += i["amount"]
        elif main_status(i) == "cancelled":
            row["cancelled"] += i["amount"]
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


def build_report_pdf(selected: List[dict], trend_items: List[dict], scope: str, today: Optional[date] = None,
                     only: bool = False) -> bytes:
    """selected: the payments of the filters. trend_items: the same without the date filters (for the month rows).
    only: a status was chosen, so the report leaves out every status, column and amount that has no payment in it."""
    from reportlab.graphics.charts.barcharts import VerticalBarChart
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
    # Only pending payments that have an amount (for example the Awaiting income filter): they are not "Pending", so no status split.
    amount_only = bool(only and selected and all(main_status(i) == "pending" and not is_delayed(i) and has_extra(i) for i in selected))
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
    if only and not s["to_pay"]["count"]:  # for example Paid only: there is nothing still to pay
        story.append(table(
            [[para("Total amount (Paid, Delayed, Pending)", small)],
             [Paragraph(escape(money(total)), big)],
             [para(f"{s['total']['count']} payments", small)]],
            [180 * mm], header=False))
    else:
        story.append(table(
            [[para("Total amount (Paid, Delayed, Pending)", small), para("Still to pay", small)],
             [Paragraph(escape(money(total)), big), Paragraph(escape(money(s["to_pay"]["amount"])), big)],
             [para(f"{s['total']['count']} payments", small), para(f"{s['to_pay']['count']} payments", small)]],
            [90 * mm, 90 * mm], header=False))

    # the four statuses
    rows = [["Status", "Payments", "Amount", "Share of total"]]
    for key, name in STATUSES:
        if only and not s[key]["count"]:
            continue
        rows.append([name, s[key]["count"], money(s[key]["amount"]), pct(s[key]["amount"])])
    if len(rows) > 1:
        story.append(Paragraph("All payments status", h2))
        t = table(rows, [60 * mm, 30 * mm, 50 * mm, 40 * mm])
        t.setStyle(TableStyle([("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
        story += [t]
        if not only:
            story += [Spacer(1, 3), Paragraph(
                "A payment that is both Paid and Delayed is in the Paid row and in the Delayed row, so the rows can add up to more than the total.", small)]

    # the typed-in amounts
    rows = [["Status", "Payments", "Amount"]]
    for _key, field, name, _color in EXTRAS:
        if only and not s["extras"][field]["count"]:
            continue
        rows.append([name, s["extras"][field]["count"], money(s["extras"][field]["amount"])])
    if len(rows) > 1:
        story.append(Paragraph("Typed-in amounts (not part of the total)", h2))
        t = table(rows, [90 * mm, 40 * mm, 50 * mm])
        t.setStyle(TableStyle([("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
        story.append(t)

    # pie chart: each payment in one slice
    slices = [(name, s["slices"][key]["amount"], STATUS_COLORS[key]) for key, name in STATUSES
              if not only or s["slices"][key]["count"]]
    if base > 0 and not amount_only:
        story.append(Paragraph("Total amount: doughnut chart", h2))
        from reportlab.graphics.charts.doughnut import Doughnut
        drawing = Drawing(180 * mm, 62 * mm)
        pie = Doughnut()
        pie.x, pie.y, pie.width, pie.height = 8 * mm, 4 * mm, 54 * mm, 54 * mm
        shown = [d for d in slices if d[1] > 0]
        pie.data = [d[1] for d in shown]
        pie.slices.strokeColor = colors.white
        pie.slices.strokeWidth = 1
        for n, d in enumerate(shown):
            pie.slices[n].fillColor = colors.HexColor(d[2])
        drawing.add(pie)
        drawing.add(String(35 * mm, 33 * mm, money(base), fontName=bold, fontSize=9, textAnchor="middle"))
        drawing.add(String(35 * mm, 28 * mm, "total", fontName=regular, fontSize=7.5, textAnchor="middle", fillColor=colors.HexColor("#5b6862")))
        for n, (name, amount, color) in enumerate(slices):
            y = 50 * mm - n * 11 * mm
            drawing.add(Rect(78 * mm, y, 4 * mm, 4 * mm, fillColor=colors.HexColor(color), strokeColor=None))
            drawing.add(String(85 * mm, y + 0.6 * mm, name, fontName=bold, fontSize=9))
            drawing.add(String(85 * mm, y - 4 * mm, f"{money(amount)}  \u00b7  {pct(amount)}", fontName=regular, fontSize=8.5, fillColor=colors.HexColor("#5b6862")))
        story.append(drawing)

    # month-wise: numbers and, for two or more months, stacked bars
    months = month_rows(trend_items)
    kept = [(key, name) for key, name in STATUSES if not only or any(m[key] for m in months)]  # status columns that have an amount
    if amount_only:
        kept = []
    kept_month = [(key, name) for key, name in kept if key != "cancelled"]
    if months:
        story.append(Paragraph("Month-wise total", h2))
        shown_months = months[-MAX_TREND_MONTHS:]
        if len(months) > MAX_TREND_MONTHS:
            story.append(Paragraph(f"Showing the latest {MAX_TREND_MONTHS} months of {len(months)}.", small))
        if len(shown_months) >= 2 and kept_month:
            chart = Drawing(180 * mm, 65 * mm)
            bc = VerticalBarChart()
            bc.x, bc.y, bc.width, bc.height = 22 * mm, 10 * mm, 150 * mm, 46 * mm
            bc.data = [[m[key] for m in shown_months] for key, _name in kept_month]  # paid at the bottom; cancelled is not in a month's total
            bc.categoryAxis.categoryNames = [f"{MONTH_NAMES[int(m['key'][5:7]) - 1][:3]} {m['key'][2:4]}" for m in shown_months]
            bc.categoryAxis.style = "stacked"
            bc.categoryAxis.labels.fontName = regular
            bc.categoryAxis.labels.fontSize = 7
            bc.valueAxis.labels.fontName = regular
            bc.valueAxis.labels.fontSize = 7
            bc.valueAxis.valueMin = 0
            bc.valueAxis.labelTextFormat = lambda v: indian(v)
            bc.groupSpacing = 6
            for n, (key, _name) in enumerate(kept_month):
                bc.bars[n].fillColor = colors.HexColor(STATUS_COLORS[key])
                bc.bars[n].strokeColor = None
            chart.add(bc)
            for n, (key, name) in enumerate(kept_month):  # what the colours mean
                x = 24 * mm + n * 30 * mm
                chart.add(Rect(x, 59 * mm, 3 * mm, 3 * mm, fillColor=colors.HexColor(STATUS_COLORS[key]), strokeColor=None))
                chart.add(String(x + 4.5 * mm, 59.4 * mm, name, fontName=regular, fontSize=7.5))
            story.append(chart)
        columns = [key for key, _name in kept]
        rows = [["Month", "Total"] + [name for _key, name in kept] + ["Payments"]]
        for m in reversed(shown_months):
            rows.append([f"{MONTH_NAMES[int(m['key'][5:7]) - 1]} {m['key'][:4]}", money(m["total"])] + [money(m[k]) for k in columns] + [m["count"]])
        grand = {k: sum(m[k] for m in months) for k in ("total", "paid", "pending", "delayed", "cancelled", "count")}
        rows.append(["All months", money(grand["total"])] + [money(grand[k]) for k in columns] + [grand["count"]])
        widths = [38 * mm, 30 * mm] + [28 * mm] * len(columns) + [22 * mm]
        scale = min(1.0, 180 * mm / sum(widths))
        t = table(rows, [w * scale for w in widths])
        t.setStyle(TableStyle([("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("FONTNAME", (0, len(rows) - 1), (-1, len(rows) - 1), bold),
                               ("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), head_bg), ("FONTSIZE", (0, 0), (-1, -1), 7.5)]))
        story.append(t)
        if any(key == "cancelled" for key in columns):
            story += [Spacer(1, 3), Paragraph("A month's Total does not include its Cancelled amount (the Cancelled column is shown on its own).", small)]

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
            elif main_status(i) == "pending" and has_extra(i):  # an amount only: named after the amount, not "Pending"
                status = next(name for _key, field, name, _color in EXTRAS if i.get(field))
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

# ---------- a report for one chosen option (Paid, Delayed, Pending, Cancelled or one of the typed-in amounts) ----------

OPTION_PALETTE = ["#2f7de1", "#e0752b", "#8a5cd0", "#1f9d5a", "#d6453d", "#139c96", "#c2418a", "#a8693a"]
OTHERS_COLOR = "#9aa5a0"
MAX_PIE_NAMES = 7


def option_info(option: str):
    """(name, colour, field) of a chosen option. field is the column that holds the typed-in amount, or None for a status."""
    for key, name in STATUSES:
        if key == option:
            return name, STATUS_COLORS[key], None
    for key, field, name, color in EXTRAS:
        if key == option:
            return name, color, field
    return option.capitalize(), "#5b7fa6", None


def build_option_report_pdf(selected: List[dict], trend_items: List[dict], option: str, scope: str,
                            today: Optional[date] = None) -> bytes:
    """The report of ONE option, with only that option's data: its amount, a pie chart of the payment names that make it up,
    its months and its payments. For Paid, Delayed, Pending and Cancelled the amount is the payments' amount; for Awaiting income,
    Awaiting income received, We paid, Excess paid and Part paid pending it is the typed-in amount."""
    from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, PolyLine, Rect, String
    from reportlab.graphics.widgets.markers import makeMarker
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    def nice_axis(biggest):
        """A round top for the side axis and round steps (1, 2, 2.5, 5 x 10^n), so the numbers on the left are clean."""
        biggest = max(float(biggest), 1.0)
        raw = biggest / 4
        unit = 10 ** math.floor(math.log10(raw))
        step = next(m * unit for m in (1, 2, 2.5, 5, 10) if m * unit >= raw)
        top = math.ceil(biggest / step) * step
        return top, [step * k for k in range(int(round(top / step)) + 1)]

    niceish = lambda v: nice_axis(v)[0]  # noqa: E731
    compact = lambda v: (f"{v / 100000:.1f}L" if v >= 100000 else f"{v / 1000:.1f}k" if v >= 1000 else f"{v:.0f}")  # noqa: E731
    name, color, field = option_info(option)
    value = (lambda i: float(i.get(field) or 0)) if field else (lambda i: float(i["amount"]))
    amount_label = f"{name} amount" if not field else name

    regular, bold, rupee = _fonts()
    money = lambda n: rupee + indian(n)  # noqa: E731
    today = today or date.today()
    text = ParagraphStyle("t", fontName=regular, fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=text, fontSize=8, leading=10, textColor=colors.HexColor("#5b6862"))
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=20, leading=24, spaceAfter=2)
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=12, leading=15, spaceBefore=12, spaceAfter=5, keepWithNext=1)
    big = ParagraphStyle("big", fontName=bold, fontSize=17, leading=21)
    cell = ParagraphStyle("c", parent=text, fontSize=8.5, leading=10.5)
    right = ParagraphStyle("r", parent=cell, alignment=2)
    para = lambda v, style=cell: Paragraph(escape(str(v)), style)  # noqa: E731
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

    total = sum(value(i) for i in selected)
    count_n = len(selected)
    biggest = max(selected, key=value) if selected else None
    avg = total / count_n if count_n else 0
    tint = colors.HexColor(color)
    tile_big = ParagraphStyle("tb", fontName=bold, fontSize=22, leading=26, textColor=colors.white)
    tile_lbl = ParagraphStyle("tl", fontName=regular, fontSize=8, leading=10, textColor=colors.white)
    tile_val = ParagraphStyle("tv", fontName=bold, fontSize=13, leading=16, textColor=colors.HexColor("#1b2420"))
    tile_cap = ParagraphStyle("tc", fontName=regular, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#5b6862"))
    short = lambda s, n=18: s if len(s) <= n else s[: n - 1] + "…"  # noqa: E731
    kpi = Table([[
        [Paragraph(escape(amount_label.upper()), tile_lbl), Paragraph(escape(money(total)), tile_big),
         Paragraph(f"{count_n} {'payment' if count_n == 1 else 'payments'}", tile_lbl)],
    ]], colWidths=[180 * mm])
    kpi.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), tint), ("BOX", (0, 0), (-1, -1), 0.6, tint),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story = [
        Paragraph(escape(f"{name} report"), h1),
        Paragraph(escape(scope), ParagraphStyle("sc", parent=text, fontSize=11, leading=14)),
        Paragraph(f"Made on {today.strftime('%d %B %Y')}", small),
        Spacer(1, 8),
        kpi,
    ]

    # data for the chart: by payment name, and by month
    by_name: Dict[str, dict] = {}
    for i in selected:
        key = re_space(i["name"]).lower()
        row = by_name.setdefault(key, {"label": re_space(i["name"]), "amount": 0.0, "count": 0})
        row["amount"] += value(i)
        row["count"] += 1
    ranked = sorted((r for r in by_name.values() if r["amount"] > 0), key=lambda r: -r["amount"])
    parts = ranked[:MAX_PIE_NAMES]
    rest = ranked[MAX_PIE_NAMES:]
    if rest:
        parts.append({"label": f"Others ({len(rest)})", "amount": sum(r["amount"] for r in rest), "count": sum(r["count"] for r in rest)})
    shades = [OPTION_PALETTE[n % len(OPTION_PALETTE)] for n in range(len(parts))]
    if rest:
        shades[-1] = OTHERS_COLOR
    # only the months of the chosen filters (a chosen month or year is not mixed with other months)
    months: Dict[str, dict] = {}
    for i in selected:
        row = months.setdefault(i["date"][:7], {"key": i["date"][:7], "amount": 0.0, "count": 0})
        row["amount"] += value(i)
        row["count"] += 1
    rows_m = [months[k] for k in sorted(months)]
    shown = rows_m[-MAX_TREND_MONTHS:]
    days: Dict[str, float] = {}
    for i in selected:
        days[i["date"]] = days.get(i["date"], 0.0) + value(i)
    day_keys = sorted(days)[-31:]
    day_labels = [date.fromisoformat(k).strftime("%d %b") for k in day_keys]
    day_values = [days[k] for k in day_keys]
    running, cum_values = 0.0, []
    for v in day_values:
        running += v
        cum_values.append(running)
    month_labels = [f"{MONTH_NAMES[int(m['key'][5:7]) - 1][:3]} {m['key'][2:4]}" for m in shown]
    month_values = [m["amount"] for m in shown]

    def legend(drawing, top, left=78):
        for n, p in enumerate(parts):
            y = top - n * 11 * mm
            drawing.add(Rect(left * mm, y, 4 * mm, 4 * mm, fillColor=colors.HexColor(shades[n]), strokeColor=None))
            drawing.add(String((left + 7) * mm, y + 0.6 * mm, p["label"][:40], fontName=bold, fontSize=9))
            share = f"{round(p['amount'] / total * 100)}%" if p["amount"] / total >= 0.005 else "<1%"
            drawing.add(String((left + 7) * mm, y - 4 * mm, f"{money(p['amount'])}  ·  {share}  ·  {p['count']} {'payment' if p['count'] == 1 else 'payments'}",
                               fontName=regular, fontSize=8.5, fillColor=colors.HexColor("#5b6862")))

    def style_axes(ch, horizontal=False):
        for axis in (ch.categoryAxis, ch.valueAxis):
            axis.labels.fontName = regular
            axis.labels.fontSize = 7
        ch.valueAxis.valueMin = 0
        ch.valueAxis.labelTextFormat = lambda v: indian(v)

    def chart_pie(donut):
        if not donut:  # a plain pie: just the pie and the percentage on each slice, no side list
            d = Drawing(180 * mm, 88 * mm)
            pie = Pie()
            pie.x, pie.y, pie.width, pie.height = 50 * mm, 4 * mm, 80 * mm, 80 * mm
            pie.data = [p["amount"] for p in parts]
            pie.labels = [f"{round(p['amount'] / total * 100)}%" if p["amount"] / total >= 0.025 else "" for p in parts]
            pie.sideLabels = False
            pie.simpleLabels = False
            pie.slices.strokeColor = colors.white
            pie.slices.strokeWidth = 1.2
            pie.slices.fontName = bold
            pie.slices.fontSize = 10
            pie.slices.labelRadius = 0.72
            for n, hex_color in enumerate(shades):
                pie.slices[n].fillColor = colors.HexColor(hex_color)
                pie.slices[n].fontColor = colors.white
            d.add(pie)
            return d
        height = max(62, 12 + len(parts) * 11)
        d = Drawing(180 * mm, height * mm)
        if donut:
            from reportlab.graphics.charts.doughnut import Doughnut
            pie = Doughnut()
            pie.slices.strokeColor = colors.white
            pie.slices.strokeWidth = 1
        else:
            pie = Pie()
            pie.labels = [f"{round(p['amount'] / total * 100)}%" if p["amount"] / total >= 0.06 else "" for p in parts]
            pie.sideLabels = False
            pie.simpleLabels = False
            pie.slices.strokeColor = colors.white
            pie.slices.strokeWidth = 1
            pie.slices.fontName = bold
            pie.slices.fontSize = 8
            pie.slices.labelRadius = 0.68
        pie.x, pie.y = 8 * mm, (height - 58) / 2 * mm
        pie.width = pie.height = 54 * mm
        pie.data = [p["amount"] for p in parts]
        for n, hex_color in enumerate(shades):
            pie.slices[n].fillColor = colors.HexColor(hex_color)
            if not donut:
                pie.slices[n].fontColor = colors.white
        d.add(pie)
        if donut:  # the total in the hole
            cx, cy = (8 + 27) * mm, (height - 58) / 2 * mm + 27 * mm
            d.add(String(cx, cy + 1 * mm, money(total), fontName=bold, fontSize=9, textAnchor="middle"))
            d.add(String(cx, cy - 4 * mm, "total", fontName=regular, fontSize=7.5, textAnchor="middle", fillColor=colors.HexColor("#5b6862")))
        legend(d, height * mm - 8 * mm)
        return d

    def chart_column(labels, values, ch_title=None):
        d = Drawing(180 * mm, 62 * mm)
        bc = VerticalBarChart()
        bc.x, bc.y, bc.width, bc.height = 22 * mm, 12 * mm, 150 * mm, 46 * mm
        bc.data = [values]
        bc.categoryAxis.categoryNames = labels
        style_axes(bc)
        bc.categoryAxis.labels.angle = 0 if len(labels) <= 6 else 30
        bc.categoryAxis.labels.dy = -2 if len(labels) <= 6 else -8
        bc.groupSpacing = 8
        bc.bars[0].fillColor = tint
        bc.bars[0].strokeColor = None
        bc.barLabelFormat = lambda v: indian(v)  # the amount above each column
        bc.barLabels.nudge = 7
        bc.barLabels.fontName = bold
        bc.barLabels.fontSize = 7
        d.add(bc)
        return d

    def chart_hbar(labels, values):
        n = len(labels)
        h = max(40, 14 + n * 9)
        d = Drawing(180 * mm, h * mm)
        bc = HorizontalBarChart()
        bc.x, bc.y, bc.width, bc.height = 38 * mm, 8 * mm, 112 * mm, (h - 12) * mm
        bc.data = [list(reversed(values))]
        bc.categoryAxis.categoryNames = [short(s, 22) for s in reversed(labels)]
        style_axes(bc)
        bc.categoryAxis.labels.boxAnchor = "e"
        bc.barWidth = 5 * mm
        bc.bars[0].fillColor = tint
        bc.bars[0].strokeColor = None
        bc.barLabelFormat = lambda v: indian(v)  # the amount at the end of each bar
        bc.barLabels.nudge = 18
        bc.barLabels.boxAnchor = "w"
        bc.barLabels.fontName = bold
        bc.barLabels.fontSize = 7
        d.add(bc)
        return d

    def chart_line(labels, values):
        d = Drawing(180 * mm, 62 * mm)
        lc = HorizontalLineChart()
        lc.x, lc.y, lc.width, lc.height = 22 * mm, 12 * mm, 150 * mm, 46 * mm
        lc.data = [values]
        lc.categoryAxis.categoryNames = labels
        style_axes(lc)
        lc.lines[0].strokeColor = tint
        lc.lines[0].strokeWidth = 2.2
        lc.lines[0].symbol = makeMarker("FilledCircle")
        lc.lines[0].symbol.fillColor = tint
        lc.lines[0].symbol.size = 6
        lc.joinedLines = 1
        d.add(lc)
        top_v = max(values) or 1
        n = len(values)
        for k, v in enumerate(values):  # the amount at each point
            px = lc.x + lc.width * (k + 0.5) / n
            py = lc.y + lc.height * v / top_v * 0.96
            d.add(String(px, py + 3 * mm, indian(v), fontName=bold, fontSize=7, textAnchor="middle"))
        return d

    def chart_lollipop(labels, values):
        d = Drawing(180 * mm, 62 * mm)
        x0, y0, w, h = 24 * mm, 12 * mm, 148 * mm, 44 * mm
        top = max(values) or 1
        d.add(Line(x0, y0, x0 + w, y0, strokeColor=colors.HexColor("#9aa5a0")))
        step = w / len(values)
        for n, v in enumerate(values):
            cx = x0 + step * (n + 0.5)
            cy = y0 + (v / top) * h
            d.add(Line(cx, y0, cx, cy, strokeColor=tint, strokeWidth=2.4))
            d.add(Circle(cx, cy, 2.6 * mm, fillColor=tint, strokeColor=colors.white, strokeWidth=1))
            d.add(String(cx, y0 - 4 * mm, labels[n], fontName=regular, fontSize=7, textAnchor="middle"))
            d.add(String(cx, cy + 3.6 * mm, compact(v), fontName=bold, fontSize=7, textAnchor="middle"))
        return d

    def chart_share_bar():
        height = 22 + len(parts) * 11
        d = Drawing(180 * mm, height * mm)
        x0, w = 8 * mm, 164 * mm
        y = (height - 14) * mm
        x = x0
        for n, p in enumerate(parts):
            seg = w * p["amount"] / total
            d.add(Rect(x, y, seg, 10 * mm, fillColor=colors.HexColor(shades[n]), strokeColor=colors.white, strokeWidth=1))
            if seg > 14 * mm:
                d.add(String(x + seg / 2, y + 3.2 * mm, f"{round(p['amount'] / total * 100)}%", fontName=bold, fontSize=8,
                             fillColor=colors.white, textAnchor="middle"))
            x += seg
        legend(d, y - 11 * mm, left=8)
        return d

    def chart_funnel():
        """Names from the biggest to the smallest, each as a centred bar that gets narrower, like a funnel."""
        n = len(parts)
        row_h = 9 * mm
        h = n * row_h + 8 * mm
        d = Drawing(180 * mm, h)
        top = parts[0]["amount"] or 1
        cx = 90 * mm
        for k, p in enumerate(parts):
            w = max(14 * mm, 150 * mm * p["amount"] / top)
            y = h - 4 * mm - (k + 1) * row_h + 1 * mm
            d.add(Rect(cx - w / 2, y, w, row_h - 2 * mm, fillColor=tint, fillOpacity=1 - 0.55 * k / max(1, n - 1) if n > 1 else 1, strokeColor=None))
            d.add(String(cx, y + 2.2 * mm, f"{short(p['label'], 20)}  {money(p['amount'])}", fontName=bold, fontSize=8, textAnchor="middle",
                         fillColor=colors.white if k < n * 0.6 or n == 1 else colors.HexColor("#1b2420")))
        return d

    def chart_area(labels, values):
        d = Drawing(180 * mm, 64 * mm)
        x0, y0, w, h = 30 * mm, 12 * mm, 142 * mm, 44 * mm
        top = niceish(max(values))
        n = len(values)
        if n == 1:  # one date: a flat area across the chart
            values, labels, n = values * 2, [labels[0], ""], 2
        pts = [(x0 + 4 * mm + (w - 8 * mm) * k / (n - 1), y0 + values[k] / top * h) for k in range(n)]
        grey = colors.HexColor("#9aa5a0")
        d.add(Polygon([pts[0][0], y0] + [c for p in pts for c in p] + [pts[-1][0], y0], fillColor=tint, fillOpacity=0.35, strokeColor=None))
        d.add(PolyLine([c for p in pts for c in p], strokeColor=tint, strokeWidth=2.2))
        d.add(Line(x0, y0, x0 + w, y0, strokeColor=grey))
        d.add(Line(x0, y0, x0, y0 + h, strokeColor=grey))
        for value_tick in nice_axis(top)[1]:  # the numbers on the left, with a clear gap before the axis line
            ty = y0 + value_tick / top * h
            d.add(String(x0 - 3 * mm, ty - 1.2 * mm, indian(value_tick), fontName=regular, fontSize=7, textAnchor="end"))
            if value_tick:
                d.add(Line(x0, ty, x0 + w, ty, strokeColor=colors.HexColor("#e1e7e4"), strokeWidth=0.5))
        every = max(1, math.ceil(n / 8))  # not more than about 8 labels, so none of them touch
        for k, (px, py) in enumerate(pts):
            d.add(Circle(px, py, 1.5 * mm, fillColor=tint, strokeColor=colors.white))
            if k % every == 0 or k == n - 1:
                d.add(String(px, y0 - 4 * mm, labels[k], fontName=regular, fontSize=7, textAnchor="middle"))
                d.add(String(px, py + 2.6 * mm, indian(values[k]), fontName=bold, fontSize=7, textAnchor="middle"))
        return d

    def chart_histogram():
        """How many payments fall in each size range: bars that touch each other."""
        vals = [value(i) for i in selected if value(i) > 0]
        lo, hi = min(vals), max(vals)
        bins = min(6, max(2, len(vals)))
        step = (hi - lo) / bins or 1
        counts = [0] * bins
        sums = [0.0] * bins
        for v in vals:
            b = min(bins - 1, int((v - lo) / step))
            counts[b] += 1
            sums[b] += v
        d = Drawing(180 * mm, 62 * mm)
        x0, y0, w, h = 24 * mm, 12 * mm, 148 * mm, 44 * mm
        top = max(counts)
        bw = w / bins
        d.add(Line(x0, y0, x0 + w, y0, strokeColor=colors.HexColor("#9aa5a0")))
        for k, c in enumerate(counts):
            bh = c / top * h
            d.add(Rect(x0 + k * bw, y0, bw, bh, fillColor=tint, strokeColor=colors.white, strokeWidth=0.8))
            if c:
                d.add(String(x0 + k * bw + bw / 2, y0 + bh + 1.5 * mm, f"{c} · {indian(sums[k])}", fontName=bold, fontSize=7, textAnchor="middle"))
            d.add(String(x0 + k * bw, y0 - 4 * mm, compact(lo + k * step), fontName=regular, fontSize=7, textAnchor="middle"))
        d.add(String(x0 + w, y0 - 4 * mm, compact(hi), fontName=regular, fontSize=7, textAnchor="middle"))
        d.add(String(x0 - 2 * mm, y0 + h / 2, "count", fontName=regular, fontSize=7, textAnchor="end"))
        return d

    def chart_scatter():
        """Every payment as a dot: the date across, the amount up."""
        pts = sorted((date.fromisoformat(i["date"]).toordinal(), value(i)) for i in selected if value(i) > 0)
        d = Drawing(180 * mm, 62 * mm)
        x0, y0, w, h = 30 * mm, 12 * mm, 142 * mm, 44 * mm
        xs = [p[0] for p in pts]
        xmin, xmax = min(xs), max(xs)
        top = niceish(max(p[1] for p in pts))
        d.add(Line(x0, y0, x0 + w, y0, strokeColor=colors.HexColor("#9aa5a0")))
        d.add(Line(x0, y0, x0, y0 + h, strokeColor=colors.HexColor("#9aa5a0")))
        for value_tick in nice_axis(top)[1]:
            ty = y0 + value_tick / top * h
            d.add(String(x0 - 3 * mm, ty - 1.2 * mm, indian(value_tick), fontName=regular, fontSize=7, textAnchor="end"))
            if value_tick:
                d.add(Line(x0, ty, x0 + w, ty, strokeColor=colors.HexColor("#e1e7e4"), strokeWidth=0.5))
        span = (xmax - xmin) or 1
        big_cut = sorted((p[1] for p in pts), reverse=True)[min(9, len(pts) - 1)]
        for ordinal, v in pts:
            px = x0 + 6 * mm + (w - 12 * mm) * (ordinal - xmin) / span if xmax > xmin else x0 + w / 2
            d.add(Circle(px, y0 + v / top * h, 2 * mm, fillColor=tint, fillOpacity=0.8, strokeColor=colors.white, strokeWidth=0.8))
            if len(pts) <= 24 or v >= big_cut:  # the amount beside each dot (with many dots, only the biggest)
                d.add(String(px, y0 + v / top * h + 3 * mm, indian(v), fontName=bold, fontSize=6.5, textAnchor="middle"))
        for ordinal in sorted({xmin, (xmin + xmax) // 2, xmax}):
            px = x0 + 6 * mm + (w - 12 * mm) * (ordinal - xmin) / span if xmax > xmin else x0 + w / 2
            d.add(String(px, y0 - 4 * mm, date.fromordinal(ordinal).strftime("%d %b %y"), fontName=regular, fontSize=7, textAnchor="middle"))
        return d

    def chart_combo(labels, values, counts):
        """Bars for the amount of each month and a line for how many payments it had."""
        d = Drawing(180 * mm, 66 * mm)
        x0, y0, w, h = 30 * mm, 12 * mm, 134 * mm, 44 * mm
        top = niceish(max(values))
        ctop = max(counts) + 1
        n = len(values)
        step = w / n
        d.add(Line(x0, y0, x0 + w, y0, strokeColor=colors.HexColor("#9aa5a0")))
        d.add(Line(x0, y0, x0, y0 + h, strokeColor=colors.HexColor("#9aa5a0")))
        for value_tick in nice_axis(top)[1]:
            d.add(String(x0 - 3 * mm, y0 + value_tick / top * h - 1.2 * mm, indian(value_tick), fontName=regular, fontSize=7, textAnchor="end"))
        pts = []
        for k, v in enumerate(values):
            bw = min(step * 0.6, 22 * mm)
            cx = x0 + step * (k + 0.5)
            d.add(Rect(cx - bw / 2, y0, bw, v / top * h, fillColor=tint, fillOpacity=0.85, strokeColor=None))
            d.add(String(cx, y0 + v / top * h + 1.5 * mm, indian(v), fontName=bold, fontSize=7, textAnchor="middle"))
            d.add(String(cx, y0 - 4 * mm, labels[k], fontName=regular, fontSize=7, textAnchor="middle"))
            pts.append((cx, y0 + counts[k] / ctop * h))
        accent = colors.HexColor("#1b2420")
        if len(pts) > 1:
            d.add(PolyLine([c for p in pts for c in p], strokeColor=accent, strokeWidth=1.8))
        for k, (px, py) in enumerate(pts):
            d.add(Circle(px, py, 1.6 * mm, fillColor=accent, strokeColor=colors.white))
            d.add(String(px + 3.5 * mm, py - 1 * mm, str(counts[k]), fontName=bold, fontSize=7.5, textAnchor="start", fillColor=accent))
        d.add(Rect(x0 + w + 4 * mm, y0 + h - 3 * mm, 3 * mm, 3 * mm, fillColor=tint, strokeColor=None))
        d.add(String(x0 + w + 8.5 * mm, y0 + h - 2.5 * mm, "amount", fontName=regular, fontSize=7))
        d.add(Circle(x0 + w + 5.5 * mm, y0 + h - 8 * mm, 1.3 * mm, fillColor=accent, strokeColor=None))
        d.add(String(x0 + w + 8.5 * mm, y0 + h - 9 * mm, "payments", fontName=regular, fontSize=7))
        return d

    def chart_spark():
        """The sparkline drawn as a full graph: axes with round numbers, grid lines, the date under each point and its amount
        above it; the lowest point is red, the highest green."""
        grey = colors.HexColor("#9aa5a0")
        d = Drawing(180 * mm, 66 * mm)
        x0, y0, w, h = 30 * mm, 12 * mm, 142 * mm, 46 * mm
        vals, labels = day_values, day_labels
        lo_v, hi_v = min(vals), max(vals)
        if hi_v == lo_v:  # all the same amount: a little room above and below it
            lo_v, hi_v = lo_v * 0.8, hi_v * 1.2 or 1
        raw = (hi_v - lo_v) / 4
        unit = 10 ** math.floor(math.log10(raw))
        step = next(m * unit for m in (1, 2, 2.5, 5, 10) if m * unit >= raw)
        base = max(0.0, math.floor((lo_v - (hi_v - lo_v) * 0.1) / step) * step)  # the axis starts just under the smallest payment
        top = math.ceil(hi_v / step) * step
        ticks = [base + step * k for k in range(int(round((top - base) / step)) + 1)]
        at = lambda v: y0 + (v - base) / (top - base) * h  # noqa: E731  the same scale for the numbers and the points
        n = len(vals)
        xs = [x0 + 6 * mm + ((w - 12 * mm) * k / (n - 1) if n > 1 else (w - 12 * mm) / 2) for k in range(n)]
        ys = [at(v) for v in vals]
        d.add(Line(x0, y0, x0 + w, y0, strokeColor=grey))
        d.add(Line(x0, y0, x0, y0 + h, strokeColor=grey))
        for value_tick in ticks:
            ty = at(value_tick)
            d.add(String(x0 - 3 * mm, ty - 1.2 * mm, indian(value_tick), fontName=regular, fontSize=7, textAnchor="end"))
            if value_tick != base:
                d.add(Line(x0, ty, x0 + w, ty, strokeColor=colors.HexColor("#e1e7e4"), strokeWidth=0.5))
        if n > 1:
            d.add(PolyLine([c for p in zip(xs, ys) for c in p], strokeColor=tint, strokeWidth=2))
        hi, lo = vals.index(max(vals)), vals.index(min(vals))
        every = max(1, math.ceil(n / 8))
        for k in range(n):
            mark = colors.HexColor("#1f9d5a") if k == hi else colors.HexColor("#d6453d") if (k == lo and n > 1 and lo != hi) else tint
            d.add(Circle(xs[k], ys[k], 1.7 * mm, fillColor=mark, strokeColor=colors.white))
            if k % every == 0 or k == n - 1 or k in (hi, lo):
                d.add(String(xs[k], ys[k] + 2.8 * mm, indian(vals[k]), fontName=bold, fontSize=7, textAnchor="middle"))
            if k % every == 0 or k == n - 1:
                d.add(String(xs[k], y0 - 4 * mm, labels[k], fontName=regular, fontSize=7, textAnchor="middle"))
        return d

    globals_parts = parts

    def chart_radar():
        """One spoke for each payment name (the biggest 7 and Others); the further from the centre, the bigger the amount."""
        grey = colors.HexColor("#c9d1cd")
        d = Drawing(180 * mm, 92 * mm)
        cx, cy, radius = 90 * mm, 46 * mm, 36 * mm
        parts = list(globals_parts)
        while len(parts) < 3:  # a radar needs at least 3 spokes: with fewer names, empty spokes are added
            parts.append({"label": "", "amount": 0.0, "count": 0})
        n = len(parts)
        top, ticks = nice_axis(max(p["amount"] for p in parts))
        point = lambda k, r: (cx + r * math.sin(2 * math.pi * k / n), cy + r * math.cos(2 * math.pi * k / n))  # noqa: E731
        for t in ticks[1:]:  # the rings, with their amount on the first spoke
            r = radius * t / top
            d.add(Polygon([c for k in range(n) for c in point(k, r)], fillColor=None, strokeColor=grey, strokeWidth=0.6))
            gx, gy = point(0.5, r)  # between the first two spokes, so it does not touch a label
            d.add(String(gx, gy, indian(t), fontName=regular, fontSize=6.5, textAnchor="middle", fillColor=colors.HexColor("#5b6862")))
        for k in range(n):
            d.add(Line(cx, cy, *point(k, radius), strokeColor=grey, strokeWidth=0.6))
        pts = [point(k, radius * p["amount"] / top) for k, p in enumerate(parts)]
        d.add(Polygon([c for p in pts for c in p], fillColor=tint, fillOpacity=0.3, strokeColor=tint, strokeWidth=2))
        for k, p in enumerate(parts):
            if p["amount"]:
                d.add(Circle(pts[k][0], pts[k][1], 1.6 * mm, fillColor=tint, strokeColor=colors.white))
            lx, ly = point(k, radius + 6 * mm)
            anchor = "middle" if abs(lx - cx) < 2 * mm else "start" if lx > cx else "end"
            d.add(String(lx, ly - 0.5 * mm, short(p["label"], 16), fontName=bold, fontSize=8, textAnchor=anchor))
            if p["amount"]:
                vx, vy = point(k, radius * p["amount"] / top + 4.5 * mm)
                d.add(String(vx, vy - 1 * mm, indian(p["amount"]), fontName=regular, fontSize=7, textAnchor="middle"))
        return d

    def chart_column3d():
        """3-D columns: a front face, a darker side and a lighter top, one for each payment name (the biggest 7 and Others)."""
        def shade(hex_color, factor):
            c = colors.HexColor(hex_color)
            return colors.Color(min(1, c.red * factor), min(1, c.green * factor), min(1, c.blue * factor))

        grey = colors.HexColor("#9aa5a0")
        d = Drawing(180 * mm, 70 * mm)
        x0, y0, w, h = 30 * mm, 14 * mm, 138 * mm, 44 * mm
        dx, dy = 3.5 * mm, 2.5 * mm  # how deep the 3-D looks
        top, ticks = nice_axis(max(p["amount"] for p in parts))
        n = len(parts)
        # the back wall and the floor
        d.add(Polygon([x0 + dx, y0 + dy, x0 + dx + w, y0 + dy, x0 + dx + w, y0 + dy + h, x0 + dx, y0 + dy + h],
                      fillColor=colors.HexColor("#f4f7f5"), strokeColor=None))
        d.add(Polygon([x0, y0, x0 + w, y0, x0 + w + dx, y0 + dy, x0 + dx, y0 + dy], fillColor=colors.HexColor("#e1e7e4"), strokeColor=grey, strokeWidth=0.5))
        for t in ticks:
            ty = y0 + t / top * h
            d.add(String(x0 - 3 * mm, ty - 1.2 * mm, indian(t), fontName=regular, fontSize=7, textAnchor="end"))
            if t:
                d.add(Line(x0 + dx, ty + dy, x0 + dx + w, ty + dy, strokeColor=colors.HexColor("#d4dcd8"), strokeWidth=0.5))
        d.add(Line(x0 + dx, y0 + dy, x0 + dx, y0 + dy + h, strokeColor=grey, strokeWidth=0.5))
        slot = w / n
        bw = min(slot * 0.55, 22 * mm)
        for k, p in enumerate(parts):
            bx = x0 + slot * k + (slot - bw) / 2
            bh = max(0.8 * mm, p["amount"] / top * h)
            d.add(Rect(bx, y0, bw, bh, fillColor=tint, strokeColor=None))
            d.add(Polygon([bx + bw, y0, bx + bw + dx, y0 + dy, bx + bw + dx, y0 + dy + bh, bx + bw, y0 + bh],
                          fillColor=shade(color, 0.7), strokeColor=None))
            d.add(Polygon([bx, y0 + bh, bx + bw, y0 + bh, bx + bw + dx, y0 + bh + dy, bx + dx, y0 + bh + dy],
                          fillColor=shade(color, 1.25), strokeColor=None))
            d.add(String(bx + bw / 2 + dx / 2, y0 + bh + dy + 1.5 * mm, indian(p["amount"]), fontName=bold, fontSize=7.5, textAnchor="middle"))
            d.add(String(bx + bw / 2, y0 - 4.5 * mm, short(p["label"], 14), fontName=regular, fontSize=7.5, textAnchor="middle"))
        return d

    # one kind of chart for each option (chosen by the user)
    #   Delayed: pie · Paid: bar chart · Cancelled: column chart · Pending: funnel · Awaiting income: line chart
    #   Awaiting income received: histogram · We paid: area chart · Excess paid: scatter plot · Part paid pending: combo chart
    KIND = {"paid": "bar", "cancelled": "column", "pending": "funnel", "delayed": "column", "awaiting": "line",
            "received": "histogram", "we_paid": "area", "excess": "column3d", "part_pending": "spark"}
    TITLES = {"bar": "bar chart by month", "column": "column chart", "funnel": "funnel by payment name",
              "pie": "pie chart by payment name", "line": "line chart by date", "histogram": "histogram of payment sizes",
              "area": "area chart by date", "scatter": "scatter plot of every payment", "radar": "radar chart by payment name", "column3d": "3-D column chart by payment name", "combo": "combo chart by month", "spark": "graph by date"}
    kind = KIND.get(option, "column")
    if total > 0 and parts:
        story.append(Paragraph(escape(f"{name}: {TITLES[kind]}"), h2))
        one_month = len(shown) < 2
        if kind == "pie":
            story.append(chart_pie(False))
        elif kind == "bar":
            story.append(chart_hbar(month_labels, month_values))
        elif kind == "column":  # by month; with one month, one column for each day
            story.append(chart_column(month_labels, month_values) if not one_month else chart_column(day_labels, day_values))
        elif kind == "spark":
            story.append(chart_spark())
        elif kind == "funnel":
            story.append(chart_funnel())
        elif kind == "line":  # the amount on each date
            story.append(chart_line(day_labels, day_values))
        elif kind == "histogram":
            story.append(chart_histogram())
        elif kind == "area":  # the amount paid on each date
            story.append(chart_area(day_labels, day_values))
        elif kind == "column3d":
            story.append(chart_column3d())
        elif kind == "radar":
            story.append(chart_radar())
        elif kind == "scatter":
            story.append(chart_scatter())
        else:
            story.append(chart_combo(month_labels, month_values, [m["count"] for m in shown]))

    # month by month table: only this option
    if rows_m:
        story.append(Paragraph(escape(f"{name}: month by month"), h2))
        if len(rows_m) > MAX_TREND_MONTHS:
            story.append(Paragraph(f"Showing the latest {MAX_TREND_MONTHS} months of {len(rows_m)}.", small))
        rows = [["Month", amount_label, "Payments"]]
        for m in reversed(shown):
            rows.append([f"{MONTH_NAMES[int(m['key'][5:7]) - 1]} {m['key'][:4]}", money(m["amount"]), m["count"]])
        rows.append(["All months", money(sum(m["amount"] for m in rows_m)), sum(m["count"] for m in rows_m)])
        t = table(rows, [60 * mm, 80 * mm, 40 * mm])
        t.setStyle(TableStyle([("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("FONTNAME", (0, len(rows) - 1), (-1, len(rows) - 1), bold),
                               ("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), head_bg)]))
        story.append(t)

    # the payments of this option
    story.append(Paragraph(f"Payments ({len(selected)})", h2))
    if selected:
        head = ["Date", "Name", name, "Details"] if field else ["Date", "Name", "Amount", "Status", "Details"]
        rows, tints = [head], []
        for i in selected:
            notes = []
            for _key, extra_field, extra_name, _color in EXTRAS:
                if i.get(extra_field) and extra_field != field:
                    notes.append(f"{extra_name} {money(i[extra_field])}")
            if i.get("description"):
                notes.append(i["description"])
            if is_delayed(i) and i.get("new_date"):
                notes.insert(0, f"Delayed to {date.fromisoformat(i['new_date']).strftime('%d %b %Y')}")
            day = date.fromisoformat(i["date"]).strftime("%d %b %Y")
            if field:  # an optional amount: one column named after the option, with that amount
                rows.append([day, para(i["name"]), para(money(value(i)), right), para(" · ".join(notes))])
            else:
                rows.append([day, para(i["name"]), para(money(i["amount"]), right), para(name), para(" · ".join(notes))])
            tints.append(ROW_TINTS.get(option) if not field else None)
        story.append(table(rows, [24 * mm, 50 * mm, 36 * mm, 70 * mm] if field else [22 * mm, 40 * mm, 25 * mm, 30 * mm, 63 * mm], tints=tints))
    else:
        story.append(Paragraph("No payments for these filters.", text))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(colors.HexColor("#5b6862"))
        canvas.drawString(15 * mm, 9 * mm, f"{name} report \u00b7 {scope}")
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                      title=f"{name} report", author="Payment Tracker").build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def re_space(text: str) -> str:
    return " ".join((text or "").split())