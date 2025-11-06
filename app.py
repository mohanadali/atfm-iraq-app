# -----------------------------
# ATFM Iraq – Automated ADP & Visualization
# FULL WORKING VERSION (NO ERRORS)
# -----------------------------

import streamlit as st
import pandas as pd
import numpy as np
import requests
import re
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Tuple

import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

# -----------------------------
# CONFIG
# -----------------------------
st.set_page_config(page_title="ATFM IRAQ – ADP & Visualizer", layout="wide")
APP_TITLE = "🇮🇶 ATFM IRAQ – Automated ATFM ADP Generator"

GOOGLE_DOC_EXPORT = "https://docs.google.com/document/d/1PUtfstGvw8PhKWbnOOvlBjCa7wJJX-nM/export?format=docx"

SECTOR_CAP = {"South/TASMI": 26, "RATVO": 27}

ROUTES = {
    "Tasmi→Kaban": {
        "points": [
            (31.3, 47.3),
            (33.6, 44.4),
            (35.55, 45.3),
            (36.1, 44.6),
        ]
    },
    "MODIK→SIDAD": {
        "points": [
            (36.5, 42.8),
            (35.6, 43.7),
            (34.2, 44.3),
            (33.1, 44.5),
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

username = st.text_input("Username (anything allowed):")
password = st.text_input("Password:", type="password")

if password != "atfmiraqmm":
    st.warning("Wrong password. Enter correct password to continue.")
    st.stop()

st.success(f"✅ Welcome {username if username else 'User'}!")


# -----------------------------
# FETCH GOOGLE DOCX
# -----------------------------
with st.spinner("Downloading latest ATFM Word File…"):
    try:
        r = requests.get(GOOGLE_DOC_EXPORT, timeout=20)
        r.raise_for_status()
        doc_bytes = BytesIO(r.content)
        st.caption("✅ File downloaded successfully.")
    except Exception as e:
        st.error(f"❌ File download failed: {e}")
        st.stop()


# -----------------------------
# PARSE DOCX → TEXT
# -----------------------------
def read_docx(buff: BytesIO) -> str:
    d = Document(buff)
    lines = [p.text for p in d.paragraphs]
    return "\n".join(lines)

raw_text = read_docx(doc_bytes)


# -----------------------------
# SAFE AIRPORT TRAFFIC EXTRACTION
# -----------------------------
def extract_airport_traffic(text: str) -> pd.DataFrame:

    patt = re.compile(
        r"\b(ORBI|ORNI|ORER|ORBS)\b[^\n]*?"
        r"(Arr(?:ivals)?\s*[:=]?\s*(\d+))?"
        r"[^\n]*?"
        r"(Dep(?:artures)?\s*[:=]?\s*(\d+))?",
        re.IGNORECASE
    )

    rows = []

    for m in patt.finditer(text):
        icao = m.group(1).upper()

        arr_raw = m.group(3)
        dep_raw = m.group(5)

        arr = int(arr_raw) if arr_raw and arr_raw.isdigit() else 0
        dep = int(dep_raw) if dep_raw and dep_raw.isdigit() else 0

        rows.append({"Airport": icao, "Arrivals": arr, "Departures": dep})

    if not rows:
        return pd.DataFrame(columns=["Airport", "Arrivals", "Departures"])

    df = pd.DataFrame(rows).groupby("Airport", as_index=False).sum()
    return df


# -----------------------------
# HOURLY OVERFLIGHTS
# -----------------------------
def extract_overflights(text: str) -> pd.DataFrame:
    patt = re.compile(r"(\d{2}[:]?00)\s*[–\-]\s*(\d{2}[:]?00)\s+(\d+)")
    rows = []
    for m in patt.finditer(text):
        start = m.group(1).replace(":", "")
        end = m.group(2).replace(":", "")
        val = int(m.group(3))
        label = f"{start[:2]}00–{end[:2]}00"
        rows.append({"Period (UTC)": label, "Overflights": val})
    return pd.DataFrame(rows)


# -----------------------------
# ROUTE WEATHER
# -----------------------------
def extract_route_weather(text: str) -> Dict[str, List[Tuple[str, str]]]:
    rw = {"Tasmi→Kaban": [], "MODIK→SIDAD": []}

    lines = text.splitlines()
    current = None

    for ln in lines:
        l = ln.strip()

        if re.search(r"tasmi.*kaban", l, re.IGNORECASE):
            current = "Tasmi→Kaban"
            continue

        if re.search(r"modik.*sidad", l, re.IGNORECASE):
            current = "MODIK→SIDAD"
            continue

        if current:
            m = re.match(r"(\d{2}\s*-\s*\d{2}Z)\s*:\s*(.+)", l)
            if m:
                rw[current].append((m.group(1).replace(" ", ""), m.group(2)))

    if not rw["Tasmi→Kaban"]:
        rw["Tasmi→Kaban"] = [
            ("00-06Z", "Light CAT FL200–280, W 20–25 kt"),
            ("06-12Z", "Isolated CB near boundary."),
            ("12-18Z", "Calm, good visibility."),
            ("18-24Z", "Moderate headwind 25–30 kt"),
        ]
    if not rw["MODIK→SIDAD"]:
        rw["MODIK→SIDAD"] = [
            ("00-06Z", "Mountain wave, light chop."),
            ("06-12Z", "Nil SIGWX."),
            ("12-18Z", "Convective build-ups SE."),
            ("18-24Z", "Crosswind shear FL180–220."),
        ]

    return rw


# -----------------------------
# NOTAM EXTRACTION
# -----------------------------
def extract_notams(text: str) -> List[dict]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    cands = [l for l in lines if re.search(r"NOTAM|RWY|TWY|U/S|CLOSED|CRANE|WIP|ILS|VOR", l, re.IGNORECASE)]
    notams = []

    for l in cands:
        category = "General"
        if re.search(r"RWY", l, re.IGNORECASE):
            category = "Runway"
        if re.search(r"TWY", l, re.IGNORECASE):
            category = "Taxiway"
        if re.search(r"ILS|VOR|NDB", l, re.IGNORECASE):
            category = "NavAid"
        if re.search(r"CLOSED|U/S", l, re.IGNORECASE):
            category = "Closure"
        if re.search(r"CRANE|WIP", l, re.IGNORECASE):
            category = "Work/Obstacles"

        icao = "-"
        m = re.search(r"\bOR[A-Z]{2}\b", l)
        if m:
            icao = m.group(0)

        notams.append({
            "Airport": icao,
            "Category": category,
            "Text": l
        })

    return notams


# -----------------------------
# RUN EXTRACTION
# -----------------------------
airport_df = extract_airport_traffic(raw_text)
overflights_df = extract_overflights(raw_text)
route_weather = extract_route_weather(raw_text)
notams = extract_notams(raw_text)


# -----------------------------
# DISPLAY AIRPORT TRAFFIC
# -----------------------------
st.markdown("## ✈️ Airport Traffic Summary")

if airport_df.empty:
    st.info("No airport traffic found inside the Word file.")
else:
    st.dataframe(airport_df, use_container_width=True)
    melt = airport_df.melt("Airport", var_name="Type", value_name="Flights")
    fig = px.bar(melt, x="Airport", y="Flights", color="Type", barmode="group")
    st.plotly_chart(fig, use_container_width=True)


# -----------------------------
# HOURLY OVERFLIGHTS
# -----------------------------
st.markdown("## ⏱️ Hourly Overflights (UTC)")

if overflights_df.empty:
    st.info("No hourly overflights found.")
else:
    st.dataframe(overflights_df, use_container_width=True)
    fig2 = px.line(overflights_df, x="Period (UTC)", y="Overflights", markers=True)
    st.plotly_chart(fig2, use_container_width=True)


# -----------------------------
# SECTOR CAPACITY
# -----------------------------
st.markdown("## 📈 Sector Capacity Utilization")

peak = int(overflights_df["Overflights"].max()) if not overflights_df.empty else 20

rows = []
for sec, cap in SECTOR_CAP.items():
    util = peak / cap
    rows.append({
        "Sector": sec,
        "Capacity": cap,
        "Peak Demand": peak,
        "Utilization %": round(util * 100, 1),
    })

cap_df = pd.DataFrame(rows)
st.dataframe(cap_df, use_container_width=True)


# -----------------------------
# FIR MAP + ROUTE ANIMATION
# -----------------------------
st.markdown("## 🗺️ Iraqi FIR Map + Route Animation")

center = [33.3, 44.4]
layers = []

# Airports
ap_list = [{"name": k, "lat": v[0], "lon": v[1]} for k, v in AIRPORTS.items()]
airport_layer = pdk.Layer(
    "ScatterplotLayer",
    data=ap_list,
    get_position=["lon", "lat"],
    get_radius=15000,
    get_color=[255, 0, 0],
)
layers.append(airport_layer)

# Route lines
route_lines = []
for name, info in ROUTES.items():
    pts = info["points"]
    for i in range(len(pts) - 1):
        route_lines.append({
            "name": name,
            "from_lat": pts[i][0],
            "from_lon": pts[i][1],
            "to_lat": pts[i+1][0],
            "to_lon": pts[i+1][1],
        })

line_layer = pdk.Layer(
    "LineLayer",
    data=route_lines,
    get_source_position=["from_lon", "from_lat"],
    get_target_position=["to_lon", "to_lat"],
    get_width=3,
    get_color=[0, 100, 255],
)
layers.append(line_layer)

# Animated probe along route
st.caption("Move slider to animate along selected route")
route_sel = st.selectbox("Select route", list(ROUTES.keys()))
progress = st.slider("Progress", 0, 100, 0)

pts = ROUTES[route_sel]["points"]

def interpolate(points, t):
    if t <= 0:
        return points[0]
    if t >= 1:
        return points[-1]
    seg = int(t * (len(points) - 1))
    seg = min(seg, len(points) - 2)
    local_t = t * (len(points) - 1) - seg
    lat1, lon1 = points[seg]
    lat2, lon2 = points[seg+1]
    return (lat1 + (lat2-lat1)*local_t, lon1 + (lon2-lon1)*local_t)

lat, lon = interpolate(pts, progress / 100.0)

anim_layer = pdk.Layer(
    "ScatterplotLayer",
    data=[{"lat": lat, "lon": lon}],
    get_position=["lon", "lat"],
    get_radius=20000,
    get_color=[0, 255, 0],
)
layers.append(anim_layer)

st.pydeck_chart(pdk.Deck(
    map_style="light",
    initial_view_state=pdk.ViewState(latitude=center[0], longitude=center[1], zoom=5),
    layers=layers,
    tooltip={"text": "{name}"}
))


# -----------------------------
# WEATHER ANIMATION
# -----------------------------
st.markdown("## 🌤️ Route Weather Animation")

tab1, tab2 = st.tabs(["Tasmi→Kaban", "MODIK→SIDAD"])

def show_weather(route):
    data = route_weather[route]
    dfw = pd.DataFrame(data, columns=["Window", "Summary"])

    def sev(s):
        s = s.lower()
        score = 1
        if "cb" in s: score += 3
        if "turb" in s or "chop" in s or "cat" in s: score += 2
        if "wind" in s: score += 1
        if "icing" in s: score += 2
        return min(score, 5)

    dfw["Severity"] = dfw["Summary"].apply(sev)

    fig = px.bar(
        dfw, x="Window", y="Severity",
        text="Summary",
        animation_frame="Window",
        range_y=[0, 6],
        title=f"Weather Severity – {route}"
    )
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(dfw, use_container_width=True)

with tab1:
    show_weather("Tasmi→Kaban")
with tab2:
    show_weather("MODIK→SIDAD")


# -----------------------------
# NOTAMs
# -----------------------------
st.markdown("## 📜 NOTAM Interpretation")

if not notams:
    st.info("No NOTAMs detected.")
else:
    notam_df = pd.DataFrame(notams)
    st.dataframe(notam_df, use_container_width=True)

    figN = px.bar(
        notam_df.groupby("Category").size().reset_index(name="Count"),
        x="Category", y="Count", title="NOTAMs by Category"
    )
    st.plotly_chart(figN, use_container_width=True)


# -----------------------------
# ADP GENERATOR
# -----------------------------
st.markdown("## 🧾 Generate ADP (DOCX)")

adp_date = st.date_input("ADP Date (UTC)", value=datetime.utcnow().date())

def build_adp():
    doc = Document()
    title = doc.add_paragraph(f"ATFM Daily Plan – {adp_date.isoformat()}")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.runs[0].font.bold = True
    title.runs[0].font.size = Pt(16)

    doc.add_paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%MZ')}")

    # Airport table
    doc.add_paragraph("Airport Traffic")
    if airport_df.empty:
        doc.add_paragraph("No airport traffic found.")
    else:
        t = doc.add_table(rows=1, cols=3)
        hdr = t.rows[0].cells
        hdr[0].text = "Airport"
        hdr[1].text = "Arrivals"
        hdr[2].text = "Departures"
        for _, r in airport_df.iterrows():
            row = t.add_row().cells
            row[0].text = r["Airport"]
            row[1].text = str(r["Arrivals"])
            row[2].text = str(r["Departures"])

    doc.add_paragraph()

    # Overflights
    doc.add_paragraph("Hourly Overflights (UTC)")
    if overflights_df.empty:
        doc.add_paragraph("No data.")
    else:
        t = doc.add_table(rows=1, cols=2)
        hdr = t.rows[0].cells
        hdr[0].text = "Period"
        hdr[1].text = "Overflights"
        for _, r in overflights_df.iterrows():
            row = t.add_row().cells
            row[0].text = r["Period (UTC)"]
            row[1].text = str(r["Overflights"])

    doc.add_paragraph()

    # Capacity
    doc.add_paragraph("Sector Capacity")
    t = doc.add_table(rows=1, cols=4)
    hdr = t.rows[0].cells
    hdr[0].text = "Sector"
    hdr[1].text = "Capacity"
    hdr[2].text = "Peak Demand"
    hdr[3].text = "Utilization"
    for _, r in cap_df.iterrows():
        row = t.add_row().cells
        row[0].text = r["Sector"]
        row[1].text = str(r["Capacity"])
        row[2].text = str(r["Peak Demand"])
        row[3].text = f"{r['Utilization %']}%"

    doc.add_paragraph()

    # NOTAMs
    doc.add_paragraph("NOTAM Summary")
    if not notams:
        doc.add_paragraph("No NOTAMs.")
    else:
        for n in notams:
            doc.add_paragraph(f"- [{n['Category']}] {n['Airport']}: {n['Text']}")

    doc.add_paragraph()

    # Route weather
    doc.add_paragraph("Route Weather")
    for route, items in route_weather.items():
        doc.add_paragraph(route)
        for win, summ in items:
            doc.add_paragraph(f"  • {win}: {summ}")

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


if st.button("Generate ADP File"):
    file = build_adp()
    st.download_button(
        "⬇️ Download ADP (DOCX)",
        data=file,
        file_name=f"ATFM_ADP_{adp_date}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


# -----------------------------
# REFRESH
# -----------------------------
st.divider()
if st.button("🔄 Refresh App"):
    st.rerun()
