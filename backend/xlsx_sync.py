"""
Keeps a stored calendar-style Excel file up to date.

When you add a payment by hand, or change its status, the payment is written into the stored
Excel file for its month (under the right day), and its two cells get the status colour:
paid = green, delayed = yellow, cancelled = red. Pending has no colour.

The file's own layout is kept: only the needed cells are written. A new day gets its own
column pair, a new week gets a new block at the bottom, and when a day has no free row a row is
inserted above the SUM row and the SUM formulas are extended.
"""
import io
import re
from copy import copy
from datetime import date, datetime
from typing import List, Optional, Tuple

import openpyxl
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter

from utils import WEEKDAYS, _day_number, _is_formula, _month_year, duplicate_key, parse_amount, parse_date

STATUS_COLORS = {"paid": "FF92D050", "delayed": "FFFFFF00", "cancelled": "FFFF7C80"}
WEEKDAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


# ---------- reading the layout ----------

def _filled(row) -> List[Tuple[int, object]]:
    return [(c, v) for c, v in enumerate(row) if v is not None and str(v).strip() != ""]


def _is_boundary(row) -> bool:
    cells = [v for _, v in _filled(row)]
    if any(_is_formula(v) for v in cells):
        return True  # SUM row
    if any(isinstance(v, str) and v.strip().lower().rstrip(".:") in WEEKDAYS for v in cells):
        return True  # weekday header row
    return any(isinstance(v, str) and re.match(r"^(grand\s+)?total\b", v.strip(), re.I) for v in cells)


def _sheet_month(ws) -> Optional[Tuple[int, int]]:
    for row in ws.iter_rows(values_only=True):
        for cell in row:
            if isinstance(cell, str):
                found = _month_year(cell)
                if found:
                    return found
    return None


def _find_blocks(grid):
    """A block is one week: a row of day numbers and the item rows under it, up to the SUM row."""
    day_rows = []
    for r, row in enumerate(grid):
        filled = _filled(row)
        if not filled or any(_is_formula(v) for _, v in filled):
            continue
        days = [_day_number(v) for _, v in filled]
        if all(d is not None for d in days) and days == sorted(set(days)):
            day_rows.append((r, [(c, d) for (c, _), d in zip(filled, days)]))
    day_row_numbers = {r for r, _ in day_rows}
    blocks = []
    for r, days in day_rows:
        end = len(grid)
        for q in range(r + 1, len(grid)):
            if q in day_row_numbers or _is_boundary(grid[q]):
                end = q
                break
        blocks.append({"day_row": r, "days": {d: c for c, d in days}, "end": end})
    return blocks


def _first_weekday(year: int, month: int) -> int:
    return (date(year, month, 1).weekday() + 1) % 7  # Sunday = 0


def _weekday(year: int, month: int, day: int) -> int:
    return (_first_weekday(year, month) + day - 1) % 7


def _week_index(year: int, month: int, day: int) -> int:
    return (day - 1 + _first_weekday(year, month)) // 7


def _as_amount(value) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_amount(str(value))


def _clean_number(amount: float):
    return int(amount) if float(amount).is_integer() else amount


# ---------- colours ----------

def _colour(ws, excel_row: int, excel_col: int, status: str) -> bool:
    """Colours the item cell and the amount cell. Returns True when something changed."""
    wanted = STATUS_COLORS.get(status)
    changed = False
    for column in (excel_col, excel_col + 1):
        cell = ws.cell(excel_row, column)
        current = cell.fill.fgColor.rgb if cell.fill and cell.fill.fill_type == "solid" else None
        if wanted:
            if current != wanted:
                cell.fill = PatternFill("solid", fgColor=wanted)
                changed = True
        elif current is not None:
            cell.fill = PatternFill(fill_type=None)
            changed = True
    return changed


def _copy_style(source, target) -> None:
    if source.has_style:
        target._style = copy(source._style)


# ---------- inserting a row ----------

_REF = re.compile(r"(?<![A-Za-z0-9_])(\$?[A-Z]{1,3}\$?)(\d+)(?![\d(])")


def _insert_row(ws, insert_at: int) -> None:
    """Inserts an empty row at insert_at and fixes merged cells and formulas below it."""
    merged_below = []
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row >= insert_at:
            merged_below.append((rng.min_col, rng.min_row, rng.max_col, rng.max_row))
            ws.unmerge_cells(str(rng))
    ws.insert_rows(insert_at)
    for min_col, min_row, max_col, max_row in merged_below:
        ws.merge_cells(start_row=min_row + 1, start_column=min_col, end_row=max_row + 1, end_column=max_col)
    for row in ws.iter_rows():
        for cell in row:
            if _is_formula(cell.value):
                cell.value = _REF.sub(
                    lambda m: m.group(1) + str(int(m.group(2)) + (1 if int(m.group(2)) >= insert_at else 0)),
                    cell.value,
                )
    # The SUM row that sat at insert_at is now one row lower: make its ranges include the new row.
    for cell in ws[insert_at + 1]:
        if _is_formula(cell.value):
            cell.value = re.sub(
                r"(SUM\(\$?[A-Z]+\$?\d+:\$?[A-Z]+\$?)(\d+)(\))",
                lambda m: m.group(1) + (str(insert_at) if int(m.group(2)) == insert_at - 1 else m.group(2)) + m.group(3),
                cell.value,
            )


