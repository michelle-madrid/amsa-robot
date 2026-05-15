"""
Flash Producciones — resumen mensual y acumulado de producción por compañía.
"""
import io
import re
import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from ..config import settings
from ..services import parquet_service as pq
from ..services.mapping_store import load_mappings
from .costos_ajustados import _fetch_snapshot   # reutiliza snapshot con enriquecimiento

router = APIRouter(prefix="/api/flash-producciones", tags=["flash"])

PARAMS_FIN_PATH = Path(__file__).resolve().parents[3] / "data" / "params_financieros.json"

COMPANIES  = ["MLP", "CEN", "ANT", "CMZ"]
COMP_LABEL = {"MLP": "Los Pelambres", "CEN": "Centinela", "ANT": "Antucoya",
              "CMZ": "Zaldívar (50%)", "GM": "Grupo Minero"}
MONTH_BY_NUM = {i + 1: f for i, f in enumerate(pq.MONTH_FIELDS)}

# ── Definición de filas ───────────────────────────────────────────────────────
# lks     : lista de claves en kpi_mappings.json a probar (en orden)
# sum_lks : True → sumar todos los lks con valor; False → usar el primero no nulo
# scale   : multiplicar el valor por este factor (ej. 0.001 kt→Mt)
# agg       : "sum" | "wavg" — cómo agregar para Grupo Minero
#             "wavg" usa tratamiento como ponderador
# co_scale  : True → multiplicar por company_cost_scale (ej. 0.5 para CMZ)
#             False → no escalar (porcentajes/tasas)
FLASH_ROWS = [
    {"key": "tratamiento",  "label": "Tratamiento/Beneficio", "unit": "Mt",
     "lks": ["Variables Mineras||Procesamiento", "Variables Mineras||Beneficio"],
     "sum_lks": True,  "scale": 0.001, "bold": False, "sep": False, "agg": "sum",  "co_scale": True},
    {"key": "ley",          "label": "Ley",                   "unit": "%",
     "lks": ["Variables Mineras||Ley sulfuros", "Variables Mineras||Ley oxidos"],
     "sum_lks": False, "scale": 1.0,   "bold": False, "sep": False, "agg": "wavg", "co_scale": False},
    {"key": "recuperacion", "label": "Recuperación",          "unit": "%",
     "lks": ["Variables Mineras||Recuperación sulfuros", "Variables Mineras||Recuperación óxidos"],
     "sum_lks": False, "scale": 1.0,   "bold": False, "sep": False, "agg": "wavg", "co_scale": False},
    {"key": "rom_ripios",   "label": "ROM/Ripios/Otros",      "unit": "kt",
     "lks": ["Variables Mineras||Remanejo sulfuros", "Variables Mineras||Remanejo oxidos"],
     "total_lk": "Variables Mineras||Remanejo total",
     "sum_lks": True,  "scale": 0.001, "bold": False, "sep": False, "agg": "sum",  "co_scale": True},
    {"key": "inventarios",  "label": "Inventarios y otros",   "unit": "kt",
     "lks": ["Variables Mineras||Var. Inv."],
     "sum_lks": False, "scale": 1.0,   "bold": False, "sep": False, "agg": "sum",  "co_scale": True},
    {"key": "cu_fino",      "label": "Total cobre fino",      "unit": "kt",
     "lks": ["Variables Mineras||CuFino", "Variables Mineras||Cátodos"],
     "sum_lks": True,  "scale": 1.0,   "bold": True,  "sep": False, "agg": "sum",  "co_scale": True},
    {"key": "moly",         "label": "Molibdeno",             "unit": "kt",
     "lks": ["Subproductos||Cantidades Producidas||Moly"],
     "sum_lks": False, "scale": 1.0,   "bold": False, "sep": True,  "agg": "sum",  "co_scale": True},
    {"key": "oro",          "label": "Oro",                   "unit": "koz",
     "lks": ["Subproductos||Cantidades Producidas||Oro"],
     "sum_lks": False, "scale": 1.0,   "bold": False, "sep": False, "agg": "sum",  "co_scale": True},
    {"key": "plata",        "label": "Plata",                 "unit": "koz",
     "lks": ["Subproductos||Cantidades Producidas||Plata"],
     "sum_lks": False, "scale": 1.0,   "bold": False, "sep": False, "agg": "sum",  "co_scale": True},
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r'[^\x00-\x7F]', '', s.replace('\n', '').replace('\r', '')).lower()


