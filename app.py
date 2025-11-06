import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from io import BytesIO
from datetime import datetime
from docx import Document
import re

# ------------------------------------------------------------
# APP CONFIG
# ------------------------------------------------------------
st.set_page_config(page_title="ATFM IRAQ – GCANS ADP", layout="wide")
PASSWORD = "atfmiraqmm"
GOOGLE_DOC_URL = "https://docs.google.com/document/d/1PUtfstGvw8PhKWbnOOvlBjCa7wJJX-nM/export?format=docx"

SECTOR_CAPACITY = {
    "South/TASMI": 26,
    "North/RATVO": 27,
}

AIRPORT_ORDER = ["ORBI/BGW", "ORBM/OSM", "ORER/EBL", "ORKK/KIK", "ORMM/BSR", "ORNI/NJF"]

# ------------------------------------------------------------
# AUTH (login disappears after success)
# ------------------------------------------------------------
if "auth" not in st.session_state:
    st.session_state.auth = False

st.title("🇮🇶 ATFM IRAQ – GCANS-IRAQ Daily ATFM Plan Generator")

if not st.session_state.auth:
    with st.form("login"):
        _u = st.text_input("Username (any):")
        _p = st.text_input("Password:", type="password")
        ok = st.form_submit_button("Login")
    if ok:
        if _p == PASSWORD:
            st.session_state.auth = True
            st.experimental_rerun()
        else:
            st.error("Incorrect password.")
            st.stop()
else:
    st.success("✅ Authentication successful")

# ------------------------------------------------------------
# DOWNLOAD LATEST DOCX FROM GOOGLE DOC
# ------------------------------------------------------------
with st.spinner("Downloading latest ATFM file from Google Drive…"):
    try:
        r = requests.get(GOOGLE_DOC_URL, timeout=30)
        r.raise_for_status()
        DOC_BYTES = BytesIO(r.content)
        st.caption("✅ File downloaded successfully")
    except Exception as e:
        st.error(f"❌ Unable to download file: {e}")
        st.stop()

# ------------------------------------------------------------
# READ DOC → TEXT + LINES
# ------------------------------------------------------------
def read_docx_text(buff: BytesIO) -> str:
    doc = Document(buff)
    return "\n".join(p.text for p in doc.paragraphs)

raw_text = read_docx_text(DOC_BYTES)
# normalize unicode dashes to a single en dash
raw_text = raw_text.replace("—", "–").replace("-", "-")
lines = [ln.rstrip() for ln in raw_text.splitlines()]

# ------------------------------------------------------------
# HELPERS: block extraction by headings
# ------------------------------------------------------------
def extract_between(text: str, start_key: str, end_key: str) -> str:
    s = text.find(start_key)
    if s == -1:
        return ""
    e = text.find(end_key, s + len(start_key)) if end_key else -1
    if e == -1:
        return text[s:].strip()
    return text[s:e].strip()

# ------------------------------------------------------------
# 1) AIRSPACE (ORBB): block between "Airspace:" and "Airports:"
# ------------------------------------------------------------
airspace_block = extract_between(raw_text, "Airspace:", "Airports:")

# ------------------------------------------------------------
# 2) AIRPORTS: parse blocks under "Airports:" heading
# headers look like ORXX/XXX; content is free text until next header or new section
# ------------------------------------------------------------
def extract_airports(text: str) -> dict:
    airports = {}
    airports_section = extract_between(text, "Airports:", "Predicted Demand")
    if not airports_section:
        # if "Predicted Demand" title changed, fall back to end of document
        airports_section = extract_between(text, "Airports:", "")
    sect_lines = [l.strip() for l in airports_section.splitlines()]

    header_re = re.compile(r"^OR[A-Z]{2}/[A-Z]{3}$")
    current = None
    buf = []
    for l in sect_lines:
        if header_re.match(l):
            if current and buf:
                airports[current] = "\n".join(buf).strip()
            current = l
            buf = []
        else:
            if current is not None:
                buf.append(l)
    if current and buf:
        airports[current] = "\n".join(buf).strip()
    return airports

airport_blocks = extract_airports(raw_text)

# ------------------------------------------------------------
# 3) PREDICTED DEMAND (Hourly OVF) – two-line format:
# e.g. "0000–0100" (or "0000-0100") on one line, value on the next line
# ------------------------------------------------------------
def extract_demand_from_lines(lines_list) -> pd.DataFrame:
    rows = []
    # accept en dash or hyphen
    dash = r"[–-]"
    period_re = re.compile(rf"^\s*(\d{{4}}){dash}(\d{{4}})\s*$")
    i = 0
    while i < len(lines_list):
        m = period_re.match(lines_list[i])
        if m:
            period = f"{m.group(1)}–{m.group(2)}"
            # read next non-empty line as value if numeric
            j = i + 1
            while j < len(lines_list) and lines_list[j].strip() == "":
                j += 1
            if j < len(lines_list) and re.match(r"^\d+$", lines_list[j].strip()):
                val = int(lines_list[j].strip())
                rows.append({"Period (UTC)": period, "Overflights": val})
                i = j + 1
                continue
        i += 1
    return pd.DataFrame(rows)

