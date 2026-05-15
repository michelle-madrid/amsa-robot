import pandas as pd, json

df = pd.read_parquet('data/Libro_Gestion.parquet')

with open('data/kpi_mappings.json', encoding='utf-8') as f:
    mappings = json.load(f)
with open('data/formula_params.json', encoding='utf-8') as f:
    fparams = json.load(f)

mlp_map = mappings.get('MLP', {})

# Filter MLP, March 2026
sub = df[(df['compania'] == 'MLP') & (df['periodo'] == 202603)]

def resolve_lk(lk, mapping, snap):
    """Resolve label-key (hoja||kpi or just kpi) to YTD value."""
    col_name = mapping.get(lk, lk)
    if '||' in col_name:
        hoja, kpi = col_name.split('||', 1)
        rows = snap[(snap['hoja'].str.strip() == hoja.strip()) & (snap['kpi'].str.strip() == kpi.strip())]
    else:
        rows = snap[snap['kpi'].str.strip() == col_name.strip()]
    if rows.empty:
        return None, col_name
    return rows[['tipo', 'ytd', 'hoja', 'kpi']].to_dict('records'), col_name

# Check dev_mina_vol
print("=== dev_mina_vol ===")
for lk in ['Fase 11||Esteril a Activar', 'Fase 12E||Esteril a Activar']:
    result, resolved = resolve_lk(lk, mlp_map, sub)
    print(f"lk={lk} -> {resolved}")
    if result:
        for r in result:
            print(f"  tipo={r['tipo']}, ytd={r['ytd']:.4f}")
    else:
        print("  NOT FOUND")

print()
print("=== dev_mina_cu ===")
lk = 'Fase 11||Costo Unitario'
result, resolved = resolve_lk(lk, mlp_map, sub)
print(f"lk={lk} -> {resolved}")
if result:
    for r in result:
        print(f"  tipo={r['tipo']}, ytd={r['ytd']:.4f}")
else:
    print("  NOT FOUND")

print()
print("=== CuFino ===")
cu_fino_lk = mlp_map.get('Variables Mineras||CuFino', '')
print(f"Variables Mineras||CuFino -> {cu_fino_lk}")
result, resolved = resolve_lk('Variables Mineras||CuFino', mlp_map, sub)
print(f"resolved={resolved}")
if result:
    for r in result:
        print(f"  tipo={r['tipo']}, ytd={r['ytd']:.4f}")
else:
    print("  NOT FOUND")

# Also check direct
print()
print("=== Direct search for 'Cu fino pagable filtrado' ===")
rows = sub[sub['kpi'].str.contains('Cu fino', na=False, case=False)]
for _, r in rows.iterrows():
    print(f"  hoja={r['hoja']}, kpi={r['kpi']}, tipo={r['tipo']}, ytd={r['ytd']:.4f}")
