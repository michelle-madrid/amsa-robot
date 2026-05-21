"""Verifica estado completo de kpi_keys ANT y ley_hidro/ley_cu"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from app.routes.calculos import _load_params, _apply_formula_var_mappings, _fetch_snapshot, _resolve
from app.services import parquet_service as pq
from app.config import settings

company = "ANT"
p = _load_params()
per_co = p.get("kpi_keys_per_company", {}).get(company, {})
if per_co:
    p["kpi_keys"] = {**p["kpi_keys"], **per_co}
p["kpi_keys"] = _apply_formula_var_mappings(p["kpi_keys"], company)
p["kpi_scales"] = p.get("kpi_scales_per_company", {}).get(company, {})

k = p["kpi_keys"]
scales = p["kpi_scales"]

print("=== kpi_keys ANT (post override) ===")
for key in ["ley_cu", "ley_hidro", "rec_hidro", "recuperacion", "tratamiento",
            "beneficio_hidro", "apilamiento", "cu_fino"]:
    v = k.get(key, "NOT FOUND")
    print(f"  {key}: {v!r}")

print(f"\n=== kpi_scales ANT ===")
for k2, v in scales.items():
    print(f"  {k2!r}: {v}")

# Construir r_snap y r_scaled
df_all = pq.get_cached_df(settings.parquet_path)
df_co  = df_all[df_all["compania"] == company]
r_snap = _fetch_snapshot(df_co, "real", 2026, 3)

def _scale_snap(snap, panel):
    if not scales:
        return snap
    result = dict(snap)
    for var, sc in scales.items():
        kpi_key = k.get(var, "")
        if not kpi_key or not isinstance(kpi_key, str):
            continue
        data = snap.get(kpi_key)
        if data is None:
            suffix = f"||{kpi_key}"
            matches = [(ck, cv) for ck, cv in snap.items() if ck.endswith(suffix)]
            if len(matches) == 1:
                kpi_key, data = matches[0]
        if data is not None:
            result[kpi_key] = {f: (v * sc if v is not None else None) for f, v in data.items()}
    return result

r_scaled = _scale_snap(r_snap, "mes")

print("\n=== Lookup de ley y rec en r_scaled ===")
for var in ["ley_cu", "ley_hidro", "rec_hidro", "recuperacion"]:
    key_val = k.get(var, "")
    if not key_val:
        print(f"  {var}: key='' -> None")
        continue
    val = _resolve(r_scaled, key_val, "mes")
    print(f"  {var}: key={key_val!r} -> {val}")

# Verificar que ley KPI existe en snapshot
ley_key = k.get("ley_cu", "")
print(f"\n=== Estado de '{ley_key}' en snapshots ===")
if ley_key:
    raw_r = r_snap.get(ley_key, {})
    scaled_r = r_scaled.get(ley_key, {})
    print(f"  r_snap[ley_cu]: {raw_r}")
    print(f"  r_scaled[ley_cu]: {scaled_r}")
