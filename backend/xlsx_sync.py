"""
Keeps a stored calendar-style Excel file up to date.

When you add a payment by hand, or change its status, the payment is written into the stored
Excel file for its month (under the right day), and its two cells get the status colour:
paid = green, delayed = yellow, cancelled = red. Pending has no colour.

The file's own layout is kept: only the needed cells are written. Every day has three columns:
name, amount and description. A sheet that still has two columns per day gets the third column
added to every day the first time the app updates it. A new day gets its own three columns, a new
week gets a new block at the bottom, and when a day has no free row a row is inserted above the
SUM row and the SUM formulas are extended.
"""
import io
import re
from copy import copy
from datetime import date, datetime
from typing import List, Optional, Tuple

import openpyxl
from openpyxl.comments import Comment
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment, PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter

from utils import (
    HEADER_COLUMNS, WEEKDAYS, _day_number, _is_formula, _month_year, calendar_stride, duplicate_key,
    parse_amount, parse_date,
)

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


def _find_blocks(grid, desc_offset: Optional[int] = 2):
    """A block is one week: a row of day numbers and the item rows under it, up to the SUM row.
    desc_offset: the description column sits this many columns right of the day column (its free text
    must not be mistaken for a total row); None for a sheet that has no description column yet."""
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
        hidden = {c + desc_offset for c, _ in days} if desc_offset else set()
        for q in range(r + 1, len(grid)):
            shown = [None if i in hidden else v for i, v in enumerate(grid[q])] if hidden else grid[q]
            if q in day_row_numbers or _is_boundary(shown):
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
    """Colours the item, amount and description cells of one payment. Returns True when something changed."""
    wanted = STATUS_COLORS.get(status)
    changed = False
    for column in (excel_col, excel_col + 1, excel_col + 2):
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

# ---------- header columns next to the month title ----------

def _title_cell(ws):
    """The cell holding the month title (for example 'November 2026'), looked for in the first rows."""
    for row in ws.iter_rows(max_row=3):
        for cell in row:
            if isinstance(cell.value, str) and _month_year(cell.value):
                return cell
    return None


def _ensure_header_columns(ws) -> bool:
    """Reminder, Apple, Orange, Mango as columns right after the month title, in the title's style.
    Headers that are already there are left alone; the missing ones go in the next free cells."""
    title = _title_cell(ws)
    if title is None:
        return False
    row, start = title.row, title.column + 1
    for merged in ws.merged_cells.ranges:
        if merged.min_row <= row <= merged.max_row and merged.min_col <= title.column <= merged.max_col:
            start = merged.max_col + 1  # the title is merged over several columns: begin after it
    present = {
        str(ws.cell(row, c).value).strip().lower()
        for c in range(start, ws.max_column + 1)
        if not isinstance(ws.cell(row, c), MergedCell) and ws.cell(row, c).value not in (None, "")
    }
    changed, column = False, start
    for label in HEADER_COLUMNS:
        if label.lower() in present:
            continue
        while isinstance(ws.cell(row, column), MergedCell) or ws.cell(row, column).value not in (None, ""):
            column += 1  # skip cells that are already used
        cell = ws.cell(row, column, label)
        _copy_style(title, cell)
        column += 1
        changed = True
    return changed


def prepare_xlsx(data: bytes) -> Optional[bytes]:
    """For a file that is uploaded: every month sheet gets the description column for each day and the
    header columns. Returns the new file, or None when nothing had to change (or it cannot be read)."""
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data))
        changed = False
        for ws in workbook.worksheets:
            month = _sheet_month(ws)
            if month is None:
                continue
            changed |= _widen_to_three(ws, *month)
            changed |= _ensure_header_columns(ws)
        if not changed:
            return None
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()
    except Exception:
        return None  # an unreadable or unusual file is stored exactly as it was uploaded


# ---------- making room for the description column ----------

