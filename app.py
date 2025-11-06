import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests, re
from io import BytesIO
from datetime import datetime
from docx import Document
import pdfplumber

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="ATFM IRAQ – GCANS ADP (PDF Source)", layout="wide")
PASSWORD = "atfmiraqmm"

# <<< PUT YOUR PDF LINK HERE >>>
PDF_VIEW_LINK = "https://drive.google.com/file/d/1g_f_vBXlv2QF9_b4QuNui_d6QiOF3ifS/view?usp=sharing"

SECTOR_CAPACITY = {"South/TASMI": 26, "North/RATVO": 27}
AIRPORT_ORDER = ["ORBI/BGW", "ORBM/OSM", "ORER/EBL", "ORKK/KIK", "ORMM/BSR", "ORNI/NJF"]

# =========================
# AUTH
# =========================
if "auth" not in st.session_state:
    st.session_state.auth = False

st.title("🇮🇶 ATFM IRAQ – GCANS-IRAQ Daily ATFM Plan Generator (PDF)")

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

# =========================
# HELPERS
# =========================
def drive_view_to_download_url(view_url: str) -> str | None:
    """
    Convert a Google Drive 'view' link into a direct download link.
    Example in:  https://drive.google.com/file/d/<FILEID>/view?usp=sharing
    Example out: https://drive.google.com/uc?export=download&id=<FILEID>
    """
    m = re.search(r"/file/d/([A-Za-z0-9_-]{20,})/view", view_url)
    if not m:
        return None
    file_id = m.group(1)
    return f"https://drive.google.com/uc?export=download&id={file_id}"

def fetch_bytes(url: str) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content

def pdf_to_lines(pdf_bytes: bytes) -> list[str]:
    """
    Extract text from PDF (all pages) into a flat list of lines.
    """
    lines: list[str] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            # Split on newline, keep order
            for ln in text.splitlines():
                # normalize dashes and spaces
                ln = ln.replace("\u2013", "–").replace("\u2014", "–").replace("\u00A0", " ")
                lines.append(ln.rstrip())
    # Remove trailing empty duplicates (optional)
    return lines

def extract_between_block(lines: list[str], start_key: str, end_key: str) -> str:
    """
    Return the text block between a line that equals start_key and a line that equals end_key.
    Matches exact (case-sensitive) heading lines.
    """
    start_idx, end_idx = -1, -1
    for i, ln in enumerate(lines):
        if ln.strip() == start_key and start_idx == -1:
            start_idx = i + 1  # start after the heading
            continue
        if start_idx != -1 and ln.strip() == end_key:
            end_idx = i
            break
    if start_idx == -1:
        return ""
    if end_idx == -1:
        end_idx = len(lines)
    block_lines = lines[start_idx:end_idx]
    # trim leading/trailing empties
    while block_lines and not block_lines[0].strip():
        block_lines = block_lines[1:]
    while block_lines and not block_lines[-1].strip():
        block_lines = block_lines[:-1]
    return "\n".join(block_lines)

def extract_airports_block_dict(lines: list[str]) -> dict:
    """
    Inside the 'Airports:' section, collect blocks whose headers look like ORXX/XXX.
    Stops at 'Predicted Demand' heading (or end of file).
    """
    airports = {}
    # first, get airports section
    # look for "Airports:" and next known heading (Predicted Demand or ATFM Measures)
    airports_start = -1
    stop_at = None
    for i, ln in enumerate(lines):
        if ln.strip() == "Airports:" and airports_start == -1:
            airports_start = i + 1
            continue
        if airports_start != -1 and ln.strip() in {"Predicted Demand", "Predicted Demand:", "Predicted Demand Nov 07th, 2025:", "ATFM Measures:", "Special events:", "Special events"}:
            stop_at = i
            break
    if airports_start == -1:
        return airports
    if stop_at is None:
        stop_at = len(lines)

    sect = lines[airports_start:stop_at]
    header_re = re.compile(r"^OR[A-Z]{2}/[A-Z]{3}$")
    current = None
    buf: list[str] = []
    for ln in sect:
        if header_re.match(ln.strip()):
            # flush previous
            if current and buf:
                airports[current] = "\n".join(buf).strip()
            current = ln.strip()
            buf = []
        else:
            if current is not None:
                buf.append(ln)
    if current and buf:
        airports[current] = "\n".join(buf).strip()
    return airports

