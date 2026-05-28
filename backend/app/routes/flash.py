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
from .explicaciones import get_explicaciones     # efectos de descomposición (ktCuf por variable)

router = APIRouter(prefix="/api/flash-producciones", tags=["flash"])

PARAMS_FIN_PATH = Path(__file__).resolve().parents[3] / "data" / "params_financieros.json"
FORMULA_PARAMS_PATH = Path(__file__).resolve().parents[3] / "data" / "formula_params.json"
# Config por celda del Flash: qué KPI (de Descomposición o Visualizar) usar para
# Real/Ppto/Efecto de cada combinación columna×fila. Estructura:
#   { "<col>": { "<rowkey>": { "real": {src,kpi}, "ppto": {...}, "efecto": {...} } } }
FLASH_CELL_CFG_PATH = Path(__file__).resolve().parents[3] / "data" / "flash_cell_config.json"

# Compañías físicas en el parquet (CEN se divide luego en dos vías)
COMPANIES  = ["MLP", "CEN", "ANT", "CMZ"]
# Columnas que se muestran (CEN → CuCons + Cátodos), en el orden del Excel
DISPLAY_COLS = ["MLP", "CEN_CC", "CEN_CAT", "ANT", "CMZ", "GM"]
COMP_LABEL = {"MLP": "Los Pelambres",
              "CEN_CC": "Centinela CuCons", "CEN_CAT": "Centinela Cátodos",
              "ANT": "Antucoya", "CMZ": "Zaldívar (50%)", "GM": "Grupo Minero"}

