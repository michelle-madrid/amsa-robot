"""
Visualización de KPIs por rango de fechas (multi-año).
Soporta dos comparaciones con sus propios tipo/rango.
"""
import json
from fastapi import APIRouter, HTTPException

from ..config import settings
from ..services import parquet_service as pq
from ..services.mapping_store import load_mappings

router = APIRouter(prefix="/api/visualizar", tags=["visualizar"])

KPI_STRUCTURE_FILE = settings.parquet_path.parent / "kpi_structure.json"
MONTH_FIELDS = pq.MONTH_FIELDS
MONTH_BY_NUM = {i+1: f for i, f in enumerate(MONTH_FIELDS)}


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


def _apply_op(a, op, b):
    if a is None or b is None:
        return None
    if op == "/" and b == 0:
        return None
    return {"+": a+b, "-": a-b, "*": a*b, "/": a/b}.get(op)


def _apply_scale(val, mapped: dict):
    """Apply optional scalar: (A op B) scale_op scale."""
    if val is None:
        return None
    scale = mapped.get("scale")
    if scale is None:
        return val
    scale_op = mapped.get("scale_op", "*")
    return _apply_op(val, scale_op, float(scale))


def _empty_series(n: int) -> list:
    return [None] * n


def _build_columns(año_ini, mes_ini, año_fin, mes_fin) -> list[dict]:
    """List of {year, month, label} for every month in the range."""
    cols = []
    y, m = año_ini, mes_ini
    while (y, m) <= (año_fin, mes_fin):
        short = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"][m-1]
        cols.append({"year": y, "month": m, "label": f"{short}'{str(y)[2:]}"})
        m += 1
        if m > 12:
            m = 1
            y += 1
    return cols


def _fetch_year_snapshot(df_co, tipo: str, year: int, snap_mes: int) -> dict[str, dict]:
    """Returns {'subcategory||kpi': {field: value}} for the given snapshot period.
    Keys are always qualified. Use _resolve_snapshot to look up by plain name or qualified name.
    """
    periodo = year * 100 + snap_mes
    variants = _tipo_variants(tipo)
    df = df_co[(df_co["periodo"] == periodo) & (df_co["tipo"].str.strip().str.lower().isin(variants))]
    result: dict[str, dict] = {}
    for _, row in df.iterrows():
        kpi_name = str(row.get("kpi", "") or "").strip()
        subcategory = str(row.get("subcategory", "") or "").strip()
        if not kpi_name:
            continue
        key = f"{subcategory}||{kpi_name}" if subcategory else kpi_name
        if key not in result:
            result[key] = pq.row_to_monthly_values(row)
    return result


def _resolve_snapshot(snapshot: dict[str, dict], src: str) -> dict:
    """Look up a source in the snapshot. Supports exact match or suffix match for plain names."""
    if src in snapshot:
        return snapshot[src]
    suffix = f"||{src}"
    matches = [v for k, v in snapshot.items() if k.endswith(suffix)]
    if len(matches) == 1:
        return matches[0]
    return {}


def _get_series(df_co, tipo, año_ini, mes_ini, año_fin, mes_fin,
                columns: list[dict], sources: list[str]) -> list:
    """Returns one value per column for the given sources (summed if multiple)."""
    if not sources:
        return _empty_series(len(columns))

    # Fetch snapshot per year: last available month of the year (or mes_fin for last year)
    years = sorted({c["year"] for c in columns})
    snapshots: dict[int, dict[str, dict]] = {}
    for yr in years:
        snap_mes = mes_fin if yr == año_fin else 12
        snapshots[yr] = _fetch_year_snapshot(df_co, tipo, yr, snap_mes)

    result = []
    for col in columns:
        yr, mo = col["year"], col["month"]
        field = MONTH_BY_NUM[mo]
        total = None
        for src in sources:
            val = _resolve_snapshot(snapshots.get(yr, {}), src).get(field)
            if val is not None:
                total = (total or 0.0) + val
        result.append(total)
    return result


def _get_end_period_vals(df_co, tipo, año_fin, mes_fin, sources: list[str]) -> tuple:
    """Returns (mes_val, ytd_val) from the end-period snapshot."""
    snap = _fetch_year_snapshot(df_co, tipo, año_fin, mes_fin)
    mes_total, ytd_total = None, None
    for src in sources:
        row = _resolve_snapshot(snap, src)
        if row.get("mes") is not None:
            mes_total = (mes_total or 0.0) + row["mes"]
        if row.get("ytd") is not None:
            ytd_total = (ytd_total or 0.0) + row["ytd"]
    return mes_total, ytd_total