_COLREF = re.compile(r"(?<![A-Za-z0-9_])(\$?)([A-Z]{1,3})(\$?)(\d+)(?![\d(])")


def _new_col(c: int) -> int:
    """1-based column after adding a third column to every day: the pairs 1-2, 3-4, 5-6 become 1-2, 4-5, 7-8."""
    return c + (c - 1) // 2


def _widen_to_three(ws, year: int, month: int) -> bool:
    """A sheet with two columns per day gets a third (description) column for every day.
    Cells keep their styles, merged headers grow to three columns, SUM formulas follow the move."""
    grid = [list(row) for row in ws.iter_rows(values_only=True)]
    blocks = _find_blocks(grid, None)
    if calendar_stride([b["days"] for b in blocks], year, month) != 2:
        return False
    max_row, max_col = ws.max_row, ws.max_column
    widths = {}
    for dim in list(ws.column_dimensions.values()):
        if dim.width and dim.min and dim.max:
            for c in range(dim.min, dim.max + 1):
                widths[c] = dim.width
    merges = [(m.min_row, m.min_col, m.max_row, m.max_col) for m in ws.merged_cells.ranges]
    for m in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(m))
    for c in range(max_col, 1, -1):  # right to left, so nothing is overwritten
        if _new_col(c) != c:
            letter = get_column_letter(c)
            ws.move_range(f"{letter}1:{letter}{max_row}", rows=0, cols=_new_col(c) - c)
    for row in ws.iter_rows():
        for cell in row:
            if _is_formula(cell.value):
                cell.value = _COLREF.sub(
                    lambda m: m.group(1) + get_column_letter(_new_col(column_index_from_string(m.group(2))))
                    + m.group(3) + m.group(4),
                    cell.value,
                )
    for min_row, min_col, max_r, max_c in merges:
        end = _new_col(max_c) + (1 if max_c % 2 == 0 else 0)  # a merge that ended on an amount column takes the new column too
        ws.merge_cells(start_row=min_row, start_column=_new_col(min_col), end_row=max_r, end_column=end)
    for key in list(ws.column_dimensions.keys()):
        del ws.column_dimensions[key]
    for c, width in widths.items():
        ws.column_dimensions[get_column_letter(_new_col(c))].width = width
    for c in range(1, _new_col(max_col) + 2):
        if c % 3 == 0:
            ws.column_dimensions[get_column_letter(c)].width = 28  # description columns
    moved = [list(row) for row in ws.iter_rows(values_only=True)]
    for block in _find_blocks(moved):  # a coloured payment gets its new description cell coloured too
        for col in block["days"].values():
            for q in range(block["day_row"] + 1, block["end"]):
                name_cell = ws.cell(q + 1, col + 1)
                if name_cell.fill and name_cell.fill.fill_type == "solid":
                    ws.cell(q + 1, col + 3).fill = copy(name_cell.fill)
    return True


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
    base = any_col - 3 * _weekday(year, month, any_day)
    col = base + 3 * _weekday(year, month, day)
    used = {c + k for c in block["days"].values() for k in (0, 1, 2)}
    if col < 0 or col in used or col + 1 in used or col + 2 in used:
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
    for offset in (1, 2, 3):
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
        base = any_col - 3 * _weekday(year, month, any_day)
        ref = (blocks[0]["day_row"] + 1, any_col + 1, blocks[0]["end"] + 1)  # day row, column, sum row
    col = base + 3 * _weekday(year, month, day)
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


# ---------- description (the comment the user writes for a payment) ----------

DESCRIPTION_HEADERS = ("description", "comment", "comments", "remarks", "remark", "note", "notes")


def _copy_note(note):
    if note is None:
        return None
    copied = Comment(note.text, note.author or "Payment Tracker")
    copied.width, copied.height = note.width, note.height
    return copied


