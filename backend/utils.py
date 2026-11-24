import csv
import io
import re
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

Payment = Tuple[str, float, str, str]  # (name, amount, YYYY-MM-DD, description - "" when there is none)
DESCRIPTION_HEADERS = ("description", "comment", "comments", "remarks", "remark", "note", "notes")
MAX_DESCRIPTION = 500


def clip_description(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_DESCRIPTION]

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


def sniff_image(data: bytes) -> Optional[str]:
    """Returns the type from the file's first bytes (JPG, PNG, GIF, WEBP or PDF), or None for anything else."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"  # a PDF can be attached to a payment like an image
    return None


def next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def format_amount(amount: float) -> str:
    return str(int(amount)) if float(amount).is_integer() else f"{amount:.2f}".rstrip("0")


def payments_to_text(payments: List[Payment]) -> str:
    """Turns parsed rows into editable 'name,amount,date,description' lines for the import box."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for name, amount, day, *rest in payments:
        row = [name, format_amount(amount), day]
        if rest and rest[0]:
            row.append(rest[0])
        writer.writerow(row)
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
    A column named Description (or Comment, Remarks, Note) is read as the description. Without a header
    line, text after the name is the description. Returns (payments, skipped_row_count).
    """
    good: List[Payment] = []
    skipped = 0
    first_row = True
    all_rows = [[_cell_text(c) for c in row] for row in rows]
    first = next((r for r in all_rows if any(r)), None)
    has_header = first is not None and not any(parse_date(c) or AMOUNT_CELL_RE.match(c) for c in first if c)
    desc_col = None
    if has_header:
        desc_col = next((i for i, c in enumerate(first) if c.strip().lower() in DESCRIPTION_HEADERS), None)
    for texts in all_rows:
        description = ""
        if desc_col is not None and desc_col < len(texts):
            description = clip_description(texts[desc_col])
            texts = texts[:desc_col] + [""] + texts[desc_col + 1:]  # keep it out of the name
        cells = [c for c in texts if c != ""]
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
            if not has_header and not description and len(names) > 1:
                description = clip_description(", ".join(names[1:]))  # text after the name, no header line
            good.append((name, amount, day, description))
        elif is_first and day is None and amount is None:
            continue  # header row such as: name, amount, date
        else:
            skipped += 1
    return good, skipped


# ---------- CSV / text ----------

def decode_text(data: bytes) -> str:
    """Text from a .csv/.txt file: UTF-8 (with or without BOM), UTF-16 (Excel's 'Unicode Text') or Windows-1252."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    if b"\x00" in data[:200]:  # UTF-16 without a BOM
        try:
            return data.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", errors="replace")


def _split_line(line: str) -> list:
    """One line of a text file as fields. The separator can be a tab, a semicolon, a pipe or a comma:
    the one that gives the most fields wins (quotes are respected)."""
    best = None
    for delimiter in ("\t", ";", "|", ","):
        if delimiter not in line:
            continue
        try:
            fields = next(csv.reader(io.StringIO(line), delimiter=delimiter))
        except Exception:
            continue
        if best is None or len(fields) > len(best):
            best = fields
    return best if best is not None else [line]


def parse_import_text(text: str) -> Tuple[List[Payment], int]:
    """Lines of: name, amount, date (separated by commas, tabs, semicolons or pipes). A header line is ignored."""
    rows = [_split_line(line) for line in text.splitlines() if line.strip()]
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


MONTH_TOKENS = {name: i + 1 for i, name in enumerate(MONTHS)}
MONTH_TOKENS.update({name[:3]: i + 1 for i, name in enumerate(MONTHS)})
MONTH_TOKENS["sept"] = 9
SHORT_TITLE_RE = re.compile(r"^\s*([A-Za-z]+)\.?[\s,\-'\u2019/]*(\d{2}|\d{4})?\s*$")  # Nov 2026, NOV-26, Nov'26, Nov
MONTH_FIRST_NUMERIC_RE = re.compile(r"^\s*(\d{1,2})\s*[/\-.]\s*(\d{4})\s*$")  # 11/2026
YEAR_FIRST_NUMERIC_RE = re.compile(r"^\s*(\d{4})\s*[/\-.]\s*(\d{1,2})\s*$")  # 2026-11


def _short_title(cell, alone: bool) -> Optional[Tuple[int, int]]:
    """A month title written the short way: Nov-26, Nov'26, 11/2026, 2026-11, only 'Nov' (this year is
    assumed), or a real date cell (Excel turns a typed 'Nov 2026' into 1 Nov 2026).
    alone: the title is the only thing in its row. Bare months and dates need that, so that the name
    'May' or a date column in a table is not taken for a title."""
    if isinstance(cell, (datetime, date)):
        return (cell.year, cell.month) if alone else None
    if not isinstance(cell, str):
        return None
    match = SHORT_TITLE_RE.match(cell)
    if match:
        month = MONTH_TOKENS.get(match.group(1).lower())
        if month is None:
            return None
        year = match.group(2)
        if year is None:
            return (date.today().year, month) if alone else None
        return (2000 + int(year) if len(year) == 2 else int(year), month)
    match = MONTH_FIRST_NUMERIC_RE.match(cell)
    if match and 1 <= int(match.group(1)) <= 12:
        return int(match.group(2)), int(match.group(1))
    match = YEAR_FIRST_NUMERIC_RE.match(cell)
    if match and 1 <= int(match.group(2)) <= 12:
        return int(match.group(1)), int(match.group(2))
    return None


def find_month_year(rows) -> Optional[Tuple[int, int]]:
    """The (year, month) of a calendar sheet, from its title. 'November 2026' and 'Nov 2026' may stand
    anywhere; the other short forms are looked for in the first three rows only."""
    rows = [list(row) for row in rows]
    for row in rows:
        for cell in row:
            if isinstance(cell, str):
                found = _month_year(cell)
                if found:
                    return found
    for row in rows[:3]:
        alone = sum(1 for c in row if c is not None and str(c).strip() != "") == 1
        for cell in row:
            found = _short_title(cell, alone)
            if found:
                return found
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


def calendar_stride(blocks_days, year: int, month: int) -> Optional[int]:
    """Columns per day in a calendar sheet: 2 (name, amount) or 3 (name, amount, description).
    blocks_days: one {day: column} dict per week block. None when no block has two days to compare."""
    first = (date(year, month, 1).weekday() + 1) % 7  # Sunday = 0
    for days in blocks_days:
        items = sorted(days.items())
        for (d1, c1), (d2, c2) in zip(items, items[1:]):
            diff = ((first + d2 - 1) % 7) - ((first + d1 - 1) % 7)
            if diff > 0 and (c2 - c1) % diff == 0 and (c2 - c1) // diff in (2, 3):
                return (c2 - c1) // diff
    return None


def calendar_payments(rows, notes=None) -> Optional[Tuple[List[Payment], int]]:
    """
    Reads a calendar-style sheet: a title like 'November 2026', weekday headers, a row of day
    numbers, then under each day an item cell, an amount cell and (newer sheets) a description cell
    (two or three columns per day) until the next weekday/day row or a SUM row. Several week blocks stacked down the sheet are supported.
    Returns None when the sheet does not look like this (so the flat reader can try instead).
    rows must be read WITHOUT cached formula values, so SUM rows are recognised as formulas.
    The description is the third column of the day. In older sheets with two columns per day, a note
    on an item's name cell is used instead: notes is an optional grid (same rows and columns) with the
    note text of each cell.
    """
    grid = [list(r) for r in rows]
    year_month = find_month_year(grid)

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
            "Add a title cell such as 'November 2026' (or 'Nov 2026') at the top."
        )
    year, month = year_month
    stride = calendar_stride([{d: c for c, d in days} for _, days in day_rows], year, month)
    has_description_column = stride == 3

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
        hidden = {c + 2 for c, _ in days} if has_description_column else set()  # free text, not a total row
        for q in range(r + 1, len(grid)):
            shown = [None if i in hidden else v for i, v in enumerate(grid[q])] if hidden else grid[q]
            if q in day_row_numbers or is_boundary(shown):
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
                    text = _cell_text(row[col + 2]) if has_description_column and col + 2 < len(row) else ""
                    if not text and notes and q < len(notes) and col < len(notes[q]):
                        text = notes[q][col]  # an older sheet: the note on the name cell
                    good.append((name_text, float(amount_value), day_iso, clip_description(text)))
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
    full = None  # read lazily: notes can only be read when the file is not opened read-only
    for index, (sheet_f, sheet_v) in enumerate(zip(with_formulas.worksheets, with_values.worksheets)):
        grid = [list(row) for row in sheet_f.iter_rows(values_only=True)]
        calendar = calendar_payments(grid)
        if calendar is not None:  # a calendar sheet: read the notes too (a note is the description)
            if full is None:
                try:
                    full = openpyxl.load_workbook(io.BytesIO(data))
                except Exception:
                    full = False
            if full:
                notes = [[(c.comment.text if c.comment else "") for c in row] for row in full.worksheets[index].iter_rows()]
                calendar = calendar_payments(grid, notes)
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
    return name, amount, day, ""


