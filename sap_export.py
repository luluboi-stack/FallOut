import os
import time
import subprocess
import pyautogui
import pyperclip
import pandas as pd
import io
import sys

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
USER_PROFILE = os.environ.get("USERPROFILE")
TARGET_DUMP_DIR = os.path.join(USER_PROFILE, "Desktop", "SAP Dumps", "LX02")
os.makedirs(TARGET_DUMP_DIR, exist_ok=True)

POSSIBLE_SAP_PATHS = [
    r"C:\Program Files\SAP\FrontEnd\SAPgui\saplogon.exe",       
    r"C:\Program Files (x86)\SAP\FrontEnd\SAPgui\saplogon.exe", 
]

SAP_LOGON_PATH = None
for path in POSSIBLE_SAP_PATHS:
    if os.path.exists(path):
        SAP_LOGON_PATH = path
        break

if not SAP_LOGON_PATH:
    print("❌ ERROR: Could not find SAP Logon.")
    sys.exit(1)

SAP_SYSTEM_NAME = "ather prd system" 
SAP_USER = "AE3021"
SAP_PASS = "Ather@2026@"
WH_NUMBER = "WH5"
PLANT_CODE = "W004"

def wait(seconds):
    time.sleep(seconds)

def run_clipboard_automation():
    print(f"🚀 Launching SAP GUI...")
    print("⚠️  CRITICAL: HANDS OFF THE MOUSE AND KEYBOARD! ⚠️")
    
    # Empty the clipboard
    pyperclip.copy("") 
    
    # 0. MINIMIZE VISUAL STUDIO CODE (Show Desktop)
    print("🔽 Minimizing background apps...")
    pyautogui.hotkey('win', 'd')
    wait(1.5)

    # 1. Open SAP Logon
    subprocess.Popen(SAP_LOGON_PATH)
    wait(8) # Wait for Logon Pad to open. Since desktop is clear, it WILL be in front.

    # 2. Select System & Hit Enter
    print(f"🖥️ Connecting to '{SAP_SYSTEM_NAME}'...")
    pyautogui.write(SAP_SYSTEM_NAME, interval=0.05)
    wait(1)
    pyautogui.press('enter')
    wait(7) # Wait for login screen

    # 3. Login
    print("🔐 Logging in...")
    pyautogui.write(SAP_USER)
    pyautogui.press('tab')
    pyautogui.write(SAP_PASS)
    pyautogui.press('enter')
    wait(4)

    # Dismiss any login popups (Multiple logins, etc)
    pyautogui.press('enter')
    wait(2)

    # 4. Navigate to LX02
    print("📂 Opening LX02...")
    pyautogui.hotkey('ctrl', '/') 
    wait(0.5)
    pyautogui.write('/nLX02')
    pyautogui.press('enter')
    wait(3)

    # 5. Fill out LX02 Fields
    print("⌨️ Filling parameters...")
    pyautogui.write(WH_NUMBER)
    
    for _ in range(7):
        pyautogui.press('tab')
        wait(0.1)
        
    pyautogui.write(PLANT_CODE)
    wait(0.5)

    # 6. Execute (F8)
    pyautogui.press('f8')
    print("⏳ Waiting for report to load...")
    wait(10) # 10 seconds to ensure the massive table fully loads

    # 7. Select All and Copy directly to Clipboard
    print("📋 Copying data to clipboard... (bypassing save menus)")
    pyautogui.hotkey('ctrl', 'a') # Select All
    wait(1.5)
    pyautogui.hotkey('ctrl', 'c') # Copy
    
    # 8. Smart Wait for Clipboard
    print("⏳ Waiting for SAP to finish copying...")
    clipboard_data = ""
    for _ in range(15): 
        clipboard_data = pyperclip.paste()
        if len(clipboard_data) > 500: # If there's a lot of text, we got it!
            break
        wait(1)

    # 9. Exit SAP cleanly
    print("🚪 Exiting SAP...")
    pyautogui.hotkey('ctrl', '/')
    wait(0.5)
    pyautogui.write('/nEX')
    pyautogui.press('enter')

    if len(clipboard_data) < 500:
        print("❌ ERROR: Failed to copy data from SAP. The table might be empty or SAP lagged.")
        return None
        
    return clipboard_data

def process_clipboard_data(raw_data):
    print("📊 Processing Data from Clipboard...")
    
    try:
        df = pd.read_csv(io.StringIO(raw_data), sep="\t", on_bad_lines="skip", dtype=str)
    except Exception as e:
        print(f"❌ Failed to parse data: {e}")
        return

    # Clean the pipe borders SAP adds
    df.columns = [str(c).strip().replace("|", "") for c in df.columns]
    first_col = df.columns[0]
    df = df[~df[first_col].astype(str).str.contains(r"^-+$", na=False)]
    
    # Save Outputs directly to your SAP Dumps folder
    dest_tsv = os.path.join(TARGET_DUMP_DIR, "LX02_X.tsv")
    dest_parquet = os.path.join(TARGET_DUMP_DIR, "LX02_X.parquet")
    
    df.to_csv(dest_tsv, sep="\t", index=False)
    df.to_parquet(dest_parquet, index=False)
    
    print(f"🎉 SUCCESS! Exported {len(df)} rows instantly.")
    print(f"   👉 {dest_tsv}")
    print(f"   👉 {dest_parquet}")

if __name__ == "__main__":
    copied_data = run_clipboard_automation()
    if copied_data:
        process_clipboard_data(copied_data)