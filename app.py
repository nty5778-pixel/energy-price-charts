from __future__ import annotations

import csv
import datetime as dt
import io
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response


APP_TITLE = "LAI Gas Chart API"
CENTRAL_TZ = ZoneInfo("America/Chicago")
DEFAULT_SHEET_ID = "1g-yuKuUhSd3nU7eDiLWFgxOcbuFkBWmWH0wZvGg6B9I"
DEFAULT_SHEET_GID = "0"


app = FastAPI(title=APP_TITLE)


@dataclass
class GasRow:
    price_date: dt.date
    nymex_strip_date: dt.date | None
    nymex_price: float | None
    katy_price: float | None
    hsc_monthly_price: float | None
    updated_at: str


def sheet_csv_url() -> str:
    explicit_url = os.getenv("GOOGLE_SHEET_CSV_URL", "").strip()
    if explicit_url:
        return explicit_url

    sheet_id = os.getenv("GOOGLE_SHEET_ID", DEFAULT_SHEET_ID).strip()
    sheet_gid = os.getenv("GOOGLE_SHEET_GID", DEFAULT_SHEET_GID).strip()
    query = urllib.parse.urlencode({"format": "csv", "gid": sheet_gid})
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?{query}"


def fetch_sheet_rows() -> list[GasRow]:
    request = urllib.request.Request(
        sheet_csv_url(),
        headers={"User-Agent": "LAI-Gas-Chart-API/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8-sig")
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not read Google Sheet CSV. Check sharing/publish access. {exc}",
        ) from exc

    reader = csv.DictReader(io.StringIO(raw))
    rows: list[GasRow] = []
    for source in reader:
        price_date = parse_date(source.get("PriceDate"))
        if price_date is None:
            continue

        rows.append(
            GasRow(
                price_date=price_date,
                nymex_strip_date=parse_date(
                    pick(source, "NYMEX_StripDate_Final", "NYMEX_StripDate")
                ),
                nymex_price=parse_float(pick(source, "NYMEX_Price_Final", "NYMEX_Price")),
                katy_price=parse_float(
                    pick(source, "RegionalPrice_Katy_Final", "RegionalPrice_Katy")
                ),
                hsc_monthly_price=parse_float(
                    pick(
                        source,
                        "RegionalPrice_HoustonShipChl_Monthly_Final",
                        "RegionalPrice_HoustonShipChl_Monthly",
                    )
                ),
                updated_at=source.get("UpdatedAt", "") or "",
            )
        )

    return sorted(rows, key=lambda row: row.price_date)


