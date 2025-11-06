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

PDF_VIEW_LINK = "https://drive.google.com/file/d/1g_f_vBXlv2QF9_b4QuNui_d6QiOF3ifS/view?usp=sharing"

# Sector base capacities
CAP_SOUTH = 26
CAP_NORTH = 27

AIRPORT_ORDER = ["ORBI/BGW", "ORBM/OSM", "ORER/EBL", "ORKK/KIK", "ORMM/BSR", "ORNI/NJF"]

# Split windows (UTC) — inclusive of any overlap
# Format "HHMM"
SPLITS_SOUTH = [("0530", "0730"), ("1200", "1400"), ("2330", "0130")]  # note 2330–0130 crosses midnight
SPLITS_NORTH = [("0600", "0800"), ("1200", "1400"), ("0000", "0200")]  # also crosses midnight in first hours

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
    lines: list[str] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for ln in text.splitlines():
                ln = ln.replace("\u2013", "–").replace("\u2014", "–").replace("\u00A0", " ")
                lines.append(ln.rstrip())
    return lines

def extract_between_lines(lines: list[str], start_key: str, end_key: str | None) -> list[str]:
    start_idx, end_idx = -1, -1
    for i, ln in enumerate(lines):
        if ln.strip() == start_key and start_idx == -1:
            start_idx = i + 1
            continue
        if start_idx != -1 and end_key and ln.strip() == end_key:
            end_idx = i
            break
    if start_idx == -1:
        return []
    if end_idx == -1:
        end_idx = len(lines)
    block = lines[start_idx:end_idx]
    # trim empties
    while block and not block[0].strip():
        block = block[1:]
    while block and not block[-1].strip():
        block = block[:-1]
    return block

def split_notams_to_bullets(block_text: str) -> list[str]:
    """
    Split NOTAM-style long text into bullets per A####/## item, keeping inner line breaks.
    """
    # Ensure newlines around codes to help splitting
    text = block_text.replace("•", "- ")
    patt = re.compile(r"(A\d{4}/\d{2}\s*–\s*.*?)(?=A\d{4}/\d{2}\s*–|\Z)", re.DOTALL)
    items = []
    for m in patt.finditer(text):
        item = m.group(1).strip()
        # compact double spaces and preserve explicit linebreaks for multi-line routeing
        item = re.sub(r"[ \t]+", " ", item)
        items.append(item)
    if not items:
        # fallback: return lines
        return [ln for ln in text.splitlines() if ln.strip()]
    return items

def extract_airports_dict_from_lines(lines: list[str]) -> dict:
    # First, isolate the Airports section until another known heading
    stop_headers = {"Predicted Demand", "Predicted Demand:", "ATFM Measures:", "Special events:", "Special events"}
    airports_start = -1
    stop_at = None
    for i, ln in enumerate(lines):
        if ln.strip() == "Airports:" and airports_start == -1:
            airports_start = i + 1
            continue
        if airports_start != -1 and ln.strip() in stop_headers:
            stop_at = i
            break
    if airports_start == -1:
        return {}
    if stop_at is None:
        stop_at = len(lines)
    sect = lines[airports_start:stop_at]

    header_re = re.compile(r"^OR[A-Z]{2}/[A-Z]{3}$")
    airports: dict[str, list[str]] = {}
    current = None
    for ln in sect:
        if header_re.match(ln.strip()):
            current = ln.strip()
            airports[current] = []
        else:
            if current:
                airports[current].append(ln)

    # normalize NIL blocks and join/bulletize long NOTAM chains
    out = {}
    for k, v in airports.items():
        txt = "\n".join(v).strip()
        if txt.upper() == "NIL":
            out[k] = "NIL"
        else:
            bullets = split_notams_to_bullets(txt)
            out[k] = "\n\n".join(bullets)
    return out

