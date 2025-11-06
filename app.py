import streamlit as st
import requests
from io import BytesIO
from docx import Document
import re

st.set_page_config(page_title="ATFM RAW TEXT TEST", layout="wide")

GOOGLE_DOC_URL = "https://docs.google.com/document/d/1PUtfstGvw8PhKWbnOOvlBjCa7wJJX-nM/export?format=docx"

st.title("📄 RAW TEXT STRUCTURE TEST (FOR FINAL ATFM APP)")

# ------------------------------------------------------------
# Download file from Google Drive
# ------------------------------------------------------------
st.subheader("1) Downloading file…")

try:
    r = requests.get(GOOGLE_DOC_URL, timeout=30)
    r.raise_for_status()
    doc_bytes = BytesIO(r.content)
    st.success("✅ File downloaded")
except Exception as e:
    st.error(f"❌ Failed to download: {e}")
    st.stop()

# ------------------------------------------------------------
# Extract all text from DOCX
# ------------------------------------------------------------
def extract_text(doc_file):
    doc = Document(doc_file)
    full = []
    for p in doc.paragraphs:
        full.append(p.text)
    return full

raw_list = extract_text(doc_bytes)

st.subheader("2) RAW extracted paragraphs")
st.write("✅ These are the EXACT paragraphs seen by Python")
st.write("✅ Copy ALL of this and send it to me")

# show with line numbers
for i, line in enumerate(raw_list):
    st.write(f"{i:03d} | {repr(line)}")

# ------------------------------------------------------------
# Show a joined version too
# ------------------------------------------------------------
joined = "\n".join(raw_list)

st.subheader("3) Normalized text output")
norm = joined
norm = norm.replace("\u2013", "–")  # en dash
norm = norm.replace("\u2014", "–")  # em dash
norm = norm.replace("\u00a0", " ")  # non-breaking space

st.code(norm)

st.warning("📌 Copy EVERYTHING above (both numbered lines AND normalized text).")
