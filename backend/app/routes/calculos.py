"""
Delta calculations: Gasto (Precio), Rendimiento, Actividad, Efecto Ley,
Desarrollo Mina, Variación Inventario.

KPI keys and new-section KPI lists are read from data/formula_params.json
so the user can change them from the front-end without touching code.
"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..services import parquet_service as pq
from ..services.mapping_store import load_mappings

router = APIRouter(prefix="/api/calculos", tags=["calculos"])

# Correspondencia fija: clave de variable de fórmula → lk en kpi_mappings
# Permite derivar automáticamente los valores desde los mapeos Excel ya configurados.
_FORMULA_VAR_TO_LK: dict[str, str | list] = {
    "tarifa_bolas":        "Tarifas||Bolas",
    "tarifa_energia":      "Tarifas||Energía",
    "tarifa_combustible":  "Tarifas||Combustible",
    "tarifa_explosivos":   "Tarifas||Explosivos",
    "consumo_bolas":       "Consumos||Bolas",
    "consumo_energia":     "Consumos||Energía Total",
    "consumo_combustible": "Consumos||Combustible",
    "consumo_explosivos":  "Consumos||Explosivos",
    "tarifa_acido":        "Tarifas||Ácido",
    "consumo_acido":       "Consumos||Ácido",
    # Variables mineras hidro
    "ley_hidro":           "Variables Mineras||Ley hidro",
    "apilamiento":         "Variables Mineras||Apilado",
    "beneficio_hidro":     "Variables Mineras||Beneficio",
    "rec_hidro":           "Variables Mineras||Recuperación hidro",
    "otros_hidro":         "Variables Mineras||ROM Dinámico /Ripos",
    # Rendimientos directos desde kpi_mappings (evita recalcular cuando ya existe el KPI)
    "rend_bolas_directo":  "Rendimientos||Bolas",
    "rend_acido_directo":  "Rendimientos||Ácido",
    "tratamiento":         ["Variables Mineras||Procesamiento",
                            "Variables Mineras||Beneficio",
                            "Variables Mineras||Apilado"],
    "horas_efectivas":     "Variables Mineras||Horas efectivas transporte",
    "tronadura":           "Variables Mineras||Total Tronado/Quebrado",
    "ley_cu":              ["Variables Mineras||Ley sulfuros",
                            "Variables Mineras||Ley oxidos"],
    "recuperacion":        ["Variables Mineras||Recuperación sulfuros",
                            "Variables Mineras||Recuperación óxidos"],
    "vi_inv":              "Variables Mineras||Var. Inv.",
    "cu_fino":             ["Variables Mineras||CuFino",
                            "Variables Mineras||Cátodos"],
    "mov_mina_sulf":       "Variables Mineras||Movimiento mina sulfuros",
    "mov_mina_ox":         "Variables Mineras||Movimiento mina óxidos",
    "mov_mina_total":      "Variables Mineras||Movimiento mina total",
    "dev_mina_sulf":       "Variables Mineras||Desarrollo mina sulfuros",
    "dev_mina_ox":         "Variables Mineras||Desarrollo mina óxidos",
    "dev_mina_total_vol":  "Variables Mineras||Desarrollo mina total",
    "tcrc_q":              "TC/RC & Comercialización||Unidades||CuFino",
    "tcrc_total":          "TC/RC & Comercialización||Gasto||TC/RC",
    "comer_total":         "TC/RC & Comercialización||Gasto||Comercialización",
}

MONTH_FIELDS = pq.MONTH_FIELDS
MONTH_BY_NUM = {i + 1: f for i, f in enumerate(MONTH_FIELDS)}

_TIPO_ALIASES: list[set[str]] = [
    {"ppto", "plan", "presupuesto", "budget"},
    {"real", "actual"},
]

FORMULA_PARAMS_PATH = Path(__file__).resolve().parents[3] / "data" / "formula_params.json"
PARAMS_FIN_PATH     = Path(__file__).resolve().parents[3] / "data" / "params_financieros.json"
KPI_STRUCTURE_FILE  = Path(__file__).resolve().parents[3] / "data" / "kpi_structure.json"
MAPPING_PATH        = Path(__file__).resolve().parents[3] / "data" / "kpi_mappings.json"

DEFAULT_PARAMS: dict = {
    "kpi_keys": {
        "tarifa_bolas":        "Tarifa Bolas",
        "tarifa_energia":      "Energía all in",
        "tarifa_combustible":  "Combustible",
        "tarifa_explosivos":   "Precios Insumos Críticos||Explosivos",
        "tarifa_acido":        "",
        "ley_hidro":           "",
        "apilamiento":         "",
        "beneficio_hidro":     "",
        "rec_hidro":           "",
        "otros_hidro":         "",
        "rend_bolas_directo":  "",
        "rend_acido_directo":  "",
        "consumo_bolas":       "Consumo Bolas",
        "consumo_energia":     "Consumo Energía Compañía",
        "consumo_combustible": "Mina||Consumo Comb. - Transporte",
        "consumo_explosivos":  "Mina||Explosivos",
        "consumo_acido":       "",
        "tratamiento":         "Tratamiento",
        "ley_cu":              "Ley de Cu",
        "recuperacion":        "Recuperación de cobre",
        "horas_efectivas":     "Mina||Horas Efectivas",
        "tronadura":           ["Roca Quebrada (Lastre)", "Roca Quebrada (Mineral)"],
        "tcrc_q":              "",
        "tcrc_total":          "",
        "comer_total":         "",
        "gasto_operacional":      "",
        "gasto_operacional_real": "",
        "dev_mina_vol":           "",
        "c3":                     "",
        "c2":                     "",
        "c1_abs":                 "",
        "da":                     "",
        "subprod_b":              "",
        "tcrc_b_total":           "",
        "comer_b_total":          "",
        # Efectos operacionales (Análisis Costo waterfall)
        "vi_inv_mina":            "",
        "vi_inv_planta":          "",
        "ifrs16":                 "",
        # Estructura real (cierre waterfall)
        "tcrc_r_total":           "",
        "comer_r_total":          "",
        "subprod_r":              "",
        "c1_r_costo":             "",
        "da_r":                   "",
        "c2_r":                   "",
        "c3_r":                   "",
        "otros_fin_r":            "",
        "dif_cambio_r":           "",
        "donaciones_r":           "",
        "gasto_expl_r":           "",
        "ing_gasto_fin_r":        "",
        "ing_no_op_r":            "",
        "otros_egr_op_r":         "",
    },
    "desarrollo_mina":      ["Desarrollo Mina||Desarrollo Mina"],
    "desarrollo_mina_items": [
        {"key": "ifric20",  "label": "IFRIC20",  "kpis": []},
        {"key": "efecto_p", "label": "Efecto P",  "kpis": []},
        {"key": "efecto_q", "label": "Efecto Q",  "kpis": []},
    ],
    "variacion_inventario": ["Var. Inv."],
    "kpi_keys_per_company": {},
    "subprod_precio_scale": 1.0,
    "actividad_contexto": {
        "mov_mina_kpis":        ["Movimiento Mina"],
        "procesamiento_kpi":    "Tratamiento",
        "costo_unitario_mina":  None,
        "costo_unitario_conc":  None,
        "costo_unitario_hidro": None,
    },
}


def _apply_formula_var_mappings(kpi_keys: dict, company: str) -> dict:
    """Override formula-var keys using the already-configured kpi_mappings for this company."""
    all_maps = load_mappings()
    company_map = all_maps.get(company, {})
    result = dict(kpi_keys)

    def _resolve_raw(raw):
        """Collapse a raw kpi_mappings value to something _resolve can handle (no _f nesting)."""
        if not raw or raw == "__NA__":
            return None
        if isinstance(raw, (str, list)):
            return raw
        if isinstance(raw, dict):
            if raw.get("_s") or raw.get("_rb"):
                return raw
            if raw.get("_f"):
                # Pre-resolve operands so _resolve can evaluate the formula later
                raw_a = company_map.get(raw.get("a", ""))
                raw_b = company_map.get(raw.get("b", ""))
                res_a = _resolve_raw(raw_a)
                res_b = _resolve_raw(raw_b)
                if res_a is not None and res_b is not None:
                    return {
                        "_f_resolved": True,
                        "a": res_a,
                        "op": raw.get("op", "/"),
                        "b": res_b,
                        "scale": raw.get("scale"),
                        "scale_op": raw.get("scale_op", "*"),
                    }
        return None

    def _assign(var_key, raw):
        resolved = _resolve_raw(raw)
        if resolved is not None:
            result[var_key] = resolved

    # 1. Static auto-resolution via _FORMULA_VAR_TO_LK (listas = fallbacks en orden)
    for var_key, lk_or_list in _FORMULA_VAR_TO_LK.items():
        lks = lk_or_list if isinstance(lk_or_list, list) else [lk_or_list]
        for lk in lks:
            raw = company_map.get(lk)
            if raw and raw != "__NA__":
                _assign(var_key, raw)
                break
    # 2. Explicit _formula_vars overrides (manual assignments, higher priority)
    for var_key, lk in all_maps.get("_formula_vars", {}).get(company, {}).items():
        _assign(var_key, company_map.get(lk))
    return result


def _load_params() -> dict:
    if FORMULA_PARAMS_PATH.exists():
        saved = json.loads(FORMULA_PARAMS_PATH.read_text(encoding="utf-8"))
        result: dict = {**DEFAULT_PARAMS}
        if "kpi_keys" in saved:
            result["kpi_keys"] = {**DEFAULT_PARAMS["kpi_keys"], **saved["kpi_keys"]}
        if "kpi_keys_per_company" in saved:
            result["kpi_keys_per_company"] = saved["kpi_keys_per_company"]
        for k in ("desarrollo_mina", "variacion_inventario", "subprod_precio_scale"):
            if k in saved:
                result[k] = saved[k]
        if "desarrollo_mina_items" in saved:
            result["desarrollo_mina_items"] = saved["desarrollo_mina_items"]
        if "actividad_contexto" in saved:
            result["actividad_contexto"] = {
                **DEFAULT_PARAMS["actividad_contexto"],
                **saved["actividad_contexto"],
            }
        if "kpi_scales_per_company" in saved:
            result["kpi_scales_per_company"] = saved["kpi_scales_per_company"]
        if "kpi_scales_mes_ytd_per_company" in saved:
            result["kpi_scales_mes_ytd_per_company"] = saved["kpi_scales_mes_ytd_per_company"]
        return result
    return DEFAULT_PARAMS


def _save_params(params: dict) -> None:
    FORMULA_PARAMS_PATH.write_text(
        json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── helpers ────────────────────────────────────────────────────────────────

def _tipo_variants(tipo: str) -> set[str]:
    t = tipo.strip().lower()
    for group in _TIPO_ALIASES:
        if t in group:
            return group
    return {t}


def _build_columns(año_ini, mes_ini, año_fin, mes_fin):
    cols, y, m = [], año_ini, mes_ini
    labels = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    while (y, m) <= (año_fin, mes_fin):
        cols.append({"year": y, "month": m, "label": f"{labels[m-1]}'{str(y)[2:]}"})
        m += 1
        if m > 12:
            m = 1
            y += 1
    return cols


def _fetch_snapshot(df_co, tipo: str, year: int, month: int) -> dict[str, dict]:
    variants = _tipo_variants(tipo)
    df_tipo  = df_co[df_co["tipo"].str.strip().str.lower().isin(variants)]

    periodo = year * 100 + month
    df = df_tipo[df_tipo["periodo"] == periodo]

    if df.empty:
        year_min = year * 100 + 1
        df_year  = df_tipo[(df_tipo["periodo"] >= year_min) & (df_tipo["periodo"] <= periodo)]
        if not df_year.empty:
            periodo = int(df_year["periodo"].max())
            df = df_tipo[df_tipo["periodo"] == periodo]

    result: dict[str, dict] = {}
    for _, row in df.iterrows():
        kpi_name    = str(row.get("kpi", "") or "").strip()
        subcategory = str(row.get("subcategory", "") or "").strip()
        if not kpi_name:
            continue
        key = f"{subcategory}||{kpi_name}" if subcategory else kpi_name
        if key not in result:
            result[key] = pq.row_to_monthly_values(row)
    return result


def _resolve(snapshot: dict, src, field: str):
    # Handle list: sum all resolved values
    if isinstance(src, list):
        return _sum_resolve(snapshot, src, field)
    # Handle dict variants from kpi_mappings
    scalar_op, scalar_val = None, None
    if isinstance(src, dict):
        if src.get("_f_resolved"):
            a_val = _resolve(snapshot, src["a"], field)
            b_val = _resolve(snapshot, src["b"], field)
            if a_val is None or b_val is None:
                return None
            op = src.get("op", "/")
            if op == "/" and b_val == 0:
                return None
            val = {"+": a_val + b_val, "-": a_val - b_val,
                   "*": a_val * b_val, "/": a_val / b_val}.get(op)
            if val is None:
                return None
            scale = src.get("scale")
            if scale is not None:
                sc = float(scale)
                s_op = src.get("scale_op", "*")
                val = {"+": val + sc, "-": val - sc, "*": val * sc,
                       "/": val / sc if sc != 0 else None}.get(s_op, val)
            return val
        if src.get("_s"):
            scalar_op  = src.get("op", "*")
            scalar_val = src.get("scalar")
            src = src.get("src", "") or ""
        elif src.get("_rb"):
            src = src.get("src", src.get("real", "")) or ""
        else:
            src = src.get("src", "") or ""
    if not src:
        return None
    data = snapshot.get(src)
    if data is None:
        suffix = f"||{src}"
        matches = [v for k, v in snapshot.items() if k.endswith(suffix)]
        data = matches[0] if len(matches) == 1 else None
    if data is None:
        return None
    val = data.get(field)
    if val is None:
        return None
    result = float(val)
    if scalar_op is not None and scalar_val is not None:
        sc = float(scalar_val)
        result = {"+": result + sc, "-": result - sc, "*": result * sc,
                  "/": result / sc if sc != 0 else None}.get(scalar_op, result)
    return result


def _sum_resolve(snapshot: dict, keys: list[str], field: str):
    total = None
    for k in keys:
        v = _resolve(snapshot, k, field)
        if v is not None:
            total = (total or 0.0) + v
    return total


def _safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _n(a, b):
    return (a - b) if (a is not None and b is not None) else None


def _m(*args):
    result = 1.0
    for a in args:
        if a is None:
            return None
        result *= a
    return result


def _s(v):
    return v if v is not None else 0.0


def _delta_act_trat(dp, dl, dr_, ley_b, rec_b):
    if dp is None:
        return None
    return (
        _s(dp) * _s(ley_b) * _s(rec_b)
        + _s(dp) * _s(dl) * _s(rec_b) / 2
        + _s(dp) * _s(dr_) * _s(ley_b) / 2
        + _s(dp) * _s(dl) * _s(dr_) / 3
    )


def _delta_act_rec(dp, dl, dr_, proc_b, ley_b):
    if dr_ is None:
        return None
    return (
        _s(dr_) * _s(proc_b) * _s(ley_b)
        + _s(dr_) * _s(dp) * _s(ley_b) / 2
        + _s(dr_) * _s(dl) * _s(proc_b) / 2
        + _s(dr_) * _s(dp) * _s(dl) / 3
    )


def _efecto_ley(dp, dl, dr_, proc_b, rec_b):
    if dl is None:
        return None
    return (
        _s(dl) * _s(proc_b) * _s(rec_b)
        + _s(dl) * _s(dp) * _s(rec_b) / 2
        + _s(dl) * _s(dr_) * _s(proc_b) / 2
        + _s(dl) * _s(dp) * _s(dr_) / 3
    )


def _compute_deltas(r_snap: dict, b_snap: dict, field: str, p: dict) -> dict:
    """Compute all formula-driven deltas for a single time field."""
    k = p["kpi_keys"]
    _sc     = p.get("kpi_scales", {})
    _sc_myt = set(p.get("kpi_scales_mes_ytd", []))  # scales solo activos en campos mes/ytd

    def r(key): return _resolve(r_snap, key, field)
    def b(key): return _resolve(b_snap, key, field)
    def rs(var, key):
        v = r(key); sc = _sc.get(var)
        if sc is None or v is None: return v
        if var in _sc_myt and field not in ("mes", "ytd"): return v
        return v * sc
    def bs(var, key):
        v = b(key); sc = _sc.get(var)
        if sc is None or v is None: return v
        if var in _sc_myt and field not in ("mes", "ytd"): return v
        return v * sc

    tron_keys = k["tronadura"] if isinstance(k["tronadura"], list) else [k["tronadura"]]

    proc_r = rs("tratamiento", k["tratamiento"]); proc_b = bs("tratamiento", k["tratamiento"])
    _ley_r = rs("ley_cu",      k["ley_cu"]);      _ley_b = bs("ley_cu",      k["ley_cu"])
    _rec_r = rs("recuperacion",k["recuperacion"]); _rec_b = bs("recuperacion",k["recuperacion"])
    # ley stored as % (e.g. 0.52), rec stored as % (e.g. 89.7) → convert to fractions
    ley_r  = _ley_r / 100 if _ley_r is not None else None
    ley_b  = _ley_b / 100 if _ley_b is not None else None
    rec_r  = _rec_r / 100 if _rec_r is not None else None
    rec_b  = _rec_b / 100 if _rec_b is not None else None
    # Variables hidro (SX-EW)
    _ben_key = k.get("beneficio_hidro", "")
    _lh_key  = k.get("ley_hidro", "")
    _rh_key  = k.get("rec_hidro", "")
    ben_r  = r(_ben_key) if _ben_key else None;  ben_b  = b(_ben_key) if _ben_key else None
    _lh_r  = r(_lh_key)  if _lh_key  else None;  _lh_b  = b(_lh_key)  if _lh_key  else None
    _rh_r  = r(_rh_key)  if _rh_key  else None;  _rh_b  = b(_rh_key)  if _rh_key  else None
    lh_r = _lh_r / 100 if _lh_r is not None else None
    lh_b = _lh_b / 100 if _lh_b is not None else None
    rh_r = _rh_r / 100 if _rh_r is not None else None
    rh_b = _rh_b / 100 if _rh_b is not None else None
    hef_r  = r(k["horas_efectivas"]);      hef_b  = b(k["horas_efectivas"])
    tron_r = _sum_resolve(r_snap, tron_keys, field)
    tron_b = _sum_resolve(b_snap, tron_keys, field)

    tar_bolas_r = r(k["tarifa_bolas"]);        tar_bolas_b = b(k["tarifa_bolas"])
    tar_energ_r = r(k["tarifa_energia"]);      tar_energ_b = b(k["tarifa_energia"])
    tar_comb_r  = r(k["tarifa_combustible"]);  tar_comb_b  = b(k["tarifa_combustible"])
    tar_expl_r  = r(k["tarifa_explosivos"]);   tar_expl_b  = b(k["tarifa_explosivos"])

    con_bolas_r = r(k["consumo_bolas"]);       con_bolas_b = b(k["consumo_bolas"])
    con_energ_r = r(k["consumo_energia"]);     con_energ_b = b(k["consumo_energia"])
    con_comb_r  = r(k["consumo_combustible"]); con_comb_b  = b(k["consumo_combustible"])
    con_expl_r  = r(k["consumo_explosivos"]);  con_expl_b  = b(k["consumo_explosivos"])
    tar_acido_r = r(k.get("tarifa_acido", "")); tar_acido_b = b(k.get("tarifa_acido", ""))
    con_acido_r = r(k.get("consumo_acido", "")); con_acido_b = b(k.get("consumo_acido", ""))

    rend_bolas_r = _m(_safe_div(con_bolas_r, proc_r), 1000.0)
    rend_bolas_b = _m(_safe_div(con_bolas_b, proc_b), 1000.0)
    rend_energ_r = _safe_div(con_energ_r, proc_r)
    rend_energ_b = _safe_div(con_energ_b, proc_b)
    rend_comb_r  = _safe_div(con_comb_r,  hef_r)
    rend_comb_b  = _safe_div(con_comb_b,  hef_b)
    rend_expl_r  = _m(_safe_div(con_expl_r, tron_r), 1000.0)
    rend_expl_b  = _m(_safe_div(con_expl_b, tron_b), 1000.0)
    # ácido: con en t, denominador en kt → ratio t/kt = kg/t (sin ×1000)
    apil_r = r(k.get("apilamiento", ""))
    apil_b = b(k.get("apilamiento", ""))
    _acido_den_r = apil_r if apil_r is not None else proc_r
    _acido_den_b = apil_b if apil_b is not None else proc_b
    rend_acido_r = _safe_div(con_acido_r, _acido_den_r)
    rend_acido_b = _safe_div(con_acido_b, _acido_den_b)

    # Delta Gasto (Precio)
    dg_bolas = _m(_n(tar_bolas_r, tar_bolas_b), con_bolas_r, 1/1000) if con_bolas_r else None
    dg_acido = _m(_n(tar_acido_r, tar_acido_b), con_acido_r, 1/1000) if con_acido_r else None
    dg_energ = _m(_n(tar_energ_r, tar_energ_b), con_energ_r, 1/1000) if con_energ_r else None
    dg_comb  = _m(_n(tar_comb_r,  tar_comb_b),  con_comb_r)          if con_comb_r  else None
    dg_expl  = _m(_n(tar_expl_r,  tar_expl_b),  con_expl_r, 1/1000)  if con_expl_r  else None
    vals_dg  = [dg_bolas, dg_acido, dg_energ, dg_comb, dg_expl]
    dg_total = sum(_s(x) for x in vals_dg if x is not None) or None

    # Delta Rendimiento
    dr_bolas   = _m(_n(rend_bolas_r, rend_bolas_b), proc_r,  1000.0, tar_bolas_b, 1/1e9) if proc_r else None
    dr_acido   = _m(_n(rend_acido_r, rend_acido_b), _acido_den_r, tar_acido_b, 1/1000) if _acido_den_r else None
    dr_energ_c = _m(_n(rend_energ_r, rend_energ_b), proc_r,  1000.0, tar_energ_b, 1/1e6) if proc_r else None
    dr_energ_h = None
    dr_comb    = _m(_n(rend_comb_r,  rend_comb_b),  hef_r,           tar_comb_b)          if hef_r  else None
    dr_expl    = _m(_n(rend_expl_r,  rend_expl_b),  tron_r,          tar_expl_b,  1/1e6)  if tron_r else None
    vals_dr    = [dr_bolas, dr_acido, dr_energ_c, dr_energ_h, dr_comb, dr_expl]
    dr_total   = sum(_s(x) for x in vals_dr if x is not None) or None

    # Delta Actividad — concentradora (None si no hay datos de concentradora)
    dp = _n(proc_r, proc_b); dl = _n(ley_r, ley_b); dr = _n(rec_r, rec_b)
    if ley_b is None and rec_b is None:
        da_trat_c = None
        da_rec_c  = None
    else:
        da_trat_c = _delta_act_trat(dp, dl, dr, ley_b, rec_b)
        da_rec_c  = _delta_act_rec(dp, dl, dr, proc_b, ley_b)
    # Delta Actividad — hidro (SX-EW)
    dph = _n(ben_r, ben_b); dlh = _n(lh_r, lh_b); drh = _n(rh_r, rh_b)
    da_trat_h = _delta_act_trat(dph, dlh, drh, lh_b, rh_b)
    da_rec_h  = _delta_act_rec(dph, dlh, drh, ben_b, lh_b)
    vals_da   = [da_trat_c, da_trat_h, da_rec_c, da_rec_h]
    da_total  = sum(_s(x) for x in vals_da if x is not None) or None

    # Efecto Ley
    el_conc  = _efecto_ley(dp, dl, dr, proc_b, rec_b)
    el_hidro = _efecto_ley(dph, dlh, drh, ben_b, rh_b)
    vals_el  = [el_conc, el_hidro]
    el_total = sum(_s(x) for x in vals_el if x is not None) or None

    # Inventarios y otros — residuo de la bridge de producción (ktCuf)
    # ΔCu_actual (CuFino KPI, en kt) − efectos explicados = efecto inventarios
    cu_fino_r = r(k.get("cu_fino", ""))
    cu_fino_b = b(k.get("cu_fino", ""))
    _cu_delta = _n(cu_fino_r, cu_fino_b)
    if _cu_delta is not None:
        _explained = sum(_s(v) for v in [da_trat_c, da_rec_c, el_conc,
                                          da_trat_h, da_rec_h, el_hidro] if v is not None)
        varinv_residual = round(_cu_delta - _explained, 4)
    else:
        varinv_residual = None

    # Efectos TC/RC y Comercialización — unitarios calculados inline (TC/RC total / cantidad)
    tcrc_q_r    = r(k.get("tcrc_q", ""))
    tcrc_q_b    = b(k.get("tcrc_q", ""))
    tcrc_tot_r  = r(k.get("tcrc_total", ""))
    tcrc_tot_b  = b(k.get("tcrc_total", ""))
    comer_tot_r = r(k.get("comer_total", ""))
    comer_tot_b = b(k.get("comer_total", ""))
    tcrc_u_r    = _safe_div(tcrc_tot_r,  tcrc_q_r)
    tcrc_u_b    = _safe_div(tcrc_tot_b,  tcrc_q_b)
    comer_u_r   = _safe_div(comer_tot_r, tcrc_q_r)
    comer_u_b   = _safe_div(comer_tot_b, tcrc_q_b)

    tcrc_cant  = _m(_n(tcrc_q_r, tcrc_q_b), tcrc_u_r)
    tcrc_tar   = _m(_n(tcrc_u_r, tcrc_u_b), tcrc_q_b)
    tcrc_tot   = _sum_or_none([tcrc_cant, tcrc_tar])
    comer_cant = _m(_n(tcrc_q_r, tcrc_q_b), comer_u_r)
    comer_tar  = _m(_n(comer_u_r, comer_u_b), tcrc_q_b)
    comer_tot  = _sum_or_none([comer_cant, comer_tar])

    def fmt(v):
        return round(v, 4) if v is not None else None

    return {
        "delta_gasto_precio": {
            "bolas": fmt(dg_bolas), "acido": fmt(dg_acido), "energia": fmt(dg_energ),
            "combustible": fmt(dg_comb), "explosivos": fmt(dg_expl), "total": fmt(dg_total),
        },
        "delta_rendimiento": {
            "bolas": fmt(dr_bolas), "acido": fmt(dr_acido), "energia_conc": fmt(dr_energ_c),
            "energia_hidro": fmt(dr_energ_h), "combustible": fmt(dr_comb),
            "explosivos": fmt(dr_expl), "total": fmt(dr_total),
        },
        "delta_actividad": {
            "trat_concentradora": fmt(da_trat_c), "trat_hidro": fmt(da_trat_h),
            "rec_concentradora": fmt(da_rec_c), "rec_hidro": fmt(da_rec_h),
            "total": fmt(da_total),
        },
        "efecto_ley": {
            "concentradora": fmt(el_conc), "hidro": fmt(el_hidro), "total": fmt(el_total),
        },
        "efectos_tcrc": {
            "cantidades": fmt(tcrc_cant), "tarifas": fmt(tcrc_tar), "total": fmt(tcrc_tot),
        },
        "efectos_comer": {
            "cantidades": fmt(comer_cant), "tarifas": fmt(comer_tar), "total": fmt(comer_tot),
        },
        "varinv_residual": varinv_residual,
    }


def _compute_simple_delta(r_snap: dict, b_snap: dict, field: str, kpi_list: list[str]):
    """Real minus budget for a list of KPIs (summed)."""
    rv = _sum_resolve(r_snap, kpi_list, field)
    bv = _sum_resolve(b_snap, kpi_list, field)
    v  = _n(rv, bv)
    return round(v, 4) if v is not None else None


def _sum_or_none(vals: list) -> float | None:
    total, has = 0.0, False
    for v in vals:
        if v is not None:
            total += v; has = True
    return round(total, 4) if has else None


def _compute_dm_item(r_snap: dict, b_snap: dict, field: str, item: dict,
                     p: dict | None = None, company: str = "") -> float | None:
    t = item.get("type", "delta")
    cost_kpis = (item.get("cost_kpis_per_company") or {}).get(company) \
                or item.get("cost_kpis") or item.get("kpis", [])

    if t == "delta":
        if not cost_kpis:
            return None
        rv = _sum_resolve(r_snap, cost_kpis, field)
        bv = _sum_resolve(b_snap, cost_kpis, field)
        v  = _n(rv, bv)
        return round(v, 4) if v is not None else None

    if t in ("efecto_p", "efecto_q"):
        vol_kpis = item.get("vol_kpis") or (p or {}).get("desarrollo_mina", [])
        if not cost_kpis or not vol_kpis:
            return None
        cost_r = _sum_resolve(r_snap, cost_kpis, field)
        cost_b = _sum_resolve(b_snap, cost_kpis, field)
        vol_r  = _sum_resolve(r_snap, vol_kpis,  field)
        vol_b  = _sum_resolve(b_snap, vol_kpis,  field)
        if None in (cost_r, cost_b, vol_r, vol_b) or vol_b == 0:
            return None
        cu_b = cost_b / vol_b
        if t == "efecto_p":
            if vol_r == 0:
                return None
            v = (cost_r / vol_r - cu_b) * vol_r
        else:
            v = (vol_r - vol_b) * cu_b
        return round(v, 4)

    return None


def _compute_actividad_contexto(
    r_snap: dict, b_snap: dict, field: str, p: dict, tc_factor: float | None
) -> dict:
    """Mov Mina delta and activity-cost variances (TC-adjusted budget unit cost × volume delta)."""
    ac = p.get("actividad_contexto", {})

    mov_kpis = ac.get("mov_mina_kpis", [])
    mov_r    = _sum_resolve(r_snap, mov_kpis, field)
    mov_b    = _sum_resolve(b_snap, mov_kpis, field)
    delta_mov = _n(mov_r, mov_b)

    proc_key  = ac.get("procesamiento_kpi", "Tratamiento")
    proc_r    = _resolve(r_snap, proc_key, field)
    proc_b    = _resolve(b_snap, proc_key, field)
    delta_proc = _n(proc_r, proc_b)

    cu_mina  = ac.get("costo_unitario_mina")
    cu_conc  = ac.get("costo_unitario_conc")
    cu_hidro = ac.get("costo_unitario_hidro")

    k = p.get("kpi_keys", {})
    ben_key = k.get("beneficio_hidro", "")
    ben_r   = _resolve(r_snap, ben_key, field) if ben_key else None
    ben_b   = _resolve(b_snap, ben_key, field) if ben_key else None
    delta_ben = _n(ben_r, ben_b)

    def _act(delta, cu):
        if delta is None or cu is None or tc_factor is None:
            return None
        return _m(delta, cu, tc_factor)

    fmt = lambda v: round(v, 4) if v is not None else None
    return {
        "mov_mina":       fmt(delta_mov),
        "actividad_mina": fmt(_act(delta_mov,  cu_mina)),
        "actividad_conc": fmt(_act(delta_proc, cu_conc)),
        "actividad_hidro": fmt(_act(delta_ben, cu_hidro)),
    }


# ── subproductos effects ────────────────────────────────────────────────────

_SUBPROD_METALS = ["Moly", "Oro", "Plata", "Renio"]


def _get_src(mapped, use_real: bool) -> str | None:
    """Extract parquet column from a mapping, respecting real/budget split (_rb format)."""
    if not mapped or mapped == "__NA__":
        return None
    if isinstance(mapped, dict):
        if mapped.get("_rb"):
            return mapped.get("real" if use_real else "budget") or None
        if mapped.get("_s"):
            return mapped.get("src") or None
    if isinstance(mapped, str):
        return mapped or None
    if isinstance(mapped, list):
        srcs = [s for s in mapped if s]
        return srcs[0] if srcs else None
    return None


def _get_kpi_list(mapped, use_real: bool) -> list[str]:
    """Extract list of parquet columns from a mapping, respecting real/budget split."""
    if mapped == "__NA__":
        return []
    if not mapped and mapped != "":
        return []
    if isinstance(mapped, dict):
        if mapped.get("_rb"):
            src = mapped.get("real" if use_real else "budget", "")
            return [src] if src else []
        if mapped.get("_s"):
            src = mapped.get("src", "")
            return [src] if src else []
    if isinstance(mapped, list):
        return [s for s in mapped if s]
    if isinstance(mapped, str):
        return [mapped] if mapped else []
    return []


def _compute_subprod_effects(
    col_snaps: list, col_fields: list,
    end_r: dict, end_b: dict,
    mappings: dict, precio_scale: float,
) -> dict:
    ep_result: dict = {}
    eq_result: dict = {}
    cr_result: dict = {}
    n = len(col_fields)
    ep_totals   = [None] * n
    eq_totals   = [None] * n
    cr_r_totals = [None] * n
    cr_b_totals = [None] * n

    def _accum_list(lst, vals):
        for i, v in enumerate(vals):
            if v is not None:
                lst[i] = (lst[i] or 0.0) + v

    fmt = lambda v: round(v, 4) if v is not None else None

    for metal in _SUBPROD_METALS:
        qty_m   = mappings.get(f"Subproductos||Ventas Subproductos||{metal}")
        pr_m    = mappings.get(f"Subproductos||Precio Realizado Subproductos al costo||{metal}")
        pb_m    = mappings.get(f"Subproductos||Precio Budget||{metal}")
        ing_m   = mappings.get(f"Subproductos||Ingresos Subproductos||{metal}")
        qty_src_r = _get_src(qty_m, True);  qty_src_b = _get_src(qty_m, False)
        pr_src    = _get_src(pr_m,  True)   # only used with real snapshot
        pb_src    = _get_src(pb_m,  False)  # only used with budget snapshot
        ing_src_r = _get_src(ing_m, True);  ing_src_b = _get_src(ing_m, False)
        qty_r_src = qty_src_r or qty_src_b
        qty_b_src = qty_src_b or qty_src_r
        ing_r_src = ing_src_r or ing_src_b
        ing_b_src = ing_src_b or ing_src_r

        ep_vals: list = []; eq_vals: list = []
        cr_r_vals: list = []; cr_b_vals: list = []

        for (rs, bs), f in zip(col_snaps, col_fields):
            qty_r = _resolve(rs, qty_r_src, f) if qty_r_src else None
            qty_b = _resolve(bs, qty_b_src, f) if qty_b_src else None
            pr_r  = _resolve(rs, pr_src,    f) if pr_src    else None
            pb_b  = _resolve(bs, pb_src,    f) if pb_src    else None
            ing_r = _resolve(rs, ing_r_src, f) if ing_r_src else None
            ing_b = _resolve(bs, ing_b_src, f) if ing_b_src else None
            ep_vals.append(fmt(_m(_n(pr_r, pb_b), qty_r, precio_scale)))
            eq_vals.append(fmt(_m(_n(qty_r, qty_b), pb_b, precio_scale)))
            cr_r_vals.append(fmt(ing_r))
            cr_b_vals.append(fmt(ing_b))

        def _rend_r(f): return _resolve(end_r, pr_src,    f) if pr_src    else None
        def _rend_b(f): return _resolve(end_b, pb_src,    f) if pb_src    else None
        def _rqty_r(f): return _resolve(end_r, qty_r_src, f) if qty_r_src else None
        def _rqty_b(f): return _resolve(end_b, qty_b_src, f) if qty_b_src else None

        ep_mes  = fmt(_m(_n(_rend_r("mes"), _rend_b("mes")), _rqty_r("mes"), precio_scale))
        ep_ytd  = fmt(_sum_or_none(ep_vals))
        eq_mes  = fmt(_m(_n(_rqty_r("mes"), _rqty_b("mes")), _rend_b("mes"), precio_scale))
        eq_ytd  = fmt(_sum_or_none(eq_vals))
        cr_r_mes = fmt(_resolve(end_r, ing_r_src, "mes") if ing_r_src else None)
        cr_b_mes = fmt(_resolve(end_b, ing_b_src, "mes") if ing_b_src else None)
        cr_r_ytd = fmt(_resolve(end_r, ing_r_src, "ytd") if ing_r_src else None)
        cr_b_ytd = fmt(_resolve(end_b, ing_b_src, "ytd") if ing_b_src else None)

        ep_result[metal] = {"vals": ep_vals, "mes": ep_mes, "ytd": ep_ytd}
        if any(v is not None for v in ep_vals) or ep_mes is not None:
            _accum_list(ep_totals, ep_vals)
        eq_result[metal] = {"vals": eq_vals, "mes": eq_mes, "ytd": eq_ytd}
        if any(v is not None for v in eq_vals) or eq_mes is not None:
            _accum_list(eq_totals, eq_vals)
        cr_result[metal] = {
            "real":   {"vals": cr_r_vals, "mes": cr_r_mes, "ytd": cr_r_ytd},
            "budget": {"vals": cr_b_vals, "mes": cr_b_mes, "ytd": cr_b_ytd},
        }
        if (ing_r_src or ing_b_src) and any(v is not None for v in cr_r_vals + cr_b_vals):
            _accum_list(cr_r_totals, cr_r_vals)
            _accum_list(cr_b_totals, cr_b_vals)

    rl = lambda lst: [fmt(v) for v in lst]
    ep_result["total"] = {
        "vals": rl(ep_totals),
        "mes":  fmt(_sum_or_none([ep_result[m]["mes"] for m in _SUBPROD_METALS])),
        "ytd":  fmt(_sum_or_none([ep_result[m]["ytd"] for m in _SUBPROD_METALS])),
    }
    eq_result["total"] = {
        "vals": rl(eq_totals),
        "mes":  fmt(_sum_or_none([eq_result[m]["mes"] for m in _SUBPROD_METALS])),
        "ytd":  fmt(_sum_or_none([eq_result[m]["ytd"] for m in _SUBPROD_METALS])),
    }
    cr_result["total"] = {
        "real":   {"vals": rl(cr_r_totals),
                   "mes": fmt(_sum_or_none([cr_result[m]["real"]["mes"]   for m in _SUBPROD_METALS])),
                   "ytd": fmt(_sum_or_none([cr_result[m]["real"]["ytd"]   for m in _SUBPROD_METALS]))},
        "budget": {"vals": rl(cr_b_totals),
                   "mes": fmt(_sum_or_none([cr_result[m]["budget"]["mes"] for m in _SUBPROD_METALS])),
                   "ytd": fmt(_sum_or_none([cr_result[m]["budget"]["ytd"] for m in _SUBPROD_METALS]))},
    }

    return {"efecto_precio": ep_result, "efecto_cantidad": eq_result, "creditos": cr_result}


# ── mapped sections (Subproductos, TC/RC) ──────────────────────────────────

def _load_company_mappings(company: str) -> dict:
    if MAPPING_PATH.exists():
        return json.loads(MAPPING_PATH.read_text(encoding="utf-8")).get(company, {})
    return {}


def _build_unit_lookup(company: str) -> dict[str, str]:
    if not KPI_STRUCTURE_FILE.exists():
        return {}
    structure = json.loads(KPI_STRUCTURE_FILE.read_text(encoding="utf-8"))
    lookup: dict[str, str] = {}
    cur_subhdr = ""
    for item in structure.get(company, []):
        if item["type"] == "header":
            cur_subhdr = ""
        elif item["type"] == "subheader":
            cur_subhdr = item["label"]
        elif item["type"] == "kpi" and cur_subhdr:
            lookup[f"{cur_subhdr}||{item['label']}"] = item.get("unit", "")
    return lookup


def _apply_scalar(v, op: str, scalar: float):
    if v is None or scalar is None:
        return None
    if op == "/" and scalar == 0:
        return None
    return {"+": v + scalar, "-": v - scalar, "*": v * scalar, "/": v / scalar}.get(op, v)


def _parse_mapped_items(mappings: dict, section_prefix: str, unit_lookup: dict) -> list:
    """Returns [(subhdr, label, kpi_list_r, kpi_list_b, unit, scalar_info)] for all valid 3-part mapping keys.
    kpi_list_r/b are the parquet columns for real and budget respectively (_rb format gives different lists).
    scalar_info is (op, scalar) for _s mappings, else None."""
    items = []
    prefix = section_prefix + "||"
    for key, mapped in mappings.items():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix):]
        parts = rest.split("||", 1)
        if len(parts) != 2:
            continue
        subhdr, label = parts
        if mapped == "__NA__":
            continue
        scalar_info = None
        if isinstance(mapped, dict) and mapped.get("_s"):
            src = mapped.get("src", "")
            kpi_list_r = [src] if src else []
            kpi_list_b = kpi_list_r
            sc = mapped.get("scalar")
            if sc is not None:
                scalar_info = (mapped.get("op", "*"), float(sc))
        else:
            kpi_list_r = _get_kpi_list(mapped, True)
            kpi_list_b = _get_kpi_list(mapped, False)
        unit = unit_lookup.get(f"{subhdr}||{label}", "")
        items.append((subhdr, label, kpi_list_r, kpi_list_b, unit, scalar_info))
    return items


def _build_mapped_section_response(
    col_snaps: list, col_fields: list,
    end_r: dict, end_b: dict,
    items: list,
) -> dict:
    result: dict = {}
    for subhdr, label, kpi_list_r, kpi_list_b, unit, scalar_info in items:
        if not kpi_list_r and not kpi_list_b:
            result.setdefault(subhdr, {})[label] = {"vals": [None] * len(col_snaps), "mes": None, "ytd": None, "unit": unit}
            continue
        r_list = kpi_list_r or kpi_list_b
        b_list = kpi_list_b or kpi_list_r
        def _delta(rs, bs, f, rl=r_list, bl=b_list):
            rv = _sum_resolve(rs, rl, f)
            bv = _sum_resolve(bs, bl, f)
            v  = _n(rv, bv)
            return round(v, 4) if v is not None else None
        vals  = [_delta(rs, bs, f) for (rs, bs), f in zip(col_snaps, col_fields)]
        mes_v = _delta(end_r, end_b, "mes")
        ytd_v = _delta(end_r, end_b, "ytd")
        if scalar_info:
            op_s, sc_s = scalar_info
            vals  = [_apply_scalar(v, op_s, sc_s) for v in vals]
            mes_v = _apply_scalar(mes_v, op_s, sc_s)
            ytd_v = _apply_scalar(ytd_v, op_s, sc_s)
        result.setdefault(subhdr, {})[label] = {"vals": vals, "mes": mes_v, "ytd": ytd_v, "unit": unit}
    return result


# ── endpoints ──────────────────────────────────────────────────────────────

@router.get("/formula-params")
def get_formula_params():
    return _load_params()


class FormulaParamsBody(BaseModel):
    kpi_keys: dict
    kpi_keys_per_company: dict = {}
    desarrollo_mina: list
    desarrollo_mina_items: list = []
    variacion_inventario: list
    actividad_contexto: dict = {}
    subprod_precio_scale: float = 1.0


@router.put("/formula-params")
def put_formula_params(body: FormulaParamsBody):
    _save_params(body.model_dump())
    return {"ok": True}


@router.get("/{company}")
def get_calculos(
    company: str,
    año_ini: int, mes_ini: int, año_fin: int, mes_fin: int,
):
    if not settings.parquet_path.exists():
        raise HTTPException(404, "Parquet no encontrado")

    p = _load_params()
    per_co_keys = p.get("kpi_keys_per_company", {}).get(company, {})
    if per_co_keys:
        p["kpi_keys"] = {**p["kpi_keys"], **per_co_keys}
    p["kpi_keys"]        = _apply_formula_var_mappings(p["kpi_keys"], company)
    p["kpi_scales"]      = p.get("kpi_scales_per_company", {}).get(company, {})
    p["kpi_scales_mes_ytd"] = p.get("kpi_scales_mes_ytd_per_company", {}).get(company, [])

    params_fin: dict = {}
    if PARAMS_FIN_PATH.exists():
        params_fin = json.loads(PARAMS_FIN_PATH.read_text(encoding="utf-8"))
    exp_tc_pct  = params_fin.get("exp_tc", {}).get(company, 0)
    exp_tc_frac = exp_tc_pct / 100.0

    def _tc_factor(yr: int, mo: int) -> float | None:
        mo_data = params_fin.get("kpis", {}).get(str(yr), {}).get(str(mo), {})
        dr = mo_data.get("dolar_real")
        db = mo_data.get("dolar_budget")
        if dr and db and float(dr) != 0:
            return exp_tc_frac * float(db) / float(dr) + (1.0 - exp_tc_frac)
        return None

    df_all = pq.get_cached_df(settings.parquet_path)
    df_co  = df_all[df_all["compania"] == company]

    cols  = _build_columns(año_ini, mes_ini, año_fin, mes_fin)
    years = sorted({c["year"] for c in cols})

    real_snaps:   dict[int, dict] = {}
    budget_snaps: dict[int, dict] = {}
    for yr in years:
        snap_mes = mes_fin if yr == año_fin else 12
        real_snaps[yr]   = _fetch_snapshot(df_co, "real", yr, snap_mes)
        budget_snaps[yr] = _fetch_snapshot(df_co, "plan", yr, snap_mes)

    dm_items = p.get("desarrollo_mina_items", [])

    # Per-column deltas
    col_deltas    = []
    col_dev       = []
    col_dev_items = []
    col_varinv    = []
    col_act_ctx   = []
    for col in cols:
        yr, mo = col["year"], col["month"]
        field  = MONTH_BY_NUM[mo]
        rs, bs = real_snaps.get(yr, {}), budget_snaps.get(yr, {})
        _d = _compute_deltas(rs, bs, field, p)
        col_deltas.append(_d)
        col_dev.append(_compute_simple_delta(rs, bs, field, p["desarrollo_mina"]))
        col_dev_items.append({it["key"]: _compute_dm_item(rs, bs, field, it, p, company) for it in dm_items})
        col_varinv.append(_d.get("varinv_residual"))
        col_act_ctx.append(_compute_actividad_contexto(rs, bs, field, p, _tc_factor(yr, mo)))

    # Mes + YTD
    end_r = real_snaps.get(año_fin, {})
    end_b = budget_snaps.get(año_fin, {})

    # Mapped sections (Subproductos, TC/RC & Comercialización)
    company_mappings = _load_company_mappings(company)
    unit_lookup      = _build_unit_lookup(company)
    col_snaps        = [(real_snaps.get(c["year"], {}), budget_snaps.get(c["year"], {})) for c in cols]
    col_fields_list  = [MONTH_BY_NUM[c["month"]] for c in cols]
    tcrc_items        = _parse_mapped_items(company_mappings, "TC/RC & Comercialización", unit_lookup)
    tcrc_data         = _build_mapped_section_response(col_snaps, col_fields_list, end_r, end_b, tcrc_items)
    precio_scale      = p.get("subprod_precio_scale", 1.0)
    subprod_effects   = _compute_subprod_effects(
        col_snaps, col_fields_list, end_r, end_b, company_mappings, precio_scale
    )
    # Mes = last column value; YTD = sum of all monthly columns
    def _sum_cols(section: str, key: str):
        total, has = 0.0, False
        for cd in col_deltas:
            v = cd[section].get(key)
            if v is not None:
                total += v; has = True
        return round(total, 4) if has else None

    # Simple delta Mes/YTD — Mes from last column, YTD from sum of columns
    dev_mes       = col_dev[-1] if col_dev else None
    dev_ytd       = _sum_or_none(col_dev)
    dev_mes_items = col_dev_items[-1] if col_dev_items else {}
    dev_ytd_items = {it["key"]: _sum_or_none([d.get(it["key"]) for d in col_dev_items]) for it in dm_items}
    varinv_mes    = col_varinv[-1] if col_varinv else None
    varinv_ytd    = _sum_or_none(col_varinv)

    # Actividad contexto Mes/YTD
    act_ctx_mes = col_act_ctx[-1] if col_act_ctx else {k: None for k in ["mov_mina", "actividad_mina", "actividad_conc", "actividad_hidro"]}
    act_ctx_ytd = {
        k: _sum_or_none([d[k] for d in col_act_ctx])
        for k in ["mov_mina", "actividad_mina", "actividad_conc", "actividad_hidro"]
    }

    def build_series(section: str, key: str) -> dict:
        return {
            "vals": [cd[section][key] for cd in col_deltas],
            "mes":  col_deltas[-1][section][key] if col_deltas else None,
            "ytd":  _sum_cols(section, key),
        }

    def section_series(section: str, keys: list[str]) -> dict:
        return {k: build_series(section, k) for k in keys}

    return {
        "company": company,
        "cols":    cols,
        "formula_params": p,
        "delta_gasto_precio": section_series("delta_gasto_precio",
            ["bolas", "acido", "energia", "combustible", "explosivos", "total"]),
        "delta_rendimiento": section_series("delta_rendimiento",
            ["bolas", "acido", "energia_conc", "energia_hidro", "combustible", "explosivos", "total"]),
        "delta_actividad": section_series("delta_actividad",
            ["trat_concentradora", "trat_hidro", "rec_concentradora", "rec_hidro", "total"]),
        "efecto_ley": section_series("efecto_ley",
            ["concentradora", "hidro", "total"]),
        "efectos_tcrc":  section_series("efectos_tcrc",  ["cantidades", "tarifas", "total"]),
        "efectos_comer": section_series("efectos_comer", ["cantidades", "tarifas", "total"]),
        "desarrollo_mina": {
            "items": [
                {
                    "key":   it["key"],
                    "label": it["label"],
                    "vals":  [d.get(it["key"]) for d in col_dev_items],
                    "mes":   dev_mes_items.get(it["key"]),
                    "ytd":   dev_ytd_items.get(it["key"]),
                }
                for it in dm_items
            ],
            "total": {
                "vals": [_sum_or_none([d.get(it["key"]) for it in dm_items]) for d in col_dev_items],
                "mes":  _sum_or_none([dev_mes_items.get(it["key"]) for it in dm_items]),
                "ytd":  _sum_or_none([dev_ytd_items.get(it["key"]) for it in dm_items]),
            },
        },
        "variacion_inventario": {
            "vals": col_varinv, "mes": varinv_mes, "ytd": varinv_ytd,
        },
        "actividad_contexto": {
            k: {
                "vals": [d[k] for d in col_act_ctx],
                "mes":  act_ctx_mes[k],
                "ytd":  act_ctx_ytd[k],
            }
            for k in ["mov_mina", "actividad_mina", "actividad_conc", "actividad_hidro"]
        },
        "efecto_precio_subprod":   subprod_effects["efecto_precio"],
        "efecto_cantidad_subprod": subprod_effects["efecto_cantidad"],
        "creditos_subprod":        subprod_effects["creditos"],
        "tcrc": tcrc_data,
    }
