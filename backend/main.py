import csv
import hashlib
import io
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from calendar_export import build_calendar_workbook
from database import get_db, init_db
from schemas import ImportRequest, PaymentCreate, PaymentOut, StatusUpdate
from utils import (
    check_basic_auth,
    decode_text,
    duplicate_key,
    next_day,
    parse_import_text,
    parse_pdf,
    parse_xlsx,
    payments_to_text,
    sniff_image,
)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 3 * 1024 * 1024
FILE_TYPES = {
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}

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


def check_month(month: Optional[str]) -> None:
    if month is not None and not MONTH_PATTERN.match(month):
        raise HTTPException(status_code=422, detail="month must look like 2026-11")


# ---------- payments ----------

@app.get("/api/payments", response_model=List[PaymentOut])
def list_payments(month: Optional[str] = None, db: sqlite3.Connection = Depends(get_db)):
    check_month(month)
    sql = (
        "SELECT p.*, (SELECT COUNT(*) FROM payment_images i WHERE i.payment_id = p.id) AS has_image "
        "FROM payments p"
    )
    params: tuple = ()
    if month:
        sql += " WHERE p.date LIKE ?"
        params = (month + "-%",)
    sql += " ORDER BY p.date, p.name"
    return [dict(row) for row in db.execute(sql, params).fetchall()]


