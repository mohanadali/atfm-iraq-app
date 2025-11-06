# ============================================================
# ATFM IRAQ – GCANS-IRAQ OFFICIAL DAILY PLAN AUTOMATION APP
# Built according to requirements from MM
# No logo, clean ATFM layout, Iraq FIR operational structure
# ============================================================

import streamlit as st
import pandas as pd
import numpy as np
import requests
import re
from io import BytesIO
from datetime import datetime
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

# ------------------------------------------------------------
# BASIC CONFIG
# ------------------------------------------------------------
st.set_page_config(page_title="ATFM IRAQ – GCANS ADP", layout="wide")

PASSWORD = "atfmiraqmm"
GOOGLE_DOC_URL = "https://docs.google.com/document/d/1PUtfstGvw8PhKWbnOOvlBjCa7wJJX-nM/export?format=docx"

SECTOR_CAPACITY = {
    "South/TASMI": 26,
    "North/RATVO": 27
}

# ------------------------------------------------------------
# SESSION LOGIN (and hide after success)
# ------------------------------------------------------------
if "auth" not in st.session_state:
    st.session_state.auth = False

st.title("🇮🇶 ATFM IRAQ – GCANS-IRAQ Daily ATFM Plan Generator")

if not st.session_state.auth:
    with st.form("login"):
        u = st.text_input("Username (any):")
        p = st.text_input("Password:", type="password")
        ok = st.form_submit_button("Login")

    if ok:
        if p == PASSWORD:
            st.session_state.auth = True
            st.experimental_rerun()
        else:
            st.error("Incorrect password.")
            st.stop()
else:
    st.success("✅ Authentication successful")

# ------------------------------------------------------------
# DOWNLOAD WORD FILE
# ------------------------------------------------------------
with st.spinner("Fetching the latest ATFM Word File..."):
    try:
        resp = requests.get(GOOGLE_DOC_URL, timeout=20)
        resp.raise_for_status()
        DOC_BYTES = BytesIO(resp.content)
        st.caption("✅ File downloaded successfully")
    except Exception as e:
        st.error("❌ Unable to download file")
        st.stop()

# ------------------------------------------------------------
# READ WORD FILE
# ------------------------------------------------------------
def read_doc(buff):
    doc = Document(buff)
    return "\n".join(p.text for p in doc.paragraphs)

raw = read_doc(DOC_BYTES)
lines = raw.splitlines()

# ------------------------------------------------------------
# EXTRACT AIRSPACE INFORMATION FOR ORBB
# ------------------------------------------------------------
def extract_airspace_block(text):
    block = []
    capturing = False
    for line in text.splitlines():
        if line.strip().startswith("Airspace") or "ORBB" in line:
            capturing = True
            block.append(line)
            continue
        if capturing:
            if line.strip().startswith("Airports:"):
                break
            block.append(line)
    return "\n".join(block).strip()

airspace_info = extract_airspace_block(raw)

# ------------------------------------------------------------
# EXTRACT AIRPORT INFORMATION (ORBI/BGW, ORER/EBL, ORMM/BSR, ORNI/NJF)
# ------------------------------------------------------------
def extract_airport_sections(text):
    sections = {}
    current = None
    block = []

    for line in text.splitlines():
        if re.match(r"^(ORBI|ORER|ORMM|ORNI|ORSU|ORKK|ORBM|ORBB)", line):
            if current and block:
                sections[current] = "\n".join(block).strip()
            current = line.strip().split()[0]
            block = []
        elif current:
            block.append(line)

    if current and block:
        sections[current] = "\n".join(block).strip()

    return sections

airport_sections = extract_airport_sections(raw)

# ------------------------------------------------------------
# EXTRACT PREDICTED DEMAND HOURLY TABLE
# ------------------------------------------------------------
def extract_predicted_demand(text):
    pattern = r"(\d{4})[–-](\d{4})\s+(\d+)"
    matches = re.findall(pattern, text)
    rows = []
    for s, e, v in matches:
        rows.append({
            "Period (UTC)": f"{s[:2]}00–{e[:2]}00",
            "Overflights": int(v)
        })
    return pd.DataFrame(rows)

overflights_df = extract_predicted_demand(raw)

# ------------------------------------------------------------
# DISPLAY AIRSPACE INFORMATION
# ------------------------------------------------------------
st.header("🛰️ ORBB – Airspace Information")

if airspace_info:
    st.write(airspace_info)
