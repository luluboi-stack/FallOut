import base64
from io import BytesIO
import json
import math
import os
import re
import socket
import sqlite3
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
from playwright.sync_api import Page, sync_playwright
import streamlit as st

# ---------------------------------------------------------
# PAGE CONFIGURATION
# ---------------------------------------------------------
st.set_page_config(
    page_title="Ather Material Replenishment & Kitting Suite",
    page_icon="⚡",
    layout="wide",
)

URL = "http://192.168.201.248/AtherReport/"
SCALEX_HOST = "192.168.201.248"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_BATCHES = os.path.join(BASE_DIR, "plan_batches.csv")
DB_PATH = os.path.join(BASE_DIR, "loading_persistence.db")
SNAPSHOT_FILE = os.path.join(BASE_DIR, "state_snapshot.json")

def find_local_file(candidates: List[str]) -> Optional[str]:
    for fname in candidates:
        p = os.path.join(BASE_DIR, fname)
        if os.path.exists(p):
            return p
    return None

LOCAL_BOM_PATH = find_local_file(["bom.csv", "BOM.csv", "BOM.CSV", "bom.xlsx", "BOM.xlsx"])
LOCAL_LX02_PATH = find_local_file(["lx02.csv", "LX02.csv", "LX02.CSV", "lx02.xlsx", "LX02.xlsx", "lx02.xls", "LX02.xls", "LMAO - Data.csv"])
LOCAL_STO_PATH = find_local_file(["sto.csv", "STO.csv", "open_sto.csv", "open_sto.xlsx", "sto.xlsx", "SIT sep 13 pm.xlsx"])

# ---------------------------------------------------------
# LOCAL DATABASE INITIALIZATION (REFRESH-SAFE)
# ---------------------------------------------------------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS truck_loading (
                su_number TEXT PRIMARY KEY,
                part_id TEXT,
                description TEXT,
                storage_bin TEXT,
                qty REAL,
                status TEXT,
                remarks TEXT,
                scan_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
init_db()

def get_scanned_records() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql("SELECT su_number, part_id, description, storage_bin, qty, status, remarks, scan_timestamp FROM truck_loading ORDER BY scan_timestamp DESC", conn)