def extract_demand_two_line(lines: list[str]) -> pd.DataFrame:
    """
    Demand entries appear as a two-line pair:
    0000–0100
    73
    Accept both '–' and '-' dash.
    """
    rows = []
    dash = r"[–-]"
    period_re = re.compile(rf"^\s*(\d{{4}}){dash}(\d{{4}})\s*$")
    i = 0
    while i < len(lines):
        m = period_re.match(lines[i])
        if m:
            period = f"{m.group(1)}–{m.group(2)}"
            # find the next non-empty numeric line
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and re.match(r"^\d+$", lines[j].strip()):
                val = int(lines[j].strip())
                rows.append({"Period (UTC)": period, "Overflights": val})
                i = j + 1
                continue
        i += 1
    return pd.DataFrame(rows)

# =========================
# DOWNLOAD & PARSE PDF
# =========================
with st.spinner("Downloading PDF from Google Drive…"):
    dl_url = drive_view_to_download_url(PDF_VIEW_LINK)
    if not dl_url:
        st.error("Could not read the Google Drive file ID from the link.")
        st.stop()
    try:
        pdf_bytes = fetch_bytes(dl_url)
        st.caption("✅ PDF downloaded")
    except Exception as e:
        st.error(f"❌ Failed to download PDF: {e}")
        st.stop()

with st.spinner("Extracting text from PDF…"):
    pdf_lines = pdf_to_lines(pdf_bytes)
    # Optional debug view
    with st.expander("🔧 Debug: show first 60 extracted lines", expanded=False):
        for i, ln in enumerate(pdf_lines[:60]):
            st.write(f"{i:03d} | {ln!r}")

# =========================
# EXTRACT SECTIONS
# =========================
# Airspace: between "Airspace:" and "Airports:"
airspace_block = extract_between_block(pdf_lines, "Airspace:", "Airports:")

# Airports: blocks under "Airports:"
airport_blocks = extract_airports_block_dict(pdf_lines)

# Predicted Demand: two-line pairs
demand_df = extract_demand_two_line(pdf_lines)

# =========================
# UI: Airspace
# =========================
st.header("🛰️ ORBB – Airspace Information")
if airspace_block.strip():
    st.write(airspace_block)
else:
    st.info("No Airspace information found under the 'Airspace:' heading in the PDF.")

# =========================
# UI: Airports
# =========================
st.header("🛬 Airport Information (One by One)")
for ap in AIRPORT_ORDER:
    with st.expander(ap, expanded=(ap == "ORBI/BGW")):
        content = (airport_blocks.get(ap) or "").strip()
        if content:
            st.write(content)
        else:
            st.info("No information found for this airport in the PDF.")

# =========================
# UI: Predicted Demand
# =========================
st.header("📈 Predicted Hourly Demand (Overflights)")
if demand_df.empty:
    st.warning("Predicted Demand (hourly pairs) not found in the PDF.")
else:
    # sort by start time numerically
    def _period_key(p: str) -> int:
        try:
            return int(p.split("–")[0])
        except:
            return 0
    demand_df = demand_df.sort_values(by="Period (UTC)", key=lambda s: s.map(_period_key))
    st.dataframe(demand_df, use_container_width=True)
    fig = px.area(demand_df, x="Period (UTC)", y="Overflights", markers=True, title="Hourly Predicted Overflights")
    st.plotly_chart(fig, use_container_width=True)
    st.metric("Peak Hour Demand", int(demand_df["Overflights"].max()))