@app.post("/api/payments", response_model=PaymentOut, status_code=201)
def create_payment(payload: PaymentCreate, db: sqlite3.Connection = Depends(get_db)):
    new_key = duplicate_key(payload.name, payload.amount, payload.date.isoformat())
    if new_key in existing_keys(db):
        raise HTTPException(
            status_code=409,
            detail="This payment is already in the list (same name, amount and date).",
        )
    cur = db.execute(
        "INSERT INTO payments (name, amount, date) VALUES (?, ?, ?)",
        (payload.name.strip(), payload.amount, payload.date.isoformat()),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM payments WHERE id = ?", (cur.lastrowid,)).fetchone())


@app.patch("/api/payments/{payment_id}/status", response_model=PaymentOut)
def update_status(payment_id: int, payload: StatusUpdate, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    # Delayed means: today's payment moves to the next day.
    new_date = next_day(row["date"]) if payload.status == "delayed" else None
    db.execute(
        "UPDATE payments SET status = ?, new_date = ? WHERE id = ?",
        (payload.status, new_date, payment_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone())


@app.delete("/api/payments/{payment_id}", status_code=204)
def delete_payment(payment_id: int, db: sqlite3.Connection = Depends(get_db)):
    db.execute("DELETE FROM payment_images WHERE payment_id = ?", (payment_id,))
    cur = db.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
    db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Payment not found")


@app.delete("/api/payments")
def delete_all_payments(db: sqlite3.Connection = Depends(get_db)):
    """Removes every payment. The frontend asks the user to confirm first."""
    db.execute("DELETE FROM payment_images")
    cur = db.execute("DELETE FROM payments")
    db.commit()
    return {"deleted": cur.rowcount}


# ---------- import / export ----------

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
        seen = set()
    else:
        seen = existing_keys(db)
    new_rows, duplicates = [], 0
    for name, amount, day in good:
        key = duplicate_key(name, amount, day)
        if key in seen:
            duplicates += 1  # already in the list, or repeated inside this file
            continue
        seen.add(key)
        new_rows.append((name, amount, day))
    if new_rows:
        db.executemany("INSERT INTO payments (name, amount, date) VALUES (?, ?, ?)", new_rows)
    db.commit()
    return {"imported": len(new_rows), "skipped": skipped, "duplicates": duplicates}


@app.post("/api/parse-file")
async def parse_file(file: UploadFile = File(...)):
    """
    Reads a .csv, .txt, .xlsx or .pdf file and returns the payments it found as
    editable text lines. Nothing is saved here: the frontend shows the lines so
    the user can check them, then calls /api/payments/import.
    """
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (limit is 5 MB)")
    filename = (file.filename or "").lower()
    try:
        if filename.endswith((".csv", ".txt")):
            good, skipped = parse_import_text(decode_text(data))
        elif filename.endswith(".xlsx"):
            good, skipped = parse_xlsx(data)
        elif filename.endswith(".pdf"):
            good, skipped = parse_pdf(data)
        elif filename.endswith(".xls"):
            raise ValueError("Old .xls files are not supported. In Excel, use Save As and choose .xlsx or .csv.")
        else:
            raise ValueError("Unsupported file type. Use .csv, .txt, .xlsx or .pdf")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    if not good:
        raise HTTPException(
            status_code=422,
            detail="No payments found. Each row needs a name, an amount and a date.",
        )
    return {"text": payments_to_text(good), "count": len(good), "skipped": skipped}


# ---------- stored import files ----------

def attachment_headers(filename: str) -> dict:
    return {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename)}


@app.post("/api/files")
def store_file(file: UploadFile = File(...), db: sqlite3.Connection = Depends(get_db)):
    """Keeps a copy of an imported CSV, Excel or PDF file. The same file is stored only once."""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (limit is 5 MB)")
    name = os.path.basename((file.filename or "upload").replace("\\", "/")) or "upload"
    extension = os.path.splitext(name.lower())[1]
    if extension not in FILE_TYPES:
        raise HTTPException(status_code=422, detail="Only .csv, .txt, .xlsx and .pdf files are stored")
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


@app.delete("/api/files/{file_id}", status_code=204)
def delete_file(file_id: int, db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute("DELETE FROM files WHERE id = ?", (file_id,))
    db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="File not found")


# ---------- payment images ----------

@app.post("/api/payments/{payment_id}/image")
def upload_image(payment_id: int, file: UploadFile = File(...), db: sqlite3.Connection = Depends(get_db)):
    if db.execute("SELECT id FROM payments WHERE id = ?", (payment_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    data = file.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large (limit is 3 MB)")
    content_type = sniff_image(data)
    if content_type is None:
        raise HTTPException(status_code=422, detail="Upload a JPG, PNG, WEBP or GIF image")
    db.execute("DELETE FROM payment_images WHERE payment_id = ?", (payment_id,))
    db.execute(
        "INSERT INTO payment_images (payment_id, content_type, data) VALUES (?, ?, ?)",
        (payment_id, content_type, data),
    )
    db.commit()
    return {"ok": True}


@app.get("/api/payments/{payment_id}/image")
def get_image(payment_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute(
        "SELECT content_type, data FROM payment_images WHERE payment_id = ?", (payment_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No image for this payment")
    return Response(
        content=bytes(row["data"]),
        media_type=row["content_type"],
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@app.delete("/api/payments/{payment_id}/image", status_code=204)
def delete_image(payment_id: int, db: sqlite3.Connection = Depends(get_db)):
    db.execute("DELETE FROM payment_images WHERE payment_id = ?", (payment_id,))
    db.commit()


# ---------- Excel export (calendar layout) ----------

@app.get("/api/export-calendar.xlsx")
def export_calendar(month: Optional[str] = None, db: sqlite3.Connection = Depends(get_db)):
    """Imported and manually added payments in the same calendar layout you import. One sheet per month."""
    check_month(month)
    sql = "SELECT name, amount, date, status FROM payments"
    params: tuple = ()
    if month:
        sql += " WHERE date LIKE ?"
        params = (month + "-%",)
    sql += " ORDER BY date, name"
    rows = [dict(row) for row in db.execute(sql, params).fetchall()]
    if not rows:
        raise HTTPException(status_code=404, detail="No payments to export for this selection")
    return Response(
        content=build_calendar_workbook(rows),
        media_type=FILE_TYPES[".xlsx"],
        headers=attachment_headers(f"payments-{month or 'all-months'}.xlsx"),
    )


@app.get("/api/export.csv", response_class=PlainTextResponse)
def export_csv(db: sqlite3.Connection = Depends(get_db)):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["name", "amount", "date", "status", "new_date"])
    for row in db.execute("SELECT * FROM payments ORDER BY date, name"):
        writer.writerow([row["name"], row["amount"], row["date"], row["status"], row["new_date"] or ""])
    return PlainTextResponse(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="payments.csv"'},
    )


# ---------- month list and summary ----------

@app.get("/api/months", response_model=List[str])
def list_months(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT DISTINCT substr(date, 1, 7) AS m FROM payments ORDER BY m").fetchall()
    return [row["m"] for row in rows]


@app.get("/api/summary")
def summary(month: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    check_month(month)
    result = {
        status: {"count": 0, "amount": 0.0}
        for status in ("pending", "paid", "delayed", "cancelled")
    }
    rows = db.execute(
        "SELECT status, COUNT(*) AS c, SUM(amount) AS s FROM payments WHERE date LIKE ? GROUP BY status",
        (month + "-%",),
    ).fetchall()
    for row in rows:
        result[row["status"]] = {"count": row["c"], "amount": row["s"] or 0.0}
    result["total"] = {
        "count": sum(v["count"] for v in result.values()),
        "amount": sum(v["amount"] for v in result.values()),
    }
    result["to_pay"] = {
        "count": result["pending"]["count"] + result["delayed"]["count"],
        "amount": result["pending"]["amount"] + result["delayed"]["amount"],
    }
    return result


# Serve the frontend. This must stay last so it does not hide the /api routes.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")