import json
from fastapi import APIRouter, Request
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
    data.setdefault("exp_tc", {c: None for c in COMPANIES + ["GM"]})
    data.setdefault("costos_fijo_var", {
        "MLP": [
            {"key": "mina",   "label": "Mina y sub-áreas",      "fijo": 0.30},
            {"key": "planta", "label": "Planta Concentradora",   "fijo": 0.35},
            {"key": "ga",     "label": "G&A y otros",            "fijo": 1.00},
        ],
        "CEN": [
            {"key": "mina",      "label": "Mina y sub-áreas",    "fijo": 0.49},
            {"key": "planta_c",  "label": "Planta Concentradora","fijo": 0.29},
            {"key": "planta_sx", "label": "Planta SX-EW",        "fijo": 0.40},
            {"key": "ga",        "label": "G&A y otros",         "fijo": 1.00},
        ],
        "ANT": [
            {"key": "mina",      "label": "Mina y sub-áreas",    "fijo": 0.50},
            {"key": "planta_sx", "label": "Planta SX-EW",        "fijo": 0.60},
            {"key": "ga",        "label": "G&A y otros",         "fijo": 1.00},
        ],
        "CMZ": [
            {"key": "mina",      "label": "Mina y sub-áreas",    "fijo": 0.50},
            {"key": "planta_sx", "label": "Planta SX-EW",        "fijo": 0.60},
            {"key": "ga",        "label": "G&A y otros",         "fijo": 1.00},
        ],
    })
    _mlp_sa = [
        {"key":"mina","label":"Mina","subareas":[
            {"key":"perforacion","label":"Perforación","fijo":0.30},
            {"key":"tronadura","label":"Tronadura","fijo":0.30},
            {"key":"carguio","label":"Carguío","fijo":0.30},
            {"key":"transporte","label":"Transporte","fijo":0.30},
            {"key":"serv_apoyo","label":"Servicios de Apoyo Mina","fijo":0.30},
            {"key":"adm_op","label":"Adm. Operación y Gestión","fijo":0.30},
        ]},
        {"key":"planta_conc","label":"Planta Concentradora + Puerto","subareas":[
            {"key":"chancado_c","label":"Chancado","fijo":0.35},
            {"key":"molienda","label":"Molienda","fijo":0.35},
            {"key":"flotacion","label":"Flotación","fijo":0.35},
            {"key":"planta_moly","label":"Planta Molibdeno","fijo":0.35},
            {"key":"tft","label":"TFT","fijo":0.35},
            {"key":"puerto","label":"Puerto","fijo":0.35},
            {"key":"adm_serv_c","label":"Adm. y Servicios","fijo":0.35},
        ]},
        {"key":"planta_sx","label":"Planta SX-EW","subareas":[
            {"key":"chancado_sx","label":"Chancado","fijo":0.35},
            {"key":"aglom_ap","label":"Aglomeración / Apilamiento","fijo":0.35},
            {"key":"lixiv","label":"Lixiviación","fijo":0.35},
            {"key":"rom_sl","label":"ROM / SL","fijo":0.35},
            {"key":"sxew","label":"SX-EW","fijo":0.35},
            {"key":"adm_sx","label":"Admin.","fijo":0.35},
            {"key":"otros_sx","label":"Otros costos planta SX-EW","fijo":0.35},
        ]},
        {"key":"ga","label":"G&A","subareas":[{"key":"ga_mlp","label":"G&A","fijo":1.00}]},
    ]
    data.setdefault("costos_aj_grupos", {
        "MLP": _mlp_sa,
        "CEN": [
            {"key":"mina","label":"Mina","subareas":[
                {"key":"perforacion","label":"Perforación","fijo":0.49},
                {"key":"tronadura","label":"Tronadura","fijo":0.49},
                {"key":"carguio","label":"Carguío","fijo":0.49},
                {"key":"transporte","label":"Transporte","fijo":0.49},
                {"key":"serv_apoyo","label":"Servicios de Apoyo Mina","fijo":0.49},
                {"key":"adm_op","label":"Adm. Operación y Gestión","fijo":0.49},
            ]},
            {"key":"planta_conc","label":"Planta Concentradora","subareas":[
                {"key":"chancado_c","label":"Chancado","fijo":0.29},
                {"key":"molienda","label":"Molienda","fijo":0.29},
                {"key":"flotacion","label":"Flotación","fijo":0.29},
                {"key":"sistemas_aux","label":"Sistemas Auxiliares","fijo":0.29},
                {"key":"planta_moly","label":"Planta Molibdeno","fijo":0.29},
                {"key":"transp_conc","label":"Transporte de Concentrado","fijo":0.29},
                {"key":"relaves","label":"Relaves y Depósitos","fijo":0.29},
                {"key":"serv_ap_conc","label":"Servicios de apoyo","fijo":0.29},
                {"key":"adm_serv_c","label":"Adm. y Servicios","fijo":0.29},
            ]},
            {"key":"puerto","label":"Puerto","subareas":[
                {"key":"pto_transp","label":"Transporte Concentrado","fijo":0.35},
                {"key":"pto_filtrado","label":"Planta Filtrado","fijo":0.35},
                {"key":"pto_muelle","label":"Muelle","fijo":0.35},
                {"key":"pto_mant","label":"Mantenimiento","fijo":0.35},
                {"key":"pto_serv","label":"Servicios de Apoyo","fijo":0.35},
                {"key":"pto_adm","label":"Admin.","fijo":0.35},
            ]},
            {"key":"planta_sx","label":"Planta SX-EW","subareas":[
                {"key":"chancado_sx","label":"Chancado SX-EW","fijo":0.40},
                {"key":"aglom_ap","label":"Aglomeración / Apilamiento","fijo":0.40},
                {"key":"lixiv","label":"Lixiviación","fijo":0.40},
                {"key":"rom_sl","label":"ROM / SL","fijo":0.40},
                {"key":"sxew","label":"SX-EW","fijo":0.40},
                {"key":"adm_sx","label":"Admin.","fijo":0.40},
                {"key":"otros_sx","label":"Otros costos planta SX-EW","fijo":0.40},
            ]},
            {"key":"ga","label":"G&A","subareas":[{"key":"ga_cen","label":"G&A","fijo":1.00}]},
            {"key":"serv_apoyo_g","label":"Servicios de Apoyo","subareas":[{"key":"serv_apoyo_total","label":"Total","fijo":0.49}]},
            {"key":"vi_grp","label":"Var. Inv.","subareas":[{"key":"vi_total","label":"Var. Inv.","fijo":1.00}]},
            {"key":"dev_mina_grp","label":"Desarrollo Mina","subareas":[{"key":"vi_devmina","label":"Desarrollo Mina","fijo":1.00}]},
            {"key":"ifrs16_grp","label":"IFRS16","subareas":[{"key":"vi_ifrs16","label":"IFRS16","fijo":1.00}]},
        ],
        "ANT": [
            {"key":"mina","label":"Mina","subareas":[
                {"key":"perforacion","label":"Perforación","fijo":0.50},
                {"key":"tronadura","label":"Tronadura","fijo":0.50},
                {"key":"carguio","label":"Carguío","fijo":0.50},
                {"key":"transporte","label":"Transporte","fijo":0.50},
                {"key":"serv_apoyo","label":"Servicios de Apoyo Mina","fijo":0.50},
                {"key":"adm_op","label":"Adm. Operación y Gestión","fijo":0.50},
            ]},
            {"key":"planta_sx","label":"Planta SX-EW","subareas":[
                {"key":"aglom_ap","label":"Aglomeración / Apilamiento","fijo":0.60},
                {"key":"lixiv","label":"Lixiviación","fijo":0.60},
                {"key":"rom_sl","label":"ROM / SL","fijo":0.60},
                {"key":"sxew","label":"SX-EW","fijo":0.60},
                {"key":"adm_sx","label":"Admin.","fijo":0.60},
            ]},
            {"key":"ga","label":"G&A","subareas":[{"key":"ga_ant","label":"G&A","fijo":1.00}]},
        ],
        "CMZ": [
            {"key":"mina","label":"Mina","subareas":[
                {"key":"perforacion","label":"Perforación","fijo":0.50},
                {"key":"tronadura","label":"Tronadura","fijo":0.50},
                {"key":"carguio","label":"Carguío","fijo":0.50},
                {"key":"transporte","label":"Transporte","fijo":0.50},
                {"key":"serv_apoyo","label":"Servicios de Apoyo Mina","fijo":0.50},
                {"key":"adm_op","label":"Adm. Operación y Gestión","fijo":0.50},
            ]},
            {"key":"planta_sx","label":"Planta SX-EW","subareas":[
                {"key":"aglom_ap","label":"Aglomeración / Apilamiento","fijo":0.60},
                {"key":"lixiv","label":"Lixiviación","fijo":0.60},
                {"key":"rom_sl","label":"ROM / SL","fijo":0.60},
                {"key":"sxew","label":"SX-EW","fijo":0.60},
                {"key":"adm_sx","label":"Admin.","fijo":0.60},
            ]},
            {"key":"ga","label":"G&A","subareas":[{"key":"ga_cmz","label":"G&A","fijo":1.00}]},
        ],
    })
    data.setdefault("costos_aj_summary", [
        {"key":"tcrc",         "label":"TC/RC",                       "fijo":0.0},
        {"key":"comer",        "label":"Comercialización",            "fijo":0.0},
        {"key":"da",           "label":"Depreciación / Amortización", "fijo":1.0},
        {"key":"credito_subp", "label":"Crédito Subproductos",        "fijo":0.0},
        {"key":"otros_fin",    "label":"Otros Items Financieros",     "fijo":0.0},
    ])
    return data


@router.get("")
def get_params():
    return _load()


@router.post("")
async def save_params(request: Request):
    data = await request.json()
    PARAMS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True}
