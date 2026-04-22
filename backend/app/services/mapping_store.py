"""
Persists user-defined KPI mappings to data/kpi_mappings.json.

Structure:
{
  "MLP": { "Variables Mineras||Movimiento mina sulfuros": "parquet_kpi_name", ... },
  "CEN": { ... }
}
"""
import json
from ..config import settings

MAPPINGS_FILE = settings.parquet_path.parent / "kpi_mappings.json"


def load_mappings() -> dict:
    if MAPPINGS_FILE.exists():
        return json.loads(MAPPINGS_FILE.read_text(encoding="utf-8"))
    return {}


def save_mappings(data: dict) -> None:
    MAPPINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_company_mappings(company: str) -> dict[str, str]:
    return load_mappings().get(company, {})
