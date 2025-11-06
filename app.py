# -----------------------------
# ATFM Iraq – Automated ADP & Visualization
# -----------------------------
# Features:
# - Login (any username, fixed password: atfmiraqmm)
# - Pulls latest Google Docx every run (auto-updates as you edit the Doc)
# - Parses: Specific-airport traffic, NOTAMs (with auto-interpretation), hourly overflights
# - Live animations: charts + route animations
# - Sector capacity (South/TASMI 26 acft/hr, RATVO 27 acft/hr) with utilization gauges
# - Iraqi FIR mini-map with routes: Tasmi→Kaban & Modik→Sidad
# - Weather animation per route if found in the Doc (fallback demo if missing)
# - Auto-generate ADP (DOCX) for download
# -----------------------------

import streamlit as st
import pandas as pd
import numpy as np
import requests
import re
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Tuple

# Viz
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk

# Word parsing & ADP generation
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

# -----------------------------
# CONFIG
# -----------------------------
st.set_page_config(page_title="ATFM IRAQ – ADP & Visualizer", layout="wide")
APP_TITLE = "🇮🇶 ATFM IRAQ – Automated ADP & Visualizer"
GOOGLE_DOC_EXPORT = "https://docs.google.com/document/d/1PUtfstGvw8PhKWbnOOvlBjCa7wJJX-nM/export?format=docx"
SECTOR_CAP = {"South/TASMI": 26, "RATVO": 27}
ROUTES = {
    "Tasmi→Kaban": {
        "points": [
            # Approximate waypoint coords for demo purposes
            # TASMI (near SE Iraq), KABAN (NE Iraq) – adjust to your authoritative waypoints anytime
            (31.3, 47.3),  # (lat, lon)
            (33.6, 44.4),
            (35.55, 45.3),
            (36.1, 44.6),  # KABAN approx
        ]
    },
    "MODIK→SIDAD": {
        "points": [
            # MODIK (NW Iraq), SIDAD (middle-east area) – approximate for demo
            (36.5, 42.8),
            (35.6, 43.7),
            (34.2, 44.3),
            (33.1, 44.5),  # SIDAD approx
        ]
    },
}
AIRPORTS = {
    "ORBI (Baghdad)": (33.2625, 44.2346),
    "ORNI (Najaf)": (31.989, 44.404),
    "ORER (Erbil)": (36.2376, 43.9632),
    "ORBS (Basra)": (30.549, 47.662),
}

# -----------------------------
# AUTH
# -----------------------------
st.title(APP_TITLE)
col_user, col_pass = st.columns([1, 1])
with col_user:
    username = st.text_input("Username (any):", value="")
with col_pass:
    password = st.text_input("Password:", type="password", value="")

if password != "atfmiraqmm":
    st.warning("Enter the correct password to access the system.")
    st.stop()

st.success(f"Welcome {username if username else 'User'} ✅")

# -----------------------------
# FETCH DOCX
# -----------------------------
with st.spinner("Fetching the latest Google Doc…"):
    try:
        r = requests.get(GOOGLE_DOC_EXPORT, timeout=30)
        r.raise_for_status()
        file_bytes = BytesIO(r.content)
        st.caption("Latest document downloaded successfully.")
    except Exception as e:
        st.error(f"Could not download the Google Doc: {e}")
        st.stop()

# -----------------------------
# PARSE DOCX → TEXT
# -----------------------------
def read_docx_text(buff: BytesIO) -> str:
    doc = Document(buff)
    parts = []
    for p in doc.paragraphs:
        parts.append(p.text)
    return "\n".join(parts)

raw_text = read_docx_text(file_bytes)

# -----------------------------
# EXTRACTION HELPERS
# -----------------------------
def extract_airport_traffic(text: str) -> pd.DataFrame:
    """
    Expected patterns (examples – flexible):
    - ORBI Arrivals: 45, Departures: 50
    - ORNI: A=20 D=22
    - ERBIL (ORER) Arr 15 Dep 17
    """
    patt = re.compile(
        r"\b(ORBI|ORNI|ORER|ORBS)\b[^\n]*?(Arr(?:ivals)?\s*[:=]?\s*(\d+))?[^\n]*?(Dep(?:artures)?\s*[:=]?\s*(\d+))?",
        re.IGNORECASE,
    )
    rows = []
    for m in patt.finditer(text):
        icao = m.group(1).upper()
        arr = m.group(3)
        dep = m.group(5)
        arr = int(arr) if arr and arr.isdigit() else None
        dep = int(dep) if dep and dep.isdigit() else None
        if arr is not None or dep is not None:
            rows.append({"Airport": icao, "Arrivals": arr or 0, "Departures": dep or 0})
    df = pd.DataFrame(rows).groupby("Airport", as_index=False).sum()
    return df