def _resolve_snap(snapshot: dict, src: str) -> dict:
    """Busca una clave en el snapshot con exact match, suffix match y norm match."""
    if src in snapshot:
        return snapshot[src]
    suffix = f"||{src}"
    m = [v for k, v in snapshot.items() if k.endswith(suffix)]
    if len(m) == 1:
        return m[0]
    src_n = _norm(src)
    m2 = [v for k, v in snapshot.items() if _norm(k) == src_n]
    if len(m2) == 1:
        return m2[0]
    if "||" in src:
        plain = src.split("||", 1)[1]
        src_p = _norm(plain)
        m3 = [v for k, v in snapshot.items() if _norm(k).endswith('||' + src_p) or _norm(k) == src_p]
        if len(m3) == 1:
            return m3[0]
    return {}


def _parquet_srcs(mapped_val) -> list[str]:
    """Extrae lista de KPI names del parquet a partir del valor de kpi_mappings."""
    if not mapped_val or mapped_val == "__NA__":
        return []
    if isinstance(mapped_val, dict):
        return []  # fórmulas derivadas: ignorar en Flash
    if isinstance(mapped_val, list):
        return [s for s in mapped_val if s and s != "__NA__"]
    if isinstance(mapped_val, str) and mapped_val:
        return [mapped_val]
    return []


def _get_vals(snapshot: dict, mappings: dict, row: dict, month_field: str):
    """Retorna (mes_raw, ytd_raw) sin aplicar escala, para poder ponderar."""
    mes_total = None
    ytd_total = None
    # Si hay total_lk mapeado, usarlo con prioridad (evita doble conteo con sulfuros+óxidos)
    total_lk = row.get("total_lk")
    if total_lk and mappings.get(total_lk):
        for src in _parquet_srcs(mappings.get(total_lk)):
            data = _resolve_snap(snapshot, src)
            if data:
                mv = data.get(month_field)
                yv = data.get("ytd")
                if mv is not None:
                    mes_total = (mes_total or 0.0) + mv
                if yv is not None:
                    ytd_total = (ytd_total or 0.0) + yv
        if mes_total is not None or ytd_total is not None:
            return mes_total, ytd_total
    for lk in row["lks"]:
        mapped = mappings.get(lk)
        srcs = _parquet_srcs(mapped)
        for src in srcs:
            data = _resolve_snap(snapshot, src)
            if not data:
                continue
            mv = data.get(month_field)
            yv = data.get("ytd")
            if mv is not None:
                mes_total = (mes_total or 0.0) + mv
            if yv is not None:
                ytd_total = (ytd_total or 0.0) + yv
            if not row["sum_lks"]:
                break   # primer lk con valor
        else:
            continue
        if not row["sum_lks"] and (mes_total is not None or ytd_total is not None):
            break
    return mes_total, ytd_total


def _load_company_scales() -> dict:
    try:
        fin = json.loads(PARAMS_FIN_PATH.read_text(encoding="utf-8"))
        return fin.get("company_cost_scale", {})
    except Exception:
        return {}


def _r(v, scale=1.0):
    if v is None:
        return None
    return round(v * scale, 6)


def _inv(cu, trat, ley, rec):
    """Inventarios y otros = CuFino_kt − Tratamiento_kt × (Ley%/100) × (Rec%/100)."""
    if cu is None or trat is None or ley is None or rec is None:
        return None
    return round(cu - trat * (ley / 100.0) * (rec / 100.0), 6)


def _compute_company(snap_r, snap_b, mappings, month_field):
    """Retorna {key: {r_mes, b_mes, r_ytd, b_ytd}} para una compañía."""
    result = {}
    for row in FLASH_ROWS:
        if row["key"] == "inventarios":
            continue  # se calcula como valor derivado al final
        r_mes_raw, r_ytd_raw = _get_vals(snap_r, mappings, row, month_field)
        b_mes_raw, b_ytd_raw = _get_vals(snap_b, mappings, row, month_field)
        result[row["key"]] = {
            "r_mes": r_mes_raw, "b_mes": b_mes_raw,
            "r_ytd": r_ytd_raw, "b_ytd": b_ytd_raw,
        }

    cf = result.get("cu_fino",      {})
    tr = result.get("tratamiento",  {})
    ly = result.get("ley",          {})
    rc = result.get("recuperacion", {})
    result["inventarios"] = {
        "r_mes": _inv(cf.get("r_mes"), tr.get("r_mes"), ly.get("r_mes"), rc.get("r_mes")),
        "b_mes": _inv(cf.get("b_mes"), tr.get("b_mes"), ly.get("b_mes"), rc.get("b_mes")),
        "r_ytd": _inv(cf.get("r_ytd"), tr.get("r_ytd"), ly.get("r_ytd"), rc.get("r_ytd")),
        "b_ytd": _inv(cf.get("b_ytd"), tr.get("b_ytd"), ly.get("b_ytd"), rc.get("b_ytd")),
    }
    return result


