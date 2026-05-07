"""
Costos Ajustados — ajusta el presupuesto por diferencias de FX, IPC y CPI.

  adj_factor = exp_tc × (TC_budget/TC_real) × (IPC_real/IPC_budget)
             + (1 − exp_tc) × (CPI_real/CPI_budget)
  PptoAjTot  = Ppto × adj_factor
  Dif        = Real − PptoAjTot

  exp_tc: exposición al tipo de cambio CLP (parámetro por compañía, en %)
"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..services import parquet_service as pq
from ..services.mapping_store import load_mappings

router = APIRouter(prefix="/api/costos-ajustados", tags=["costos-ajustados"])

PARAMS_FIN_PATH     = settings.parquet_path.parent / "params_financieros.json"
FORMULA_PARAMS_PATH = Path(__file__).resolve().parents[3] / "data" / "formula_params.json"

MONTH_FIELDS = pq.MONTH_FIELDS
MONTH_BY_NUM = {i + 1: f for i, f in enumerate(MONTH_FIELDS)}

_TIPO_ALIASES = [
    {"ppto", "plan", "presupuesto", "budget"},
    {"real", "actual"},
]

_DEFAULT_SUMMARY_ROWS = [
    {"key": "tcrc",          "label": "TC/RC",                         "fijo": 0.0},
    {"key": "comer",         "label": "Comercialización",              "fijo": 0.0},
    {"key": "da",            "label": "Depreciación / Amortización",   "fijo": 1.0},
    {"key": "credito_subp",  "label": "Crédito Subproductos",          "fijo": 0.0},
    {"key": "otros_fin",     "label": "Otros Items Financieros",       "fijo": 0.0},
    {"key": "dif_cambio",    "label": "Diferencias de cambio",         "fijo": 0.0},
    {"key": "donaciones",    "label": "Donaciones",                    "fijo": 0.0},
    {"key": "gasto_expl",    "label": "Gastos Exploraciones",          "fijo": 0.0},
    {"key": "ing_gasto_fin", "label": "Ingresos y Gastos Financieros", "fijo": 0.0},
    {"key": "ing_no_op",     "label": "Ingresos No Operacionales",     "fijo": 0.0},
    {"key": "otros_egr_op",  "label": "Otros egresos operacionales",   "fijo": 0.0},
]

# Keys that can fall back to formula_params kpi_keys
_FALLBACK_KEYS = {
    "tcrc":          ("tcrc_b_total",  "tcrc_r_total"),
    "comer":         ("comer_b_total", "comer_r_total"),
    "da":            ("da",            "da_r"),
    "credito_subp":  ("subprod_b",     "subprod_r"),
    "otros_fin":     ("",              "otros_fin_r"),
    "dif_cambio":    ("",              "dif_cambio_r"),
    "donaciones":    ("",              "donaciones_r"),
    "gasto_expl":    ("",              "gasto_expl_r"),
    "ing_gasto_fin": ("",              "ing_gasto_fin_r"),
    "ing_no_op":     ("",              "ing_no_op_r"),
    "otros_egr_op":  ("",              "otros_egr_op_r"),
    "vi_mina":       ("vi_inv_mina",   "vi_inv_mina"),
    "vi_planta":     ("vi_inv_planta", "vi_inv_planta"),
    "vi_devmina":    ("dev_mina_vol",  "dev_mina_vol"),
    "vi_ifrs16":     ("ifrs16",        "ifrs16"),
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _tipo_variants(tipo: str) -> set[str]:
    t = tipo.strip().lower()
    for group in _TIPO_ALIASES:
        if t in group:
            return group
    return {t}


def _load_fin_params() -> dict:
    if PARAMS_FIN_PATH.exists():
        return json.loads(PARAMS_FIN_PATH.read_text(encoding="utf-8"))
    return {}


def _load_formula_params() -> dict:
    if FORMULA_PARAMS_PATH.exists():
        return json.loads(FORMULA_PARAMS_PATH.read_text(encoding="utf-8"))
    return {}


def _fetch_snapshot(df_co, tipo: str, year: int, month: int) -> dict[str, dict]:
    variants = _tipo_variants(tipo)
    df_tipo  = df_co[df_co["tipo"].str.strip().str.lower().isin(variants)]
    periodo  = year * 100 + month
    df       = df_tipo[df_tipo["periodo"] == periodo]
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


def _get_kpi_list(mapped, use_real: bool) -> list[str]:
    if not mapped or mapped == "__NA__":
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


def _adj_factor(fin_kpis: dict, fin: dict, company: str, año: int, mes: int) -> float:
    m    = fin_kpis.get(str(año), {}).get(str(mes), {})
    tc_r = m.get("dolar_real");    tc_b = m.get("dolar_budget")
    ip_r = m.get("ipc_real");      ip_b = m.get("ipc_budget")
    cp_r = m.get("cpi_real");      cp_b = m.get("cpi_budget")
    exp_tc_pct = (fin.get("exp_tc") or {}).get(company)
    exp_tc = float(exp_tc_pct) / 100.0 if exp_tc_pct is not None else 0.5

    clp_ok = tc_r and tc_b and ip_r and ip_b and tc_r != 0 and ip_b != 0
    usd_ok = cp_r and cp_b and cp_b != 0

    clp_part = exp_tc * (tc_b / tc_r) * (ip_r / ip_b) if clp_ok else exp_tc
    usd_part = (1.0 - exp_tc) * (cp_r / cp_b)          if usd_ok else (1.0 - exp_tc)
    return clp_part + usd_part


def _r(v, d=4):
    return round(v, d) if v is not None else None


def _compute_row(r_snap, b_snap, kpi_r, kpi_b, fin_kpis, fin, company, año, mes_fin):
    field_mes = MONTH_BY_NUM[mes_fin]
    real_m = _sum_resolve(r_snap, kpi_r, field_mes)
    ppto_m = _sum_resolve(b_snap, kpi_b, field_mes)
    af_m   = _adj_factor(fin_kpis, fin, company, año, mes_fin)

    paj_tot_m = _r(ppto_m * af_m) if ppto_m is not None else None
    dif_m = None
    if real_m is not None and paj_tot_m is not None:
        dif_m = _r(real_m - paj_tot_m)
    elif real_m is not None:
        dif_m = _r(real_m)

    ytd_real = ytd_ppto = ytd_paj = None
    for m in range(1, mes_fin + 1):
        f   = MONTH_BY_NUM[m]
        r_m = _sum_resolve(r_snap, kpi_r, f)
        b_m = _sum_resolve(b_snap, kpi_b, f)
        if r_m is not None:
            ytd_real = (ytd_real or 0.0) + r_m
        if b_m is not None:
            af_mm    = _adj_factor(fin_kpis, fin, company, año, m)
            ytd_ppto = (ytd_ppto or 0.0) + b_m
            ytd_paj  = (ytd_paj  or 0.0) + b_m * af_mm

    ytd_paj_tot = _r(ytd_paj) if ytd_paj is not None else None
    ytd_dif = None
    if ytd_real is not None and ytd_paj_tot is not None:
        ytd_dif = _r(ytd_real - ytd_paj_tot)
    elif ytd_real is not None:
        ytd_dif = _r(ytd_real)

    return {
        "mes": {
            "real": _r(real_m), "ppto": _r(ppto_m),
            "ppto_aj_tot": paj_tot_m, "dif": dif_m,
            "adj_factor": round(af_m, 6),
        },
        "ytd": {
            "real": _r(ytd_real), "ppto": _r(ytd_ppto),
            "ppto_aj_tot": ytd_paj_tot, "dif": ytd_dif,
        },
    }


def _sum_periods(period_list: list) -> dict | None:
    result = None
    for p in period_list:
        if p is None:
            continue
        if result is None:
            result = {k: v for k, v in p.items() if k != "adj_factor"}
        else:
            for k in ["real", "ppto", "ppto_aj_tot", "dif"]:
                a = result.get(k); b = p.get(k)
                result[k] = _r((a or 0.0) + (b or 0.0)) if (a is not None or b is not None) else None
    return result


def _add_periods(a: dict | None, b: dict | None) -> dict | None:
    return _sum_periods([a, b])


# ── Monthly adj-factor helpers ────────────────────────────────────────────────

def _exp_tc(fin: dict, company: str) -> float:
    return float((fin.get("exp_tc") or {}).get(company) or 50) / 100.0


def _adj_tc_only(fin_kpis: dict, fin: dict, company: str, año: int, mes: int) -> float:
    m    = fin_kpis.get(str(año), {}).get(str(mes), {})
    tc_r = m.get("dolar_real"); tc_b = m.get("dolar_budget")
    e    = _exp_tc(fin, company)
    return e * (tc_b / tc_r) + (1.0 - e) if (tc_r and tc_b and tc_r != 0) else 1.0


def _adj_ipc_only(fin_kpis: dict, fin: dict, company: str, año: int, mes: int) -> float:
    m    = fin_kpis.get(str(año), {}).get(str(mes), {})
    ip_r = m.get("ipc_real"); ip_b = m.get("ipc_budget")
    e    = _exp_tc(fin, company)
    return e * (ip_r / ip_b) + (1.0 - e) if (ip_r and ip_b and ip_b != 0) else 1.0


def _adj_cpi_only(fin_kpis: dict, fin: dict, company: str, año: int, mes: int) -> float:
    m    = fin_kpis.get(str(año), {}).get(str(mes), {})
    cp_r = m.get("cpi_real"); cp_b = m.get("cpi_budget")
    e    = _exp_tc(fin, company)
    return e + (1.0 - e) * (cp_r / cp_b) if (cp_r and cp_b and cp_b != 0) else 1.0


def _build_monthly_row(b_snap: dict, kpi_b: list, fijo: float,
                        fin_kpis: dict, fin: dict, company: str, año: int, mes_cierre: int) -> dict:
    ppto: list = []
    aj_tc: list = []; aj_ipc: list = []; aj_cpi: list = []
    ef_tc: list = []; ef_ipc: list = []; ef_cpi: list = []

    for m in range(1, 13):
        f = MONTH_BY_NUM[m]
        p = _sum_resolve(b_snap, kpi_b, f)
        ppto.append(p)
        if p is not None:
            atc  = _adj_tc_only(fin_kpis, fin, company, año, m)
            aipc = _adj_ipc_only(fin_kpis, fin, company, año, m)
            acpi = _adj_cpi_only(fin_kpis, fin, company, año, m)
            aj_tc.append(_r(p * (fijo * atc  + (1.0 - fijo))))
            aj_ipc.append(_r(p * (fijo * aipc + (1.0 - fijo))))
            aj_cpi.append(_r(p * (fijo * acpi + (1.0 - fijo))))
            ef_tc.append(_r(p * fijo * (atc  - 1.0)))
            ef_ipc.append(_r(p * fijo * (aipc - 1.0)))
            ef_cpi.append(_r(p * fijo * (acpi - 1.0)))
        else:
            aj_tc.append(None); aj_ipc.append(None); aj_cpi.append(None)
            ef_tc.append(None); ef_ipc.append(None); ef_cpi.append(None)

    def _ma(lst: list):
        mes  = lst[mes_cierre - 1] if 1 <= mes_cierre <= 12 else None
        vals = [v for v in lst[:mes_cierre] if v is not None]
        return _r(mes), (_r(sum(vals)) if vals else None)

    pm, pa       = _ma(ppto)
    atcm, atca   = _ma(aj_tc)
    aipcm, aipca = _ma(aj_ipc)
    acpim, acpia = _ma(aj_cpi)
    etcm, etca   = _ma(ef_tc)
    eipcm, eipca = _ma(ef_ipc)
    ecpim, ecpia = _ma(ef_cpi)

    return {
        "ppto": ppto, "ppto_mes": pm, "ppto_acum": pa,
        "aj_tc":  aj_tc,  "aj_tc_mes":  atcm,  "aj_tc_acum":  atca,
        "aj_ipc": aj_ipc, "aj_ipc_mes": aipcm, "aj_ipc_acum": aipca,
        "aj_cpi": aj_cpi, "aj_cpi_mes": acpim, "aj_cpi_acum": acpia,
        "ef_tc":  ef_tc,  "ef_tc_mes":  etcm,  "ef_tc_acum":  etca,
        "ef_ipc": ef_ipc, "ef_ipc_mes": eipcm, "ef_ipc_acum": eipca,
        "ef_cpi": ef_cpi, "ef_cpi_mes": ecpim, "ef_cpi_acum": ecpia,
    }


def _sum_monthly_rows(rows: list) -> dict | None:
    result: dict | None = None
    for row in rows:
        if not row:
            continue
        if result is None:
            result = {k: (list(v) if isinstance(v, list) else v)
                      for k, v in row.items() if k != "mapped"}
        else:
            for key in list(result.keys()):
                rv = result[key]; bv = row.get(key)
                if isinstance(rv, list):
                    result[key] = [
                        _r((rv[i] or 0.0) + (bv[i] or 0.0))
                        if (rv[i] is not None or (bv and bv[i] is not None)) else None
                        for i in range(12)
                    ]
                else:
                    a, b = rv, bv
                    result[key] = _r((a or 0.0) + (b or 0.0)) if (a is not None or b is not None) else None
    if result is not None:
        result["mapped"] = True
    return result


def _fp_kpis(fp: dict, key_b: str, key_r: str, use_real: bool) -> list[str]:
    k   = fp.get("kpi_keys", {})
    val = k.get(key_r if use_real else key_b, "")
    if isinstance(val, list):
        return [s for s in val if s]
    return [val] if val else []


def _auto_lookup(mappings: dict, label: str) -> object | None:
    """Find a unique existing mapping ending in ||{label} (outside Costos Ajustados section)."""
    candidates = [v for k, v in mappings.items()
                  if k.endswith(f"||{label}") and not k.startswith("Costos Ajustados")]
    return candidates[0] if len(candidates) == 1 else None


def _costos_lookup(mappings: dict, key: str, label: str | None) -> object | None:
    """Look up a Costos Ajustados mapping supporting 2-part and 3-part keys."""
    v = mappings.get(f"Costos Ajustados||{key}")
    if v is not None:
        return v
    if not label:
        return None
    v = mappings.get(f"Costos Ajustados||{label}")
    if v is not None:
        return v
    # 3-part: "Costos Ajustados||<subheader>||<label>"
    candidates = [v for k, v in mappings.items()
                  if k.startswith("Costos Ajustados||") and k.endswith(f"||{label}")
                  and k.count("||") == 2]
    return candidates[0] if len(candidates) == 1 else None


def _resolve_row_kpis(key: str, mappings: dict, fp: dict, use_real: bool, label: str | None = None) -> list[str]:
    mapped = _costos_lookup(mappings, key, label)
    if mapped is None and label:
        mapped = _auto_lookup(mappings, label)
    kpis   = _get_kpi_list(mapped, use_real)
    if kpis:
        return kpis
    if key in _FALLBACK_KEYS:
        kb, kr = _FALLBACK_KEYS[key]
        return _fp_kpis(fp, kb, kr, use_real)
    return []


# ── Endpoint ─────────────────────────────────────────────────────────────────

@router.get("/{company}")
def get_costos_ajustados(company: str, año: int, mes: int):
    if not settings.parquet_path.exists():
        raise HTTPException(404, "Parquet no encontrado")

    fin      = _load_fin_params()
    fin_kpis = fin.get("kpis", {})
    grupos   = fin.get("costos_aj_grupos", {}).get(company, [])
    sum_defs = fin.get("costos_aj_summary", _DEFAULT_SUMMARY_ROWS)

    mappings = load_mappings().get(company, {})
    fp       = _load_formula_params()

    df_all = pq.get_cached_df(settings.parquet_path)
    df_co  = df_all[df_all["compania"] == company]
    r_snap = _fetch_snapshot(df_co, "real", año, mes)
    b_snap = _fetch_snapshot(df_co, "ppto", año, mes)

    def _proc_key(key, is_summary=False, label=None):
        if is_summary:
            kpi_r = _resolve_row_kpis(key, mappings, fp, True,  label)
            kpi_b = _resolve_row_kpis(key, mappings, fp, False, label)
        else:
            mapped = _costos_lookup(mappings, key, label)
            if mapped is None and label:
                mapped = _auto_lookup(mappings, label)
            kpi_r  = _get_kpi_list(mapped, True)
            kpi_b  = _get_kpi_list(mapped, False)
        if not kpi_r and not kpi_b:
            return {"mes": None, "ytd": None, "mapped": False}
        data = _compute_row(
            r_snap, b_snap,
            kpi_r or kpi_b, kpi_b or kpi_r,
            fin_kpis, fin, company, año, mes,
        )
        data["mapped"] = True
        return data

    # ── Main groups ──────────────────────────────────────────────────────────
    result_grupos   = []
    onsite_mes_list = []
    onsite_ytd_list = []

    for grp in grupos:
        sub_mes = []
        sub_ytd = []
        result_sub = []

        for sa in grp.get("subareas", []):
            sa_data = _proc_key(sa["key"], label=sa.get("label"))
            result_sub.append({
                "key": sa["key"], "label": sa["label"],
                "fijo": sa.get("fijo", 0.0), **sa_data,
            })
            sub_mes.append(sa_data.get("mes"))
            sub_ytd.append(sa_data.get("ytd"))

        grp_mes = _sum_periods(sub_mes)
        grp_ytd = _sum_periods(sub_ytd)
        onsite_mes_list.append(grp_mes)
        onsite_ytd_list.append(grp_ytd)

        result_grupos.append({
            "key": grp["key"], "label": grp["label"],
            "subareas": result_sub,
            "total": {"mes": grp_mes, "ytd": grp_ytd},
        })

    # ── Summary rows ─────────────────────────────────────────────────────────
    result_sum  = []
    sum_by_key: dict[str, dict] = {}
    for sr in sum_defs:
        key    = sr["key"]
        sr_data = _proc_key(key, is_summary=True, label=sr.get("label"))
        result_sum.append({"key": key, "label": sr["label"], "fijo": sr.get("fijo", 0.0), **sr_data})
        sum_by_key[key] = sr_data

    # ── Computed totals ──────────────────────────────────────────────────────
    def _sp(key, period):
        return (sum_by_key.get(key) or {}).get(period)

    onsite_mes = _sum_periods(onsite_mes_list)
    onsite_ytd = _sum_periods(onsite_ytd_list)

    pre_m = _add_periods(_add_periods(onsite_mes, _sp("tcrc",  "mes")), _sp("comer", "mes"))
    pre_y = _add_periods(_add_periods(onsite_ytd, _sp("tcrc",  "ytd")), _sp("comer", "ytd"))
    c1_m  = _add_periods(pre_m, _sp("credito_subp", "mes"))
    c1_y  = _add_periods(pre_y, _sp("credito_subp", "ytd"))
    c2_m  = _add_periods(c1_m,  _sp("da",           "mes"))
    c2_y  = _add_periods(c1_y,  _sp("da",           "ytd"))
    c3_m  = _add_periods(c2_m,  _sp("otros_fin",    "mes"))
    c3_y  = _add_periods(c2_y,  _sp("otros_fin",    "ytd"))

    return {
        "company": company, "año": año, "mes": mes,
        "grupos":  result_grupos,
        "summary": result_sum,
        "totales": {
            "costo_onsite": {"mes": onsite_mes, "ytd": onsite_ytd},
            "pre_credito":  {"mes": pre_m,      "ytd": pre_y},
            "c1":           {"mes": c1_m,       "ytd": c1_y},
            "c2":           {"mes": c2_m,       "ytd": c2_y},
            "c3":           {"mes": c3_m,       "ytd": c3_y},
        },
    }


@router.get("/{company}/monthly")
def get_costos_ajustados_monthly(company: str, año: int, mes_cierre: int):
    """Monthly PPTO + PPTO Ajustado TC/IPC/CPI + Efectos for the full year."""
    if not settings.parquet_path.exists():
        raise HTTPException(404, "Parquet no encontrado")
    if not 1 <= mes_cierre <= 12:
        raise HTTPException(400, "mes_cierre debe ser 1-12")

    fin      = _load_fin_params()
    fin_kpis = fin.get("kpis", {})
    grupos   = fin.get("costos_aj_grupos", {}).get(company, [])
    sum_defs = fin.get("costos_aj_summary", _DEFAULT_SUMMARY_ROWS)

    mappings = load_mappings().get(company, {})
    fp       = _load_formula_params()

    df_all = pq.get_cached_df(settings.parquet_path)
    df_co  = df_all[df_all["compania"] == company]
    # Fetch full-year plan: request month 12 so the fallback picks the latest PPTO snapshot
    b_snap = _fetch_snapshot(df_co, "ppto", año, 12)

    def _monthly(key: str, fijo: float, is_summary: bool = False, label: str | None = None) -> dict:
        if is_summary:
            kpi_b = _resolve_row_kpis(key, mappings, fp, False, label)
        else:
            mapped_val = _costos_lookup(mappings, key, label)
            if mapped_val is None and label:
                mapped_val = _auto_lookup(mappings, label)
            kpi_b = _get_kpi_list(mapped_val, False)
        if not kpi_b:
            return {"mapped": False}
        d = _build_monthly_row(b_snap, kpi_b, fijo, fin_kpis, fin, company, año, mes_cierre)
        d["mapped"] = True
        return d

    result_grupos: list = []
    onsite_rows:   list = []

    for grp in grupos:
        sub_rows: list = []
        result_sub: list = []
        for sa in grp.get("subareas", []):
            d = _monthly(sa["key"], sa.get("fijo", 0.0), label=sa.get("label"))
            result_sub.append({"key": sa["key"], "label": sa["label"], "fijo": sa.get("fijo", 0.0), **d})
            if d.get("mapped"):
                sub_rows.append(d)
        grp_total = _sum_monthly_rows(sub_rows)
        onsite_rows.append(grp_total)
        result_grupos.append({"key": grp["key"], "label": grp["label"],
                               "subareas": result_sub, "total": grp_total})

    result_sum: list = []
    sum_by_key: dict = {}
    for sr in sum_defs:
        key = sr["key"]
        d   = _monthly(key, sr.get("fijo", 0.0), is_summary=True, label=sr.get("label"))
        result_sum.append({"key": key, "label": sr["label"], "fijo": sr.get("fijo", 0.0), **d})
        sum_by_key[key] = d

    def _sk(k: str) -> dict | None:
        d = sum_by_key.get(k)
        return d if d and d.get("mapped") else None

    onsite   = _sum_monthly_rows([r for r in onsite_rows if r])
    pre_cred = _sum_monthly_rows([r for r in [onsite, _sk("tcrc"), _sk("comer")] if r])
    c1       = _sum_monthly_rows([r for r in [pre_cred, _sk("credito_subp")] if r])
    c2       = _sum_monthly_rows([r for r in [c1, _sk("da")] if r])
    c3       = _sum_monthly_rows([r for r in [c2, _sk("otros_fin")] if r])

    return {
        "company": company, "año": año, "mes_cierre": mes_cierre,
        "grupos": result_grupos,
        "summary": result_sum,
        "totales": {
            "costo_onsite": onsite,
            "pre_credito":  pre_cred,
            "c1": c1, "c2": c2, "c3": c3,
        },
    }
