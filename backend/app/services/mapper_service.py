"""
Discovers the KPI row layout in each Excel Input sheet and builds
a lookup dict: normalised_section_label -> row_number.

Keys use the format "{section}|{label}" to handle duplicate labels
across sections (e.g. "Bolas" appears in Tarifas, Consumos, Rendimientos, Gasto).

Matching strategy (tries in order):
  1. Saved user mapping: {section}|{label} -> parquet_kpi (exact, handles duplicates)
  2. Fallback fuzzy match on parquet kpi / subcategory / category
"""

import re
from openpyxl.workbook.workbook import Workbook


COMPANY_SHEET = {
    "MLP": "Inputs MLP",
    "CEN": "Inputs CEN",
    "ANT": "Inputs ANT",
    "CMZ": "Inputs CMZ",
}

MONTH_COL = {m: 4 + m for m in range(1, 13)}   # {1:5, 2:6, ..., 12:16}
MES_COL = 18
YTD_COL = 19
KPI_DATA_START_ROW = 4
LABEL_COL_IDX = 1
_KPI_TYPES = {"actual", "plan", "real", "presupuesto", "budget"}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _is_kpi_row(row) -> bool:
    code = str(row[0].value).strip() if row[0].value else ""
    type_val = str(row[3].value).strip().lower() if len(row) > 3 and row[3].value else ""
    return bool(code) or type_val in _KPI_TYPES


def discover_sheet_structure(wb: Workbook, company: str) -> dict[str, int]:
    """Returns {normalised_section|label: excel_row_number}.
    Section-qualified keys prevent collisions for labels repeated across sections.
    """
    sheet_name = COMPANY_SHEET.get(company)
    if not sheet_name or sheet_name not in wb.sheetnames:
        return {}

    ws = wb[sheet_name]
    mapping: dict[str, int] = {}
    current_section = ""

    for row in ws.iter_rows(min_row=KPI_DATA_START_ROW):
        label_cell = row[LABEL_COL_IDX]
        if not label_cell.value or not isinstance(label_cell.value, str):
            continue
        label = label_cell.value.strip()
        if not label:
            continue

        if _is_kpi_row(row):
            qualified = f"{current_section}||{label}" if current_section else label
            mapping[_normalise(qualified)] = label_cell.row
        else:
            current_section = label

    return mapping


def get_excel_kpis_for_setup(wb: Workbook, company: str) -> list[dict]:
    """Returns rows for the Input sheet: section headers and KPI rows.
    Each KPI row includes its section so the frontend can build unique keys.
    """
    sheet_name = COMPANY_SHEET.get(company)
    if not sheet_name or sheet_name not in wb.sheetnames:
        return []

    ws = wb[sheet_name]
    result = []
    current_section = ""

    for row in ws.iter_rows(min_row=KPI_DATA_START_ROW):
        code_cell  = row[0]
        label_cell = row[LABEL_COL_IDX]
        if not label_cell.value or not isinstance(label_cell.value, str):
            continue
        label = label_cell.value.strip()
        if not label:
            continue

        if _is_kpi_row(row):
            code = str(code_cell.value).strip() if code_cell.value else ""
            unit = row[2].value if len(row) > 2 else None
            kpi_id = f"{current_section}||{label}" if current_section else label
            result.append({
                "type":    "kpi",
                "id":      kpi_id,
                "section": current_section,
                "code":    code,
                "label":   label,
                "unit":    str(unit).strip() if unit else "",
                "row":     label_cell.row,
            })
        else:
            current_section = label
            result.append({"type": "header", "label": label})

    return result


def find_excel_row(
    kpi_row,
    sheet_mapping: dict[str, int],
    company_mappings: dict | None = None,
) -> int | None:
    """Match a parquet row to an Excel row.
    Uses saved mappings first (section-qualified keys), then fuzzy fallback.
    """
    parquet_kpi = str(kpi_row.get("kpi", ""))

    if company_mappings:
        norm_kpi = _normalise(parquet_kpi)
        for mapping_key, mapped_value in company_mappings.items():
            if mapped_value == "__NA__":
                continue
            sources = [mapped_value] if isinstance(mapped_value, str) else (mapped_value or [])
            if not any(_normalise(s) == norm_kpi for s in sources if s):
                continue
            # mapping_key is "{section}|{label}" — look up directly in sheet_mapping
            norm_key = _normalise(mapping_key)
            if norm_key in sheet_mapping:
                return sheet_mapping[norm_key]

    # Automatic fallback: try label suffix match (handles section||label keys)
    for candidate in [parquet_kpi, kpi_row.get("subcategory", ""), kpi_row.get("category", "")]:
        if not candidate:
            continue
        norm = _normalise(str(candidate))
        suffix = f"||{norm}"
        for key, row_num in sheet_mapping.items():
            if key == norm or key.endswith(suffix):
                return row_num

    return None
