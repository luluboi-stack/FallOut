import pandas as pd
df = pd.read_parquet("BOM_19TH_ALLSKU.parquet")
df = df.drop_duplicates([
    "Parent Material", "Child Material", "BOM Item Number", "Comp Qty.",
    "Alt. BOM", "valid From", "Valid To",
])
df.to_parquet("BOM_19TH_DEDUPED.parquet", index=False)