def extract_demand(lines: list[str]) -> pd.DataFrame:
    """
    Supports both formats:
      1) single-line: "0000–0100 73"
      2) two-line:
         "0000–0100"
         "73"
    """
    rows = []
    dash = r"[–-]"
    single = re.compile(rf"^\s*(\d{{4}}){dash}(\d{{4}})\s+(\d+)\s*$")
    period = re.compile(rf"^\s*(\d{{4}}){dash}(\d{{4}})\s*$")
    for i, ln in enumerate(lines):
        m = single.match(ln)
        if m:
            rows.append({"Period (UTC)": f"{m.group(1)}–{m.group(2)}", "Overflights": int(m.group(3))})
            continue
        pm = period.match(ln)
        if pm:
            # look at next non-empty line for value
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and re.match(r"^\d+$", lines[j].strip()):
                rows.append({"Period (UTC)": f"{pm.group(1)}–{pm.group(2)}", "Overflights": int(lines[j].strip())})
    df = pd.DataFrame(rows)
    if not df.empty:
        # sort by start time
        def k(p: str) -> int:
            try:
                return int(p.split("–")[0])
            except:
                return 0
        df = df.sort_values(by="Period (UTC)", key=lambda s: s.map(k)).reset_index(drop=True)
    return df

def hhmm_to_min(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[2:])

def range_overlaps_hour(start_hhmm: str, end_hhmm: str, hour_start_min: int, hour_end_min: int) -> bool:
    """
    Does [start,end) overlap hour interval? Handles wrap over midnight (e.g., 2330–0130).
    """
    s = hhmm_to_min(start_hhmm)
    e = hhmm_to_min(end_hhmm)
    if s <= e:
        # normal
        return not (e <= hour_start_min or s >= hour_end_min)
    else:
        # wraps midnight: treat as [s, 1440) U [0, e)
        return not (hour_end_min <= s and hour_start_min >= e)

def build_capacity_timeline() -> pd.DataFrame:
    """
    Build a 24-row table: Period, SouthCap, NorthCap, FIRCap
    """
    rows = []
    for h in range(24):
        start = h * 60
        end = (h + 1) * 60
        south_split = any(range_overlaps_hour(a, b, start, end) for a, b in SPLITS_SOUTH)
        north_split = any(range_overlaps_hour(a, b, start, end) for a, b in SPLITS_NORTH)
        south = CAP_SOUTH * (2 if south_split else 1)
        north = CAP_NORTH * (2 if north_split else 1)
        rows.append({"Period (UTC)": f"{h:02d}00–{(h+1)%24:02d}00", "SouthCap": south, "NorthCap": north, "FIRCap": south + north})
    return pd.DataFrame(rows)

def extract_met_block(lines: list[str]) -> str:
    # Try a few common headings
    candidates = ["ATFM Meteorological Forecast", "Meteorological Forecast", "Weather", "MET:"]
    heads = set(candidates)
    # find first matching heading, then run until next known major heading
    idx = -1
    for i, ln in enumerate(lines):
        if ln.strip() in heads:
            idx = i + 1
            break
    if idx == -1:
        return ""
    stop_heads = {"Airspace:", "Airports:", "Predicted Demand", "Predicted Demand:", "Predicted Demand Nov 07th, 2025:", "ATFM Measures:", "Special events:", "Special events"}
    block = []
    for j in range(idx, len(lines)):
        if lines[j].strip() in stop_heads:
            break
        block.append(lines[j])
    # clean up
    while block and not block[0].strip():
        block = block[1:]
    while block and not block[-1].strip():
        block = block[:-1]
    return "\n".join(block)

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

# =========================
# EXTRACT SECTIONS
# =========================
airspace_lines = extract_between_lines(pdf_lines, "Airspace:", "Airports:")
airspace_text = "\n".join(airspace_lines)
airspace_bullets = split_notams_to_bullets(airspace_text) if airspace_text else []

airport_blocks = extract_airports_dict_from_lines(pdf_lines)
demand_df = extract_demand(pdf_lines)
cap_timeline = build_capacity_timeline()
met_block = extract_met_block(pdf_lines)

# =========================
# UI: Airspace
# =========================
st.header("🛰️ ORBB – Airspace Information")
if airspace_bullets:
    for it in airspace_bullets:
        st.markdown(f"- {it}")
