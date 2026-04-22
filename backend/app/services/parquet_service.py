import pandas as pd
from pathlib import Path


_df_cache: pd.DataFrame | None = None


def get_cached_df(parquet_path: Path) -> pd.DataFrame:
    global _df_cache
    if _df_cache is None:
        _df_cache = load_parquet(parquet_path)
    return _df_cache


MONTH_FIELDS = [
    "enero", "febrero", "marzo", "abril",
    "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
]


def load_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def get_available_periods(df: pd.DataFrame) -> dict:
    return {
        "periodos": sorted(df["periodo"].dropna().unique().tolist()),
        "anios": sorted(df["anio"].dropna().unique().tolist()),
        "meses": list(range(1, 13)),
        "tipos": sorted(df["tipo"].dropna().unique().tolist()),
        "companias": sorted(df["compania"].dropna().unique().tolist()),
    }


_TIPO_ALIASES: list[set[str]] = [
    {"ppto", "plan", "presupuesto", "budget"},
    {"real", "actual"},
]


def _tipo_variants(tipo: str) -> set[str]:
    t = tipo.strip().lower()
    for group in _TIPO_ALIASES:
        if t in group:
            return group
    return {t}


def filter_data(
    df: pd.DataFrame,
    anio: int,
    mes: int,
    companies: list[str],
    tipo: str,
) -> pd.DataFrame:
    periodo = anio * 100 + mes
    variants = _tipo_variants(tipo)
    mask = (
        (df["periodo"] == periodo)
        & (df["compania"].isin(companies))
        & (df["tipo"].str.strip().str.lower().isin(variants))
    )
    return df[mask].copy()


def get_company_kpis(df: pd.DataFrame, compania: str) -> pd.DataFrame:
    return df[df["compania"] == compania].copy()


def row_to_monthly_values(row: pd.Series) -> dict[str, float | None]:
    result = {}
    for field in MONTH_FIELDS:
        val = row.get(field)
        result[field] = float(val) if pd.notna(val) else None
    result["mes"] = float(row["mes"]) if pd.notna(row.get("mes")) else None
    result["ytd"] = float(row["ytd"]) if pd.notna(row.get("ytd")) else None
    return result
