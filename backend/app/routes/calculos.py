"""
Delta calculations: Gasto (Precio), Rendimiento, Actividad, Efecto Ley.
Formulas replicated from Excel 'Inputs MLP' sheet.
Returns per-column series + Mes + YTD, matching the visualizar endpoint format.
"""
from fastapi import APIRouter, HTTPException

from ..config import settings
from ..services import parquet_service as pq

router = APIRouter(prefix="/api/calculos", tags=["calculos"])

MONTH_FIELDS  = pq.MONTH_FIELDS
MONTH_BY_NUM  = {i + 1: f for i, f in enumerate(MONTH_FIELDS)}

_TIPO_ALIASES: list[set[str]] = [
    {"ppto", "plan", "presupuesto", "budget"},
    {"real", "actual"},
]


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
        kpi_name   = str(row.get("kpi", "") or "").strip()
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


def _compute_deltas(r_snap: dict, b_snap: dict, field: str) -> dict:
    """Compute all deltas for a single field (e.g. 'enero', 'mes', 'ytd')."""
    def r(key): return _resolve(r_snap, key, field)
    def b(key): return _resolve(b_snap, key, field)

    proc_r = r("Tratamiento");         proc_b = b("Tratamiento")
    ley_r  = r("Ley de Cu");           ley_b  = b("Ley de Cu")
    rec_r  = r("Recuperación de cobre"); rec_b = b("Recuperación de cobre")
    hef_r  = r("Horas Efectivas");     hef_b  = b("Horas Efectivas")
    tron_r = _sum_resolve(r_snap, ["Roca Quebrada (Lastre)", "Roca Quebrada (Mineral)"], field)
    tron_b = _sum_resolve(b_snap, ["Roca Quebrada (Lastre)", "Roca Quebrada (Mineral)"], field)

    tar_bolas_r = r("Tarifa Bolas");        tar_bolas_b = b("Tarifa Bolas")
    tar_energ_r = r("Energía all in");      tar_energ_b = b("Energía all in")
    tar_comb_r  = r("Combustible");         tar_comb_b  = b("Combustible")
    tar_expl_r  = r("Precios Insumos Críticos||Explosivos")
    tar_expl_b  = b("Precios Insumos Críticos||Explosivos")

    con_bolas_r = r("Consumo Bolas");       con_bolas_b = b("Consumo Bolas")
    con_energ_r = r("Consumo Energía Compañía"); con_energ_b = b("Consumo Energía Compañía")
    con_comb_r  = r("Mina||Consumo Comb. - Transporte"); con_comb_b = b("Mina||Consumo Comb. - Transporte")
    con_expl_r  = r("Mina||Explosivos");    con_expl_b  = b("Mina||Explosivos")

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
    dr_comb    = _m(_n(rend_comb_r,  rend_comb_b),  hef_r,           tar_comb_b,  1/1000) if hef_r else None
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
        "delta_gasto_precio":  {"bolas": fmt(dg_bolas), "acido": fmt(dg_acido), "energia": fmt(dg_energ), "combustible": fmt(dg_comb), "explosivos": fmt(dg_expl), "total": fmt(dg_total)},
        "delta_rendimiento":   {"bolas": fmt(dr_bolas), "acido": fmt(dr_acido), "energia_conc": fmt(dr_energ_c), "energia_hidro": fmt(dr_energ_h), "combustible": fmt(dr_comb), "explosivos": fmt(dr_expl), "total": fmt(dr_total)},
        "delta_actividad":     {"trat_concentradora": fmt(da_trat_c), "trat_hidro": fmt(da_trat_h), "rec_concentradora": fmt(da_rec_c), "rec_hidro": fmt(da_rec_h), "total": fmt(da_total)},
        "efecto_ley":          {"concentradora": fmt(el_conc), "hidro": fmt(el_hidro), "total": fmt(el_total)},
    }


@router.get("/{company}")
def get_calculos(
    company: str,
    año_ini: int, mes_ini: int, año_fin: int, mes_fin: int,
):
    if not settings.parquet_path.exists():
        raise HTTPException(404, "Parquet no encontrado")

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

    # Per-column values
    col_deltas = []
    for col in cols:
        yr, mo = col["year"], col["month"]
        field  = MONTH_BY_NUM[mo]
        col_deltas.append(_compute_deltas(
            real_snaps.get(yr, {}),
            budget_snaps.get(yr, {}),
            field,
        ))

    # Mes + YTD from end-period snapshot
    end_r = real_snaps.get(año_fin, {})
    end_b = budget_snaps.get(año_fin, {})
    mes_deltas = _compute_deltas(end_r, end_b, "mes")
    ytd_deltas = _compute_deltas(end_r, end_b, "ytd")

    def build_series(section: str, key: str) -> dict:
        return {
            "vals": [cd[section][key] for cd in col_deltas],
            "mes":  mes_deltas[section][key],
            "ytd":  ytd_deltas[section][key],
        }

    def section_series(section: str, keys: list[str]) -> dict:
        return {k: build_series(section, k) for k in keys}

    return {
        "company": company,
        "cols":    cols,
        "delta_gasto_precio": section_series("delta_gasto_precio",
            ["bolas", "acido", "energia", "combustible", "explosivos", "total"]),
        "delta_rendimiento": section_series("delta_rendimiento",
            ["bolas", "acido", "energia_conc", "energia_hidro", "combustible", "explosivos", "total"]),
        "delta_actividad": section_series("delta_actividad",
            ["trat_concentradora", "trat_hidro", "rec_concentradora", "rec_hidro", "total"]),
        "efecto_ley": section_series("efecto_ley",
            ["concentradora", "hidro", "total"]),
    }
