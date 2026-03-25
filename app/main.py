import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.cxcalc import router as cxcalc_router
from app.config import settings

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: log preflight check results from docker-entrypoint.sh
    from app.services.vbox_service import get_preflight_result

    preflight = get_preflight_result()
    if preflight:
        passed = preflight.get("checks_passed", "?")
        failed = preflight.get("checks_failed", "?")
        warned = preflight.get("checks_warned", "?")
        logger.info(
            "Preflight check results: passed=%s, failed=%s, warned=%s (VM: %s, state: %s)",
            passed, failed, warned,
            preflight.get("vm_name", "?"),
            preflight.get("vm_state", "?"),
        )
        if int(failed) > 0:
            logger.warning(
                "Preflight detected %s failed check(s). "
                "Use GET /api/v1/cxcalc/diagnostics for details.",
                failed,
            )
    else:
        logger.info("No preflight result found (running outside Docker or entrypoint skipped checks)")

    yield


app = FastAPI(
    title="Marvin cxcalc API",
    description="REST API for ChemAxon cxcalc running on a Windows VM via VirtualBox",
    version="1.0.0",
    lifespan=lifespan,
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
