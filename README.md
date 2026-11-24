# Payment Tracker

Monthly payment tracker. Import `name, amount, date` from a **CSV, TSV, text, Excel (.xlsx, .xlsm, .xls), OpenDocument (.ods), Word (.docx) or PDF** file
(or paste the lines), then mark each payment:

- **Green** (Paid): paid on its date.
- **Yellow** (Delayed): moved to the next day. The list shows "Pay on <date>".
- **Red** (Cancelled).
- A payment's status is **Paid** (green) or **Cancel** (red), one or the other, and **Delayed** (yellow) can be switched on
  together with either of them, or alone. Click a button again to switch it off. Delayed moves the payment to the next day
  and the row shows `Delayed to <date>`. Paid + Delayed shows the row as Paid with a Delayed tag.
- On the dashboard, Paid and Cancelled count the payments with that status, Delayed counts every delayed payment (also one that is
  Paid or Cancelled as well), and Pending counts the rest. A payment that is both Paid and Delayed is therefore in the Paid card and in
  the Delayed card. The pie chart, the bar and the month rows put each payment in one place (Paid or Cancelled first, then Delayed), and
  Still to pay is every payment that is not Paid and not Cancelled.
- Next to the status, any of three amounts can be added by clicking its button and typing the amount in a small window
  (the app fills it in from the description when it finds a number next to a word such as `balance`, `to come`, `extra` or `paid`):
  - **Awaiting income** (purple): income that still has to come in.
  - **Awaiting income received** (pink): the part of it that has come in.
  - **We paid** (teal): an amount we paid.
  - **Excess paid** (tan): what was paid on top of the amount, for example 2,000 to pay but 3,000 paid: type 1,000.
  - **Part paid pending** (orange): the pending part of a part payment.
  They can be used together with the status and with each other. Each shows as a tag on the row. Click the button again to change or remove the amount.
- **Edit** (next to Remove) changes a payment's name, amount or date. The stored Excel files follow: the payment is taken out of
  its old place and put in the new one, also when the new date is in another week or month. A delayed payment moves along with its date.
  The description box is a larger box of several lines; Enter saves it and Shift+Enter starts a new line.
- The typed-in amounts are never added to the dashboard's Total, Paid, Pending, Delayed or Cancelled numbers. Each one has its own card.
- A payment that has an **Awaiting income** amount is left out of **Total amount** and **Still to pay** (dashboard and PDF report). It still shows in its own status card, the pie chart, the month rows and the list.

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

