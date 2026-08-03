# Employee Attendance Extractor

Converts an ESSL biometric machine's **Monthly Status Report (Basic Report)**
PDF into a clean `attendance_summary.xlsx`. Works for any month — 28, 29, 30,
or 31 days, any employee count — with **zero code changes**.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Then, in the browser tab that opens:
1. Upload the Monthly Status Report (Basic Report) PDF in the sidebar.
2. Click **Process Report**.
3. Review the metadata, processing stats, and preview table.
4. Click **Download Excel** to get `attendance_summary.xlsx`.

## How it works

| Step | Module | Notes |
|---|---|---|
| Read PDF | `parser.py` | `pdfplumber` text extraction, page by page |
| Metadata | `parser.py` / `utils.py` | Month, year, days-in-period, company, department |
| Employee rows | `utils.split_employee_row` | Tokenizes each line from **both ends inward** (Sl + Code from the front, the 7 summary numbers from the back), so it never relies on fixed column positions |
| Attendance | `parser._parse_row` | **Primary**: machine's `P` summary column. **Fallback**: recomputed from daily status cells only if the summary is missing/unparsable |
| Absent Days | `parser._parse_row` | Always derived from daily `A` cells, regardless of primary/fallback |
| Validation | `validator.py` | Missing fields, out-of-range attendance, duplicate Employee IDs (keeps the most complete record) |
| Excel | `excel_writer.py` | Bold header, frozen top row, autofilter, autofit columns, aligned cells |

## Output

`attendance_summary.xlsx`, sheet **Attendance Summary**:

| Employee ID | Employee Name | Days Present | Absent Count | Absent Days |
|---|---|---:|---:|---|

## Status code reference

| Code | Meaning | Fallback weight |
|---|---|---:|
| P | Present | 1.0 |
| ½P | Half day present | 0.5 |
| A | Absent | 0.0 |
| WO | Week off | 0.0 |
| WOP | Worked on week off (paid) | 0.0 |
| L | Leave | 0.0 |
| HP | Half-day paid | 0.0 |

The fallback weights only apply when the machine-generated `P` summary
can't be read for a row — the machine total is always trusted over the
recomputed one when both are available. Small differences between the two
(e.g. a `WOP` day credited by the machine but weighted 0 in the fallback
map) are logged as verification warnings but do **not** change the output;
the report's own `P` count is the source of truth.

## Data handling

Nothing is written to disk on the server. The uploaded PDF is read straight
into memory, parsed, and the resulting workbook is built into an in-memory
buffer that's only ever handed to Streamlit's `st.download_button` — the
person downloads it themselves, the app doesn't save a copy anywhere. The
`sample_reports/` folder is a placeholder for you to drop your own local
test files into; it's git-ignored so real attendance data never ends up in
version control.

## Logs

Every run appends to `logs/app.log`: timestamps, file name, processing
time, parse counts (primary/fallback/failed/duplicate), and any warnings.

## Project layout

```
attendance_extractor/
├── app.py            # Streamlit UI
├── parser.py          # PDF → structured records
├── validator.py        # Cleanup, dedup, bounds checking
├── excel_writer.py     # Formatted .xlsx output
├── utils.py            # Regex/tokenizing/metadata helpers
├── logger.py            # loguru setup
├── config.py            # Column layout, status codes, paths
├── requirements.txt
├── logs/
└── sample_reports/
```

## Known limitations / next steps

- Only the **Basic Report** PDF is supported as input (per spec). A separate
  "Basic Work Duration" export (with in/out punch times but no P/A/L/H/HP/WO/WOP
  totals) is a different report and isn't parsed by this tool.
- Multiple-PDF upload, CSV export, and department-wise summaries are noted
  as future enhancements in the original brief and aren't built yet.
