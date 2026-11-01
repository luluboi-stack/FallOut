import pandas as pd

# Load the Excel file instead of a CSV
df = pd.read_csv("BOM_30THOCT_UPDATED.csv")

# Export to Parquet
df.to_parquet("BOM_30THOCT_UPDATED.parquet", index=False)