def save_scanned_su(su: str, part: str, desc: str, bin_loc: str, qty: float):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO truck_loading (su_number, part_id, description, storage_bin, qty, status, remarks)
            VALUES (?, ?, ?, ?, ?, 'LOADED', '')
        """, (su, part, desc, bin_loc, qty))

def clear_truck_ledger():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM truck_loading")

# ---------------------------------------------------------
# ATHER BRANDING
# ---------------------------------------------------------
st.markdown("""
<style>
    .stApp { background-color: #ffffff; color: #0f172a; }
    .stButton>button { 
        background-color: #00c853 !important; 
        color: #ffffff !important; 
        font-weight: 800 !important; 
        border-radius: 8px !important; 
        border: none !important; 
        padding: 10px 24px !important; 
        box-shadow: 0 2px 4px rgba(0, 200, 83, 0.2) !important;
    }
    .stButton>button:hover { 
        background-color: #00b047 !important; 
        box-shadow: 0 4px 8px rgba(0, 200, 83, 0.3) !important;
    }
    .ather-header {
        background: #ffffff;
        border-bottom: 3px solid #00c853;
        padding: 12px 20px;
        border-radius: 8px;
        margin-bottom: 15px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    div[data-testid="stMetricValue"] { font-size: 26px; font-weight: 900; color: #0f172a; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 1. OFFICIAL PSS MASTER (Complete & Uncut)
# ---------------------------------------------------------
# ---------------------------------------------------------
# 1. OFFICIAL PSS MASTER (Consolidated & Complete Floor Truth)
# ---------------------------------------------------------
OFFICIAL_PSS_MASTER = {
    # --- Consolidated Ground-Truth Packaging Master ---
    "BE0203B004MNNC": (180, "FLC"),
    "BE0203B005MNNC": (180, "FLC"),
    "MC00000177E": (160, "Pallet"),
    "BE0407A001MNNL": (160, "Pallet"),
    "BW00000683B": (336, "Pallet"),
    "BW00001484A": (336, "Pallet"),
    "BE0112B001MNNZ0B": (384, "Pallet"),
    "CS00005535A": (2304, "Pallet"),
    "340A0000975F": (2500, "Pallet"),
    "BE0202A001MNNF": (30, "FLC"),
    "BE0202A004MNNE": (30, "FLC"),
    "BS00000520A": (240, "Pallet"),
    "BW00000484A": (360, "Pallet"),
    "BE0311C001MNNZF": (45, "Pallet"),
    "EP00000137B": (6000, "Pallet"),
    "BW00001252A": (288, "Pallet"),
    "BW00000502BMA": (60, "Pallet"),
    "BW00000502BKA": (60, "Pallet"),
    "BW00000480BMC": (56, "Pallet"),
    "BW00000503BMA": (60, "Pallet"),
    "BW00000503BKA": (60, "Pallet"),
    "BW00000480BKC": (56, "Pallet"),
    "ZZ00000859A": (576, "Pallet"),
    "BF0101C005ANNB": (960, "Pallet"),
    "BW00000327A": (576, "Pallet"),
    "ZZ00000841A": (576, "Pallet"),
    "340A0000984C": (240, "Pallet"),
    "340A0000986G": (240, "Pallet"),
    "340A001051F": (4000, "Pallet"),
    "DB00001096A": (160, "Pallet"),
    "LL00000002C": (48, "Pallet"),
    "ZZ00000708E": (100, "FLC"),
    "PN00000625A": (280, "Pallet"),
    "CS00004377MKA": (40, "Pallet"),
    "CS00004377MSA": (40, "Pallet"),
    "CS00004377WTA": (40, "Pallet"),
    "EP00000897B": (640, "Pallet"),
    "EP00000898B": (2560, "Pallet"),
    "EP00001121A": (3200, "Pallet"),
    "EP00000590B": (864, "Pallet"),
    "EP00000892B": (640, "Pallet"),
    "CS00008488A": (448, "Pallet"),
    "CS00008492A": (448, "Pallet"),
    "EP00000118A": (2000, "Pallet"),
    "CS00003172A": (32, "Trolley"),
    "WT00000113MGA": (32, "Trolley"),
    "WT00000113MGB": (32, "Trolley"),
    "CS00000221A": (32, "Trolley"),
    "WT00000045A": (32, "Trolley"),
    "WT00000045MGB": (32, "Trolley"),
    "WT00000045MGC": (32, "Trolley"),
    "ES00001978B": (150, "Pallet"),
    "CS00009472B": (80, "Trolley"),
    "EP00000913B": (48, "Pallet"),
    "EP00000914B": (36, "Pallet"),
    "EP00000928B": (60, "Pallet"),
    "EP00000899A": (1728, "Pallet"),
    "EP00000682B": (576, "Pallet"),
    "BW00000485A": (360, "Pallet"),
    "BS00000037B": (30, "FLC"),
    "BW00000012WE0A": (110, "FLC"),
    "BW00000013WE0A": (110, "FLC"),
    "BD0103A001MSGZ0B": (44, "FLC"),
    "BD0103A002MSGZ0B": (44, "FLC"),
    "ZZ00000836A": (120, "Pallet"),
    "340A0001037F": (320, "Pallet"),
    "BD0105B002MSGZ0B": (78, "FLC"),
    "BD0105C001MWE0A": (78, "FLC"),
    "BE0111B001MNNZA": (300, "Trolley"),
    "ZZ00000829A": (192, "Trolley"),
    "BW00000366BMA": (120, "Trolley"),
    "BW00000366BKA": (120, "Trolley"),
    "BD0104D001MSGZ0D": (60, "FLC"),
    "ZZ00000856A": (96, "Pallet"),
    "BW00000017B": (60, "Pallet"),
    "ZZ00000857A": (96, "Pallet"),
    "BD0104E001MSGZ0D": (21, "FLC"),
    "BW00000014YW0A": (60, "Trolley"),
    "BW00001438DCB": (30, "FLC"),
    "BW00001438SYB": (30, "FLC"),
    "BW00001438GDB": (30, "FLC"),
    "BW00001438WEB": (30, "FLC"),
    "BW00001439DCB": (30, "FLC"),
    "BW00001439GDB": (30, "FLC"),
    "BW00001439SYB": (30, "FLC"),
    "BW00001439WEB": (30, "FLC"),
    "ZZ00000823SN0A": (60, "FLC"),
    "ZZ00000823SY0A": (60, "FLC"),
    "ZZ00000823WE0A": (60, "FLC"),
    "SN00000054B": (140, "FLC"),
    "SN00000055B": (140, "FLC"),
    "BF0101B005MFBZ0A": (60, "Trolley"),
    "MA00001463BLD": (32, "Trolley"),
    "WT00000107BLA": (32, "Trolley"),
    "BW00001437DCA": (6, "FLC"),
    "BW00001437SYA": (6, "FLC"),
    "BW00001437WEA": (6, "FLC"),
    "BW00000024WE0A": (60, "FLC"),
    "BW00000024YW0B": (32, "FLC"),
    "BW00000359SYA": (24, "FLC"),
    "BW00000359GDA": (24, "FLC"),
    "BW00000359DCA": (24, "FLC"),
    "BW00000359WEA": (24, "FLC"),
    "SN00000007B": (30, "Trolley"),
    "BE0203B003MNNG": (30, "Trolley"),
    "BW00000295A": (1792, "Pallet"),
    "BS00000610A": (576, "Pallet"),
    "BW00000009FB0B": (35, "FLC"),
    "MA00001463OED": (32, "Trolley"),
    "MA00001463RDD": (32, "Trolley"),
    "WT00000107RDA": (32, "Trolley"),
    "WT00000107OEA": (32, "Trolley"),
    "MA00001463OE0B": (32, "Trolley"),
    "MA00001463BL0B": (32, "Trolley"),
    "MA00001463RD0B": (32, "Trolley"),
    "340A0015243C": (100, "Pallet"),
    "BE0601A001MNNG": (160, "Pallet"),
    "BF0801A004MNNZ0E": (160, "Pallet"),
    "CS00004249B": (72, "Pallet"),
    "BW00000684BMA": (168, "Pallet"),
    "BW00000684BKA": (168, "Pallet"),
    "BD0201H001MNNZ0A": (100, "Pallet"),
    "CS00000088C": (48, "Pallet"),
    "CS00001173B": (88, "Trolley"),
    "ZZ00000882A": (60, "Trolley"),
    "BE0208A004MNNZ0B": (60, "Trolley"),
    "340A0013625E": (256, "Pallet"),
    "BE0201C005MNNZ0F": (32, "Trolley"),
    "ZZ00000855A": (40, "Trolley"),
    "BE0104C001MNNZ0D": (60, "Trolley"),
    "BW00000365BMA": (40, "Pallet"),
    "BW00000365BKA": (40, "Pallet"),
    "BE0104C001MNNZE": (60, "Trolley"),
    "BW00000225C": (1680, "Pallet"),
    "TS00000072A": (48000, "Pallet"),
    "340A0021507A": (2000, "Pallet"),
    "BW00001136BMA": (16, "FLC"),
    "BW00001136BKA": (16, "FLC"),
    "BE0113C001MNNZA": (288, "Pallet"),
    "TS00000052B": (576, "Pallet"),
    "340A0006351D": (200, "Pallet"),
    "340A0006351F": (200, "Pallet"),
    "TS00000402B": (288, "Pallet"),
    "BE0802A015MNNB": (600, "Pallet"),
    "BE0802A014MNNB": (600, "Pallet"),
    "ES00001376B": (1120, "Pallet"),
    "ES00001377B": (1120, "Pallet"),
    "BF0805A001MNNZE": (784, "Pallet"),
    "BF0805A002MNNZE": (784, "Pallet"),
    "BF0805B002MNNZ0F": (64, "Pallet"),
    "BF0805F003MNNZ0C": (1600, "Pallet"),
    "LL00000005C": (64, "Pallet"),
    "LL00000005D": (64, "Pallet"),
    "BF0805B003MNNZ0F": (80, "Pallet"),
    "BE0203B013MNNA": (198, "FLC"),
    "BE0203B014MNNA": (198, "FLC"),
    "BS00000497A": (200, "Trolley"),
    "BD0105C001MSGZ0B": (78, "FLC"),
    "340A0003365H": (5600, "Pallet"),
    "340A0003366H": (6000, "Pallet"),
    "CS00006960A": (350, "Pallet"),
    "CS00006959A": (350, "Pallet"),
    "ZZ00000824SN0A": (60, "FLC"),
    "BE0203B009MNNA": (3780, "Pallet"),
    "BW00001559A": (48000, "Pallet"),
    "340A0013724E": (14400, "Pallet"),
    "BE0201E001ANNZ0F": (36, "FLC"),
    "BE0401A002ANNG": (120, "FLC"),
    "BW00000035D": (144, "Pallet"),
    "340A0001050I": (300, "Pallet"),
    "340A0001034I": (1200, "Pallet"),
    "BE0401A001ANNE": (72, "FLC"),
    "BE0401A001ANNG": (72, "FLC"),
    "BE0104F001MNNZA": (120, "Pallet"),
    "ZZ00000834A": (72, "Pallet"),
    "BE0104G001MNNZA": (120, "Pallet"),
    "ZZ00000835A": (72, "Pallet"),
    "CS00003149B": (90, "Trolley"),
    "340A0021448H": (280, "Pallet"),
    "BE0105D001MNNZ0D": (96, "Trolley"),
    "BD0105A001MSGZ0B": (28, "FLC"),
    "BD0105A002MSGZ0B": (28, "FLC"),
    "BE0109Z001ANNB": (12000, "Pallet"),
    "CS00006936B": (19200, "Pallet"),
    "CS00004336A": (1536, "Pallet"),
    "CS00004337A": (1536, "Pallet"),
    "340A0006332B": (400, "Pallet"),
    "BF0502B002ANNC": (1600, "Pallet"),
    "BY00000893A": (320, "Pallet"),
    "BW00001433A": (96, "Pallet"),
    "BS00000484A": (1400, "Pallet"),
    "BS00000482A": (4680, "Pallet"),
    "BS00000514A": (960, "Pallet"),
    "BS00000621A": (14400, "Pallet"),
    "BW00000481A": (32, "FLC"),
    "BF0107C001ANNB": (280, "Pallet"),
    "ZZ00000837A": (96, "Pallet"),
    "SN00000053B": (140, "FLC"),
    "SN00000035BLC": (286, "FLC"),
    "SN00000035BLD": (286, "FLC"),
    "SN00000035ABC": (286, "FLC"),
    "SN00000035ABD": (286, "FLC"),
    "SN00000035GNC": (286, "FLC"),
    "SN00000035GND": (286, "FLC"),
    "SN00000035OEC": (286, "FLC"),
    "SN00000035OED": (286, "FLC"),
    "SN00000035RDC": (286, "FLC"),
    "SN00000035RDD": (286, "FLC"),
    "SN00000035WEC": (286, "FLC"),
    "SN00000035WED": (286, "FLC"),
    "SN00000035YWC": (286, "FLC"),
    "SN00000035YWD": (286, "FLC"),
    "SN00000037B": (1152, "Pallet"),
    "ZZ00000860A": (35, "FLC"),
    "BF0101C001ANNB": (120, "Trolley"),
    "BW00000342B": (35, "FLC"),
    "BW00001427BMB": (100, "Trolley"),
    "BW00001427BKB": (100, "Trolley"),
    "MA00001512OEC": (32, "Trolley"),
    "MA00001512RDC": (32, "Trolley"),
    "WT00000104OEA": (32, "Trolley"),
    "WT00000104RDA": (32, "Trolley"),
    "MA00001513BLC": (32, "Trolley"),
    "WT00000105BLA": (32, "Trolley"),
    "MA00001512BLC": (32, "Trolley"),
    "WT00000104BLA": (32, "Trolley"),
    "MA00001512OE0A": (32, "Trolley"),
    "MA00001513BL0A": (32, "Trolley"),
    "MA00001489BL0B": (32, "Trolley"),
    "WT00000106BLA": (32, "Trolley"),
    "MA00001512BL0A": (32, "Trolley"),
    "MA00001512RD0A": (32, "Trolley"),
    "340A0004363B": (9600, "Pallet"),
    "BS00000490A": (30, "FLC"),
    "SN00000005C": (120, "FLC"),
    "SN00000006C": (120, "FLC"),
    "BD0107B001MSGZ0B": (60, "FLC"),
    "ZZ00000780B": (550, "Pallet"),
    "ZZ00000781A": (550, "FLC"),
    "BE0201A003MNNB": (160, "Trolley"),
    "BW00000098D": (21, "Trolley"),
    "BW00000286B": (21, "Trolley"),
    "BW00001482A": (21, "Trolley"),
    "BF0108A001MNNC": (18, "Trolley"),
    "BW00000341D": (21, "Trolley"),
    "BW00000098E": (21, "Trolley"),
    "340A0005486I": (1232, "Pallet"),
    "ZZ00000149B": (1280, "Pallet"),
    "340A0001097G": (14400, "Pallet"),
    "340A0014645D": (16000, "Pallet"),
    "EP00000586A": (9000, "Pallet"),
    "CS00009507B": (448, "Pallet"),
    "ES00001326B": (160, "Pallet"),
    "ZZ00000779A": (12, "Pallet"),
    "CS00003170A": (3360, "Pallet"),
    "TS00000015B": (144, "Pallet"),
    "BD0109B001MNNZE": (60, "Trolley"),
    "BW00000667A": (8000, "Pallet"),
    "BW00000666A": (8000, "Pallet"),
    "ZZ00000852A": (30, "Trolley"),
    "BW00000304B": (2560, "Pallet"),
    "CS00001079B": (48, "Trolley"),
    "ZZ00000839A": (60, "Pallet"),
    "CS00003886B": (44, "Trolley"),
    "CS00004474B": (16, "Trolley"),
    "BW00000032WE0B": (28, "FLC"),
    "BW00000478SYA": (60, "Pallet"),
    "BW00001259DCA": (24, "FLC"),
    "BW00001259WEA": (24, "FLC"),
    "BW00001259GDA": (24, "FLC"),
    "BE0105D001MNNZE": (96, "Trolley"),
    "BW00000472DCB": (19, "FLC"),
    "BW00000472SYB": (19, "FLC"),
    "BW00000472WEB": (19, "FLC"),
    "BW00000473DCB": (19, "FLC"),
    "BW00000473WEB": (19, "FLC"),
    "BW00000030YW0A": (28, "FLC"),
    "BW00000031WE0A": (28, "FLC"),
    "ES00002002B": (4600, "Pallet"),
    "ES00001837B": (2160, "Pallet"),
    "DB00000837B": (160, "Pallet"),
    "EP00000334A": (400, "Pallet"),
    "BE0311C002MNNZ0E": (45, "Pallet"),
    "ZZ00000840A": (256, "Pallet"),
    "BW00000140A": (72, "Pallet"),
    "BW00000139B": (72, "Pallet"),
    "ZZ00000838A": (60, "Pallet"),
    "CS00004724B": (6, "Trolley"),
    "CS00000066D": (16, "Trolley"),
    "ES00001379B": (5600, "Pallet"),
    "LL00000314A": (4000, "Pallet"),
    "CS00008763A": (90, "Trolley"),
    "BE0203B008MNNA": (1980, "Pallet"),
    "BW00001435BMB": (24000, "Pallet"),
    "BW00001435BKB": (24000, "Pallet"),
    "ES00001380B": (5600, "Pallet"),
    "ZZ00000696B": (200, "Pallet"),
    "EP00001127A": (200, "Pallet"),
    "ES00001381B": (5600, "Pallet"),
    "CS00000017A": (360, "Pallet"),
    "CS00000017OEA": (480, "Pallet"),
    "CS00000023A": (144, "Pallet"),
    "CS00000023OEA": (216, "Pallet"),
    "EP00000337C": (6000, "Pallet"),
    "BD0106A001MNNZ0F": (240, "Pallet"),
    "BW00001440A": (16, "FLC"),
    "ZZ00000858A": (56, "Pallet"),
    "BW00000026C": (200, "Pallet"),
    "ZZ00000918A": (9600, "Pallet"),
    "ZZ00000919A": (16000, "Pallet"),
    "BE1001A001MNNZ0C": (320, "Pallet"),
    "BE1001A002MNNZ0C": (320, "Pallet"),
    "CS00000394B": (448, "Pallet"),
    "CS00000393B": (448, "Pallet"),
    "BW00000341F": (21, "Trolley"),
    "MO00000010B": (72, "FLC"),
    "DB00000982A": (160, "Pallet"),
    "BF0805F003MNNZ0D": (1600, "Pallet"),
    "CS00003149C": (120, "Trolley"),
    "ZZ00000826TDA": (21, "FLC"),
    "ZZ00000842TDA": (32, "FLC"),
    "ZZ00000854TDA": (60, "FLC"),
    "ZZ00000853TDA": (60, "FLC"),
    "ZZ00000825TDA": (21, "FLC"),
    "ZZ00000827TDA": (21, "FLC"),
    "ZZ00000820TDA": (18, "FLC"),
    "ZZ00000842YW0A": (32, "FLC"),
    "ZZ00000853YW0A": (60, "Trolley"),
    "ZZ00000827YW0A": (21, "FLC"),
    "ZZ00000854YW0A": (60, "Trolley"),
    "ZZ00000821YW0A": (110, "Trolley"),
    "ZZ00000826YW0A": (21, "FLC"),
    "ZZ00000822YW0A": (110, "Trolley"),
    "ZZ00000825YW0A": (21, "FLC"),
    "BS00000340C": (200, "Trolley"),
    "BS00000341B": (14400, "Pallet"),
    "BS00000342B": (14400, "Pallet"),
    "BS00000343B": (14400, "Pallet"),
    "BS00000344B": (14400, "Pallet"),
    "BD0107B001MHSZ0D": (60, "FLC"),
    "BD0104E001MHSZ0E": (36, "FLC"),
    "BD0104D001MHSZ0E": (36, "FLC"),
    "BD0103A001MHSZ0E": (40, "FLC"),
    "BD0103A002MHSZ0E": (40, "FLC"),
    "BD0105B002MHSZ0D": (66, "FLC"),
    "BD0105C001MHSZ0D": (66, "FLC"),
    "BD0105A002MHSZ0E": (24, "FLC"),
    "BD0105A001MHSZ0E": (24, "FLC"),
    "ZZ00000821TDA": (110, "FLC"),
    "ZZ00000822TDA": (110, "FLC"),
    "WT00000079B": (200, "Pallet"),
    "DB00000362B": (128, "Pallet"),
    "WT00000080B": (200, "Pallet"),
    "DB00000897A": (128, "Pallet"),
    "WT00000110A": (200, "Pallet"),
    "WT00000111A": (200, "Pallet"),
    "CS00003169A": (64, "Pallet"),
    "ZZ00000884A": (400, "Pallet"),
    "EP00000944B": (1920, "Pallet"),
    "EP00001193A": (1920, "Pallet"),
    "340A0004176D": (2400, "Pallet"),
    "BD0812A001MNNZ0A": (2400, "Pallet"),
    "BW00001259SYA": (24, "FLC"),
    "BW00001437GDA": (6, "FLC"),
    "BW00000472GDB": (19, "FLC"),
    "BW00000473GDB": (19, "FLC"),
    "BW00000473SYB": (19, "FLC"),
    "BW00001486FBC": (28, "FLC"),
    "CS00004044B": (4800, "Pallet"),
    "BE0201J002ANNZ0C": (1440, "Pallet"),
    "BW00001144GDA": (2496, "FLC"),
    "BW00001979A": (144, "Pallet"),
    "TS00000237A": (288, "Pallet"),
    "340A0006351J": (200, "Pallet"),
    "ZZ00000830C": (60, "FLC"),
    "ZZ00000831C": (60, "FLC"),
    "ZZ00000825WE0A": (21, "FLC"),
    "ZZ00000842WE0A": (32, "FLC"),
    "BD0104D001MWEZE": (36, "FLC"),
    "ZZ00000827WE0A": (21, "FLC"),
    "BD0103A002MGYZE": (40, "FLC"),
    "ZZ00000853WE0A": (60, "FLC"),
    "BD0105A002MGYZE": (24, "FLC"),
    "BD0105A002MWEZE": (24, "FLC"),
    "BD0105B002MWEZD": (66, "FLC"),
    "BD0105C001MWEZD": (66, "FLC"),
    "BD0107B001MGYZD": (60, "FLC"),
    "BD0107B001MWEZD": (60, "FLC"),
    "BF0503B012ANNE": (320, "Pallet"),
    "ZZ00000820SY0A": (32, "FLC"),
    "BF0503B020ANNF": (360, "Pallet"),
    "BW00000323BE0A": (28, "FLC"),
    "ZZ00000820WE0A": (18, "FLC"),
    "BF0101C004ANNB": (192, "Trolley"),
    "BD0103A001MWEZE": (40, "FLC"),
    "BD0103A002MWEZE": (40, "FLC"),
    "BD0104E001MWEZE": (36, "FLC"),
    "ZZ00000854WE0A": (60, "FLC"),
    "BD0105A001MWEZE": (24, "FLC"),
    "BD0105C001MGYZD": (66, "FLC"),
    "BD0105C001MTRZD": (66, "FLC"),
    "ZZ00000821SY0A": (120, "FLC"),
    "ZZ00000821WE0A": (110, "FLC"),
    "ZZ00000822SY0A": (120, "FLC"),
    "ES00001449B": (800, "Pallet"),
    "ZZ00000822WE0A": (110, "FLC"),
    "ES00002012B": (800, "Pallet"),
    "ZZ00000825SY0A": (21, "FLC"),
    "ZZ00000826SY0A": (21, "FLC"),
    "BW00000293C": (36, "Trolley"),
    "ZZ00000826WE0A": (21, "FLC"),
    "ZZ00000827SY0A": (24, "FLC"),
    "ZZ00000842SY0A": (36, "FLC"),
    "ZZ00000853SY0A": (60, "FLC"),
    "ZZ00000854SY0A": (60, "FLC"),
    "ZZ00000907A": (120, "Pallet"),
    "ZZ00000912B": (150, "Pallet"),
    "BW00000324BE0A": (28, "FLC"),
    "ZZ00000913A": (120, "Pallet"),
    "340A0013728C": (4800, "Pallet"),
    "BW00000479A": (40, "Pallet"),
    "BY00000583E": (480, "Pallet"),
    "BW00000363B": (200, "Pallet"),
    "ZZ00000779C": (150, "Pallet"),
    "BD0105A001MTRZE": (24, "FLC"),
    "BD0105B002MGYZD": (66, "FLC"),
    "340A0020366OEZ0H": (72, "FLC"),
    "BD0103A001MGYZE": (40, "FLC"),
    "BF0503B009ANNE": (1518, "Pallet"),
    "BF0503B015ANNG": (5280, "Pallet"),
    "BW00000032YW0A": (21, "FLC"),
    "BW00001262A": (1440, "Pallet"),
    "CS00000116A": (360, "Pallet"),
    "CS00001792B": (1078, "Pallet"),
    "WT00000001A": (600, "Pallet"),
    "ZZ00000221A": (720, "Pallet"),
    "ZZ00000695A": (182, "FLC"),
    "340A0021170Z0D": (182, "FLC"),
    "BW00001224A": (3600, "Pallet"),
    "CS00003169MGA": (32, "Trolley"),
    "WT00000002B": (600, "Pallet"),
    "ZZ00000800A": (720, "Pallet"),
    "BE0201G015AOEZ0A": (72, "FLC"),
    "BW00001126A": (3200, "Pallet"),
    "CS00000221MGB": (32, "Trolley"),
    "BW00001088A": (952, "Pallet"),
    "CS00000023RDA": (72, "Pallet"),
    "340A0021171Z0D": (525, "Pallet"),
    "BW00000960B": (192, "Pallet"),
    "CS00000017RDA": (240, "Pallet"),
    "BW00000906A": (1000, "Pallet"),
    "BD0104D001MGYZE": (36, "FLC"),
    "BW00000362B": (200, "FLC"),
    "BW00000478GDA": (60, "Pallet"),
    "BD0104E001MGYZE": (36, "FLC"),
    "BD0105A001MGYZE": (24, "FLC"),
    "340A0020366Z0H": (150, "Pallet"),
    "340A0020370Z0G": (336, "FLC"),
    "340A0020368Z0G": (150, "Pallet"),
    "340A0020371Z0G": (336, "FLC"),
    "BD0103A001MTRZE": (40, "FLC"),
    "BE0201G014ANNZ0A": (400, "Pallet"),
    "BE0201G015ANNZ0A": (550, "Pallet"),
    "BE0201G016ANNZ0A": (600, "Pallet"),
    "BD0103A002MTRZE": (40, "FLC"),
    "BD0104D001MTRZE": (36, "FLC"),
    "BD0104E001MTRZE": (36, "FLC"),
    "BD0105A002MTRZE": (24, "FLC"),
    "BD0107B001MTRZD": (60, "FLC"),
    "CS00000044D": (200, "Pallet"),
    "EP00000118B": (1600, "Pallet"),
    "ZZ00000772D": (2000, "Pallet"),
    "BD0105B002MTRZD": (66, "FLC"),
    "BE0201B001MNNZ0A": (1200, "Pallet"),
    "ZZ00000778C": (150, "Pallet"),
    "BE0401A002MNNZ0C": (72, "FLC"),
    "BF0503B020ANNE": (360, "Pallet"),
    "BY00004219B": (480, "Pallet"),
    "ZZ000007840A": (70, "Pallet"),
    "EP00000352B": (8955, "Pallet"),
    "EP00000092A": (4130, "Pallet"),
    "BW00001144SYA": (2496, "Pallet"),
    "BE0201B002MNNZ0A": (1200, "Pallet"),
    "CS00002770B": (550, "Pallet"),
    "BD0201G001MGNZ0B": (720, "Pallet"),
    "EP00000628B": (200, "Pallet"),
    "EP00001023B": (200, "Pallet"),
    "BD0201G002MGNZ0B": (720, "Pallet"),
    "BD0201G001MYWZ0B": (720, "Pallet"),
    "BD0103A002MSBZ0E": (30, "Trolley"),
    "BD0104D001MSBZ0E": (60, "Trolley"),
    "BD0201G002MYWZ0B": (720, "Pallet"),
    "BD0105B002MSBZ0D": (85, "Trolley"),
    "BD0107B001MSBZ0D": (84, "Trolley"),
    "BD0105C001MSBZ0D": (85, "Trolley"),
    "BD0105A001MSBZ0E": (36, "Trolley"),
    "BD0103A001MLGZ0E": (30, "Trolley"),
    "BD0103A002MLGZ0E": (30, "Trolley"),
    "BD0105A001MLGZ0E": (36, "Trolley"),
    "BD0105A002MLGZ0E": (36, "Trolley"),
    "BD0105B002MCBZ0D": (85, "Trolley"),
    "BD0104D001MCBZ0E": (60, "Trolley"),
    "BD0107B001MCBZ0D": (84, "Trolley"),
    "MC00000177H": (128, "Pallet"),
    "340A0020368OEZ0G": (72, "Pallet"),
    "BE0205A002MNND": (480, "Pallet"),
    "DB00000707C": (128, "Pallet"),
    "ZZ00000822MB0A": (110, "FLC"),
    "DB00000362D": (128, "Pallet"),
    "ZZ00000820MB0A": (18, "FLC"),
    "DB00000897B": (128, "Pallet"),
    "BD0201G001MWEZ0B": (720, "Pallet"),
    "ZZ00000842MB0A": (32, "FLC"),
    "ZZ00000827MB0A": (21, "FLC"),
    "BW00001210A": (3200, "Pallet"),
    "ZZ00000825MB0A": (21, "FLC"),
    "BW00001211A": (8000, "Pallet"),
    "ZZ00000826MB0A": (21, "FLC"),
    "BD0103A001MSBZ0E": (30, "Trolley"),
    "ZZ00000825SN0A": (21, "FLC"),
    "ZZ00000821MB0A": (110, "FLC"),
    "BD0201G002MWEZ0B": (720, "Pallet"),
    "BD0104E001MSBZ0E": (60, "Trolley"),
    "BD0105A002MSBZ0E": (36, "Trolley"),
    "ZZ00000842SN0A": (32, "FLC"),
    "ZZ00000827SN0A": (21, "FLC"),
    "BD0105B002MLGZ0D": (85, "Trolley"),
    "ZZ00000826SN0A": (21, "FLC"),
    "ZZ00000853MB0A": (60, "FLC"),
    "ZZ00000854MB0A": (60, "FLC"),
    "BD0104E001MLGZ0E": (60, "Trolley"),
    "ZZ00000853SN0A": (60, "FLC"),
    "BD0107B001MLGZ0D": (84, "Trolley"),
    "ZZ00000854SN0A": (60, "FLC"),
    "BD0103A001MCBZ0E": (30, "Trolley"),
    "BD0103A002MCBZ0E": (30, "Trolley"),
    "BD0105C001MCBZ0D": (85, "Trolley"),
    "BD0105A001MCBZ0E": (36, "Trolley"),
    "BD0105A002MCBZ0E": (36, "Trolley"),
    "ZZ00000822SN0A": (110, "FLC"),
    "BD0104D001MLGZ0E": (60, "FLC"),
    "ZZ00000821SN0A": (110, "FLC"),
    "BD0104E001MCBZ0E": (60, "Trolley"),
    "BD0103A001MBEZ0E": (90, "FLC"),
    "BD0103A002MBEZ0E": (60, "FLC"),
    "BD0105B002MBEZ0D": (85, "FLC"),
    "BD0105C001MBEZ0D": (85, "FLC"),
    "BD0104D001MBEZ0E": (60, "FLC"),
    "BD0104E001MBEZ0E": (60, "FLC"),
    "BD0107B001MBEZ0D": (84, "FLC"),
    "DB00000837C": (160, "Pallet"),
    "ZZ00000872A": (200, "Pallet"),
    "BE0202A001MNNG": (30, "FLC"),
    "CS00001081B": (480, "Pallet"),
    "BE0401A002ANNH": (120, "FLC"),
    "BS00000355B": (1000, "Pallet"),
    "BD0105C001MLGZ0D": (85, "Trolley"),
    "BE0203B016MNNC": (60, "FLC"),
    "340A0006333E": (300, "Pallet"),
    "BW00000478DCA": (100, "Pallet"),
    "MC00000316A": (3600, "Pallet"),
    "BD0503B003ANNA": (720, "Pallet")
}

def get_official_pss(part_id: str, desc: str = "") -> Tuple[int, str]:
    pid_raw = str(part_id).strip().upper()
    pid_clean = clean_sap_key(pid_raw)

    if pid_raw in OFFICIAL_PSS_MASTER:
        return OFFICIAL_PSS_MASTER[pid_raw]
    
    # Try normalized clean key match
    for master_k, (qty, ptype) in OFFICIAL_PSS_MASTER.items():
        if clean_sap_key(master_k) == pid_clean:
            return qty, ptype

    # Intelligent fallback
    d = (desc or "").upper()
    if any(k in d for k in ["COVER", "FENDER", "PANEL", "FRAME", "LAMP", "SHROUD"]):
        return 60, "Pallet"
    elif any(k in d for k in ["WHEEL", "TIRE", "TYRE", "SEAT", "HANDLEBAR", "SWINGARM", "FORK"]):
        return 32, "Trolley"
    
    return 60, "FLC"

def find_column_by_priority(df: pd.DataFrame, candidates: List[str], exclude: List[str] = None) -> Optional[str]:
    exclude = exclude or []
    for cand in candidates:
        for col in df.columns:
            c_clean = str(col).strip().lower()
            if cand in c_clean and not any(ex in c_clean for ex in exclude):
                return col
    return None

def clean_sap_key(val: Any) -> str:
    s = re.sub(r'[^A-Za-z0-9]', '', str(val)).upper()
    return s.lstrip("0")

# ---------------------------------------------------------
# 2. BULLETPROOF LX02 PROCESSOR (MATCHED TO LMAO - DATA.CSV)
# ---------------------------------------------------------
VALID_STORAGE_TYPES = ["HRR", "TSA", "IDP"]

def process_lx02_master(lx02_raw: pd.DataFrame) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str], pd.DataFrame]:
    if lx02_raw.empty:
        return {}, {}, {}, pd.DataFrame()

    df = lx02_raw.copy()
    df.columns = [str(c).strip() for c in df.columns]

    mat_col = find_column_by_priority(df, ["material", "matnr"], exclude=["desc", "type", "group", "plant"])
    desc_col = find_column_by_priority(df, ["material description", "description", "maktx"], exclude=["line", "model", "wc"])
    bin_col = find_column_by_priority(df, ["storage bin", "bin", "lgpla"], exclude=["type"])
    su_col = find_column_by_priority(df, ["storage unit", "lenum", "su number", "su"], exclude=["type"])
    type_col = find_column_by_priority(df, ["storage type", "type", "lgtyp"], exclude=["bin", "unit"])
    qty_col = find_column_by_priority(df, ["available stock", "verme", "unrestricted", "available", "total stock"], exclude=["category", "segment", "putaway", "type", "unit"])
    cat_col = find_column_by_priority(df, ["stock category", "category"])

    clean_df = pd.DataFrame()
    clean_df["Material"] = df[mat_col].astype(str).str.strip() if mat_col else ""
    clean_df["Description"] = df[desc_col].astype(str).str.strip() if desc_col else ""
    clean_df["Storage_Bin"] = df[bin_col].astype(str).str.strip() if bin_col else ""
    clean_df["Storage_Type"] = df[type_col].astype(str).str.strip() if type_col else ""
    clean_df["Stock_Category"] = df[cat_col].fillna("").astype(str).str.strip() if cat_col else ""

    if su_col:
        clean_df["SU_Number"] = df[su_col].fillna("").astype(str).str.strip()
        clean_df["SU_Number_Clean"] = clean_df["SU_Number"].str.lstrip("0")
    else:
        clean_df["SU_Number"] = ""
        clean_df["SU_Number_Clean"] = ""

    if qty_col:
        clean_df["SU_Qty"] = pd.to_numeric(
            df[qty_col].astype(str).str.replace(",", "").str.strip(), errors="coerce"
        ).fillna(0.0)
    else:
        clean_df["SU_Qty"] = 0.0

    clean_df = clean_df[clean_df["Material"].str.len() > 3]

    location_map = {}
    desc_map = {}
    type_map = {}
    clean_df["Is_Physical"] = clean_df["Storage_Type"].isin(VALID_STORAGE_TYPES)
    sorted_df = clean_df.sort_values(by=["Is_Physical", "SU_Qty"], ascending=[False, False])

    for _, row in sorted_df.iterrows():
        raw_mat = str(row["Material"]).strip().upper()
        norm_key = clean_sap_key(raw_mat)
        bin_val = str(row["Storage_Bin"]).strip()
        desc_val = str(row["Description"]).strip()
        type_val = str(row["Storage_Type"]).strip()

        if bin_val not in ["", "nan", "None"]:
            if raw_mat not in location_map:
                location_map[raw_mat] = bin_val
                if desc_val and desc_val not in ["nan", "None"]: desc_map[raw_mat] = desc_val
                if type_val and type_val not in ["nan", "None"]: type_map[raw_mat] = type_val
            if norm_key not in location_map:
                location_map[norm_key] = bin_val
                if desc_val and desc_val not in ["nan", "None"]: desc_map[norm_key] = desc_val
                if type_val and type_val not in ["nan", "None"]: type_map[norm_key] = type_val

    clean_df = clean_df[clean_df["SU_Qty"] > 0]
    clean_df = clean_df[clean_df["Stock_Category"].isin(["", "nan", "None"])]

    # Returns location, description, and storage type maps
    return location_map, desc_map, type_map, clean_df
# ---------------------------------------------------------
# 3. SCALEX SCRAPER & FILE LOADER
# ---------------------------------------------------------
def extract_batches_from_text(raw_text: str) -> List[dict]:
    pattern = re.compile(
        r"([A-Za-z0-9_\-\/]+)\s+(HOLD|PLAN|RUNNING|WIP|ACTIVE)\s+(Line[\s_]\d+|\S+)\s+(\d+)\s+(\d+)\s+(\d+)",
        re.IGNORECASE,
    )
    records = []
    for match in pattern.finditer(raw_text):
        sku, status, line, s_start, s_end, count = match.groups()
        clean_line = "Line_1" if "1" in line else ("Line_2" if "2" in line else line.strip())
        records.append({
            "SKUName": sku.strip().upper(),
            "Status": status.strip().upper(),
            "Line": clean_line,
            "Serial_Start": int(s_start),
            "Serial_End": int(s_end),
            "Count": int(count),
        })
    return records

def select_line_and_fetch(page: Page, line_name: str) -> pd.DataFrame:
    try:
        dropdown_found = False
        for target in [page] + page.frames:
            for sel in target.query_selector_all("select"):
                opt_text = sel.inner_text().upper()
                if "LINE_1" in opt_text or "LINE_2" in opt_text or "LINE 1" in opt_text:
                    try:
                        sel.select_option(label=line_name)
                        dropdown_found = True
                        break
                    except Exception:
                        sel.select_option(value=line_name)
                        dropdown_found = True
                        break
            if dropdown_found:
                break

        time.sleep(1.5)
        for target in [page] + page.frames:
            btn = target.query_selector("input[value*='View Report'], button:has-text('View Report')")
            if btn:
                btn.click()
                break

        for _ in range(12):
            time.sleep(2)
            all_texts = [page.inner_text("body")] + [f.inner_text("body") for f in page.frames]
            for text in all_texts:
                if "SERIAL NO START" in text.upper() or "SERIAL_START" in text.upper():
                    records = extract_batches_from_text(text)
                    if records:
                        df = pd.DataFrame(records)
                        target_clean = "line_1" if "1" in line_name.lower() else "line_2"
                        df_line = df[df["Line"].str.lower() == target_clean]
                        if not df_line.empty:
                            return df_line
    except Exception:
        pass
    return pd.DataFrame()

def fetch_all_scalex_data() -> pd.DataFrame:
    all_batches = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=True)

        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        try:
            page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a, span'));
                const target = links.find(el => (el.innerText || '').trim() === 'Plan Sequence');
                if (target) target.click();
            }""")
            time.sleep(5)
        except Exception:
            pass

        df_l1 = select_line_and_fetch(page, "Line_1")
        if not df_l1.empty:
            all_batches.append(df_l1)

        df_l2 = select_line_and_fetch(page, "Line_2")
        if not df_l2.empty:
            all_batches.append(df_l2)

        browser.close()

    if not all_batches:
        return pd.DataFrame()

    combined_df = pd.concat(all_batches, ignore_index=True)
    combined_df.to_csv(CSV_BATCHES, index=False)
    return combined_df

def process_open_sto(sto_raw: pd.DataFrame) -> Tuple[Dict[str, float], Dict[str, str]]:
    """
    Parses Open Stock Transport Orders (W004 -> M002) with bulletproof column resolution.
    Guarantees zero KeyError: None crashes.
    """
    if sto_raw.empty:
        return {}, {}

    df = sto_raw.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Resolve Material Column
    mat_col = find_column_by_priority(df, ["material", "matnr", "part", "item", "article"], exclude=["desc", "text", "unit"])
    if not mat_col:
        # Fallback to column 0 if material keyword not in header
        mat_col = df.columns[0]

    # Resolve Quantity Column with broad STO keywords
    qty_col = find_column_by_priority(
        df, 
        ["quantity", "order quantity", "menge", "qty", "transit", "delivered", "scheduled", "open", "balance"],
        exclude=["unit", "price", "value", "amt", "currency"]
    )

    # Fallback: if no qty column found by name, locate first numeric column
    if not qty_col:
        for c in df.columns:
            cleaned_series = pd.to_numeric(df[c].astype(str).str.replace(",", "").str.strip(), errors="coerce")
            if cleaned_series.notna().sum() > len(df) * 0.5:
                qty_col = c
                break

    # If still not found, return empty gracefully without crashing
    if not qty_col or not mat_col:
        return {}, {}

    po_col = find_column_by_priority(df, ["purchasing document", "po number", "ebeln", "order", "document", "po"], exclude=["item", "type", "date"])
    plant_col = find_column_by_priority(df, ["plant", "receiving plant", "werks"], exclude=["supplying"])
    supp_col = find_column_by_priority(df, ["supplying plant", "reswk", "issuing"])

    # Filter for W004 -> M002 if plant columns exist
    if supp_col and any("W004" in str(x).upper() for x in df[supp_col].dropna()):
        df = df[df[supp_col].astype(str).str.upper().str.contains("W004")]
    if plant_col and any("M002" in str(x).upper() for x in df[plant_col].dropna()):
        df = df[df[plant_col].astype(str).str.upper().str.contains("M002")]

    df["Clean_Qty"] = pd.to_numeric(
        df[qty_col].astype(str).str.replace(",", "").str.strip(), errors="coerce"
    ).fillna(0.0)
    df["Mat_Clean"] = df[mat_col].apply(clean_sap_key)

    sto_qty_map = df.groupby("Mat_Clean")["Clean_Qty"].sum().to_dict()

    sto_po_map = {}
    if po_col:
        for mat, group in df.groupby("Mat_Clean"):
            pos = group[po_col].dropna().astype(str).unique()
            sto_po_map[mat] = ", ".join(pos)

    return sto_qty_map, sto_po_map

def load_uploaded_file(uploaded_file) -> pd.DataFrame:
    if uploaded_file is None:
        return pd.DataFrame()
    
    file_name = getattr(uploaded_file, "name", "").lower()
    df = None

    # 1. Read Excel (.xlsx, .xls)
    if file_name.endswith((".xlsx", ".xls")):
        try:
            df = pd.read_excel(uploaded_file, dtype=str)
        except Exception as e:
            st.error(f"❌ Failed to parse Excel file '{file_name}': {e}")
            return pd.DataFrame()

    # 2. Read CSV / TSV with Multi-Encoding Fallback
    else:
        for enc in ["utf-8", "latin1", "cp1252", "iso-8859-1"]:
            for sep in [",", "\t", ";"]:
                try:
                    if hasattr(uploaded_file, "seek"):
                        uploaded_file.seek(0)
                    df = pd.read_csv(uploaded_file, dtype=str, sep=sep, encoding=enc)
                    if df is not None and len(df.columns) > 1:
                        break
                except Exception:
                    continue
            if df is not None and len(df.columns) > 1:
                break

    if df is None or df.empty:
        st.error(f"❌ File '{file_name}' was loaded but contains 0 rows.")
        return pd.DataFrame()

    # ---------------------------------------------------------
    # SMART SAP HEADER DETECTOR (WORKS FOR MB52, LX02, BOM, STO)
    # ---------------------------------------------------------
    # Checks if headers are already clean
    cols_clean = [str(c).strip().lower() for c in df.columns]
    has_valid_header = any(k in cols_clean for k in ["material", "matnr", "child material", "part id"])

    # If top rows contain SAP title metadata, find the real header row
    if not has_valid_header:
        for i in range(min(15, len(df))):
            row_vals = [str(x).strip().lower() for x in df.iloc[i].values]
            
            # Universal match: Any row that has a Material column + a Location, Plant, or Qty column
            has_mat = any("material" in v or "matnr" in v or "part" in v for v in row_vals)
            has_secondary = any(k in row_vals for k in ["plant", "plnt", "unrestricted", "storage bin", "storage location", "available stock", "comp qty"])
            
            if has_mat and has_secondary:
                df.columns = [str(x).strip() for x in df.iloc[i].values]
                df = df.iloc[i+1:].reset_index(drop=True)
                break

    df.columns = [str(c).strip() for c in df.columns]
    return df
def check_hold_to_plan_transitions(current_batches: pd.DataFrame) -> List[str]:
    current_state = {}
    for _, b in current_batches.iterrows():
        key = f"{b['Line']}#{b['ScaleX_Sequence_No']}"
        current_state[key] = {
            "status": str(b["Status"]).upper().strip(),
            "sku": str(b["SKUName"]).strip(),
            "count": int(b["Count"])
        }

    released_alerts = []
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r") as f:
                prev_state = json.load(f)

            for key, curr in current_state.items():
                if key in prev_state:
                    old_status = prev_state[key]["status"]
                    new_status = curr["status"]
                    if old_status == "HOLD" and new_status in ["PLAN", "RUNNING"]:
                        line_name, seq_no = key.split("#")
                        released_alerts.append(
                            f"🔔 **HOLD LIFTED:** {line_name} Seq **#{seq_no}** ({curr['sku']}, {curr['count']} units) has been released to **PLAN**! Immediate pick required."
                        )
        except Exception:
            pass

    try:
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(current_state, f)
    except Exception:
        pass

    return released_alerts

# ---------------------------------------------------------
# 4. TAKT TIME & MULTI-SEQUENCE TRACEABILITY ENGINE
# ---------------------------------------------------------
def build_description_map(mb52_df: pd.DataFrame, lx02_df: pd.DataFrame, bom_df: pd.DataFrame) -> Dict[str, str]:
    desc_map = {}
    
    # 1. BOM (Fallback)
    if not bom_df.empty:
        df = bom_df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        mat_col = find_column_by_priority(df, ["child material", "component", "material", "part"], exclude=["desc", "type", "group"])
        desc_col = find_column_by_priority(df, ["component description", "material description", "part description", "description", "desc", "maktx"], exclude=["line", "model", "machine", "wc", "work", "plant"])
        if mat_col and desc_col:
            for _, r in df.iterrows():
                m = str(r[mat_col]).strip().upper()
                d = str(r[desc_col]).strip()
                if m and d and d.lower() not in ["nan", "none", ""]:
                    desc_map[m] = d
                    desc_map[m.lstrip("0")] = d

    # 2. MB52
    if not mb52_df.empty:
        df = mb52_df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        mat_col = find_column_by_priority(df, ["material", "matnr", "part"], exclude=["desc", "type", "group"])
        desc_col = find_column_by_priority(df, ["material description", "component description", "part description", "description", "desc", "maktx"], exclude=["line", "model", "machine", "wc", "work", "plant"])
        if mat_col and desc_col:
            for _, r in df.iterrows():
                m = str(r[mat_col]).strip().upper()
                d = str(r[desc_col]).strip()
                if m and d and d.lower() not in ["nan", "none", ""]:
                    desc_map[m] = d
                    desc_map[m.lstrip("0")] = d

    # 3. LX02 (Primary Source)
    if not lx02_df.empty:
        df = lx02_df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        mat_col = find_column_by_priority(df, ["material", "matnr", "child material", "part id", "part no"], exclude=["desc", "type", "group"])
        desc_col = find_column_by_priority(df, ["material description", "component description", "part description", "description", "desc", "maktx"], exclude=["line", "model", "machine", "wc", "work", "plant"])
        if mat_col and desc_col:
            for _, r in df.iterrows():
                m = str(r[mat_col]).strip().upper()
                d = str(r[desc_col]).strip()
                if m and d and d.lower() not in ["nan", "none", ""]:
                    desc_map[m] = d
                    desc_map[m.lstrip("0")] = d

    return desc_map

def resolve_dispatch_sequences(
    batches_df: pd.DataFrame,
    target_units: Optional[int] = None,
    manual_seq_list: Optional[List[int]] = None,
    l1_start: int = 1,
    l2_start: int = 1
) -> Tuple[Set[str], Set[str]]:
    """
    Calculates active 'Line#Seq' keys based on Target Production Qty (minutes/units)
    or manual sequence entry.
    Returns:
        active_keys: set of 'Line#Seq' strings included in the picklist
        cutoff_keys: set of 'Line#Seq' strings that mark the final cutoff sequence
    """
    active_keys = set()
    cutoff_keys = set()

    plan_df = batches_df[batches_df["Status"] == "PLAN"].copy()

    # Case A: User entered a Target Production Qty (e.g. 180 or 240)
    if target_units and target_units > 0:
        for line_name, start_seq in [("Line_1", l1_start), ("Line_2", l2_start)]:
            line_batches = plan_df[
                (plan_df["Line"].str.lower() == line_name.lower()) &
                (plan_df["ScaleX_Sequence_No"] >= start_seq)
            ].sort_values(by="ScaleX_Sequence_No")

            cum_qty = 0
            last_key = None
            for _, b in line_batches.iterrows():
                seq_no = int(b["ScaleX_Sequence_No"])
                count = int(b["Count"])
                key = f"{line_name}#{seq_no}"

                active_keys.add(key)
                last_key = key
                cum_qty += count

                if cum_qty >= target_units:
                    break

            if last_key:
                cutoff_keys.add(last_key)

    # Case B: User manually selected sequences
    elif manual_seq_list:
        for _, b in plan_df[plan_df["ScaleX_Sequence_No"].isin(manual_seq_list)].iterrows():
            active_keys.add(f"{b['Line']}#{int(b['ScaleX_Sequence_No'])}")

    # Case C: All sequences
    else:
        for _, b in plan_df.iterrows():
            active_keys.add(f"{b['Line']}#{int(b['ScaleX_Sequence_No'])}")

    return active_keys, cutoff_keys

def calculate_band2_and_pss_engine(
    batches_df: pd.DataFrame,
    mb52_df: pd.DataFrame,
    cogi_df: pd.DataFrame,
    sto_df: pd.DataFrame,
    selected_line: str,
    hide_hold: bool,
    active_seq_keys: Set[str],   # <-- UPDATED
    calc_formula_mode: str = "Standard Net Deficit"
) -> pd.DataFrame:
    if "uploaded_bom_df" in st.session_state:
        bom_df = st.session_state["uploaded_bom_df"].copy()
    elif LOCAL_BOM_PATH and os.path.exists(LOCAL_BOM_PATH):
        bom_df = pd.read_csv(LOCAL_BOM_PATH, dtype=str) if LOCAL_BOM_PATH.endswith(".csv") else pd.read_excel(LOCAL_BOM_PATH, dtype=str)
    else:
        st.error("🚨 BOM file (`bom.csv`) not found. Upload it in the sidebar.")
        return pd.DataFrame()

    plan_batches = batches_df[batches_df["Status"] == "PLAN"].copy()

    if selected_line != "All":
        plan_batches = plan_batches[plan_batches["Line"].str.lower() == selected_line.lower()]
    
    # Filter using Line#Seq keys
    if active_seq_keys:
        plan_batches["Line_Seq_Key"] = plan_batches["Line"] + "#" + plan_batches["ScaleX_Sequence_No"].astype(str)
        plan_batches = plan_batches[plan_batches["Line_Seq_Key"].isin(active_seq_keys)]

    if plan_batches.empty:
        st.warning("⚠️ No active PLAN batches match the selected sequence filter.")
        return pd.DataFrame()

    bom_clean = bom_df.copy()
    bom_clean.columns = [str(c).strip() for c in bom_clean.columns]
    parent_col = find_column_by_priority(bom_clean, ["parent material", "parent", "sku", "vehicle"]) or "Parent Material"
    child_col = find_column_by_priority(bom_clean, ["child material", "component", "material", "part"]) or "Child Material"
    qty_col = find_column_by_priority(bom_clean, ["comp qty", "quantity", "qty", "menge"]) or "Comp Qty."
    
    desc_col = find_column_by_priority(
        bom_clean, 
        ["component description", "material description", "part name", "part description", "description", "desc", "maktx"],
        exclude=["line", "model", "machine", "wc", "work", "plant"]
    ) or "Description"

    bom_clean["Parent_Clean"] = bom_clean[parent_col].astype(str).str.strip().str.upper()
    bom_clean["Clean_Qty"] = pd.to_numeric(
        bom_clean[qty_col].astype(str).str.replace(",", "").str.strip(), errors="coerce"
    ).fillna(0.0)

    line_offsets = {"line_1": 0, "line_2": 0}
    batch_takt_minutes = {}

    for _, b in plan_batches.sort_values(by=["Line", "ScaleX_Sequence_No"], ascending=[True, True]).iterrows():
        b_line = str(b["Line"]).lower()
        seq_k = f"{b['Line']}#{b['ScaleX_Sequence_No']}"
        batch_takt_minutes[seq_k] = line_offsets.get(b_line, 0)
        line_offsets[b_line] = line_offsets.get(b_line, 0) + int(b["Count"])

    sequence_rows = []
    unmatched_skus = set()

    for _, batch in plan_batches.iterrows():
        sku = str(batch["SKUName"]).strip().upper()
        line = batch["Line"]
        seq_no = int(batch["ScaleX_Sequence_No"])
        count = int(batch["Count"])
        start_min = batch_takt_minutes.get(f"{line}#{seq_no}", 0)

        sku_bom = bom_clean[bom_clean["Parent_Clean"] == sku]
        if sku_bom.empty:
            unmatched_skus.add(sku)
            continue

        for _, comp in sku_bom.iterrows():
            sequence_rows.append({
                "Line": line,
                "Sequence_Order": seq_no,
                "Vehicle_SKU": sku,
                "Child Material": str(comp[child_col]).strip(),
                "Description": str(comp[desc_col]).strip() if desc_col in comp else "",
                "Required_Qty": comp["Clean_Qty"] * count,
                "Minute_Offset": start_min,
            })

    if unmatched_skus:
        st.warning(f"⚠️ Production plan SKUs not found in BOM: `{', '.join(unmatched_skus)}`")

    df_seq = pd.DataFrame(sequence_rows)
    if df_seq.empty:
        st.error("🚨 Zero materials exploded. Verify SKU names in BOM match ScaleX.")
        return pd.DataFrame()

    def build_sequence_coverage_str(sub_df):
        coverage = []
        for l_name in ["Line_1", "Line_2"]:
            l_seqs = sub_df[sub_df["Line"].str.lower() == l_name.lower()]["Sequence_Order"].unique()
            if len(l_seqs) > 0:
                coverage.append(f"{'L1' if '1' in l_name else 'L2'}: #" + ", #".join(map(str, sorted(l_seqs))))
        return " | ".join(coverage)

    lx02_in = st.session_state.get("lx02_df", pd.DataFrame())
    desc_map = build_description_map(mb52_df, lx02_in, bom_clean)

    grouped = []
    for child_mat, group_data in df_seq.groupby("Child Material"):
        earliest_min = int(group_data["Minute_Offset"].min())
        seq_str = build_sequence_coverage_str(group_data)
        sku_str = ", ".join(sorted(list(set(group_data["Vehicle_SKU"]))))
        earliest_seq = int(group_data["Sequence_Order"].min())

        bom_desc = str(group_data["Description"].iloc[0]).strip() if "Description" in group_data.columns else ""
        c_mat_clean = child_mat.lstrip("0")
        resolved_desc = desc_map.get(child_mat) or desc_map.get(c_mat_clean) or bom_desc
        
        if earliest_seq <= 3 or earliest_min <= 60:
            prio = "🚨 P1: CRITICAL (<60m)"
        elif earliest_min <= 120:
            prio = "🟡 P2: HIGH (60-120m)"
        else:
            prio = "🟢 P3: NORMAL (>120m)"

        grouped.append({
            "Child Material": child_mat,
            "Description": resolved_desc,
            "Sequence_Order": earliest_seq,
            "Total_Demand": group_data["Required_Qty"].sum(),
            "Starvation_Mins": earliest_min,
            "Priority": prio,
            "Sequences_Covered": seq_str,
            "Vehicle_SKU": sku_str,
        })

    aggregated = pd.DataFrame(grouped).sort_values(by=["Sequence_Order", "Starvation_Mins"], ascending=[True, True])

    # Ingest MB52
    if not mb52_df.empty:
        mb = mb52_df.copy()
        mb.columns = [c.strip() for c in mb.columns]
        mat_c = find_column_by_priority(mb, ["material", "matnr", "part"], exclude=["desc", "text"]) or "Material"
        plant_c = find_column_by_priority(mb, ["plant", "plnt", "werks"]) or "Plant"
        sloc_c = find_column_by_priority(mb, ["storage location", "sloc", "lgort"]) or "Storage Location"
        unrest_c = find_column_by_priority(mb, ["unrestricted", "available", "stock", "labst"], exclude=["value", "transit", "blocked"]) or "Unrestricted"

        mb["Stock_Qty"] = pd.to_numeric(mb[unrest_c].astype(str).str.replace(",", "").str.strip(), errors="coerce").fillna(0.0)
        mb["Mat_Clean"] = mb[mat_c].apply(clean_sap_key)
        mb["Plant_Clean"] = mb[plant_c].astype(str).str.strip().str.upper()
        mb["SLoc_Clean"] = mb[sloc_c].astype(str).str.strip().str.upper() if sloc_c in mb.columns else "RM01"

        # M002: Only include RM01 and PS01 storage locations (strictly ignores PV01 and others)
        m002_df = mb[
            (mb["Plant_Clean"].str.contains("M002")) & 
            (mb["SLoc_Clean"].isin(["RM01", "PS01"]))
        ]
        m002_stock_map = m002_df.groupby("Mat_Clean")["Stock_Qty"].sum().to_dict()

        # W004: Only include RM01 storage location
        w004_df = mb[
            (mb["Plant_Clean"].str.contains("W004")) & 
            (mb["SLoc_Clean"].str.contains("RM01"))
        ]
        w004_stock_map = w004_df.groupby("Mat_Clean")["Stock_Qty"].sum().to_dict()

        aggregated["Mat_Clean"] = aggregated["Child Material"].apply(clean_sap_key)
        aggregated["M002_Stock"] = aggregated["Mat_Clean"].map(lambda k: m002_stock_map.get(k, 0.0))
        aggregated["W004_Stock"] = aggregated["Mat_Clean"].map(lambda k: w004_stock_map.get(k, 0.0))
    else:
        aggregated["M002_Stock"] = 0.0
        aggregated["W004_Stock"] = 0.0

    # Ingest COGI
    if not cogi_df.empty:
        cogi = cogi_df.copy()
        cogi.columns = [c.strip() for c in cogi.columns]
        c_mat = next((c for c in cogi.columns if "material" in c.lower()), None)
        c_qty = next((c for c in cogi.columns if "qty" in c.lower() or "quantity" in c.lower()), None)
        if c_mat:
            if not c_qty:
                c_qty = [c for c in cogi.columns if c != c_mat][0]
            cogi["Clean_COGI"] = pd.to_numeric(cogi[c_qty].astype(str).str.replace(",", "").str.strip(), errors="coerce").fillna(0)
            cogi_sum = cogi.groupby(c_mat)["Clean_COGI"].sum().reset_index()
            cogi_sum.columns = ["Child Material", "COGI_Error"]
            aggregated = pd.merge(aggregated, cogi_sum, on="Child Material", how="left")
            aggregated["COGI_Error"] = aggregated["COGI_Error"].fillna(0)
        else:
            aggregated["COGI_Error"] = 0
    else:
        aggregated["COGI_Error"] = 0

    aggregated["Effective_M002"] = aggregated["M002_Stock"] - aggregated["COGI_Error"]
    aggregated["Effective_M002"] = aggregated["Effective_M002"].apply(lambda x: x if x > 0 else 0)

    # Ingest Open STO
    sto_qty_dict, sto_po_dict = process_open_sto(sto_df)

    final_records = []
    for _, r in aggregated.iterrows():
        part_id = r["Child Material"]
        req = int(r["Total_Demand"])
        m002_raw = r["M002_Stock"]
        m002_eff = r["Effective_M002"]
        cogi_qty = r["COGI_Error"]
        w004 = int(r["W004_Stock"])
        open_sto = int(sto_qty_dict.get(part_id, 0))
        open_pos = sto_po_dict.get(part_id, "")

        if "Experimental Additive" in calc_formula_mode:
            demand_to_fulfil = max(0, int((m002_raw + req + cogi_qty) - open_sto))
        else:
            total_pipeline = m002_eff + open_sto
            demand_to_fulfil = max(0, int(req - total_pipeline))

        box_size, p_type = get_official_pss(part_id, r["Description"])
        
        if demand_to_fulfil > 0:
            ideal_pss_count = math.ceil(demand_to_fulfil / box_size)
            ideal_pick_qty = ideal_pss_count * box_size
            
            if w004 > 0:
                actual_need_pick = min(ideal_pick_qty, w004)
                actual_pss_count = math.ceil(actual_need_pick / box_size)
                
                if actual_need_pick >= demand_to_fulfil:
                    transfer_status = "Ready for Full Transfer"
                else:
                    transfer_status = "Partial Transfer Available"
            else:
                actual_need_pick = 0
                actual_pss_count = 0
                transfer_status = "W004 Out of Stock"
        else:
            actual_need_pick = 0
            actual_pss_count = 0
            if open_sto > 0 and req > m002_eff:
                transfer_status = f"🚚 Covered by Open STO ({open_sto} in transit)"
            else:
                transfer_status = "Sufficient M002 Stock"

        final_records.append({
            "Sequence_Order": r["Sequence_Order"],
            "Part ID": part_id,
            "Part Name": r["Description"],
            "BOM Demand": req,
            "Demand to Fulfil": demand_to_fulfil,
            "Need to be Picked": actual_need_pick,
            "PSS Qty": box_size,
            "To be picked PSS count": actual_pss_count,
            "W004 Stock": w004,
            "M002 Stock": int(m002_raw),
            "COGI Qty": int(cogi_qty),
            "Open STO In-Transit": open_sto,
            "Open PO Numbers": open_pos,
            "Total M002 Pipeline": int(m002_eff + open_sto),
            "PSS Type": p_type,
            "Starvation_Mins": r["Starvation_Mins"],
            "Priority": r["Priority"],
            "Sequences_Covered": r["Sequences_Covered"],
            "Transfer Qty (W004 ➡️ M002)": actual_need_pick,
            "Transfer Status": transfer_status,
            "Is_Zero_Stock": (w004 == 0 and demand_to_fulfil > 0),
        })

    return pd.DataFrame(final_records)

# ---------------------------------------------------------
# 5. UI CONTROLS & TIMED REFRESH
# ---------------------------------------------------------
st.markdown("""
<div class="ather-header">
    <div style="display: flex; justify-content: space-between; align-items: center;">
        <div>
            <span style="font-size: 24px; font-weight: 900; color: #00c853; letter-spacing: 1px;">⚡ ATHER</span>
            <span style="font-size: 18px; font-weight: 700; color: #0f172a; margin-left: 10px;">| 1-Min Takt Replenishment & PSS Kitting Suite</span>
        </div>
        <span style="font-size: 12px; font-weight: bold; background: #e8f5e9; color: #1b5e20; padding: 4px 12px; border-radius: 20px;">
            ● Dual-Line Simultaneous Execution
        </span>
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# SIDEBAR FILTERS & DATA INGESTION
# ---------------------------------------------------------
if "last_sync_timestamp" not in st.session_state:
    st.session_state["last_sync_timestamp"] = time.time()

