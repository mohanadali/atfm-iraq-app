import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import re
import io
from io import BytesIO
from pdfminer.high_level import extract_text

# ------------------------------
# CONFIG
# ------------------------------
st.set_page_config(page_title="ATFM IRAQ – GCANS-IRAQ ADP", layout="wide")
PASSWORD = "atfmiraqmm"

PDF_URL = "https://drive.google.com/uc?export=download&id=1g_f_vBXlv2QF9_b4QuNui_d6QiOF3ifS"

AIRPORT_ORDER = ["ORBI/BGW", "ORBM/OSM", "ORER/EBL", "ORKK/KIK", "ORMM/BSR", "ORNI/NJF", "ORSU/ISU"]

SECTOR_CAPACITY = {"South Sector": 26, "North Sector": 27}

# ------------------------------
# AUTH
# ------------------------------
if "auth" not in st.session_state:
    st.session_state.auth = False

st.title("🇮🇶 ATFM IRAQ – GCANS-IRAQ Daily ATFM Plan")

if not st.session_state.auth:
    with st.form("login"):
        _u = st.text_input("Username")
        _p = st.text_input("Password", type="password")
        ok = st.form_submit_button("Login")
    if ok:
        if _p == PASSWORD:
            st.session_state.auth = True
            st.experimental_rerun()
        else:
            st.error("Wrong password.")
            st.stop()
else:
    st.success("✅ Authentication successful")

# ------------------------------
# DOWNLOAD PDF
# ------------------------------
st.subheader("📥 Downloading ATFM Source File")

try:
    r = requests.get(PDF_URL, timeout=20)
    r.raise_for_status()
    raw_pdf = r.content
    st.success("✅ PDF downloaded successfully")
except Exception as e:
    st.error(f"❌ Failed to download PDF: {e}")
    st.stop()

# ------------------------------
# EXTRACT TEXT FROM PDF
# ------------------------------
st.subheader("📄 Extracting text from PDF...")
try:
    text = extract_text(BytesIO(raw_pdf))
    st.success("✅ PDF text extracted")
except:
    st.error("❌ Failed to extract text")
    st.stop()

lines = [l.strip() for l in text.split("\n") if l.strip()]

# ------------------------------
# HELPERS
# ------------------------------
def colorize_notam(nt):
    nt_low = nt.lower()

    if "unserviceable" in nt_low or "closed" in nt_low:
        return f"⛔ **{nt}**"
    if "caution" in nt_low or "interference" in nt_low:
        return f"⚠️ **{nt}**"
    if "trigger notam" in nt_low or "airac" in nt_low:
        return f"✈️ {nt}"
    return f"✈️ {nt}"

def split_notams_block(block):
    parts = re.split(r"\b(A\d{4}/\d{2})", block)
    notams = []
    for i in range(1, len(parts), 2):
        code = parts[i]
        body = parts[i + 1].strip()
        full = f"{code} – {body}"
        notams.append(colorize_notam(full))
    return notams

# ------------------------------
# EXTRACT ORBB AIRSPACE
# ------------------------------
def extract_orbb(lines):
    out = []
    grab = False
    for ln in lines:
        if ln.startswith("Airspace"):
            grab = True
            continue
        if ln.startswith("Airports:"):
            break
        if grab:
            out.append(ln)
    return "\n".join(out).strip()

# ------------------------------
# EXTRACT AIRPORTS
# ------------------------------
AIRPORT_RE = re.compile(r"^(OR[A-Z]{2}/[A-Z]{3})(.*)$")

def extract_airports(lines):
    airports = {a: "" for a in AIRPORT_ORDER}
    current = None

    for ln in lines:
        m = AIRPORT_RE.match(ln)
        if m:
            current = m.group(1)
            rest = m.group(2).strip()
            if rest:
                airports[current] += rest + " "
        else:
            if current:
                airports[current] += ln + " "

    # process NOTAMs
    final = {}
    for ap, txt in airports.items():
        txt = txt.strip()
        if txt.upper() == "NIL" or txt == "":
            final[ap] = ["⛔ NIL"]
        else:
            final[ap] = split_notams_block(txt)
    return final

# ------------------------------
# EXTRACT PREDICTED DEMAND
# ------------------------------
def extract_demand(lines):
    demand = {}
    grab = False
    for ln in lines:
        if "Zulu Time Period" in ln:
            grab = True
            continue
        if grab:
            m = re.match(r"(\d{4}–\d{4})\s+(\d+)", ln)
            if m:
                demand[m.group(1)] = int(m.group(2))
            if "Total" in ln:
                break
    return demand

# ------------------------------
# RUN EXTRACTIONS
# ------------------------------
airspace_txt = extract_orbb(lines)
airport_dict = extract_airports(lines)
demand = extract_demand(lines)

# ------------------------------
# DISPLAY: ORBB AIRSPACE
# ------------------------------
st.header("🛰️ ORBB – Airspace Information")
st.write(airspace_txt.replace(". ", ".\n"))

# ------------------------------
# DISPLAY: AIRPORTS
# ------------------------------
st.header("🛬 Airport Information")

for ap in AIRPORT_ORDER:
    with st.expander(f"**{ap}**"):
        for nt in airport_dict[ap]:
            st.markdown(f"- {nt}")

# ------------------------------
# DISPLAY: PREDICTED DEMAND
# ------------------------------
st.header("📈 Predicted Hourly Demand")

if demand:
    df = pd.DataFrame({"Zulu": list(demand.keys()), "OVF": list(demand.values())})
    st.dataframe(df, use_container_width=True)

    fig = px.line(df, x="Zulu", y="OVF", markers=True, title="Predicted Overflights")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.warning("No predicted demand found.")

# ------------------------------
# DISPLAY: SECTORIZATION / ATFM MEASURES
# ------------------------------
st.header("🛠️ ATFM Measures")

st.subheader("✅ Rerouting")
st.write("""
Change exit points between **NINVA → KABAN** during congestion periods.
""")

st.subheader("✅ Sectorization")
st.write("""
- **South Sector**
  - South Low: FL240–FL350  
  - South High: FL360–FL460  
  - Periods: 0530–0730, 1200–1400, 2330–0130

- **North Sector**
  - North Low: FL240–FL350  
  - North High: FL360–FL460  
  - Periods: 0600–0800, 1200–1400, 0000–0200
""")

# ------------------------------
# CDM SECTION
# ------------------------------
st.header("🤝 CDM – Collaborative Decision Making (Daily Guidance)")
st.write("""
- Ensure continuous coordination between **ACC**, **ATFM Unit**, **Airports**, and **Airlines**  
- Communicate sector opening/closing times in advance  
- Share expected weather impact and predicted demand with stakeholders  
- Conduct pre-tactical review during morning briefing  
- Validate any major airport constraints (RWY closures, TWY work, NOTAMs)
""")

# ------------------------------
# END OF APP
# ------------------------------
st.info("✅ This ATFM Plan App is supervised by **MM & CU**")
