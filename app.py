from __future__ import annotations

import csv
import datetime as dt
import io
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageDraw, ImageFont


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
        price_date = parse_date(pick(source, "PriceDate", "Price Date", "Date", "date"))
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


def fetch_sheet_preview() -> dict[str, object]:
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
    sample_rows = []
    for index, row in enumerate(reader):
        if index >= 5:
            break
        sample_rows.append(row)

    return {
        "csv_url": sheet_csv_url(),
        "headers": reader.fieldnames or [],
        "sample_rows": sample_rows,
    }


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


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
    anchor: str | None = None,
) -> None:
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    fill: str,
    width: int,
    dash: int = 14,
    gap: int = 10,
) -> None:
    x1, y1 = start
    x2, y2 = end
    length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    if length == 0:
        return
    dx = (x2 - x1) / length
    dy = (y2 - y1) / length
    distance = 0.0
    while distance < length:
        segment_end = min(distance + dash, length)
        sx = x1 + dx * distance
        sy = y1 + dy * distance
        ex = x1 + dx * segment_end
        ey = y1 + dy * segment_end
        draw.line((sx, sy, ex, ey), fill=fill, width=width)
        distance += dash + gap


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

    width, height = 1920, 1080
    margin_left, margin_right = 120, 50
    margin_top, margin_bottom = 150, 125
    plot_left = margin_left
    plot_right = width - margin_right
    plot_top = margin_top
    plot_bottom = height - margin_bottom
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top

    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)

    font_title = load_font(42, bold=True)
    font_body = load_font(24)
    font_small = load_font(20)
    font_axis = load_font(22)
    font_legend = load_font(24)
    font_label_bold = load_font(22, bold=True)

    all_values = clean_values(nymex + katy + hsc)
    if not all_values:
        value_min, value_max = 0.0, 1.0
    else:
        value_min = min(all_values)
        value_max = max(all_values)
        if value_min == value_max:
            value_min -= 0.5
            value_max += 0.5
        padding = max((value_max - value_min) * 0.2, 0.1)
        value_min -= padding
        value_max += padding

    def x_for(index: int) -> float:
        if len(dates) <= 1:
            return plot_left
        return plot_left + index * plot_width / (len(dates) - 1)

    def y_for(value: float) -> float:
        return plot_bottom - ((value - value_min) / (value_max - value_min)) * plot_height

    # Weekend and holiday shading.
    for index, date in enumerate(dates):
        if date.weekday() >= 5 or date in holidays:
            day_width = plot_width / max(len(dates) - 1, 1)
            left = int(x_for(index) - day_width / 2)
            right = int(x_for(index) + day_width / 2)
            draw.rectangle(
                (max(plot_left, left), plot_top, min(plot_right, right), plot_bottom),
                fill="#e5e7eb",
            )

    # Grid and frame.
    for step in range(6):
        y = plot_bottom - step * plot_height / 5
        value = value_min + step * (value_max - value_min) / 5
        draw.line((plot_left, y, plot_right, y), fill="#d7dde5", width=1)
        draw_text(draw, (plot_left - 18, int(y)), f"{value:.2f}", font_axis, "#1f2937", anchor="rm")

    tick_positions = list(range(0, len(dates), 2))
    if (len(dates) - 1) not in tick_positions:
        tick_positions.append(len(dates) - 1)
    for index in tick_positions:
        x = x_for(index)
        draw.line((x, plot_top, x, plot_bottom), fill="#e1e7ef", width=1)
        draw_text(
            draw,
            (int(x), plot_bottom + 28),
            dates[index].strftime("%b %d"),
            font_axis,
            "#1f2937",
            anchor="mm",
        )

    draw.line((plot_left, plot_bottom, plot_right, plot_bottom), fill="#c7ccd4", width=2)
    draw.line((plot_left, plot_top, plot_left, plot_bottom), fill="#c7ccd4", width=2)

    def draw_series(values: list[float | None], color: str, dashed: bool = False, markers: bool = True) -> None:
        prev: tuple[float, float] | None = None
        for index, value in enumerate(values):
            if value is None:
                prev = None
                continue
            point = (x_for(index), y_for(value))
            if prev is not None:
                if dashed:
                    draw_dashed_line(draw, prev, point, color, 5)
                else:
                    draw.line((*prev, *point), fill=color, width=6)
            if markers:
                x, y = point
                draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline=color)
            prev = point

    draw_series(nymex, "#1f6f8b")
    draw_series(katy, "#c75000")
    draw_series(hsc, "#5b5f97", dashed=True, markers=False)

    if lds and lds.nymex_price is not None:
        lds_index = dates.index(lds.price_date)
        x = x_for(lds_index)
        y = y_for(lds.nymex_price)
        draw.ellipse((x - 14, y - 14, x + 14, y + 14), fill="#ffd166", outline="#7c2d12", width=5)
        draw_text(
            draw,
            (int(min(x + 18, plot_right - 130)), int(max(y - 46, plot_top + 8))),
            f"LDS {lds.price_date:%b %d}\n{lds.nymex_price:.3f}",
            font_label_bold,
            "#7c2d12",
        )

    # Titles and labels.
    draw_text(draw, (width // 2, 42), f"LAI Database Gas Price Trend - {month_label}", font_title, "#24272d", anchor="ma")
    subtitle = f"NYMEX front strip: {strip_label}. {'; '.join(latest_parts)}. Blank dates indicate no value returned from DB."
    draw_text(draw, (plot_left, 96), subtitle, font_body, "#4b5563")
    draw_text(draw, (plot_left - 72, (plot_top + plot_bottom) // 2), "Price", font_body, "#24272d", anchor="mm")
    draw_text(draw, ((plot_left + plot_right) // 2, height - 30), "Price date", font_body, "#24272d", anchor="mm")

    # Legend.
    legend_x, legend_y = plot_left + 12, plot_top + 18
    legend_items = [
        ("#1f6f8b", "GM_NYMEX_new Price (front strip)", False),
        ("#c75000", "GM_RegionalPrice RegionalPrice_Katy", False),
        ("#5b5f97", "GM_RegionalPriceMonthly RegionalPrice_HoustonShipChl", True),
    ]
    if lds and lds.nymex_price is not None:
        legend_items.append(("#ffd166", "NYMEX LDS (last business day D-2)", False))
    box_w, box_h = 610, 38 + len(legend_items) * 36
    draw.rounded_rectangle((legend_x, legend_y, legend_x + box_w, legend_y + box_h), radius=8, fill="#ffffff", outline="#d4d0c8", width=2)
    y_cursor = legend_y + 24
    for color, label, dashed in legend_items:
        if label.startswith("NYMEX LDS"):
            draw.ellipse((legend_x + 20, y_cursor - 10, legend_x + 40, y_cursor + 10), fill=color, outline="#7c2d12", width=3)
        elif dashed:
            draw_dashed_line(draw, (legend_x + 18, y_cursor), (legend_x + 54, y_cursor), color, 5)
        else:
            draw.line((legend_x + 18, y_cursor, legend_x + 54, y_cursor), fill=color, width=6)
            draw.ellipse((legend_x + 32, y_cursor - 6, legend_x + 44, y_cursor + 6), fill=color)
        draw_text(draw, (legend_x + 72, y_cursor - 13), label, font_legend, "#24272d")
        y_cursor += 36

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@app.get("/")
def root() -> dict[str, str]:
    return {"service": APP_TITLE, "chart": "/chart.png?month=current", "health": "/health"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/debug-sheet")
def debug_sheet() -> JSONResponse:
    return JSONResponse(fetch_sheet_preview())


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


@app.get("/chart-check")
def chart_check(month: str | None = Query(default="current")) -> JSONResponse:
    rows = fetch_sheet_rows()
    selected_month = choose_month(rows, month)
    month_rows = calendar_rows(rows, selected_month)
    nymex_count = sum(1 for row in month_rows if row.nymex_price is not None)
    katy_count = sum(1 for row in month_rows if row.katy_price is not None)
    hsc_count = sum(1 for row in month_rows if row.hsc_monthly_price is not None)
    try:
        render_chart(month_rows, selected_month)
        render_status = "ok"
        render_error = None
    except Exception as exc:
        render_status = "error"
        render_error = f"{type(exc).__name__}: {exc}"

    return JSONResponse(
        {
            "month": selected_month,
            "sheet_row_count": len(rows),
            "calendar_row_count": len(month_rows),
            "nymex_count": nymex_count,
            "katy_count": katy_count,
            "hsc_count": hsc_count,
            "render_status": render_status,
            "render_error": render_error,
        }
    )


@app.get("/chart.png")
def chart_png(month: str | None = Query(default="current")) -> Response:
    rows = fetch_sheet_rows()
    selected_month = choose_month(rows, month)
    month_rows = calendar_rows(rows, selected_month)
    try:
        png = render_chart(month_rows, selected_month)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Chart rendering failed: {type(exc).__name__}: {exc}",
        ) from exc
    filename = f"lai-gas-price-trend-{selected_month}.png"
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
