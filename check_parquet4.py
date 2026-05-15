import pandas as pd

df = pd.read_parquet('data/Libro_Gestion.parquet')
sub = df[(df['compania'] == 'MLP') & (df['periodo'] == 202603)]

print("=== Columnas disponibles ===")
print(df.columns.tolist())
print()

print("=== Rows con subcategory/kpi para Esteril/Activar ===")
rows = sub[sub['kpi'].str.contains('steril|Activar', na=False, case=False)]
print(rows[['hoja', 'subcategory', 'kpi', 'tipo', 'ytd']].drop_duplicates().to_string())
print()

print("=== Rows con Costo Unitario ===")
rows2 = sub[sub['kpi'].str.contains('Costo Unitario', na=False, case=False)]
print(rows2[['hoja', 'subcategory', 'kpi', 'tipo', 'ytd']].drop_duplicates().to_string())
print()

print("=== subcategory con 'Fase 11' ===")
rows3 = sub[sub['subcategory'].str.contains('Fase 11', na=False, case=False)]
print(rows3[['hoja', 'subcategory', 'kpi', 'tipo', 'ytd']].drop_duplicates().head(20).to_string())
print()

print("=== subcategory con 'Fase 12E' ===")
rows4 = sub[sub['subcategory'].str.contains('Fase 12E', na=False, case=False)]
print(rows4[['hoja', 'subcategory', 'kpi', 'tipo', 'ytd']].drop_duplicates().head(20).to_string())
