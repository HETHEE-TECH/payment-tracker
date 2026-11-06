# Payment Tracker

Monthly payment tracker. Import `name, amount, date` from a **CSV, Excel (.xlsx), PDF** or text file
(or paste the lines), then mark each payment:

- **Green** (Paid): paid on its date.
- **Yellow** (Delayed): moved to the next day. The list shows "Pay on <date>".
- **Red** (Cancelled).

## Structure

```
payment-tracker/
  backend/
    main.py          FastAPI routes
    database.py      SQLite setup and connection
    schemas.py       Request and response models (Pydantic)
    utils.py         CSV line, date and amount parsing
    requirements.txt
  frontend/
    index.html
    style.css
    script.js        Calls the API with fetch()
  sample.csv         Try this with Import
```

## Run

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000. The backend serves the frontend too, so one command is enough.
API docs (Swagger): http://127.0.0.1:8000/docs

The database file `backend/payments.db` is created on first start.

## Importing files

Choosing a file does not save anything. The backend reads it and puts the rows it found in the
import box, so you can check and fix them. Then click **Add to list** or **Replace whole list**.

- **CSV / TXT**: `name, amount, date` per line.
- **Excel (.xlsx)**: every sheet is read. Column order does not matter, the app finds the date,
  amount and name in each row. Old `.xls` files: use Save As `.xlsx` first.
- **PDF**: tables are read first, otherwise each text line is read. Always check the rows after
  importing a PDF, because PDF layouts vary. Scanned PDFs (pictures of text) are not supported.

## Deploy on Render (with a free Postgres database)

Render's free plan wipes local files on every restart, so a SQLite file would lose your payments.
On Render the app uses Postgres instead. Locally nothing changes, it still uses SQLite.

1. **Database:** create a free Postgres database (for example on neon.tech). Copy its connection
   string, it starts with `postgresql://` and ends with `?sslmode=require`.
2. **Render:** New > Web Service > connect the GitHub repo. Then set:
   - Root Directory: leave it **empty**. The backend serves the `frontend` folder, and Render does
     not give a service the files outside its Root Directory. With a Root Directory set, frontend
     changes also do not trigger a new deploy.
   - Build Command: `pip install -r backend/requirements.txt`
   - Start Command: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Instance Type: Free
3. **Environment variables** (Render > Environment):

   | Name | Value |
   | --- | --- |
   | `DATABASE_URL` | the Postgres connection string |
   | `APP_USERNAME` | a username you choose |
   | `APP_PASSWORD` | a long password you choose |
   | `PYTHON_VERSION` | `3.12.8` |

   With `APP_USERNAME` and `APP_PASSWORD` set, the browser asks for them before showing anything.
   Without them, anyone who knows the link can see and change your payments.
4. Deploy. The free plan sleeps after 15 minutes without visits, so the first load after a break
   takes about a minute.

## Stored Excel files stay up to date

Every imported file is kept under **Stored files**. For calendar-style Excel files:

- A payment you add by hand is written into the stored Excel of its month, under its day. A new day
  gets its own column, a new week gets a new block at the bottom, and when a day has no free row a
  row is inserted above the SUM row and the SUM formulas are extended.
- Paid, Delayed and Cancelled colour the payment's two cells green, yellow and red. Undo removes the colour.
- A month with no stored Excel gets a new one, `Payments 2026-12.xlsx`.
- Removing a payment (Remove button) also takes it out of the stored Excel files: its cells are cleared,
  the items under it move up, an emptied extra row is deleted and the SUM formulas shrink back.
- Delete all payments does not change the stored files. Delete a stored file with its own Delete file button.
- CSV and PDF files are kept as they were imported. They cannot hold colours.

## Running the frontend separately

Open `frontend/` with VS Code Live Server and change the first line of `frontend/script.js` to:

```js
const API = "http://127.0.0.1:8000/api";
```

CORS is already enabled in the backend.

## API

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/payments?month=2026-11` | List payments, optionally for one month |
| POST | `/api/payments` | Add one payment `{name, amount, date}`. Also writes it into the stored Excel file of its month |
| PATCH | `/api/payments/{id}/status` | Set `pending`, `paid`, `delayed` or `cancelled`. Also colours the payment in the stored Excel file |
| DELETE | `/api/payments/{id}` | Remove a payment. Also removes it from the stored Excel files |
| POST | `/api/parse-file` | Upload .csv / .txt / .xlsx / .pdf (form field `file`), returns the rows found as text. Saves nothing |
| POST | `/api/payments/import` | Import text `{text, replace}` |
| GET | `/api/months` | Months that have payments |
| GET | `/api/summary?month=2026-11` | Totals per status |
| POST | `/api/files` | Keep a copy of an imported CSV / Excel / PDF (same file is stored once) |
| GET | `/api/files` | List stored files |
| GET / DELETE | `/api/files/{id}` | Download / delete a stored file |
| POST / GET / DELETE | `/api/payments/{id}/image` | Upload / view / remove the image of a payment (JPG, PNG, WEBP, GIF, max 3 MB) |