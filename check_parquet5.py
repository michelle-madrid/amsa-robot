import pandas as pd, json

df = pd.read_parquet('data/Libro_Gestion.parquet')
sub = df[(df['compania'] == 'MLP') & (df['periodo'] == 202603)]

with open('data/kpi_mappings.json', encoding='utf-8') as f:
    mappings = json.load(f)

mlp_map = mappings.get('MLP', {})

# Show what MLP has for dev_mina keys
print("=== kpi_mappings MLP para dev_mina ===")
for k, v in mlp_map.items():
    if 'Fase' in k or 'IFRIC' in k or 'ifric' in k.lower() or 'Activar' in k or 'Esteril' in k:
        print(f"  {k!r}: {v!r}")
print()

# Build snapshot like _fetch_snapshot does (subcategory||kpi)
print("=== Snapshot keys con subcategory||kpi para IFRIC20 ===")
ifric = sub[sub['hoja'] == 'IFRIC20']
for _, row in ifric.iterrows():
    subcat = str(row.get('subcategory') or '').strip()
    kpi = str(row.get('kpi') or '').strip()
    key = f"{subcat}||{kpi}" if subcat else kpi
    print(f"  tipo={row['tipo']}, key={key!r}, ytd={row['ytd']:.4f}")

print()
# Exact values needed
print("=== Valores exactos para Desarrollo Mina ===")
for subcat, kpi_name in [('Fase 11', 'Esteril a Activar'), ('Fase 12E', 'Esteril a Activar'), ('Fase 11', 'Costo Unitario')]:
    rows = sub[(sub['subcategory'] == subcat) & (sub['kpi'] == kpi_name)]
    print(f"subcategory={subcat!r}, kpi={kpi_name!r}:")
    for _, r in rows.iterrows():
        print(f"  tipo={r['tipo']}, ytd={r['ytd']:.6f}")

print()
# Compute effects
f11_ea_r = sub[(sub['subcategory']=='Fase 11')&(sub['kpi']=='Esteril a Activar')&(sub['tipo']=='Real')]['ytd'].values[0]
f11_ea_b = sub[(sub['subcategory']=='Fase 11')&(sub['kpi']=='Esteril a Activar')&(sub['tipo']=='Ppto')]['ytd'].values[0]
f12e_ea_r = sub[(sub['subcategory']=='Fase 12E')&(sub['kpi']=='Esteril a Activar')&(sub['tipo']=='Real')]['ytd'].values[0]
f12e_ea_b = sub[(sub['subcategory']=='Fase 12E')&(sub['kpi']=='Esteril a Activar')&(sub['tipo']=='Ppto')]['ytd'].values[0]
cu_r = sub[(sub['subcategory']=='Fase 11')&(sub['kpi']=='Costo Unitario')&(sub['tipo']=='Real')]['ytd'].values[0]
cu_b = sub[(sub['subcategory']=='Fase 11')&(sub['kpi']=='Costo Unitario')&(sub['tipo']=='Ppto')]['ytd'].values[0]

vol_r = f11_ea_r + f12e_ea_r
vol_b = f11_ea_b + f12e_ea_b
cu_fino_r = 66.2971  # from parquet
cu_fino_b = 68.5890

print(f"vol_r = {vol_r:.3f} kt, vol_b = {vol_b:.3f} kt")
print(f"cu_r = {cu_r:.6f} US$/t, cu_b = {cu_b:.6f} US$/t")
print(f"cu_fino_r = {cu_fino_r} kt, cu_fino_b = {cu_fino_b} kt")
print()

denom_formula = 69.557  # formula-derived (old)
denom_cufino = cu_fino_r  # actual (new)

ton_kus = (vol_b - vol_r) * cu_b
cu_kus = (cu_b - cu_r) * vol_r
print(f"ton_kus = {ton_kus:.2f} kUS$")
print(f"cu_kus = {cu_kus:.2f} kUS$")

for label, denom in [("fórmula (viejo)", denom_formula), ("CuFino real (nuevo)", denom_cufino)]:
    d = denom * 2204.62
    ton_clb = ton_kus * 100 / d
    cu_clb = cu_kus * 100 / d
    print(f"\n[{label}] denominador={denom:.3f} ktCuf")
    print(f"  Ton = {ton_clb:.2f} c/lb")
    print(f"  CU  = {cu_clb:.2f} c/lb")
    print(f"  Total = {(ton_clb+cu_clb):.2f} c/lb")

# What cu_b would give Excel's results?
print("\n=== Inverso: ¿qué cu_b necesita Excel? ===")
target_ton = 6.1
target_cu  = -1.3
target_total = 4.8
d = denom_cufino * 2204.62
ton_kus_target = target_ton * d / 100
cu_kus_target  = target_cu  * d / 100
cu_b_from_ton = ton_kus_target / (vol_b - vol_r)
cu_b_from_cu  = cu_r - cu_kus_target / vol_r  # since cu_kus = (cu_b - cu_r)*vol_r
print(f"  Cu_b necesario (desde Ton): {cu_b_from_ton:.4f} US$/t")
print(f"  Cu_b necesario (desde CU):  {cu_b_from_cu:.4f} US$/t")
print(f"  Parquet cu_b = {cu_b:.4f} US$/t")
