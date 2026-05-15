"""
Costos Ajustados — ajusta el presupuesto por diferencias de FX, IPC y CPI.

  adj_factor = exp_tc × (TC_budget/TC_real) × (IPC_real/IPC_budget)
             + (1 − exp_tc) × (CPI_real/CPI_budget)
  PptoAjTot  = Ppto × adj_factor
  Dif        = Real − PptoAjTot

  exp_tc: exposición al tipo de cambio CLP (parámetro por compañía, en %)
"""
import json
import re
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
    {"key": "credito_subp",  "label": "Crédito Subproductos",          "fijo": 0.0},
    {"key": "da",            "label": "Depreciación / Amortización",   "fijo": 1.0},
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
    "vi_total":      ("vi_inv",        "vi_inv"),
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


def _build_snapshot_from_df(df) -> dict[str, dict]:
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


def _fetch_snapshot(df_co, tipo: str, year: int, month: int) -> tuple[dict[str, dict], int]:
    """Returns (snapshot_dict, actual_month_used). actual_month may be < month if data not yet available.
    If the exact-period snapshot is sparse (missing cost-breakdown KPIs added later),
    it is enriched with keys from the nearest richer period in the same year."""
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
    actual_month = periodo % 100
    result = _build_snapshot_from_df(df)

    # Enriquecer con KPIs del periodo más completo disponible en el mismo año si el actual es incompleto
    # (ej. enero/febrero 2026 carecen del desglose de Costos Ajustados que aparece en marzo)
    if len(df) > 0:
        next_periodo = year * 100 + actual_month + 1
        while next_periodo <= year * 100 + 12:
            df_next = df_tipo[df_tipo["periodo"] == next_periodo]
            if not df_next.empty and len(df_next) > len(df) * 1.05:
                richer = _build_snapshot_from_df(df_next)
                for k, v in richer.items():
                    if k not in result:
                        result[k] = v   # añade claves faltantes; las existentes no se sobreescriben
                break   # encontramos un periodo más rico; no buscar más
            next_periodo += 1

    return result, actual_month


def _norm_key(s: str) -> str:
    """Normaliza para comparación: elimina \\n y caracteres no-ASCII (Mojibake, tildes)."""
    return re.sub(r'[^\x00-\x7F]', '', s.replace('\n', '').replace('\r', '')).lower()


def _resolve(snapshot: dict, src: str, field: str):
    # 1. Exact match
    data = snapshot.get(src)
    # 2. Suffix match ("||src")
    if data is None:
        suffix = f"||{src}"
        matches = [v for k, v in snapshot.items() if k.endswith(suffix)]
        data = matches[0] if len(matches) == 1 else None
    # 3. Normalized match — maneja \n y Mojibake en nombres del parquet
    if data is None:
        src_norm = _norm_key(src)
        norm_matches = [v for k, v in snapshot.items() if _norm_key(k) == src_norm]
        data = norm_matches[0] if len(norm_matches) == 1 else None
    # 4. Normalized suffix match
    if data is None:
        src_norm = _norm_key(src)
        norm_matches = [v for k, v in snapshot.items()
                        if _norm_key(k).endswith('||' + src_norm)]
        data = norm_matches[0] if len(norm_matches) == 1 else None
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
    dif_m = _r(paj_tot_m - ppto_m) if (paj_tot_m is not None and ppto_m is not None) else None

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
    ytd_ppto_r  = _r(ytd_ppto) if ytd_ppto is not None else None
    ytd_dif = _r(ytd_paj_tot - ytd_ppto_r) if (ytd_paj_tot is not None and ytd_ppto_r is not None) else None

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


_PERIOD_COST_KEYS = {"real", "ppto", "ppto_aj_tot", "dif"}
_MONTHLY_COST_KEYS = {"ppto", "aj_tc", "aj_ipc", "aj_cpi",
                      "ppto_mes", "ppto_acum",
                      "aj_tc_mes", "aj_tc_acum",
                      "aj_ipc_mes", "aj_ipc_acum",
                      "aj_cpi_mes", "aj_cpi_acum",
                      "ef_tc_mes", "ef_tc_acum",
                      "ef_ipc_mes", "ef_ipc_acum",
                      "ef_cpi_mes", "ef_cpi_acum"}


def _scale_period(p: dict | None, f: float) -> dict | None:
    if p is None or f == 1.0:
        return p
    return {k: (_r(v * f) if k in _PERIOD_COST_KEYS and v is not None else v)
            for k, v in p.items()}


def _scale_row(row: dict, f: float) -> dict:
    if not row or f == 1.0:
        return row
    out = dict(row)
    for p in ("mes", "ytd"):
        if out.get(p) is not None:
            out[p] = _scale_period(out[p], f)
    return out