time_elapsed = int(time.time() - st.session_state["last_sync_timestamp"])
seconds_left = max(0, 600 - time_elapsed)

st.sidebar.header("⚙️ ScaleX & SAP Controls")

calc_mode = st.sidebar.selectbox(
    "Calculation Logic Mode:",
    [
        "Standard Net Deficit (Demand - M002 + COGI - STO)",
        "Experimental Additive (M002 + Demand + COGI - STO)"
    ]
)

auto_sync_enabled = st.sidebar.checkbox("Enable 10-Min Auto-Sync", value=True)

if auto_sync_enabled and time_elapsed >= 600:
    st.session_state["last_sync_timestamp"] = time.time()
    # Runs quietly in background without forcing a full screen reload
    res = fetch_all_scalex_data()
    if not res.empty:
        st.session_state["batches"] = res
        st.session_state["force_recalculate"] = True
        st.toast("🔄 ScaleX Plan synced in background!", icon="ℹ️")

if st.sidebar.button("🔄 Sync Live ScaleX Plan (Now)", type="primary"):
    with st.spinner("Connecting to ScaleX Portal at 192.168.201.248..."):
        st.session_state["last_sync_timestamp"] = time.time()
        batches_result = fetch_all_scalex_data()
        if not batches_result.empty:
            st.session_state["batches"] = batches_result
            st.sidebar.success(f"Synced {len(batches_result)} batches successfully!")
        else:
            st.sidebar.error("Could not reach 192.168.201.248. Checking local CSV.")
            if os.path.exists(CSV_BATCHES):
                st.session_state["batches"] = pd.read_csv(CSV_BATCHES)

