"""
Energy Price Dashboard — Chart Image Service
Render Web Service (Flask). n8n이 HTTP GET으로 호출하면 지표별 PNG를 반환한다.

Endpoints:
  GET /                      → health check
  GET /chart/nymex          → NYMEX 일별 (당월)
  GET /chart/hsc_katy       → IFERC HSC vs GD Katy (당월)
  GET /chart/power_dart     → ERCOT Daily DA/RT (당월)
  GET /chart/power_peak     → Daily Peak Load & Net Load at Peak (당월)
  GET /chart/all            → 4장 zip (선택)

Query params (공통, 선택):
  ?month=YYYY-MM            → 대상 월 (기본: 현재 CT 기준 당월)

환경변수:
  SHEET_ID                  → Google 스프레드시트 ID
  GOOGLE_API_KEY            → Sheets API 키 (읽기 전용, 시트 공개 필요)
"""

import io
import os
import sys
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import matplotlib

matplotlib.use("Agg")  # 헤드리스 렌더링
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator, MultipleLocator, FixedLocator
from flask import Flask, send_file, request, jsonify

sys.setrecursionlimit(10000)

app = Flask(__name__)

# ── 설정 ────────────────────────────────────────────────────────
SHEET_ID = os.environ.get("SHEET_ID", "1g-yuKuUhSd3nU7eDiLWFgxOcbuFkBWmWH0wZvGg6B9I")
API_KEY = os.environ.get("GOOGLE_API_KEY", "")
CT = ZoneInfo("America/Chicago")

# 대시보드와 동일한 컬러 팔레트 (글래스 테마)
C_SKY = "#0ea5e9"      # DAM / NYMEX
C_PURPLE = "#7c3aed"   # RTM / Net Load
C_CYAN = "#0284c7"     # Katy
C_NAVY = "#075985"     # HSC
C_RED = "#e11d48"      # Peak Load
C_TEXT = "#0c4a6e"
C_MUTED = "#5b8bb5"
C_GRID = "#dbeafe"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.edgecolor": C_GRID,
    "axes.linewidth": 0.8,
    "axes.grid": True,
    "grid.color": C_GRID,
    "grid.linewidth": 0.8,
    "text.color": C_TEXT,
    "axes.labelcolor": C_MUTED,
    "xtick.color": C_MUTED,
    "ytick.color": C_MUTED,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
})


# ── 유틸 ────────────────────────────────────────────────────────
def current_month():
    now = datetime.now(CT)
    return f"{now.year}-{now.month:02d}"


def fetch_sheet(tab):
    """Google Sheets 탭 전체를 2D 리스트로 반환."""
    if not API_KEY:
        raise RuntimeError("GOOGLE_API_KEY 환경변수가 설정되지 않았습니다.")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
        f"/values/{tab}?key={API_KEY}"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json().get("values", [])


def to_float(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (ValueError, TypeError):
        return None


def month_label(ym):
    y, m = ym.split("-")
    return datetime(int(y), int(m), 1).strftime("%B %Y")


def style_axes(ax, ylabel=None, legend_items=None):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(length=0)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10)
    # 상단에 데이터와 겹치지 않도록 y축 여유 확보
    ymin, ymax = ax.get_ylim()
    ax.set_ylim(ymin, ymax + (ymax - ymin) * 0.18)
    # ax.legend()는 Python 3.14 + matplotlib에서 deepcopy 무한 재귀를 일으키므로
    # 사용하지 않고, 축 상단에 색상 선 + 라벨을 직접 그려 범례를 구성한다.
    if legend_items:
        draw_manual_legend(ax, legend_items)


def draw_manual_legend(ax, items):
    """items: [(label, color, dashed(bool)), ...] 를 축 상단에 수동 범례로 그린다."""
    x = 0.02          # axes 좌표 시작 x
    y = 0.94          # axes 좌표 y (상단)
    line_len = 0.035  # 선 길이 (axes 좌표)
    gap = 0.015       # 선-텍스트 간격
    for label, color, dashed in items:
        ax.plot(
            [x, x + line_len], [y, y],
            transform=ax.transAxes, color=color, lw=2,
            ls="--" if dashed else "-", clip_on=False, solid_capstyle="round",
        )
        ax.text(
            x + line_len + gap, y, label,
            transform=ax.transAxes, fontsize=10, va="center", ha="left",
            color=C_TEXT,
        )
        # 다음 항목 x 위치 = 텍스트 길이에 비례해 이동 (대략치)
        x += line_len + gap + 0.018 * (len(label) + 1)


