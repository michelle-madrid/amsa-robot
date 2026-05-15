import pandas as pd, json

df = pd.read_parquet('data/Libro_Gestion.parquet')
sub = df[(df['compania'] == 'MLP') & (df['periodo'] == 202603)]

print("=== Hojas disponibles MLP ===")
print(sub['hoja'].unique().tolist())
print()

print("=== Filas con 'Fase' en hoja o kpi ===")
rows = sub[sub['hoja'].str.contains('Fase', na=False, case=False) | sub['kpi'].str.contains('Fase', na=False, case=False)]
print(rows[['hoja', 'kpi', 'tipo', 'ytd']].drop_duplicates(['hoja','kpi','tipo']).head(40).to_string())
print()

print("=== Filas con 'steril' o 'Activar' en kpi ===")
rows2 = sub[sub['kpi'].str.contains('steril|Activar', na=False, case=False)]
print(rows2[['hoja', 'kpi', 'tipo', 'ytd']].drop_duplicates(['hoja','kpi','tipo']).head(30).to_string())
print()

print("=== Filas con 'Costo Unitario' en kpi (hoja Mina) ===")
rows3 = sub[sub['kpi'].str.contains('Costo Unitario', na=False, case=False)]
print(rows3[['hoja', 'kpi', 'tipo', 'ytd']].drop_duplicates(['hoja','kpi','tipo']).head(20).to_string())
