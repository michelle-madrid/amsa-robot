"""
Explicaciones tab — Real, Ppto, Efecto per company for a given año/mes.
"""
import json
from fastapi import APIRouter, HTTPException

from ..config import settings
from ..services import parquet_service as pq
from .calculos import (
    _load_params, _apply_formula_var_mappings, PARAMS_FIN_PATH,
    _fetch_snapshot, _resolve, _sum_resolve,
    _safe_div, _n, _m, _compute_deltas, _compute_actividad_contexto,
)

router = APIRouter(prefix="/api/explicaciones", tags=["explicaciones"])


@router.get("/{company}")
def get_explicaciones(company: str, año: int, mes: int):
    if not settings.parquet_path.exists():
        raise HTTPException(404, "Parquet no encontrado")

    p = _load_params()
    per_co_keys = p.get("kpi_keys_per_company", {}).get(company, {})
    if per_co_keys:
        p["kpi_keys"] = {**p["kpi_keys"], **per_co_keys}
    p["kpi_keys"] = _apply_formula_var_mappings(p["kpi_keys"], company)
    params_fin: dict = {}
    if PARAMS_FIN_PATH.exists():
        params_fin = json.loads(PARAMS_FIN_PATH.read_text(encoding="utf-8"))

    exp_tc_frac = params_fin.get("exp_tc", {}).get(company, 0) / 100.0
    year_kpis = params_fin.get("kpis", {}).get(str(año), {})

    def _avg_fin(key: str):
        vals = [
            float(year_kpis[str(m)][key])
            for m in range(1, mes + 1)
            if str(m) in year_kpis and year_kpis[str(m)].get(key) is not None
        ]
        return sum(vals) / len(vals) if vals else None

    mo_mes = year_kpis.get(str(mes), {})
    mo_ytd = {
        "dolar_real":   _avg_fin("dolar_real"),
        "dolar_budget": _avg_fin("dolar_budget"),
        "ipc_real":     _avg_fin("ipc_real"),
        "ipc_budget":   _avg_fin("ipc_budget"),
        "cpi_real":     _avg_fin("cpi_real"),
        "cpi_budget":   _avg_fin("cpi_budget"),
    }

    def _tc_factor(mo: dict) -> float | None:
        dr = mo.get("dolar_real")
        db = mo.get("dolar_budget")
        if dr and db and float(dr) != 0:
            return exp_tc_frac * float(db) / float(dr) + (1.0 - exp_tc_frac)
        return None

    df_all = pq.get_cached_df(settings.parquet_path)
    df_co  = df_all[df_all["compania"] == company]

    r_snap = _fetch_snapshot(df_co, "real", año, mes)
    b_snap = _fetch_snapshot(df_co, "plan", año, mes)

    k = p["kpi_keys"]

    panels: dict = {}
    for panel in ("mes", "ytd"):
        mo_fin     = mo_mes if panel == "mes" else mo_ytd
        tc_factor  = _tc_factor(mo_fin)
        deltas = _compute_deltas(r_snap, b_snap, panel, p)
        dg  = deltas["delta_gasto_precio"]
        dr  = deltas["delta_rendimiento"]
        da  = deltas["delta_actividad"]
        el  = deltas["efecto_ley"]
        ac  = _compute_actividad_contexto(r_snap, b_snap, panel, p, tc_factor)

        def rv(key, _p=panel): return _resolve(r_snap, key, _p) if key else None
        def bv(key, _p=panel): return _resolve(b_snap, key, _p) if key else None

        proc_r = rv(k["tratamiento"]);         proc_b = bv(k["tratamiento"])
        ley_rr = rv(k["ley_cu"]);              ley_rb = bv(k["ley_cu"])
        rec_rr = rv(k["recuperacion"]);        rec_rb = bv(k["recuperacion"])
        hef_r  = rv(k["horas_efectivas"]);     hef_b  = bv(k["horas_efectivas"])
        tron_keys = k["tronadura"] if isinstance(k["tronadura"], list) else [k["tronadura"]]
        tron_r = _sum_resolve(r_snap, tron_keys, panel)
        tron_b = _sum_resolve(b_snap, tron_keys, panel)

        rend_bolas_r = _m(_safe_div(rv(k["consumo_bolas"]),       proc_r), 1000.0)
        rend_bolas_b = _m(_safe_div(bv(k["consumo_bolas"]),       proc_b), 1000.0)
        rend_energ_r = _safe_div(rv(k["consumo_energia"]),         proc_r)
        rend_energ_b = _safe_div(bv(k["consumo_energia"]),         proc_b)
        rend_comb_r  = _safe_div(rv(k["consumo_combustible"]),     hef_r)
        rend_comb_b  = _safe_div(bv(k["consumo_combustible"]),     hef_b)
        rend_expl_r  = _m(_safe_div(rv(k["consumo_explosivos"]),   tron_r), 1000.0)
        rend_expl_b  = _m(_safe_div(bv(k["consumo_explosivos"]),   tron_b), 1000.0)

        # Cu production in ktCuf: proc(kt) × ley(%)/100 × rec(%)/100  →  × 1e-4
        _all = lambda *args: None not in args
        cu_r = _m(proc_r, ley_rr, rec_rr, 1e-4) if _all(proc_r, ley_rr, rec_rr) else None
        cu_b = _m(proc_b, ley_rb, rec_rb, 1e-4) if _all(proc_b, ley_rb, rec_rb) else None

        mov_kpis = p.get("actividad_contexto", {}).get("mov_mina_kpis", [])
        mov_r = _sum_resolve(r_snap, mov_kpis, panel) if mov_kpis else None
        mov_b = _sum_resolve(b_snap, mov_kpis, panel) if mov_kpis else None
        dm_r  = _sum_resolve(r_snap, p["desarrollo_mina"], panel) if p.get("desarrollo_mina") else None
        dm_b  = _sum_resolve(b_snap, p["desarrollo_mina"], panel) if p.get("desarrollo_mina") else None
        vi_r  = _sum_resolve(r_snap, p["variacion_inventario"], panel) if p.get("variacion_inventario") else None
        vi_b  = _sum_resolve(b_snap, p["variacion_inventario"], panel) if p.get("variacion_inventario") else None

        dev_vol_key = k.get("dev_mina_vol", "")
        dev_vol_r = rv(dev_vol_key) if dev_vol_key else None
        dev_vol_b = bv(dev_vol_key) if dev_vol_key else None
        cu_dev_r = (dm_r / dev_vol_r) if (dev_vol_r and dm_r is not None and dev_vol_r != 0) else None
        cu_dev_b = (dm_b / dev_vol_b) if (dev_vol_b and dm_b is not None and dev_vol_b != 0) else None

        f = lambda v: round(v, 4) if v is not None else None

        dev_ton_kus = f(_m(_n(dev_vol_r, dev_vol_b), cu_dev_b)) if (cu_dev_b is not None and dev_vol_r is not None and dev_vol_b is not None) else None
        dev_cu_kus  = f(_m(_n(cu_dev_r, cu_dev_b), dev_vol_r)) if (cu_dev_r is not None and cu_dev_b is not None and dev_vol_r is not None) else None

        # Ajuste monetario effects: need total operational cost budget as base
        gasto_key  = k.get("gasto_operacional", "")
        gasto_b    = bv(gasto_key) if gasto_key else None

        tc_r_val   = mo_fin.get("dolar_real")
        tc_b_val   = mo_fin.get("dolar_budget")
        ipc_r_val  = mo_fin.get("ipc_real")
        ipc_b_val  = mo_fin.get("ipc_budget")
        cpi_r_val  = mo_fin.get("cpi_real")
        cpi_b_val  = mo_fin.get("cpi_budget")

        def _aj_efecto(base, frac, ratio_num, ratio_den):
            """base (kUS$) × frac × (ratio_num/ratio_den − 1), or None if any input missing."""
            if base is None or frac is None or not ratio_num or not ratio_den or float(ratio_den) == 0:
                return None
            return base * frac * (float(ratio_num) / float(ratio_den) - 1.0)

        ef_tc  = f(_aj_efecto(gasto_b, exp_tc_frac,        tc_b_val,  tc_r_val))
        ef_ipc = f(_aj_efecto(gasto_b, exp_tc_frac,        ipc_r_val, ipc_b_val))
        ef_cpi = f(_aj_efecto(gasto_b, 1.0 - exp_tc_frac, cpi_r_val, cpi_b_val))

        # Real operational cost (for eficiencia/desfases residual)
        gasto_r_key = k.get("gasto_operacional_real", "")
        gasto_r     = rv(gasto_r_key) if gasto_r_key else None

        # Upper cost structure (C3 → C2 → D&A → C1 → Subprod → Pre-credit → TC/RC_b → Comer_b)
        _bk = lambda key: bv(key) if key else None
        c3_b_val        = _bk(k.get("c3",            ""))
        c2_b_val        = _bk(k.get("c2",            ""))
        c1_abs_b_val    = _bk(k.get("c1_abs",        ""))
        da_b_val        = _bk(k.get("da",            ""))
        subprod_b_val   = _bk(k.get("subprod_b",     ""))
        tcrc_b_tot_val  = _bk(k.get("tcrc_b_total",  ""))
        comer_b_tot_val = _bk(k.get("comer_b_total", ""))

        # Variación inventario (real − budget, ktCuf delta)
        vi_delta = f(_n(vi_r, vi_b))

        # Efecto inventarios kUS$ (real − budget por segmento)
        vi_mina_key   = k.get("vi_inv_mina",   "")
        vi_planta_key = k.get("vi_inv_planta",  "")
        vi_mina_kus   = f(_n(rv(vi_mina_key),   bv(vi_mina_key)))   if vi_mina_key   else None
        vi_planta_kus = f(_n(rv(vi_planta_key), bv(vi_planta_key))) if vi_planta_key else None

        # IFRS16 kUS$ (real − budget)
        ifrs16_key = k.get("ifrs16", "")
        ifrs16_kus = f(_n(rv(ifrs16_key), bv(ifrs16_key))) if ifrs16_key else None

        # Estructura real (valores absolutos del snapshot real)
        _rk = lambda key: f(rv(key)) if key else None
        tcrc_r_val        = _rk(k.get("tcrc_r_total",   ""))
        comer_r_val       = _rk(k.get("comer_r_total",  ""))
        subprod_r_val     = _rk(k.get("subprod_r",      ""))
        c1_r_costo_val    = _rk(k.get("c1_r_costo",     ""))
        da_r_val          = _rk(k.get("da_r",           ""))
        c2_r_val          = _rk(k.get("c2_r",           ""))
        c3_r_val          = _rk(k.get("c3_r",           ""))
        otros_fin_r_val   = _rk(k.get("otros_fin_r",    ""))
        dif_cambio_r_val  = _rk(k.get("dif_cambio_r",   ""))
        donaciones_r_val  = _rk(k.get("donaciones_r",   ""))
        gasto_expl_r_val  = _rk(k.get("gasto_expl_r",   ""))
        ing_gasto_fin_r_val = _rk(k.get("ing_gasto_fin_r", ""))
        ing_no_op_r_val   = _rk(k.get("ing_no_op_r",    ""))
        otros_egr_op_r_val = _rk(k.get("otros_egr_op_r", ""))

        # TC/RC and Comercialización total effects (kUS$)
        tcrc_kus  = f(deltas["efectos_tcrc"]["total"])
        comer_kus = f(deltas["efectos_comer"]["total"])

        panels[panel] = {
            "cu_prod_real":   f(cu_r),
            "cu_prod_budget": f(cu_b),
            "ajuste_monetario": [
                {"label": "TC (Dólar)", "unit": "CLP/USD", "real": f(tc_r_val),  "ppto": f(tc_b_val),  "efecto_kus": ef_tc},
                {"label": "IPC",        "unit": "Índice",  "real": f(ipc_r_val), "ppto": f(ipc_b_val), "efecto_kus": ef_ipc},
                {"label": "CPI",        "unit": "Índice",  "real": f(cpi_r_val), "ppto": f(cpi_b_val), "efecto_kus": ef_cpi},
            ],
            "precio_insumos": [
                {"label": "Energía",     "unit": "US$/MWh", "real": f(rv(k["tarifa_energia"])),     "ppto": f(bv(k["tarifa_energia"])),     "efecto_kus": f(dg["energia"])},
                {"label": "Combustible", "unit": "US$/lt",  "real": f(rv(k["tarifa_combustible"])), "ppto": f(bv(k["tarifa_combustible"])), "efecto_kus": f(dg["combustible"])},
                {"label": "Ácido",       "unit": "US$/t",   "real": None,                           "ppto": None,                           "efecto_kus": f(dg["acido"])},
                {"label": "Bolas",       "unit": "US$/t",   "real": f(rv(k["tarifa_bolas"])),       "ppto": f(bv(k["tarifa_bolas"])),       "efecto_kus": f(dg["bolas"])},
                {"label": "Explosivos",  "unit": "US$/kg",  "real": f(rv(k["tarifa_explosivos"])),  "ppto": f(bv(k["tarifa_explosivos"])),  "efecto_kus": f(dg["explosivos"])},
            ],
            "rendimiento": [
                {"label": "Energía",     "unit": "MWh/kt", "real": f(rend_energ_r), "ppto": f(rend_energ_b), "efecto_kus": f(dr["energia_conc"])},
                {"label": "Combustible", "unit": "lt/hr",  "real": f(rend_comb_r),  "ppto": f(rend_comb_b),  "efecto_kus": f(dr["combustible"])},
                {"label": "Ácido",       "unit": "t/t",    "real": None,            "ppto": None,            "efecto_kus": f(dr["acido"])},
                {"label": "Bolas",       "unit": "g/t",    "real": f(rend_bolas_r), "ppto": f(rend_bolas_b), "efecto_kus": f(dr["bolas"])},
                {"label": "Explosivos",  "unit": "g/t",    "real": f(rend_expl_r),  "ppto": f(rend_expl_b),  "efecto_kus": f(dr["explosivos"])},
            ],
            "produccion": [
                {"label": "Ley Conc",        "unit": "% CuT", "real": f(ley_rr), "ppto": f(ley_rb), "efecto_ktcuf": f(el["concentradora"])},
                {"label": "Tratamiento Conc","unit": "kt",    "real": f(proc_r), "ppto": f(proc_b), "efecto_ktcuf": f(da["trat_concentradora"])},
                {"label": "Rec Conc",        "unit": "%",     "real": f(rec_rr), "ppto": f(rec_rb), "efecto_ktcuf": f(da["rec_concentradora"])},
                {"label": "Ley Hidro",       "unit": "% CuT", "real": None, "ppto": None, "efecto_ktcuf": None},
                {"label": "Beneficio Hidro", "unit": "kt",    "real": None, "ppto": None, "efecto_ktcuf": f(da["trat_hidro"])},
                {"label": "Rec Hidro",       "unit": "%",     "real": None, "ppto": None, "efecto_ktcuf": f(da["rec_hidro"])},
                {"label": "Otros",           "unit": "kt",    "real": None, "ppto": None, "efecto_ktcuf": None},
                {"label": "Inventarios",     "unit": "kt",    "real": f(vi_r), "ppto": f(vi_b), "efecto_ktcuf": None},
            ],
            "mov_mina": {
                "real": f(mov_r), "ppto": f(mov_b),
                "efecto_kus": f(ac["actividad_mina"]),
            },
            "desarrollo_mina": {
                "real": f(dm_r), "ppto": f(dm_b),
                "efecto_kus": f(_n(dm_r, dm_b)),
                "tonelaje_real": f(abs(dev_vol_r)) if dev_vol_r is not None else None,
                "tonelaje_ppto": f(abs(dev_vol_b)) if dev_vol_b is not None else None,
                "tonelaje_efecto_kus": dev_ton_kus,
                "cu_real": f(abs(cu_dev_r)) if cu_dev_r is not None else None,
                "cu_ppto": f(abs(cu_dev_b)) if cu_dev_b is not None else None,
                "cu_efecto_kus": dev_cu_kus,
            },
            "waterfall": {
                "c1_b":  f(gasto_b),
                "c1_r":  f(gasto_r),
                # Estructura superior (contexto ppto)
                "c3_b":         f(c3_b_val),
                "c2_b":         f(c2_b_val),
                "c1_abs_b":     f(c1_abs_b_val),
                "da_b":         f(da_b_val),
                "subprod_b":    f(subprod_b_val),
                "tcrc_b_total": f(tcrc_b_tot_val),
                "comer_b_total":f(comer_b_tot_val),
                # Ajuste monetario (kUS$)
                "aj_tc":  ef_tc,
                "aj_ipc": ef_ipc,
                "aj_cpi": ef_cpi,
                # Precio insumos (kUS$)
                "pr_energia":     f(dg["energia"]),
                "pr_combustible": f(dg["combustible"]),
                "pr_acido":       f(dg["acido"]),
                "pr_bolas":       f(dg["bolas"]),
                "pr_explosivos":  f(dg["explosivos"]),
                # Efecto ley (ktCuf, production)
                "ley_conc":  f(el["concentradora"]),
                "ley_hidro": f(el["hidro"]),
                # Efecto actividad — cost effects (kUS$)
                "act_mina_kus":  f(ac["actividad_mina"]),
                "act_conc_kus":  f(ac["actividad_conc"]),
                # Efecto Producción Cu — production effects (ktCuf)
                "da_trat_conc": f(da["trat_concentradora"]),
                "da_trat_hidro": f(da["trat_hidro"]),
                "da_rec_conc":  f(da["rec_concentradora"]),
                "da_rec_hidro": f(da["rec_hidro"]),
                "varinv":       vi_delta,
                # Rendimiento insumos (kUS$)
                "rend_energia_conc":  f(dr["energia_conc"]),
                "rend_energia_hidro": f(dr["energia_hidro"]),
                "rend_combustible":   f(dr["combustible"]),
                "rend_acido":         f(dr["acido"]),
                "rend_bolas":         f(dr["bolas"]),
                "rend_explosivos":    f(dr["explosivos"]),
                # Desarrollo mina (kUS$) con apertura P/Q
                "dev_mina":     f(_n(dm_r, dm_b)),
                "dev_mina_cu":  dev_cu_kus,
                "dev_mina_ton": dev_ton_kus,
                # TC/RC & Comer effects (kUS$) — delta real vs ppto
                "tcrc_kus":  tcrc_kus,
                "comer_kus": comer_kus,
                # Efecto inventarios (kUS$ delta)
                "vi_inv_mina_kus":   vi_mina_kus,
                "vi_inv_planta_kus": vi_planta_kus,
                # IFRS16 (kUS$ delta)
                "ifrs16_kus": ifrs16_kus,
                # Estructura real (valores absolutos)
                "tcrc_r_total":    tcrc_r_val,
                "comer_r_total":   comer_r_val,
                "subprod_r":       subprod_r_val,
                "c1_r_costo":      c1_r_costo_val,
                "da_r":            da_r_val,
                "c2_r":            c2_r_val,
                "c3_r":            c3_r_val,
                "otros_fin_r":     otros_fin_r_val,
                "dif_cambio_r":    dif_cambio_r_val,
                "donaciones_r":    donaciones_r_val,
                "gasto_expl_r":    gasto_expl_r_val,
                "ing_gasto_fin_r": ing_gasto_fin_r_val,
                "ing_no_op_r":     ing_no_op_r_val,
                "otros_egr_op_r":  otros_egr_op_r_val,
            },
        }

    return {"company": company, "año": año, "mes": mes, "data": panels}