else:
    st.info("No Airspace Information found in the file.")

# ------------------------------------------------------------
# DISPLAY AIRPORT SECTIONS
# ------------------------------------------------------------
st.header("🛬 Airport Information (One by One)")

for key in ["ORBI", "ORER", "ORMM", "ORNI"]:
    match = [k for k in airport_sections.keys() if k.startswith(key)]
    if match:
        ap = match[0]
        with st.expander(f"{ap} Information", expanded=True if key == "ORBI" else False):
            st.write(airport_sections[ap])
    else:
        with st.expander(f"{key} Information", expanded=False):
            st.info("No information found.")

# ------------------------------------------------------------
# PREDICTED DEMAND
# ------------------------------------------------------------
st.header("📈 Predicted Hourly Demand (Overflights)")

if overflights_df.empty:
    st.warning("Predicted Demand table not found in the file.")
else:
    st.dataframe(overflights_df, use_container_width=True)

    fig = px.line(
        overflights_df,
        x="Period (UTC)",
        y="Overflights",
        markers=True,
        title="Hourly Predicted Demand",
    )
    st.plotly_chart(fig, use_container_width=True)

    peak = int(overflights_df["Overflights"].max())
    st.metric("Peak Hour Demand", peak)

# ------------------------------------------------------------
# SECTOR CAPACITY VISUALIZATION
# ------------------------------------------------------------
st.header("📊 Sector Capacity & Utilization")

if overflights_df.empty:
    estimated_peak = 20
else:
    estimated_peak = int(overflights_df["Overflights"].max())

rows = []
for sector, cap in SECTOR_CAPACITY.items():
    util = round((estimated_peak / cap) * 100, 1)
    rows.append({"Sector": sector, "Capacity": cap, "Peak Demand": estimated_peak, "Utilization %": util})

cap_df = pd.DataFrame(rows)
st.dataframe(cap_df, use_container_width=True)

# Professional gauge
gfig = go.Figure()

for idx, r in cap_df.iterrows():
    gfig.add_trace(go.Indicator(
        mode="gauge+number",
        value=r["Utilization %"],
        title={"text": r["Sector"]},
        gauge={
            "axis": {"range": [0, 150]},
            "bar": {"color": "darkblue"},
        }
    ))

gfig.update_layout(height=350)
st.plotly_chart(gfig, use_container_width=True)

# ------------------------------------------------------------
# ATFM MEASURES
# ------------------------------------------------------------
st.header("🧰 ATFM Measures Applied Today")

st.subheader("✅ Rerouting")
st.write("""
Change exit points between **NINVA → KABAN** during high congestion periods.
Improves vertical and lateral distribution of northbound flows.
""")

st.subheader("✅ Sectorisation")
st.write("""
During peak hours, Iraq ACC activates dynamic sectorisation to increase capacity:

- **South Sector**  
  - South Low: FL240–FL350  
  - South High: FL360–FL460  

- **North Sector**  
  - North Low: FL240–FL350  
  - North High: FL360–FL460  

**Purpose:** Increase sector capacity, maintain orderly traffic flow,  
reduce controller workload, and avoid tactical holding.
""")

st.subheader("✅ Sectorisation Time Windows")

st.table(pd.DataFrame([
    ["0530–0730 UTC", "South Sector", "South Low / South High", "FL240–350 / FL360–460"],
    ["0600–0800 UTC", "North Sector", "North Low / North High", "FL240–350 / FL360–460"],
    ["1200–1400 UTC", "South Sector", "South Low / South High", "FL240–350 / FL360–460"],
    ["1200–1400 UTC", "North Sector", "North Low / North High", "FL240–350 / FL360–460"],
    ["2330–0130 UTC", "South Sector", "South Low / South High", "FL240–350 / FL360–460"],
    ["0000–0200 UTC", "North Sector", "North Low / North High", "FL240–350 / FL360–460"],
], columns=["Period (UTC)", "Sector", "Configuration", "Flight Levels"])
)

# ------------------------------------------------------------
# PROFESSIONAL CDM SECTION
# ------------------------------------------------------------
st.header("🤝 CDM – Collaborative Decision Making")