# =========================
# UI: Sector Capacity & Utilization
# =========================
st.header("📊 Sector Capacity & Utilization")
peak = int(demand_df["Overflights"].max()) if not demand_df.empty else 0
cap_rows = []
for sector, cap in SECTOR_CAPACITY.items():
    util = round((peak / cap) * 100, 1) if cap else 0.0
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

# =========================
# UI: ATFM Measures
# =========================
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

# =========================
# UI: CDM – professional guidance
# =========================
st.header("🤝 CDM (Collaborative Decision Making) – Daily Guidance")
st.write("""
**Objectives:** shared situational awareness, predictable ops, and rapid recovery.

**Practices for today:**
- Publish a common pre-tactical plan to ACC/ATFMU, airlines, airports, and MET.
- Update stakeholders on **sectorisation windows**, expected **peak hour**, and **rerouting (NINVA→KABAN)**.
- Confirm airport readiness for predicted peaks (stands, gates, turnaround, staff).
- Agree **trigger thresholds** to extend/terminate measures as demand changes.
- Run **post-ops**: log delay causes, hotspots, and capacity shortfall for tomorrow’s plan.
""")

# =========================
# UI: What’s missing for a pro page
# =========================
st.header("🧩 Missing for a Complete Professional ATFM/CDM Platform")
st.write("""
1) Real-time traffic feed (ADS-B/OpenSky) layered on sectors/routes  
2) Automatic capacity estimator per sector with live updates  
3) MET integration (SIGWX/CB/jetstream impact) with route-level risk  
4) STAM suggestions (MIT/MINIT/level capping/reroutes) with “what-if”  
5) Slot/CTOT monitoring and A-CDM milestone integration  
6) Historical analytics for delay attribution and hotspot prediction
""")

# =========================
# ADP (DOCX) generator
# =========================
st.header("🧾 Generate ATFM Daily Plan (DOCX)")
adp_date = st.date_input("ADP Date (UTC)", value=datetime.utcnow().date())

def build_adp_docx() -> BytesIO:
    doc = Document()

    # Title + provider
    title = doc.add_paragraph(f"ATFM Daily Plan – {adp_date.isoformat()}")
    title.alignment = 1  # center
    doc.add_paragraph("Service Provider: GCANS-IRAQ").alignment = 1
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
        doc.add_paragraph("No demand table found in the PDF.")
    else:
        t = doc.add_table(rows=1, cols=2)
        hdr = t.rows[0].cells
        hdr[0].text = "Period (UTC)"
        hdr[1].text = "Overflights"
        for _, row in demand_df.iterrows():
            rr = t.add_row().cells
            rr[0].text = row["Period (UTC)"]
            rr[1].text = str(int(row["Overflights"]))

    # Capacity & Utilization
    doc.add_paragraph().add_run("Sector Capacity & Utilization").bold = True
    for _, r in cap_df.iterrows():
        doc.add_paragraph(f"{r['Sector']}: Peak {int(r['Peak Demand'])} / Cap {int(r['Capacity (acft/hr)'])} → Util {r['Utilization %']}%")

    # ATFM Measures
    doc.add_paragraph().add_run("ATFM Measures").bold = True
    doc.add_paragraph("Rerouting: Change exit points NINVA→KABAN during congestion.")
    for _, r in sector_table.iterrows():
        doc.add_paragraph(f"- {r['Period (UTC)']} {r['Sector']} ({r['Configuration']}, {r['Flight Levels']}) – {r['Reason']}")

    # CDM
    doc.add_paragraph().add_run("CDM – Daily Guidance").bold = True
    doc.add_paragraph("Share pre-tactical plan, confirm thresholds, coordinate airport readiness, and run post-ops analysis.")

    # Footer
    doc.add_paragraph("This app built and supervised by MM and CU.").alignment = 1

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