- **CSV / TSV / TXT**: `name, amount, date` per line. Commas, tabs, semicolons and pipes all work as the separator,
  and UTF-8, UTF-16 (Excel's Unicode Text) and Windows-1252 files are read.
- **Excel (.xlsx)**: every sheet is read. Column order does not matter, the app finds the date,
  amount and name in each row. `.xlsm` (macro-enabled) is read the same way, the macros are ignored.
- **Old Excel (.xls)**: read with the `xlrd` package (it is in `backend/requirements.txt`). An .xls holds values only,
  so in a calendar sheet the SUM row is recognised as a row of numbers without names.
- **OpenDocument (.ods)**, from LibreOffice or a Google Sheets download: tables and calendar sheets, like .xlsx.
- **Word (.docx)**: tables are read first, otherwise every line of text (like a PDF).
- **PDF**: three kinds are read. A **calendar sheet** (month title, weekday names, day numbers, then name and amount under each
  day, with or without a description column), also when the PDF has no ruled lines (the position of the words tells which day
  each belongs to) and when a wide sheet was cut over several pages. Otherwise a **table** (name, amount, date), otherwise
  **text lines** such as `Rent 12000 01-11-2026`. Always check the rows after importing a PDF, because PDF layouts vary. Scanned
  PDFs (pictures of text) are not supported. A wide sheet that the PDF cut in the middle of a day (the name on one page, the amount
  on the next) cannot be read: save the PDF again with all columns on one page, or import the Excel file.

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
- Paid, Delayed and Cancelled colour the payment's name, amount and description cells green, yellow and red (Paid or Cancelled wins over Delayed). A pending payment that has an amount in Awaiting income, We paid or Part paid pending is coloured purple, pink, teal, tan or orange (the first one it has). Undo removes the colour.
- A month with no stored Excel gets a new one, `Payments 2026-12.xlsx`.
- Every payment has a Description box next to the image buttons. It is required: the image cannot be
  uploaded until the description is written. Whatever is left in the box is saved (also when you leave the box
  or press Enter); erasing it clears the saved description and the Excel cell, unless the payment has an image. The description is
  written into the stored Excel files too: in a calendar sheet every day has three
  columns (name, amount, description) and the description goes in the third one, next to the amount; in a
  table sheet it goes in a Description column (made if missing). A calendar sheet that still has two columns
  per day gets the third column added to every day the first time the app updates it (styles, merged
  headers and SUM formulas move along).
- Import can carry descriptions too: a fourth item on a pasted line (`Rent, 12000, 2026-11-01, Monthly rent`),
  a Description / Comment / Remarks column in a CSV or Excel table, or the third column of each day in a
  calendar sheet (an old note on the name cell is read too). It is optional on import. A payment that is already in the list gets the description only when it
  has none yet; an existing description is never overwritten.
- The month title of a calendar sheet can be written in any of these ways: `November 2026`, `Nov 2026`,
  `Nov-26`, `Nov'26`, `11/2026`, `2026-11`, a real date cell (Excel turns a typed `Nov 2026` into 1 Nov 2026),
  or just `Nov` (the current year is assumed; check the dates in the preview before importing).
- A payment can have several images **or PDF files** (up to 10 in all; images max 3 MB, PDFs max 5 MB). A PDF shows as a small red `PDF` badge and opens in the viewer's PDF window (or in a new tab). Choose one or many files in the file box, and
  each shows as a small thumbnail with an x button that removes just that image. The description is still
  required before the first image is added. Click a thumbnail to open the viewer: the big image, previous and
  next buttons (or the left and right arrow keys, or a swipe on a phone), a strip of all the payment's
  images, Esc or Close to leave. Ctrl-click a thumbnail to open that image in a new tab.
- The dashboard has a pie chart with a legend (amount, share and number of payments per slice). The switch above it shows
  either the status split (Paid, Pending, Delayed, Cancelled: each payment in one slice) or the three typed-in amounts
  (Awaiting income, Awaiting income received, We paid, Part paid pending). It follows the filters like the rest of the dashboard.
  **Download PNG** saves the pie chart, its legend, what it covers and the date as a picture.
- **Category**: every payment has a Category box (free text such as Fees, Rent, Fruits; the boxes suggest the categories already in
  use, and capitals do not matter). It is saved when you leave the box. The Category filter, the Category pie chart and the
  Category-wise table in the report use it. Categories are not written into the stored Excel sheets.
- **Month-wise trend**: a chart of the total of every month (latest 12), as stacked bars by status or as lines (the Total is a dashed
  line). Click a month to show only that month. It follows the search, status and category filters, not the date filters.
- **Download report (PDF)** (top right of the dashboard) makes a PDF for the filters that are set: totals, status table, typed-in amounts,
  pie chart, month-wise numbers with bars, category-wise table and the list of payments with their details. It needs `reportlab` and
  the fonts in `backend/fonts/` (DejaVu Sans, which has the rupee sign). Without the font files it still works, with `Rs.` and Helvetica.
  Characters the font does not have (for example Tamil letters) are shown as boxes in the PDF.
- Removing a payment (Remove button) also takes it out of the stored Excel files: its cells are cleared,
  the items under it move up, an emptied extra row is deleted and the SUM formulas shrink back.
- Delete all payments does not change the stored files. Delete a stored file with its own Delete file button.
- Only .xlsx files are updated by the app. Files of other types (CSV, .xls, .xlsm, .ods, Word, PDF) are kept as
  they were imported: the app cannot write into them.

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
| PATCH | `/api/payments/{id}/status` | Send `{"status": "pending" \| "paid" \| "cancelled"}` and/or `{"delayed": true \| false}`. Also colours the payment in the stored Excel file |
| PUT | `/api/payments/{id}` | Edit `name`, `amount` and `date` (409 when another payment has the same three). Updates the stored Excel files |
| PUT | `/api/payments/{id}/category` | Set (`{"category": "Fees"}`) or clear (`{"category": null}`) the category |
| GET | `/api/report` | PDF report. Optional query: `q`, `status`, `category`, `year`, `month`, `day` (the same filters as the page) |
| PUT | `/api/payments/{id}/extra/{kind}` | Set (`{"amount": 13000}`) or clear (`{"amount": null}`) `awaiting`, `received`, `we_paid`, `excess` or `part_pending`. Not part of any total |
| PUT | `/api/payments/{id}/description` | Save the required description (also written into the stored Excel files) |
| DELETE | `/api/payments/{id}` | Remove a payment. Also removes it from the stored Excel files |
| POST | `/api/parse-file` | Upload .csv / .tsv / .txt / .xlsx / .xlsm / .xls / .ods / .docx / .pdf (form field `file`), returns the rows found as text. Saves nothing |
| POST | `/api/payments/import` | Import text `{text, replace}` |
| GET | `/api/months` | Months that have payments |
| GET | `/api/summary?month=2026-11` | Totals per status |
| POST | `/api/files` | Keep a copy of an imported file of any supported type (same file is stored once) |
| GET | `/api/files` | List stored files |
| GET / DELETE | `/api/files/{id}` | Download / delete a stored file |
| POST | `/api/payments/{id}/image` | Add one more image to a payment (JPG, PNG, WEBP, GIF, max 3 MB, up to 10 per payment) |
| GET / DELETE | `/api/images/{image_id}` | View / remove one image |
| GET / DELETE | `/api/payments/{id}/image` | View the first image / remove all images of a payment |