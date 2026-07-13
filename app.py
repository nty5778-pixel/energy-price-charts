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
APP_VERSION = "2026-07-12-split-panels-v2"
CENTRAL_TZ = ZoneInfo("America/Chicago")
DEFAULT_SHEET_ID = "1g-yuKuUhSd3nU7eDiLWFgxOcbuFkBWmWH0wZvGg6B9I"
DEFAULT_SHEET_GID = "0"
DEFAULT_POWER_SHEET_NAME = "Power"


app = FastAPI(title=APP_TITLE)


@dataclass
class GasRow:
    price_date: dt.date
    nymex_strip_date: dt.date | None
    nymex_price: float | None
    katy_price: float | None
    hsc_monthly_price: float | None
    updated_at: str


@dataclass
class PowerHourlyRow:
    date_time: str
    date: dt.date
    hour: int | None
    dam: float | None
    rtm: float | None
    demand: float | None
    wind_production: float | None
    solar_production: float | None
    net_load: float | None
    updated_at: str


@dataclass
class PowerDailyRow:
    date: dt.date
    dam_avg: float | None
    rtm_avg: float | None
    sample_count: int


def sheet_csv_url() -> str:
    explicit_url = os.getenv("GOOGLE_SHEET_CSV_URL", "").strip()
    if explicit_url:
        return explicit_url

    sheet_id = os.getenv("GOOGLE_SHEET_ID", DEFAULT_SHEET_ID).strip()
    sheet_gid = os.getenv("GOOGLE_SHEET_GID", DEFAULT_SHEET_GID).strip()
    query = urllib.parse.urlencode({"format": "csv", "gid": sheet_gid})
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?{query}"


def power_sheet_csv_url() -> str:
    explicit_url = os.getenv("GOOGLE_POWER_SHEET_CSV_URL", "").strip()
    if explicit_url:
        return explicit_url

    sheet_id = os.getenv("GOOGLE_SHEET_ID", DEFAULT_SHEET_ID).strip()
    sheet_gid = os.getenv("GOOGLE_POWER_SHEET_GID", "").strip()
    if sheet_gid:
        query = urllib.parse.urlencode({"format": "csv", "gid": sheet_gid})
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?{query}"

    sheet_name = os.getenv("GOOGLE_POWER_SHEET_NAME", DEFAULT_POWER_SHEET_NAME).strip()
    query = urllib.parse.urlencode({"tqx": "out:csv", "sheet": sheet_name})
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?{query}"


def fetch_csv_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "LAI-Gas-Chart-API/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8-sig")
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not read Google Sheet CSV. Check sharing/publish access. {exc}",
        ) from exc


def fetch_sheet_rows() -> list[GasRow]:
    raw = fetch_csv_text(sheet_csv_url())
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
    raw = fetch_csv_text(sheet_csv_url())
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


def fetch_power_rows() -> list[PowerHourlyRow]:
    raw = fetch_csv_text(power_sheet_csv_url())
    reader = csv.DictReader(io.StringIO(raw))
    rows: list[PowerHourlyRow] = []
    for source in reader:
        date = parse_date(pick(source, "Date", "PriceDate", "date"))
        date_time = pick(source, "DateTime", "Datetime", "Date Time", "Dates")
        if date is None and date_time:
            date = parse_date(date_time)
        if date is None:
            continue

        rows.append(
            PowerHourlyRow(
                date_time=date_time,
                date=date,
                hour=parse_int(pick(source, "Hour", "HE", "hour")),
                dam=parse_float(pick(source, "DAM", "DA", "DA_Price")),
                rtm=parse_float(pick(source, "RTM", "RT", "RT_Price")),
                demand=parse_float(pick(source, "Demand")),
                wind_production=parse_float(pick(source, "WindProduction", "Wind")),
                solar_production=parse_float(pick(source, "SolarProduction", "Solar")),
                net_load=parse_float(pick(source, "NetLoad")),
                updated_at=source.get("UpdatedAt", "") or "",
            )
        )

    return sorted(rows, key=lambda row: (row.date, row.hour if row.hour is not None else 99, row.date_time))