def label_last(ax, xs, ys, color, fmt="{:.2f}", dy=8):
    """가장 최근(마지막) 데이터 지점에 값 라벨 표시."""
    if not xs or not ys:
        return
    x, y = xs[-1], ys[-1]
    ax.annotate(
        fmt.format(y), xy=(x, y), xytext=(0, dy),
        textcoords="offset points", ha="center", va="bottom",
        fontsize=10, fontweight="bold", color=color,
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=color, lw=1, alpha=0.9),
    )


def fig_to_png(fig):
    # 제목/축 라벨 공간을 명시적 여백으로 확보 (bbox_inches="tight"는
    # py3.14에서 재귀를 유발하므로 사용하지 않음). 범례는 축 안쪽에 있음.
    fig.subplots_adjust(top=0.88, bottom=0.12, left=0.09, right=0.97)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


# ── 데이터 파싱 ──────────────────────────────────────────────────
def parse_gas(rows, ym):
    """Gas 시트 → 당월 NYMEX(일별), Katy(일별), HSC(월)."""
    # 컬럼: PriceDate | NYMEX_StripDate | NYMEX_Price | NYMEX_RowCount | RegionalPrice_Katy | RegionalPrice_HoustonShipChl_Monthly
    nymex, katy, hsc = {}, {}, None
    prev_nymex_vals = []  # 전월 NYMEX 값 (LDS 계산용)
    prev_ym = prev_month(ym)

    for row in rows[1:]:
        if not row or len(row) < 3:
            continue
        pdate = str(row[0])[:10] if row[0] else ""
        if len(pdate) < 10:
            continue
        row_ym = pdate[:7]
        day = int(pdate[8:10])
        nv = to_float(row[2]) if len(row) > 2 else None
        kv = to_float(row[4]) if len(row) > 4 else None
        hv = to_float(row[5]) if len(row) > 5 else None

        if row_ym == ym:
            if nv is not None:
                nymex[day] = nv
            if kv is not None:
                katy[day] = kv
            if hv is not None:
                hsc = hv
        elif row_ym == prev_ym and nv is not None:
            prev_nymex_vals.append((day, nv))

    # 전월 마지막 3거래일 평균 (LDS)
    prev_nymex_vals.sort()
    last3 = [v for _, v in prev_nymex_vals[-3:]]
    prev_lds = sum(last3) / len(last3) if last3 else None

    return nymex, katy, hsc, prev_lds


def parse_power(rows, ym):
    """Power 시트 → 당월 일별 DA/RT 평균 + 일별 Peak Load / Peak시점 NetLoad."""
    # 컬럼: DateTime | Date | Hour | DAM | RTM | Demand | WindProduction | SolarProduction | Netload | UpdatedAt
    days = {}  # date -> {'DAM':[], 'RTM':[], 'LOAD':{h:v}, 'NL':{h:v}}
    for row in rows[1:]:
        if not row or len(row) < 9:
            continue
        dstr = str(row[1])[:10] if row[1] else ""
        if len(dstr) < 10 or dstr[:7] != ym:
            continue
        try:
            hour = int(row[2])
        except (ValueError, TypeError):
            continue
        if not (0 <= hour <= 23):
            continue
        d = days.setdefault(dstr, {"DAM": [], "RTM": [], "LOAD": {}, "NL": {}})
        dam = to_float(row[3])
        rtm = to_float(row[4])
        dem = to_float(row[5])
        nl = to_float(row[8])
        if dam is not None:
            d["DAM"].append(dam)
        if rtm is not None:
            d["RTM"].append(rtm)
        if dem is not None:
            d["LOAD"][hour] = dem / 1000.0  # MW → GW
        if nl is not None:
            d["NL"][hour] = nl / 1000.0

    # 일별 집계
    dam_daily, rtm_daily, peak_daily, nl_at_peak = {}, {}, {}, {}
    for dstr, d in days.items():
        day = int(dstr[8:10])
        if d["DAM"]:
            dam_daily[day] = sum(d["DAM"]) / len(d["DAM"])
        if d["RTM"]:
            rtm_daily[day] = sum(d["RTM"]) / len(d["RTM"])
        if d["LOAD"]:
            peak_hour = max(d["LOAD"], key=d["LOAD"].get)
            peak_daily[day] = d["LOAD"][peak_hour]
            nl_at_peak[day] = d["NL"].get(peak_hour)
    return dam_daily, rtm_daily, peak_daily, nl_at_peak