if st.sidebar.button("🧹 Clear SAP Cache"):
    st.session_state.pop("mb52_df", None)
    st.session_state.pop("cogi_df", None)
    st.session_state.pop("lx02_df", None)
    st.session_state.pop("sto_df", None)
    st.sidebar.success("Cache cleared!")

st.sidebar.divider()
st.sidebar.subheader("📂 Upload SAP Dumps")
uploaded_mb52 = st.sidebar.file_uploader("Upload MB52 Stock", type=["csv", "xlsx", "xls"], key="uploader_mb52_stock")
uploaded_cogi = st.sidebar.file_uploader("Upload COGI Dump", type=["csv", "xlsx", "xls"])
uploaded_lx02 = st.sidebar.file_uploader("Upload LX02 WM Stock (Bins & SUs)", type=["csv", "xlsx", "xls"])
uploaded_sto = st.sidebar.file_uploader("Upload Open STO / In-Transit", type=["csv", "xlsx", "xls"])
uploaded_bom = st.sidebar.file_uploader("Upload `bom.csv` (If missing locally)", type=["csv", "xlsx"])

if uploaded_bom:
    st.session_state["uploaded_bom_df"] = load_uploaded_file(uploaded_bom)
    st.sidebar.success("BOM Loaded!")

if uploaded_sto:
    st.session_state["sto_df"] = load_uploaded_file(uploaded_sto)
    st.sidebar.success("Open STO Loaded!")