def _payments_from_tables_and_lines(table_rows, text_lines) -> Tuple[List[Payment], int]:
    """Table rows first; when they give no payments, every text line is read on its own."""
    good, skipped = rows_to_payments(table_rows) if table_rows else ([], 0)
    if not good:
        good, skipped = [], 0
        for line in text_lines:
            parsed = _parse_pdf_line(line)
            if parsed:
                good.append(parsed)
            elif DATE_RE.search(line):
                skipped += 1
    return good, skipped


def _pdf_cell(value):
    """A table cell from a PDF: empty becomes None, an amount or a number becomes a number, other text stays text."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    if AMOUNT_CELL_RE.match(text):
        amount = float(re.sub(r"[^0-9.]", "", text) or 0)
        return int(amount) if amount.is_integer() else amount
    return text


_CURRENCY_WORDS = {"\u20b9", "rs", "rs.", "inr", "inr."}


def _pdf_word_lines(page) -> list:
    """The words of a page grouped into text lines, each word with its position: [{"text", "x0", "x1"}, ...]."""
    words = page.extract_words(x_tolerance=2, y_tolerance=2) or []
    words.sort(key=lambda w: (w["top"], w["x0"]))
    lines, current, top = [], [], None
    for word in words:
        if current and abs(word["top"] - top) > 3:
            lines.append(current)
            current = []
        if not current:
            top = word["top"]
        current.append({"text": word["text"], "x0": word["x0"], "x1": word["x1"]})
    if current:
        lines.append(current)
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


_GLUED = re.compile(r"^(\d[\d,]*(?:\.\d+)?)([^\W\d_].*)$")  # '2400advance': an amount touching the text after it


def _split_item(tokens):
    """The words under one day on one line: name, then the amount, then maybe a description.
    Returns (name, amount, description), "empty" when there is no name, or None when there is a name but no amount."""
    words = []
    for token in tokens:
        glued = _GLUED.match(token)
        if glued and (len(glued.group(1)) >= 3 or "," in glued.group(1) or "." in glued.group(1)):
            words += [glued.group(1), glued.group(2)]  # a right-aligned amount can touch the description next to it
        else:
            words.append(token)
    words = [t for t in words if t.lower() not in _CURRENCY_WORDS]
    at = next((i for i, t in enumerate(words) if AMOUNT_CELL_RE.match(t)), None)
    if at == 0:
        return "empty"  # only an amount: a total, or a 0 with no item
    if at is None:
        return "empty" if not words else None
    amount = parse_amount(words[at])
    if amount is None:
        return "empty"
    return " ".join(words[:at]), amount, " ".join(words[at + 1:])


def _pdf_calendar_blocks(lines):
    """The week blocks of a calendar page: the day numbers (with where they stand and which weekday name is
    nearest) and the text lines under them, up to the next row of weekday names."""
    blocks, header, block = [], None, None
    for line in lines:
        texts = [w["text"].strip() for w in line]
        weekdays = [w for w in line if w["text"].strip().lower().rstrip(".:") in WEEKDAYS]
        if weekdays and len(weekdays) * 2 >= len(line):
            header, block = [(w["x0"] + w["x1"]) / 2 for w in weekdays], None
            continue
        if header is None:
            continue
        numbers = [_day_number(t) for t in texts]
        if block is None and all(n is not None for n in numbers) and numbers == sorted(set(numbers)):
            days = []
            for word, number in zip(line, numbers):
                center = (word["x0"] + word["x1"]) / 2
                days.append({"day": number, "x0": word["x0"], "header": min(header, key=lambda h: abs(h - center))})
            block = {"days": days, "lines": []}
            blocks.append(block)
        elif block is not None:
            block["lines"].append(line)
    return blocks


def _read_block(block, column_of, year, mon):
    good: List[Payment] = []
    skipped = 0
    for line in block["lines"]:
        under = {}
        for word in line:
            day = column_of(block["days"], word)
            under.setdefault(day["day"], []).append(word["text"].strip())
        for day, tokens in under.items():
            item = _split_item(tokens)
            if item == "empty":
                continue
            if item is None:
                skipped += 1
                continue
            try:
                day_iso = date(year, mon, day).isoformat()
            except ValueError:
                skipped += 1
                continue
            name, amount, description = item
            good.append((name, float(amount), day_iso, clip_description(description)))
    return good, skipped


# Two ways to tell which day a word belongs to. The day number can stand at the left edge of its day (left aligned)
# or the weekday name can stand over the middle of it. Both are tried and the one that reads more items is used.
def _by_left_edge(days, word):
    left = [d for d in days if d["x0"] <= word["x0"] + 6]
    return max(left, key=lambda d: d["x0"]) if left else min(days, key=lambda d: d["x0"])


def _by_weekday_middle(days, word):
    center = (word["x0"] + word["x1"]) / 2
    return min(days, key=lambda d: abs(d["header"] - center))


def _pdf_calendar_lines(lines, month) -> Tuple[List[Payment], int]:
    """A calendar sheet in a PDF without ruled lines: the position of the words says which day each belongs to."""
    year, mon = month
    good: List[Payment] = []
    skipped = 0
    for block in _pdf_calendar_blocks(lines):
        results = [_read_block(block, strategy, year, mon) for strategy in (_by_left_edge, _by_weekday_middle)]
        best = max(results, key=lambda r: len(r[0]) - 2 * r[1])
        good += best[0]
        skipped += best[1]
    return good, skipped


def _pdf_calendar(pages) -> Tuple[List[Payment], int]:
    """A calendar sheet that was saved as a PDF (month title, weekday names, day numbers, then name and amount under
    each day). pages: (tables, text lines, word lines) per page. A big sheet is cut over several pages by the PDF,
    each page holding some of the days, so every page is read on its own. The month comes from the title text."""
    first_month = next((m for m in (find_month_year([[line] for line in lines]) for _, lines, _ in pages) if m), None)
    good: List[Payment] = []
    skipped = 0
    for tables, lines, word_lines in pages:
        month = find_month_year([[line] for line in lines]) or first_month
        page_good: List[Payment] = []
        if tables:  # ruled lines: the cells of the tables
            for table in tables:
                grid = [[_pdf_cell(cell) for cell in row] for row in table]
                if month and find_month_year(grid) is None:  # the title is not inside the table: put it on top
                    grid = [[date(month[0], month[1], 1).strftime("%B %Y")]] + grid
                try:
                    result = calendar_payments(_mark_total_rows(grid))
                except ValueError:
                    result = None
                if result:
                    page_good += result[0]
                    skipped += result[1]
        if not page_good and month and word_lines:  # no ruled lines (or no calendar in them): the position of the words
            page_good, page_skipped = _pdf_calendar_lines(word_lines, month)
            skipped += page_skipped if page_good else 0
        good += page_good
    return good, skipped


def parse_pdf(data: bytes) -> Tuple[List[Payment], int]:
    try:
        import pdfplumber
    except ImportError:
        raise ValueError("PDF import needs pdfplumber. Run: pip install pdfplumber")
    pages, table_rows, text_lines = [], [], []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                lines = (page.extract_text() or "").splitlines()
                pages.append((tables, lines, _pdf_word_lines(page)))
                for table in tables:
                    table_rows.extend(table)
                text_lines.extend(lines)
    except Exception:
        raise ValueError("Could not read this PDF file.")

    if not table_rows and not any(line.strip() for line in text_lines):
        raise ValueError(
            "No text found in this PDF. It may be a scanned image (a photo of a page). "
            "Export it as CSV or Excel, or type the list."
        )

    calendar = _pdf_calendar(pages)
    if calendar[0]:  # a calendar sheet saved as a PDF
        return calendar
    return _payments_from_tables_and_lines(table_rows, text_lines)


# ---------- other spreadsheet and document formats: .xls, .ods, .docx ----------

def _payments_from_sheets(sheets) -> Tuple[List[Payment], int]:
    """sheets: (values, formulas) grids per sheet. A calendar sheet is read from the formulas grid (a SUM
    row is recognised by being a formula); any other sheet is read as a table from the values grid."""
    good: List[Payment] = []
    skipped = 0
    for values, formulas in sheets:
        calendar = calendar_payments(formulas)
        sheet_good, sheet_skipped = calendar if calendar is not None else rows_to_payments(values)
        good += sheet_good
        skipped += sheet_skipped
    return good, skipped


def _mark_total_rows(grid):
    """An .xls file only holds values, so a SUM row is a row of plain numbers. A row of numbers only, right
    under a row with text, is marked as a formula row, the way a SUM row is in an .xlsx file."""
    marked, previous_text = [], False
    for row in grid:
        filled = [v for v in row if v is not None and str(v).strip() != ""]
        numbers_only = bool(filled) and all(_is_number(v) for v in filled)
        days = [_day_number(v) for v in filled] if numbers_only else []
        is_day_row = numbers_only and all(d is not None for d in days) and days == sorted(set(days))
        if numbers_only and previous_text and not is_day_row:
            marked.append(["=SUM" if _is_number(v) else v for v in row])
        else:
            marked.append(list(row))
        if filled:
            previous_text = any(isinstance(v, str) for v in filled)
    return marked


def parse_xls(data: bytes) -> Tuple[List[Payment], int]:
    """The old Excel format (.xls), read with the xlrd package."""
    try:
        import xlrd
    except ImportError:
        raise ValueError("Old .xls files need the xlrd package (pip install xlrd). Or save the file as .xlsx.")
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception:
        raise ValueError("Could not read this .xls file. In Excel, use Save As and choose .xlsx.")
    sheets = []
    for sheet in book.sheets():
        grid = []
        for r in range(sheet.nrows):
            row = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        value = xlrd.xldate_as_datetime(cell.value, book.datemode)
                    except Exception:
                        value = None
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                    value = None
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    value = bool(cell.value)
                else:
                    value = cell.value
                row.append(value)
            grid.append(row)
        sheets.append((grid, _mark_total_rows(grid)))
    return _payments_from_sheets(sheets)


_ODF = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}


def _odf(prefix: str, name: str) -> str:
    return "{%s}%s" % (_ODF[prefix], name)


def _ods_value(cell):
    kind = cell.get(_odf("office", "value-type"))
    if kind in ("float", "percentage", "currency"):
        try:
            number = float(cell.get(_odf("office", "value")))
        except (TypeError, ValueError):
            return None
        return int(number) if number.is_integer() else number
    if kind == "date":
        try:
            return datetime.fromisoformat(cell.get(_odf("office", "date-value")) or "")
        except ValueError:
            return None
    if kind == "boolean":
        return cell.get(_odf("office", "boolean-value")) == "true"
    text = "\n".join("".join(p.itertext()) for p in cell.findall(_odf("text", "p"))).strip()
    return text or None


def parse_ods(data: bytes) -> Tuple[List[Payment], int]:
    """OpenDocument spreadsheets (.ods: LibreOffice, Google Sheets download), read with the standard library."""
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("content.xml"))
    except Exception:
        raise ValueError("Could not read this .ods file.")
    sheets = []
    for table in root.iter(_odf("table", "table")):
        values, formulas = [], []
        for row in table.iter(_odf("table", "table-row")):
            row_values, row_formulas = [], []
            for cell in row:
                if cell.tag not in (_odf("table", "table-cell"), _odf("table", "covered-table-cell")):
                    continue
                value = _ods_value(cell) if cell.tag == _odf("table", "table-cell") else None
                formula = cell.get(_odf("table", "formula"))
                shown = "=" + formula.split("=", 1)[-1] if formula else value
                repeat = int(cell.get(_odf("table", "number-columns-repeated"), "1") or 1)
                repeat = min(repeat, 100 if (value is not None or formula) else 64)
                row_values.extend([value] * repeat)
                row_formulas.extend([shown] * repeat)
            while row_values and row_values[-1] is None and row_formulas[-1] is None:  # trailing empty cells
                row_values.pop()
                row_formulas.pop()
            repeat_rows = int(row.get(_odf("table", "number-rows-repeated"), "1") or 1)
            repeat_rows = min(repeat_rows, 100 if row_values else 2)
            for _ in range(repeat_rows):
                values.append(list(row_values))
                formulas.append(list(row_formulas))
            if len(values) > 20000:
                break
        sheets.append((values, formulas))
    return _payments_from_sheets(sheets)


def parse_docx(data: bytes) -> Tuple[List[Payment], int]:
    """Word documents (.docx): the tables are read first, otherwise every line of text."""
    import zipfile
    import xml.etree.ElementTree as ET
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        body = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")).find(w + "body")
    except Exception:
        raise ValueError("Could not read this Word file. Make sure it is a .docx file.")

    def text_of(element) -> str:
        parts = []
        for node in element.iter():
            if node.tag == w + "t":
                parts.append(node.text or "")
            elif node.tag == w + "tab":
                parts.append("\t")
        return "".join(parts)

    table_rows, lines = [], []
    for child in body:
        if child.tag == w + "tbl":
            for tr in child.iter(w + "tr"):
                table_rows.append([" ".join(text_of(p) for p in tc.iter(w + "p")).strip() for tc in tr.findall(w + "tc")])
        elif child.tag == w + "p":
            lines.append(text_of(child))
    if not table_rows and not any(line.strip() for line in lines):
        raise ValueError("No text found in this Word file.")
    return _payments_from_tables_and_lines(table_rows, lines)


# ---------- the colour a payment gets in an Excel sheet ----------

def excel_status(payment: dict) -> str:
    """Paid and cancelled keep their own colour (also when the payment is delayed too). A payment that is
    delayed is yellow. A pending payment that also has an amount in Awaiting income, We paid or Part paid
    pending is coloured by the first of those."""
    if payment.get("status") in ("paid", "cancelled"):
        return payment["status"]
    if payment.get("status") == "delayed" or payment.get("new_date"):
        return "delayed"
    if payment.get("awaiting_amount"):
        return "awaiting_income"
    if payment.get("received_amount"):
        return "received"
    if payment.get("we_paid_amount"):
        return "we_paid"
    if payment.get("excess_amount"):
        return "excess"
    if payment.get("part_pending_amount"):
        return "part_pending"
    return payment.get("status") or "pending"