def prev_month(ym):
    y, m = map(int, ym.split("-"))
    m -= 1
    if m == 0:
        y -= 1
        m = 12
    return f"{y}-{m:02d}"


def dict_to_series(d, ndays=31):
    """{day: val} → (x일리스트, y값리스트), 값 없는 날 제외."""
    xs = sorted(d.keys())
    ys = [d[x] for x in xs]
    return xs, ys


# ── 차트 생성 ────────────────────────────────────────────────────
def chart_nymex(ym):
    rows = fetch_sheet("Gas")
    nymex, _, _, prev_lds = parse_gas(rows, ym)
    xs, ys = dict_to_series(nymex)

    fig, ax = plt.subplots(figsize=(9, 4.4))
    if xs:
        ax.plot(xs, ys, color=C_SKY, lw=2, marker="o", ms=4,
                mfc="white", mec=C_SKY, mew=1.4, label="NYMEX Daily")
        label_last(ax, xs, ys, C_SKY, fmt="{:.3f}")
    if prev_lds is not None:
        ax.axhline(prev_lds, color=C_SKY, lw=1.5, ls="--", alpha=0.7,
                   label=f"NYMEX LDS ({prev_lds:.3f})")
    fig.suptitle(f"NYMEX Daily Price — {month_label(ym)}",
                 fontsize=14, fontweight="bold", color=C_TEXT, x=0.09, ha="left", y=0.97)
    if xs:
        ax.xaxis.set_major_locator(FixedLocator([float(x) for x in xs]))
        ax.tick_params(axis="x", labelsize=8)
    ax.set_xlabel("Day")
    lds_label = f"NYMEX LDS ({prev_lds:.3f})" if prev_lds is not None else "NYMEX LDS"
    style_axes(ax, "$/MMBtu", legend_items=[
        ("NYMEX Daily", C_SKY, False),
        (lds_label, C_SKY, True),
    ])
    return fig_to_png(fig)


def chart_hsc_katy(ym):
    rows = fetch_sheet("Gas")
    _, katy, hsc, _ = parse_gas(rows, ym)
    xs, ys = dict_to_series(katy)

    fig, ax = plt.subplots(figsize=(9, 4.4))
    if xs:
        ax.plot(xs, ys, color=C_CYAN, lw=2, marker="o", ms=4,
                mfc="white", mec=C_CYAN, mew=1.4, label="Katy GD Daily")
        label_last(ax, xs, ys, C_CYAN, fmt="{:.3f}")
    if hsc is not None:
        ax.axhline(hsc, color=C_NAVY, lw=2.5, label=f"HSC Monthly ({hsc:.3f})")
    fig.suptitle(f"IFERC HSC vs GD Katy — {month_label(ym)}",
                 fontsize=14, fontweight="bold", color=C_TEXT, x=0.09, ha="left", y=0.97)
    if xs:
        ax.xaxis.set_major_locator(FixedLocator([float(x) for x in xs]))
        ax.tick_params(axis="x", labelsize=8)
    ax.set_xlabel("Day")
    hsc_label = f"HSC Monthly ({hsc:.3f})" if hsc is not None else "HSC Monthly"
    style_axes(ax, "$/MMBtu", legend_items=[
        ("Katy GD Daily", C_CYAN, False),
        (hsc_label, C_NAVY, False),
    ])
    return fig_to_png(fig)


def chart_power_dart(ym):
    rows = fetch_sheet("Power")
    dam_daily, rtm_daily, _, _ = parse_power(rows, ym)
    xd, yd = dict_to_series(dam_daily)
    xr, yr = dict_to_series(rtm_daily)

    fig, ax = plt.subplots(figsize=(9, 4.4))
    if xd:
        ax.plot(xd, yd, color=C_SKY, lw=2, marker="o", ms=3.5,
                mfc="white", mec=C_SKY, mew=1.4, label="DAM")
        label_last(ax, xd, yd, C_SKY, fmt="{:.1f}", dy=-18)
    if xr:
        ax.plot(xr, yr, color=C_PURPLE, lw=2, marker="o", ms=3.5,
                mfc="white", mec=C_PURPLE, mew=1.4, label="RTM")
        label_last(ax, xr, yr, C_PURPLE, fmt="{:.1f}", dy=8)
    fig.suptitle(f"ERCOT Daily DA / RT — {month_label(ym)}",
                 fontsize=14, fontweight="bold", color=C_TEXT, x=0.09, ha="left", y=0.97)
    ax.xaxis.set_major_locator(MultipleLocator(1))
    ax.tick_params(axis="x", labelsize=8)
    ax.set_xlabel("Day")
    style_axes(ax, "$/MWh", legend_items=[
        ("DAM", C_SKY, False),
        ("RTM", C_PURPLE, False),
    ])
    return fig_to_png(fig)


