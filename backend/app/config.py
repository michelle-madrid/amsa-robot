from pathlib import Path
from pydantic_settings import BaseSettings


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    parquet_path: Path = BASE_DIR / "data" / "Libro_Gestion.parquet"
    template_path: Path = BASE_DIR / "templates" / "Robot 2026.xlsx"
    output_dir: Path = BASE_DIR / "output"

    class Config:
        env_file = ".env"


settings = Settings()