def _grupo_minero(companies_data: dict) -> dict:
    """Agrega datos de todas las compañías en Grupo Minero."""
    row_by_key = {r["key"]: r for r in FLASH_ROWS}
    gm = {}
    for row in FLASH_ROWS:
        key = row["key"]
        vals = [companies_data[c].get(key, {}) for c in COMPANIES if c in companies_data]
        if row["agg"] == "sum":
            def _sadd(field):
                vs = [v.get(field) for v in vals if v.get(field) is not None]
                return sum(vs) if vs else None
            gm[key] = {f: _sadd(f) for f in ("r_mes", "b_mes", "r_ytd", "b_ytd")}
        else:  # weighted average by tratamiento
            trat_key = "tratamiento"
            trat_vals = [companies_data[c].get(trat_key, {}) for c in COMPANIES if c in companies_data]
            def _wavg(value_field, weight_field):
                pairs = [(vals[i].get(value_field), trat_vals[i].get(weight_field))
                         for i in range(len(vals))
                         if vals[i].get(value_field) is not None and trat_vals[i].get(weight_field) is not None]
                if not pairs:
                    return None
                num = sum(v * w for v, w in pairs)
                den = sum(w for _, w in pairs)
                return num / den if den else None
            gm[key] = {
                "r_mes": _wavg("r_mes", "r_mes"),
                "b_mes": _wavg("b_mes", "b_mes"),
                "r_ytd": _wavg("r_ytd", "r_ytd"),
                "b_ytd": _wavg("b_ytd", "b_ytd"),
            }
    return gm


# ── Endpoint principal ────────────────────────────────────────────────────────

@router.get("")
def get_flash(año: int, mes: int, tipo_r: str = "Real", tipo_b: str = "Ppto"):
    df_all  = pq.get_cached_df(settings.parquet_path)
    all_maps = load_mappings()
    month_field = MONTH_BY_NUM[mes]

    co_scales = _load_company_scales()

    companies_data: dict[str, dict] = {}
    for co in COMPANIES:
        df_co = df_all[df_all["compania"] == co]
        snap_r, _ = _fetch_snapshot(df_co, tipo_r, año, mes)
        snap_b, _ = _fetch_snapshot(df_co, tipo_b, año, mes)
        maps = all_maps.get(co, {})
        raw = _compute_company(snap_r, snap_b, maps, month_field)
        sc = co_scales.get(co, 1.0)
        if sc != 1.0:
            scalable = {r["key"] for r in FLASH_ROWS if r.get("co_scale", True)}
            raw = {
                key: ({f: (v * sc if v is not None else None) for f, v in vals.items()}
                      if key in scalable else vals)
                for key, vals in raw.items()
            }
        companies_data[co] = raw

    gm = _grupo_minero(companies_data)
    companies_data["GM"] = gm

    # Aplicar escala y construir respuesta
    rows_out = []
    for row in FLASH_ROWS:
        key   = row["key"]
        scale = row["scale"]
        comp_out = {}
        for co, cdata in companies_data.items():
            v = cdata.get(key, {})
            comp_out[co] = {
                "r_mes":  _r(v.get("r_mes"),  scale),
                "b_mes":  _r(v.get("b_mes"),  scale),
                "r_ytd":  _r(v.get("r_ytd"),  scale),
                "b_ytd":  _r(v.get("b_ytd"),  scale),
            }
        rows_out.append({
            "key":   key,
            "label": row["label"],
            "unit":  row["unit"],
            "bold":  row["bold"],
            "sep":   row["sep"],
            "companies": comp_out,
        })

    return {"año": año, "mes": mes, "rows": rows_out}


# ── Excel download ────────────────────────────────────────────────────────────

