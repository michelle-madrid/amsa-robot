from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .routes.automation import router
from .routes.setup import router as setup_router
from .routes.visualizar import router as viz_router
from .routes.params import router as params_router
from .routes.calculos import router as calculos_router
from .routes.explicaciones import router as explicaciones_router
from .routes.overrides import router as overrides_router
from .routes.costos_ajustados import router as ca_router
from .routes.export import router as export_router

FRONTEND_INDEX = Path(__file__).resolve().parents[2] / "frontend" / "index.html"

app = FastAPI(
    title="Robot 2026 - API de Automatización",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(setup_router)
app.include_router(viz_router)
app.include_router(params_router)
app.include_router(calculos_router)
app.include_router(explicaciones_router)
app.include_router(overrides_router)
app.include_router(ca_router)
app.include_router(export_router)


@app.get("/health")
def health():
    return {"status": "ok", "app": "Robot 2026"}


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str):
    return FileResponse(
        FRONTEND_INDEX,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
