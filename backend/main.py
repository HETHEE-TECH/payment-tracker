import hashlib
import os
import re
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from calendar_export import build_calendar_workbook
from database import USING_POSTGRES, get_db, init_db
from report import build_option_report_pdf, build_report_pdf, filter_items, scope_label
from schemas import AmountUpdate, CategoryUpdate, DescriptionUpdate, ImportRequest, PaymentAdd, PaymentCreate, PaymentOut, StatusUpdate
from utils import (
    check_basic_auth,
    decode_text,
    duplicate_key,
    excel_status,
    next_day,
    parse_docx,
    parse_import_text,
    parse_ods,
    parse_pdf,
    parse_xls,
    parse_xlsx,
    payments_to_text,
    sniff_image,
)
from xlsx_sync import apply_payment, remove_payment, remove_payments

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGES_PER_PAYMENT = 10
FILE_TYPES = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}
SUPPORTED_TYPES_TEXT = ".csv, .tsv, .txt, .xlsx, .xlsm, .xls, .ods, .docx or .pdf"

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # creates the payments table on first start
    yield


app = FastAPI(title="Payment Tracker API", lifespan=lifespan)

# Optional login. On Render, set APP_USERNAME and APP_PASSWORD, and the browser asks for them
# before showing anything. Without them (your own computer) there is no login.
APP_USERNAME = os.environ.get("APP_USERNAME", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
LOGIN_ENABLED = bool(APP_USERNAME and APP_PASSWORD)

# Lets the frontend call the API even when it is opened from another port (e.g. Live Server).
# Switched off when the login is on, because then the page and the API share one address.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if LOGIN_ENABLED else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def always_check_for_new_frontend(request, call_next):
    """Tells the browser to re-check index.html, script.js and style.css on every load,
    so an updated frontend file is never hidden behind an old cached copy."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def require_login(request, call_next):
    if LOGIN_ENABLED and request.method != "OPTIONS":
        if not check_basic_auth(request.headers.get("authorization"), APP_USERNAME, APP_PASSWORD):
            return Response(
                "Login required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Payment Tracker"'},
            )
    return await call_next(request)


def existing_keys(db) -> set:
    rows = db.execute("SELECT name, amount, date FROM payments").fetchall()
    return {duplicate_key(r["name"], r["amount"], r["date"]) for r in rows}


def existing_by_key(db) -> dict:
    """key -> (payment id, description) for every payment already in the list."""
    rows = db.execute("SELECT id, name, amount, date, description FROM payments").fetchall()
    return {duplicate_key(r["name"], r["amount"], r["date"]): (r["id"], r["description"]) for r in rows}


_SYNC_LOCK = threading.Lock()  # one writer at a time, so two people never overwrite each other's Excel edit


def sync_stored_excels(db, payment: dict, add_if_missing: bool = True) -> dict:
    payment = {**payment, "status": excel_status(payment)}  # the colour: the status, or the amount tag
    with _SYNC_LOCK:
        return _sync_stored_excels(db, payment, add_if_missing)


def remove_from_stored_excels(db, payment: dict) -> dict:
    """Takes a deleted payment out of every stored Excel file, so the sheets match the list."""
    result = {"files_updated": [], "sync_error": None}
    with _SYNC_LOCK:
        try:
            files = db.execute(
                "SELECT id, filename, data FROM files WHERE content_type = ? ORDER BY id"
                + (" FOR UPDATE" if USING_POSTGRES else ""),
                (FILE_TYPES[".xlsx"],),
            ).fetchall()
            for row in files:
                try:
                    new_data, state = remove_payment(bytes(row["data"]), payment)
                except Exception as error:  # one unreadable file must not block the others
                    result["sync_error"] = f"{row['filename']}: {error}"
                    continue
                if new_data is not None:
                    db.execute(
                        "UPDATE files SET data = ?, size = ? WHERE id = ?", (new_data, len(new_data), row["id"])
                    )
                    result["files_updated"].append(row["filename"])
            db.commit()
        except Exception as error:
            result["sync_error"] = str(error)
    return result


def _sync_stored_excels(db, payment: dict, add_if_missing: bool = True) -> dict:
    """
    Writes the payment and its status colour into every stored Excel file that has a sheet for the
    payment's month. When no stored Excel has that month, one is created ("Payments 2026-11.xlsx")
    from the payments of that month. A problem here never stops the payment from being saved.
    """
    result = {"files_updated": [], "sync_error": None}
    try:
        month_key = payment["date"][:7]
        files = db.execute(
            "SELECT id, filename, data FROM files WHERE content_type = ? ORDER BY id"
            + (" FOR UPDATE" if USING_POSTGRES else ""),
            (FILE_TYPES[".xlsx"],),
        ).fetchall()
        has_month = False
        for row in files:
            try:
                new_data, state = apply_payment(bytes(row["data"]), payment, add_if_missing)
            except Exception as error:  # one unreadable file must not block the others
                result["sync_error"] = f"{row['filename']}: {error}"
                has_month = True
                continue
            if state != "no-month":
                has_month = True
            if new_data is not None:
                db.execute(
                    "UPDATE files SET data = ?, size = ? WHERE id = ?", (new_data, len(new_data), row["id"])
                )
                result["files_updated"].append(row["filename"])
        if not has_month and add_if_missing:
            month_rows = [
                dict(r)
                for r in db.execute(
                    "SELECT name, amount, date, status, new_date, description, awaiting_amount, received_amount, "
                    "we_paid_amount, excess_amount, part_pending_amount FROM payments WHERE date LIKE ? ORDER BY date, name",
                    (month_key + "-%",),
                ).fetchall()
            ]
            for month_row in month_rows:
                month_row["status"] = excel_status(month_row)
            if month_rows:
                data = build_calendar_workbook(month_rows)
                filename = f"Payments {month_key}.xlsx"
                db.execute(
                    "INSERT INTO files (filename, content_type, size, sha256, uploaded_at, data) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        filename,
                        FILE_TYPES[".xlsx"],
                        len(data),
                        hashlib.sha256(data).hexdigest(),
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        data,
                    ),
                )
                result["files_updated"].append(filename + " (new)")
        db.commit()
    except Exception as error:
        result["sync_error"] = str(error)
    return result


def check_month(month: Optional[str]) -> None:
    if month is not None and not MONTH_PATTERN.match(month):
        raise HTTPException(status_code=422, detail="month must look like 2026-11")


# ---------- payments ----------

@app.get("/api/payments", response_model=List[PaymentOut])
def list_payments(month: Optional[str] = None, db: sqlite3.Connection = Depends(get_db)):
    check_month(month)
    sql = "SELECT p.* FROM payments p"
    params: tuple = ()
    if month:
        sql += " WHERE p.date LIKE ?"
        params = (month + "-%",)
    sql += " ORDER BY p.date, p.name"
    payments = [dict(row) for row in db.execute(sql, params).fetchall()]
    image_ids: dict = {}
    image_types: dict = {}
    for row in db.execute("SELECT id, payment_id, content_type FROM payment_images ORDER BY id").fetchall():
        image_ids.setdefault(row["payment_id"], []).append(row["id"])
        image_types.setdefault(row["payment_id"], []).append(row["content_type"])
    for payment in payments:
        payment["images"] = image_ids.get(payment["id"], [])
        payment["image_types"] = image_types.get(payment["id"], [])
        payment["has_image"] = bool(payment["images"])  # a true/false, not the number of images
    return payments


@app.post("/api/payments", response_model=PaymentOut, status_code=201)
def create_payment(payload: PaymentAdd, db: sqlite3.Connection = Depends(get_db)):
    new_key = duplicate_key(payload.name, payload.amount, payload.date.isoformat())
    if new_key in existing_keys(db):
        raise HTTPException(
            status_code=409,
            detail="This payment is already in the list (same name, amount and date).",
        )
    day = payload.date.isoformat()
    new_date = next_day(day) if payload.delayed else None  # delayed means: the payment moves to the next day
    extras = [
        round(value, 2) if value else None
        for value in (payload.awaiting_amount, payload.received_amount, payload.we_paid_amount,
                      payload.excess_amount, payload.part_pending_amount)
    ]
    cur = db.execute(
        "INSERT INTO payments (name, amount, date, status, new_date, awaiting_amount, received_amount, "
        "we_paid_amount, excess_amount, part_pending_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (payload.name.strip(), payload.amount, day, payload.status, new_date, *extras),
    )
    db.commit()
    created = dict(db.execute("SELECT * FROM payments WHERE id = ?", (cur.lastrowid,)).fetchone())
    return {**created, **sync_stored_excels(db, created)}


@app.put("/api/payments/{payment_id}", response_model=PaymentOut)
def edit_payment(payment_id: int, payload: PaymentCreate, db: sqlite3.Connection = Depends(get_db)):
    """Changes the name, amount or date of a payment. The stored Excel files follow: the payment is taken out
    of its old place and put in the new one (a new date can mean another week or month)."""
    row = db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    old = dict(row)
    name, amount, day = payload.name.strip(), payload.amount, payload.date.isoformat()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required.")
    new_key = duplicate_key(name, amount, day)
    for key, (other_id, _description) in existing_by_key(db).items():
        if key == new_key and other_id != payment_id:
            raise HTTPException(status_code=409, detail="Another payment has the same name, amount and date.")
    if (name, amount, day) == (old["name"], old["amount"], old["date"]):
        return old  # nothing changed
    new_date = next_day(day) if old["new_date"] else None  # a delayed payment moves along with its date
    db.execute(
        "UPDATE payments SET name = ?, amount = ?, date = ?, new_date = ? WHERE id = ?",
        (name, amount, day, new_date, payment_id),
    )
    db.commit()
    updated = dict(db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone())
    removed = remove_from_stored_excels(db, old)  # out of the old place in every stored Excel file ...
    synced = sync_stored_excels(  # ... and into the new one (put back where it was, or when it has a colour)
        db, updated, add_if_missing=bool(removed["files_updated"]) or excel_status(updated) != "pending"
    )
    return {
        **updated,
        "files_updated": sorted(set(removed["files_updated"]) | set(synced["files_updated"])),
        "sync_error": removed["sync_error"] or synced["sync_error"],
    }


@app.patch("/api/payments/{payment_id}/status", response_model=PaymentOut)
def update_status(payment_id: int, payload: StatusUpdate, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    # Delayed means: today's payment moves to the next day.
    if payload.status is None and payload.delayed is None:
        raise HTTPException(status_code=422, detail="Send a status or delayed true/false.")
    status, new_date = row["status"], row["new_date"]
    delayed = payload.delayed
    if payload.status == "delayed":  # an older page: delayed is a mark now, not a status
        delayed = True
    elif payload.status is not None:
        status = payload.status
    if status == "delayed":
        status = "pending"
    if delayed is True and not new_date:
        new_date = next_day(row["date"])  # delayed means: the payment moves to the next day
    elif delayed is False:
        new_date = None
    db.execute("UPDATE payments SET status = ?, new_date = ? WHERE id = ?", (status, new_date, payment_id))
    db.commit()
    updated = dict(db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone())
    return {**updated, **sync_stored_excels(db, updated, add_if_missing=excel_status(updated) != "pending")}


@app.put("/api/payments/{payment_id}/category", response_model=PaymentOut)
def set_category(payment_id: int, payload: CategoryUpdate, db: sqlite3.Connection = Depends(get_db)):
    """Sets (or, when empty, clears) the category of a payment. A name that only differs in capitals from an
    existing category uses the existing spelling, so Fees and fees do not become two categories."""
    if db.execute("SELECT id FROM payments WHERE id = ?", (payment_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    text = re.sub(r"\s+", " ", payload.category or "").strip() or None
    if text:
        for row in db.execute("SELECT DISTINCT category FROM payments WHERE category IS NOT NULL").fetchall():
            if row["category"].lower() == text.lower():
                text = row["category"]
                break
    db.execute("UPDATE payments SET category = ? WHERE id = ?", (text, payment_id))
    db.commit()
    return dict(db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone())


@app.get("/api/report")
def payment_report(
    q: str = Query(""), status: str = Query(""), year: str = Query(""), month: str = Query(""),
    day: str = Query(""), category: str = Query(""), db: sqlite3.Connection = Depends(get_db),
):
    """A PDF report for the same filters as the page: totals, pie chart, month-wise numbers, categories, the list."""
    rows = [dict(r) for r in db.execute("SELECT * FROM payments ORDER BY date, name").fetchall()]
    selected = filter_items(rows, q, status, year, month, day, category)
    trend_items = filter_items(rows, q, status, category=category)  # every month, like the month rows on the page
    scope = scope_label(q, status, year, month, day, category)
    option = status.strip()
    if option:  # one option chosen (Paid, Delayed, ..., or a typed-in amount): a report with only that option's data
        pdf = build_option_report_pdf(selected, trend_items, option, scope)
    else:
        pdf = build_report_pdf(selected, trend_items, scope)
    name = re.sub(r"[^A-Za-z0-9]+", "-", scope).strip("-").lower() or "all"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="payment-report-{name[:60]}.pdf"'},
    )


# The amounts that can be added to a payment next to its status. They are not part of any total.
EXTRA_COLUMNS = {"awaiting": "awaiting_amount", "received": "received_amount", "we_paid": "we_paid_amount", "excess": "excess_amount", "part_pending": "part_pending_amount"}


@app.put("/api/payments/{payment_id}/extra/{kind}", response_model=PaymentOut)
def set_extra_amount(payment_id: int, kind: str, payload: AmountUpdate, db: sqlite3.Connection = Depends(get_db)):
    """Sets (or, with an empty amount, clears) Awaiting income, We paid or Part paid pending of a payment."""
    column = EXTRA_COLUMNS.get(kind)
    if column is None:
        raise HTTPException(status_code=404, detail="Unknown kind: use awaiting, received, we_paid, excess or part_pending")
    if db.execute("SELECT id FROM payments WHERE id = ?", (payment_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    value = round(payload.amount, 2) if payload.amount is not None else None
    db.execute(f"UPDATE payments SET {column} = ? WHERE id = ?", (value, payment_id))  # the column comes from the fixed list above
    db.commit()
    updated = dict(db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone())
    return {**updated, **sync_stored_excels(db, updated, add_if_missing=excel_status(updated) != "pending")}


@app.put("/api/payments/{payment_id}/description", response_model=PaymentOut)
def update_description(payment_id: int, payload: DescriptionUpdate, db: sqlite3.Connection = Depends(get_db)):
    """Saves the comment for a payment and writes it into the stored Excel files too.
    An empty text clears the description (and the Excel note), unless the payment has an image."""
    text = payload.description.strip()
    if db.execute("SELECT id FROM payments WHERE id = ?", (payment_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if not text:
        images = db.execute("SELECT COUNT(*) AS n FROM payment_images WHERE payment_id = ?", (payment_id,)).fetchone()
        if images["n"]:
            raise HTTPException(
                status_code=422,
                detail="This payment has an image, so it needs a description. Remove the image first to clear it.",
            )
    db.execute("UPDATE payments SET description = ? WHERE id = ?", (text or None, payment_id))
    db.commit()
    updated = dict(db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone())
    if text:
        return {**updated, **sync_stored_excels(db, updated)}
    # Cleared: take the note out of the sheets, but never add the payment to a sheet just for that.
    return {**updated, **sync_stored_excels(db, {**updated, "clear_description": True}, add_if_missing=False)}


@app.delete("/api/payments/{payment_id}")
def delete_payment(payment_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    payment = dict(row)
    db.execute("DELETE FROM payment_images WHERE payment_id = ?", (payment_id,))
    db.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
    db.commit()
    return remove_from_stored_excels(db, payment)  # the stored Excel files lose it too


@app.delete("/api/payments")
def delete_all_payments(db: sqlite3.Connection = Depends(get_db)):
    """Removes every payment. The frontend asks the user to confirm first."""
    db.execute("DELETE FROM payment_images")
    cur = db.execute("DELETE FROM payments")
    db.commit()
    return {"deleted": cur.rowcount}


# ---------- import ----------

@app.post("/api/payments/import")
def import_payments(payload: ImportRequest, db: sqlite3.Connection = Depends(get_db)):
    good, skipped = parse_import_text(payload.text)
    if not good:
        raise HTTPException(
            status_code=422,
            detail="No valid lines found. Each line needs: name, amount, date",
        )
    if payload.replace:
        db.execute("DELETE FROM payment_images")
        db.execute("DELETE FROM payments")
        known = {}
    else:
        known = existing_by_key(db)
    new_rows, duplicates, described = [], 0, 0
    for name, amount, day, description in good:
        key = duplicate_key(name, amount, day)
        if key in known:
            duplicates += 1  # already in the list, or repeated inside this file
            payment_id, old = known[key]
            if description and payment_id is not None and not (old or "").strip():
                # the payment is already there without a description: fill it in, never overwrite one
                db.execute("UPDATE payments SET description = ? WHERE id = ?", (description, payment_id))
                known[key] = (payment_id, description)
                described += 1
            continue
        known[key] = (None, description or None)
        new_rows.append((name, amount, day, description or None))
    if new_rows:
        db.executemany("INSERT INTO payments (name, amount, date, description) VALUES (?, ?, ?, ?)", new_rows)
    db.commit()
    with_description = described + sum(1 for row in new_rows if row[3])
    return {
        "imported": len(new_rows),
        "skipped": skipped,
        "duplicates": duplicates,
        "with_description": with_description,
    }


@app.post("/api/parse-file")
async def parse_file(file: UploadFile = File(...)):
    """
    Reads a .csv, .tsv, .txt, .xlsx, .xlsm, .xls, .ods, .docx or .pdf file and returns the payments it found as
    editable text lines. Nothing is saved here: the frontend shows the lines so
    the user can check them, then calls /api/payments/import.
    """
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (limit is 5 MB)")
    filename = (file.filename or "").lower()
    try:
        if filename.endswith((".csv", ".tsv", ".txt")):
            good, skipped = parse_import_text(decode_text(data))
        elif filename.endswith((".xlsx", ".xlsm")):
            good, skipped = parse_xlsx(data)
        elif filename.endswith(".xls"):
            good, skipped = parse_xls(data)
        elif filename.endswith(".ods"):
            good, skipped = parse_ods(data)
        elif filename.endswith(".docx"):
            good, skipped = parse_docx(data)
        elif filename.endswith(".pdf"):
            good, skipped = parse_pdf(data)
        else:
            raise ValueError("Unsupported file type. Use " + SUPPORTED_TYPES_TEXT + ".")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    if not good:
        detail = "No payments found. Each row needs a name, an amount and a date."
        if filename.endswith(".pdf"):
            detail = (
                "No payments found in this PDF. It can be a table (name, amount, date), lines of text, or a calendar sheet "
                "(month title, weekday names, day numbers). If it is a wide sheet that the PDF cut over several pages, save "
                "the PDF again with all columns on one page, or import the Excel file instead."
            )
        raise HTTPException(status_code=422, detail=detail)
    return {"text": payments_to_text(good), "count": len(good), "skipped": skipped}


# ---------- stored import files ----------

def attachment_headers(filename: str) -> dict:
    return {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename)}


@app.post("/api/files")
def store_file(file: UploadFile = File(...), db: sqlite3.Connection = Depends(get_db)):
    """Keeps a copy of an imported file (CSV, Excel, ODS, Word, PDF). The same file is stored only once."""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (limit is 5 MB)")
    name = os.path.basename((file.filename or "upload").replace("\\", "/")) or "upload"
    extension = os.path.splitext(name.lower())[1]
    if extension not in FILE_TYPES:
        raise HTTPException(status_code=422, detail="Only " + SUPPORTED_TYPES_TEXT + " files are stored")
    digest = hashlib.sha256(data).hexdigest()
    existing = db.execute("SELECT id, filename FROM files WHERE sha256 = ?", (digest,)).fetchone()
    if existing:
        return {"id": existing["id"], "filename": existing["filename"], "already_stored": True}
    cur = db.execute(
        "INSERT INTO files (filename, content_type, size, sha256, uploaded_at, data) VALUES (?, ?, ?, ?, ?, ?)",
        (
            name,
            FILE_TYPES[extension],
            len(data),
            digest,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            data,
        ),
    )
    db.commit()
    return {"id": cur.lastrowid, "filename": name, "already_stored": False}


@app.get("/api/files")
def list_files(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT id, filename, content_type, size, uploaded_at FROM files ORDER BY id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/files/{file_id}")
def download_file(file_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(
        content=bytes(row["data"]),
        media_type=row["content_type"],
        headers=attachment_headers(row["filename"]),
    )


def parse_stored_file(filename: str, data: bytes):
    """The payments a stored file holds (the same reading as Import). An unreadable file gives an empty list."""
    name = (filename or "").lower()
    try:
        if name.endswith((".csv", ".tsv", ".txt")):
            good, _ = parse_import_text(decode_text(data))
        elif name.endswith((".xlsx", ".xlsm")):
            good, _ = parse_xlsx(data)
        elif name.endswith(".xls"):
            good, _ = parse_xls(data)
        elif name.endswith(".ods"):
            good, _ = parse_ods(data)
        elif name.endswith(".docx"):
            good, _ = parse_docx(data)
        elif name.endswith(".pdf"):
            good, _ = parse_pdf(data)
        else:
            return []
    except Exception:
        return []
    return good


@app.delete("/api/files/{file_id}")
def delete_file(file_id: int, db: sqlite3.Connection = Depends(get_db)):
    """Deletes a stored file and the payments that were imported from it (the payments the file holds).
    The other stored Excel files lose those payments too, so every sheet matches the list."""
    row = db.execute("SELECT id, filename, data FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    keys = {duplicate_key(name, amount, day) for name, amount, day, *_ in parse_stored_file(row["filename"], bytes(row["data"]))}
    gone = []
    if keys:
        for payment in db.execute("SELECT id, name, amount, date FROM payments").fetchall():
            if duplicate_key(payment["name"], payment["amount"], payment["date"]) in keys:
                gone.append(dict(payment))
    for payment in gone:
        db.execute("DELETE FROM payment_images WHERE payment_id = ?", (payment["id"],))
        db.execute("DELETE FROM payments WHERE id = ?", (payment["id"],))
    db.execute("DELETE FROM files WHERE id = ?", (file_id,))
    db.commit()
    sync_error = None
    if gone:
        with _SYNC_LOCK:
            try:
                others = db.execute(
                    "SELECT id, filename, data FROM files WHERE content_type = ? ORDER BY id"
                    + (" FOR UPDATE" if USING_POSTGRES else ""),
                    (FILE_TYPES[".xlsx"],),
                ).fetchall()
                for other in others:
                    try:
                        new_data, _count = remove_payments(bytes(other["data"]), gone)
                    except Exception as error:  # one unreadable file must not block the others
                        sync_error = f"{other['filename']}: {error}"
                        continue
                    if new_data is not None:
                        db.execute("UPDATE files SET data = ?, size = ? WHERE id = ?", (new_data, len(new_data), other["id"]))
                db.commit()
            except Exception as error:
                sync_error = str(error)
    return {"deleted_payments": len(gone), "sync_error": sync_error}


# ---------- payment images ----------

@app.post("/api/payments/{payment_id}/image")
def upload_image(payment_id: int, file: UploadFile = File(...), db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT description FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if not (row["description"] or "").strip():
        raise HTTPException(status_code=422, detail="Write a description for this payment before uploading an image.")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    content_type = sniff_image(data)
    if content_type is None:
        raise HTTPException(status_code=422, detail="Upload a JPG, PNG, WEBP or GIF image, or a PDF file")
    if content_type == "application/pdf":
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="PDF is too large (limit is 5 MB)")
    elif len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large (limit is 3 MB)")
    have = db.execute("SELECT COUNT(*) AS n FROM payment_images WHERE payment_id = ?", (payment_id,)).fetchone()["n"]
    if have >= MAX_IMAGES_PER_PAYMENT:
        raise HTTPException(
            status_code=422,
            detail=f"A payment can have up to {MAX_IMAGES_PER_PAYMENT} images or PDFs. Remove one to add another.",
        )
    db.execute(  # adds one more image; the payment's other images stay
        "INSERT INTO payment_images (payment_id, content_type, data) VALUES (?, ?, ?)",
        (payment_id, content_type, data),
    )
    db.commit()
    return {"ok": True}


def image_response(row) -> Response:
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    if row["content_type"] == "application/pdf":
        headers["Content-Disposition"] = 'inline; filename="attachment.pdf"'  # opens in the browser's PDF viewer
    return Response(content=bytes(row["data"]), media_type=row["content_type"], headers=headers)


@app.get("/api/images/{image_id}")
def get_image_by_id(image_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT content_type, data FROM payment_images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return image_response(row)


@app.delete("/api/images/{image_id}", status_code=204)
def delete_image_by_id(image_id: int, db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute("DELETE FROM payment_images WHERE id = ?", (image_id,))
    db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Image not found")


@app.get("/api/payments/{payment_id}/image")
def get_image(payment_id: int, db: sqlite3.Connection = Depends(get_db)):
    """The payment's first image or PDF (older link; the page uses /api/images/{id})."""
    row = db.execute(
        "SELECT content_type, data FROM payment_images WHERE payment_id = ? ORDER BY id LIMIT 1", (payment_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No image for this payment")
    return image_response(row)


@app.delete("/api/payments/{payment_id}/image", status_code=204)
def delete_images(payment_id: int, db: sqlite3.Connection = Depends(get_db)):
    """Removes all images and PDFs of a payment."""
    db.execute("DELETE FROM payment_images WHERE payment_id = ?", (payment_id,))
    db.commit()


# ---------- month list and summary ----------

@app.get("/api/months", response_model=List[str])
def list_months(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT DISTINCT substr(date, 1, 7) AS m FROM payments ORDER BY m").fetchall()
    return [row["m"] for row in rows]


@app.get("/api/summary")
def summary(month: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    check_month(month)
    result = {status: {"count": 0, "amount": 0.0} for status in ("pending", "paid", "delayed", "cancelled")}
    total = {"count": 0, "amount": 0.0}
    rows = db.execute(
        "SELECT status, CASE WHEN new_date IS NOT NULL THEN 1 ELSE 0 END AS d, COUNT(*) AS c, SUM(amount) AS s "
        "FROM payments WHERE date LIKE ? GROUP BY status, CASE WHEN new_date IS NOT NULL THEN 1 ELSE 0 END",
        (month + "-%",),
    ).fetchall()
    to_pay = {"count": 0, "amount": 0.0}
    for row in rows:
        status = "pending" if row["status"] == "delayed" else row["status"]
        delayed = row["d"] == 1 or row["status"] == "delayed"
        amount = row["s"] or 0.0
        total["count"] += row["c"]
        total["amount"] += amount
        if delayed:  # Delayed counts every delayed payment, also one that is paid or cancelled as well
            result["delayed"]["count"] += row["c"]
            result["delayed"]["amount"] += amount
        if status in ("paid", "cancelled") or not delayed:
            result[status]["count"] += row["c"]
            result[status]["amount"] += amount
        if status == "pending":  # still to pay: not paid and not cancelled, delayed or not
            to_pay["count"] += row["c"]
            to_pay["amount"] += amount
    result["total"] = total
    result["to_pay"] = to_pay
    # Awaiting income, We paid and Part paid pending: the amounts that were entered, kept out of the totals above.
    extra = db.execute(
        "SELECT COUNT(awaiting_amount) AS ac, SUM(awaiting_amount) AS a, COUNT(received_amount) AS rc, "
        "SUM(received_amount) AS r, COUNT(we_paid_amount) AS wc, SUM(we_paid_amount) AS w, "
        "COUNT(excess_amount) AS xc, SUM(excess_amount) AS x, "
        "COUNT(part_pending_amount) AS pc, SUM(part_pending_amount) AS p "
        "FROM payments WHERE date LIKE ?",
        (month + "-%",),
    ).fetchone()
    result["awaiting_income"] = {"count": extra["ac"] or 0, "amount": extra["a"] or 0.0}
    result["received"] = {"count": extra["rc"] or 0, "amount": extra["r"] or 0.0}
    result["we_paid"] = {"count": extra["wc"] or 0, "amount": extra["w"] or 0.0}
    result["excess"] = {"count": extra["xc"] or 0, "amount": extra["x"] or 0.0}
    result["part_pending"] = {"count": extra["pc"] or 0, "amount": extra["p"] or 0.0}
    return result


# Serve the frontend. This must stay last so it does not hide the /api routes.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")