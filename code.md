import base64
from io import BytesIO
import math
import os
import re
import socket
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from playwright.sync_api import Page, sync_playwright
import qrcode
import streamlit as st

# Page Configuration
st.set_page_config(
    page_title="Ather Material Replenishment & Kitting Suite",
    page_icon="⚡",
    layout="wide",
)

URL = "http://192.168.201.248/AtherReport/"
SCALEX_HOST = "192.168.201.248"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_BATCHES = os.path.join(BASE_DIR, "plan_batches.csv")
BACKGROUND_BOM = os.path.join(BASE_DIR, "bom.csv")
SNAPSHOT_FILE = os.path.join(BASE_DIR, "state_snapshot.json")

# ---------------------------------------------------------
# ATHER BRANDING: WHITE BACKGROUND & ATHER GREEN
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
        margin-bottom: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    div[data-testid="stMetricValue"] { font-size: 26px; font-weight: 900; color: #0f172a; }
    @media print {
        @page { size: 100mm 50mm; margin: 0; }
        body * { visibility: hidden; }
        .thermal-print-area, .thermal-print-area * { visibility: visible; }
        .thermal-print-area { position: absolute; left: 0; top: 0; width: 100mm; height: 50mm; }
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------
# 1. OFFICIAL PSS MASTER (Plant Packaging Standard)
# ---------------------------------------------------------
OFFICIAL_PSS_MASTER = {
    "BE0203B004MNNC": (180, "FLC"), "BE0203B005MNNC": (180, "FLC"),
    "MC00000177E": (160, "Pallet"), "BE0407A001MNNL": (160, "Pallet"),
    "BW00000683B": (336, "Pallet"), "BW00001484A": (336, "Pallet"),
    "BE0112B001MNNZ0B": (384, "Pallet"), "CS00005535A": (2304, "Pallet"),
    "340A0000975F": (2500, "Pallet"), "BE0202A001MNNF": (30, "FLC"),
    "BE0202A004MNNE": (30, "FLC"), "BS00000520A": (240, "Pallet"),
    "BW00000484A": (360, "Pallet"), "BE0311C001MNNZF": (45, "Pallet"),
    "EP00000137B": (6000, "Pallet"), "BW00001252A": (288, "Pallet"),
    "BW00000502BMA": (60, "Pallet"), "BW00000502BKA": (60, "Pallet"),
    "BW00000480BMC": (56, "Pallet"), "BW00000503BMA": (60, "Pallet"),
    "BW00000503BKA": (60, "Pallet"), "BW00000480BKC": (56, "Pallet"),
    "ZZ00000859A": (576, "Pallet"), "BF0101C005ANNB": (960, "Pallet"),
    "BW00000327A": (576, "Pallet"), "ZZ00000841A": (576, "Pallet"),
    "340A0000984C": (240, "Pallet"), "340A0000986G": (240, "Pallet"),
    "340A001051F": (4000, "Pallet"), "DB00001096A": (160, "Pallet"),
    "LL00000002C": (48, "Pallet"), "ZZ00000708E": (100, "FLC"),
    "PN00000625A": (280, "Pallet"), "CS00004377MKA": (40, "Pallet"),
    "CS00004377MSA": (40, "Pallet"), "CS00004377WTA": (40, "Pallet"),
    "EP00000897B": (640, "Pallet"), "EP00000898B": (2560, "Pallet"),
    "EP00001121A": (3200, "Pallet"), "EP00000590B": (864, "Pallet"),
    "EP00000892B": (640, "Pallet"), "CS00008488A": (448, "Pallet"),
    "CS00008492A": (448, "Pallet"), "EP00000118A": (2000, "Pallet"),
    "CS00003172A": (32, "Trolley"), "WT00000113MGA": (32, "Trolley"),
    "WT00000113MGB": (32, "Trolley"), "CS00000221A": (32, "Trolley"),
    "WT00000045A": (32, "Trolley"), "WT00000045MGB": (32, "Trolley"),
    "WT00000045MGC": (32, "Trolley"), "ES00001978B": (150, "Pallet"),
    "CS00009472B": (80, "Trolley"), "EP00000913B": (48, "Pallet"),
    "EP00000914B": (36, "Pallet"), "EP00000928B": (60, "Pallet"),
    "EP00000899A": (1728, "Pallet"), "EP00000682B": (576, "Pallet"),
    "BW00000485A": (360, "Pallet"), "BS00000037B": (30, "FLC"),
    "BW00000012WE0A": (110, "FLC"), "BW00000013WE0A": (110, "FLC"),
    "BD0103A001MSGZ0B": (44, "FLC"), "BD0103A002MSGZ0B": (44, "FLC"),
    "ZZ00000836A": (120, "Pallet"), "340A0001037F": (320, "Pallet"),
    "BD0105B002MSGZ0B": (78, "FLC"), "BD0105C001MWE0A": (78, "FLC"),
    "BE0111B001MNNZA": (300, "Trolley"), "ZZ00000829A": (192, "Trolley"),
    "BW00000366BMA": (120, "Trolley"), "BW00000366BKA": (120, "Trolley"),
    "BD0104D001MSGZ0D": (60, "FLC"), "ZZ00000856A": (96, "Pallet"),
    "BW00000017B": (60, "Pallet"), "ZZ00000857A": (96, "Pallet"),
    "BD0104E001MSGZ0D": (21, "FLC"), "BW00000014YW0A": (60, "Trolley"),
    "BW00001438DCB": (30, "FLC"), "BW00001438SYB": (30, "FLC"),
    "BW00001438GDB": (30, "FLC"), "BW00001438WEB": (30, "FLC"),
    "BW00001439DCB": (30, "FLC"), "BW00001439GDB": (30, "FLC"),
    "BW00001439SYB": (30, "FLC"), "BW00001439WEB": (30, "FLC"),
    "ZZ00000823SN0A": (60, "FLC"), "ZZ00000823SY0A": (60, "FLC"),
    "ZZ00000823WE0A": (60, "FLC"), "SN00000054B": (140, "FLC"),
    "SN00000055B": (140, "FLC"), "BF0101B005MFBZ0A": (60, "Trolley"),
    "MA00001463BLD": (32, "Trolley"), "WT00000107BLA": (32, "Trolley"),
    "BW00001437DCA": (6, "FLC"), "BW00001437SYA": (6, "FLC"),
    "BW00001437WEA": (6, "FLC"), "BW00000024WE0A": (60, "FLC"),
    "BW00000024YW0B": (32, "FLC"), "BW00000359SYA": (24, "FLC"),
    "BW00000359GDA": (24, "FLC"), "BW00000359DCA": (24, "FLC"),
    "BW00000359WEA": (24, "FLC"), "SN00000007B": (30, "Trolley"),
    "BE0203B003MNNG": (30, "Trolley"), "BW00000295A": (1792, "Pallet"),
    "BS00000610A": (576, "Pallet"), "BW00000009FB0B": (35, "FLC"),
    "MA00001463OED": (32, "Trolley"), "MA00001463RDD": (32, "Trolley"),
    "WT00000107RDA": (32, "Trolley"), "WT00000107OEA": (32, "Trolley"),
    "MA00001463OE0B": (32, "Trolley"), "MA00001463BL0B": (32, "Trolley"),
    "MA00001463RD0B": (32, "Trolley"), "340A0015243C": (100, "FLC"),
    "BE0601A001MNNG": (160, "Pallet"), "BF0801A004MNNZ0E": (160, "Pallet"),
    "CS00004249B": (72, "Pallet"), "340A0018306D": (500000, "Pallet"),
    "BW00000684BMA": (168, "Pallet"), "BW00000684BKA": (168, "Pallet"),
    "BD0201H001MNNZ0A": (100, "Pallet"), "CS00000088C": (48, "Pallet"),
    "CS00001173B": (88, "Trolley"), "ZZ00000882A": (60, "Trolley"),
    "BE0208A004MNNZ0B": (60, "Trolley"), "340A0013625E": (256, "Pallet"),
    "BE0201C005MNNZ0F": (32, "Trolley"), "ZZ00000855A": (40, "Trolley"),
    "BE0104C001MNNZ0D": (60, "Trolley"), "BW00000365BMA": (40, "Pallet"),
    "BW00000365BKA": (40, "Pallet"), "BE0104C001MNNZE": (60, "Trolley"),
    "BW00000225C": (1680, "Pallet"), "TS00000072A": (48000, "Pallet"),
    "340A0021507A": (2000, "Pallet"), "BW00001136BMA": (16, "FLC"),
    "BW00001136BKA": (16, "FLC"), "BE0113C001MNNZA": (288, "Pallet"),
    "TS00000052B": (576, "Pallet"), "340A0006351D": (200, "Pallet"),
    "340A0006351F": (200, "Pallet"), "TS00000402B": (288, "Pallet"),
    "BE0802A015MNNB": (600, "Pallet"), "BE0802A014MNNB": (600, "Pallet"),
    "ES00001376B": (1120, "Pallet"), "ES00001377B": (1120, "Pallet"),
    "BF0805A001MNNZE": (784, "Pallet"), "BF0805A002MNNZE": (784, "Pallet"),
    "BF0805B002MNNZ0F": (64, "Pallet"), "BF0805F003MNNZ0C": (1600, "Pallet"),
    "LL00000005C": (64, "Pallet"), "LL00000005D": (64, "Pallet"),
    "BF0805B003MNNZ0F": (80, "Pallet"), "BE0203B013MNNA": (198, "FLC"),
    "BE0203B014MNNA": (198, "FLC"), "BS00000497A": (200, "Trolley"),
    "BD0105C001MSGZ0B": (78, "FLC"), "340A0003365H": (5600, "Pallet"),
    "340A0003366H": (6000, "Pallet"), "CS00006960A": (350, "Pallet"),
    "CS00006959A": (350, "Pallet"), "ZZ00000824SN0A": (60, "FLC"),
    "BE0203B009MNNA": (3780, "Pallet"), "340A0000539E": (200, "Trolley"),
    "BW00001559A": (48000, "Pallet"), "340A0013724E": (14400, "Pallet"),
    "BE0201E001ANNZ0F": (36, "FLC"), "BE0401A002ANNG": (120, "FLC"),
    "BW00000035D": (144, "Pallet"), "340A0001050I": (300, "Pallet"),
    "340A0001034I": (1200, "Pallet"), "BE0401A001ANNE": (72, "FLC"),
    "BE0401A001ANNG": (72, "FLC"), "BE0104F001MNNZA": (120, "Pallet"),
    "ZZ00000834A": (72, "Pallet"), "BE0104G001MNNZA": (120, "Pallet"),
    "ZZ00000835A": (72, "Pallet"), "CS00003149B": (90, "Trolley"),
    "340A0021448H": (280, "Pallet"), "340A0009789B": (3000, "FLC"),
    "340A0003908B": (1000, "FLC"), "340A0003912C": (1000, "FLC"),
    "BE0105D001MNNZ0D": (96, "Trolley"), "BD0105A001MSGZ0B": (28, "FLC"),
    "BD0105A002MSGZ0B": (28, "FLC"), "BE0109Z001ANNB": (12000, "Pallet"),
    "CS00006936B": (19200, "Pallet"), "CS00004336A": (1536, "Pallet"),
    "CS00004337A": (1536, "Pallet"), "340A0006332B": (400, "Pallet"),
    "BF0502B002ANNC": (1600, "Pallet"), "BY00000893A": (320, "Pallet"),
    "BW00001433A": (96, "Pallet"), "BS00000484A": (1400, "Pallet"),
    "BS00000482A": (4680, "Pallet"), "BS00000514A": (960, "Pallet"),
    "BS00000621A": (14400, "Pallet"), "BW00000481A": (32, "FLC"),
    "BF0107C001ANNB": (280, "Pallet"), "ZZ00000837A": (96, "Pallet"),
    "SN00000053B": (140, "FLC"), "SN00000035BLC": (286, "FLC"),
    "SN00000035BLD": (286, "FLC"), "SN00000035ABC": (286, "FLC"),
    "SN00000035ABD": (286, "FLC"), "SN00000035GNC": (286, "FLC"),
    "SN00000035GND": (286, "FLC"), "SN00000035OEC": (286, "FLC"),
    "SN00000035OED": (286, "FLC"), "SN00000035RDC": (286, "FLC"),
    "SN00000035RDD": (286, "FLC"), "SN00000035WEC": (286, "FLC"),
    "SN00000035WED": (286, "FLC"), "SN00000035YWC": (286, "FLC"),
    "SN00000035YWD": (286, "FLC"), "SN00000037B": (1152, "Pallet"),
    "ZZ00000860A": (35, "FLC"), "BF0101C001ANNB": (120, "Trolley"),
    "BW00000342B": (35, "FLC"), "BW00001427BMB": (100, "Trolley"),
    "BW00001427BKB": (100, "Trolley"), "MA00001512OEC": (32, "Trolley"),
    "MA00001512RDC": (32, "Trolley"), "WT00000104OEA": (32, "Trolley"),
    "WT00000104RDA": (32, "Trolley"), "MA00001513BLC": (32, "Trolley"),
    "WT00000105BLA": (32, "Trolley"), "MA00001512BLC": (32, "Trolley"),
    "WT00000104BLA": (32, "Trolley"), "MA00001512OE0A": (32, "Trolley"),
    "MA00001513BL0A": (32, "Trolley"), "MA00001489BL0B": (32, "Trolley"),
    "WT00000106BLA": (32, "Trolley"), "MA00001512BL0A": (32, "Trolley"),
    "MA00001512RD0A": (32, "Trolley"), "340A0004363B": (9600, "Pallet"),
    "BS00000490A": (30, "FLC"), "SN00000005C": (120, "FLC"),
    "SN00000006C": (120, "FLC"), "BD0107B001MSGZ0B": (60, "FLC"),
    "ZZ00000780B": (550, "Pallet"), "ZZ00000781A": (550, "FLC"),
    "BE0201A003MNNB": (160, "Trolley"), "340A0003135C": (1000, "FLC"),
    "BW00000098D": (21, "Trolley"), "BW00000286B": (21, "Trolley"),
    "BW00001482A": (21, "Trolley"), "BF0108A001MNNC": (18, "Trolley"),
    "BW00000341D": (21, "Trolley"), "BW00000098E": (21, "Trolley"),
    "340A0005486I": (1232, "Pallet"), "ZZ00000149B": (1280, "Pallet"),
    "340A0001097G": (14400, "Pallet"), "340A0014645D": (16000, "Pallet"),
    "EP00000586A": (9000, "Pallet"), "CS00009507B": (448, "Pallet"),
    "ES00001326B": (160, "Pallet"), "ZZ00000779A": (12, "Pallet"),
    "BE0312C016ANNA": (3000, "FLC"), "BE0312C015ANNA": (7200, "FLC"),
    "CS00003170A": (3360, "Pallet"), "TS00000015B": (144, "Pallet"),
    "BD0109B001MNNZE": (60, "Trolley"), "BW00000667A": (8000, "FLC"),
    "BW00000666A": (8000, "FLC"), "ZZ00000852A": (30, "Trolley"),
    "BW00000304B": (2560, "Pallet"), "CS00001079B": (48, "Trolley"),
    "ZZ00000839A": (60, "Pallet"), "CS00003886B": (44, "Trolley"),
    "CS00004474B": (16, "Trolley"), "BW00000032WE0B": (28, "FLC"),
    "BW00000478SYA": (60, "Pallet"), "BW00001259DCA": (24, "FLC"),
    "BW00001259WEA": (24, "FLC"), "BW00001259GDA": (24, "FLC"),
    "BE0105D001MNNZE": (96, "Trolley"), "BW00000472DCB": (19, "FLC"),
    "BW00000472SYB": (19, "FLC"), "BW00000472WEB": (19, "FLC"),
    "BW00000473DCB": (19, "FLC"), "BW00000473WEB": (19, "FLC"),
    "BW00000030YW0A": (28, "FLC"), "BW00000031WE0A": (28, "FLC"),
    "ES00002002B": (4600, "Pallet"), "ES00001837B": (2160, "Pallet"),
    "DB00000837B": (160, "Pallet"), "EP00000334A": (400, "Pallet"),
    "BE0311C002MNNZ0E": (45, "Pallet"), "ZZ00000840A": (256, "Pallet"),
    "BW00000140A": (72, "Pallet"), "BW00000139B": (72, "Pallet"),
    "ZZ00000838A": (60, "Pallet"), "CS00004724B": (6, "Trolley"),
    "CS00000066D": (16, "Trolley"), "ES00001379B": (5600, "Pallet"),
    "LL00000314A": (4000, "Pallet"), "CS00008763A": (90, "Trolley"),
    "BE0203B008MNNA": (1980, "Pallet"), "BW00001435BMB": (24000, "Pallet"),
    "BW00001435BKB": (24000, "Pallet"), "ES00001380B": (5600, "Pallet"),
    "ZZ00000696B": (200, "Pallet"), "EP00001127A": (200, "Pallet"),
    "ES00001381B": (5600, "Pallet"), "340A0017565A": (1000, "FLC"),
    "340A0003083B": (500, "Trolley"), "CS00000017A": (360, "Pallet"),
    "CS00000017OEA": (480, "Pallet"), "CS00000023A": (144, "Pallet"),
    "CS00000023OEA": (216, "Pallet"), "EP00000337C": (6000, "Pallet"),
    "BD0106A001MNNZ0F": (240, "Pallet"), "BW00001440A": (16, "FLC"),
    "ZZ00000858A": (56, "Pallet"), "BW00000026C": (200, "Pallet"),
    "ZZ00000918A": (9600, "Pallet"), "ZZ00000919A": (16000, "Pallet"),
    "BE1001A001MNNZ0C": (320, "Pallet"), "BE1001A002MNNZ0C": (320, "Pallet"),
    "CS00000394B": (448, "Pallet"), "CS00000393B": (448, "Pallet"),
    "BW00000341F": (21, "Trolley"), "MO00000010B": (72, "FLC"),
    "DB00000982A": (160, "Pallet"), "BF0805F003MNNZ0D": (1600, "Pallet"),
    "CS00003149C": (120, "Trolley"), "ZZ00000826TDA": (21, "FLC"),
    "ZZ00000842TDA": (32, "FLC"), "ZZ00000854TDA": (60, "FLC"),
    "ZZ00000853TDA": (60, "FLC"), "ZZ00000825TDA": (21, "FLC"),
    "ZZ00000827TDA": (21, "FLC"), "ZZ00000820TDA": (18, "FLC"),
    "ZZ00000842YW0A": (32, "FLC"), "ZZ00000853YW0A": (60, "Trolley"),
    "ZZ00000827YW0A": (21, "FLC"), "ZZ00000854YW0A": (60, "Trolley"),
    "ZZ00000821YW0A": (110, "Trolley"), "ZZ00000826YW0A": (21, "FLC"),
    "ZZ00000822YW0A": (110, "Trolley"), "ZZ00000825YW0A": (21, "FLC"),
    "BS00000340C": (200, "Trolley"), "BS00000341B": (14400, "Pallet"),
    "BS00000342B": (14400, "Pallet"), "BS00000343B": (14400, "Pallet"),
    "BS00000344B": (14400, "Pallet"), "BD0107B001MHSZ0D": (60, "FLC"),
    "BD0104E001MHSZ0E": (36, "FLC"), "BD0104D001MHSZ0E": (36, "FLC"),
    "BD0103A001MHSZ0E": (40, "FLC"), "BD0103A002MHSZ0E": (40, "FLC"),
    "BD0105B002MHSZ0D": (66, "FLC"), "BD0105C001MHSZ0D": (66, "FLC"),
    "BD0105A002MHSZ0E": (24, "FLC"), "BD0105A001MHSZ0E": (24, "FLC"),
    "ZZ00000821TDA": (110, "FLC"), "ZZ00000822TDA": (110, "FLC"),
    "WT00000079B": (200, "Pallet"), "DB00000362B": (128, "Pallet"),
    "WT00000080B": (200, "Pallet"), "DB00000897A": (128, "Pallet"),
    "WT00000110A": (200, "Pallet"), "WT00000111A": (200, "Pallet"),
    "CS00003169A": (64, "Trolley"), "ZZ00000884A": (400, "Pallet"),
    "EP00000944B": (1920, "Pallet"), "EP00001193A": (1920, "Pallet"),
    "340A0004176D": (2400, "Pallet"), "BD0812A001MNNZ0A": (2400, "Pallet"),
    "BW00001259SYA": (24, "FLC"), "BW00001437GDA": (6, "FLC"),
    "BW00000472GDB": (19, "FLC"), "BW00000473GDB": (19, "FLC"),
    "BW00000473SYB": (19, "FLC"), "BW00001486FBC": (28, "FLC"),
    "CS00004044B": (4800, "Pallet"), "BE0201J002ANNZ0C": (1440, "Pallet"),
    "BW00001144GDA": (2496, "FLC"), "BW00001979A": (144, "Pallet"),
    "TS00000237A": (288, "Pallet"), "340A0006351J": (200, "Pallet"),
    "ZZ00000830C": (60, "FLC"), "ZZ00000831C": (60, "FLC"),
    "ZZ00000825WE0A": (21, "FLC"), "ZZ00000842WE0A": (32, "FLC"),
    "BD0104D001MWEZE": (36, "FLC"), "ZZ00000827WE0A": (21, "FLC"),
    "BD0103A002MGYZE": (40, "FLC"), "ZZ00000853WE0A": (60, "FLC"),
    "BD0105A002MGYZE": (24, "FLC"), "BD0105A002MWEZE": (24, "FLC"),
    "BD0105B002MWEZD": (66, "FLC"), "BD0105C001MWEZD": (66, "FLC"),
    "BD0107B001MGYZD": (60, "FLC"), "BD0107B001MWEZD": (60, "FLC"),
    "BF0503B012ANNE": (320, "Pallet"), "ZZ00000820SY0A": (32, "FLC"),
    "BF0503B020ANNF": (360, "Pallet"), "BW00000323BE0A": (28, "FLC"),
    "ZZ00000820WE0A": (18, "FLC"), "BF0101C004ANNB": (192, "Trolley"),
    "BD0103A001MWEZE": (40, "FLC"), "BD0103A002MWEZE": (40, "FLC"),
    "BD0104E001MWEZE": (36, "FLC"), "ZZ00000854WE0A": (60, "FLC"),
    "BD0105A001MWEZE": (24, "FLC"), "BD0105C001MGYZD": (66, "FLC"),
    "BD0105C001MTRZD": (66, "FLC"), "ZZ00000821SY0A": (120, "FLC"),
    "ZZ00000821WE0A": (110, "FLC"), "ZZ00000822SY0A": (120, "FLC"),
    "ES00001449B": (800, "Pallet"), "ZZ00000822WE0A": (110, "FLC"),
    "ES00002012B": (800, "Pallet"), "ZZ00000825SY0A": (21, "FLC"),
    "ZZ00000826SY0A": (21, "FLC"), "BW00000293C": (36, "Trolley"),
    "ZZ00000826WE0A": (21, "FLC"), "ZZ00000827SY0A": (24, "FLC"),
    "ZZ00000842SY0A": (36, "FLC"), "ZZ00000853SY0A": (60, "FLC"),
    "ZZ00000854SY0A": (60, "FLC"), "ZZ00000907A": (120, "Pallet"),
    "ZZ00000912B": (150, "Pallet"), "BW00000324BE0A": (28, "FLC"),
    "ZZ00000913A": (120, "Pallet"), "340A0013728C": (4800, "Pallet"),
    "BW00000479A": (40, "Pallet"), "BY00000583E": (480, "Pallet"),
    "BW00000363B": (200, "Pallet"), "ZZ00000779C": (150, "Pallet"),
    "BD0105A001MTRZE": (24, "FLC"), "BD0105B002MGYZD": (66, "FLC"),
    "340A0020366OEZ0H": (72, "FLC"), "BD0103A001MGYZE": (40, "FLC"),
    "BF0503B009ANNE": (1518, "Pallet"), "BF0503B015ANNG": (5280, "Pallet"),
    "BW00000032YW0A": (21, "FLC"), "BW00001262A": (1440, "Pallet"),
    "CS00000116A": (360, "Pallet"), "CS00001792B": (1078, "Pallet"),
    "WT00000001A": (600, "Pallet"), "ZZ00000221A": (720, "Pallet"),
    "ZZ00000695A": (182, "FLC"), "340A0021170Z0D": (182, "FLC"),
    "BW00001224A": (3600, "Pallet"), "CS00003169MGA": (32, "Trolley"),
    "WT00000002B": (600, "Pallet"), "ZZ00000800A": (720, "Pallet"),
    "BE0201G015AOEZ0A": (72, "FLC"), "BW00001126A": (3200, "Pallet"),
    "CS00000221MGB": (32, "Trolley"), "BW00001088A": (952, "Pallet"),
    "CS00000023RDA": (72, "Pallet"), "340A0021171Z0D": (525, "Pallet"),
    "BW00000960B": (192, "Pallet"), "CS00000017RDA": (240, "Pallet"),
    "BW00000906A": (1000, "Pallet"), "BD0104D001MGYZE": (36, "FLC"),
    "BW00000362B": (200, "FLC"), "BW00000478GDA": (60, "Pallet"),
    "BD0104E001MGYZE": (36, "FLC"), "BD0105A001MGYZE": (24, "FLC"),
    "340A0020366Z0H": (150, "Pallet"), "340A0020370Z0G": (336, "FLC"),
    "340A0020368Z0G": (150, "Pallet"), "340A0020371Z0G": (336, "FLC"),
    "BD0103A001MTRZE": (40, "FLC"), "BE0201G014ANNZ0A": (400, "Pallet"),
    "BE0201G015ANNZ0A": (550, "Pallet"), "BE0201G016ANNZ0A": (600, "Pallet"),
    "BD0103A002MTRZE": (40, "FLC"), "BD0104D001MTRZE": (36, "FLC"),
    "BD0104E001MTRZE": (36, "FLC"), "BD0105A002MTRZE": (24, "FLC"),
    "BD0107B001MTRZD": (60, "FLC"), "CS00000044D": (200, "Pallet"),
    "EP00000118B": (1600, "Pallet"), "ZZ00000772D": (2000, "Pallet"),
    "BD0105B002MTRZD": (66, "FLC"), "BE0201B001MNNZ0A": (1200, "Pallet"),
    "ZZ00000778C": (150, "Pallet"), "BE0401A002MNNZ0C": (72, "FLC"),
    "BF0503B020ANNE": (360, "Pallet"), "BY00004219B": (480, "Pallet"),
    "ZZ000007840A": (70, "Pallet"), "EP00000352B": (8955, "Pallet"),
    "EP00000092A": (4130, "Pallet"), "BW00001144SYA": (2496, "Pallet"),
    "BE0201B002MNNZ0A": (1200, "Pallet"), "CS00002770B": (550, "Pallet"),
    "BD0201G001MGNZ0B": (720, "Pallet"), "EP00000628B": (200, "Pallet"),
    "EP00001023B": (200, "Pallet"), "BD0201G002MGNZ0B": (720, "Pallet"),
    "BD0201G001MYWZ0B": (720, "Pallet"), "BD0103A002MSBZ0E": (30, "Trolley"),
    "BD0104D001MSBZ0E": (60, "Trolley"), "BD0201G002MYWZ0B": (720, "Pallet"),
    "BD0105B002MSBZ0D": (85, "Trolley"), "BD0107B001MSBZ0D": (84, "Trolley"),
    "BD0105C001MSBZ0D": (85, "Trolley"), "BD0105A001MSBZ0E": (36, "Trolley"),
    "BD0103A001MLGZ0E": (30, "Trolley"), "BD0103A002MLGZ0E": (30, "Trolley"),
    "BD0105A001MLGZ0E": (36, "Trolley"), "BD0105A002MLGZ0E": (36, "Trolley"),
    "BD0105B002MCBZ0D": (85, "Trolley"), "BD0104D001MCBZ0E": (60, "Trolley"),
    "BD0107B001MCBZ0D": (84, "Trolley"), "MC00000177H": (128, "Pallet"),
    "340A0020368OEZ0G": (72, "Pallet"), "BE0205A002MNND": (480, "Pallet"),
    "DB00000707C": (128, "Pallet"), "ZZ00000822MB0A": (110, "FLC"),
    "DB00000362D": (128, "Pallet"), "ZZ00000820MB0A": (18, "FLC"),
    "DB00000897B": (128, "Pallet"), "BD0201G001MWEZ0B": (720, "Pallet"),
    "ZZ00000842MB0A": (32, "FLC"), "ZZ00000827MB0A": (21, "FLC"),
    "BW00001210A": (3200, "Pallet"), "ZZ00000825MB0A": (21, "FLC"),
    "BW00001211A": (8000, "Pallet"), "ZZ00000826MB0A": (21, "FLC"),
    "BD0103A001MSBZ0E": (30, "Trolley"), "ZZ00000825SN0A": (21, "FLC"),
    "ZZ00000821MB0A": (110, "FLC"), "BD0201G002MWEZ0B": (720, "Pallet"),
    "BD0104E001MSBZ0E": (60, "Trolley"), "BD0105A002MSBZ0E": (36, "Trolley"),
    "ZZ00000842SN0A": (32, "FLC"), "ZZ00000827SN0A": (21, "FLC"),
    "BD0105B002MLGZ0D": (85, "Trolley"), "ZZ00000826SN0A": (21, "FLC"),
    "ZZ00000853MB0A": (60, "FLC"), "ZZ00000854MB0A": (60, "FLC"),
    "BD0104E001MLGZ0E": (60, "Trolley"), "ZZ00000853SN0A": (60, "FLC"),
    "BD0107B001MLGZ0D": (84, "Trolley"), "ZZ00000854SN0A": (60, "FLC"),
    "BD0103A001MCBZ0E": (30, "Trolley"), "BD0103A002MCBZ0E": (30, "Trolley"),
    "BD0105C001MCBZ0D": (85, "Trolley"), "BD0105A001MCBZ0E": (36, "Trolley"),
    "BD0105A002MCBZ0E": (36, "Trolley"), "ZZ00000822SN0A": (110, "FLC"),
    "BD0104D001MLGZ0E": (60, "FLC"), "ZZ00000821SN0A": (110, "FLC"),
    "BD0104E001MCBZ0E": (60, "Trolley"), "BD0103A001MBEZ0E": (90, "FLC"),
    "BD0103A002MBEZ0E": (60, "FLC"), "BD0105B002MBEZ0D": (85, "FLC"),
    "BD0105C001MBEZ0D": (85, "FLC"), "BD0104D001MBEZ0E": (60, "FLC"),
    "BD0104E001MBEZ0E": (60, "FLC"), "BD0107B001MBEZ0D": (84, "FLC"),
    "DB00000837C": (160, "Pallet"), "ZZ00000872A": (200, "Pallet"),
    "EP00000002C": (100, "FLC"), "BE0202A001MNNG": (30, "FLC"),
    "CS00001081B": (480, "Pallet"), "BE0401A002ANNH": (120, "FLC"),
    "BS00000355B": (80, "Pallet"), "BD0105C001MLGZ0D": (85, "Trolley"),
    "BE0203B016MNNC": (60, "FLC"), "340A0006333E": (300, "Pallet"),
    "BW00000478DCA": (100, "Pallet")
}

def get_official_pss(part_id: str, desc: str = "") -> Tuple[int, str]:
    pid = str(part_id).strip()
    if pid in OFFICIAL_PSS_MASTER:
        return OFFICIAL_PSS_MASTER[pid]
    d = (desc or "").upper()
    if any(k in d for k in ["COVER", "FENDER", "PANEL", "FRAME", "LAMP", "SHROUD"]):
        return 60, "Pallet"
    elif any(k in d for k in ["BOLT", "NUT", "WASHER", "SCREW", "CLIP"]):
        return 500, "Trolley"
    return 100, "FLC"

def calculate_pss_units(req_qty: int, part_id: str, desc: str = "") -> Tuple[int, int, int, str]:
    box_size, pss_type = get_official_pss(part_id, desc)
    if box_size <= 0 or req_qty <= 0:
        return req_qty, req_qty, 1, pss_type
    pss_count = math.ceil(req_qty / box_size)
    need_to_be_picked = pss_count * box_size
    return need_to_be_picked, box_size, pss_count, pss_type


# ---------------------------------------------------------
# 2. QR CODE GENERATION (50 x 100 mm Thermal Label)
# ---------------------------------------------------------
def generate_qr_base64(data_text: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=4, border=1)
    qr.add_data(data_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    b = BytesIO()
    img.save(b, format="PNG")
    return base64.b64encode(b.getvalue()).decode("utf-8")


def render_50x100_thermal_sticker(
    picklist_id: str,
    pss_num: int,
    total_pss: int,
    part_id: str,
    part_name: str,
    pss_qty: int,
    pss_type: str,
    seq_no: int,
    starvation_mins: int = 0,
    seq_coverage_str: str = ""
) -> str:
    qr_data = f"{part_id}#{pss_qty}#{picklist_id}#{pss_num}"
    qr_img = generate_qr_base64(qr_data)
    urgency_tag = f"STARVES IN {starvation_mins}m" if starvation_mins > 0 else "NORMAL"

    return f"""
    <div class="thermal-sticker" style="
        width: 100mm; height: 50mm; border: 2px solid #000; padding: 3mm;
        margin: 2mm auto; font-family: Arial, sans-serif; background: #fff;
        color: #000; box-sizing: border-box; page-break-after: always;
        display: flex; flex-direction: column; justify-content: space-between;
    ">
        <div style="display: flex; justify-content: space-between; border-bottom: 2px solid #000; padding-bottom: 2px;">
            <span style="font-size: 13px; font-weight: 900;">ATHER ENERGY - PSS</span>
            <span style="font-size: 11px; font-weight: bold; background: #000; color: #fff; padding: 1px 5px; border-radius: 2px;">
                {pss_type.upper()} ({pss_num}/{total_pss})
            </span>
        </div>
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div style="width: 65%;">
                <div style="font-size: 16px; font-weight: 900;">{part_id}</div>
                <div style="font-size: 10px; font-weight: bold; height: 18px; overflow: hidden; line-height: 10px; margin-top: 2px;">
                    {part_name[:38]}
                </div>
                <div style="margin-top: 3px; display: flex; gap: 10px;">
                    <div><span style="font-size: 8px; display: block; color: #333;">PICK QTY:</span><span style="font-size: 16px; font-weight: 900;">{pss_qty}</span></div>
                    <div><span style="font-size: 8px; display: block; color: #333;">URGENCY:</span><span style="font-size: 10px; font-weight: bold; color: #d32f2f;">{urgency_tag}</span></div>
                </div>
                <div style="font-size: 9px; font-weight: bold; margin-top: 2px; color: #1565c0;">
                    {seq_coverage_str[:32]}
                </div>
            </div>
            <div style="width: 32%; text-align: right;">
                <img src="data:image/png;base64,{qr_img}" style="width: 30mm; height: 30mm;" />
            </div>
        </div>
        <div style="display: flex; justify-content: space-between; border-top: 1px solid #666; padding-top: 2px; font-size: 8px;">
            <span>PL: <b>{picklist_id}</b></span>
            <span>TOKEN: <b>{part_id}#{pss_qty}#{pss_num}</b></span>
        </div>
    </div>
    """


# ---------------------------------------------------------
# 3. PROVEN SCALEX SCRAPER
# ---------------------------------------------------------
def extract_batches_from_text(raw_text: str) -> List[dict]:
    pattern = re.compile(
        r"([A-Z0-9]+)\s+(HOLD|PLAN)\s+(Line_\d+|\S+)\s+(\d+)\s+(\d+)\s+(\d+)",
        re.IGNORECASE,
    )
    records = []
    for match in pattern.finditer(raw_text):
        sku, status, line, s_start, s_end, count = match.groups()
        records.append({
            "SKUName": sku.strip(),
            "Status": status.strip().upper(),
            "Line": line.strip(),
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
                if "LINE_1" in opt_text or "LINE_2" in opt_text:
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

        time.sleep(1)
        for target in [page] + page.frames:
            btn = target.query_selector("input[value*='View Report'], button:has-text('View Report')")
            if btn:
                btn.click()
                break

        for _ in range(12):
            time.sleep(2)
            all_texts = [page.inner_text("body")] + [f.inner_text("body") for f in page.frames]
            for text in all_texts:
                if "SERIAL NO START" in text.upper():
                    records = extract_batches_from_text(text)
                    if records:
                        df = pd.DataFrame(records)
                        if line_name.lower() in df["Line"].str.lower().values:
                            return df
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
            df_l1["Line"] = "Line_1"
            all_batches.append(df_l1)

        df_l2 = select_line_and_fetch(page, "Line_2")
        if not df_l2.empty:
            df_l2["Line"] = "Line_2"
            all_batches.append(df_l2)

        browser.close()

    if not all_batches:
        return pd.DataFrame()

    combined_df = pd.concat(all_batches, ignore_index=True)
    combined_df["ScaleX_Sequence_No"] = combined_df.groupby("Line").cumcount() + 1
    combined_df.to_csv(CSV_BATCHES, index=False)
    return combined_df


def load_uploaded_file(uploaded_file) -> pd.DataFrame:
    if uploaded_file is None:
        return pd.DataFrame()
    file_name = uploaded_file.name.lower()
    try:
        if file_name.endswith(".csv"):
            return pd.read_csv(uploaded_file, dtype=str)
        elif file_name.endswith((".xlsx", ".xls")):
            return pd.read_excel(uploaded_file, dtype=str)
    except Exception as e:
        st.error(f"Error reading {file_name}: {e}")
    return pd.DataFrame()


# ---------------------------------------------------------
# 4. TAKT TIME & MULTI-SEQUENCE TRACEABILITY ENGINE
# ---------------------------------------------------------
def calculate_band2_and_pss_engine(
    batches_df: pd.DataFrame,
    mb52_df: pd.DataFrame,
    cogi_df: pd.DataFrame,
    selected_line: str,
    hide_hold: bool,
    selected_seqs: List[int],
) -> pd.DataFrame:
    if batches_df.empty or not os.path.exists(BACKGROUND_BOM):
        return pd.DataFrame()

    bom_df = pd.read_csv(BACKGROUND_BOM, dtype=str)
    filtered = batches_df.copy()

    if hide_hold:
        filtered = filtered[filtered["Status"] == "PLAN"]
    plan_batches = filtered[filtered["Status"] == "PLAN"].copy()

    if selected_line != "All":
        plan_batches = plan_batches[plan_batches["Line"].str.lower() == selected_line.lower()]
    if selected_seqs:
        plan_batches = plan_batches[plan_batches["ScaleX_Sequence_No"].isin(selected_seqs)]

    if plan_batches.empty:
        return pd.DataFrame()

    bom_clean = bom_df.copy()
    bom_clean.columns = [c.strip() for c in bom_clean.columns]
    parent_col = "Parent Material"
    child_col = "Child Material"
    qty_col = "Comp Qty."
    desc_col = "Description"

    bom_clean["Clean_Qty"] = pd.to_numeric(
        bom_clean[qty_col].astype(str).str.replace(",", "").str.strip(), errors="coerce"
    ).fillna(0)

    # 1-MINUTE TAKT TIME RUNOUT OFFSETS (Line 1 & Line 2 Simultaneous)
    line_offsets = {"line_1": 0, "line_2": 0}
    batch_takt_minutes = {}

    for _, b in plan_batches.sort_values(by=["Line", "ScaleX_Sequence_No"]).iterrows():
        b_line = b["Line"].lower()
        seq_k = f"{b['Line']}#{b['ScaleX_Sequence_No']}"
        batch_takt_minutes[seq_k] = line_offsets[b_line]
        line_offsets[b_line] += int(b["Count"])  # 1 min per scooter takt time

    # Explode BOM batch-by-batch
    sequence_rows = []
    for _, batch in plan_batches.iterrows():
        sku = batch["SKUName"]
        line = batch["Line"]
        seq_no = batch["ScaleX_Sequence_No"]
        count = batch["Count"]
        start_min = batch_takt_minutes.get(f"{line}#{seq_no}", 0)

        sku_bom = bom_clean[bom_clean[parent_col] == sku]
        for _, comp in sku_bom.iterrows():
            sequence_rows.append({
                "Line": line,
                "Sequence_Order": seq_no,
                "Vehicle_SKU": sku,
                "Child Material": comp[child_col],
                "Description": comp[desc_col],
                "Required_Qty": comp["Clean_Qty"] * count,
                "Minute_Offset": start_min,
            })

    df_seq = pd.DataFrame(sequence_rows)
    if df_seq.empty:
        return pd.DataFrame()

    # Aggregate & Trace Multiple Sequences across Line 1 & Line 2
    def build_sequence_coverage_str(sub_df):
        coverage = []
        for l_name in ["Line_1", "Line_2"]:
            l_seqs = sub_df[sub_df["Line"].str.lower() == l_name.lower()]["Sequence_Order"].unique()
            if len(l_seqs) > 0:
                coverage.append(f"{'L1' if '1' in l_name else 'L2'}: #" + ", #".join(map(str, sorted(l_seqs))))
        return " | ".join(coverage)

    grouped = []
    for (child_mat, desc), group_data in df_seq.groupby(["Child Material", "Description"]):
        earliest_min = int(group_data["Minute_Offset"].min())
        seq_str = build_sequence_coverage_str(group_data)
        sku_str = ", ".join(sorted(list(set(group_data["Vehicle_SKU"]))))
        
        # Priority mapping based on takt runout time
        if earliest_min <= 60:
            prio = "🔴 P1: CRITICAL (<60m)"
        elif earliest_min <= 120:
            prio = "🟡 P2: HIGH (60-120m)"
        else:
            prio = "🟢 P3: NORMAL (>120m)"

        grouped.append({
            "Child Material": child_mat,
            "Description": desc,
            "Sequence_Order": int(group_data["Sequence_Order"].min()),
            "Total_Demand": group_data["Required_Qty"].sum(),
            "Starvation_Mins": earliest_min,
            "Priority": prio,
            "Sequences_Covered": seq_str,
            "Vehicle_SKU": sku_str,
        })

    aggregated = pd.DataFrame(grouped).sort_values(by="Starvation_Mins", ascending=True)

    # Ingest MB52
    if not mb52_df.empty:
        mb = mb52_df.copy()
        mb.columns = [c.strip() for c in mb.columns]
        mat_c = next((c for c in mb.columns if "material" in c.lower()), "Material")
        plant_c = next((c for c in mb.columns if "plant" in c.lower()), "Plant")
        unrest_c = next((c for c in mb.columns if "unrestricted" in c.lower() and "value" not in c.lower()), "Unrestricted")

        mb["Stock_Qty"] = pd.to_numeric(mb[unrest_c].astype(str).str.replace(",", "").str.strip(), errors="coerce").fillna(0)

        m002_stock = mb[mb[plant_c].str.upper() == "M002"].groupby(mat_c)["Stock_Qty"].sum().reset_index()
        m002_stock.columns = ["Child Material", "M002_Stock"]

        w004_stock = mb[mb[plant_c].str.upper() == "W004"].groupby(mat_c)["Stock_Qty"].sum().reset_index()
        w004_stock.columns = ["Child Material", "W004_Stock"]

        aggregated = pd.merge(aggregated, m002_stock, on="Child Material", how="left")
        aggregated = pd.merge(aggregated, w004_stock, on="Child Material", how="left")
        aggregated["M002_Stock"] = aggregated["M002_Stock"].fillna(0)
        aggregated["W004_Stock"] = aggregated["W004_Stock"].fillna(0)
    else:
        aggregated["M002_Stock"] = 0
        aggregated["W004_Stock"] = 0

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

    final_records = []
    for _, r in aggregated.iterrows():
        req = int(r["Total_Demand"])
        m002 = r["Effective_M002"]
        w004 = r["W004_Stock"]

        if m002 >= req:
            transfer_qty = 0
            transfer_status = "Sufficient M002 Stock"
        else:
            deficit = req - m002
            if w004 >= deficit:
                transfer_qty = deficit
                transfer_status = "Ready for Full Transfer"
            elif w004 > 0:
                transfer_qty = w004
                transfer_status = "Partial Transfer Available"
            else:
                transfer_qty = 0
                transfer_status = "W004 Out of Stock"

        need_pick, box_size, pss_count, p_type = calculate_pss_units(req, r["Child Material"], r["Description"])

        final_records.append({
            "Sequence_Order": r["Sequence_Order"],
            "Part ID": r["Child Material"],
            "Part Name": r["Description"],
            "Required Qty": req,
            "Need to be Picked": need_pick,
            "PSS Qty": box_size,
            "To be picked PSS count": pss_count,
            "PSS Type": p_type,
            "Starvation_Mins": r["Starvation_Mins"],
            "Priority": r["Priority"],
            "Sequences_Covered": r["Sequences_Covered"],
            "M002 Stock": int(m002),
            "COGI Qty": int(r["COGI_Error"]),
            "W004 Stock": int(w004),
            "Transfer Qty (W004 ➡️ M002)": int(transfer_qty),
            "Transfer Status": transfer_status,
            "Is_Zero_Stock": (w004 == 0 and m002 == 0),
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

# 10-MINUTE TIMER ENGINE (Auto-Refresh Background Loop)
if "last_sync_timestamp" not in st.session_state:
    st.session_state["last_sync_timestamp"] = time.time()

time_elapsed = int(time.time() - st.session_state["last_sync_timestamp"])
seconds_left = max(0, 600 - time_elapsed)

# Sidebar Controls
st.sidebar.header("⚙️ ScaleX & SAP Controls")

auto_sync_enabled = st.sidebar.checkbox("Enable 10-Min Auto-Sync", value=True)

if auto_sync_enabled:
    st.sidebar.info(f"🕒 Next Auto-Sync in: **{seconds_left // 60}m {seconds_left % 60}s**")
    # Auto reload trigger after 10 mins
    if time_elapsed >= 600:
        st.session_state["last_sync_timestamp"] = time.time()
        with st.spinner("10-Minute Auto-Syncing ScaleX..."):
            res = fetch_all_scalex_data()
            if not res.empty:
                st.session_state["batches"] = res
                st.rerun()

if st.sidebar.button("🔄 Sync Live ScaleX Plan (Now)", type="primary"):
    with st.spinner("Connecting to ScaleX Portal at 192.168.201.248..."):
        st.session_state["last_sync_timestamp"] = time.time()
        batches_result = fetch_all_scalex_data()
        if not batches_result.empty:
            st.session_state["batches"] = batches_result
            st.sidebar.success(f"Synced {len(batches_result)} batches successfully!")
        else:
            st.sidebar.error("Could not reach 192.168.201.248. Ensure laptop is on Factory Wi-Fi or VPN.")
            if os.path.exists(CSV_BATCHES):
                st.session_state["batches"] = pd.read_csv(CSV_BATCHES)

if st.sidebar.button("🧹 Clear SAP Cache"):
    st.session_state.pop("mb52_df", None)
    st.session_state.pop("cogi_df", None)
    st.session_state.pop("scanned_parts", None)
    st.sidebar.success("Cache cleared!")

st.sidebar.divider()
st.sidebar.subheader("📂 Upload SAP Dumps")
uploaded_mb52 = st.sidebar.file_uploader("Upload MB52 Stock (M002 to W004)", type=["csv", "xlsx", "xls"])
uploaded_cogi = st.sidebar.file_uploader("Upload COGI Dump (Clear Max Entries!)", type=["csv", "xlsx", "xls"])

if uploaded_mb52:
    st.session_state["mb52_df"] = load_uploaded_file(uploaded_mb52)
    st.sidebar.success("MB52 Loaded!")

if uploaded_cogi:
    st.session_state["cogi_df"] = load_uploaded_file(uploaded_cogi)
    st.sidebar.success("COGI Loaded!")

st.sidebar.divider()
st.sidebar.subheader("🛠️ Offline / Manual Plan Upload")
uploaded_plan = st.sidebar.file_uploader("Upload `plan_batches.csv` (If offline)", type=["csv"])
if uploaded_plan:
    st.session_state["batches"] = pd.read_csv(uploaded_plan)
    st.sidebar.success("Manual Plan Loaded!")

if "batches" not in st.session_state:
    if os.path.exists(CSV_BATCHES):
        st.session_state["batches"] = pd.read_csv(CSV_BATCHES)
    else:
        st.warning("⚠️ No production plan loaded.")
        st.info("• Connect to Factory Wi-Fi or VPN and click **'🔄 Sync Live ScaleX Plan'**.")
        st.info("• Or upload a `plan_batches.csv` file in the sidebar if working offline.")
        st.stop()

batches_df = st.session_state["batches"]

# Normalize Columns
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
if "ScaleX_Sequence_No" not in batches_df.columns:
    batches_df["ScaleX_Sequence_No"] = batches_df.groupby("Line").cumcount() + 1

available_seqs = sorted(batches_df["ScaleX_Sequence_No"].unique().tolist())

# FILTERS BAR
st.subheader("🎛️ Viewing, Line & Priority Filters")
f_col1, f_col2, f_col3, f_col4 = st.columns(4)
line_filter = f_col1.selectbox("Select Line:", ["All", "Line_1", "Line_2"])
status_filter = f_col2.selectbox(
    "Filter Status:",
    ["All Statuses", "Ready for Full Transfer", "Partial Transfer Available", "W004 Out of Stock", "Sufficient M002 Stock"]
)
pss_filter = f_col3.selectbox("Filter PSS Type:", ["All", "Pallet", "Trolley", "FLC"])
search_text = f_col4.text_input("Search Part ID / Name:", "")

sf_col1, sf_col2, sf_col3 = st.columns([1, 2, 1])
hide_hold = sf_col1.checkbox("Hide HOLD Batches", value=True)
selected_seqs = sf_col2.multiselect("Select Specific Sequence Numbers (Empty = ALL):", options=available_seqs, default=[])
num_pickers = sf_col3.number_input("Active Pickers (Workload Split):", min_value=1, max_value=10, value=2, step=1)

st.divider()

# Compute Data
mb52_in = st.session_state.get("mb52_df", pd.DataFrame())
cogi_in = st.session_state.get("cogi_df", pd.DataFrame())
master_df = calculate_band2_and_pss_engine(batches_df, mb52_in, cogi_in, line_filter, hide_hold, selected_seqs)

if master_df.empty:
    st.warning("⚠️ No data generated. Ensure `bom.csv` is present in your folder.")
    st.stop()

# Apply Filters
view_df = master_df.copy()
if status_filter != "All Statuses":
    view_df = view_df[view_df["Transfer Status"] == status_filter]
if pss_filter != "All":
    view_df = view_df[view_df["PSS Type"] == pss_filter]
if search_text:
    view_df = view_df[
        view_df["Part ID"].str.contains(search_text, case=False, na=False) |
        view_df["Part Name"].str.contains(search_text, case=False, na=False)
    ]

# SOP Video Summary Cards
c1, c2, c3, c4 = st.columns(4)
c1.metric("🟢 Sufficient M002 Stock", f"{(master_df['Transfer Status'] == 'Sufficient M002 Stock').sum()} SKUs")
c2.metric("✅ Ready for Full Transfer", f"{(master_df['Transfer Status'] == 'Ready for Full Transfer').sum()} SKUs")
c3.metric("🟡 Partial Transfer Available", f"{(master_df['Transfer Status'] == 'Partial Transfer Available').sum()} SKUs")
c4.metric("🔴 W004 Out of Stock", f"{(master_df['Transfer Status'] == 'W004 Out of Stock').sum()} SKUs")

st.divider()

# TABS
tab_master, tab_pickers, tab_loader, tab_deo_scan, tab_band2, tab_sequence = st.tabs([
    "📋 1. Master PSS Picklist",
    "🧑‍🏭 2. Individual Pickers & QR Labels",
    "🚚 3. Truck Loader Checklist",
    "🔍 4. DEO QR Scanner & SAP Transfer",
    "📊 5. Band 2 Transfer Requirements",
    "📈 6. ScaleX Sequence Schedule"
])

# TAB 1: MASTER PSS PICKLIST
with tab_master:
    st.subheader("Aggregated Warehouse Staging Picklist (1-Min Takt Prioritized)")
    st.caption("Ordered by Starvation Urgency. Red highlighted rows indicate zero available stock across M002 and W004.")

    def highlight_zeros(row):
        if row.get("W004 Stock", 0) == 0 and row.get("M002 Stock", 0) == 0:
            return ["background-color: #fee2e2; color: #991b1b; font-weight: bold;"] * len(row)
        return [""] * len(row)

    display_master = view_df.drop(columns=["Is_Zero_Stock"], errors="ignore")
    st.dataframe(display_master.style.apply(highlight_zeros, axis=1), use_container_width=True, hide_index=True)

    csv_out = display_master.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download Master Picklist (CSV)", data=csv_out, file_name="Master_PSS_Picklist.csv", mime="text/csv")

# TAB 2: INDIVIDUAL PICKERS & 50x100mm QR LABELS
with tab_pickers:
    st.subheader(f"🧑‍🏭 Individual Picker Assignments & Thermal QR Labels (Split for {num_pickers} Pickers)")
    st.caption("Each picker receives a balanced list showing exact starvation urgency and multi-sequence coverage.")

    pickable_df = view_df[~view_df["Is_Zero_Stock"]].copy()

    if not pickable_df.empty:
        picker_tabs = st.tabs([f"🧑‍🏭 Picker {i+1}" for i in range(num_pickers)])

        for p_idx, p_tab in enumerate(picker_tabs):
            with p_tab:
                assigned_rows = pickable_df.iloc[p_idx::num_pickers].copy()

                picker_view = assigned_rows[[
                    "Part ID", "Part Name", "Need to be Picked", "PSS Qty", "To be picked PSS count",
                    "PSS Type", "Priority", "Starvation_Mins", "Sequences_Covered"
                ]].reset_index(drop=True)
                picker_view.insert(0, "S No", picker_view.index + 1)

                st.dataframe(picker_view, use_container_width=True, hide_index=True)

                p_csv = picker_view.to_csv(index=False).encode("utf-8")
                st.download_button(
                    f"⬇️ Download Picker {p_idx+1} Sheet (CSV)",
                    data=p_csv,
                    file_name=f"Picker_{p_idx+1}_Sheet.csv",
                    mime="text/csv",
                    key=f"dl_p_{p_idx}"
                )

                st.markdown("---")
                st.subheader(f"🏷️ Printable 50 × 100 mm QR Stickers for Picker {p_idx+1}")
                st.write(f"Total container stickers for this picker: **{assigned_rows['To be picked PSS count'].sum()}** stickers.")

                st.markdown("""
                <button onclick="window.print()" style="
                    background-color: #00c853; color: white; padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 15px; font-weight: 800; margin-bottom: 12px;
                ">🖨️ Print Picker Labels Now (Ctrl + P)</button>
                """, unsafe_allow_html=True)

                all_stickers_html = """
                <style>
                @media print {
                    @page { size: 100mm 50mm; margin: 0; }
                    body { margin: 0; padding: 0; background: #fff; }
                    .thermal-sticker { page-break-after: always; }
                }
                </style>
                <div class="thermal-print-area">
                """

                for _, r in assigned_rows.iterrows():
                    total_pss = int(r["To be picked PSS count"])
                    for pss_idx in range(1, total_pss + 1):
                        all_stickers_html += render_50x100_thermal_sticker(
                            picklist_id=f"PL-{time.strftime('%Y%m%d')}-{p_idx+1}",
                            pss_num=pss_idx,
                            total_pss=total_pss,
                            part_id=r["Part ID"],
                            part_name=r["Part Name"],
                            pss_qty=int(r["PSS Qty"]),
                            pss_type=r["PSS Type"],
                            seq_no=r["Sequence_Order"],
                            starvation_mins=int(r["Starvation_Mins"]),
                            seq_coverage_str=r["Sequences_Covered"]
                        )
                all_stickers_html += "</div>"

                st.components.v1.html(all_stickers_html, height=500, scrolling=True)
    else:
        st.info("No pickable materials found with available stock.")

# TAB 3: TRUCK LOADER CHECKLIST (Purely Scan-Driven)
with tab_loader:
    st.subheader("🚚 Truck Loading & Dock Check Sheet (100% Scan-Driven)")
    st.caption("Status updates automatically when containers are scanned through the dock door.")

    loader_source = view_df[view_df["Transfer Status"].isin(["Ready for Full Transfer", "Partial Transfer Available"])]

    if not loader_source.empty:
        if "scanned_parts" not in st.session_state:
            st.session_state["scanned_parts"] = set()

        loader_rows = []
        fully_loaded_count = 0

        for _, r in loader_source.iterrows():
            part_id = r["Part ID"]
            total_containers = int(r["To be picked PSS count"])
            is_verified = part_id in st.session_state["scanned_parts"]
            
            if is_verified:
                status_text = f"🚚 LOADED ({total_containers}/{total_containers})"
                fully_loaded_count += 1
            else:
                status_text = "⏳ WAITING AT DOCK"

            loader_rows.append({
                "Part Number": part_id,
                "Description": r["Part Name"],
                "Picklist Qty": r["PSS Qty"],
                "PSS Type": r["PSS Type"],
                "Priority": r["Priority"],
                "Total Containers": total_containers,
                "Loading Status": status_text,
            })

        loader_df = pd.DataFrame(loader_rows)
        total_parts = len(loader_df)

        st.progress(fully_loaded_count / total_parts if total_parts > 0 else 0)
        st.write(f"**Loading Progress:** {fully_loaded_count} / {total_parts} materials confirmed loaded into the vehicle.")

        def highlight_loader(row):
            if "LOADED" in row["Loading Status"]:
                return ["background-color: #dcfce7; color: #166534; font-weight: bold;"] * len(row)
            return ["background-color: #f1f5f9; color: #475569;"] * len(row)

        st.dataframe(loader_df.style.apply(highlight_loader, axis=1), use_container_width=True, hide_index=True)
    else:
        st.info("No items marked for transfer under current filters.")

# TAB 4: DEO LIVE QR SCANNER & SAP TRANSFER GRID
with tab_deo_scan:
    st.subheader("🔍 DEO Dock-Door QR Scanner & SAP Transfer Desk")
    st.caption("Scan the 2D QR Code on the PSS container label using a USB scanner. Scanned parts automatically verify and build the SAP transfer table.")

    if "scanned_parts" not in st.session_state:
        st.session_state["scanned_parts"] = set()

    def process_qr_scan():
        raw_val = st.session_state.get("qr_scanner_input_box", "").strip()
        if raw_val:
            tokens = raw_val.split("#")
            part_id = tokens[0].strip()
            pss_qty = tokens[1].strip() if len(tokens) > 1 else "Standard PSS"
            container_idx = f"({tokens[3]})" if len(tokens) > 3 else ""

            st.session_state["scanned_parts"].add(part_id)
            st.toast(f"✅ Scanned: {part_id} | PSS Qty: {pss_qty} {container_idx}", icon="📦")
            st.session_state["qr_scanner_input_box"] = ""

    st.text_input(
        "⚡ Focus here with Barcode/QR Scanner (Scanner auto-presses Enter):",
        key="qr_scanner_input_box",
        on_change=process_qr_scan,
        placeholder="Scan PSS Container QR Code...",
    )

    ready_transfer = view_df[view_df["Transfer Status"].isin(["Ready for Full Transfer", "Partial Transfer Available"])].copy()

    if not ready_transfer.empty:
        ready_transfer["Verification_Status"] = ready_transfer["Part ID"].apply(
            lambda pid: "✅ CONFIRMED" if pid in st.session_state["scanned_parts"] else "⏳ PENDING SCAN"
        )
        
        verified_count = (ready_transfer["Verification_Status"] == "✅ CONFIRMED").sum()
        total_transfer_items = len(ready_transfer)
        
        kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
        kpi_col1.metric("📦 Total Containers to Verify", f"{total_transfer_items}")
        kpi_col2.metric("✅ Verified at Dock", f"{verified_count}")
        kpi_col3.metric("⏳ Remaining to Scan", f"{total_transfer_items - verified_count}")
        st.progress(verified_count / total_transfer_items if total_transfer_items > 0 else 0)

        st.markdown("---")

        view_mode = st.radio("Display in SAP Grid:", ["Verified Items Only", "All Transfer Items"], horizontal=True)

        if view_mode == "Verified Items Only":
            active_deo_df = ready_transfer[ready_transfer["Verification_Status"] == "✅ CONFIRMED"].copy()
        else:
            active_deo_df = ready_transfer.copy()

        if not active_deo_df.empty:
            deo_grid = active_deo_df[["Part ID", "PSS Qty", "Part Name"]].copy()
            deo_grid.columns = ["Part No", "Quantity", "Description"]

            st.markdown("### 📋 1-Click SAP Direct Clipboard Handshake")
            st.caption("Select cell 1 in SAP GUI (ME21N / MIGO / MB1B) and press Ctrl + V.")

            sap_tsv = "\n".join([f"{r['Part No']}\t{r['Quantity']}\t{r['Description']}" for _, r in deo_grid.iterrows()])
            st.text_area("SAP Clipboard Output (Part No \\t Quantity \\t Description):", sap_tsv, height=120)

            st.dataframe(deo_grid, use_container_width=True, hide_index=True)
        else:
            st.info("No items confirmed yet. Scan a container QR code above to build the SAP transfer table.")
    else:
        st.info("No materials available for transfer.")

# TAB 5: BAND 2 TRANSFER REQUIREMENTS
with tab_band2:
    st.subheader("Band 2: Component Transfer Requirements (W004 ➡️ M002)")
    transfer_table = view_df[[
        "Sequence_Order", "Part ID", "Part Name",
        "Required Qty", "M002 Stock", "COGI Qty", "W004 Stock", "Transfer Qty (W004 ➡️ M002)", "Transfer Status"
    ]].copy()
    transfer_table.rename(columns={
        "Sequence_Order": "Seq #",
        "Part ID": "SKU (Child Material)",
        "Part Name": "Description",
        "Required Qty": "Total Gross Demand",
        "M002 Stock": "M002 Stock (Plant)",
        "W004 Stock": "W004 Stock (RM01)"
    }, inplace=True)

    def highlight_status(row):
        if row["Transfer Status"] == "W004 Out of Stock":
            return ["background-color: #fee2e2; color: #991b1b; font-weight: bold;"] * len(row)
        elif row["Transfer Status"] == "Ready for Full Transfer":
            return ["background-color: #dcfce7; color: #166534; font-weight: bold;"] * len(row)
        return [""] * len(row)

    st.dataframe(transfer_table.style.apply(highlight_status, axis=1), use_container_width=True, hide_index=True)
    csv_band2 = transfer_table.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download Band 2 CSV", data=csv_band2, file_name="Band2_Transfer_Requirements.csv", mime="text/csv")

# TAB 6: SCALEX SEQUENCE SCHEDULE
with tab_sequence:
    st.subheader("📈 Raw ScaleX Production Sequence Schedule")
    display_raw = batches_df.copy()
    if hide_hold:
        display_raw = display_raw[display_raw["Status"] == "PLAN"]
    if line_filter != "All":
        display_raw = display_raw[display_raw["Line"].str.lower() == line_filter.lower()]

    st.dataframe(display_raw, use_container_width=True, hide_index=True)