def _scale_monthly_row(row: dict, f: float) -> dict:
    """Scale a monthly-endpoint row (arrays + scalar totals)."""
    if not row or f == 1.0:
        return row
    out = dict(row)
    for k, v in out.items():
        if k not in _MONTHLY_COST_KEYS:
            continue
        if isinstance(v, list):
            out[k] = [(_r(x * f) if x is not None else None) for x in v]
        elif isinstance(v, (int, float)):
            out[k] = _r(v * f)
    return out


# ── Monthly adj-factor helpers ────────────────────────────────────────────────

def _exp_tc(fin: dict, company: str) -> float:
    return float((fin.get("exp_tc") or {}).get(company) or 50) / 100.0


def _last_real(yr_data: dict, mes: int, key: str):
    """Devuelve el último valor real disponible en o antes del mes dado."""
    for m in range(mes, 0, -1):
        v = yr_data.get(str(m), {}).get(key)
        if v:
            return v
    return None


def _adj_tc_only(fin_kpis: dict, fin: dict, company: str, año: int, mes: int) -> float:
    yr   = fin_kpis.get(str(año), {})
    m    = yr.get(str(mes), {})
    tc_r = m.get("dolar_real") or _last_real(yr, mes - 1, "dolar_real")
    tc_b = m.get("dolar_budget")
    e    = _exp_tc(fin, company)
    return e * (tc_b / tc_r) + (1.0 - e) if (tc_r and tc_b and tc_r != 0) else 1.0


def _adj_ipc_only(fin_kpis: dict, fin: dict, company: str, año: int, mes: int) -> float:
    yr   = fin_kpis.get(str(año), {})
    m    = yr.get(str(mes), {})
    ip_r = m.get("ipc_real") or _last_real(yr, mes - 1, "ipc_real")
    ip_b = m.get("ipc_budget")
    e    = _exp_tc(fin, company)
    return e * (ip_r / ip_b) + (1.0 - e) if (ip_r and ip_b and ip_b != 0) else 1.0


def _adj_cpi_only(fin_kpis: dict, fin: dict, company: str, año: int, mes: int) -> float:
    yr   = fin_kpis.get(str(año), {})
    m    = yr.get(str(mes), {})
    cp_r = m.get("cpi_real") or _last_real(yr, mes - 1, "cpi_real")
    cp_b = m.get("cpi_budget")
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
            aj_tc.append(_r(p * atc))
            aj_ipc.append(_r(p * aipc))
            aj_cpi.append(_r(p * acpi))
            ef_tc.append(_r(p * (atc  - 1.0)))
            ef_ipc.append(_r(p * (aipc - 1.0)))
            ef_cpi.append(_r(p * (acpi - 1.0)))
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


def _costos_lookup(mappings: dict, key: str, label: str | None, grp_label: str | None = None) -> object | None:
    """Look up a Costos Ajustados mapping supporting 2-part and 3-part keys."""
    v = mappings.get(f"Costos Ajustados||{key}")
    if v is not None:
        return v
    if not label:
        return None
    v = mappings.get(f"Costos Ajustados||{label}")
    if v is not None:
        return v
    # Variante 3-partes para totales de grupo guardados desde la UI: "||<label>||Total"
    v = mappings.get(f"Costos Ajustados||{label}||Total")
    if v is not None:
        return v
    # 3-part: "Costos Ajustados||<subheader>||<label>"
    if grp_label:
        # Exact group match only — no cross-group fallback
        return mappings.get(f"Costos Ajustados||{grp_label}||{label}")
    candidates = [v for k, v in mappings.items()
                  if k.startswith("Costos Ajustados||") and k.endswith(f"||{label}")
                  and k.count("||") == 2]
    return candidates[0] if len(candidates) == 1 else None