st.write("""
A professional **CDM (Collaborative Decision Making)** process ensures efficiency and predictability  
across the Baghdad FIR and all Iraqi airports.

### ✅ Key CDM Components:
- **Shared situational awareness** between ACC, ATFM-U, airports, airlines, and MET services  
- **Agreed tactical plan** including regulations, reroutes, and sectorisation  
- **Continuous information exchange** (capacity changes, weather impact, staffing, NOTAM updates)  
- **Pre-tactical review** of demand–capacity imbalances  
- **Tactical mitigation** through rerouting, level capping, or traffic metering  
- **Post-operations analysis** (delay causes, sector loads, hotspots)

### ✅ Benefits of CDM:
- Reduces airborne holding  
- Increases predictability for operators  
- Allows faster recovery from disruptions  
- Enhances safety by reducing tactical conflicts  
""")

# ------------------------------------------------------------
# WHAT IS STILL MISSING SECTION
# ------------------------------------------------------------
st.header("🧩 Missing Elements for a Complete Professional ATFM/CDM Platform")

st.write("""
Even though this app provides a strong daily ATFM planning foundation,  
these elements are required for a **fully professional ATFM/CDM system**:

### ✅ Missing Components:
1. **Real-time traffic feed** (OpenSky / ADS-B) integrated with ATFM logic  
2. **Automated capacity calculation engine** for each sector and airport  
3. **Delay attribution calculator** (weather, capacity, system, airline, ATC)  
4. **Dynamic regulation proposal tool** (GDP, MIT, MINIT, reroutes)  
5. **STAM (Short-Term ATFM Measures)** automated suggestions  
6. **Live MET integration** including SIGWX, CB, wind/jetstream impact  
7. **System-to-system CDM integration** with airports (A-CDM milestones)  
8. **Historical performance analytics** dashboard  
9. **ATFM slot monitoring tool** (CTOT, TTOT, ATOT)  
10. **Full NOTAM parsing engine** with categorization & automatic impact scoring  

This app is a **solid daily plan generator**, but a complete ATFM/CDM  
solution requires continuous data-driven automation and interoperability.
""")

# ------------------------------------------------------------
# ADP GENERATION
# ------------------------------------------------------------
st.header("🧾 Generate ATFM Daily Plan (DOCX)")

adp_date = st.date_input("ADP Date", value=datetime.utcnow().date())

def build_doc():
    doc = Document()

    title = doc.add_paragraph(f"ATFM Daily Plan – {adp_date.isoformat()}")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.runs[0].bold = True
    title.runs[0].font.size = Pt(16)

    provider = doc.add_paragraph("Service Provider: GCANS-IRAQ")
    provider.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%MZ')}")

    # Airspace
    doc.add_paragraph().add_run("Airspace Information").bold = True
    doc.add_paragraph(airspace_info if airspace_info else "N/A")

    # Airport info
    doc.add_paragraph().add_run("Airport Information").bold = True
    for a, b in airport_sections.items():
        doc.add_paragraph(f"{a}:")
        doc.add_paragraph(b)

    # Demand
    doc.add_paragraph().add_run("Predicted Demand").bold = True
    if overflights_df.empty:
        doc.add_paragraph("No data.")
    else:
        t = doc.add_table(rows=1, cols=2)
        hdr = t.rows[0].cells
        hdr[0].text = "Period (UTC)"
        hdr[1].text = "Overflights"
        for _, r in overflights_df.iterrows():
            row = t.add_row().cells
            row[0].text = r["Period (UTC)"]
            row[1].text = str(r["Overflights"])

    # Capacity
    doc.add_paragraph().add_run("Sector Capacity & Utilization").bold = True
    for _, r in cap_df.iterrows():
        doc.add_paragraph(f"{r['Sector']}: {r['Utilization %']}% utilization")

    # ATFM Measures
    doc.add_paragraph().add_run("ATFM Measures").bold = True
    doc.add_paragraph("Rerouting: NINVA → KABAN during congestion")
    doc.add_paragraph("Sectorisation: North Low/High & South Low/High as needed FL240–460")

    # CDM
    doc.add_paragraph().add_run("CDM Notes").bold = True
    doc.add_paragraph("Shared situational awareness, tactical updates, operator coordination.")

    # Footer
    foot = doc.add_paragraph("This app was built and supervised by MM and CU.")
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf

if st.button("Generate DOCX"):
    file = build_doc()
    st.download_button(
        "⬇️ Download ADP (DOCX)",
        data=file,
        file_name=f"ATFM_ADP_{adp_date.isoformat()}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

# ------------------------------------------------------------
# FOOTER
# ------------------------------------------------------------
st.markdown("---")
st.caption("GCANS-IRAQ — App built & supervised by **MM** and **CU**")
