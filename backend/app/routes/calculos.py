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

router = APIRouter(prefix="/api/calculos", tags=["calculos"])

MONTH_FIELDS = pq.MONTH_FIELDS
MONTH_BY_NUM = {i + 1: f for i, f in enumerate(MONTH_FIELDS)}

_TIPO_ALIASES: list[set[str]] = [
    {"ppto", "plan", "presupuesto", "budget"},
    {"real", "actual"},
]

FORMULA_PARAMS_PATH = Path(__file__).resolve().parents[3] / "data" / "formula_params.json"
PARAMS_FIN_PATH     = Path(__file__).resolve().parents[3] / "data" / "params_financieros.json"

DEFAULT_PARAMS: dict = {
    "kpi_keys": {
        "tarifa_bolas":        "Tarifa Bolas",
        "tarifa_energia":      "Energía all in",
        "tarifa_combustible":  "Combustible",
        "tarifa_explosivos":   "Precios Insumos Críticos||Explosivos",
        "consumo_bolas":       "Consumo Bolas",
        "consumo_energia":     "Consumo Energía Compañía",
        "consumo_combustible": "Mina||Consumo Comb. - Transporte",
        "consumo_explosivos":  "Mina||Explosivos",
        "tratamiento":         "Tratamiento",
        "ley_cu":              "Ley de Cu",
        "recuperacion":        "Recuperación de cobre",
        "horas_efectivas":     "Mina||Horas Efectivas",
        "tronadura":           ["Roca Quebrada (Lastre)", "Roca Quebrada (Mineral)"],
    },
    "desarrollo_mina":      ["Desarrollo Mina||Desarrollo Mina"],
    "desarrollo_mina_items": [
        {"key": "ifric20",  "label": "IFRIC20",  "kpis": []},
        {"key": "efecto_p", "label": "Efecto P",  "kpis": []},
        {"key": "efecto_q", "label": "Efecto Q",  "kpis": []},
    ],
    "variacion_inventario": ["Var. Inv. Mina", "Var. Inv. Planta"],
    "actividad_contexto": {
        "mov_mina_kpis":        ["Movimiento Mina"],
        "procesamiento_kpi":    "Tratamiento",
        "costo_unitario_mina":  None,
        "costo_unitario_conc":  None,
        "costo_unitario_hidro": None,
    },
}


def _load_params() -> dict:
    if FORMULA_PARAMS_PATH.exists():
        saved = json.loads(FORMULA_PARAMS_PATH.read_text(encoding="utf-8"))
        result: dict = {**DEFAULT_PARAMS}
        if "kpi_keys" in saved:
            result["kpi_keys"] = {**DEFAULT_PARAMS["kpi_keys"], **saved["kpi_keys"]}
        for k in ("desarrollo_mina", "variacion_inventario"):
            if k in saved:
                result[k] = saved[k]
        if "desarrollo_mina_items" in saved:
            result["desarrollo_mina_items"] = saved["desarrollo_mina_items"]
        if "actividad_contexto" in saved:
            result["actividad_contexto"] = {
                **DEFAULT_PARAMS["actividad_contexto"],
                **saved["actividad_contexto"],
            }
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
    periodo  = year * 100 + month
    variants = _tipo_variants(tipo)
    df = df_co[
        (df_co["periodo"] == periodo)
        & (df_co["tipo"].str.strip().str.lower().isin(variants))
    ]
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


