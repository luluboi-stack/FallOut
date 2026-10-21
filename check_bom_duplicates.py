"""Read a local BOM and list repeated lines. Does not change the file or the picklist.

Usage:
    python check_bom_duplicates.py
    python check_bom_duplicates.py "ypp_bom_report.xlsx"
"""
import sys
from pathlib import Path
from typing import List, Optional

import pandas as pd

LINE_KEYS = ["Parent Material", "Child Material", "BOM Item Number", "Comp Qty."]
SHOW_COLUMNS = LINE_KEYS + ["Description", "Alt. BOM", "valid From", "Valid To", "Operation Number"]
LOCAL_NAMES = [
    "ypp_bom_report.xlsx",
    "ypp_bom_report.csv",
    "BOM_19TH_ALLSKU.parquet",
    "bom.csv",
    "BOM_FINAL_EXPLODED (15th Oct).parquet",
]


def read_bom(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str, low_memory=False)


def find_local_bom(folder: Path) -> Optional[Path]:
    for name in LOCAL_NAMES:
        candidate = folder / name
        if candidate.exists():
            return candidate
    return None


def repeated_lines(bom: pd.DataFrame) -> pd.DataFrame:
    keys = [column for column in LINE_KEYS if column in bom.columns]
    if "Parent Material" not in keys or "Child Material" not in keys:
        missing = [column for column in ("Parent Material", "Child Material") if column not in bom.columns]
        raise ValueError("BOM is missing " + ", ".join(missing))
    counts = bom.groupby(keys, dropna=False).size().reset_index(name="Copies")
    repeated = counts[counts["Copies"] > 1].sort_values("Copies", ascending=False)
    detail_columns = [column for column in SHOW_COLUMNS if column in bom.columns and column not in keys]
    if not detail_columns or repeated.empty:
        return repeated.reset_index(drop=True)
    details = bom[keys + detail_columns].drop_duplicates(keys)
    return repeated.merge(details, on=keys, how="left").reset_index(drop=True)


def report(path: Path) -> int:
    bom = read_bom(path)
    repeated = repeated_lines(bom)
    extra = int((repeated["Copies"] - 1).sum()) if not repeated.empty else 0
    print(f"File: {path}")
    print(f"Rows read: {len(bom)}")
    print(f"Repeated lines: {len(repeated)}")
    print(f"Extra copies: {extra}")
    if repeated.empty:
        print("No repeated BOM lines.")
        return 0
    print()
    print(repeated.to_string(index=False))
    out = path.with_name(path.stem + "_duplicates.csv")
    repeated.to_csv(out, index=False)
    print()
    print(f"Wrote {out}")
    return 0


def main(argv: List[str]) -> int:
    if len(argv) > 1:
        path = Path(argv[1])
    else:
        found = find_local_bom(Path.cwd())
        if found is None:
            print("Pass the BOM path. No local ypp_bom_report, BOM_19TH, or bom.csv was found.")
            return 1
        path = found
    if not path.exists():
        print(f"File not found: {path}")
        return 1
    return report(path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
