# LAI Gas Chart API for Render

This small FastAPI app reads the Google Sheet CSV and returns the LAI gas price trend chart as PNG.

## Files

- `app.py` - FastAPI app and chart rendering logic
- `requirements.txt` - Python dependencies
- `runtime.txt` - Python runtime pin for Render
- `render.yaml` - Render blueprint example

## Render Settings

Create a new Render Web Service.

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Environment variables:

```text
GOOGLE_SHEET_ID=1g-yuKuUhSd3nU7eDiLWFgxOcbuFkBWmWH0wZvGg6B9I
GOOGLE_SHEET_GID=0
```

If the sheet tab gid changes, update `GOOGLE_SHEET_GID`.

If you prefer to use a published CSV URL directly:

```text
GOOGLE_SHEET_CSV_URL=https://docs.google.com/spreadsheets/d/.../export?format=csv&gid=...
```

`GOOGLE_SHEET_CSV_URL` overrides `GOOGLE_SHEET_ID` and `GOOGLE_SHEET_GID`.

## Endpoints

Health:

```text
GET /health
```

Chart PNG for current month:

```text
GET /chart.png?month=current
```

Chart PNG for a specific month:

```text
GET /chart.png?month=2026-07
```

Chart metadata:

```text
GET /chart-info?month=current
```

## n8n Flow

Use this workflow:

```text
Schedule Trigger
→ HTTP Request
→ Gmail
```

HTTP Request node:

```text
Method: GET
URL: https://YOUR-RENDER-SERVICE.onrender.com/chart.png?month=current
Response Format: File
Binary Property: data
```

Gmail node:

```text
Operation: Send
Attachments: data
```

Suggested subject:

```text
LAI Gas Price Trend - Current Month
```
