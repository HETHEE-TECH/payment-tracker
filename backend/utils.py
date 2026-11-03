import csv
import io
import re
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

Payment = Tuple[str, float, str]  # (name, amount, YYYY-MM-DD)

DATE_RE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}")
NUMBER_RE = re.compile(r"(?:₹|Rs\.?|INR)?\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
AMOUNT_CELL_RE = re.compile(r"^(?:₹|Rs\.?|INR)?\s*\d[\d,]*(?:\.\d+)?$", re.IGNORECASE)


def parse_date(text: str) -> Optional[str]:
    """Accepts 2026-11-05, 05-11-2026, 05/11/2026, 05.11.2026. Returns YYYY-MM-DD or None."""
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_amount(text: str) -> Optional[float]:
    """'₹1,500' -> 1500.0. Returns None if it is not a positive number."""
    cleaned = re.sub(r"[^0-9.\-]", "", text or "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


def check_basic_auth(header: Optional[str], username: str, password: str) -> bool:
    """True when the Authorization header is 'Basic <base64 user:password>' and matches."""
    import base64
    import hmac

    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except Exception:
        return False
    given_user, _, given_password = decoded.partition(":")
    return hmac.compare_digest(given_user.encode(), username.encode()) and hmac.compare_digest(
        given_password.encode(), password.encode()
    )


def duplicate_key(name: str, amount: float, day: str):
    """Two payments are duplicates when name (ignoring case and spaces), amount and date all match."""
    return (re.sub(r"\s+", " ", name or "").strip().lower(), round(float(amount), 2), day)


def next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def format_amount(amount: float) -> str:
    return str(int(amount)) if float(amount).is_integer() else f"{amount:.2f}".rstrip("0")


def payments_to_text(payments: List[Payment]) -> str:
    """Turns parsed rows into editable 'name,amount,date' lines for the import box."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for name, amount, day in payments:
        writer.writerow([name, format_amount(amount), day])
    return buffer.getvalue()


# ---------- shared row logic (CSV, Excel, PDF tables) ----------

def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def rows_to_payments(rows) -> Tuple[List[Payment], int]:
    """
    rows: list of rows, each a list of cells.
    Finds the date cell, the amount cell and the name cell in each row, so the
    column order does not matter. A header row at the top is skipped.
    Returns (payments, skipped_row_count).
    """
    good: List[Payment] = []
    skipped = 0
    first_row = True
    for row in rows:
        cells = [c for c in (_cell_text(c) for c in row) if c != ""]
        if not cells:
            continue
        is_first, first_row = first_row, False

        day = None
        day_index = None
        for index, cell in enumerate(cells):
            parsed = parse_date(cell)
            if parsed:
                day, day_index = parsed, index
                break

        rest = [c for index, c in enumerate(cells) if index != day_index]
        amount = None
        amount_index = None
        for index, cell in enumerate(rest):
            if AMOUNT_CELL_RE.match(cell):
                amount = parse_amount(cell)
                if amount is not None:
                    amount_index = index
                    break
        names = [c for index, c in enumerate(rest) if index != amount_index and not AMOUNT_CELL_RE.match(c)]
        name = names[0] if names else ""

        if name and amount is not None and day:
            good.append((name, amount, day))
        elif is_first and day is None and amount is None:
            continue  # header row such as: name, amount, date
        else:
            skipped += 1
    return good, skipped


# ---------- CSV / text ----------

def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_import_text(text: str) -> Tuple[List[Payment], int]:
    """Lines of: name, amount, date (comma or tab separated). A header line is ignored."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        delimiter = "\t" if "\t" in line else ","
        rows.append(next(csv.reader(io.StringIO(line), delimiter=delimiter)))
    return rows_to_payments(rows)


# ---------- Excel ----------

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun"}
MONTH_TITLE_RE = re.compile(
    r"\b(" + "|".join(m for m in MONTHS) + "|" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\.?[\s,\-]*(\d{4})\b",
    re.IGNORECASE,
)


def _month_year(text: str) -> Optional[Tuple[int, int]]:
    match = MONTH_TITLE_RE.search(text or "")
    if not match:
        return None
    word = match.group(1).lower()
    for index, name in enumerate(MONTHS):
        if name.startswith(word[:3]):
            return int(match.group(2)), index + 1
    return None


def _day_number(value) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and float(value).is_integer() and 1 <= int(value) <= 31:
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\d{1,2}", value.strip()):
        number = int(value.strip())
        return number if 1 <= number <= 31 else None
    return None


def _is_formula(value) -> bool:
    return isinstance(value, str) and value.strip().startswith("=")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def calendar_payments(rows) -> Optional[Tuple[List[Payment], int]]:
    """
    Reads a calendar-style sheet: a title like 'November 2026', weekday headers, a row of day
    numbers, then under each day an item cell and an amount cell (two columns per day) until the
    next weekday/day row or a SUM row. Several week blocks stacked down the sheet are supported.
    Returns None when the sheet does not look like this (so the flat reader can try instead).
    rows must be read WITHOUT cached formula values, so SUM rows are recognised as formulas.
    """
    grid = [list(r) for r in rows]
    year_month = None
    for row in grid:
        for cell in row:
            if isinstance(cell, str):
                year_month = _month_year(cell)
                if year_month:
                    break
        if year_month:
            break

    # A day-number row: every filled cell is a day number 1..31, increasing from left to right.
    day_rows = []
    for r, row in enumerate(grid):
        filled = [(c, v) for c, v in enumerate(row) if v is not None and str(v).strip() != ""]
        if not filled or any(_is_formula(v) for _, v in filled):
            continue
        days = [_day_number(v) for _, v in filled]
        if all(d is not None for d in days) and days == sorted(set(days)):
            day_rows.append((r, [(c, d) for (c, _), d in zip(filled, days)]))
    if not day_rows:
        return None
    if year_month is None:
        raise ValueError(
            "This looks like a calendar sheet but the month and year were not found. "
            "Add a title cell such as 'November 2026' at the top."
        )
    year, month = year_month

    def is_boundary(row) -> bool:
        cells = [v for v in row if v is not None and str(v).strip() != ""]
        if any(_is_formula(v) for v in cells):
            return True  # SUM / total row
        if any(isinstance(v, str) and v.strip().lower().rstrip(".:") in WEEKDAYS for v in cells):
            return True  # weekday header row
        if any(isinstance(v, str) and re.match(r"^(grand\s+)?total\b", v.strip(), re.I) for v in cells):
            return True
        return False

    day_row_numbers = {r for r, _ in day_rows}
    good: List[Payment] = []
    skipped = 0
    for r, days in day_rows:
        end = len(grid)
        for q in range(r + 1, len(grid)):
            if q in day_row_numbers or is_boundary(grid[q]):
                end = q
                break
        for col, day in days:
            try:
                day_iso = date(year, month, day).isoformat()
            except ValueError:
                skipped += 1  # for example 31 in a 30-day month
                continue
            for q in range(r + 1, end):
                row = grid[q]
                name = row[col] if col < len(row) else None
                amount = row[col + 1] if col + 1 < len(row) else None
                name_text = _cell_text(name)
                amount_value = amount if _is_number(amount) else parse_amount(_cell_text(amount)) if amount is not None else None
                if not name_text and (amount is None or amount == 0 or amount == ""):
                    continue  # empty pair, or a 0 with no item
                if name_text and amount_value and amount_value > 0:
                    good.append((name_text, float(amount_value), day_iso))
                elif name_text and (amount is None or amount == 0 or amount == ""):
                    # item with no amount: nothing to pay, skip quietly only when it is 0
                    if amount == 0:
                        continue
                    skipped += 1
                else:
                    skipped += 1
    return good, skipped


def parse_xlsx(data: bytes) -> Tuple[List[Payment], int]:
    try:
        import openpyxl
    except ImportError:
        raise ValueError("Excel import needs openpyxl. Run: pip install openpyxl")
    try:
        with_formulas = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
        with_values = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ValueError("Could not read this Excel file. Make sure it is a real .xlsx file.")
    good: List[Payment] = []
    skipped = 0
    for sheet_f, sheet_v in zip(with_formulas.worksheets, with_values.worksheets):
        calendar = calendar_payments(list(row) for row in sheet_f.iter_rows(values_only=True))
        if calendar is not None:
            sheet_good, sheet_skipped = calendar
        else:
            rows = [list(row) for row in sheet_v.iter_rows(values_only=True)]
            sheet_good, sheet_skipped = rows_to_payments(rows)
        good += sheet_good
        skipped += sheet_skipped
    with_formulas.close()
    with_values.close()
    return good, skipped


# ---------- PDF ----------

def _parse_pdf_line(line: str) -> Optional[Payment]:
    """Reads a text line like 'Rent  ₹12,000  01-11-2026'. Date and amount can be anywhere."""
    match = DATE_RE.search(line)
    if not match:
        return None
    day = parse_date(match.group(0))
    if not day:
        return None
    rest = line[: match.start()] + " " + line[match.end():]
    numbers = list(NUMBER_RE.finditer(rest))
    if not numbers:
        return None
    last = numbers[-1]
    amount = parse_amount(last.group(1))
    if amount is None:
        return None
    name = rest[: last.start()] + " " + rest[last.end():]
    name = re.sub(r"\s+", " ", name).strip(" -–|:,")
    if not name:
        return None
    return name, amount, day


def parse_pdf(data: bytes) -> Tuple[List[Payment], int]:
    try:
        import pdfplumber
    except ImportError:
        raise ValueError("PDF import needs pdfplumber. Run: pip install pdfplumber")
    table_rows, text_lines = [], []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    table_rows.extend(table)
                text_lines.extend((page.extract_text() or "").splitlines())
    except Exception:
        raise ValueError("Could not read this PDF file.")

    if not table_rows and not any(line.strip() for line in text_lines):
        raise ValueError(
            "No text found in this PDF. It may be a scanned image. "
            "Export it as CSV or Excel, or type the list."
        )

    good, skipped = rows_to_payments(table_rows) if table_rows else ([], 0)
    if not good:  # no table found: read line by line
        good, skipped = [], 0
        for line in text_lines:
            parsed = _parse_pdf_line(line)
            if parsed:
                good.append(parsed)
            elif DATE_RE.search(line):
                skipped += 1
    return good, skipped