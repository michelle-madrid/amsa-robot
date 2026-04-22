from pydantic import BaseModel
from typing import Optional


class AutomationRequest(BaseModel):
    anio: int
    mes: int
    companies: list[str] = ["MLP", "CEN", "ANT", "CMZ"]
    tipo: str = "Real"


class KpiPreview(BaseModel):
    kpi_id: str
    compania: str
    kpi: str
    category: str
    matched: bool
    excel_row: Optional[int] = None
    valores: dict[str, Optional[float]]


class PreviewResponse(BaseModel):
    anio: int
    mes: int
    tipo: str
    total_kpis: int
    matched_kpis: int
    unmatched_kpis: int
    kpis: list[KpiPreview]


class CompanyResult(BaseModel):
    compania: str
    kpis_escritos: int
    kpis_no_mapeados: int
    kpis_no_mapeados_detalle: list[str]
    ok: bool


class AutomationResult(BaseModel):
    anio: int
    mes: int
    tipo: str
    companies: list[CompanyResult]
    archivo_salida: str
    total_escritos: int


class AvailablePeriods(BaseModel):
    periodos: list[int]
    anios: list[int]
    meses: list[int]
    tipos: list[str]
    companias: list[str]