def _resolve(snapshot: dict, src: str, field: str):
    data = snapshot.get(src)
    if data is None:
        suffix = f"||{src}"
        matches = [v for k, v in snapshot.items() if k.endswith(suffix)]
        data = matches[0] if len(matches) == 1 else None
    if data is None:
        return None
    val = data.get(field)
    return float(val) if val is not None else None


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

    def r(key): return _resolve(r_snap, key, field)
    def b(key): return _resolve(b_snap, key, field)

    tron_keys = k["tronadura"] if isinstance(k["tronadura"], list) else [k["tronadura"]]

    proc_r = r(k["tratamiento"]);          proc_b = b(k["tratamiento"])
    _ley_r = r(k["ley_cu"]);               _ley_b = b(k["ley_cu"])
    _rec_r = r(k["recuperacion"]);         _rec_b = b(k["recuperacion"])
    # ley stored as % (e.g. 0.52), rec stored as % (e.g. 89.7) → convert to fractions
    ley_r  = _ley_r / 100 if _ley_r is not None else None
    ley_b  = _ley_b / 100 if _ley_b is not None else None
    rec_r  = _rec_r / 100 if _rec_r is not None else None
    rec_b  = _rec_b / 100 if _rec_b is not None else None
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

    rend_bolas_r = _m(_safe_div(con_bolas_r, proc_r), 1000.0)
    rend_bolas_b = _m(_safe_div(con_bolas_b, proc_b), 1000.0)
    rend_energ_r = _safe_div(con_energ_r, proc_r)
    rend_energ_b = _safe_div(con_energ_b, proc_b)
    rend_comb_r  = _safe_div(con_comb_r,  hef_r)
    rend_comb_b  = _safe_div(con_comb_b,  hef_b)
    rend_expl_r  = _m(_safe_div(con_expl_r, tron_r), 1000.0)
    rend_expl_b  = _m(_safe_div(con_expl_b, tron_b), 1000.0)

    # Delta Gasto (Precio)
    dg_bolas = _m(_n(tar_bolas_r, tar_bolas_b), con_bolas_r, 1/1000) if con_bolas_r else None
    dg_acido = None
    dg_energ = _m(_n(tar_energ_r, tar_energ_b), con_energ_r, 1/1000) if con_energ_r else None
    dg_comb  = _m(_n(tar_comb_r,  tar_comb_b),  con_comb_r)          if con_comb_r  else None
    dg_expl  = _m(_n(tar_expl_r,  tar_expl_b),  con_expl_r, 1/1000)  if con_expl_r  else None
    vals_dg  = [dg_bolas, dg_acido, dg_energ, dg_comb, dg_expl]
    dg_total = sum(_s(x) for x in vals_dg if x is not None) or None

    # Delta Rendimiento
    dr_bolas   = _m(_n(rend_bolas_r, rend_bolas_b), proc_r,  1000.0, tar_bolas_b, 1/1e9) if proc_r else None
    dr_acido   = None
    dr_energ_c = _m(_n(rend_energ_r, rend_energ_b), proc_r,  1000.0, tar_energ_b, 1/1e6) if proc_r else None
    dr_energ_h = None
    dr_comb    = _m(_n(rend_comb_r,  rend_comb_b),  hef_r,           tar_comb_b)          if hef_r  else None
    dr_expl    = _m(_n(rend_expl_r,  rend_expl_b),  tron_r,          tar_expl_b,  1/1e6)  if tron_r else None
    vals_dr    = [dr_bolas, dr_acido, dr_energ_c, dr_energ_h, dr_comb, dr_expl]
    dr_total   = sum(_s(x) for x in vals_dr if x is not None) or None

    # Delta Actividad
    dp = _n(proc_r, proc_b); dl = _n(ley_r, ley_b); dr = _n(rec_r, rec_b)
    da_trat_c = _delta_act_trat(dp, dl, dr, ley_b, rec_b)
    da_trat_h = None
    da_rec_c  = _delta_act_rec(dp, dl, dr, proc_b, ley_b)
    da_rec_h  = None
    vals_da   = [da_trat_c, da_trat_h, da_rec_c, da_rec_h]
    da_total  = sum(_s(x) for x in vals_da if x is not None) or None

    # Efecto Ley
    el_conc  = _efecto_ley(dp, dl, dr, proc_b, rec_b)
    el_hidro = None
    vals_el  = [el_conc, el_hidro]
    el_total = sum(_s(x) for x in vals_el if x is not None) or None

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