def _set_note(cell, text: str) -> bool:
    """Puts the description on a cell as a note (the red corner you hover in Excel)."""
    current = (cell.comment.text if cell.comment else "") or ""
    if current.strip() == text:
        return False
    note = Comment(text, "Payment Tracker")
    note.width, note.height = 260, 110
    cell.comment = note
    return True


def _describe_calendar(ws, year: int, month: int, day: int, payment: dict) -> bool:
    text = (payment.get("description") or "").strip()
    clear = bool(payment.get("clear_description")) and not text  # only set when the user emptied the description
    if not text and not clear:
        return False
    iso = date(year, month, day).isoformat()
    key = duplicate_key(payment["name"], float(payment["amount"]), iso)
    grid = [list(row) for row in ws.iter_rows(values_only=True)]
    for block in _find_blocks(grid):
        col = block["days"].get(day)
        if col is None:
            continue
        for r in range(block["day_row"] + 1, block["end"]):
            item = grid[r][col] if col < len(grid[r]) else None
            value = _as_amount(grid[r][col + 1] if col + 1 < len(grid[r]) else None)
            if isinstance(item, str) and value is not None and duplicate_key(item, value, iso) == key:
                cell = ws.cell(r + 1, col + 3)  # the day's third column
                if clear:
                    had = cell.value not in (None, "")
                    cell.value = None
                    return had
                if (cell.value or "") == text:
                    return False
                cell.value = text
                if text.startswith("="):
                    cell.data_type = "s"  # text, never a formula
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                cell.fill = copy(ws.cell(r + 1, col + 1).fill)  # same colour as the payment
                return True
    return False


def _describe_flat(ws, payment: dict) -> bool:
    """A table sheet: the description goes in a Description column (made when the sheet has none)."""
    text = (payment.get("description") or "").strip()
    clear = bool(payment.get("clear_description")) and not text
    rows = _flat_rows(ws)
    if (not text and not clear) or not rows:
        return False
    key = duplicate_key(payment["name"], float(payment["amount"]), payment["date"])
    target = next((r for r, _nc, _ac, _dc, iso, name, amount in rows if duplicate_key(name, amount, iso) == key), None)
    if target is None:
        return False
    header_row = rows[0][0] - 1
    column = None
    for row in ws.iter_rows(min_row=1, max_row=max(header_row, 1)):
        for cell in row:
            if isinstance(cell.value, str) and cell.value.strip().lower() in DESCRIPTION_HEADERS:
                column = cell.column
                break
        if column:
            break
    if column is None and clear:
        return False  # no description column, nothing to clear
    if column is None:
        column = ws.max_column + 1
        if header_row >= 1:
            header = ws.cell(header_row, column, "Description")
            _copy_style(ws.cell(header_row, column - 1), header)
        ws.column_dimensions[get_column_letter(column)].width = 32
    cell = ws.cell(target, column)
    if (cell.value or "") == text:
        return False
    cell.value = text or None
    return True


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
        changed |= _describe_flat(flat_sheet, payment)
    else:
        changed = _widen_to_three(sheet, year, month)
        changed |= _ensure_header_columns(sheet)
        changed |= _place(
            sheet, year, month, day, payment["name"], float(payment["amount"]), payment["status"], add_if_missing
        )
        changed |= _describe_calendar(sheet, year, month, day, payment)
    if not changed:
        return None, "unchanged"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), "updated"


# ---------- removing a payment ----------