# ---------- placing a payment ----------

def _write_item(ws, grid, block, col: int, name: str, amount: float, status: str) -> None:
    """Writes name and amount in the first free row under the day, inserting a row when there is none."""
    target = None
    for r in range(block["day_row"] + 1, block["end"]):
        row = grid[r]
        name_cell = row[col] if col < len(row) else None
        amount_cell = row[col + 1] if col + 1 < len(row) else None
        if (name_cell is None or str(name_cell).strip() == "") and (amount_cell in (None, "", 0)):
            target = r + 1  # Excel row number
            break
    if target is None:
        target = block["end"] + 1
        if block["end"] < len(grid):
            _insert_row(ws, target)
    ws.cell(target, col + 1, name)
    ws.cell(target, col + 2, _clean_number(amount))
    _colour(ws, target, col + 1, status)


def _add_day_column(ws, grid, block, year: int, month: int, day: int) -> int:
    """Gives a new day (for example Wednesday) its own column pair in an existing week block."""
    any_day, any_col = next(iter(block["days"].items()))
    base = any_col - 2 * _weekday(year, month, any_day)
    col = base + 2 * _weekday(year, month, day)
    used = {c for c in block["days"].values()} | {c + 1 for c in block["days"].values()}
    if col < 0 or col in used or col + 1 in used:
        raise ValueError("This sheet's layout has no free column for that day")
    ref_day, ref_col = min(block["days"].items(), key=lambda item: abs(item[1] - col))
    day_row = block["day_row"] + 1  # Excel row numbers from here on
    header_row = day_row - 1
    if header_row >= 1 and any(
        isinstance(v, str) and v.strip().lower() in WEEKDAYS for _, v in _filled(grid[header_row - 1])
    ):
        ws.cell(header_row, col + 1, WEEKDAY_NAMES[_weekday(year, month, day)])
        _copy_style(ws.cell(header_row, ref_col + 1), ws.cell(header_row, col + 1))
    ws.cell(day_row, col + 1, day)
    _copy_style(ws.cell(day_row, ref_col + 1), ws.cell(day_row, col + 1))
    end = block["end"]
    if end < len(grid) and any(_is_formula(v) for _, v in _filled(grid[end])):
        letter = get_column_letter(col + 2)
        ws.cell(end + 1, col + 2, f"=SUM({letter}{day_row + 1}:{letter}{end})")
        _copy_style(ws.cell(end + 1, ref_col + 2), ws.cell(end + 1, col + 2))
    for offset in (1, 2):
        width = ws.column_dimensions[get_column_letter(ref_col + offset)].width
        if width:
            ws.column_dimensions[get_column_letter(col + offset)].width = width
    block["days"][day] = col
    return col


def _append_block(ws, blocks, grid, year: int, month: int, day: int, name: str, amount: float, status: str) -> None:
    """A week that is not in the sheet yet: add a new block at the bottom."""
    base, ref = 0, None
    if blocks:
        any_day, any_col = next(iter(blocks[0]["days"].items()))
        base = any_col - 2 * _weekday(year, month, any_day)
        ref = (blocks[0]["day_row"] + 1, any_col + 1, blocks[0]["end"] + 1)  # day row, column, sum row
    col = base + 2 * _weekday(year, month, day)
    if col < 0:
        raise ValueError("This sheet's layout has no free column for that day")
    top = ws.max_row + 2  # one empty row between weeks
    ws.cell(top, col + 1, WEEKDAY_NAMES[_weekday(year, month, day)])
    ws.cell(top + 1, col + 1, day)
    ws.cell(top + 2, col + 1, name)
    ws.cell(top + 2, col + 2, _clean_number(amount))
    letter = get_column_letter(col + 2)
    ws.cell(top + 3, col + 2, f"=SUM({letter}{top + 2}:{letter}{top + 2})")
    if ref:
        day_row, ref_col, sum_row = ref
        _copy_style(ws.cell(day_row - 1, ref_col), ws.cell(top, col + 1))
        _copy_style(ws.cell(day_row, ref_col), ws.cell(top + 1, col + 1))
        _copy_style(ws.cell(sum_row, ref_col + 1), ws.cell(top + 3, col + 2))
    _colour(ws, top + 2, col + 1, status)


