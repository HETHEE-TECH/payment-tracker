"""
Builds a calendar-layout Excel file (used when a month has no stored Excel file yet):

    November 2026                       <- title (one sheet per month)
    Sunday      Monday      Tuesday     <- weekday names (three columns per day)
    1           2           3           <- day numbers
    Apple 2400 note  Apple 11820 ...    <- item, amount and description under each day
    =SUM(...)                           <- total of each day, one block per week

Every payment of the month is included. Paid is green, delayed is yellow and cancelled is red.
The file can be imported back into the app.
"""
import io
from calendar import monthrange
from collections import defaultdict
from datetime import date

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from utils import HEADER_COLUMNS

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]
WEEKDAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
STATUS_FILL = {"paid": "92D050", "delayed": "FFFF00", "cancelled": "FF7C80"}


def _fill_sheet(ws, year: int, month: int, by_day) -> None:
    title = ws.cell(1, 1, f"{MONTH_NAMES[month - 1]} {year}")
    title.font = Font(bold=True, size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=6)  # the title covers two days
    for offset, label in enumerate(HEADER_COLUMNS):  # Reminder, Apple, Orange, Mango right next to it
        head = ws.cell(1, 7 + offset, label)
        head.font = Font(bold=True, size=14)

    first_weekday = (date(year, month, 1).weekday() + 1) % 7  # Sunday = 0
    days_in_month = monthrange(year, month)[1]

    # Split the month into Sunday-to-Saturday weeks: [(day, weekday_index), ...]
    weeks, current = [], []
    for day in range(1, days_in_month + 1):
        weekday = (first_weekday + day - 1) % 7
        if weekday == 0 and current:
            weeks.append(current)
            current = []
        current.append((day, weekday))
    if current:
        weeks.append(current)

    bold = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="E7E6E6")
    row = 2
    for week in weeks:
        most = max(1, max(len(by_day.get(day, [])) for day, _ in week))
        sum_row = row + 2 + most
        for day, weekday in week:
            col = 1 + 3 * weekday
            head = ws.cell(row, col, WEEKDAY_NAMES[weekday])
            head.font, head.fill = bold, header_fill
            head.alignment = Alignment(horizontal="center")
            ws.cell(row, col + 1).fill = header_fill
            ws.cell(row, col + 2).fill = header_fill
            ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + 2)
            number = ws.cell(row + 1, col, day)
            number.font = bold
            number.alignment = Alignment(horizontal="left")
            for index, payment in enumerate(by_day.get(day, [])):
                name_cell = ws.cell(row + 2 + index, col, payment["name"])
                amount_cell = ws.cell(row + 2 + index, col + 1, payment["amount"])
                if payment.get("description"):
                    note = ws.cell(row + 2 + index, col + 2, payment["description"])  # third column of the day
                    note.alignment = Alignment(wrap_text=True, vertical="top")
                    if str(payment["description"]).startswith("="):
                        note.data_type = "s"  # text, never a formula
                color = STATUS_FILL.get(payment.get("status"))
                if color:
                    fill = PatternFill("solid", fgColor=color)
                    name_cell.fill = fill
                    amount_cell.fill = fill
                    ws.cell(row + 2 + index, col + 2).fill = fill  # the description cell too
            letter = get_column_letter(col + 1)
            total = ws.cell(sum_row, col + 1, f"=SUM({letter}{row + 2}:{letter}{row + 1 + most})")
            total.font = bold
        row = sum_row + 2  # one empty row between weeks

    for weekday in range(7):
        ws.column_dimensions[get_column_letter(1 + 3 * weekday)].width = 18
        ws.column_dimensions[get_column_letter(2 + 3 * weekday)].width = 12
        ws.column_dimensions[get_column_letter(3 + 3 * weekday)].width = 28


def build_calendar_workbook(payments) -> bytes:
    """payments: dicts with name, amount, date (YYYY-MM-DD) and status. One sheet per month."""
    by_month = defaultdict(lambda: defaultdict(list))
    for payment in payments:
        year, month, day = (int(part) for part in payment["date"].split("-"))
        by_month[(year, month)][day].append(payment)
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for year, month in sorted(by_month):
        sheet = workbook.create_sheet(f"{MONTH_NAMES[month - 1]} {year}")
        _fill_sheet(sheet, year, month, by_month[(year, month)])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()