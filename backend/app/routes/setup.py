"""
Endpoints for the KPI mapping configuration UI.
"""
import json
from fastapi import APIRouter, HTTPException
import openpyxl

from ..config import settings
from ..services import parquet_service as pq
from ..services import mapping_store
from ..services.mapper_service import get_excel_kpis_for_setup

router = APIRouter(prefix="/api/setup", tags=["setup"])

KPI_STRUCTURE_FILE = settings.parquet_path.parent / "kpi_structure.json"


def _get_df():
    if not settings.parquet_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Parquet no encontrado: {settings.parquet_path}",
        )
    return pq.get_cached_df(settings.parquet_path)


def _load_structure(company: str) -> list | None:
    """Load predefined KPI structure from JSON, enriching KPI rows with their section."""
    if not KPI_STRUCTURE_FILE.exists():
        return None
    data = json.loads(KPI_STRUCTURE_FILE.read_text(encoding="utf-8"))
    raw = data.get(company)
    if raw is None:
        return None
    current_section = ""
    result = []
    for item in raw:
        if item["type"] == "header":
            current_section = item["label"]
            result.append(item)
        else:
            kpi_id = f"{current_section}||{item['label']}"
            result.append({**item, "section": current_section, "id": kpi_id})
    return result


@router.get("/excel-kpis/{company}")
def get_excel_kpis(company: str):
    """KPI structure for this company: uses kpi_structure.json if available,
    otherwise falls back to reading the Excel template."""
    structure = _load_structure(company)
    if structure is not None:
        return {"company": company, "kpis": structure}

    if not settings.template_path.exists():
        raise HTTPException(404, f"Template no encontrado: {settings.template_path}")
    wb = openpyxl.load_workbook(settings.template_path, read_only=True, data_only=True)
    kpis = get_excel_kpis_for_setup(wb, company)
    return {"company": company, "kpis": kpis}


@router.get("/parquet-kpis/{company}")
def get_parquet_kpis(company: str):
    """All unique KPI names in the parquet for this company.
    Returns 'subcategory||kpi' when subcategory exists, plain 'kpi' otherwise.
    """
    df = _get_df()
    company_df = df[df["compania"] == company]
    names: set[str] = set()
    if "kpi" in company_df.columns:
        cols = ["kpi"] + (["subcategory"] if "subcategory" in company_df.columns else [])
        for _, row in company_df[cols].drop_duplicates().iterrows():
            kpi_name = str(row.get("kpi", "") or "").strip()
            subcategory = str(row.get("subcategory", "") or "").strip()
            if not kpi_name:
                continue
            names.add(f"{subcategory}||{kpi_name}" if subcategory else kpi_name)
    return {"company": company, "kpis": sorted(names)}


@router.get("/parquet-snapshot/{company}")
def get_parquet_snapshot(company: str, tipo: str, anio: int, mes: int):
    """Diagnostic: returns KPI names available for a specific tipo+periodo."""
    df = _get_df()
    periodo = anio * 100 + mes
    subset = df[(df["compania"] == company) & (df["tipo"] == tipo) & (df["periodo"] == periodo)]
    kpis: list[str] = []
    if "kpi" in subset.columns:
        for _, row in subset[["kpi", "subcategory"] if "subcategory" in subset.columns else ["kpi"]].drop_duplicates().iterrows():
            kpi_name = str(row.get("kpi", "") or "").strip()
            subcategory = str(row.get("subcategory", "") or "").strip()
            if not kpi_name:
                continue
            qualified = f"{subcategory}||{kpi_name}" if subcategory else kpi_name
            if qualified not in kpis:
                kpis.append(qualified)
    periodos_disponibles = sorted(
        df[(df["compania"] == company) & (df["tipo"] == tipo)]["periodo"].dropna().unique().tolist()
    )
    return {
        "company": company,
        "tipo": tipo,
        "periodo_buscado": periodo,
        "kpis_en_periodo": sorted(kpis),
        "periodos_disponibles": periodos_disponibles,
    }


def _migrate_mappings(mappings: dict) -> dict:
    """Upgrade old bare-label keys (no ||) to section||label format using kpi_structure.json."""
    if not KPI_STRUCTURE_FILE.exists():
        return mappings
    structure = json.loads(KPI_STRUCTURE_FILE.read_text(encoding="utf-8"))
    changed = False
    for company, items in structure.items():
        if company not in mappings:
            continue
        # Build label -> section lookup (first occurrence wins for unique labels)
        label_to_section: dict[str, str] = {}
        cur_sec = ""
        for item in items:
            if item["type"] == "header":
                cur_sec = item["label"]
            elif item["label"] not in label_to_section:
                label_to_section[item["label"]] = cur_sec
        old_maps = mappings[company]
        new_maps: dict = {}
        for key, val in old_maps.items():
            if "||" in key:
                new_maps[key] = val
            elif key in label_to_section:
                new_key = f"{label_to_section[key]}||{key}"
                new_maps[new_key] = val
                changed = True
            else:
                new_maps[key] = val
        mappings[company] = new_maps
    if changed:
        mapping_store.save_mappings(mappings)
    return mappings


@router.get("/mappings")
def get_mappings():
    mappings = mapping_store.load_mappings()
    return _migrate_mappings(mappings)


@router.post("/mappings")
def save_mappings(data: dict):
    mapping_store.save_mappings(data)
    return {"ok": True}
