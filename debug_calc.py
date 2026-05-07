import sys, json, pathlib
sys.path.insert(0, 'backend')
from app.routes.calculos import _load_params, _apply_formula_var_mappings, _fetch_snapshot, _compute_deltas, MONTH_BY_NUM
from app.services import parquet_service as pq
from app.config import settings

p = _load_params()
p["kpi_keys"] = _apply_formula_var_mappings(p["kpi_keys"], "MLP")
k = p["kpi_keys"]

print("tcrc_q      =", repr(k.get("tcrc_q")))
print("tcrc_tarifa =", repr(k.get("tcrc_tarifa_u")))
print("comer_tarifa=", repr(k.get("comer_tarifa_u")))

df_all = pq.get_cached_df(settings.parquet_path)
df_co  = df_all[df_all["compania"] == "MLP"]

# Check Jan 2026
r_snap = _fetch_snapshot(df_co, "real", 2026, 1)
b_snap = _fetch_snapshot(df_co, "plan", 2026, 1)
field  = MONTH_BY_NUM[1]  # "enero"

result = _compute_deltas(r_snap, b_snap, field, p)
print("\n--- Efectos TC/RC (enero 2026) ---")
print(result["efectos_tcrc"])
print("--- Efectos Comer (enero 2026) ---")
print(result["efectos_comer"])

# Check which parquet keys exist for Cu and TC/RC
print("\n--- Parquet keys con 'cu' o 'tc' (real snap, enero 2026) ---")
for key in sorted(r_snap.keys()):
    if any(x in key.lower() for x in ['cu', 'tc/', 'comerci', 'pag.']):
        print(" ", key)
