import pandas as pd

df = pd.read_parquet('data/Libro_Gestion.parquet')
sub = df[(df['compania'] == 'MLP') & (df['periodo'] == 202603)]

# Full year and YTD values for Fase 11
for subcat, kpi_name in [('Fase 11', 'Esteril a Activar'), ('Fase 11', 'Costo Unitario'), ('Fase 12E', 'Esteril a Activar'), ('Fase 12E', 'Costo Unitario')]:
    rows = sub[(sub['subcategory'] == subcat) & (sub['kpi'] == kpi_name)]
    for _, r in rows.iterrows():
        print(f"subcategory={subcat!r}, kpi={kpi_name!r}, tipo={r['tipo']}: ytd={r['ytd']:.4f}, fy={r['fy']:.4f}, mes={r['mes']:.4f}")

print()
# Try computing budget CU from YTD activated cost / YTD volume
# budget activated cost = vol_b * cu_b
f11_vol_b = sub[(sub['subcategory']=='Fase 11')&(sub['kpi']=='Esteril a Activar')&(sub['tipo']=='Ppto')]['ytd'].values[0]
f11_cu_b = sub[(sub['subcategory']=='Fase 11')&(sub['kpi']=='Costo Unitario')&(sub['tipo']=='Ppto')]['ytd'].values[0]
f12e_vol_b = sub[(sub['subcategory']=='Fase 12E')&(sub['kpi']=='Esteril a Activar')&(sub['tipo']=='Ppto')]['ytd'].values[0]
f12e_cu_b = sub[(sub['subcategory']=='Fase 12E')&(sub['kpi']=='Costo Unitario')&(sub['tipo']=='Ppto')]['ytd'].values[0]

print(f"Fase 11: vol_b={f11_vol_b:.2f} kt, cu_b={f11_cu_b:.4f} US$/t")
print(f"Fase 12E: vol_b={f12e_vol_b:.2f} kt, cu_b={f12e_cu_b:.4f} US$/t")

total_vol_b = f11_vol_b + f12e_vol_b
total_cost_b = f11_vol_b * f11_cu_b + f12e_vol_b * f12e_cu_b
combined_cu_b = total_cost_b / total_vol_b if total_vol_b else 0
print(f"\nTotal vol_b = {total_vol_b:.2f} kt")
print(f"Total cost_b = {total_cost_b:.2f} kUS$")
print(f"Combined CU_b (ponderado) = {combined_cu_b:.4f} US$/t")

# Check FY
f11_vol_b_fy = sub[(sub['subcategory']=='Fase 11')&(sub['kpi']=='Esteril a Activar')&(sub['tipo']=='Ppto')]['fy'].values[0]
f11_cu_b_fy = sub[(sub['subcategory']=='Fase 11')&(sub['kpi']=='Costo Unitario')&(sub['tipo']=='Ppto')]['fy'].values[0]
print(f"\nFase 11 FY: vol_b={f11_vol_b_fy:.2f}, cu_b={f11_cu_b_fy:.4f}")
