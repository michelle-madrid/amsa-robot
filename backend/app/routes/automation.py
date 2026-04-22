from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..config import settings
from ..schemas.models import (
    AutomationRequest,
    AutomationResult,
    AvailablePeriods,
    CompanyResult,
    KpiPreview,
    PreviewResponse,
)
from ..services import parquet_service as pq
from ..services import excel_service as xl
from ..services import mapping_store
from ..services.mapper_service import discover_sheet_structure, find_excel_row

import openpyxl

router = APIRouter(prefix="/api", tags=["automation"])


def _get_df():
    if not settings.parquet_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Parquet no encontrado: {settings.parquet_path}",
        )
    return pq.get_cached_df(settings.parquet_path)


@router.get("/periodos", response_model=AvailablePeriods)
def get_periodos():
    df = _get_df()
    return pq.get_available_periods(df)


@router.post("/preview", response_model=PreviewResponse)
def preview(req: AutomationRequest):
    df = _get_df()
    df_filtered = pq.filter_data(df, req.anio, req.mes, req.companies, req.tipo)

    if not settings.template_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Template Excel no encontrado: {settings.template_path}",
        )
    wb = openpyxl.load_workbook(settings.template_path, read_only=False, data_only=True)

    all_mappings = mapping_store.load_mappings()
    kpis: list[KpiPreview] = []
    for _, row in df_filtered.iterrows():
        company = row["compania"]
        mapping = discover_sheet_structure(wb, company)
        company_mappings = all_mappings.get(company, {})
        excel_row = find_excel_row(row, mapping, company_mappings)
        valores = pq.row_to_monthly_values(row)
        kpis.append(
            KpiPreview(
                kpi_id=str(row.get("kpi_id", "")),
                compania=company,
                kpi=str(row.get("kpi", "")),
                category=str(row.get("category", "")),
                matched=excel_row is not None,
                excel_row=excel_row,
                valores=valores,
            )
        )

    matched = sum(1 for k in kpis if k.matched)
    return PreviewResponse(
        anio=req.anio,
        mes=req.mes,
        tipo=req.tipo,
        total_kpis=len(kpis),
        matched_kpis=matched,
        unmatched_kpis=len(kpis) - matched,
        kpis=kpis,
    )


@router.post("/ejecutar", response_model=AutomationResult)
def ejecutar(req: AutomationRequest):
    df = _get_df()
    df_filtered = pq.filter_data(df, req.anio, req.mes, req.companies, req.tipo)

    if df_filtered.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Sin datos para año={req.anio}, mes={req.mes}, tipo={req.tipo}",
        )
    if not settings.template_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Template Excel no encontrado: {settings.template_path}",
        )

    output_path, company_results = xl.run_automation(
        template_path=settings.template_path,
        output_dir=settings.output_dir,
        df_filtered=df_filtered,
        anio=req.anio,
        mes=req.mes,
        companies=req.companies,
    )

    total = sum(r["kpis_escritos"] for r in company_results)
    return AutomationResult(
        anio=req.anio,
        mes=req.mes,
        tipo=req.tipo,
        companies=[CompanyResult(**r) for r in company_results],
        archivo_salida=output_path.name,
        total_escritos=total,
    )


@router.get("/descargar/{filename}")
def descargar(filename: str):
    file_path = settings.output_dir / filename
    if not file_path.exists() or file_path.suffix != ".xlsx":
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(
        path=file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
