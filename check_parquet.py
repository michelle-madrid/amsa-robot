import pandas as pd, json

df = pd.read_parquet('data/Libro_Gestion.parquet')

# Load mappings
with open('data/kpi_mappings.json', encoding='utf-8') as f:
    mappings = json.load(f)

mlp_map = mappings.get('MLP', {})

# Filter MLP, March 2026
mask = (df['compania'] == 'MLP') & (df['periodo'] == 202603)
sub = df[mask]

# Check kpi_mappings keys for dev_mina
with open('data/formula_params.json', encoding='utf-8') as f:
    fparams = json.load(f)
k = fparams['kpi_keys']
print("dev_mina_vol keys:", k.get('dev_mina_vol'))
print("dev_mina_cu key:", k.get('dev_mina_cu'))
print("cu_fino key:", k.get('cu_fino'))
print()

# Resolve labels via mappings
def get_label(lk, mapping):
    if '||' in lk:
        parts = lk.split('||', 1)
        hoja, kpi = parts[0].strip(), parts[1].strip()
        return kpi
    return lk

# Look at specific KPIs
for lk_key in ['dev_mina_vol', 'dev_mina_cu', 'cu_fino']:
    val = k.get(lk_key, '')
    keys = val if isinstance(val, list) else [val]
    for lk in keys:
        if not lk:
            continue
        # Resolve via MLP mappings
        col_name = mlp_map.get(lk, lk)
        print(f"Key={lk_key}, lk={lk}, mapped_col={col_name}")
        # Try to find in parquet
        rows = sub[sub['kpi'] == col_name]
        if rows.empty:
            # Try partial match
            rows = sub[sub['kpi'].str.contains(col_name[:15], na=False, case=False)]
        for _, row in rows.iterrows():
            print(f"  tipo={row['tipo']}, ytd={row['ytd']}, hoja={row['hoja']}")
        if rows.empty:
            print(f"  NOT FOUND in parquet for col_name={col_name}")