def extract_hourly_overflights(text: str) -> pd.DataFrame:
    """
    Matches lines like:
    0000–0100 73
    01:00-02:00 64
    2300–0000 25
    """
    patt = re.compile(
        r"\b(\d{2}[:]?00)\s*[–\-]\s*(\d{2}[:]?00)\s+(\d{1,4})",
        re.IGNORECASE
    )
    hours, values = [], []
    for m in patt.finditer(text):
        start_h = m.group(1).replace(":", "")
        end_h = m.group(2).replace(":", "")
        val = int(m.group(3))
        label = f"{start_h[:2]}00–{end_h[:2]}00"
        hours.append(label)
        values.append(val)
    return pd.DataFrame({"Period (UTC)": hours, "Overflights": values})

def extract_weather_by_route(text: str) -> Dict[str, List[Tuple[str, str]]]:
    """
    Looks for route blocks like:
    Tasmi to Kaban:
      00-06Z: Light CAT FL200-280, W @ 20-25kt
      06-12Z: ...
    MODIK to SIDAD:
      00-06Z: ...
    Returns dict of route -> list of (window, summary)
    """
    route_blocks = {}
    for key in ["Tasmi to Kaban", "Tasmi→Kaban", "Tasmi-Kaban", "MODIK to SIDAD", "MODIK→SIDAD", "MODIK-SIDAD"]:
        route_blocks[key] = []

    # Simple block detection
    lines = text.splitlines()
    current_key = None
    for ln in lines:
        l = ln.strip()
        if re.search(r"tasmi.*kaban", l, re.IGNORECASE):
            current_key = "Tasmi→Kaban"
            continue
        if re.search(r"modik.*sidad", l, re.IGNORECASE):
            current_key = "MODIK→SIDAD"
            continue
        if current_key:
            # time window + summary e.g., "00-06Z: Light CAT ..."
            m = re.match(r"(\d{2}\s*-\s*\d{2}Z)\s*:\s*(.+)$", l, re.IGNORECASE)
            if m:
                route_blocks[current_key].append((m.group(1).replace(" ", ""), m.group(2)))

    # Fallback demo if none found
    if not route_blocks["Tasmi→Kaban"]:
        route_blocks["Tasmi→Kaban"] = [
            ("00-06Z", "Light CAT FL200–280, W 20–25 kt"),
            ("06-12Z", "Isolated CB N of route, icing above FL240"),
            ("12-18Z", "Calm, high cirrus"),
            ("18-24Z", "Moderate headwind 25–30 kt"),
        ]
    if not route_blocks["MODIK→SIDAD"]:
        route_blocks["MODIK→SIDAD"] = [
            ("00-06Z", "Mountain wave near NW, light chop"),
            ("06-12Z", "Nil SIGWX, good vis"),
            ("12-18Z", "Convective build-ups SE of route"),
            ("18-24Z", "Crosswind shear FL180–220"),
        ]
    return route_blocks

