"""
KPI value overrides — persisted per company in data/kpi_overrides.json.
Structure: {company: {lk: {tipo: {colkey: value}}}}
colkey examples: "2026_3" (year_month), "mes", "ytd"
"""
import json
from pathlib import Path
from fastapi import APIRouter

router = APIRouter(prefix="/api/overrides", tags=["overrides"])

OVERRIDES_PATH = Path(__file__).resolve().parents[3] / "data" / "kpi_overrides.json"


def _load() -> dict:
    if OVERRIDES_PATH.exists():
        return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    return {}


def _save(data: dict) -> None:
    OVERRIDES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@router.get("/{company}")
def get_overrides(company: str):
    return _load().get(company, {})


@router.post("/{company}")
def save_overrides(company: str, body: dict):
    all_ovr = _load()
    all_ovr[company] = body
    _save(all_ovr)
    return {"ok": True}


@router.delete("/{company}")
def clear_overrides(company: str):
    all_ovr = _load()
    all_ovr.pop(company, None)
    _save(all_ovr)
    return {"ok": True}