def _place(ws, year: int, month: int, day: int, name: str, amount: float, status: str, add_if_missing: bool) -> bool:
    iso = date(year, month, day).isoformat()
    grid = [list(row) for row in ws.iter_rows(values_only=True)]
    blocks = _find_blocks(grid)
    key = duplicate_key(name, amount, iso)

    # 1. The payment is already in the sheet: only the colour may change.
    for block in blocks:
        col = block["days"].get(day)
        if col is None:
            continue
        for r in range(block["day_row"] + 1, block["end"]):
            item = grid[r][col] if col < len(grid[r]) else None
            value = _as_amount(grid[r][col + 1] if col + 1 < len(grid[r]) else None)
            if isinstance(item, str) and value is not None and duplicate_key(item, value, iso) == key:
                return _colour(ws, r + 1, col + 1, status)
    if not add_if_missing:
        return False

    # 2. Add it under its day, in the week block that holds that day or that week.
    block = next((b for b in blocks if day in b["days"]), None)
    if block is not None:
        col = block["days"][day]
    else:
        week = _week_index(year, month, day)
        block = next(
            (b for b in blocks if any(_week_index(year, month, d) == week for d in b["days"])), None
        )
        if block is None:
            _append_block(ws, blocks, grid, year, month, day, name, amount, status)
            return True
        col = _add_day_column(ws, grid, block, year, month, day)
    _write_item(ws, grid, block, col, name, amount, status)
    return True


# ---------- flat tables (one payment per row: name, amount, date) ----------

def _cell_date(value) -> Optional[str]:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return parse_date(value)
    return None


def _flat_rows(ws):
    """Rows that look like a payment: (excel_row, name_col, amount_col, date_col, iso, name, amount) with 1-based columns."""
    found = []
    for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
        date_col = iso = None
        for c, v in enumerate(row):
            iso = _cell_date(v)
            if iso:
                date_col = c
                break
        if date_col is None:
            continue
        amount_col = amount = name_col = name = None
        for c, v in enumerate(row):
            if c == date_col or v is None or isinstance(v, (bool, datetime, date)):
                continue
            number = _as_amount(v)
            if number is not None and amount_col is None:
                amount_col, amount = c, number
            elif isinstance(v, str) and v.strip() and name_col is None and number is None:
                name_col, name = c, v.strip()
        if amount_col is not None and name_col is not None:
            found.append((r, name_col + 1, amount_col + 1, date_col + 1, iso, name, amount))
    return found


def _flat_place(ws, payment: dict, add_if_missing: bool) -> bool:
    rows = _flat_rows(ws)
    iso, status = payment["date"], payment["status"]
    key = duplicate_key(payment["name"], float(payment["amount"]), iso)
    for r, nc, ac, dc, row_iso, name, amount in rows:
        if duplicate_key(name, amount, row_iso) == key:
            changed = False
            for col in (nc, ac, dc):
                changed |= _colour_one(ws, r, col, status)
            return changed
    if not add_if_missing:
        return False
    last, nc, ac, dc = rows[-1][0], rows[-1][1], rows[-1][2], rows[-1][3]
    target = last + 1
    for col in (nc, ac, dc):
        _copy_style(ws.cell(last, col), ws.cell(target, col))
    ws.cell(target, nc, payment["name"])
    ws.cell(target, ac, _clean_number(float(payment["amount"])))
    sample = ws.cell(last, dc).value
    day = date.fromisoformat(iso)
    ws.cell(target, dc, datetime(day.year, day.month, day.day) if isinstance(sample, (datetime, date)) else iso)
    for col in (nc, ac, dc):
        _colour_one(ws, target, col, status)
    return True


def _colour_one(ws, excel_row: int, excel_col: int, status: str) -> bool:
    wanted = STATUS_COLORS.get(status)
    cell = ws.cell(excel_row, excel_col)
    current = cell.fill.fgColor.rgb if cell.fill and cell.fill.fill_type == "solid" else None
    if wanted and current != wanted:
        cell.fill = PatternFill("solid", fgColor=wanted)
        return True
    if not wanted and current is not None:
        cell.fill = PatternFill(fill_type=None)
        return True
    return False


def apply_payment(data: bytes, payment: dict, add_if_missing: bool = True) -> Tuple[Optional[bytes], str]:
    """
    Updates one stored .xlsx for a payment {name, amount, date, status}.
    Returns (new_file_bytes, "updated"), (None, "unchanged") or (None, "no-month") when the
    workbook has no sheet for the payment's month.
    """
    year, month, day = (int(part) for part in payment["date"].split("-"))
    workbook = openpyxl.load_workbook(io.BytesIO(data))
    sheet = next((ws for ws in workbook.worksheets if _sheet_month(ws) == (year, month)), None)
    if sheet is None:
        flat_sheet = next((ws for ws in workbook.worksheets if _flat_rows(ws)), None)
        if flat_sheet is None:
            return None, "no-month"
        changed = _flat_place(flat_sheet, payment, add_if_missing)
    else:
        changed = _place(
            sheet, year, month, day, payment["name"], float(payment["amount"]), payment["status"], add_if_missing
        )
    if not changed:
        return None, "unchanged"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), "updated"