else:
    st.info("No Airspace information found under the 'Airspace:' heading in the PDF.")

# =========================
# UI: Airports (each separately)
# =========================
st.header("🛬 Airport Information (One by One)")
for ap in AIRPORT_ORDER:
    with st.expander(ap, expanded=(ap == "ORBI/BGW")):
        content = (airport_blocks.get(ap) or "").strip()
        if not content:
            st.info("No information found for this airport in the PDF.")
        elif content.upper() == "NIL":
            st.success("NIL")
        else:
            # split into NOTAM bullets per A####/##
            bullets = split_notams_to_bullets(content)
            for it in bullets:
                st.markdown(f"- {it}")

# =========================
# UI: Predicted Demand vs Capacity (hourly)
# =========================
st.header("📈 Predicted Hourly Demand (Overflights) & Capacity")
if demand_df.empty:
    st.warning("Predicted Demand (hourly) not found in the PDF.")
else:
    # merge with capacity timeline by Period
    merged = pd.merge(cap_timeline, demand_df, on="Period (UTC)", how="left")
    merged["Overflights"] = merged["Overflights"].fillna(0).astype(int)
    merged["Utilization % (FIR)"] = (merged["Overflights"] / merged["FIRCap"] * 100).round(1)
    st.dataframe(merged, use_container_width=True)

    fig = px.line(
        merged,
        x="Period (UTC)",
        y=["Overflights", "FIRCap"],
        title="Overflights vs FIR Capacity by Hour",
        markers=True,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.metric("Peak Hour Overflights", int(merged["Overflights"].max()))
    st.metric("Max Utilization % (FIR)", float(merged["Utilization % (FIR)"].max()))

# =========================
# UI: MET (from PDF if present)
# =========================
if met_block:
    st.header("🌤️ ATFM Meteorological Forecast")
    st.write(met_block)

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

    # Airspace (bullets)
    doc.add_paragraph().add_run("Airspace Information").bold = True
    if airspace_bullets:
        for it in airspace_bullets:
            doc.add_paragraph(it, style=None)
    else:
        doc.add_paragraph("N/A")

    # Airports
    doc.add_paragraph().add_run("Airports").bold = True
    for ap in AIRPORT_ORDER:
        doc.add_paragraph(ap)
        content = (airport_blocks.get(ap) or "").strip()
        if not content:
            doc.add_paragraph("No information found.")
        elif content.upper() == "NIL":
            doc.add_paragraph("NIL")
        else:
            for it in split_notams_to_bullets(content):
                doc.add_paragraph(it)

    # Demand vs Capacity
    doc.add_paragraph().add_run("Predicted Demand vs FIR Capacity").bold = True
    if 'merged' in locals() and not merged.empty:
        t = doc.add_table(rows=1, cols=4)
        hdr = t.rows[0].cells
        hdr[0].text = "Period (UTC)"
        hdr[1].text = "Overflights"
        hdr[2].text = "FIRCap"
        hdr[3].text = "Util %"
        for _, row in merged.iterrows():
            rr = t.add_row().cells
            rr[0].text = str(row["Period (UTC)"])
            rr[1].text = str(int(row["Overflights"]))
            rr[2].text = str(int(row["FIRCap"]))
            rr[3].text = f"{float(row['Utilization % (FIR)']):.1f}"
    else:
        doc.add_paragraph("No demand data found in the PDF.")

    # MET (optional)
    if met_block:
        doc.add_paragraph().add_run("ATFM Meteorological Forecast").bold = True
        doc.add_paragraph(met_block)

    # ATFM Measures
    doc.add_paragraph().add_run("ATFM Measures").bold = True
    doc.add_paragraph("Rerouting: Change exit points NINVA→KABAN during congestion.")
    for _, r in sector_table.iterrows():
        doc.add_paragraph(f"- {r['Period (UTC)']} {r['Sector']} ({r['Configuration']}, {r['Flight Levels']}) – {r['Reason']}")

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