def pick(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return ""


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def current_month() -> str:
    today = dt.datetime.now(CENTRAL_TZ).date()
    return f"{today.year:04d}-{today.month:02d}"


def choose_month(rows: list[GasRow], requested_month: str | None) -> str:
    if requested_month and requested_month.lower() != "current":
        return requested_month[:7]

    month = current_month()
    if any(row.price_date.strftime("%Y-%m") == month for row in rows):
        return month

    months = sorted({row.price_date.strftime("%Y-%m") for row in rows})
    if not months:
        raise HTTPException(status_code=404, detail="No dated rows were found in the sheet.")
    return months[-1]


def month_bounds(month: str) -> tuple[dt.date, dt.date]:
    year, month_number = map(int, month.split("-"))
    start = dt.date(year, month_number, 1)
    if month_number == 12:
        end = dt.date(year + 1, 1, 1)
    else:
        end = dt.date(year, month_number + 1, 1)
    return start, end


def calendar_rows(rows: list[GasRow], month: str) -> list[GasRow]:
    start, end = month_bounds(month)
    by_date = {row.price_date: row for row in rows if start <= row.price_date < end}
    result: list[GasRow] = []
    cursor = start
    while cursor < end:
        result.append(
            by_date.get(
                cursor,
                GasRow(
                    price_date=cursor,
                    nymex_strip_date=None,
                    nymex_price=None,
                    katy_price=None,
                    hsc_monthly_price=None,
                    updated_at="",
                ),
            )
        )
        cursor += dt.timedelta(days=1)
    return result


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> dt.date:
    date = dt.date(year, month, 1)
    days = (weekday - date.weekday()) % 7
    return date + dt.timedelta(days=days + 7 * (nth - 1))


def last_weekday(year: int, month: int, weekday: int) -> dt.date:
    if month == 12:
        date = dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    else:
        date = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return date - dt.timedelta(days=(date.weekday() - weekday) % 7)


def observed_fixed_holiday(year: int, month: int, day: int) -> dt.date:
    holiday = dt.date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - dt.timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + dt.timedelta(days=1)
    return holiday


def us_market_holidays(year: int) -> set[dt.date]:
    return {
        observed_fixed_holiday(year, 1, 1),
        nth_weekday(year, 1, 0, 3),
        nth_weekday(year, 2, 0, 3),
        last_weekday(year, 5, 0),
        observed_fixed_holiday(year, 6, 19),
        observed_fixed_holiday(year, 7, 4),
        nth_weekday(year, 9, 0, 1),
        nth_weekday(year, 10, 0, 2),
        observed_fixed_holiday(year, 11, 11),
        nth_weekday(year, 11, 3, 4),
        observed_fixed_holiday(year, 12, 25),
    }


def is_business_day(date: dt.date, holidays: set[dt.date]) -> bool:
    return date.weekday() < 5 and date not in holidays


def lds_row(rows: list[GasRow]) -> GasRow | None:
    if not rows:
        return None
    holidays = us_market_holidays(rows[0].price_date.year)
    business_rows = [row for row in rows if is_business_day(row.price_date, holidays)]
    if len(business_rows) < 3:
        return None
    return business_rows[-3]


def latest_value(rows: list[GasRow], attr: str) -> tuple[GasRow, float] | None:
    for row in reversed(rows):
        value = getattr(row, attr)
        if value is not None:
            return row, value
    return None


def clean_values(values: list[float | None]) -> list[float]:
    return [value for value in values if value is not None]


def fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def render_chart(rows: list[GasRow], month: str) -> bytes:
    if not rows:
        raise HTTPException(status_code=404, detail=f"No rows found for {month}.")

    dates = [row.price_date for row in rows]
    nymex = [row.nymex_price for row in rows]
    katy = [row.katy_price for row in rows]
    hsc = [row.hsc_monthly_price for row in rows]

    latest_nymex = latest_value(rows, "nymex_price")
    latest_katy = latest_value(rows, "katy_price")
    lds = lds_row(rows)
    holidays = us_market_holidays(dates[0].year)

    month_label = dates[0].strftime("%B %Y")
    strip_dates = sorted(
        {
            row.nymex_strip_date.isoformat()
            for row in rows
            if row.nymex_strip_date and row.nymex_price is not None
        }
    )
    strip_label = ", ".join(strip_dates) or "N/A"
    latest_parts = []
    if latest_nymex:
        latest_parts.append(f"NYMEX through {latest_nymex[0].price_date:%b %d}")
    if latest_katy:
        latest_parts.append(f"Katy through {latest_katy[0].price_date:%b %d}")

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=160)

    for date in dates:
        if date.weekday() >= 5 or date in holidays:
            ax.axvspan(
                dt.datetime.combine(date, dt.time.min),
                dt.datetime.combine(date + dt.timedelta(days=1), dt.time.min),
                color="#d1d5db",
                alpha=0.55,
                linewidth=0,
                zorder=0,
            )

    ax.plot(
        dates,
        nymex,
        color="#1f6f8b",
        linewidth=2.4,
        marker="o",
        markersize=5.2,
        label="GM_NYMEX_new Price (front strip)",
    )
    ax.plot(
        dates,
        katy,
        color="#c75000",
        linewidth=2.4,
        marker="o",
        markersize=5.2,
        label="GM_RegionalPrice RegionalPrice_Katy",
    )
    ax.plot(
        dates,
        hsc,
        color="#5b5f97",
        linewidth=2.3,
        linestyle="--",
        label="GM_RegionalPriceMonthly RegionalPrice_HoustonShipChl",
    )

    if lds and lds.nymex_price is not None:
        ax.scatter(
            [lds.price_date],
            [lds.nymex_price],
            s=150,
            facecolor="#ffd166",
            edgecolor="#7c2d12",
            linewidth=1.8,
            zorder=5,
            label="NYMEX LDS (last business day D-2)",
        )
        ax.annotate(
            f"LDS {lds.price_date:%b %d}\n{lds.nymex_price:.3f}",
            xy=(lds.price_date, lds.nymex_price),
            xytext=(10, 18),
            textcoords="offset points",
            fontsize=9.2,
            fontweight="bold",
            color="#7c2d12",
            arrowprops={"arrowstyle": "->", "color": "#7c2d12", "linewidth": 1.2},
            bbox={
                "boxstyle": "round,pad=0.25",
                "facecolor": "#fff7ed",
                "edgecolor": "#fed7aa",
                "alpha": 0.95,
            },
        )

    ax.set_title(
        f"LAI Database Gas Price Trend - {month_label}",
        fontsize=17,
        fontweight="bold",
        pad=18,
    )
    ax.text(
        0,
        1.015,
        f"NYMEX front strip: {strip_label}. {'; '.join(latest_parts)}. Blank dates indicate no value returned from DB.",
        transform=ax.transAxes,
        fontsize=9.5,
        color="#4b5563",
    )
    ax.set_ylabel("Price")
    ax.set_xlabel("Price date")
    ax.legend(loc="upper left", frameon=True)

    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.set_xlim(dates[0] - dt.timedelta(days=0.6), dates[-1] + dt.timedelta(days=0.6))

    all_values = clean_values(nymex + katy + hsc)
    if all_values:
        value_min = min(all_values)
        value_max = max(all_values)
        padding = max((value_max - value_min) * 0.2, 0.1)
        ax.set_ylim(value_min - padding, value_max + padding)

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    ax.grid(True, which="major", axis="both", color="#d7dde5", linewidth=0.8)
    fig.autofmt_xdate(rotation=35, ha="right")
    fig.tight_layout()

    output = io.BytesIO()
    fig.savefig(output, format="png", bbox_inches="tight")
    plt.close(fig)
    return output.getvalue()