elif LOCAL_STO_PATH and "sto_df" not in st.session_state:
    st.session_state["sto_df"] = load_uploaded_file(open(LOCAL_STO_PATH, "rb"))

if uploaded_mb52 is not None:
    loaded_mb = load_uploaded_file(uploaded_mb52)
    if not loaded_mb.empty:
        st.session_state["mb52_df"] = loaded_mb
        st.sidebar.success(f"✅ MB52 Loaded: **{len(loaded_mb)} rows**")
    else:
        st.sidebar.error("❌ MB52 file was uploaded but could not be parsed.")

if uploaded_cogi:
    st.session_state["cogi_df"] = load_uploaded_file(uploaded_cogi)
    st.sidebar.success("COGI Loaded!")

if uploaded_lx02:
    st.session_state["lx02_df"] = load_uploaded_file(uploaded_lx02)
    st.sidebar.success("LX02 Loaded!")
elif LOCAL_LX02_PATH and "lx02_df" not in st.session_state:
    st.session_state["lx02_df"] = load_uploaded_file(open(LOCAL_LX02_PATH, "rb"))

uploaded_plan = st.sidebar.file_uploader("Upload `plan_batches.csv` (If offline)", type=["csv"])
if uploaded_plan:
    st.session_state["batches"] = pd.read_csv(uploaded_plan)
    st.sidebar.success("Manual Plan Loaded!")

if "batches" not in st.session_state:
    if os.path.exists(CSV_BATCHES):
        st.session_state["batches"] = pd.read_csv(CSV_BATCHES)
    else:
        st.warning("⚠️ No production plan loaded.")
        st.stop()

batches_df = st.session_state["batches"]

rename_map = {}
for c in batches_df.columns:
    c_lower = c.strip().lower()
    if c_lower == "line": rename_map[c] = "Line"
    elif c_lower in ("skuname", "sku", "parent material"): rename_map[c] = "SKUName"
    elif c_lower == "status": rename_map[c] = "Status"
    elif c_lower in ("count", "qty", "quantity"): rename_map[c] = "Count"
    elif c_lower in ("serial_start", "serial no start"): rename_map[c] = "Serial_Start"
    elif c_lower in ("serial_end", "serial no end"): rename_map[c] = "Serial_End"
batches_df.rename(columns=rename_map, inplace=True)

if "Line" not in batches_df.columns:
    batches_df["Line"] = "Line_1"

batches_df["Status"] = batches_df["Status"].astype(str).str.strip().str.upper()

# Assign Sequence Numbers ONLY to active PLAN batches
batches_df["ScaleX_Sequence_No"] = batches_df[batches_df["Status"] == "PLAN"].groupby("Line", sort=False).cumcount() + 1
batches_df["ScaleX_Sequence_No"] = batches_df["ScaleX_Sequence_No"].fillna(0).astype(int)

# ---------------------------------------------------------
# CLIENT-SIDE LIVE TAKT MONITOR (ZERO SCREEN REFRESH)
# ---------------------------------------------------------
if "current_running_seq_l1" not in st.session_state:
    st.session_state["current_running_seq_l1"] = 1
if "current_running_seq_l2" not in st.session_state:
    st.session_state["current_running_seq_l2"] = 1

l1_curr = st.session_state.get("current_running_seq_l1", 1)
l2_curr = st.session_state.get("current_running_seq_l2", 1)

def get_seq_target(line_name: str, seq_no: int) -> Tuple[str, int]:
    sub = batches_df[
        (batches_df["Line"].str.lower() == line_name.lower()) & 
        (batches_df["ScaleX_Sequence_No"] == seq_no) & 
        (batches_df["Status"] == "PLAN")
    ]
    if not sub.empty:
        return str(sub.iloc[0]["SKUName"]), int(sub.iloc[0]["Count"])
    return "No Active Plan Batch", 30

l1_sku, l1_target = get_seq_target("Line_1", l1_curr)
l2_sku, l2_target = get_seq_target("Line_2", l2_curr)

col_adv1, col_adv2 = st.columns([3, 1])
with col_adv2:
    if st.button("⏩ Advance Sequence Floor", type="secondary"):
        st.session_state["current_running_seq_l1"] += 1
        st.session_state["current_running_seq_l2"] += 1
        st.rerun()

# Check if browser triggered an automatic batch advance
query_params = st.query_params
if "advance_line" in query_params:
    adv_line = query_params["advance_line"]
    if adv_line == "1":
        st.session_state["current_running_seq_l1"] = st.session_state.get("current_running_seq_l1", 1) + 1
    elif adv_line == "2":
        st.session_state["current_running_seq_l2"] = st.session_state.get("current_running_seq_l2", 1) + 1
    elif adv_line == "both":
        st.session_state["current_running_seq_l1"] = st.session_state.get("current_running_seq_l1", 1) + 1
        st.session_state["current_running_seq_l2"] = st.session_state.get("current_running_seq_l2", 1) + 1
    del st.query_params["advance_line"]
    st.rerun()


