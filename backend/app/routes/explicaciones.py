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
from .costos_ajustados import get_costos_ajustados, get_costos_ajustados_monthly

router = APIRouter(prefix="/api/explicaciones", tags=["explicaciones"])


@router.get("/{company}")
def get_explicaciones(company: str, año: int, mes: int):
    if not settings.parquet_path.exists():
        raise HTTPException(404, "Parquet no encontrado")

    p = _load_params()
    per_co_keys = p.get("kpi_keys_per_company", {}).get(company, {})
    if per_co_keys:
        p["kpi_keys"] = {**p["kpi_keys"], **per_co_keys}
    p["kpi_keys"]           = _apply_formula_var_mappings(p["kpi_keys"], company)
    p["kpi_scales"]         = p.get("kpi_scales_per_company", {}).get(company, {})
    p["kpi_scales_mes_ytd"] = p.get("kpi_scales_mes_ytd_per_company", {}).get(company, [])
    params_fin: dict = {}
    if PARAMS_FIN_PATH.exists():
        params_fin = json.loads(PARAMS_FIN_PATH.read_text(encoding="utf-8"))

    exp_tc_frac = params_fin.get("exp_tc", {}).get(company, 0) / 100.0
    year_kpis = params_fin.get("kpis", {}).get(str(año), {})

    # Onsite cost budget (base for monetary adjustment effects)
    _ca_onsite: dict = {}
    _ca_grupos: dict = {}
    _ca = None
    try:
        _ca = get_costos_ajustados(company=company, año=año, mes=mes)
        _ca_onsite = (_ca or {}).get("totales", {}).get("costo_onsite", {})
        _ca_grupos = {g["key"]: g for g in (_ca.get("grupos") or [])} if _ca else {}
    except Exception:
        pass
    _gasto_b = {
        "mes": (_ca_onsite.get("mes") or {}).get("ppto"),
        "ytd": (_ca_onsite.get("ytd") or {}).get("ppto"),
    }

    # Monetary effects from monthly costos_ajustados (month-by-month, matches Excel)
    _cam_ef: dict[str, dict] = {"mes": {}, "ytd": {}}
    try:
        _cam = get_costos_ajustados_monthly(company=company, año=año, mes_cierre=mes)
        all_grupos    = (_cam or {}).get("grupos") or []
        onsite_grupos = [g for g in all_grupos if not g.get("exclude_from_onsite")]
        def _sg(field, grupos=None):
            t = None
            for g in (grupos if grupos is not None else all_grupos):
                v = (g.get("total") or {}).get(field)
                if v is not None:
                    t = (t or 0.0) + v
            return t
        def _total_onsite_kus(suffix, grupos):
            tc  = _sg(f"ef_tc_{suffix}",  grupos) or 0.0
            ipc = _sg(f"ef_ipc_{suffix}", grupos) or 0.0
            cpi = _sg(f"ef_cpi_{suffix}", grupos) or 0.0
            return tc + ipc + cpi
        _cam_ef = {
            # TC: todos los grupos (incl. vi/dev/ifrs16) — igual que Excel AM20
            # total_onsite: suma (tc+ipc+cpi) solo onsite — numerador de O22 en Excel
            "mes": {
                "tc":            _sg("ef_tc_mes"),
                "ipc":           _sg("ef_ipc_mes",  onsite_grupos),
                "cpi":           _sg("ef_cpi_mes",  onsite_grupos),
                "total_onsite":  _total_onsite_kus("mes",  onsite_grupos),
            },
            "ytd": {
                "tc":            _sg("ef_tc_acum"),
                "ipc":           _sg("ef_ipc_acum", onsite_grupos),
                "cpi":           _sg("ef_cpi_acum", onsite_grupos),
                "total_onsite":  _total_onsite_kus("acum", onsite_grupos),
            },
        }
    except Exception:
        pass

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

    def _scale_snap(snap: dict, panel: str) -> dict:
        """Devuelve snapshot con valores de proc/ley/rec ya escalados, para que
        _compute_deltas opere siempre en unidades correctas (kt, %, %)."""
        scales  = p.get("kpi_scales", {})
        myt_set = set(p.get("kpi_scales_mes_ytd", []))
        if not scales:
            return snap
        result = dict(snap)
        for var, sc in scales.items():
            if var in myt_set and panel not in ("mes", "ytd"):
                continue
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

    panels: dict = {}
    for panel in ("mes", "ytd"):
        mo_fin     = mo_mes if panel == "mes" else mo_ytd
        tc_factor  = _tc_factor(mo_fin)
        r_scaled   = _scale_snap(r_snap, panel)
        b_scaled   = _scale_snap(b_snap, panel)
        _p_calc    = {**p, "kpi_scales": {}, "kpi_scales_mes_ytd": []}
        deltas = _compute_deltas(r_scaled, b_scaled, panel, _p_calc)
        dg  = deltas["delta_gasto_precio"]
        dgc = deltas.get("delta_gasto_precio_calc", {})
        drc = deltas.get("delta_rendimiento_calc", {})
        dr  = deltas["delta_rendimiento"]
        da  = deltas["delta_actividad"]
        el  = deltas["efecto_ley"]

        def rv(key, _p=panel, _snap=r_scaled): return _resolve(_snap, key, _p) if key else None
        def bv(key, _p=panel, _snap=b_scaled): return _resolve(_snap, key, _p) if key else None

        proc_r = rv(k["tratamiento"]); proc_b = bv(k["tratamiento"])
        ley_rr = rv(k["ley_cu"]);      ley_rb = bv(k["ley_cu"])
        rec_rr = rv(k["recuperacion"]); rec_rb = bv(k["recuperacion"])
        hef_r  = rv(k["horas_efectivas"]);     hef_b  = bv(k["horas_efectivas"])
        tron_keys = k["tronadura"] if isinstance(k["tronadura"], list) else [k["tronadura"]]
        tron_r = _sum_resolve(r_snap, tron_keys, panel)
        tron_b = _sum_resolve(b_snap, tron_keys, panel)

        rend_bolas_r = _m(_safe_div(rv(k["consumo_bolas"]),       proc_r), 1000.0)
        rend_bolas_b = _m(_safe_div(bv(k["consumo_bolas"]),       proc_b), 1000.0)
        rend_energ_r = _safe_div(rv(k["consumo_energia"]),         proc_r)
        rend_energ_b = _safe_div(bv(k["consumo_energia"]),         proc_b)
        rend_comb_r  = _m(_safe_div(rv(k["consumo_combustible"]),  hef_r), 1000.0)
        rend_comb_b  = _m(_safe_div(bv(k["consumo_combustible"]),  hef_b), 1000.0)
        rend_expl_r  = _m(_safe_div(rv(k["consumo_explosivos"]),   tron_r), 1000.0)
        rend_expl_b  = _m(_safe_div(bv(k["consumo_explosivos"]),   tron_b), 1000.0)
        # rendimiento bolas: usar KPI directo cuando existe (CEN), si no, calcular
        _rend_bolas_dir_r = rv(k.get("rend_bolas_directo", "")) if k.get("rend_bolas_directo") else None
        _rend_bolas_dir_b = bv(k.get("rend_bolas_directo", "")) if k.get("rend_bolas_directo") else None
        if _rend_bolas_dir_r is not None:
            rend_bolas_r = _rend_bolas_dir_r
            rend_bolas_b = _rend_bolas_dir_b
        # ácido: denominador apilamiento si existe, si no proc
        apil_r = rv(k.get("apilamiento", "")) if k.get("apilamiento") else None
        apil_b = bv(k.get("apilamiento", "")) if k.get("apilamiento") else None
        _acido_den_r = apil_r if apil_r is not None else proc_r
        _acido_den_b = apil_b if apil_b is not None else proc_b
        rend_acido_r = _safe_div(rv(k.get("consumo_acido", "")), _acido_den_r)
        rend_acido_b = _safe_div(bv(k.get("consumo_acido", "")), _acido_den_b)

        # Variables mineras hidro
        ley_hidro_r  = rv(k.get("ley_hidro",       "")) if k.get("ley_hidro")       else None
        ley_hidro_b  = bv(k.get("ley_hidro",       "")) if k.get("ley_hidro")       else None
        ben_hidro_r  = rv(k.get("beneficio_hidro", "")) if k.get("beneficio_hidro") else None
        ben_hidro_b  = bv(k.get("beneficio_hidro", "")) if k.get("beneficio_hidro") else None
        rec_hidro_r  = rv(k.get("rec_hidro",       "")) if k.get("rec_hidro")       else None
        rec_hidro_b  = bv(k.get("rec_hidro",       "")) if k.get("rec_hidro")       else None
        otros_r      = rv(k.get("otros_hidro",     "")) if k.get("otros_hidro")     else None
        otros_b      = bv(k.get("otros_hidro",     "")) if k.get("otros_hidro")     else None

        # Cu production in ktCuf: use actual CuFino KPI when available, fallback to proc×ley×rec
        _all = lambda *args: None not in args
        _cu_fino_r = rv(k.get("cu_fino", "")) if k.get("cu_fino") else None
        _cu_fino_b = bv(k.get("cu_fino", "")) if k.get("cu_fino") else None
        cu_r = _cu_fino_r if _cu_fino_r is not None else (_m(proc_r, ley_rr, rec_rr, 1e-4) if _all(proc_r, ley_rr, rec_rr) else None)
        cu_b = _cu_fino_b if _cu_fino_b is not None else (_m(proc_b, ley_rb, rec_rb, 1e-4) if _all(proc_b, ley_rb, rec_rb) else None)

        _mov_sulf  = k.get("mov_mina_sulf",  "")
        _mov_ox    = k.get("mov_mina_ox",    "")
        _mov_total = k.get("mov_mina_total", "")
        _mov_from_keys = [x for x in [_mov_sulf, _mov_ox] if x]
        if not _mov_from_keys and _mov_total:
            _mov_from_keys = [_mov_total]
        mov_kpis = _mov_from_keys if _mov_from_keys else p.get("actividad_contexto", {}).get("mov_mina_kpis", [])
        mov_r = _sum_resolve(r_snap, mov_kpis, panel) if mov_kpis else None
        mov_b = _sum_resolve(b_snap, mov_kpis, panel) if mov_kpis else None

        # Derivar costo unitario de actividad desde costos_ajustados cuando no configurado.
        # Costo unit. = (Ppto_grupo_kUS$ × %Var) / volumen_ppto  → "Ppto aj Var" unitario.
        # La parte Variable NO se ajusta por FX/IPC, así que el efecto NO lleva tc_factor.
        _fijo_by_key = {}
        for _g in (params_fin.get("costos_fijo_var", {}) or {}).get(company, []):
            if isinstance(_g, dict) and _g.get("key") is not None:
                _fijo_by_key[_g["key"]] = _g.get("fijo")
        def _var_pct(gkey, default=1.0):
            f = _fijo_by_key.get(gkey)
            return (1.0 - f) if f is not None else default
        _act_ctx = dict(p.get("actividad_contexto", {}))
        # Asegurar que el cálculo de actividad use los KPIs de Mov Mina ya resueltos
        if not _act_ctx.get("mov_mina_kpis") and mov_kpis:
            _act_ctx["mov_mina_kpis"] = mov_kpis
        if _ca_grupos:
            def _ca_ppto(gkey): return (_ca_grupos.get(gkey) or {}).get("total", {}).get(panel, {}).get("ppto")
            # Costo unit. = (Ppto_grupo × %Var) / volumen_ppto (key Costos Ajustados ≠ key %fijo)
            if _act_ctx.get("costo_unitario_mina") is None and mov_b and _ca_ppto("mina"):
                _act_ctx["costo_unitario_mina"] = _ca_ppto("mina") * _var_pct("mina") / mov_b
            if _act_ctx.get("costo_unitario_conc") is None and proc_b and _ca_ppto("planta_conc"):
                _act_ctx["costo_unitario_conc"] = _ca_ppto("planta_conc") * _var_pct("planta") / proc_b
            if _act_ctx.get("costo_unitario_hidro") is None and ben_hidro_b and _ca_ppto("planta_sx"):
                _act_ctx["costo_unitario_hidro"] = _ca_ppto("planta_sx") * _var_pct("planta_sx") / ben_hidro_b
        _p_act = {**_p_calc, "actividad_contexto": _act_ctx}
        # tc_factor=1.0 → la parte Variable no se ajusta por FX/IPC (fórmula del Excel)
        ac = _compute_actividad_contexto(r_scaled, b_scaled, panel, _p_act, 1.0)
        # Override Actividad Mina con mov ya resuelto de forma fiable (la resolución
        # interna de _compute_actividad_contexto no encuentra "Movimiento Total"):
        #   Actividad Mina = (Mov Real − Mov Ppto) × Costo Ajustado Mina (Ppto aj Var)
        _cu_mina = _act_ctx.get("costo_unitario_mina")
        if mov_r is not None and mov_b is not None and _cu_mina is not None:
            ac["mov_mina"]       = round(mov_r - mov_b, 4)
            ac["actividad_mina"] = round((mov_r - mov_b) * _cu_mina, 4)

        # Desarrollo mina: volume (kt) — usa keys mapeados si existen, si no el hardcodeado
        _dv_sulf  = k.get("dev_mina_sulf",      "")
        _dv_ox    = k.get("dev_mina_ox",         "")
        _dv_total = k.get("dev_mina_total_vol",  "")
        _dv_from_keys = [x for x in [_dv_sulf, _dv_ox] if x]
        if not _dv_from_keys and _dv_total:
            _dv_from_keys = [_dv_total]
        if _dv_from_keys:
            _dv_keys = _dv_from_keys
        else:
            _dv_raw  = k.get("dev_mina_vol", "")
            _dv_keys = _dv_raw if isinstance(_dv_raw, list) else ([_dv_raw] if _dv_raw else [])
        dev_vol_r = _sum_resolve(r_snap, _dv_keys, panel) if _dv_keys else None
        dev_vol_b = _sum_resolve(b_snap, _dv_keys, panel) if _dv_keys else None

        dev_cu_key = k.get("dev_mina_cu", "")
        cu_dev_r   = rv(dev_cu_key) if dev_cu_key else None
        cu_dev_b   = bv(dev_cu_key) if dev_cu_key else None

        # Fallback: derive CU from old desarrollo_mina cost / volume
        if cu_dev_r is None and dev_vol_r:
            _dm_r_fb = _sum_resolve(r_snap, p.get("desarrollo_mina") or [], panel)
            cu_dev_r = (_dm_r_fb / dev_vol_r) if (_dm_r_fb is not None and dev_vol_r != 0) else None
        if cu_dev_b is None and dev_vol_b:
            _dm_b_fb = _sum_resolve(b_snap, p.get("desarrollo_mina") or [], panel)
            cu_dev_b = (_dm_b_fb / dev_vol_b) if (_dm_b_fb is not None and dev_vol_b != 0) else None

        # Total cost (kUS$) = volume (kt) × unit cost (US$/t = kUS$/kt)
        dm_r = (dev_vol_r * cu_dev_r) if (dev_vol_r is not None and cu_dev_r is not None) else None
        dm_b = (dev_vol_b * cu_dev_b) if (dev_vol_b is not None and cu_dev_b is not None) else None

        f = lambda v: round(v, 4) if v is not None else None

        # Effects: ppto − real (positive = favorable, spent less than planned)
        dev_ton_kus = f(_m(_n(dev_vol_b, dev_vol_r), cu_dev_b)) if (cu_dev_b is not None and dev_vol_r is not None and dev_vol_b is not None) else None
        dev_cu_kus  = f(_m(_n(cu_dev_b, cu_dev_r), dev_vol_r)) if (cu_dev_r is not None and cu_dev_b is not None and dev_vol_r is not None) else None

        # Ajuste monetario effects: base = Costo Onsite Ppto from costos-ajustados
        gasto_key = k.get("gasto_operacional", "")
        gasto_b   = _gasto_b.get(panel) or (bv(gasto_key) if gasto_key else None)

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

        _mef = _cam_ef.get(panel, {})
        ef_tc  = _mef.get("tc")  if _mef.get("tc")  is not None else f(_aj_efecto(gasto_b, exp_tc_frac,        tc_b_val,  tc_r_val))
        ef_ipc = _mef.get("ipc") if _mef.get("ipc") is not None else f(_aj_efecto(gasto_b, exp_tc_frac,        ipc_r_val, ipc_b_val))
        ef_cpi = _mef.get("cpi") if _mef.get("cpi") is not None else f(_aj_efecto(gasto_b, 1.0 - exp_tc_frac, cpi_r_val, cpi_b_val))

        def _aj_calc(base, frac, ratio_num, ratio_den, ratio_lbl, frac_lbl, fuente_mef):
            # Solo describible cuando proviene de la fórmula directa (no del cálculo mixto del Excel)
            if fuente_mef is not None or base is None or frac is None or not ratio_num or not ratio_den or float(ratio_den) == 0:
                return None
            return {
                "metodo": "ajuste_monetario",
                "gasto_base": round(base, 4), "fraccion": round(frac, 4), "fraccion_lbl": frac_lbl,
                "ratio_num": round(float(ratio_num), 4), "ratio_den": round(float(ratio_den), 4),
                "ratio_lbl": ratio_lbl, "ratio": round(float(ratio_num) / float(ratio_den), 6),
            }
        calc_tc  = _aj_calc(gasto_b, exp_tc_frac, tc_b_val, tc_r_val, "TC_ppto / TC_real", "% expuesto a TC", _mef.get("tc"))
        # IPC/CPI = O22 − TC (Excel mixed-denominator formula):
        #   O22 uses budget production as denominator, TC uses real production.
        #   ef_ipc_cpi_kus × (1/cu_r) = total_onsite_kus × (1/cu_b) − ef_tc_kus × (1/cu_r)
        #   → ef_ipc_cpi_kus = total_onsite_kus × (cu_r/cu_b) − ef_tc_kus
        _total_onsite = _mef.get("total_onsite")
        if _total_onsite is not None and ef_tc is not None and cu_r and cu_b and cu_b != 0:
            ef_ipc_cpi = f(_total_onsite * cu_r / cu_b - ef_tc)
        else:
            ef_ipc_cpi = f((ef_ipc or 0.0) + (ef_cpi or 0.0)) if (ef_ipc is not None or ef_cpi is not None) else None

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

        # Inventarios y otros (ktCuf) — residuo de la bridge de producción
        # = Δ Cu Total − (Δ trat + Δ rec + Δ ley), igual que Flash Producciones
        vi_delta = deltas.get("varinv_residual")

        # Efecto inventarios kUS$ (real − budget) — sólo para el waterfall de costos
        vi_key = k.get("vi_inv", "")
        vi_kus = f(_n(rv(vi_key), bv(vi_key))) if vi_key else None

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
                {"label": "TC (Dólar)", "unit": "CLP/USD", "real": f(tc_r_val),  "ppto": f(tc_b_val),  "efecto_kus": ef_tc, "calc": calc_tc},
                {"label": "IPC / CPI",  "unit": "Índice",  "real": f(ipc_r_val), "ppto": f(ipc_b_val), "efecto_kus": ef_ipc_cpi},
                {"label": "CPI",        "unit": "Índice",  "real": f(cpi_r_val), "ppto": f(cpi_b_val), "efecto_kus": None},
            ],
            "precio_insumos": [
                {"label": "Energía",     "unit": "US$/MWh", "real": f(rv(k["tarifa_energia"])),     "ppto": f(bv(k["tarifa_energia"])),     "efecto_kus": f(dg["energia"]),     "calc": dgc.get("energia")},
                {"label": "Combustible", "unit": "US$/lt",  "real": f(rv(k["tarifa_combustible"])), "ppto": f(bv(k["tarifa_combustible"])), "efecto_kus": f(dg["combustible"]), "calc": dgc.get("combustible")},
                {"label": "Ácido",       "unit": "US$/t",   "real": f(rv(k.get("tarifa_acido",""))),  "ppto": f(bv(k.get("tarifa_acido",""))),  "efecto_kus": f(dg["acido"]),  "calc": dgc.get("acido")},
                {"label": "Bolas",       "unit": "US$/t",   "real": f(rv(k["tarifa_bolas"])),       "ppto": f(bv(k["tarifa_bolas"])),       "efecto_kus": f(dg["bolas"]),       "calc": dgc.get("bolas")},
                {"label": "Explosivos",  "unit": "US$/t",   "real": f(rv(k["tarifa_explosivos"])),  "ppto": f(bv(k["tarifa_explosivos"])),  "efecto_kus": f(dg["explosivos"]),  "calc": dgc.get("explosivos")},
            ],
            "rendimiento": [
                {"label": "Energía",     "unit": "MWh/kt", "real": f(rend_energ_r), "ppto": f(rend_energ_b), "efecto_kus": f(dr["energia_conc"]), "calc": drc.get("energia_conc")},
                {"label": "Combustible", "unit": "lt/hr",  "real": f(rend_comb_r),  "ppto": f(rend_comb_b),  "efecto_kus": f(dr["combustible"]), "calc": drc.get("combustible")},
                {"label": "Ácido",       "unit": "kg/t",   "real": f(rend_acido_r),  "ppto": f(rend_acido_b),  "efecto_kus": f(dr["acido"]), "calc": drc.get("acido")},
                {"label": "Bolas",       "unit": "g/t",    "real": f(rend_bolas_r), "ppto": f(rend_bolas_b), "efecto_kus": f(dr["bolas"]), "calc": drc.get("bolas")},
                {"label": "Explosivos",  "unit": "g/t",    "real": f(rend_expl_r),  "ppto": f(rend_expl_b),  "efecto_kus": f(dr["explosivos"]), "calc": drc.get("explosivos")},
            ],
            "produccion": [
                {"label": "Ley Conc",        "unit": "% CuT", "real": f(ley_rr),      "ppto": f(ley_rb),      "efecto_ktcuf": f(el["concentradora"])},
                {"label": "Tratamiento Conc","unit": "kt",    "real": f(proc_r) if (ley_rr is not None or ley_rb is not None) else None, "ppto": f(proc_b) if (ley_rr is not None or ley_rb is not None) else None, "efecto_ktcuf": f(da["trat_concentradora"])},
                {"label": "Rec Conc",        "unit": "%",     "real": f(rec_rr),      "ppto": f(rec_rb),      "efecto_ktcuf": f(da["rec_concentradora"])},
                {"label": "Ley Hidro",       "unit": "% CuT", "real": f(ley_hidro_r), "ppto": f(ley_hidro_b), "efecto_ktcuf": f(el["hidro"])},
                {"label": "Apilamiento",     "unit": "kt",    "real": f(apil_r),      "ppto": f(apil_b),      "efecto_ktcuf": None},
                {"label": "Beneficio Hidro", "unit": "kt",    "real": f(ben_hidro_r), "ppto": f(ben_hidro_b), "efecto_ktcuf": f(da["trat_hidro"])},
                {"label": "Rec Hidro",       "unit": "%",     "real": f(rec_hidro_r), "ppto": f(rec_hidro_b), "efecto_ktcuf": f(da["rec_hidro"])},
                {"label": "Otros",           "unit": "kt",    "real": f(otros_r),     "ppto": f(otros_b),     "efecto_ktcuf": None},
                {"label": "Inventarios y otros", "unit": "kt", "real": None, "ppto": None, "efecto_ktcuf": vi_delta, "efecto_kus": None},
            ],
            "mov_mina": {
                "real": f(mov_r), "ppto": f(mov_b),
                "efecto_kus": f(ac["actividad_mina"]),
            },
            "desarrollo_mina": {
                "real": f(dm_r), "ppto": f(dm_b),
                "efecto_kus": f(_n(dm_b, dm_r)),
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
                "vi_inv_kus": vi_kus,
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
