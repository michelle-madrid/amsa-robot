import json
from fastapi import APIRouter
from ..config import settings

router = APIRouter(prefix="/api/params", tags=["params"])

PARAMS_FILE = settings.parquet_path.parent / "params_financieros.json"

COMPANIES = ["MLP", "CEN", "ANT", "CMZ"]


def _load() -> dict:
    if PARAMS_FILE.exists():
        data = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    else:
        data = {}
    data.setdefault("kpis", {})
    data.setdefault("exp_tc", {c: None for c in COMPANIES})
    return data


@router.get("")
def get_params():
    return _load()


@router.post("")
def save_params(data: dict):
    PARAMS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True}