def fetch_power_preview() -> dict[str, object]:
    raw = fetch_csv_text(power_sheet_csv_url())
    reader = csv.DictReader(io.StringIO(raw))
    sample_rows = []
    for index, row in enumerate(reader):
        if index >= 5:
            break
        sample_rows.append(row)

    return {
        "csv_url": power_sheet_csv_url(),
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


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
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


def choose_power_month(rows: list[PowerHourlyRow], requested_month: str | None) -> str:
    if requested_month and requested_month.lower() != "current":
        return requested_month[:7]

    month = current_month()
    if any(row.date.strftime("%Y-%m") == month for row in rows):
        return month

    months = sorted({row.date.strftime("%Y-%m") for row in rows})
    if not months:
        raise HTTPException(status_code=404, detail="No dated power rows were found in the sheet.")
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


def power_daily_rows(rows: list[PowerHourlyRow], month: str) -> list[PowerDailyRow]:
    start, end = month_bounds(month)
    grouped: dict[dt.date, list[PowerHourlyRow]] = {}
    for row in rows:
        if start <= row.date < end:
            grouped.setdefault(row.date, []).append(row)

    result: list[PowerDailyRow] = []
    cursor = start
    while cursor < end:
        day_rows = grouped.get(cursor, [])
        dam_values = [row.dam for row in day_rows if row.dam is not None]
        rtm_values = [row.rtm for row in day_rows if row.rtm is not None]
        result.append(
            PowerDailyRow(
                date=cursor,
                dam_avg=average(dam_values),
                rtm_avg=average(rtm_values),
                sample_count=len(day_rows),
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


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


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

    width, height = 1920, 1380
    plot_left, plot_right = 120, width - 50
    panel1_top, panel1_bottom = 245, 665
    panel2_top, panel2_bottom = 880, 1270
    plot_width = plot_right - plot_left
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)

    font_title = load_font(42, bold=True)
    font_body = load_font(24)
    font_small = load_font(20)
    font_axis = load_font(22)
    font_legend = load_font(24)
    font_label_bold = load_font(22, bold=True)

    def x_for(index: int) -> float:
        if len(dates) <= 1:
            return float(plot_left)
        return plot_left + index * plot_width / (len(dates) - 1)

    tick_positions = list(range(0, len(dates), 2))
    if (len(dates) - 1) not in tick_positions:
        tick_positions.append(len(dates) - 1)

    # Titles and labels.
    draw_text(draw, (width // 2, 42), f"LAI Database Gas Price Trend - {month_label}", font_title, "#24272d", anchor="ma")
    subtitle = f"NYMEX front strip: {strip_label}. {'; '.join(latest_parts)}. Blank dates indicate no value returned from DB."
    draw_text(draw, (plot_left, 96), subtitle, font_body, "#4b5563")
    draw_text(draw, (plot_right, 58), APP_VERSION, font_small, "#9b9892", anchor="ra")

    def draw_panel(
        title: str,
        subtitle_text: str,
        top: int,
        bottom: int,
        series: list[dict[str, object]],
        show_x_labels: bool,
        lds_marker: GasRow | None = None,
    ) -> None:
        header_y = top - 70
        legend_x, legend_y = plot_right - 670, header_y - 8
        legend_width = 660
        legend_height = 44
        draw.rounded_rectangle(
            (legend_x, legend_y, legend_x + legend_width, legend_y + legend_height),
            radius=8,
            fill="#ffffff",
            outline="#d4d0c8",
            width=2,
        )
        x_cursor = legend_x + 18
        for item in series:
            label = str(item["label"])
            color = str(item["color"])
            dashed = bool(item.get("dashed", False))
            if dashed:
                draw_dashed_line(draw, (x_cursor, legend_y + 22), (x_cursor + 42, legend_y + 22), color, 5)
            else:
                draw.line((x_cursor, legend_y + 22, x_cursor + 42, legend_y + 22), fill=color, width=6)
                draw.ellipse((x_cursor + 16, legend_y + 16, x_cursor + 28, legend_y + 28), fill=color)
            draw_text(draw, (x_cursor + 52, legend_y + 8), label, font_small, "#24272d")
            x_cursor += 52 + max(150, len(label) * 11)
        if lds_marker and lds_marker.nymex_price is not None:
            draw.ellipse((x_cursor, legend_y + 12, x_cursor + 20, legend_y + 32), fill="#ffd166", outline="#7c2d12", width=3)
            draw_text(draw, (x_cursor + 30, legend_y + 8), "LDS D-2", font_small, "#24272d")

        panel_values: list[float] = []
        for item in series:
            panel_values.extend(clean_values(item["values"]))  # type: ignore[arg-type]
        if not panel_values:
            value_min, value_max = 0.0, 1.0
        else:
            value_min = min(panel_values)
            value_max = max(panel_values)
            if value_min == value_max:
                value_min -= 0.5
                value_max += 0.5
            padding = max((value_max - value_min) * 0.18, 0.08)
            value_min -= padding
            value_max += padding

        panel_height = bottom - top

        def y_for(value: float) -> float:
            return bottom - ((value - value_min) / (value_max - value_min)) * panel_height

        draw_text(draw, (plot_left, header_y), title, font_label_bold, "#24272d")
        draw_text(draw, (plot_left, header_y + 28), subtitle_text, font_small, "#6b7280")

        for index, date in enumerate(dates):
            if date.weekday() >= 5 or date in holidays:
                day_width = plot_width / max(len(dates) - 1, 1)
                left = int(x_for(index) - day_width / 2)
                right = int(x_for(index) + day_width / 2)
                draw.rectangle((max(plot_left, left), top, min(plot_right, right), bottom), fill="#e5e7eb")

        for step in range(5):
            y = bottom - step * panel_height / 4
            value = value_min + step * (value_max - value_min) / 4
            draw.line((plot_left, y, plot_right, y), fill="#d7dde5", width=1)
            draw_text(draw, (plot_left - 18, int(y)), f"{value:.2f}", font_axis, "#1f2937", anchor="rm")

        for index in tick_positions:
            x = x_for(index)
            draw.line((x, top, x, bottom), fill="#e1e7ef", width=1)
            if show_x_labels:
                draw_text(draw, (int(x), bottom + 28), dates[index].strftime("%b %d"), font_axis, "#1f2937", anchor="mm")

        draw.line((plot_left, bottom, plot_right, bottom), fill="#c7ccd4", width=2)
        draw.line((plot_left, top, plot_left, bottom), fill="#c7ccd4", width=2)

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

        for item in series:
            draw_series(
                item["values"],  # type: ignore[arg-type]
                item["color"],  # type: ignore[arg-type]
                bool(item.get("dashed", False)),
                bool(item.get("markers", True)),
            )

        if lds_marker and lds_marker.nymex_price is not None:
            lds_index = dates.index(lds_marker.price_date)
            x = x_for(lds_index)
            y = y_for(lds_marker.nymex_price)
            draw.ellipse((x - 14, y - 14, x + 14, y + 14), fill="#ffd166", outline="#7c2d12", width=5)
            draw_text(draw, (int(min(x + 18, plot_right - 130)), int(max(y - 42, top + 8))), f"LDS {lds_marker.price_date:%b %d}\n{lds_marker.nymex_price:.3f}", font_label_bold, "#7c2d12")

    draw_panel(
        "NYMEX Daily Price",
        "Front strip daily price with LDS D-2 marker",
        panel1_top,
        panel1_bottom,
        [
            {
                "values": nymex,
                "color": "#1f6f8b",
                "label": "NYMEX",
                "markers": True,
            }
        ],
        show_x_labels=False,
        lds_marker=lds,
    )

    draw_panel(
        "IFERC Houston Ship Channel + Katy",
        "HSC monthly fixed line versus Katy Gas Daily",
        panel2_top,
        panel2_bottom,
        [
            {
                "values": katy,
                "color": "#c75000",
                "label": "Katy GD",
                "markers": True,
            },
            {
                "values": hsc,
                "color": "#5b5f97",
                "label": "IFERC HSC",
                "dashed": True,
                "markers": False,
            },
        ],
        show_x_labels=True,
    )

    draw_text(draw, (plot_left - 72, (panel1_top + panel1_bottom) // 2), "Price", font_body, "#24272d", anchor="mm")
    draw_text(draw, (plot_left - 72, (panel2_top + panel2_bottom) // 2), "Price", font_body, "#24272d", anchor="mm")
    draw_text(draw, ((plot_left + plot_right) // 2, height - 32), "Price date", font_body, "#24272d", anchor="mm")

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def render_power_chart(rows: list[PowerDailyRow], month: str) -> bytes:
    if not rows:
        raise HTTPException(status_code=404, detail=f"No power rows found for {month}.")

    dates = [row.date for row in rows]
    dam = [row.dam_avg for row in rows]
    rtm = [row.rtm_avg for row in rows]
    valid_values = clean_values(dam + rtm)
    month_label = dates[0].strftime("%B %Y")
    latest_da = next((row for row in reversed(rows) if row.dam_avg is not None), None)
    latest_rt = next((row for row in reversed(rows) if row.rtm_avg is not None), None)
    holidays = us_market_holidays(dates[0].year)

    width, height = 1920, 980
    plot_left, plot_right = 120, width - 50
    plot_top, plot_bottom = 210, 850
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

    if not valid_values:
        value_min, value_max = 0.0, 1.0
    else:
        value_min = min(valid_values)
        value_max = max(valid_values)
        if value_min == value_max:
            value_min -= 5.0
            value_max += 5.0
        padding = max((value_max - value_min) * 0.18, 5.0)
        value_min -= padding
        value_max += padding

    def x_for(index: int) -> float:
        if len(dates) <= 1:
            return float(plot_left)
        return plot_left + index * plot_width / (len(dates) - 1)

    def y_for(value: float) -> float:
        return plot_bottom - ((value - value_min) / (value_max - value_min)) * plot_height

    draw_text(draw, (width // 2, 42), f"ERCOT Power Daily Average - {month_label}", font_title, "#24272d", anchor="ma")
    subtitle_parts = []
    if latest_da:
        subtitle_parts.append(f"DA through {latest_da.date:%b %d}")
    if latest_rt:
        subtitle_parts.append(f"RT through {latest_rt.date:%b %d}")
    subtitle = "; ".join(subtitle_parts) or "No DA/RT values returned from sheet"
    draw_text(draw, (plot_left, 98), f"DAM and RTM daily averages from Google Sheet power tab. {subtitle}.", font_body, "#4b5563")
    draw_text(draw, (plot_right, 58), APP_VERSION, font_small, "#9b9892", anchor="ra")

    legend_x, legend_y = plot_right - 410, 132
    draw.rounded_rectangle((legend_x, legend_y, legend_x + 400, legend_y + 48), radius=8, fill="#ffffff", outline="#d4d0c8", width=2)
    draw.line((legend_x + 18, legend_y + 24, legend_x + 60, legend_y + 24), fill="#1e3a5f", width=6)
    draw.ellipse((legend_x + 34, legend_y + 18, legend_x + 46, legend_y + 30), fill="#1e3a5f")
    draw_text(draw, (legend_x + 74, legend_y + 10), "DA Daily Avg", font_legend, "#24272d")
    draw.line((legend_x + 225, legend_y + 24, legend_x + 267, legend_y + 24), fill="#c75000", width=6)
    draw.ellipse((legend_x + 241, legend_y + 18, legend_x + 253, legend_y + 30), fill="#c75000")
    draw_text(draw, (legend_x + 281, legend_y + 10), "RT Daily Avg", font_legend, "#24272d")

    for index, date in enumerate(dates):
        if date.weekday() >= 5 or date in holidays:
            day_width = plot_width / max(len(dates) - 1, 1)
            left = int(x_for(index) - day_width / 2)
            right = int(x_for(index) + day_width / 2)
            draw.rectangle((max(plot_left, left), plot_top, min(plot_right, right), plot_bottom), fill="#e5e7eb")

    for step in range(6):
        y = plot_bottom - step * plot_height / 5
        value = value_min + step * (value_max - value_min) / 5
        draw.line((plot_left, y, plot_right, y), fill="#d7dde5", width=1)
        draw_text(draw, (plot_left - 18, int(y)), f"{value:.0f}", font_axis, "#1f2937", anchor="rm")

    tick_positions = list(range(0, len(dates), 2))
    if (len(dates) - 1) not in tick_positions:
        tick_positions.append(len(dates) - 1)
    for index in tick_positions:
        x = x_for(index)
        draw.line((x, plot_top, x, plot_bottom), fill="#e1e7ef", width=1)
        draw_text(draw, (int(x), plot_bottom + 30), dates[index].strftime("%b %d"), font_axis, "#1f2937", anchor="mm")

    draw.line((plot_left, plot_bottom, plot_right, plot_bottom), fill="#c7ccd4", width=2)
    draw.line((plot_left, plot_top, plot_left, plot_bottom), fill="#c7ccd4", width=2)

    def draw_series(values: list[float | None], color: str) -> None:
        prev: tuple[float, float] | None = None
        for index, value in enumerate(values):
            if value is None:
                prev = None
                continue
            point = (x_for(index), y_for(value))
            if prev is not None:
                draw.line((*prev, *point), fill=color, width=6)
            x, y = point
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline=color)
            prev = point

    draw_series(dam, "#1e3a5f")
    draw_series(rtm, "#c75000")

    if latest_da and latest_da.dam_avg is not None:
        x = x_for(dates.index(latest_da.date))
        y = y_for(latest_da.dam_avg)
        draw_text(draw, (int(x + 14), int(y - 34)), f"DA {latest_da.dam_avg:.2f}", font_label_bold, "#1e3a5f")
    if latest_rt and latest_rt.rtm_avg is not None:
        x = x_for(dates.index(latest_rt.date))
        y = y_for(latest_rt.rtm_avg)
        draw_text(draw, (int(x + 14), int(y + 10)), f"RT {latest_rt.rtm_avg:.2f}", font_label_bold, "#c75000")

    draw_text(draw, (plot_left - 72, (plot_top + plot_bottom) // 2), "$/MWh", font_body, "#24272d", anchor="mm")
    draw_text(draw, ((plot_left + plot_right) // 2, height - 36), "Price date", font_body, "#24272d", anchor="mm")

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": APP_TITLE,
        "version": APP_VERSION,
        "gas_chart": "/chart.png?month=current",
        "power_chart": "/power-chart.png?month=current",
        "health": "/health",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


@app.get("/version")
def version() -> dict[str, str]:
    return {"version": APP_VERSION}


@app.get("/debug-sheet")
def debug_sheet() -> JSONResponse:
    return JSONResponse(fetch_sheet_preview())


@app.get("/debug-power-sheet")
def debug_power_sheet() -> JSONResponse:
    return JSONResponse(fetch_power_preview())


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


@app.get("/power-chart-info")
def power_chart_info(month: str | None = Query(default="current")) -> JSONResponse:
    rows = fetch_power_rows()
    selected_month = choose_power_month(rows, month)
    daily_rows = power_daily_rows(rows, selected_month)
    latest_da = next((row for row in reversed(daily_rows) if row.dam_avg is not None), None)
    latest_rt = next((row for row in reversed(daily_rows) if row.rtm_avg is not None), None)
    return JSONResponse(
        {
            "month": selected_month,
            "hourly_row_count": len(rows),
            "calendar_row_count": len(daily_rows),
            "da_day_count": sum(1 for row in daily_rows if row.dam_avg is not None),
            "rt_day_count": sum(1 for row in daily_rows if row.rtm_avg is not None),
            "latest_da_date": latest_da.date.isoformat() if latest_da else None,
            "latest_da_avg": latest_da.dam_avg if latest_da else None,
            "latest_rt_date": latest_rt.date.isoformat() if latest_rt else None,
            "latest_rt_avg": latest_rt.rtm_avg if latest_rt else None,
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


@app.get("/power-chart-check")
def power_chart_check(month: str | None = Query(default="current")) -> JSONResponse:
    rows = fetch_power_rows()
    selected_month = choose_power_month(rows, month)
    daily_rows = power_daily_rows(rows, selected_month)
    try:
        render_power_chart(daily_rows, selected_month)
        render_status = "ok"
        render_error = None
    except Exception as exc:
        render_status = "error"
        render_error = f"{type(exc).__name__}: {exc}"

    return JSONResponse(
        {
            "month": selected_month,
            "hourly_row_count": len(rows),
            "calendar_row_count": len(daily_rows),
            "da_day_count": sum(1 for row in daily_rows if row.dam_avg is not None),
            "rt_day_count": sum(1 for row in daily_rows if row.rtm_avg is not None),
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


@app.get("/power-chart.png")
def power_chart_png(month: str | None = Query(default="current")) -> Response:
    rows = fetch_power_rows()
    selected_month = choose_power_month(rows, month)
    daily_rows = power_daily_rows(rows, selected_month)
    try:
        png = render_power_chart(daily_rows, selected_month)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Power chart rendering failed: {type(exc).__name__}: {exc}",
        ) from exc
    filename = f"lai-ercot-power-daily-average-{selected_month}.png"
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