def _delete_row(ws, delete_at: int) -> None:
    """The reverse of _insert_row: removes an empty row and fixes merged cells and formulas below it."""
    merged_below = []
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row > delete_at:
            merged_below.append((rng.min_col, rng.min_row, rng.max_col, rng.max_row))
            ws.unmerge_cells(str(rng))
    ws.delete_rows(delete_at)
    for min_col, min_row, max_col, max_row in merged_below:
        ws.merge_cells(start_row=min_row - 1, start_column=min_col, end_row=max_row - 1, end_column=max_col)
    for row in ws.iter_rows():
        for cell in row:
            if _is_formula(cell.value):
                # A SUM that ended on the deleted row now ends one row higher.
                value = re.sub(
                    r"(SUM\(\$?[A-Z]+\$?\d+:\$?[A-Z]+\$?)(\d+)(\))",
                    lambda m: m.group(1) + (str(delete_at - 1) if int(m.group(2)) == delete_at else m.group(2)) + m.group(3),
                    cell.value,
                )
                cell.value = _REF.sub(
                    lambda m: m.group(1) + str(int(m.group(2)) - (1 if int(m.group(2)) > delete_at else 0)),
                    value,
                )


def _remove_calendar(ws, year: int, month: int, day: int, name: str, amount: float) -> bool:
    """Clears the payment's two cells and moves the items under it up, so no gap is left."""
    iso = date(year, month, day).isoformat()
    grid = [list(row) for row in ws.iter_rows(values_only=True)]
    key = duplicate_key(name, amount, iso)
    for block in _find_blocks(grid):
        col = block["days"].get(day)
        if col is None:
            continue
        for r in range(block["day_row"] + 1, block["end"]):
            item = grid[r][col] if col < len(grid[r]) else None
            value = _as_amount(grid[r][col + 1] if col + 1 < len(grid[r]) else None)
            if not (isinstance(item, str) and value is not None and duplicate_key(item, value, iso) == key):
                continue
            last = block["end"] - 1  # last item row of this block (0-based)
            for q in range(r, last):  # pull every item below one row up (value and colour)
                for column in (col + 1, col + 2, col + 3):  # name, amount, description
                    source, target = ws.cell(q + 2, column), ws.cell(q + 1, column)
                    target.value = source.value
                    target.fill = copy(source.fill)
                    target.alignment = copy(source.alignment)
                    target.comment = _copy_note(source.comment)
            for column in (col + 1, col + 2, col + 3):  # the freed last row
                cell = ws.cell(last + 1, column)
                cell.value = None
                cell.fill = PatternFill(fill_type=None)
                cell.alignment = Alignment()
                cell.comment = None
            # When the whole last row of the week is now empty, take the row out too (it is the row
            # that adding this payment may have inserted), but always keep at least one item row.
            empty = all(
                c.value is None and not (c.fill and c.fill.fill_type == "solid")
                for c in ws[last + 1]
            )
            if empty and last - 1 > block["day_row"]:
                _delete_row(ws, last + 1)
            return True
    return False


def _remove_flat(ws, payment: dict) -> bool:
    key = duplicate_key(payment["name"], float(payment["amount"]), payment["date"])
    for r, _nc, _ac, _dc, row_iso, name, amount in _flat_rows(ws):
        if duplicate_key(name, amount, row_iso) == key:
            ws.delete_rows(r)
            return True
    return False


def remove_payment(data: bytes, payment: dict) -> Tuple[Optional[bytes], str]:
    """
    Takes one payment {name, amount, date} out of a stored .xlsx: the reverse of apply_payment.
    Returns (new_file_bytes, "updated"), (None, "unchanged") when it is not in the file, or
    (None, "no-month") when the workbook has no sheet for the payment's month.
    """
    year, month, day = (int(part) for part in payment["date"].split("-"))
    workbook = openpyxl.load_workbook(io.BytesIO(data))
    sheet = next((ws for ws in workbook.worksheets if _sheet_month(ws) == (year, month)), None)
    if sheet is None:
        flat_sheet = next((ws for ws in workbook.worksheets if _flat_rows(ws)), None)
        if flat_sheet is None:
            return None, "no-month"
        changed = _remove_flat(flat_sheet, payment)
    else:
        widened = _widen_to_three(sheet, year, month)
        changed = _remove_calendar(sheet, year, month, day, payment["name"], float(payment["amount"])) or widened
    if not changed:
        return None, "unchanged"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), "updated"