demand_df = extract_demand_from_lines(lines)

# ------------------------------------------------------------
# UI: AIRSPACE
# ------------------------------------------------------------
st.header("🛰️ ORBB – Airspace Information")
if airspace_block.strip():
    st.write(airspace_block)
else:
    st.info("No Airspace information found (expected a section starting with **Airspace:**).")

# ------------------------------------------------------------
# UI: AIRPORTS (one by one in fixed order)
# ------------------------------------------------------------
st.header("🛬 Airport Information (One by One)")
for ap in AIRPORT_ORDER:
    with st.expander(ap, expanded=(ap == "ORBI/BGW")):
        content = airport_blocks.get(ap, "").strip()
        if content:
            st.write(content)
        else:
            st.info("No information found in the source document for this airport.")

# ------------------------------------------------------------
# UI: PREDICTED DEMAND (Hourly)
# ------------------------------------------------------------
st.header("📈 Predicted Hourly Demand (Overflights)")
if demand_df.empty:
    st.warning("Predicted Demand table not found (expected 2-line pairs like `0000–0100` then a number).")
else:
    st.dataframe(demand_df, use_container_width=True)
    # nicer order by start time
    def period_key(p):
        try:
            return int(p.split("–")[0])
        except:
            return 0
    demand_df_sorted = demand_df.sort_values(by="Period (UTC)", key=lambda s: s.map(period_key))
    fig = px.area(
        demand_df_sorted,
        x="Period (UTC)",
        y="Overflights",
        title="Hourly Predicted Overflights",
        markers=True,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.metric("Peak Hour Demand", int(demand_df_sorted["Overflights"].max()))

# ------------------------------------------------------------
# UI: SECTOR CAPACITY & UTILIZATION (improved)
# ------------------------------------------------------------
st.header("📊 Sector Capacity & Utilization")
peak = int(demand_df["Overflights"].max()) if not demand_df.empty else 0
cap_rows = []
for sector, cap in SECTOR_CAPACITY.items():
    util = round((peak / cap) * 100, 1) if cap > 0 else 0.0
    cap_rows.append({"Sector": sector, "Capacity (acft/hr)": cap, "Peak Demand": peak, "Utilization %": util})
cap_df = pd.DataFrame(cap_rows)
st.dataframe(cap_df, use_container_width=True)

gfig = go.Figure()
for _, r in cap_df.iterrows():
    gfig.add_trace(go.Indicator(
        mode="gauge+number",
        value=float(r["Utilization %"]),
        title={"text": r["Sector"]},
        gauge={
            "axis": {"range": [0, 150]},
            "bar": {"thickness": 0.4},
            "steps": [
                {"range": [0, 80], "color": "#e6f4ea"},
                {"range": [80, 100], "color": "#fff4e6"},
                {"range": [100, 150], "color": "#fdecea"},
            ],
        },
    ))
gfig.update_layout(height=380, margin=dict(l=0, r=0, t=40, b=0))
st.plotly_chart(gfig, use_container_width=True)

# ------------------------------------------------------------
# UI: ATFM MEASURES (fixed daily content)
# ------------------------------------------------------------
st.header("🧰 ATFM Measures (Applied / Available)")
st.subheader("✅ Rerouting")
st.write("Change exit points between **NINVA → KABAN** during congestion to balance flows.")

st.subheader("✅ Sectorisation (Time Windows & Levels)")
sector_table = pd.DataFrame([
    ["0530–0730 UTC", "South Sector", "South Low / South High", "FL240–350 / FL360–460", "Increase sector capacity"],
    ["0600–0800 UTC", "North Sector", "North Low / North High", "FL240–350 / FL360–460", "Increase sector capacity"],
    ["1200–1400 UTC", "South Sector", "South Low / South High", "FL240–350 / FL360–460", "Increase sector capacity"],
    ["1200–1400 UTC", "North Sector", "North Low / North High", "FL240–350 / FL360–460", "Increase sector capacity"],
    ["2330–0130 UTC", "South Sector", "South Low / South High", "FL240–350 / FL360–460", "Increase sector capacity"],
    ["0000–0200 UTC", "North Sector", "North Low / North High", "FL240–350 / FL360–460", "Increase sector capacity"],
], columns=["Period (UTC)", "Sector", "Configuration", "Flight Levels", "Reason"])
st.dataframe(sector_table, use_container_width=True)

# ------------------------------------------------------------
# UI: CDM – professional guidance
# ------------------------------------------------------------
st.header("🤝 CDM (Collaborative Decision Making) – Daily Guidance")
st.write("""
**Objectives:** shared situational awareness, predictable operations, and rapid recovery.

**Practices for today:**
- Publish a common pre-tactical plan to ACC/ATFMU, airlines, airports, and MET.
- Update stakeholders on **sectorisation windows**, expected **peak hour**, and **rerouting (NINVA→KABAN)**.
- Confirm airport readiness (stands, gates, de-icing if needed) for the predicted peaks.
- Agree **trigger thresholds**: when to extend/terminate sectorisation or rerouting.
- Run **post-ops**: log delays (root cause), hotspots, and capacity shortfall for tomorrow’s plan.
""")

# ------------------------------------------------------------
# UI: What’s missing for a pro ATFM/CDM page
# ------------------------------------------------------------
st.header("🧩 Missing for a Complete Professional ATFM/CDM Platform")
st.write("""
1) Real-time traffic feed (ADS-B/OpenSky) layered on sectors/routes  
2) Automatic capacity estimator per sector with live updates  
3) MET integration (SIGWX/CB/jetstream impact) with route-level risk  
4) STAM suggestions (MIT/MINIT/level capping/reroutes) with “what-if”  
5) Slot/CTOT monitoring and A-CDM milestone integration  
6) Historical analytics for delay attribution and hotspot prediction
""")

# ------------------------------------------------------------
# ADP (DOCX) generator
# ------------------------------------------------------------
st.header("🧾 Generate ATFM Daily Plan (DOCX)")
adp_date = st.date_input("ADP Date (UTC)", value=datetime.utcnow().date())

def build_adp_docx() -> BytesIO:
    doc = Document()

    # Title
    title = doc.add_paragraph(f"ATFM Daily Plan – {adp_date.isoformat()}")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER  # type: ignore

    # Provider & Generation time
    p = doc.add_paragraph("Service Provider: GCANS-IRAQ")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER  # type: ignore
    doc.add_paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%MZ')}")

    # Airspace
    doc.add_paragraph().add_run("Airspace Information").bold = True
    doc.add_paragraph(airspace_block if airspace_block else "N/A")

    # Airports
    doc.add_paragraph().add_run("Airports").bold = True
    for ap in AIRPORT_ORDER:
        doc.add_paragraph(ap)
        doc.add_paragraph(airport_blocks.get(ap, "No information found."))

    # Demand
    doc.add_paragraph().add_run("Predicted Demand (Hourly Overflights)").bold = True
    if demand_df.empty:
        doc.add_paragraph("No demand table found.")
    else:
        t = doc.add_table(rows=1, cols=2)
        hdr = t.rows[0].cells
        hdr[0].text = "Period (UTC)"
        hdr[1].text = "Overflights"
        # sort by start time
        d_sorted = demand_df.sort_values(by="Period (UTC)")
        for _, row in d_sorted.iterrows():
            rr = t.add_row().cells
            rr[0].text = row["Period (UTC)"]
            rr[1].text = str(int(row["Overflights"]))

    # Capacity & Utilization
    doc.add_paragraph().add_run("Sector Capacity & Utilization").bold = True
    for _, r in pd.DataFrame(cap_rows := [
        {"Sector": s, "Cap": c, "Peak": peak, "Util": (round((peak / c) * 100, 1) if c else 0.0)}
        for s, c in SECTOR_CAPACITY.items()
    ]).iterrows():
        doc.add_paragraph(f"{r['Sector']}: Peak {int(r['Peak'])} / Cap {int(r['Cap'])} → Util {r['Util']}%")

    # ATFM Measures
    doc.add_paragraph().add_run("ATFM Measures").bold = True
    doc.add_paragraph("Rerouting: Change exit points NINVA→KABAN during congestion.")
    doc.add_paragraph("Sectorisation windows:")
    for _, r in sector_table.iterrows():
        doc.add_paragraph(f"- {r['Period (UTC)']} {r['Sector']} ({r['Configuration']}, {r['Flight Levels']}) – {r['Reason']}")

    # CDM
    doc.add_paragraph().add_run("CDM – Daily Guidance").bold = True
    doc.add_paragraph(
        "Share the pre-tactical plan, confirm thresholds for extending/terminating measures, "
        "coordinate airport readiness for peaks, and run post-ops analysis for tomorrow."
    )

    # Footer
    f = doc.add_paragraph("This app built and supervised by MM and CU.")
    f.alignment = WD_ALIGN_PARAGRAPH.CENTER  # type: ignore

    out = BytesIO()
    doc.save(out)
    out.seek(0)
    return out

if st.button("Generate ADP (DOCX)"):
    file = build_adp_docx()
    st.download_button(
        "⬇️ Download ADP (DOCX)",
        data=file,
        file_name=f"ATFM_ADP_{adp_date.isoformat()}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

st.divider()
st.caption("GCANS-IRAQ — App built & supervised by **MM** and **CU**")