takt_banner_html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 0; background: transparent; }}
        .takt-container {{
            display: flex; gap: 15px; justify-content: space-between; align-items: center;
            background: #ffffff; border: 2px solid #00c853; border-radius: 10px;
            padding: 12px 20px; box-shadow: 0 4px 12px rgba(0,200,83,0.15);
        }}
        .takt-card {{ flex: 1; border-left: 4px solid #00c853; padding-left: 12px; }}
        .takt-title {{ font-size: 11px; font-weight: 800; color: #166534; text-transform: uppercase; letter-spacing: 0.5px; }}
        .takt-val {{ font-size: 16px; font-weight: 900; color: #0f172a; margin: 2px 0; display: flex; align-items: center; gap: 8px; }}
        .progress-bar-bg {{ width: 100%; height: 7px; background: #e2e8f0; border-radius: 4px; overflow: hidden; margin-top: 5px; }}
        .progress-bar-fill {{ height: 100%; width: 0%; background: #00c853; transition: width 1s linear; }}
        .timer-badge {{ font-size: 11px; font-weight: 800; color: #0369a1; background: #e0f2fe; padding: 2px 8px; border-radius: 10px; display: inline-block; margin-top: 4px; }}
        .complete-badge {{ display: none; font-size: 11px; font-weight: 900; background: #dcfce7; color: #15803d; padding: 2px 6px; border-radius: 6px; }}
        .pulse {{ animation: alertPulse 1.5s infinite; }}
        @keyframes alertPulse {{
            0% {{ box-shadow: 0 0 0 0 rgba(0, 200, 83, 0.4); }}
            70% {{ box-shadow: 0 0 0 10px rgba(0, 200, 83, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(0, 200, 83, 0); }}
        }}
    </style>
</head>
<body>
    <div class="takt-container" id="taktContainer">
        <!-- LINE 1 MONITOR -->
        <div class="takt-card">
            <div class="takt-title">🏍️ Line 1 Conveyor Takt</div>
            <div class="takt-val">
                <span id="l1_base_text">Seq #{l1_curr} ({l1_sku})</span>
                <span id="l1_badge" class="complete-badge">🏁 BATCH FINISHED</span>
            </div>
            <div class="progress-bar-bg"><div class="progress-bar-fill" id="l1_prog"></div></div>
            <div class="timer-badge" id="l1_timer">⏱️ Next Batch in: calculating...</div>
        </div>

        <!-- LINE 2 MONITOR -->
        <div class="takt-card" style="border-left-color: #0284c7;">
            <div class="takt-title" style="color: #0369a1;">🛵 Line 2 Conveyor Takt</div>
            <div class="takt-val">
                <span id="l2_base_text">Seq #{l2_curr} ({l2_sku})</span>
                <span id="l2_badge" class="complete-badge" style="background:#e0f2fe; color:#0369a1;">🏁 BATCH FINISHED</span>
            </div>
            <div class="progress-bar-bg"><div class="progress-bar-fill" id="l2_prog" style="background: #0284c7;"></div></div>
            <div class="timer-badge" id="l2_timer" style="color:#0369a1; background:#e0f2fe;">⏱️ Next Batch in: calculating...</div>
        </div>

        <!-- CADENCE STATUS -->
        <div style="flex: 0.4; text-align: right; border-left: 1px solid #cbd5e1; padding-left: 15px;">
            <div style="font-size: 11px; font-weight: 800; color: #64748b; text-transform: uppercase;">Factory Cadence</div>
            <div style="font-size: 14px; font-weight: 900; color: #00c853;">● 1-Min Takt</div>
            <div style="font-size: 11px; font-weight: 700; color: #475569;">2 Scooters / min</div>
        </div>
    </div>

    <script>
        const chimeAudio = new Audio('https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3');

        let l1_target_sec = Math.max(60, parseInt({l1_target}) * 60);
        let l2_target_sec = Math.max(60, parseInt({l2_target}) * 60);

        let active_l1_seq = "{l1_curr}";
        let active_l2_seq = "{l2_curr}";

        if (sessionStorage.getItem('persisted_l1_seq') !== active_l1_seq) {{
            sessionStorage.setItem('l1_elapsed_sec', 0);
            sessionStorage.setItem('persisted_l1_seq', active_l1_seq);
        }}
        if (sessionStorage.getItem('persisted_l2_seq') !== active_l2_seq) {{
            sessionStorage.setItem('l2_elapsed_sec', 0);
            sessionStorage.setItem('persisted_l2_seq', active_l2_seq);
        }}

        let l1_elapsed = parseInt(sessionStorage.getItem('l1_elapsed_sec')) || 0;
        let l2_elapsed = parseInt(sessionStorage.getItem('l2_elapsed_sec')) || 0;

        function updateTaktClock() {{
            l1_elapsed++;
            l2_elapsed++;

            // LINE 1 LOGIC
            if (l1_elapsed >= l1_target_sec) {{
                chimeAudio.play().catch(e => console.log('Audio blocked'));
                document.getElementById('l1_badge').style.display = 'inline-block';
                l1_elapsed = 0; // Seamless reset for the next batch cycle
            }} else {{
                document.getElementById('l1_badge').style.display = 'none';
            }}

            // LINE 2 LOGIC
            if (l2_elapsed >= l2_target_sec) {{
                chimeAudio.play().catch(e => console.log('Audio blocked'));
                document.getElementById('l2_badge').style.display = 'inline-block';
                l2_elapsed = 0; // Seamless reset for the next batch cycle
            }} else {{
                document.getElementById('l2_badge').style.display = 'none';
            }}

            sessionStorage.setItem('l1_elapsed_sec', l1_elapsed);
            sessionStorage.setItem('l2_elapsed_sec', l2_elapsed);

            // Render Line 1 Progress
            let l1_pct = Math.min(100, (l1_elapsed / l1_target_sec) * 100);
            let l1_left = Math.max(0, l1_target_sec - l1_elapsed);
            document.getElementById('l1_prog').style.width = l1_pct + '%';
            document.getElementById('l1_timer').innerText = '⏱️ Next Batch in: ' + Math.floor(l1_left / 60) + 'm ' + (l1_left % 60) + 's';

            // Render Line 2 Progress
            let l2_pct = Math.min(100, (l2_elapsed / l2_target_sec) * 100);
            let l2_left = Math.max(0, l2_target_sec - l2_elapsed);
            document.getElementById('l2_prog').style.width = l2_pct + '%';
            document.getElementById('l2_timer').innerText = '⏱️ Next Batch in: ' + Math.floor(l2_left / 60) + 'm ' + (l2_left % 60) + 's';
        }}

        setInterval(updateTaktClock, 1000);
    </script>
</body>
</html>
"""

st.components.v1.html(takt_banner_html, height=125)

available_seqs = sorted(batches_df[batches_df["ScaleX_Sequence_No"] > 0]["ScaleX_Sequence_No"].unique().tolist())
status_alerts = check_hold_to_plan_transitions(batches_df)
for alert_msg in status_alerts:
    st.warning(alert_msg)

lx02_raw = st.session_state.get("lx02_df", pd.DataFrame())
part_locations, part_descriptions, part_types, clean_lx02_df = process_lx02_master(lx02_raw)

if part_locations:
    st.sidebar.success(f"📍 LX02 Active: **{len(part_locations)} parts mapped to Storage Bins**")
else:
    st.sidebar.warning("⚠️ No LX02 loaded. Bins will show 'Awaiting Staging'.")

# ---------------------------------------------------------
# RESTORED FULL FILTERS (STATUS + SEQUENCE PRIORITIZATION)
# ---------------------------------------------------------
st.subheader("🎛️ Viewing, Line & Priority Filters")
f_col1, f_col2, f_col3, f_col4 = st.columns(4)

line_filter = f_col1.selectbox("Select Line:", ["All", "Line_1", "Line_2"])
plan_only_df = batches_df[batches_df["Status"].isin(["PLAN", "RUNNING"])].copy()

status_filter = f_col2.selectbox(
    "Filter Transfer Status:",
    ["All Statuses", "Ready for Full Transfer", "Partial Transfer Available", "W004 Out of Stock", "Sufficient M002 Stock"]
)

pss_filter = f_col3.selectbox("Filter PSS Type:", ["All", "Pallet", "Trolley", "FLC"])
search_text = f_col4.text_input("Search Part ID / Name:", "")

# --- HORIZON / SEQUENCE INPUT BLOCK ---
sf_col1, sf_col2, sf_col3 = st.columns([1, 1.2, 1.8])
hide_hold = sf_col1.checkbox("Hide HOLD Batches", value=True)

selection_mode = sf_col2.selectbox(
    "Filter Scope By:",
    ["⏱️ Target Qty / Minutes (e.g. 180m)", "🔢 Manual Sequence Numbers", "⚡ All Sequences"]
)

target_dispatch_qty = None
manual_selected_seqs = []

if selection_mode.startswith("⏱️"):
    target_dispatch_qty = sf_col3.number_input(
        "Enter Target Production Order Qty / Minutes:",
        min_value=30, max_value=600, value=180, step=30,
        help="e.g. 180 = 180 minutes of production (~180 scooters/line)."
    )
elif selection_mode.startswith("🔢"):
    manual_selected_seqs = sf_col3.multiselect(
        "Select Specific Sequences:",
        options=available_seqs,
        default=available_seqs[:3] if len(available_seqs) >= 3 else available_seqs
    )

# Compute active Line#Seq keys and the cutoff
active_seq_keys, cutoff_seq_keys = resolve_dispatch_sequences(
    batches_df=batches_df,
    target_units=int(target_dispatch_qty) if target_dispatch_qty else None,
    manual_seq_list=manual_selected_seqs if manual_selected_seqs else None,
    l1_start=st.session_state.get("current_running_seq_l1", 1),
    l2_start=st.session_state.get("current_running_seq_l2", 1)
)


# ---------------------------------------------------------
# COMPUTE CORE REPLENISHMENT MATH (GRACEFUL NON-BLOCKING)
# ---------------------------------------------------------
# ---------------------------------------------------------
# COMPUTE CORE REPLENISHMENT MATH (CACHED - INSTANT ON SCAN)
# ---------------------------------------------------------
# Unique signature: only recalculates if filters or files actually change!
mb52_in = st.session_state.get("mb52_df", pd.DataFrame())
cogi_in = st.session_state.get("cogi_df", pd.DataFrame())
sto_in = st.session_state.get("sto_df", pd.DataFrame())

filter_signature = f"{line_filter}_{status_filter}_{pss_filter}_{sorted(list(active_seq_keys))}_{calc_mode}_{len(batches_df)}_{len(mb52_in)}"

need_recompute = (
    "cached_master_df" not in st.session_state or
    st.session_state.get("last_calc_signature") != filter_signature or
    st.session_state.pop("force_recalculate", False)
)

if need_recompute:
    master_df = calculate_band2_and_pss_engine(
        batches_df, mb52_in, cogi_in, sto_in, line_filter, hide_hold, active_seq_keys, calc_mode
    )
    st.session_state["cached_master_df"] = master_df
    st.session_state["last_calc_signature"] = filter_signature
else:
    master_df = st.session_state["cached_master_df"]

# LIVE DATA HEALTH DIAGNOSTIC (VERIFIES INGESTION)
with st.expander("🔍 System Ingestion Diagnostics (Check Active Files)", expanded=master_df.empty):
    d_col1, d_col2, d_col3, d_col4 = st.columns(4)
    d_col1.write(f"**ScaleX Batches:** {len(batches_df)} rows")
    d_col2.write(f"**MB52 Stock:** {len(mb52_in)} rows" if not mb52_in.empty else "⚠️ **MB52:** Not Loaded")
    d_col3.write(f"**LX02 Bins:** {len(clean_lx02_df)} bins" if not clean_lx02_df.empty else "⚠️ **LX02:** Not Loaded")
    d_col4.write(f"**Open STO:** {len(sto_in)} items" if not sto_in.empty else "ℹ️ **STO:** 0 in transit")
    
    if master_df.empty:
        st.warning("⚠️ **No material demand generated.** This happens if: (1) Selected sequences contain only HOLD batches, (2) Line filter excluded all batches, or (3) `bom.csv` is missing.")
        st.info("💡 **Quick Fix:** In the top filter bar, set 'Sequence Prioritization Preset' to **'Full Shift (All Sequences)'** and uncheck 'Hide HOLD Batches'.")
# If master_df is empty, create safe placeholder so page NEVER crashes
if master_df.empty:
    master_df = pd.DataFrame(columns=[
        "Sequence_Order", "Part ID", "Part Name", "Location", "BOM Demand", "Demand to Fulfil",
        "Need to be Picked", "PSS Qty", "To be picked PSS count", "W004 Stock", "M002 Stock",
        "COGI Qty", "Open STO In-Transit", "Total M002 Pipeline", "PSS Type", "Starvation_Mins",
        "Priority", "Sequences_Covered", "Transfer Qty (W004 ➡️ M002)", "Transfer Status", "Is_Zero_Stock"
    ])

# Dual-Key Location & Real Description Resolver
def resolve_bin_location(part_id: Any) -> str:
    pid_raw = str(part_id).strip().upper()
    pid_norm = clean_sap_key(pid_raw)
    if pid_raw in part_locations: return part_locations[pid_raw]
    if pid_norm in part_locations: return part_locations[pid_norm]
    for k, v in part_locations.items():
        if k.endswith(pid_norm) or pid_norm.endswith(k):
            return v
    return "Awaiting Staging"

def resolve_real_description(part_id: Any, current_desc: str) -> str:
    pid_raw = str(part_id).strip().upper()
    pid_norm = clean_sap_key(pid_raw)
    if pid_raw in part_descriptions: return part_descriptions[pid_raw]
    if pid_norm in part_descriptions: return part_descriptions[pid_norm]
    if "ML_" in str(current_desc) or str(current_desc).strip() in ["", "nan"]:
        return "Standard Part"
    return str(current_desc)

if not master_df.empty and "Part ID" in master_df.columns:
    master_df["Location"] = master_df["Part ID"].map(resolve_bin_location)
    master_df["Part Name"] = master_df.apply(lambda r: resolve_real_description(r["Part ID"], r["Part Name"]), axis=1)
    master_df["Storage_Type"] = master_df["Part ID"].map(lambda pid: part_types.get(str(pid).strip(), part_types.get(clean_sap_key(pid), "-"))
)
# Apply Filters Safely
view_df = master_df.copy()
if not view_df.empty:
    if status_filter != "All Statuses":
        view_df = view_df[view_df["Transfer Status"] == status_filter]
    if pss_filter != "All":
        view_df = view_df[view_df["PSS Type"] == pss_filter]
    if search_text:
        view_df = view_df[
            view_df["Part ID"].astype(str).str.contains(search_text, case=False, na=False) |
            view_df["Part Name"].astype(str).str.contains(search_text, case=False, na=False)
        ]

# ---------------------------------------------------------
# HIGH-IMPACT OPERATIONAL COMMAND BANNER
# ---------------------------------------------------------
ready_df = master_df[master_df["Transfer Status"] == "Ready for Full Transfer"]
stockout_df = master_df[master_df["Transfer Status"] == "W004 Out of Stock"]
covered_df = master_df[master_df["Transfer Status"].str.contains("Sufficient|Covered by Open STO")]

total_skus_to_pick = len(ready_df)
total_sus_to_pick = int(ready_df["To be picked PSS count"].sum())
pallets = (ready_df["PSS Type"] == "Pallet").sum()
flcs = (ready_df["PSS Type"] == "FLC").sum()
trolleys = (ready_df["PSS Type"] == "Trolley").sum()
est_trucks = math.ceil(total_sus_to_pick / 40) if total_sus_to_pick > 0 else 0
critical_starve_count = (stockout_df["Starvation_Mins"] <= 60).sum()

col_work, col_risk, col_health = st.columns([1.4, 1.3, 1.0])
with col_work:
    st.markdown(f"""
    <div style="background: #f0fdf4; border-left: 5px solid #00c853; border-radius: 8px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <div style="font-size: 11px; font-weight: 800; color: #166534; text-transform: uppercase;">📦 Active Dispatch Queue</div>
        <div style="display: flex; align-items: baseline; gap: 10px; margin-top: 4px;">
            <span style="font-size: 28px; font-weight: 900; color: #0f172a;">{total_skus_to_pick} SKUs</span>
            <span style="font-size: 14px; font-weight: 700; color: #15803d;">({total_sus_to_pick} SUs)</span>
        </div>
        <div style="font-size: 12px; color: #334155; margin-top: 6px;">
            <b>Mix:</b> {pallets} Pallets | {flcs} FLCs | {trolleys} Trolleys &nbsp;|&nbsp; <b>Fleet:</b> ~<b>{est_trucks} Trucks</b>
        </div>
    </div>
    """, unsafe_allow_html=True)

with col_risk:
    st.markdown(f"""
    <div style="background: #fef2f2; border-left: 5px solid #ef4444; border-radius: 8px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <div style="font-size: 11px; font-weight: 800; color: #991b1b; text-transform: uppercase;">🚨 Line Starvation Risk</div>
        <div style="display: flex; align-items: baseline; gap: 10px; margin-top: 4px;">
            <span style="font-size: 28px; font-weight: 900; color: #991b1b;">{len(stockout_df)} SKUs</span>
            <span style="font-size: 12px; font-weight: 800; background: #fee2e2; color: #b91c1c; padding: 2px 8px; border-radius: 12px;">{critical_starve_count} Critical (<60m)</span>
        </div>
        <div style="font-size: 12px; color: #7f1d1d; margin-top: 6px;">
            Immediate escalation required. Assembly lines starve on next sequence.
        </div>
    </div>
    """, unsafe_allow_html=True)

with col_health:
    coverage_pct = round((len(covered_df) / (len(master_df) or 1)) * 100, 1)
    st.markdown(f"""
    <div style="background: #f8fafc; border-left: 5px solid #94a3b8; border-radius: 8px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
        <div style="font-size: 11px; font-weight: 800; color: #475569; text-transform: uppercase;">🛡️ M002 Line Health</div>
        <div style="display: flex; align-items: baseline; gap: 10px; margin-top: 4px;">
            <span style="font-size: 28px; font-weight: 900; color: #334155;">{len(covered_df)} SKUs</span>
            <span style="font-size: 13px; font-weight: 700; color: #64748b;">({coverage_pct}% Covered)</span>
        </div>
        <div style="font-size: 12px; color: #64748b; margin-top: 6px;">
            Sufficient buffer or Open STO in-transit.<br><b>Zero handling</b> needed.
        </div>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# ---------------------------------------------------------
# HIGH-PERFORMANCE OPERATOR VIEW (ZERO IFRAME LAG)
# ---------------------------------------------------------

# ---------------------------------------------------------
# EQUIPMENT-SPECIALIZED PICKLIST SPLITTER (BOPT vs TROLLEY)
# ---------------------------------------------------------
def split_picklist_by_equipment(df_pick: pd.DataFrame, num_pickers: int) -> List[Tuple[str, str, pd.DataFrame]]:
    """
    Segregates picklists by Material Handling Equipment (MHE):
    - Pallets & FLCs = Handled together via BOPT / Forklift.
    - Trolleys = Handled via Tow Tugger / Manual Roll-on.
    """
    if df_pick.empty:
        return []

    # Segregate into Forklift/BOPT vs Roll-on Trolleys
    df_fork_bopt = df_pick[df_pick["PSS Type"].isin(["Pallet", "FLC"])].copy()
    df_trolleys = df_pick[df_pick["PSS Type"] == "Trolley"].copy()

    # Catch unrecognized packaging into the BOPT bucket
    df_others = df_pick[~df_pick["PSS Type"].isin(["Pallet", "FLC", "Trolley"])].copy()
    if not df_others.empty:
        df_fork_bopt = pd.concat([df_fork_bopt, df_others], ignore_index=True)

    results = []

    # --- CASE 1: EXACTLY 2 PICKERS ---
    if num_pickers == 2:
        total_items = len(df_pick)
        trolley_count = len(df_trolleys)

        if trolley_count > 0 and (trolley_count / total_items) < 0.20 and len(df_fork_bopt) > 10:
            df_pallets = df_fork_bopt[df_fork_bopt["PSS Type"] == "Pallet"].copy()
            df_flcs = df_fork_bopt[df_fork_bopt["PSS Type"] == "FLC"].copy()

            p1 = pd.concat([df_pallets, df_flcs.iloc[:len(df_flcs)//2]], ignore_index=True)
            p2 = pd.concat([df_trolleys, df_flcs.iloc[len(df_flcs)//2:]], ignore_index=True)

            p1 = p1.sort_values(by=["Starvation Mins", "Storage Bin"], ascending=[True, True]).reset_index(drop=True)
            p2 = p2.sort_values(by=["Starvation Mins", "Storage Bin"], ascending=[True, True]).reset_index(drop=True)

            results.append((
                "🧑‍🏭 Picker 1 (Heavy BOPT - Pallet/FLC)",
                "🚜 Forklift / High BOPT | Pallets & Upper Bay FLCs",
                p1
            ))
            results.append((
                "🧑‍🏭 Picker 2 (Trolleys & Ground FLC)",
                "🛒 Roll-on Tow Tugger + Ground Bay FLCs",
                p2
            ))
        else:
            p1 = df_fork_bopt.sort_values(by=["Starvation Mins", "Storage Bin"], ascending=[True, True]).reset_index(drop=True)
            p2 = df_trolleys.sort_values(by=["Starvation Mins", "Storage Bin"], ascending=[True, True]).reset_index(drop=True)

            results.append((
                "🧑‍🏭 Picker 1 (BOPT - Pallet & FLC)",
                "🚜 Forklift / BOPT Only | Rigid Heavy Unit Loads",
                p1
            ))
            results.append((
                "🧑‍🏭 Picker 2 (Floor Trolleys)",
                "🛒 Roll-on Tow Tractor / Wheel-Dolly Handling",
                p2
            ))

    # --- CASE 2: 3 PICKERS ---
    elif num_pickers == 3:
        df_pallets = df_fork_bopt[df_fork_bopt["PSS Type"] == "Pallet"].sort_values(by=["Starvation Mins", "Storage Bin"]).reset_index(drop=True)
        df_flcs = df_fork_bopt[df_fork_bopt["PSS Type"] == "FLC"].sort_values(by=["Starvation Mins", "Storage Bin"]).reset_index(drop=True)
        p3 = df_trolleys.sort_values(by=["Starvation Mins", "Storage Bin"]).reset_index(drop=True)

        results.append(("🧑‍🏭 Picker 1 (Forklift - Pallets)", "🚜 High-Rack Reach Truck / Pallets", df_pallets))
        results.append(("🧑‍🏭 Picker 2 (BOPT - FLCs)", "📦 Powered Pallet Truck (BOPT) / FLCs", df_flcs))
        results.append(("🧑‍🏭 Picker 3 (Trolley Tugger)", "🛒 Roll-on Trolley Hitch / Tugger", p3))

    # --- CASE 3: 4+ PICKERS ---
    elif num_pickers >= 4:
        results.append((
            "🧑‍🏭 Picker 1 (Trolleys)",
            "🛒 Roll-on Trolley Hitch",
            df_trolleys.sort_values(by=["Starvation Mins", "Storage Bin"]).reset_index(drop=True)
        ))
        
        bopt_pickers = num_pickers - 1
        df_bopt_sorted = df_fork_bopt.sort_values(by=["Starvation Mins", "Storage Bin"]).reset_index(drop=True)
        k, m = divmod(len(df_bopt_sorted), bopt_pickers)
        
        for i in range(bopt_pickers):
            start = i * k + min(i, m)
            end = (i + 1) * k + min(i + 1, m)
            chunk = pd.DataFrame(df_bopt_sorted.iloc[start:end]).copy().reset_index(drop=True)
            results.append((f"🧑‍🏭 Picker {i+2} (BOPT - Pallet/FLC)", "🚜 Forklift / BOPT Heavy Route", chunk))

    # --- CASE 4: 1 CONSOLIDATED PICKER ---
    else:
        results.append((
            "🧑‍🏭 Master Picker (Consolidated)",
            "🚜 BOPT (Pallet/FLC) first ➡️ Then Roll-on Trolleys",
            df_pick.sort_values(by=["PSS Type", "Storage Bin"]).reset_index(drop=True)
        ))

    return results

def render_landscape_printable_view(df_subset: pd.DataFrame, sheet_title: str):
    df_print = df_subset.copy().reset_index(drop=True)
    df_print.insert(0, "S No", df_print.index + 1)
    
    # 1. Native Fast Virtualized Display (0ms render time, no iframe memory leak)
    st.dataframe(
        df_print,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Need to be Picked": st.column_config.NumberColumn("Need Pick", format="%d"),
            "Demand to Fulfil": st.column_config.NumberColumn("Demand", format="%d"),
            "W004 Stock": st.column_config.NumberColumn("W004 Stock", format="%d"),
            "Priority": st.column_config.TextColumn("Priority")
        }
    )

    c_btn1, c_btn2 = st.columns([1, 4])
    
    # 2. Download CSV
    csv_data = df_print.to_csv(index=False).encode("utf-8")
    c_btn1.download_button("⬇️ Export CSV", data=csv_data, file_name=f"{sheet_title.replace(' ', '_')}.csv", mime="text/csv", key=f"csv_{sheet_title}")

    # 3. Clean Print Dialog (Generates print window on-demand without constantly running in background)
    table_id = f"table_{re.sub(r'[^a-zA-Z0-9_]', '', sheet_title)}"
    
    html_rows = ""
    for _, r in df_print.iterrows():
        prio_color = "#d32f2f" if "CRITICAL" in str(r.get("Priority", "")) else ("#f57c00" if "HIGH" in str(r.get("Priority", "")) else "#388e3c")
        bin_val = r.get("Storage Bin", r.get("Location", "-"))
        type_val = r.get("Storage Type", r.get("Storage_Type", "-"))
        need_pick = r.get("Need to be Picked", 0)
        pss_type_val = r.get("PSS Type", "-")

        html_rows += f"""
        <tr>
            <td style="text-align: center;">{r['S No']}</td>
            <td style="font-weight: bold; font-family: monospace;">{r.get('Part ID', '')}</td>
            <td style="font-size: 10px;">{r.get('Part Name', '')[:30]}</td>
            <td style="text-align: right; font-weight: bold; background: #dcfce7;">{need_pick}</td>
            <td style="text-align: right;">{r.get('PSS Qty', '')}</td>
            <td style="text-align: center; font-weight: bold;">{r.get('To be picked PSS count', '')}</td>
            <td style="text-align: right;">{r.get('W004 Stock', 0)}</td>
            <td style="text-align: center; font-weight: bold; background: #fef08a;">{pss_type_val}</td>
            <td style="text-align: center; font-weight: bold; background: #e0f2fe;">{bin_val}</td>
            <td style="text-align: center;">{type_val}</td>
            <td style="color: {prio_color}; font-weight: bold;">{r.get('Priority', '')}</td>
            <td style="text-align: center;">{r.get('Starvation Mins', 0)}m</td>
            <td style="font-size: 9px;">{r.get('Sequences Covered', '')}</td>
        </tr>
        """

    print_html_launcher = f"""
    <!DOCTYPE html>
    <html>
    <body>
        <div id="{table_id}" style="display:none;">
            <table border="1" style="width:100%; border-collapse:collapse; font-size:10px; font-family:Arial;">
                <thead>
                    <tr style="background:#0f172a; color:#fff;">
                        <th>#</th><th>Part ID</th><th>Part Name</th><th>Need Pick</th><th>PSS Qty</th><th>PSS Cnt</th>
                        <th>W004 Stock</th><th>PSS Type</th><th>Storage Bin</th><th>Type</th><th>Priority</th><th>Starve</th><th>Sequences</th>
                    </tr>
                </thead>
                <tbody>{html_rows}</tbody>
            </table>
        </div>
        <button onclick="
            var printWin = window.open('', '', 'width=1100,height=800');
            printWin.document.write('<html><head><title>{sheet_title}</title>');
            printWin.document.write('<style>@page {{ size: landscape; margin: 8mm; }} table {{ width:100%; border-collapse:collapse; font-size:10px; font-family:Arial; }} th, td {{ border:1px solid #94a3b8; padding:4px; }} th {{ background:#0f172a; color:#fff; }}</style>');
            printWin.document.write('</head><body>');
            printWin.document.write('<div style=\\'display:flex; justify-content:space-between; border-bottom:2px solid #00c853; padding-bottom:4px; margin-bottom:8px;\\'>');
            printWin.document.write('<div><b style=\\'font-size:16px; color:#00c853;\\'>⚡ ATHER</b> <b>{sheet_title}</b></div>');
            printWin.document.write('<div>Total SKUs: {len(df_print)} | Date: ' + new Date().toLocaleDateString() + '</div></div>');
            printWin.document.write(document.getElementById('{table_id}').innerHTML);
            printWin.document.write('</body></html>');
            printWin.document.close();
            printWin.focus();
            setTimeout(function() {{ printWin.print(); printWin.close(); }}, 300);
        " style="
            background-color: #00c853; color: white; padding: 7px 18px; border: none; 
            border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 800;
        ">🖨️ Print Landscape Route Sheet</button>
    </body>
    </html>
    """
    
    # Tiny 45px launcher button (eliminates 100% of iframe overhead)
    st.components.v1.html(print_html_launcher, height=45)
# ---------------------------------------------------------
# 4 CORE TABS
# ---------------------------------------------------------
tab_sequence, tab_pickers, tab_deo_dock, tab_band2 = st.tabs([
    "📈 1. ScaleX Sequence Plan",
    "🧑‍🏭 2. Operator Location Picklist",
    "🚚 3. DEO Loading & SAP Desk",
    "📊 4. Band 2 Transfer Requirements"
])

# ---------------------------------------------------------
# TAB 1: SCALEX PLAN
# ---------------------------------------------------------
# ---------------------------------------------------------
# TAB 1: SCALEX PLAN (UNALTERED SCREEN + NEW PICKLIST COLUMN)
# ---------------------------------------------------------
with tab_sequence:
    st.subheader("📈 ScaleX Plan Sequence (Exact MES Mirror)")
    st.caption("Direct 1:1 reflection of the ScaleX Production Sequence Report from 192.168.201.248. Untouched conveyor order.")

    scalex_raw = batches_df.copy()

    # Determine "In Current Picklist" status for each row
    def check_in_picklist(row):
        st_val = str(row.get("Status", "")).upper()
        if st_val == "HOLD":
            return "NO (HOLD)"
        
        key = f"{row.get('Line', '')}#{int(row.get('ScaleX_Sequence_No', 0))}"
        if key in cutoff_seq_keys:
            return "🏁 YES (Cutoff)"
        elif key in active_seq_keys:
            return "✅ YES"
        return "NO"

    scalex_raw["In Current Picklist"] = scalex_raw.apply(check_in_picklist, axis=1)

    # Added 'In Current Picklist' right next to Seq #
    col_order = ["ScaleX_Sequence_No", "In Current Picklist", "SKUName", "Status", "Line", "Serial_Start", "Serial_End", "Count"]
    display_scalex = scalex_raw[[c for c in col_order if c in scalex_raw.columns]].copy()
    display_scalex.rename(columns={
        "ScaleX_Sequence_No": "Seq #",
        "SKUName": "SKU / Model Name",
        "Status": "Status",
        "Line": "Line",
        "Serial_Start": "Serial No Start",
        "Serial_End": "Serial No End",
        "Count": "Plan Qty"
    }, inplace=True)

    def highlight_scalex(row):
        st_val = str(row.get("Status", "")).upper()
        p_val = str(row.get("In Current Picklist", ""))
        
        if "HOLD" in st_val:
            return ["background-color: #fee2e2; color: #991b1b; font-weight: bold;"] * len(row)
        elif "Cutoff" in p_val:
            # Clear highlighted boundary row for the last sequence of the entered quantity
            return ["background-color: #fef08a; color: #854d0e; font-weight: bold;"] * len(row)
        elif "YES" in p_val:
            return ["background-color: #f0fdf4; color: #166534; font-weight: bold;"] * len(row)
        return [""] * len(row)

    sx_tab_l1, sx_tab_l2, sx_tab_all = st.tabs([
        "🏍️ Line 1 Sequence (ScaleX)",
        "🛵 Line 2 Sequence (ScaleX)",
        "⚡ Combined Both Lines"
    ])

    with sx_tab_l1:
        df_l1 = display_scalex[display_scalex["Line"].str.lower().str.contains("1")].reset_index(drop=True)
        if not df_l1.empty:
            l1_plans = (df_l1["Status"] == "PLAN").sum()
            l1_holds = (df_l1["Status"] == "HOLD").sum()
            st.info(f"📍 **Line 1 Schedule:** {len(df_l1)} Total Batches | **{l1_plans} PLAN** | **{l1_holds} HOLD** | Total Scooters: **{int(df_l1['Plan Qty'].sum())}**")
            st.dataframe(df_l1.style.apply(highlight_scalex, axis=1), use_container_width=True, hide_index=True)
        else:
            st.warning("No batches found for Line 1.")

    with sx_tab_l2:
        df_l2 = display_scalex[display_scalex["Line"].str.lower().str.contains("2")].reset_index(drop=True)
        if not df_l2.empty:
            l2_plans = (df_l2["Status"] == "PLAN").sum()
            l2_holds = (df_l2["Status"] == "HOLD").sum()
            st.info(f"📍 **Line 2 Schedule:** {len(df_l2)} Total Batches | **{l2_plans} PLAN** | **{l2_holds} HOLD** | Total Scooters: **{int(df_l2['Plan Qty'].sum())}**")
            st.dataframe(df_l2.style.apply(highlight_scalex, axis=1), use_container_width=True, hide_index=True)
        else:
            st.warning("No batches found for Line 2.")

    with sx_tab_all:
        st.dataframe(display_scalex.style.apply(highlight_scalex, axis=1), use_container_width=True, hide_index=True)
# ---------------------------------------------------------
# TAB 2: OPERATOR PICKLIST
# ---------------------------------------------------------
# ---------------------------------------------------------
# TAB 2: OPERATOR PICKLIST
# ---------------------------------------------------------
with tab_pickers:
    col_main_pick, col_side_band2 = st.columns([2.6, 1.0])

    with col_main_pick:
        col_t2_title, col_t2_split = st.columns([2.0, 1.2])
        with col_t2_title:
            st.subheader("🧑‍🏭 Operator Picklist (Equipment-Specialized)")
            st.caption("Pallets & FLCs are assigned to BOPT/Forklift. Trolleys are assigned to Roll-on Handlers.")
        with col_t2_split:
            num_pickers = st.number_input("Active Pickers:", min_value=1, max_value=6, value=2, step=1,
                                          help="2 Pickers = Picker 1 gets BOPT (Pallet/FLC), Picker 2 gets Trolleys.")

        pickable_df = view_df[view_df["Need to be Picked"] > 0].copy()

        if not pickable_df.empty:
            picklist_display = pd.DataFrame({
                "Part ID": pickable_df["Part ID"],
                "Part Name": pickable_df["Part Name"],
                "Need to be Picked": pickable_df["Need to be Picked"],
                "PSS Qty": pickable_df["PSS Qty"],
                "To be picked PSS count": pickable_df["To be picked PSS count"],
                "W004 Stock": pickable_df["W004 Stock"],
                "Storage Bin": pickable_df["Location"],
                "Storage Type": pickable_df.get("Storage_Type", pd.Series(["-"] * len(pickable_df))),
                "PSS Type": pickable_df["PSS Type"],
                "Priority": pickable_df["Priority"],
                "Starvation Mins": pickable_df["Starvation_Mins"],
                "Sequences Covered": pickable_df["Sequences_Covered"]
            }).sort_values(by=["Starvation Mins", "Storage Bin"], ascending=[True, True]).reset_index(drop=True)

            picker_workloads = split_picklist_by_equipment(picklist_display, num_pickers)

            tab_labels = ["📋 Consolidated Master"] + [w[0] for w in picker_workloads]
            op_tabs = st.tabs(tab_labels)

            with op_tabs[0]:
                render_landscape_printable_view(picklist_display, sheet_title="Consolidated Warehouse Master")

            for idx, (tab_name, subtitle, op_df) in enumerate(picker_workloads):
                with op_tabs[idx + 1]:
                    if not op_df.empty:
                        p1_count = (op_df["Priority"].astype(str).str.contains("CRITICAL")).sum()
                        container_total = int(pd.to_numeric(op_df["To be picked PSS count"], errors="coerce").sum())
                        st.markdown(f"**Equipment Directive:** `{subtitle}`")
                        st.info(f"📊 **Workload:** {len(op_df)} SKUs | **{container_total} Containers** | 🚨 **Critical P1s:** {p1_count}")
                        render_landscape_printable_view(op_df, sheet_title=f"{tab_name.split('(')[0].strip()} Route Sheet")
                    else:
                        st.info(f"No items assigned to {tab_name}.")
        else:
            st.success("✅ Zero parts to pick for the selected sequence. Everything is covered in M002!")

    with col_side_band2:
        st.markdown("### 📊 Band 2 Live Snapshot")
        side_b2 = view_df[["Part ID", "Total M002 Pipeline", "W004 Stock", "Need to be Picked", "Transfer Status"]].copy()
        side_b2.columns = ["SKU", "Pipeline", "W004", "Transfer", "Status"]

        def highlight_side(row):
            if "Out of Stock" in str(row["Status"]): return ["background-color: #fee2e2; color: #991b1b;"] * len(row)
            elif "Ready" in str(row["Status"]): return ["background-color: #dcfce7; color: #166534;"] * len(row)
            elif "Covered" in str(row["Status"]): return ["background-color: #e0f2fe; color: #0369a1;"] * len(row)
            return [""] * len(row)

        st.dataframe(side_b2.style.apply(highlight_side, axis=1), height=550, use_container_width=True, hide_index=True)
# ---------------------------------------------------------
# TAB 3: DEO LOADING & SAP DESK
# ---------------------------------------------------------
# ---------------------------------------------------------
# TAB 3: DEO LOADING & SAP DESK (ISOLATED INSTANT SCANNER)
# ---------------------------------------------------------
with tab_deo_dock:
    st.subheader("🚚 DEO Dock Loading & Instant SAP Desk")
    st.caption("Scan SU barcodes on pallets/boxes or Part ID barcodes on trolleys. Zero-delay execution.")

    # @st.fragment ensures ONLY this dock desk reruns when a barcode is scanned!
    # The rest of the page, scroll position, and tabs do NOT reload!
    @st.fragment
    def render_instant_dock_desk():
        def process_su_scan():
            raw_val = st.session_state.get("su_input_box", "").strip()
            raw_su = raw_val.lstrip("0")
            raw_mat = clean_sap_key(raw_val)
            
            if not raw_val:
                return

            if clean_lx02_df.empty:
                st.error("⚠️ LX02 file not loaded. Upload LX02 in the sidebar first.")
                return

            # Check for Duplicate Scans
            existing_records = get_scanned_records()
            if not existing_records.empty and (raw_su in existing_records["su_number"].values or raw_val in existing_records["su_number"].values):
                st.warning(f"⚠️ Already Scanned: '{raw_val}' is already recorded on this truck!")
                st.session_state["su_input_box"] = ""
                return

            # 1. Match by SU Number (Pallets / FLCs)
            su_match = clean_lx02_df[clean_lx02_df["SU_Number_Clean"] == raw_su]

            if not su_match.empty:
                item = su_match.iloc[0]
                mat_id = str(item["Material"]).strip()
                desc = str(item.get("Description", "")).strip()
                bin_loc = str(item.get("Storage_Bin", "")).strip()
                qty = float(item["SU_Qty"])

                save_scanned_su(raw_su, mat_id, desc, bin_loc, qty)
                st.toast(f"✅ Loaded SU #{raw_su} | {mat_id} (Qty: {qty})", icon="📦")
            else:
                # 2. Match by Part ID (Trolleys / Non-SU Parts)
                mat_match = clean_lx02_df[clean_lx02_df["Material"].apply(clean_sap_key) == raw_mat]
                if not mat_match.empty:
                    item = mat_match.iloc[0]
                    mat_id = str(item["Material"]).strip()
                    desc = str(item.get("Description", "")).strip()
                    bin_loc = str(item.get("Storage_Bin", "")).strip()
                    trolley_qty, pss_type = get_official_pss(mat_id, desc)
                    dummy_id = f"TRL-{mat_id}-{time.strftime('%H%M%S')}"

                    save_scanned_su(dummy_id, mat_id, desc, bin_loc, float(trolley_qty))
                    st.toast(f"🛒 Loaded Trolley | {mat_id} (Qty: {trolley_qty} {pss_type})", icon="🚚")
                else:
                    st.error(f"❌ SU Label not registered! (Scanned: '{raw_val}')")

            st.session_state["su_input_box"] = ""

        st.text_input(
            "⚡ Focus here with Barcode Scanner (Scanner auto-presses Enter):",
            key="su_input_box",
            on_change=process_su_scan,
            placeholder="Scan SU Barcode or Part ID..."
        )

        loading_records = get_scanned_records()
        col_dock_left, col_dock_right = st.columns([1.5, 1.2])

        with col_dock_left:
            st.markdown("#### 📋 Active Truck Loading Manifest")
            if not loading_records.empty:
                st.caption(f"**Loaded:** {len(loading_records)} SUs | **Total Quantity:** {int(loading_records['qty'].sum())} pcs")

                edited_loading_df = st.data_editor(
                    loading_records,
                    num_rows="dynamic",
                    use_container_width=True,
                    key="truck_manifest_editor",
                    column_config={
                        "su_number": st.column_config.TextColumn("SU Number", disabled=True),
                        "part_id": st.column_config.TextColumn("Part ID", disabled=True),
                        "storage_bin": st.column_config.TextColumn("Storage Bin", disabled=True),
                        "qty": st.column_config.NumberColumn("Qty", required=True, min_value=0),
                        "status": st.column_config.SelectboxColumn("Status", options=["LOADED", "SHORT", "DAMAGED"]),
                        "remarks": st.column_config.TextColumn("Remarks")
                    }
                )

                btn_c1, btn_c2 = st.columns(2)
                if btn_c1.button("💾 Save Edits"):
                    with sqlite3.connect(DB_PATH) as conn:
                        edited_loading_df.to_sql("truck_loading", conn, if_exists="replace", index=False)
                    st.success("Manifest saved!")

                if btn_c2.button("🏁 Clear Truck"):
                    clear_truck_ledger()
                    st.rerun()
            else:
                st.info("No SUs scanned yet. Scan a barcode above to begin.")

        with col_dock_right:
            st.markdown("#### 📋 1-Click SAP Direct Clipboard Handshake")
            st.caption("Select cell 1 in SAP GUI (ME21N / MIGO / MB1B) and press Ctrl + V.")

            if not loading_records.empty:
                sap_summary = loading_records.groupby(["part_id", "description"])["qty"].sum().reset_index()
                sap_summary.columns = ["Part No", "Description", "Quantity"]
                sap_grid = sap_summary[["Part No", "Quantity", "Description"]].copy()

                sap_tsv = "\n".join([f"{r['Part No']}\t{int(r['Quantity'])}\t{r['Description']}" for _, r in sap_grid.iterrows()])
                st.text_area("SAP Clipboard Output (Part No \\t Quantity \\t Description):", sap_tsv, height=140)
                st.dataframe(sap_grid, use_container_width=True, hide_index=True)
            else:
                st.info("SAP transfer table will build automatically as SUs are scanned.")

    # Call the isolated fragment
    render_instant_dock_desk()
# ---------------------------------------------------------
# TAB 4: BAND 2 REQUIREMENTS
# ---------------------------------------------------------
with tab_band2:
    st.subheader("Band 2: Component Transfer Requirements (W004 ➡️ M002)")
    transfer_table = view_df[[
        "Sequence_Order", "Part ID", "Part Name", "Location",
        "BOM Demand", "M002 Stock", "COGI Qty", "Open STO In-Transit", "Total M002 Pipeline",
        "W004 Stock", "Transfer Qty (W004 ➡️ M002)", "Transfer Status"
    ]].copy()
    transfer_table.rename(columns={
        "Sequence_Order": "Seq #",
        "Part ID": "SKU (Child Material)",
        "Part Name": "Description",
        "Location": "Storage Bin",
        "BOM Demand": "Gross BOM Demand",
        "M002 Stock": "M002 On-Hand",
        "Open STO In-Transit": "In-Transit STO",
        "Total M002 Pipeline": "Effective Pipeline",
        "W004 Stock": "W004 Stock"
    }, inplace=True)

    def highlight_status(row):
        if "Out of Stock" in str(row["Transfer Status"]): return ["background-color: #fee2e2; color: #991b1b; font-weight: bold;"] * len(row)
        elif "Ready" in str(row["Transfer Status"]): return ["background-color: #dcfce7; color: #166534; font-weight: bold;"] * len(row)
        elif "Covered" in str(row["Transfer Status"]): return ["background-color: #e0f2fe; color: #0369a1; font-weight: bold;"] * len(row)
        return [""] * len(row)

    st.dataframe(transfer_table.style.apply(highlight_status, axis=1), use_container_width=True, hide_index=True)
    csv_band2 = transfer_table.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download Band 2 CSV", data=csv_band2, file_name="Band2_Transfer_Requirements.csv", mime="text/csv")