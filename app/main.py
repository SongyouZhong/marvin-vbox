import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.cxcalc import router as cxcalc_router
from app.config import settings

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Marvin cxcalc API",
    description="REST API for ChemAxon cxcalc running on a Windows VM via VirtualBox",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cxcalc_router)


@app.get("/")
async def root():
    return {"service": "marvin-cxcalc-api", "version": "1.0.0"}