@app.get("/")
def root() -> dict[str, str]:
    return {"service": APP_TITLE, "chart": "/chart.png?month=current", "health": "/health"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/chart-info")
def chart_info(month: str | None = Query(default="current")) -> JSONResponse:
    rows = fetch_sheet_rows()
    selected_month = choose_month(rows, month)
    month_rows = calendar_rows(rows, selected_month)
    lds = lds_row(month_rows)
    latest_nymex = latest_value(month_rows, "nymex_price")
    latest_katy = latest_value(month_rows, "katy_price")
    return JSONResponse(
        {
            "month": selected_month,
            "row_count": len(month_rows),
            "latest_nymex_date": latest_nymex[0].price_date.isoformat() if latest_nymex else None,
            "latest_nymex_price": latest_nymex[1] if latest_nymex else None,
            "latest_katy_date": latest_katy[0].price_date.isoformat() if latest_katy else None,
            "latest_katy_price": latest_katy[1] if latest_katy else None,
            "lds_date": lds.price_date.isoformat() if lds else None,
            "lds_price": lds.nymex_price if lds else None,
        }
    )


@app.get("/chart.png")
def chart_png(month: str | None = Query(default="current")) -> Response:
    rows = fetch_sheet_rows()
    selected_month = choose_month(rows, month)
    month_rows = calendar_rows(rows, selected_month)
    png = render_chart(month_rows, selected_month)
    filename = f"lai-gas-price-trend-{selected_month}.png"
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