def _resolve_row_kpis(key: str, mappings: dict, fp: dict, use_real: bool,
                      label: str | None = None, grp_label: str | None = None) -> list[str]:
    mapped = _costos_lookup(mappings, key, label, grp_label)
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
    r_snap, _r_mes = _fetch_snapshot(df_co, "real", año, mes)
    b_snap, _b_mes = _fetch_snapshot(df_co, "ppto", año, mes)
    mes_fin = mes

    def _proc_key(key, is_summary=False, label=None, grp_label=None):
        if is_summary:
            kpi_r = _resolve_row_kpis(key, mappings, fp, True,  label, grp_label)
            kpi_b = _resolve_row_kpis(key, mappings, fp, False, label, grp_label)
        else:
            mapped = _costos_lookup(mappings, key, label, grp_label)
            if mapped is None and label:
                mapped = _auto_lookup(mappings, label)
            kpi_r  = _get_kpi_list(mapped, True)
            kpi_b  = _get_kpi_list(mapped, False)
        if not kpi_r and not kpi_b:
            return {"mes": None, "ytd": None, "mapped": False}
        data = _compute_row(
            r_snap, b_snap,
            kpi_r or kpi_b, kpi_b or kpi_r,
            fin_kpis, fin, company, año, mes_fin,
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
            sa_data = _proc_key(sa["key"], label=sa.get("label"), grp_label=grp.get("label"))
            result_sub.append({
                "key": sa["key"], "label": sa["label"],
                "fijo": sa.get("fijo", 0.0), **sa_data,
            })
            sub_mes.append(sa_data.get("mes"))
            sub_ytd.append(sa_data.get("ytd"))

        # Si existe mapeo directo a nivel de grupo, úsalo como total (ej. "Total Servicios de Apoyo")
        grp_direct = _proc_key(grp["key"], label=grp.get("label"))
        if grp_direct.get("mapped"):
            grp_mes = grp_direct.get("mes")
            grp_ytd = grp_direct.get("ytd")
        else:
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
    _otros_keys = ["otros_fin", "dif_cambio", "donaciones", "gasto_expl",
                    "ing_gasto_fin", "ing_no_op", "otros_egr_op"]
    c3_m = c2_m
    c3_y = c2_y
    for _k in _otros_keys:
        c3_m = _add_periods(c3_m, _sp(_k, "mes"))
        c3_y = _add_periods(c3_y, _sp(_k, "ytd"))

    sc = fin.get("company_cost_scale", {}).get(company, 1.0)
    if sc != 1.0:
        result_grupos = [
            {**grp,
             "subareas": [_scale_row(sa, sc) for sa in grp["subareas"]],
             "total": {"mes": _scale_period(grp["total"]["mes"], sc),
                       "ytd": _scale_period(grp["total"]["ytd"], sc)}}
            for grp in result_grupos
        ]
        result_sum  = [_scale_row(sr, sc) for sr in result_sum]
        onsite_mes  = _scale_period(onsite_mes, sc)
        onsite_ytd  = _scale_period(onsite_ytd, sc)
        pre_m = _scale_period(pre_m, sc);  pre_y = _scale_period(pre_y, sc)
        c1_m  = _scale_period(c1_m,  sc);  c1_y  = _scale_period(c1_y,  sc)
        c2_m  = _scale_period(c2_m,  sc);  c2_y  = _scale_period(c2_y,  sc)
        c3_m  = _scale_period(c3_m,  sc);  c3_y  = _scale_period(c3_y,  sc)

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
    b_snap, _ = _fetch_snapshot(df_co, "ppto", año, 12)

    def _monthly(key: str, fijo: float, is_summary: bool = False,
                 label: str | None = None, grp_label: str | None = None) -> dict:
        if is_summary:
            kpi_b = _resolve_row_kpis(key, mappings, fp, False, label, grp_label)
        else:
            mapped_val = _costos_lookup(mappings, key, label, grp_label)
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
            d = _monthly(sa["key"], sa.get("fijo", 0.0), label=sa.get("label"), grp_label=grp.get("label"))
            result_sub.append({"key": sa["key"], "label": sa["label"], "fijo": sa.get("fijo", 0.0), **d})
            if d.get("mapped"):
                sub_rows.append(d)
        grp_direct = _monthly(grp["key"], 1.0, label=grp.get("label"))
        grp_total = grp_direct if grp_direct.get("mapped") else _sum_monthly_rows(sub_rows)
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
    _otros_keys_m = ["otros_fin", "dif_cambio", "donaciones", "gasto_expl",
                      "ing_gasto_fin", "ing_no_op", "otros_egr_op"]
    c3 = _sum_monthly_rows([r for r in [c2] + [_sk(k) for k in _otros_keys_m] if r])

    sc = fin.get("company_cost_scale", {}).get(company, 1.0)
    if sc != 1.0:
        result_grupos = [
            {**grp,
             "subareas": [_scale_monthly_row(sa, sc) for sa in grp["subareas"]],
             "total": _scale_monthly_row(grp.get("total") or {}, sc)}
            for grp in result_grupos
        ]
        result_sum = [_scale_monthly_row(sr, sc) for sr in result_sum]
        onsite   = _scale_monthly_row(onsite,   sc)
        pre_cred = _scale_monthly_row(pre_cred, sc)
        c1 = _scale_monthly_row(c1, sc)
        c2 = _scale_monthly_row(c2, sc)
        c3 = _scale_monthly_row(c3, sc)

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