def _compute_dm_item(r_snap: dict, b_snap: dict, field: str, item: dict) -> float | None:
    kpis = item.get("kpis", [])
    if not kpis:
        return None
    rv = _sum_resolve(r_snap, kpis, field)
    bv = _sum_resolve(b_snap, kpis, field)
    v  = _n(rv, bv)
    return round(v, 4) if v is not None else None


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

    def _act(delta, cu):
        if delta is None or cu is None or tc_factor is None:
            return None
        return _m(delta, cu, tc_factor)

    fmt = lambda v: round(v, 4) if v is not None else None
    return {
        "mov_mina":       fmt(delta_mov),
        "actividad_mina": fmt(_act(delta_mov,  cu_mina)),
        "actividad_conc": fmt(_act(delta_proc, cu_conc)),
        "actividad_hidro": None,
    }


# ── endpoints ──────────────────────────────────────────────────────────────

@router.get("/formula-params")
def get_formula_params():
    return _load_params()


class FormulaParamsBody(BaseModel):
    kpi_keys: dict
    desarrollo_mina: list
    desarrollo_mina_items: list = []
    variacion_inventario: list
    actividad_contexto: dict = {}


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
        col_deltas.append(_compute_deltas(rs, bs, field, p))
        col_dev.append(_compute_simple_delta(rs, bs, field, p["desarrollo_mina"]))
        col_dev_items.append({it["key"]: _compute_dm_item(rs, bs, field, it) for it in dm_items})
        col_varinv.append(_compute_simple_delta(rs, bs, field, p["variacion_inventario"]))
        col_act_ctx.append(_compute_actividad_contexto(rs, bs, field, p, _tc_factor(yr, mo)))

    # Mes + YTD
    end_r = real_snaps.get(año_fin, {})
    end_b = budget_snaps.get(año_fin, {})
    mes_deltas = _compute_deltas(end_r, end_b, "mes", p)
    ytd_deltas = _compute_deltas(end_r, end_b, "ytd", p)

    # YTD for rate-based sections = sum of monthly cols
    def _sum_cols(section: str, key: str):
        total, has = 0.0, False
        for cd in col_deltas:
            v = cd[section].get(key)
            if v is not None:
                total += v; has = True
        return round(total, 4) if has else None

    ytd_gasto_override = {
        k: _sum_cols("delta_gasto_precio", k)
        for k in ["bolas", "acido", "energia", "combustible", "explosivos", "total"]
    }
    ytd_rend_override = {
        k: _sum_cols("delta_rendimiento", k)
        for k in ["bolas", "acido", "energia_conc", "energia_hidro", "combustible", "explosivos", "total"]
    }

    # Simple delta Mes/YTD
    dev_mes    = _compute_simple_delta(end_r, end_b, "mes", p["desarrollo_mina"])
    dev_ytd    = _compute_simple_delta(end_r, end_b, "ytd", p["desarrollo_mina"])
    dev_mes_items = {it["key"]: _compute_dm_item(end_r, end_b, "mes", it) for it in dm_items}
    dev_ytd_items = {it["key"]: _compute_dm_item(end_r, end_b, "ytd", it) for it in dm_items}
    varinv_mes = _compute_simple_delta(end_r, end_b, "mes", p["variacion_inventario"])
    varinv_ytd = _compute_simple_delta(end_r, end_b, "ytd", p["variacion_inventario"])

    # Actividad contexto Mes/YTD
    act_ctx_mes = _compute_actividad_contexto(end_r, end_b, "mes", p, _tc_factor(año_fin, mes_fin))
    act_ctx_ytd = {
        k: _sum_or_none([d[k] for d in col_act_ctx])
        for k in ["mov_mina", "actividad_mina", "actividad_conc", "actividad_hidro"]
    }

    def build_series(section: str, key: str) -> dict:
        if section == "delta_gasto_precio":
            ytd_val = ytd_gasto_override[key]
        elif section == "delta_rendimiento":
            ytd_val = ytd_rend_override[key]
        else:
            ytd_val = ytd_deltas[section][key]
        return {
            "vals": [cd[section][key] for cd in col_deltas],
            "mes":  mes_deltas[section][key],
            "ytd":  ytd_val,
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
    }