def chart_power_peak(ym):
    rows = fetch_sheet("Power")
    _, _, peak_daily, nl_at_peak = parse_power(rows, ym)
    xp, yp = dict_to_series(peak_daily)
    xn, yn = dict_to_series(nl_at_peak)

    fig, ax = plt.subplots(figsize=(9, 4.4))
    if xp:
        ax.plot(xp, yp, color=C_RED, lw=2, marker="o", ms=3.5,
                mfc="white", mec=C_RED, mew=1.4, label="Peak Load")
        label_last(ax, xp, yp, C_RED, fmt="{:.1f}", dy=8)
    if xn:
        ax.plot(xn, yn, color=C_PURPLE, lw=2, ls="--", marker="o", ms=3.5,
                mfc="white", mec=C_PURPLE, mew=1.4, label="Net Load @ Peak")
        label_last(ax, xn, yn, C_PURPLE, fmt="{:.1f}", dy=-18)
    fig.suptitle(f"Daily Peak Load & Net Load — {month_label(ym)}",
                 fontsize=14, fontweight="bold", color=C_TEXT, x=0.09, ha="left", y=0.97)
    ax.xaxis.set_major_locator(MultipleLocator(1))
    ax.tick_params(axis="x", labelsize=8)
    ax.set_xlabel("Day")
    style_axes(ax, "GW", legend_items=[
        ("Peak Load", C_RED, False),
        ("Net Load @ Peak", C_PURPLE, True),
    ])
    return fig_to_png(fig)


CHARTS = {
    "nymex": ("nymex_daily", chart_nymex),
    "hsc_katy": ("hsc_vs_katy", chart_hsc_katy),
    "power_dart": ("power_daily_dart", chart_power_dart),
    "power_peak": ("power_daily_peak", chart_power_peak),
}


# ── 라우트 ──────────────────────────────────────────────────────
@app.route("/")
def health():
    import matplotlib
    return jsonify({
        "status": "ok",
        "service": "energy-price-charts",
        "python": sys.version.split()[0],
        "matplotlib": matplotlib.__version__,
        "endpoints": [f"/chart/{k}" for k in CHARTS] + ["/chart/all"],
        "month_default": current_month(),
    })


@app.route("/chart/<name>")
def chart(name):
    ym = request.args.get("month", current_month())
    try:
        datetime.strptime(ym, "%Y-%m")
    except ValueError:
        return jsonify({"error": "month must be YYYY-MM"}), 400

    if name == "all":
        return chart_all(ym)

    if name not in CHARTS:
        return jsonify({"error": f"unknown chart '{name}'",
                        "available": list(CHARTS)}), 404

    fname, fn = CHARTS[name]
    try:
        png = fn(ym)
    except Exception as e:
        import traceback
        tb = traceback.format_exc().splitlines()
        # app.py 프레임만 추출 (재귀 유발 지점 확인용)
        app_frames = [ln.strip() for ln in tb if "app.py" in ln]
        return jsonify({
            "error": str(e),
            "type": type(e).__name__,
            "app_frames": app_frames[:12],
            "tail": tb[-6:],
        }), 500
    return send_file(png, mimetype="image/png",
                     download_name=f"{fname}_{ym}.png")


def chart_all(ym):
    """4개 차트를 zip으로 묶어 반환."""
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, (fname, fn) in CHARTS.items():
            try:
                png = fn(ym)
                zf.writestr(f"{fname}_{ym}.png", png.read())
            except Exception as e:
                zf.writestr(f"{fname}_ERROR.txt", str(e))
    mem.seek(0)
    return send_file(mem, mimetype="application/zip",
                     download_name=f"energy_charts_{ym}.zip")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