@router.get("/{company}")
def get_visualization(
    company: str,
    tipo1: str, año1_ini: int, mes1_ini: int, año1_fin: int, mes1_fin: int,
    tipo2: str, año2_ini: int, mes2_ini: int, año2_fin: int, mes2_fin: int,
):
    if not settings.parquet_path.exists():
        raise HTTPException(404, "Parquet no encontrado")
    if not KPI_STRUCTURE_FILE.exists():
        raise HTTPException(404, "kpi_structure.json no encontrado")

    df_all = pq.get_cached_df(settings.parquet_path)
    df_co  = df_all[df_all["compania"] == company]

    cols1 = _build_columns(año1_ini, mes1_ini, año1_fin, mes1_fin)
    cols2 = _build_columns(año2_ini, mes2_ini, año2_fin, mes2_fin)

    structure = json.loads(KPI_STRUCTURE_FILE.read_text(encoding="utf-8"))
    mappings  = load_mappings().get(company, {})

    def resolve_sources(mapped_value) -> list[str]:
        if not mapped_value or mapped_value == "__NA__":
            return []
        if isinstance(mapped_value, dict):
            return []  # formula — handled separately
        return [mapped_value] if isinstance(mapped_value, str) else [s for s in mapped_value if s]

    sections = []
    cur_sec = ""
    cur_kpis: list = []

    for item in structure.get(company, []):
        if item["type"] == "header":
            if cur_sec and cur_kpis:
                sections.append({"label": cur_sec, "kpis": cur_kpis})
            cur_sec = item["label"]
            cur_kpis = []
            continue

        label = item["label"]
        lk    = f"{cur_sec}||{label}"
        mapped = mappings.get(lk)
        src1 = resolve_sources(mapped)
        src2 = resolve_sources(mapped)
        is_formula = isinstance(mapped, dict) and bool(mapped.get("_f"))

        vals1   = _get_series(df_co, tipo1, año1_ini, mes1_ini, año1_fin, mes1_fin, cols1, src1)
        vals2   = _get_series(df_co, tipo2, año2_ini, mes2_ini, año2_fin, mes2_fin, cols2, src2)
        mes1, ytd1 = _get_end_period_vals(df_co, tipo1, año1_fin, mes1_fin, src1)
        mes2, ytd2 = _get_end_period_vals(df_co, tipo2, año2_fin, mes2_fin, src2)

        cur_kpis.append({
            "label": label,
            "unit":  item.get("unit", ""),
            "lk":    lk,
            "mapped": bool(mapped and mapped != "__NA__"),
            "is_na": mapped == "__NA__",
            "is_formula": is_formula,
            "vals1": vals1, "mes1": mes1, "ytd1": ytd1,
            "vals2": vals2, "mes2": mes2, "ytd2": ytd2,
        })

    if cur_sec and cur_kpis:
        sections.append({"label": cur_sec, "kpis": cur_kpis})

    # Second pass: compute formula KPIs
    by_lk1: dict[str, list] = {}
    by_lk2: dict[str, list] = {}
    mes_by_lk1: dict[str, float | None] = {}
    mes_by_lk2: dict[str, float | None] = {}
    ytd_by_lk1: dict[str, float | None] = {}
    ytd_by_lk2: dict[str, float | None] = {}
    for sec in sections:
        for kpi in sec["kpis"]:
            by_lk1[kpi["lk"]] = kpi["vals1"]
            by_lk2[kpi["lk"]] = kpi["vals2"]
            mes_by_lk1[kpi["lk"]] = kpi["mes1"]
            mes_by_lk2[kpi["lk"]] = kpi["mes2"]
            ytd_by_lk1[kpi["lk"]] = kpi["ytd1"]
            ytd_by_lk2[kpi["lk"]] = kpi["ytd2"]

    for sec in sections:
        for kpi in sec["kpis"]:
            mapped = mappings.get(kpi["lk"])
            if not isinstance(mapped, dict) or not mapped.get("_f"):
                continue
            a, op, b = mapped.get("a",""), mapped.get("op","/"), mapped.get("b","")
            a1 = by_lk1.get(a, _empty_series(len(cols1)))
            b1 = by_lk1.get(b, _empty_series(len(cols1)))
            a2 = by_lk2.get(a, _empty_series(len(cols2)))
            b2 = by_lk2.get(b, _empty_series(len(cols2)))
            kpi["vals1"] = [_apply_scale(_apply_op(x, op, y), mapped) for x, y in zip(a1, b1)]
            kpi["vals2"] = [_apply_scale(_apply_op(x, op, y), mapped) for x, y in zip(a2, b2)]
            kpi["mes1"]  = _apply_scale(_apply_op(mes_by_lk1.get(a), op, mes_by_lk1.get(b)), mapped)
            kpi["mes2"]  = _apply_scale(_apply_op(mes_by_lk2.get(a), op, mes_by_lk2.get(b)), mapped)
            kpi["ytd1"]  = _apply_scale(_apply_op(ytd_by_lk1.get(a), op, ytd_by_lk1.get(b)), mapped)
            kpi["ytd2"]  = _apply_scale(_apply_op(ytd_by_lk2.get(a), op, ytd_by_lk2.get(b)), mapped)
            kpi["mapped"] = bool(a and b)

    return {
        "company": company,
        "tipo1": tipo1, "cols1": cols1,
        "tipo2": tipo2, "cols2": cols2,
        "sections": sections,
    }
