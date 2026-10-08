import base64
from io import BytesIO
import json
import os
import time
import pandas as pd
from nicegui import app, run, ui

from core.scalex_scraper import scrape_scalex, is_online
from core.diff_engine import detect_plan_state_diff
from core.replenishment_engine import run_replenishment_math

BOM_PATH = os.path.join(os.path.dirname(__file__), "bom.csv")
BATCHES_CSV = os.path.join(os.path.dirname(__file__), "data", "plan_batches.csv")

state = {
    "batches": [],
    "confidence_score": 100.0,
    "mb52_stock": {},
    "cogi_errors": {},
    "scanned_tokens": set(),
    "truck_loaded": {},
    "scanner_buffer": "",
    "last_key_time": time.time(),
}

if os.path.exists(BATCHES_CSV):
    try:
        df_c = pd.read_csv(BATCHES_CSV)
        state["batches"] = df_c.to_dict(orient="records")
    except Exception:
        pass

def generate_qr_b64(data_text: str) -> str:
    import qrcode
    qr = qrcode.QRCode(version=1, box_size=4, border=1)
    qr.add_data(data_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    b = BytesIO()
    img.save(b, format="PNG")
    return base64.b64encode(b.getvalue()).decode("utf-8")

@ui.page("/")
def index():
    ui.add_head_html("""
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
        <style>
            body { font-family: 'Inter', sans-serif; background-color: #0b0f19; color: #f1f5f9; }
            .ather-card { background: #131b2e; border: 1px solid #1e293b; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
            .ather-btn { background: #00e676; color: #000; font-weight: 800; border-radius: 8px; transition: all 0.2s; }
            .ather-btn:hover { background: #00c853; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,230,118,0.3); }
            .q-table__container { background: #131b2e !important; color: #f1f5f9 !important; border: 1px solid #1e293b !important; border-radius: 10px; }
            .q-table th { font-weight: 800; color: #94a3b8 !important; background: #0f172a !important; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
            .q-table td { font-size: 13px; border-bottom: 1px solid #1e293b !important; }
            .q-table tbody tr:hover { background: #1e293b !important; }
            @media print {
                @page { size: 100mm 50mm; margin: 0; }
                body * { visibility: hidden; }
                .print-area, .print-area * { visibility: visible; }
                .print-area { position: absolute; left: 0; top: 0; }
            }
        </style>
    """)

    def handle_key(e):
        if not getattr(e.action, "keydown", False):
            return
        k = str(e.key) if e.key is not None else ""
        now = time.time()
        if now - state["last_key_time"] > 0.08:
            state["scanner_buffer"] = ""
        state["last_key_time"] = now

        if k in ("Enter", "Return"):
            buf = state["scanner_buffer"].strip()
            if len(buf) > 3:
                tokens = buf.split("#")
                part_id = tokens[0]
                state["scanned_tokens"].add(part_id)
                ui.notify(f"⚡ QR VERIFIED: {part_id}", type="positive", color="green")
                refresh_tables()
            state["scanner_buffer"] = ""
        elif len(k) == 1:
            state["scanner_buffer"] += k

    ui.keyboard(on_key=handle_key)

    # NAVBAR
    with ui.row().classes("w-full items-center justify-between ather-card p-4 mb-4 border-l-4 border-l-[#00e676]"):
        with ui.row().classes("items-center gap-4"):
            ui.label("⚡ ATHER").classes("text-2xl font-black text-[#00e676] tracking-wider")
            ui.label("|").classes("text-slate-600 text-xl font-bold")
            ui.label("SEQUENCE REPLENISHMENT & PSS TERMINAL").classes("text-md font-bold text-slate-200 tracking-wide")
            
            online = is_online()
            status_chip = ui.chip("ScaleX Online" if online else "ScaleX Offline",
                                  icon="wifi" if online else "wifi_off",
                                  color="green" if online else "red")
        
        with ui.row().classes("items-center gap-3"):
            conf_badge = ui.badge(f"Parser Confidence: {state['confidence_score']}%", color="emerald").classes("text-xs font-bold px-3 py-1.5 rounded-lg border border-slate-700")

    alert_container = ui.column().classes("w-full")

    # CONTROL CARD
    with ui.card().classes("w-full ather-card p-4 mb-4"):
        with ui.row().classes("w-full items-center justify-between gap-4"):
            async def sync_scalex():
                sync_btn.text = "Syncing..."
                status_chip.text = "Syncing ScaleX..."
                status_chip.color = "amber"
                
                records, conf, msg = await run.io_bound(scrape_scalex)
                if records:
                    state["batches"] = records
                    state["confidence_score"] = conf
                    conf_badge.text = f"Parser Confidence: {conf}%"
                    status_chip.text = "ScaleX Online"
                    status_chip.color = "green"

                    hold_to_plan, _ = detect_plan_state_diff(records)
                    alert_container.clear()
                    with alert_container:
                        if hold_to_plan:
                            ui.notification("🚨 BATCHES RELEASED FROM HOLD TO PLAN!", type="warning", timeout=8)
                            for b in hold_to_plan:
                                ui.label(f"⚡ URGENT PICK: {b['Line']} | {b['SKUName']} | Serials: {b['Serial_Start']}..{b['Serial_End']} ({b['Count']} units)").classes("bg-amber-500/20 text-amber-400 border border-amber-500/30 p-3 font-bold w-full rounded-lg mb-2")
                    
                    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
                    pd.DataFrame(records).to_csv(BATCHES_CSV, index=False)
                    ui.notify(f"Synced {len(records)} batches successfully!", type="positive", color="green")
                    refresh_tables()
                else:
                    status_chip.text = "ScaleX Offline"
                    status_chip.color = "red"
                    ui.notify(f"ScaleX Sync Error: {msg}", type="negative", color="red")
                sync_btn.text = "🔄 Sync Live ScaleX"

            sync_btn = ui.button("🔄 Sync Live ScaleX", on_click=sync_scalex).classes("ather-btn px-6 py-2 text-sm")

            with ui.row().classes("items-center gap-3"):
                def on_mb52_upload(e):
                    from io import BytesIO
                    df = pd.read_excel(BytesIO(e.content.read())) if e.name.endswith(('.xlsx', '.xls')) else pd.read_csv(BytesIO(e.content.read()))
                    state["mb52_stock"] = {}
                    for _, r in df.iterrows():
                        mat = str(r.get("Material", "")).strip()
                        plant = str(r.get("Plant", "")).strip().upper()
                        unrest = float(str(r.get("Unrestricted", 0)).replace(",", ""))
                        if mat not in state["mb52_stock"]: state["mb52_stock"][mat] = {"m002": 0.0, "w004": 0.0}
                        if plant == "M002": state["mb52_stock"][mat]["m002"] += unrest
                        elif plant == "W004": state["mb52_stock"][mat]["w004"] += unrest
                    ui.notify("MB52 Stock loaded!", type="positive", color="green")
                    refresh_tables()

                ui.upload(on_upload=on_mb52_upload, label="Upload MB52").classes("max-w-xs bg-slate-800 rounded-lg")

                def on_cogi_upload(e):
                    from io import BytesIO
                    df = pd.read_excel(BytesIO(e.content.read())) if e.name.endswith(('.xlsx', '.xls')) else pd.read_csv(BytesIO(e.content.read()))
                    state["cogi_errors"] = {}
                    for _, r in df.iterrows():
                        mat = str(r.get("Material", "")).strip()
                        qty_key = next((k for k in r.keys() if "qty" in str(k).lower() or "quantity" in str(k).lower()), None)
                        qty = float(str(r.get(qty_key, 0)).replace(",", "")) if qty_key else 0.0
                        state["cogi_errors"][mat] = state["cogi_errors"].get(mat, 0.0) + qty
                    ui.notify("COGI loaded!", type="positive", color="green")
                    refresh_tables()

                ui.upload(on_upload=on_cogi_upload, label="Upload COGI").classes("max-w-xs bg-slate-800 rounded-lg")

    # FILTERS BAR
    with ui.card().classes("w-full ather-card p-4 mb-4"):
        with ui.row().classes("w-full items-center gap-4"):
            filter_line = ui.select(["All", "Line_1", "Line_2"], value="All", label="Assembly Line").classes("w-40 bg-slate-800 rounded-lg")
            filter_status = ui.select([
                "All Statuses", "Ready for Full Transfer", "Partial Transfer Available", "W004 Out of Stock", "Sufficient M002 Stock"
            ], value="All Statuses", label="SOP Transfer Status").classes("w-64 bg-slate-800 rounded-lg")
            filter_pss = ui.select(["All", "Pallet", "Trolley", "FLC"], value="All", label="Container (PSS)").classes("w-40 bg-slate-800 rounded-lg")
            hide_hold_cb = ui.checkbox("Hide HOLD Batches", value=True).classes("text-slate-300 font-bold")
            pickers_spin = ui.number("Active Pickers:", value=2, min=1, max=10, step=1).classes("w-32 bg-slate-800 rounded-lg")

    # SOP 4-METRIC CARDS
    cards_row = ui.row().classes("w-full gap-4 mb-4")

    # TABS
    with ui.tabs().classes("w-full ather-card p-1 text-slate-300") as tabs:
        t_master = ui.tab("1. Master Picklist (PSS)", icon="inventory")
        t_pickers = ui.tab("2. Picker Stations & 50x100 QR Labels", icon="badge")
        t_loader = ui.tab("3. Truck Loading Dock Checklist", icon="local_shipping")
        t_deo = ui.tab("4. DEO SAP Direct Transfer Grid", icon="content_copy")
        t_mes = ui.tab("5. ScaleX Plant Floor Sequence", icon="format_list_numbered")

    with ui.tab_panels(tabs, value=t_master).classes("w-full bg-transparent"):
        with ui.tab_panel(t_master):
            p_table = ui.table(columns=[
                {"name": "seq", "label": "Seq #", "field": "Sequence_Order", "sortable": True},
                {"name": "part", "label": "Part ID", "field": "Part ID", "sortable": True},
                {"name": "desc", "label": "Description", "field": "Part Name", "sortable": True},
                {"name": "req", "label": "Demand", "field": "Required Qty"},
                {"name": "need", "label": "Need Pick (PSS Round)", "field": "Need to be Picked"},
                {"name": "box", "label": "Pack Size", "field": "PSS Qty"},
                {"name": "cnt", "label": "Containers", "field": "To be picked PSS count"},
                {"name": "type", "label": "Container", "field": "PSS Type"},
                {"name": "w004", "label": "W004 Stock", "field": "W004 Stock"},
                {"name": "status", "label": "SOP Status", "field": "Transfer Status"},
            ], rows=[]).classes("w-full")

        with ui.tab_panel(t_pickers):
            pickers_container = ui.column().classes("w-full")

        with ui.tab_panel(t_loader):
            ui.label("🚚 Vehicle Loading & Dock-Door Confirmation").classes("font-black text-lg text-slate-200 mb-2")
            loader_container = ui.column().classes("w-full")

        with ui.tab_panel(t_deo):
            with ui.card().classes("w-full ather-card p-6 border border-blue-500/30 mb-4"):
                ui.label("📋 DEO SAP Material Transfer Desk").classes("text-xl font-black text-blue-400")
                ui.label("Simplified strictly to Part No, Quantity, and Description for immediate transfer into SAP GUI.").classes("text-xs text-slate-400 mb-4")

                sap_output_area = ui.textarea(label="1-Click SAP Clipboard Preview (TSV):").classes("w-full font-mono bg-slate-900 border border-slate-700 rounded-lg text-emerald-400 mb-4")

                def copy_sap_clipboard():
                    ui.run_javascript(f'navigator.clipboard.writeText({json.dumps(sap_output_area.value)})')
                    ui.notify("✅ Copied to Clipboard! Ready to paste into SAP (Ctrl + V)", type="positive", color="green")

                ui.button("📋 Copy for SAP Transfer (Ctrl + V)", on_click=copy_sap_clipboard).classes("ather-btn px-8 py-3 text-md font-black")

            deo_table = ui.table(columns=[
                {"name": "part", "label": "Part No", "field": "Part No"},
                {"name": "qty", "label": "Quantity", "field": "Quantity"},
                {"name": "desc", "label": "Description", "field": "Description"},
            ], rows=[]).classes("w-full")

        with ui.tab_panel(t_mes):
            ui.label("📈 Official ScaleX MES Production Ledger").classes("font-black text-lg text-slate-200 mb-2")
            mes_table = ui.table(columns=[
                {"name": "seq", "label": "Seq #", "field": "ScaleX_Sequence_No"},
                {"name": "sku", "label": "Vehicle SKU", "field": "SKUName"},
                {"name": "line", "label": "Line", "field": "Line"},
                {"name": "status", "label": "Status", "field": "Status"},
                {"name": "start", "label": "Serial Start", "field": "Serial_Start"},
                {"name": "end", "label": "Serial End", "field": "Serial_End"},
                {"name": "cnt", "label": "Count", "field": "Count"},
            ], rows=[]).classes("w-full")

    def refresh_tables():
        for idx, b in enumerate(state["batches"], 1):
            b["ScaleX_Sequence_No"] = idx

        items = run_replenishment_math(
            state["batches"], BOM_PATH, state["mb52_stock"], state["cogi_errors"],
            selected_line=filter_line.value, hide_hold=hide_hold_cb.value
        )

        filtered = [
            r for r in items
            if (filter_status.value == "All Statuses" or r["Transfer Status"] == filter_status.value) and
               (filter_pss.value == "All" or r["PSS Type"] == filter_pss.value)
        ]

        cards_row.clear()
        with cards_row:
            s_m002 = sum(1 for r in items if r["Transfer Status"] == "Sufficient M002 Stock")
            r_full = sum(1 for r in items if r["Transfer Status"] == "Ready for Full Transfer")
            p_avail = sum(1 for r in items if r["Transfer Status"] == "Partial Transfer Available")
            w_zero = sum(1 for r in items if r["Transfer Status"] == "W004 Out of Stock")

            with ui.card().classes("ather-card p-4 flex-1 border-t-4 border-t-emerald-400"):
                ui.label("SUFFICIENT M002").classes("text-xs font-bold text-slate-400")
                ui.label(f"{s_m002} SKUs").classes("text-2xl font-black text-emerald-400")

            with ui.card().classes("ather-card p-4 flex-1 border-t-4 border-t-blue-400"):
                ui.label("READY FULL TRANSFER").classes("text-xs font-bold text-slate-400")
                ui.label(f"{r_full} SKUs").classes("text-2xl font-black text-blue-400")

            with ui.card().classes("ather-card p-4 flex-1 border-t-4 border-t-amber-400"):
                ui.label("PARTIAL AVAILABLE").classes("text-xs font-bold text-slate-400")
                ui.label(f"{p_avail} SKUs").classes("text-2xl font-black text-amber-400")

            with ui.card().classes("ather-card p-4 flex-1 border-t-4 border-t-rose-500"):
                ui.label("W004 SHORTAGE (0 STOCK)").classes("text-xs font-bold text-slate-400")
                ui.label(f"{w_zero} SKUs").classes("text-2xl font-black text-rose-500")

        p_table.rows = filtered

        pickers_container.clear()
        with pickers_container:
            n_p = int(pickers_spin.value)
            pickable = [r for r in filtered if not r["Is_Zero_Stock"]]

            with ui.tabs().classes("w-full text-slate-300") as p_subtabs:
                sub_tabs = [ui.tab(f"🧑‍🏭 Picker {i+1}") for i in range(n_p)]
            with ui.tab_panels(p_subtabs, value=sub_tabs[0]).classes("w-full bg-transparent"):
                for p_i in range(n_p):
                    with ui.tab_panel(sub_tabs[p_i]):
                        p_items = pickable[p_i::n_p]
                        ui.table(columns=[
                            {"name": "part", "label": "Part ID", "field": "Part ID"},
                            {"name": "desc", "label": "Part Name", "field": "Part Name"},
                            {"name": "need", "label": "Need Pick Qty", "field": "Need to be Picked"},
                            {"name": "box", "label": "Box Size", "field": "PSS Qty"},
                            {"name": "cnt", "label": "Containers", "field": "To be picked PSS count"},
                            {"name": "type", "label": "PSS Type", "field": "PSS Type"},
                        ], rows=p_items).classes("w-full mb-4")

                        ui.button(f"🖨️ Print 50x100mm Thermal Stickers (Picker {p_i+1})", 
                                  on_click=lambda: ui.run_javascript("window.print()")).classes("ather-btn px-6 py-2.5 font-bold mb-4")

                        with ui.row().classes("print-area hidden"):
                            for r in p_items:
                                for pss_idx in range(1, r["To be picked PSS count"] + 1):
                                    qr_str = f"{r['Part ID']}#{r['PSS Qty']}#PL-{p_i+1}#{pss_idx}"
                                    img_b64 = generate_qr_b64(qr_str)
                                    ui.html(f"""
                                        <div style="width:100mm; height:50mm; border:2px solid #000; padding:3mm; box-sizing:border-box; page-break-after:always; display:flex; justify-content:space-between; font-family:Arial,sans-serif; background:#fff; color:#000;">
                                            <div>
                                                <div style="font-weight:bold; font-size:12px;">ATHER ENERGY - {r['PSS Type']} ({pss_idx}/{r['To be picked PSS count']})</div>
                                                <div style="font-weight:900; font-size:15px; margin-top:3px;">{r['Part ID']}</div>
                                                <div style="font-size:10px; height:20px; overflow:hidden;">{r['Part Name'][:35]}</div>
                                                <div style="font-size:16px; font-weight:900; margin-top:5px;">QTY: {r['PSS Qty']}</div>
                                            </div>
                                            <img src="data:image/png;base64,{img_b64}" style="width:30mm; height:30mm;" />
                                        </div>
                                    """)

        loader_container.clear()
        with loader_container:
            for r in filtered:
                if r["Transfer Status"] in ("Ready for Full Transfer", "Partial Transfer Available"):
                    with ui.row().classes("w-full items-center justify-between ather-card p-3 mb-2 border border-slate-800"):
                        with ui.row().classes("items-center gap-3"):
                            ui.checkbox("", value=state["truck_loaded"].get(r["Part ID"], False),
                                        on_change=lambda e, pid=r["Part ID"]: state["truck_loaded"].update({pid: e.value}))
                            ui.label(f"{r['Part ID']} - {r['Part Name']}").classes("font-bold text-slate-200")
                        ui.badge(f"{r['Need to be Picked']} units ({r['PSS Type']})", color="blue").classes("font-bold px-3 py-1")

        sap_rows = [
            {"Part No": r["Part ID"], "Quantity": r["Need to be Picked"], "Description": r["Part Name"]}
            for r in filtered if not r["Is_Zero_Stock"]
        ]
        deo_table.rows = sap_rows
        sap_output_area.value = "\n".join([f"{r['Part No']}\t{r['Quantity']}\t{r['Description']}" for r in sap_rows])

        mes_table.rows = state["batches"]

    filter_line.on_value_change(refresh_tables)
    filter_status.on_value_change(refresh_tables)
    filter_pss.on_value_change(refresh_tables)
    hide_hold_cb.on_value_change(refresh_tables)
    pickers_spin.on_value_change(refresh_tables)

    refresh_tables()

ui.run(title="Ather Industrial Replenishment Suite", port=8080, reload=False)