@router.get("/excel")
def get_flash_excel(año: int, mes: int, tipo_r: str = "Real", tipo_b: str = "Ppto"):
    data = get_flash(año=año, mes=mes, tipo_r=tipo_r, tipo_b=tipo_b)
    mes_name = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"][mes - 1]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Flash Producciones"

    COLS = COMPANIES + ["GM"]
    TEAL   = PatternFill("solid", fgColor="1A3A4A")
    BLUE_H = PatternFill("solid", fgColor="1E6F7E")
    GOLD   = PatternFill("solid", fgColor="FFF3CD")
    GREEN  = PatternFill("solid", fgColor="D1FAE5")
    GREY   = PatternFill("solid", fgColor="F3F4F6")
    WHITE  = Font(color="FFFFFF", bold=True)
    BOLD   = Font(bold=True)
    THIN   = Side(style="thin", color="CCCCCC")
    DEF_B  = Border(bottom=Side(style="thin", color="AAAAAA"))

    def _fmt(v, unit, efecto=False):
        if v is None:
            return "—"
        if unit == "%":
            s = f"{v:.2f}%"
        elif unit == "Mt":
            s = f"{v:.1f}"
        elif unit == "koz" and abs(v) >= 100:
            s = f"{round(v)}"
        else:
            s = f"{v:.1f}"
        if efecto and v < 0:
            return f"({s.lstrip('-')})"
        return s

    def _wv(ws, r, c, val, **kw):
        cell = ws.cell(row=r, column=c, value=val)
        for k, v in kw.items():
            setattr(cell, k, v)
        return cell

    row = 1
    # Title
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2 + len(COLS) * 3)
    c = ws.cell(row=row, column=1, value=f"Flash Producciones — {mes_name} {año}")
    c.font = Font(bold=True, size=12)
    row += 1

    for section, period_keys, section_label in [
        ("mes",  ("r_mes",  "b_mes"),  f"MES {mes_name}"),
        ("ytd",  ("r_ytd",  "b_ytd"),  f"Acumulado Ene–{mes_name}"),
    ]:
        rk, bk = period_keys

        # Section header
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2 + len(COLS) * 3)
        c = ws.cell(row=row, column=1, value=section_label)
        c.font = BOLD; c.fill = TEAL; c.font = WHITE
        row += 1

        # Company headers
        ws.cell(row=row, column=1, value="KPI").font = BOLD
        ws.cell(row=row, column=2, value="Und").font = BOLD
        col = 3
        for co in COLS:
            ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + 2)
            c = ws.cell(row=row, column=col, value=COMP_LABEL[co])
            c.font = WHITE; c.fill = BLUE_H; c.alignment = Alignment(horizontal="center")
            col += 3
        row += 1

        # Sub-headers Real/Ppto/Efecto
        col = 3
        for co in COLS:
            for i, sub in enumerate(["Real","Ppto","Efecto"]):
                c = ws.cell(row=row, column=col + i, value=sub)
                c.font = BOLD
                c.fill = GREY
            col += 3
        row += 1

        # Data rows
        for flash_row in data["rows"]:
            if flash_row["sep"]:
                ws.append([""] * (2 + len(COLS) * 3))
                row += 1
            unit = flash_row["unit"]
            r_ws = ws.cell(row=row, column=1, value=flash_row["label"])
            ws.cell(row=row, column=2, value=unit)
            if flash_row["bold"]:
                r_ws.font = BOLD
            col = 3
            for co in COLS:
                cv = flash_row["companies"].get(co, {})
                rv = cv.get(rk); bv = cv.get(bk)
                ef = round(rv - bv, 4) if rv is not None and bv is not None else None
                fill = GREEN if flash_row["bold"] else None
                for i, (val, is_ef) in enumerate([(rv, False), (bv, False), (ef, True)]):
                    c = ws.cell(row=row, column=col + i, value=_fmt(val, unit, is_ef))
                    c.alignment = Alignment(horizontal="right")
                    if fill:
                        c.fill = fill
                    if is_ef and ef is not None and ef < 0:
                        c.font = Font(color="CC0000")
                col += 3
            row += 1

        row += 1  # blank between sections

    # Column widths
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 5
    for i in range(len(COLS) * 3):
        col_letter = openpyxl.utils.get_column_letter(3 + i)
        ws.column_dimensions[col_letter].width = 8

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"Flash_Producciones_{año}_{mes:02d}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