# Filas que en Centinela cambian de fuente según la vía (concentradora vs cátodos).
# Cada entrada redefine los `lks` a usar para esa vía; el resto de filas usa lo de FLASH_ROWS.
# Vía CuCons (sulfuros / Mineral Procesado / concentradora):
CEN_CC_LKS = {
    "tratamiento":  ["Variables Mineras||Procesamiento"],
    "ley":          ["Variables Mineras||Ley sulfuros"],
    "recuperacion": ["Variables Mineras||Recuperación sulfuros"],
    "cu_fino":      ["Variables Mineras||CuFino"],
    # ROM/Ripios/Otros no aplica a concentradora → va a Cátodos
    "rom_ripios":   [],
}
# Vía Cátodos (óxidos / Mineral Beneficiado / SX-EW / lixiviación):
CEN_CAT_LKS = {
    "tratamiento":  ["Variables Mineras||Beneficio"],
    "ley":          ["Variables Mineras||Ley oxidos"],
    "recuperacion": ["Variables Mineras||Recuperación óxidos"],
    "cu_fino":      ["Variables Mineras||Cátodos"],
    # ROM/Ripios/Otros (óxidos) sí va aquí; subproductos van a CuCons
    "moly":         [],
    "oro":          [],
    "plata":        [],
}
# MLP es concentradora pura → ROM/Ripios/Otros no aplica
MLP_LKS = {
    "rom_ripios":   [],
}
# Antucoya y Zaldívar son operaciones solo de óxidos (cátodos/lixiviación):
# su Ley/Rec corresponden al KPI "Ley óxidos"/"Recuperación óxidos" mapeado en Config.
OXIDO_ONLY_LKS = {
    "ley":          ["Variables Mineras||Ley oxidos"],
    "recuperacion": ["Variables Mineras||Recuperación óxidos"],
}
# Zaldívar (CMZ): igual que óxidos, pero ROM/Ripios = CuFino + ROM Dinámico/Ripos
# (el /1000 lo da scale=0.001 y el /2 el factor 50% de co_scale).
CMZ_LKS = {
    **OXIDO_ONLY_LKS,
    "rom_ripios": ["TC/RC & Comercialización||Unidades||CuFino",
                   "Variables Mineras||ROM Dinámico /Ripos"],
}
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
     "lks": ["Variables Mineras||ROM Dinámico /Ripos"],
     "sum_lks": False, "scale": 0.001, "bold": False, "sep": False, "agg": "sum",  "co_scale": True},
    {"key": "inventarios",  "label": "Inventarios y otros",   "unit": "kt",
     # Valor DERIVADO por fórmula (CuFino − Trat×Ley×Rec), no usa lks (ver _inv / _compute_company)
     "lks": [],
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
        # Mapeo con escalar {_s, src, op, scalar}: usar su fuente base
        if mapped_val.get("_s") and mapped_val.get("src"):
            return [mapped_val["src"]]
        return []  # otras fórmulas derivadas: ignorar en Flash
    if isinstance(mapped_val, list):
        return [s for s in mapped_val if s and s != "__NA__"]
    if isinstance(mapped_val, str) and mapped_val:
        return [mapped_val]
    return []


def _apply_op(a, op, b):
    """Aplica operación escalar (igual que Visualizar)."""
    if a is None or b is None:
        return a
    if op == "/":
        return a / b if b != 0 else None
    if op == "*":
        return a * b
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    return a


def _src_value(snapshot, src, month_field):
    """(mes, ytd) de una fuente del parquet, o (None, None)."""
    data = _resolve_snap(snapshot, src)
    if not data:
        return (None, None)
    return (data.get(month_field), data.get("ytd"))


def _eval_mapping(snapshot, mappings, mapped, month_field, depth=0):
    """Evalúa CUALQUIER tipo de mapeo de Config y devuelve (mes, ytd):
    directo (str), suma (list), escalar (_s), parquet-fórmula (_pf),
    fórmula entre KPIs (_f), o real/ppto (_rb → usa real)."""
    if depth > 6 or not mapped or mapped == "__NA__":
        return (None, None)
    if isinstance(mapped, str):
        return _src_value(snapshot, mapped, month_field)
    if isinstance(mapped, list):
        mt = yt = None
        for s in mapped:
            if not s or s == "__NA__":
                continue
            m, y = _src_value(snapshot, s, month_field)
            if m is not None: mt = (mt or 0.0) + m
            if y is not None: yt = (yt or 0.0) + y
        return (mt, yt)
    if isinstance(mapped, dict):
        if mapped.get("_s"):
            m, y = _src_value(snapshot, mapped.get("src", ""), month_field)
            sc = mapped.get("scalar")
            if sc is not None:
                op = mapped.get("op", "*")
                m = _apply_op(m, op, float(sc)); y = _apply_op(y, op, float(sc))
            return (m, y)
        if mapped.get("_pf"):
            op = mapped.get("op", "/")
            nm, ny = _src_value(snapshot, mapped.get("num", ""), month_field) if mapped.get("num") else (None, None)
            dm, dy = _src_value(snapshot, mapped.get("den", ""), month_field) if mapped.get("den") else (None, None)
            m = _apply_op(nm, op, dm); y = _apply_op(ny, op, dy)
            sc = mapped.get("scale")
            if sc:
                m = m * sc if m is not None else None
                y = y * sc if y is not None else None
            return (m, y)
        if mapped.get("_f"):
            op = mapped.get("op", "/")
            am, ay = _eval_mapping(snapshot, mappings, mappings.get(mapped.get("a")), month_field, depth + 1)
            bm, by = _eval_mapping(snapshot, mappings, mappings.get(mapped.get("b")), month_field, depth + 1)
            return (_apply_op(am, op, bm), _apply_op(ay, op, by))
        if mapped.get("_rb"):
            return _eval_mapping(snapshot, mappings, mapped.get("real"), month_field, depth + 1)
    return (None, None)


def _get_vals(snapshot: dict, mappings: dict, row: dict, month_field: str):
    """Retorna (mes_raw, ytd_raw) evaluando los lks de la fila (cualquier tipo de mapeo)."""
    # total_lk con prioridad (evita doble conteo sulfuros+óxidos)
    total_lk = row.get("total_lk")
    if total_lk and mappings.get(total_lk):
        m, y = _eval_mapping(snapshot, mappings, mappings.get(total_lk), month_field)
        if m is not None or y is not None:
            return (m, y)
    mes_total = ytd_total = None
    for lk in row["lks"]:
        m, y = _eval_mapping(snapshot, mappings, mappings.get(lk), month_field)
        if m is None and y is None:
            continue
        if m is not None: mes_total = (mes_total or 0.0) + m
        if y is not None: ytd_total = (ytd_total or 0.0) + y
        if not row["sum_lks"]:
            break   # primer lk con valor
    return mes_total, ytd_total


def _load_company_scales() -> dict:
    try:
        fin = json.loads(PARAMS_FIN_PATH.read_text(encoding="utf-8"))
        return fin.get("company_cost_scale", {})
    except Exception:
        return {}


def _load_visualizar_scales(company: str) -> dict:
    """Escalas de presentación por KPI definidas en Config (formula_params.json),
    p.ej. {'Variables Mineras||Ley oxidos': 100}. Devuelve {} si no hay."""
    try:
        data = json.loads(FORMULA_PARAMS_PATH.read_text(encoding="utf-8"))
        return data.get("visualizar_scales_per_company", {}).get(company, {})
    except Exception:
        return {}


# Catálogo de efectos seleccionables de "Descomposición de Varianzas"
# (claves del waterfall de Explicaciones) con etiqueta legible y unidad.
DESCOMP_CATALOG = [
    {"kpi": "ley_conc",          "label": "Efecto Ley (concentradora)",        "unit": "ktCuf"},
    {"kpi": "ley_hidro",         "label": "Efecto Ley (hidro/óxidos)",         "unit": "ktCuf"},
    {"kpi": "da_trat_conc",      "label": "Efecto Tratamiento (concentradora)", "unit": "ktCuf"},
    {"kpi": "da_trat_hidro",     "label": "Efecto Tratamiento (hidro)",        "unit": "ktCuf"},
    {"kpi": "da_rec_conc",       "label": "Efecto Recuperación (concentradora)","unit": "ktCuf"},
    {"kpi": "da_rec_hidro",      "label": "Efecto Recuperación (hidro)",       "unit": "ktCuf"},
    {"kpi": "varinv",            "label": "Efecto Inventarios",                "unit": "ktCuf"},
    {"kpi": "rend_energia_conc", "label": "Efecto Rendimiento Energía (conc)",  "unit": "kUS$"},
    {"kpi": "rend_energia_hidro","label": "Efecto Rendimiento Energía (hidro)", "unit": "kUS$"},
    {"kpi": "rend_combustible",  "label": "Efecto Rendimiento Combustible",    "unit": "kUS$"},
    {"kpi": "rend_acido",        "label": "Efecto Rendimiento Ácido",          "unit": "kUS$"},
    {"kpi": "rend_bolas",        "label": "Efecto Rendimiento Bolas",          "unit": "kUS$"},
    {"kpi": "rend_explosivos",   "label": "Efecto Rendimiento Explosivos",     "unit": "kUS$"},
    {"kpi": "act_mina_kus",      "label": "Efecto Actividad Mina",             "unit": "kUS$"},
    {"kpi": "act_conc_kus",      "label": "Efecto Actividad Concentradora",    "unit": "kUS$"},
]
DESCOMP_KEYS = {c["kpi"] for c in DESCOMP_CATALOG}


def _load_cell_cfg() -> dict:
    try:
        return json.loads(FLASH_CELL_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cell_cfg(cfg: dict) -> None:
    FLASH_CELL_CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _cell_override(cfg: dict, col: str, rowkey: str) -> dict:
    """Config de una celda (slots real/ppto/efecto) o {} si no hay."""
    return (cfg.get(col, {}) or {}).get(rowkey, {}) or {}


def _r(v, scale=1.0):
    if v is None:
        return None
    return round(v * scale, 6)


def _inv(cu, trat, ley_frac, rec_frac):
    """Inventarios y otros (fórmula Excel): CuFino_kt − Tratamiento_kt × Ley × Rec.
    Ley y Rec deben entrar como FRACCIÓN (ej. 0.005, 0.90)."""
    if cu is None or trat is None or ley_frac is None or rec_frac is None:
        return None
    return round(cu - trat * ley_frac * rec_frac, 6)


def _compute_company(snap_r, snap_b, mappings, month_field, lk_overrides=None,
                     viz_scales=None):
    """Retorna {key: {r_mes, b_mes, r_ytd, b_ytd}} para una compañía.

    lk_overrides: dict opcional {key: [lks]} para redefinir las fuentes de
    ciertas filas (usado para separar Centinela en CuCons y Cátodos). Una lista
    vacía deja la fila sin valor.
    viz_scales: escalas de presentación por clave KPI definidas en Config
    (visualizar_scales_per_company), p.ej. {'Variables Mineras||Ley oxidos': 100}.
    Se aplican al valor crudo del lk usado por cada fila (incl. Ley/Rec → %).
    """
    lk_overrides = lk_overrides or {}
    viz_scales = viz_scales or {}

    def _scale_for(lks):
        """Factor de escala (de Config) del primer lk con escala definida."""
        for lk in (lks or []):
            if lk in viz_scales:
                return float(viz_scales[lk])
        return 1.0

    result = {}
    # Guardar el factor aplicado a ley/rec para revertirlo en inventarios
    scale_used = {}
    for row in FLASH_ROWS:
        if row["key"] == "inventarios":
            continue  # se calcula como valor derivado al final
        if row["key"] in lk_overrides:
            ov = lk_overrides[row["key"]]
            if not ov:  # vía sin esta métrica → vacío
                result[row["key"]] = {"r_mes": None, "b_mes": None, "r_ytd": None, "b_ytd": None}
                scale_used[row["key"]] = 1.0
                continue
            # fila efectiva con lks de la vía y sin total_lk (no aplica a una sola vía)
            # >1 lk en el override → sumar (ej. CMZ ROM = CuFino + ROM Dinámico)
            row = {**row, "lks": ov, "total_lk": None,
                   "sum_lks": True if len(ov) > 1 else row["sum_lks"]}
        sc_kpi = _scale_for(row["lks"])
        scale_used[row["key"]] = sc_kpi
        r_mes_raw, r_ytd_raw = _get_vals(snap_r, mappings, row, month_field)
        b_mes_raw, b_ytd_raw = _get_vals(snap_b, mappings, row, month_field)
        def _sc(v): return (v * sc_kpi if v is not None else None)
        result[row["key"]] = {
            "r_mes": _sc(r_mes_raw), "b_mes": _sc(b_mes_raw),
            "r_ytd": _sc(r_ytd_raw), "b_ytd": _sc(b_ytd_raw),
        }

    cf = result.get("cu_fino",      {})
    tr = result.get("tratamiento",  {})
    ly = result.get("ley",          {})
    rc = result.get("recuperacion", {})
    # Inventarios necesita Ley/Rec en FRACCIÓN. Tras aplicar la escala de Config,
    # Ley/Rec quedan en % → dividir por 100 para volver a fracción.
    def _frac(v): return (v / 100.0) if v is not None else None
    result["inventarios"] = {
        "r_mes": _inv(cf.get("r_mes"), tr.get("r_mes"), _frac(ly.get("r_mes")), _frac(rc.get("r_mes"))),
        "b_mes": _inv(cf.get("b_mes"), tr.get("b_mes"), _frac(ly.get("b_mes")), _frac(rc.get("b_mes"))),
        "r_ytd": _inv(cf.get("r_ytd"), tr.get("r_ytd"), _frac(ly.get("r_ytd")), _frac(rc.get("r_ytd"))),
        "b_ytd": _inv(cf.get("b_ytd"), tr.get("b_ytd"), _frac(ly.get("b_ytd")), _frac(rc.get("b_ytd"))),
    }
    return result


def _grupo_minero(companies_data: dict) -> dict:
    """Agrega datos de todas las columnas (incl. ambas vías de Centinela) en Grupo Minero."""
    src_cols = [c for c in DISPLAY_COLS if c != "GM"]
    gm = {}
    for row in FLASH_ROWS:
        key = row["key"]
        vals = [companies_data[c].get(key, {}) for c in src_cols if c in companies_data]
        if row["agg"] == "sum":
            def _sadd(field):
                vs = [v.get(field) for v in vals if v.get(field) is not None]
                return sum(vs) if vs else None
            gm[key] = {f: _sadd(f) for f in ("r_mes", "b_mes", "r_ytd", "b_ytd")}
        else:
            # Promedios ponderados del Grupo Minero (NO se suman):
            #   Ley GM = Σ(Ley_i·Trat_i) / Σ(Trat_i)            [peso = tratamiento]
            #   Rec GM = Σ(Rec_i·Ley_i·Trat_i) / Σ(Ley_i·Trat_i) [peso = ley·tratamiento]
            cols = [c for c in src_cols if c in companies_data]
            trat = {c: companies_data[c].get("tratamiento", {}) for c in cols}
            ley  = {c: companies_data[c].get("ley", {}) for c in cols}
            self_vals = {c: companies_data[c].get(key, {}) for c in cols}

            def _weighted(field):
                num = den = 0.0
                used = False
                for c in cols:
                    val = self_vals[c].get(field)
                    t   = trat[c].get(field)
                    if val is None or t is None:
                        continue
                    if key == "recuperacion":
                        l = ley[c].get(field)
                        if l is None:
                            continue
                        w = l * t           # peso = ley × tratamiento
                    else:                    # ley u otra tasa → peso = tratamiento
                        w = t
                    num += val * w
                    den += w
                    used = True
                return (num / den) if (used and den) else None

            gm[key] = {f: _weighted(f) for f in ("r_mes", "b_mes", "r_ytd", "b_ytd")}
    return gm


# Cada columna de display → (compañía física en parquet, overrides de lks por vía)
DISPLAY_COL_SOURCE = {
    "MLP":     ("MLP", MLP_LKS),
    "CEN_CC":  ("CEN", CEN_CC_LKS),
    "CEN_CAT": ("CEN", CEN_CAT_LKS),
    "ANT":     ("ANT", OXIDO_ONLY_LKS),
    "CMZ":     ("CMZ", OXIDO_ONLY_LKS),
}

# Vía de cada columna para el cálculo de efectos (concentradora vs hidro/óxidos).
# MLP y CuCons usan la vía concentradora; Cátodos/ANT/CMZ la vía hidro.
DISPLAY_COL_VIA = {
    "MLP": "conc", "CEN_CC": "conc",
    "CEN_CAT": "hidro", "ANT": "hidro", "CMZ": "hidro",
}

# Mapa fila Flash → claves del waterfall de Explicaciones por vía (efecto en ktCuf).
# Cada fila toma el efecto de la variable correspondiente según la vía de la columna.
EFECTO_KEYS = {
    "tratamiento":  {"conc": "da_trat_conc", "hidro": "da_trat_hidro"},
    "ley":          {"conc": "ley_conc",     "hidro": "ley_hidro"},
    "recuperacion": {"conc": "da_rec_conc",  "hidro": "da_rec_hidro"},
    "inventarios":  {"conc": "varinv",       "hidro": "varinv"},
}


def _flash_efectos(año, mes):
    """Para cada compañía física, obtiene los efectos de descomposición (ktCuf)
    desde Explicaciones, en mes y ytd. Devuelve {company: {panel: waterfall}}."""
    out = {}
    for co in COMPANIES:
        try:
            exp = get_explicaciones(co, año, mes)
            data = exp.get("data", {})
            out[co] = {
                "mes": (data.get("mes") or {}).get("waterfall", {}),
                "ytd": (data.get("ytd") or {}).get("waterfall", {}),
            }
        except Exception:
            out[co] = {"mes": {}, "ytd": {}}
    return out


def _efecto_for(col, key, panel, efectos):
    """Efecto (ktCuf) de una celda según su vía, o None si no aplica."""
    src = DISPLAY_COL_SOURCE.get(col)
    if not src:
        return None  # GM se agrega por suma de efectos
    company = src[0]
    via = DISPLAY_COL_VIA.get(col, "conc")
    keymap = EFECTO_KEYS.get(key)
    if not keymap:
        return None
    wf = efectos.get(company, {}).get(panel, {})
    return wf.get(keymap[via])


def _effective_lks(row, overrides):
    """lks que realmente usa una fila para una vía (aplica overrides)."""
    overrides = overrides or {}
    if row["key"] in overrides:
        return overrides[row["key"]]
    return row.get("lks", [])


def _describe_mapping(mapped):
    """Describe un mapeo de Config: tipo, fuente(s), escalar o fórmula."""
    if not mapped or mapped == "__NA__":
        return {"tipo": "sin_mapear", "src": None}
    if isinstance(mapped, dict):
        if mapped.get("_s"):
            return {"tipo": "escalar", "src": mapped.get("src"),
                    "op": mapped.get("op", "*"), "scalar": mapped.get("scalar")}
        if mapped.get("_f"):
            return {"tipo": "formula", "a": mapped.get("a"), "op": mapped.get("op"),
                    "b": mapped.get("b")}
        if mapped.get("_rb"):
            return {"tipo": "real_ppto", "real": mapped.get("real"), "budget": mapped.get("budget")}
        return {"tipo": "otro", "raw": mapped}
    if isinstance(mapped, list):
        return {"tipo": "suma", "src": [s for s in mapped if s and s != "__NA__"]}
    return {"tipo": "directo", "src": mapped}


def _build_cell_trace(col, key, all_maps):
    """Trazabilidad de una celda (columna+fila): lks usados y su mapeo en Config."""
    src = DISPLAY_COL_SOURCE.get(col)
    if not src:  # GM: agregado, sin mapeo directo
        return {"agregado": True, "lks": []}
    company, overrides = src
    # Inventarios es un valor DERIVADO por fórmula, no un mapeo directo
    if key == "inventarios":
        return {
            "company": company,
            "derivado": True,
            "formula": "CuFino − Tratamiento × Ley × Rec",
            "detalle": "Inventarios y otros = Total cobre fino − (Tratamiento_kt × Ley × Recuperación). "
                       "No tiene un KPI mapeado: se calcula a partir de las filas Tratamiento, Ley, "
                       "Recuperación y Total cobre fino de esta misma columna.",
            "lks": [],
        }
    maps = all_maps.get(company, {})
    row = next((r for r in FLASH_ROWS if r["key"] == key), None)
    if not row:
        return {"company": company, "lks": []}
    lks = _effective_lks(row, overrides)
    total_lk = row.get("total_lk")
    entries = []
    if total_lk and maps.get(total_lk):
        entries.append({"lk": total_lk, "es_total": True, "mapping": _describe_mapping(maps.get(total_lk))})
    for lk in (lks or []):
        entries.append({"lk": lk, "es_total": False, "mapping": _describe_mapping(maps.get(lk))})
    return {"company": company, "lks": entries}


# ── Endpoint principal ────────────────────────────────────────────────────────

@router.get("")
def get_flash(año: int, mes: int, tipo_r: str = "Real", tipo_b: str = "Ppto"):
    df_all  = pq.get_cached_df(settings.parquet_path)
    all_maps = load_mappings()
    month_field = MONTH_BY_NUM[mes]

    co_scales = _load_company_scales()

    def _apply_scale(raw, sc):
        if sc == 1.0:
            return raw
        scalable = {r["key"] for r in FLASH_ROWS if r.get("co_scale", True)}
        return {
            key: ({f: (v * sc if v is not None else None) for f, v in vals.items()}
                  if key in scalable else vals)
            for key, vals in raw.items()
        }

    def _scale_keys(raw, factor, keys):
        """Multiplica las filas indicadas por un factor (ej. cátodos en t → kt)."""
        out = dict(raw)
        for key in keys:
            vals = raw.get(key)
            if vals:
                out[key] = {f: (v * factor if v is not None else None) for f, v in vals.items()}
        return out

    snap_store: dict[str, tuple] = {}   # co_física → (snap_r, snap_b, maps, viz)
    companies_data: dict[str, dict] = {}
    for co in COMPANIES:
        df_co = df_all[df_all["compania"] == co]
        snap_r, _ = _fetch_snapshot(df_co, tipo_r, año, mes)
        snap_b, _ = _fetch_snapshot(df_co, tipo_b, año, mes)
        maps = all_maps.get(co, {})
        sc = co_scales.get(co, 1.0)
        # Escalas de presentación por KPI (Ley/Rec fracción→%) definidas en Config
        viz = _load_visualizar_scales(co)
        snap_store[co] = (snap_r, snap_b, maps, viz)
        if co == "CEN":
            # Centinela se divide en dos vías que comparten snapshot y mappings.
            companies_data["CEN_CC"]  = _apply_scale(
                _compute_company(snap_r, snap_b, maps, month_field, CEN_CC_LKS, viz), sc)
            _cat = _compute_company(snap_r, snap_b, maps, month_field, CEN_CAT_LKS, viz)
            # CuFino de cátodos viene en toneladas → kt (÷1000 extra).
            # Inventario va a CuCons, no a cátodos.
            _cat = _scale_keys(_cat, 0.001, ("cu_fino",))
            _cat["inventarios"] = {f: None for f in ("r_mes", "b_mes", "r_ytd", "b_ytd")}
            companies_data["CEN_CAT"] = _apply_scale(_cat, sc)
        elif co in ("ANT", "CMZ"):
            # Solo óxidos: Ley/Rec vienen del KPI "Ley/Recuperación óxidos".
            # CMZ además: ROM/Ripios = CuFino + ROM Dinámico/Ripos.
            ov_lks = CMZ_LKS if co == "CMZ" else OXIDO_ONLY_LKS
            raw = _compute_company(snap_r, snap_b, maps, month_field, ov_lks, viz)
            companies_data[co] = _apply_scale(raw, sc)
        else:
            overrides = MLP_LKS if co == "MLP" else None
            companies_data[co] = _apply_scale(
                _compute_company(snap_r, snap_b, maps, month_field, overrides, viz), sc)

    gm = _grupo_minero(companies_data)
    companies_data["GM"] = gm

    # Efectos de descomposición (ktCuf) desde Explicaciones, por compañía/vía
    efectos = _flash_efectos(año, mes)
    # Filas cuyo Efecto es la descomposición valorizada (no Real−Ppto)
    EFECTO_ROWS = set(EFECTO_KEYS.keys()) | {"cu_fino"}
    # Subproductos y otros: Efecto = Real − Ppto (número)
    def _ef_simple(v, panel, scale):
        r = _r(v.get(f"r_{panel}"), scale)
        b = _r(v.get(f"b_{panel}"), scale)
        return (r - b) if (r is not None and b is not None) else None

    # Config por celda (overrides de fuente KPI elegidos en el popover del Flash)
    cell_cfg = _load_cell_cfg()

    def _resolve_visualizar(phys_co, lk, which):
        """Valor mes/ytd de un KPI de Visualizar (lk) para real/ppto/efecto."""
        st = snap_store.get(phys_co)
        if not st:
            return (None, None)
        snr, snb, mps, vz = st
        srow = {"lks": [lk], "sum_lks": False, "total_lk": None}
        sc_kpi = float(vz.get(lk, 1.0))
        rm, ry = _get_vals(snr, mps, srow, month_field)
        bm, by = _get_vals(snb, mps, srow, month_field)
        rm = rm * sc_kpi if rm is not None else None
        ry = ry * sc_kpi if ry is not None else None
        bm = bm * sc_kpi if bm is not None else None
        by = by * sc_kpi if by is not None else None
        if which == "real":   return (rm, ry)
        if which == "ppto":   return (bm, by)
        # efecto = real − ppto
        return ((rm - bm) if (rm is not None and bm is not None) else None,
                (ry - by) if (ry is not None and by is not None) else None)

    def _resolve_slot(slot, which, phys_co):
        """Devuelve (mes, ytd) según la config de un slot {source, kpi}."""
        if not slot or not slot.get("kpi"):
            return None
        source = slot.get("source")
        kpi = slot["kpi"]
        if source == "descomposicion":
            wf_m = efectos.get(phys_co, {}).get("mes", {})
            wf_y = efectos.get(phys_co, {}).get("ytd", {})
            return (wf_m.get(kpi), wf_y.get(kpi))
        if source == "visualizar":
            return _resolve_visualizar(phys_co, kpi, which)
        return None

    def _apply_cell_overrides(cell, col, rowkey):
        ov = _cell_override(cell_cfg, col, rowkey)
        if not ov:
            return
        src = DISPLAY_COL_SOURCE.get(col)
        phys_co = src[0] if src else None
        if not phys_co:
            return
        for which, (mk, yk) in (("real", ("r_mes", "r_ytd")),
                                ("ppto", ("b_mes", "b_ytd")),
                                ("efecto", ("ef_mes", "ef_ytd"))):
            slot = ov.get(which)
            res = _resolve_slot(slot, which, phys_co)
            if res is not None:
                cell[mk] = _r(res[0])
                cell[yk] = _r(res[1])

    # Aplicar escala y construir respuesta
    rows_out = []
    for row in FLASH_ROWS:
        key   = row["key"]
        scale = row["scale"]
        comp_out = {}
        for co in DISPLAY_COLS:
            cdata = companies_data.get(co, {})
            v = cdata.get(key, {})
            cell = {
                "r_mes":  _r(v.get("r_mes"),  scale),
                "b_mes":  _r(v.get("b_mes"),  scale),
                "r_ytd":  _r(v.get("r_ytd"),  scale),
                "b_ytd":  _r(v.get("b_ytd"),  scale),
                "trace":  _build_cell_trace(co, key, all_maps),
            }
            # Efecto valorizado (ktCuf) para filas de producción; Real−Ppto para el resto
            if key in EFECTO_KEYS and co in DISPLAY_COL_SOURCE:
                cell["ef_mes"] = _r(_efecto_for(co, key, "mes", efectos))
                cell["ef_ytd"] = _r(_efecto_for(co, key, "ytd", efectos))
            elif key == "cu_fino" and co in DISPLAY_COL_SOURCE:
                # CuFino: suma de los efectos de tratamiento+ley+rec+inventarios de la vía
                def _sum_ef(panel):
                    parts = [_efecto_for(co, k, panel, efectos) for k in EFECTO_KEYS]
                    vals = [p for p in parts if p is not None]
                    return sum(vals) if vals else None
                cell["ef_mes"] = _r(_sum_ef("mes"))
                cell["ef_ytd"] = _r(_sum_ef("ytd"))
            else:
                cell["ef_mes"] = _ef_simple(v, "mes", scale)
                cell["ef_ytd"] = _ef_simple(v, "ytd", scale)
            # Overrides por celda (KPI elegido en el popover) y config para la UI
            _apply_cell_overrides(cell, co, key)
            cell["cell_cfg"] = _cell_override(cell_cfg, co, key)
            comp_out[co] = cell

        # Grupo Minero: efecto = suma de los efectos de las columnas fuente
        gm_cell = comp_out.get("GM")
        if gm_cell is not None:
            src_cols = [c for c in DISPLAY_COLS if c in DISPLAY_COL_SOURCE]
            for pf in ("ef_mes", "ef_ytd"):
                vals = [comp_out[c].get(pf) for c in src_cols if comp_out[c].get(pf) is not None]
                gm_cell[pf] = _r(sum(vals)) if vals else None

        rows_out.append({
            "key":   key,
            "label": row["label"],
            "unit":  row["unit"],
            "bold":  row["bold"],
            "sep":   row["sep"],
            "companies": comp_out,
        })

    return {"año": año, "mes": mes, "rows": rows_out}


# ── Configuración por celda: fuentes seleccionables y guardado ─────────────────

@router.get("/sources")
def get_flash_sources(col: str):
    """KPIs seleccionables para una columna: Visualizar (KPIs mapeados de su
    compañía física) + Descomposición de Varianzas (catálogo de efectos)."""
    src = DISPLAY_COL_SOURCE.get(col)
    company = src[0] if src else None
    visualizar = []
    if company:
        maps = load_mappings().get(company, {})
        for lk, mapped in maps.items():
            if not mapped or mapped == "__NA__":
                continue
            visualizar.append({"kpi": lk, "label": lk.replace("||", " · ")})
        visualizar.sort(key=lambda x: x["label"])
    return {
        "col": col, "company": company,
        "descomposicion": DESCOMP_CATALOG,
        "visualizar": visualizar,
    }


@router.get("/cell-config")
def get_flash_cell_config():
    return _load_cell_cfg()


@router.put("/cell-config")
def put_flash_cell_config(body: dict):
    """Guarda la config por celda. Estructura: {col: {rowkey: {real,ppto,efecto}}}.
    Un slot vacío/None elimina el override de esa parte."""
    cfg = _load_cell_cfg()
    col = body.get("col"); rowkey = body.get("rowkey"); slots = body.get("slots", {})
    if not col or not rowkey:
        return {"ok": False, "error": "col y rowkey requeridos"}
    cfg.setdefault(col, {})
    # Limpiar slots vacíos
    clean = {k: v for k, v in slots.items() if v and v.get("kpi")}
    if clean:
        cfg[col][rowkey] = clean
    else:
        cfg[col].pop(rowkey, None)
        if not cfg[col]:
            cfg.pop(col, None)
    _save_cell_cfg(cfg)
    return {"ok": True}


# ── Excel download ────────────────────────────────────────────────────────────

@router.get("/excel")
def get_flash_excel(año: int, mes: int, tipo_r: str = "Real", tipo_b: str = "Ppto"):
    data = get_flash(año=año, mes=mes, tipo_r=tipo_r, tipo_b=tipo_b)
    mes_name = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"][mes - 1]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Flash Producciones"

    COLS = DISPLAY_COLS
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
            ef_panel = "ef_" + rk.split("_", 1)[1]   # "r_mes" → "ef_mes"
            for co in COLS:
                cv = flash_row["companies"].get(co, {})
                rv = cv.get(rk); bv = cv.get(bk)
                ef = cv.get(ef_panel)  # efecto pre-calculado (ktCuf o Real−Ppto)
                fill = GREEN if flash_row["bold"] else None
                # Real/Ppto con su unidad; Efecto SIEMPRE como número (1 decimal)
                cells = [(rv, unit, False), (bv, unit, False), (ef, "num", True)]
                for i, (val, u, is_ef) in enumerate(cells):
                    txt = "—" if val is None else (
                        f"({abs(val):.1f})" if (is_ef and val < 0) else
                        (f"{val:.1f}" if is_ef else _fmt(val, u, False)))
                    c = ws.cell(row=row, column=col + i, value=txt)
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