def extract_notams(text: str) -> List[dict]:
    """
    Extracts basic NOTAM lines and classifies them.
    Looks for lines containing 'NOTAM' or Q) ... or RWY/TWY/ILS keywords.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    candidates = [l for l in lines if re.search(r"\bNOTAM\b|^Q\)|RWY|TWY|ILS|VOR|NDB|CLOSED|U/S|CRANE|WORK IN PROGRESS", l, re.IGNORECASE)]
    notams = []
    for l in candidates:
        category = "General"
        if re.search(r"RWY|RUNWAY", l, re.IGNORECASE):
            category = "Runway"
        if re.search(r"TWY|TAXI", l, re.IGNORECASE):
            category = "Taxiway"
        if re.search(r"ILS|VOR|NDB", l, re.IGNORECASE):
            category = "NavAid"
        if re.search(r"CLOSED|U/S|UNSERVICEABLE", l, re.IGNORECASE):
            category = "Closure/Unserviceable"
        if re.search(r"CRANE|WIP|WORK IN PROGRESS", l, re.IGNORECASE):
            category = "Obstacles/Work"
        icao = None
        m = re.search(r"\bOR[A-Z]{2}\b", l)  # ORBI/ORNI/ORER/ORBS...
        if m:
            icao = m.group(0)
        action = "Info"
        if re.search(r"CLOSED|U/S|UNSERVICEABLE", l, re.IGNORECASE):
            action = "Restrict / Mitigate"
        notams.append({"Airport": icao or "-", "Category": category, "Action": action, "Text": l})
    return notams

# -----------------------------
# RUN EXTRACTION
# -----------------------------
airport_df = extract_airport_traffic(raw_text)
overflights_df = extract_hourly_overflights(raw_text)
route_weather = extract_weather_by_route(raw_text)
notams = extract_notams(raw_text)

# -----------------------------
# LAYOUT
# -----------------------------
st.markdown("### 📊 Specific Airport Traffic")
if airport_df.empty:
    st.info("No airport traffic found in the document. Add lines like `ORBI Arrivals: 45 Departures: 50`.")
else:
    c1, c2 = st.columns([2, 1])
    with c1:
        st.dataframe(airport_df, use_container_width=True)
        # bar
        melted = airport_df.melt(id_vars="Airport", var_name="Type", value_name="Flights")
        fig_air = px.bar(melted, x="Airport", y="Flights", color="Type", barmode="group")
        st.plotly_chart(fig_air, use_container_width=True)
    with c2:
        # total & pies
        total_arr = int(airport_df["Arrivals"].sum())
        total_dep = int(airport_df["Departures"].sum())
        st.metric("Total Arrivals", total_arr)
        st.metric("Total Departures", total_dep)
        fig_pie = px.pie(melted, names="Airport", values="Flights", title="Share by Airport")
        st.plotly_chart(fig_pie, use_container_width=True)

st.markdown("### ⏱️ Hourly Overflights (UTC)")
if overflights_df.empty:
    st.info("No hourly overflights table found. Use lines like `0000–0100 73` in your Doc.")
else:
    st.dataframe(overflights_df, use_container_width=True)
    fig_line = px.line(overflights_df, x="Period (UTC)", y="Overflights", markers=True)
    st.plotly_chart(fig_line, use_container_width=True)

# -----------------------------
# SECTOR CAPACITY & UTILIZATION
# -----------------------------
st.markdown("### 📈 Sector Capacity & Utilization")
# We will estimate a peak hour from overflights if available, otherwise use airport totals as proxy
peak_ovf = int(overflights_df["Overflights"].max()) if not overflights_df.empty else int(airport_df[["Arrivals", "Departures"]].sum().sum() // 10 or 1)

cap_rows = []
for sector, cap in SECTOR_CAP.items():
    util = peak_ovf / cap if cap else 0
    cap_rows.append({"Sector": sector, "Capacity (acft/hr)": cap, "Estimated Peak Demand": peak_ovf, "Utilization": round(util, 2)})

cap_df = pd.DataFrame(cap_rows)
c1, c2 = st.columns([2, 1])
with c1:
    st.dataframe(cap_df, use_container_width=True)
with c2:
    # gauge-style bar
    util_fig = go.Figure()
    for i, row in cap_df.iterrows():
        util_fig.add_trace(go.Indicator(
            mode="gauge+number",
            value=row["Utilization"] * 100,
            title={"text": f"{row['Sector']} Utilization %"},
            gauge={"axis": {"range": [0, 150]},
                   "bar": {"thickness": 0.4}}
        ))
    util_fig.update_layout(height=400)
    st.plotly_chart(util_fig, use_container_width=True)

# -----------------------------
# IRAQI FIR MAP + ROUTE ANIMATIONS
# -----------------------------
st.markdown("### 🗺️ Iraqi FIR Map & Route Animations")
# Basic map center near Baghdad
map_center = [33.3, 44.4]
layers = []

# Airports as scatter
airport_data = [{"name": k, "lat": v[0], "lon": v[1]} for k, v in AIRPORTS.items()]
airport_layer = pdk.Layer(
    "ScatterplotLayer",
    data=airport_data,
    get_position=["lon", "lat"],
    get_radius=10000,
    pickable=True,
)
layers.append(airport_layer)

# Static route lines
route_lines = []
for rname, info in ROUTES.items():
    pts = info["points"]
    for i in range(len(pts) - 1):
        route_lines.append({
            "route": rname,
            "from_lon": pts[i][1], "from_lat": pts[i][0],
            "to_lon": pts[i+1][1], "to_lat": pts[i+1][0],
        })

line_layer = pdk.Layer(
    "LineLayer",
    data=route_lines,
    get_source_position=["from_lon", "from_lat"],
    get_target_position=["to_lon", "to_lat"],
    get_width=4,
    pickable=True
)
layers.append(line_layer)

# Animated moving marker along selected route using slider
st.caption("Use the slider to animate a probe aircraft along the selected route.")
rchoice = st.selectbox("Route for animation", list(ROUTES.keys()), index=0)
pts = ROUTES[rchoice]["points"]
step = st.slider("Progress", min_value=0, max_value=100, value=0, help="Move to animate")
# Interpolate along polyline
def interpolate_polyline(points: List[Tuple[float, float]], t: float) -> Tuple[float, float]:
    if t <= 0: return points[0]
    if t >= 1: return points[-1]
    # equal-length segments by index
    seg = int(t * (len(points) - 1))
    seg = min(seg, len(points) - 2)
    local_t = t * (len(points) - 1) - seg
    lat1, lon1 = points[seg]
    lat2, lon2 = points[seg + 1]
    lat = lat1 + (lat2 - lat1) * local_t
    lon = lon1 + (lon2 - lon1) * local_t
    return (lat, lon)

ilat, ilon = interpolate_polyline(pts, step / 100.0)
anim_layer = pdk.Layer(
    "ScatterplotLayer",
    data=[{"lat": ilat, "lon": ilon, "name": "Probe"}],
    get_position=["lon", "lat"],
    get_radius=15000,
    pickable=True,
)
layers.append(anim_layer)

st.pydeck_chart(pdk.Deck(
    map_style=None,
    initial_view_state=pdk.ViewState(latitude=map_center[0], longitude=map_center[1], zoom=5.0, pitch=0),
    layers=layers,
    tooltip={"text": "{name}"}
))

# -----------------------------
# WEATHER ANIMATION PER ROUTE
# -----------------------------
st.markdown("### 🌤️ Route Weather (Animated)")
tab1, tab2 = st.tabs(["Tasmi→Kaban", "MODIK→SIDAD"])

def weather_anim(route_key: str):
    data = route_weather[route_key]
    wdf = pd.DataFrame(data, columns=["Window", "Summary"])
    # Fake "severity index" from keywords to visualize change
    def sev(s: str) -> int:
        s = s.lower()
        score = 0
        if "cb" in s or "convect" in s: score += 3
        if "cat" in s or "chop" in s or "turb" in s: score += 2
        if "icing" in s: score += 2
        if "headwind" in s or "crosswind" in s or "shear" in s: score += 1
        return max(1, min(5, score))  # 1..5
    wdf["SeverityIndex"] = wdf["Summary"].apply(sev)
    fig = px.bar(wdf, x="Window", y="SeverityIndex", text="Summary", animation_frame="Window",
                 range_y=[0, 6], title=f"Weather Severity – {route_key}")
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(wdf, use_container_width=True)

with tab1:
    weather_anim("Tasmi→Kaban")
with tab2:
    weather_anim("MODIK→SIDAD")

# -----------------------------
# NOTAM INTERPRETATION
# -----------------------------
st.markdown("### 📜 NOTAMs – Interpretation & Actions")
if not notams:
    st.info("No NOTAM-like lines found. Include lines with NOTAM/Q), RWY/TWY/ILS, CLOSED/U/S, etc.")
else:
    notam_df = pd.DataFrame(notams)
    st.dataframe(notam_df, use_container_width=True)
    # Quick summary by category
    fig_notam = px.bar(notam_df.groupby("Category").size().reset_index(name="Count"),
                       x="Category", y="Count", title="NOTAMs by Category")
    st.plotly_chart(fig_notam, use_container_width=True)

# -----------------------------
# AUTO-GENERATE ADP (DOCX)
# -----------------------------
st.markdown("### 🧾 Auto-Generate ADP (DOCX)")
adp_date = st.date_input("ADP Date (UTC)", value=datetime.utcnow().date())
adp_title = f"ATFM Daily Plan – {adp_date.isoformat()}"

def build_adp_docx(airport_df, overflights_df, cap_df, notam_df, route_weather) -> BytesIO:
    doc = Document()
    # Title
    title = doc.add_paragraph(adp_title)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.runs[0].font.size = Pt(16)
    title.runs[0].bold = True

    doc.add_paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%MZ')}")

    # Airport Traffic
    doc.add_paragraph().add_run("Airport Traffic").bold = True
    if airport_df is not None and not airport_df.empty:
        table = doc.add_table(rows=1, cols=3)
        hdr = table.rows[0].cells
        hdr[0].text = "Airport"
        hdr[1].text = "Arrivals"
        hdr[2].text = "Departures"
        for _, r in airport_df.iterrows():
            row = table.add_row().cells
            row[0].text = str(r["Airport"])
            row[1].text = str(int(r["Arrivals"]))
            row[2].text = str(int(r["Departures"]))
    else:
        doc.add_paragraph("No airport traffic found.")

    # Overflights
    doc.add_paragraph().add_run("Hourly Overflights (UTC)").bold = True
    if overflights_df is not None and not overflights_df.empty:
        table = doc.add_table(rows=1, cols=2)
        hdr = table.rows[0].cells
        hdr[0].text = "Period (UTC)"
        hdr[1].text = "Overflights"
        for _, r in overflights_df.iterrows():
            row = table.add_row().cells
            row[0].text = str(r["Period (UTC)"])
            row[1].text = str(int(r["Overflights"]))
    else:
        doc.add_paragraph("No hourly overflights table found.")

    # Capacity
    doc.add_paragraph().add_run("Sector Capacity & Utilization").bold = True
    table = doc.add_table(rows=1, cols=4)
    hdr = table.rows[0].cells
    hdr[0].text = "Sector"
    hdr[1].text = "Capacity (acft/hr)"
    hdr[2].text = "Estimated Peak Demand"
    hdr[3].text = "Utilization"
    for _, r in cap_df.iterrows():
        row = table.add_row().cells
        row[0].text = str(r["Sector"])
        row[1].text = str(int(r["Capacity (acft/hr)"]))
        row[2].text = str(int(r["Estimated Peak Demand"]))
        row[3].text = f"{float(r['Utilization'])*100:.0f}%"

    # NOTAMs
    doc.add_paragraph().add_run("NOTAMs – Summary").bold = True
    if not notams:
        doc.add_paragraph("No NOTAM-like items found.")
    else:
        for n in notams:
            p = doc.add_paragraph(f"- [{n['Category']}] {n['Airport'] or '-'}: {n['Text']}")

    # Route Weather
    doc.add_paragraph().add_run("Route Weather (Tasmi→Kaban / MODIK→SIDAD)").bold = True
    for rname, items in route_weather.items():
        doc.add_paragraph(f"{rname}:")
        for win, summ in items:
            doc.add_paragraph(f"  • {win}: {summ}")

    # Footer
    doc.add_paragraph()
    doc.add_paragraph("Prepared by: ATFM Iraq").alignment = WD_ALIGN_PARAGRAPH.RIGHT

    out = BytesIO()
    doc.save(out)
    out.seek(0)
    return out

gen_btn = st.button("Generate ADP DOCX")
if gen_btn:
    notam_df = pd.DataFrame(notams) if notams else pd.DataFrame(columns=["Airport","Category","Action","Text"])
    adp_file = build_adp_docx(airport_df, overflights_df, cap_df, notam_df, route_weather)
    st.download_button(
        label="⬇️ Download ADP (DOCX)",
        data=adp_file,
        file_name=f"ATFM_ADP_{adp_date.isoformat()}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

# -----------------------------
# REFRESH
# -----------------------------
st.divider()
if st.button("🔄 Refresh now"):
    st.rerun()
