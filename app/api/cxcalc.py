import csv
import io
import logging
import os
from enum import Enum
from typing import Optional

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.vbox_service import (
    VBoxError,
    check_vm_running,
    generate_task_id,
    get_preflight_result,
    get_shared_folder_path,
    run_cxcalc_on_vm,
    run_vm_diagnostics,
    start_vm,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/cxcalc", tags=["cxcalc"])


class CalcType(str, Enum):
    molecular_properties = "molecular_properties"
    logs = "logs"
    logd = "logd"
    all = "all"


# Mapping from calc type to cxcalc arguments
CALC_ARGS = {
    CalcType.molecular_properties: "molecularpolarizability dipole fsp3 psa logp pka hbonddonoracceptor",
    CalcType.logs: "logs",
    CalcType.logd: "logd",
}


def _merge_csv_contents(results: dict[str, str]) -> str:
    """Merge multiple CSV outputs by joining columns on the row index."""
    parsed: dict[str, list[dict[str, str]]] = {}
    for calc_type, content in results.items():
        reader = csv.DictReader(io.StringIO(content), delimiter="\t")
        parsed[calc_type] = list(reader)

    if not parsed:
        return ""

    # Use the first result set as the base
    first_key = next(iter(parsed))
    base_rows = parsed[first_key]
    num_rows = len(base_rows)

    # Collect all column names (preserving order, deduplicating)
    all_columns: list[str] = []
    seen: set[str] = set()
    for rows in parsed.values():
        if rows:
            for col in rows[0].keys():
                if col not in seen:
                    all_columns.append(col)
                    seen.add(col)

    # Merge rows by index
    merged_rows: list[dict[str, str]] = []
    for i in range(num_rows):
        merged_row: dict[str, str] = {}
        for calc_type, rows in parsed.items():
            if i < len(rows):
                for k, v in rows[i].items():
                    if k not in merged_row:
                        merged_row[k] = v
        merged_rows.append(merged_row)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_columns, delimiter="\t")
    writer.writeheader()
    writer.writerows(merged_rows)
    return output.getvalue()


@router.get("/health")
async def health():
    """Check VM status and service health, including preflight results."""
    try:
        running = await check_vm_running()
        result = {"status": "ok", "vm_running": running, "vm_name": settings.vm_name}
        preflight = get_preflight_result()
        if preflight:
            result["preflight"] = preflight
        return result
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": str(e)},
        )


@router.get("/diagnostics")
async def diagnostics():
    """Run comprehensive VM environment diagnostics (may take 30-60 seconds)."""
    try:
        result = await run_vm_diagnostics()
        all_ok = (
            result["vboxmanage_available"]
            and result["vm_exists"]
            and result["vm_running"]
            and result["guest_additions_ok"]
            and result["cxcalc_available"]
            and result["shared_folder_host_ok"]
        )
        return {
            "status": "ok" if all_ok else "degraded",
            "all_checks_passed": all_ok,
            **result,
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": str(e)},
        )


@router.post("/calculate")
async def calculate(
    file: UploadFile = File(..., description="SDF file to process"),
    calc_types: str = Query(
        default="all",
        description="Comma-separated calculation types: molecular_properties, logs, logd, all",
    ),
    merge: bool = Query(default=True, description="Merge all CSV results into one"),
    auto_start_vm: bool = Query(default=True, description="Auto-start VM if not running"),
):
    """
    Upload an SDF file and run cxcalc calculations on the Windows VM.

    Returns CSV results as JSON. When merge=true (default), all calculation
    results are merged into a single table.
    """
    # Parse calc types
    requested = [s.strip() for s in calc_types.split(",")]
    types_to_run: list[CalcType] = []
    for r in requested:
        if r == "all":
            types_to_run = [CalcType.molecular_properties, CalcType.logs, CalcType.logd]
            break
        try:
            types_to_run.append(CalcType(r))
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": f"Unknown calc_type: {r}. Valid: molecular_properties, logs, logd, all"},
            )

    # Ensure shared folder exists
    os.makedirs(settings.shared_folder_host, exist_ok=True)

    # Auto-start VM if needed
    if auto_start_vm:
        try:
            await start_vm()
        except VBoxError as e:
            return JSONResponse(status_code=503, content={"detail": f"VM startup failed: {e}"})

    task_id = generate_task_id()
    sdf_filename = f"{task_id}.sdf"
    sdf_host_path = get_shared_folder_path(sdf_filename)

    # Save uploaded SDF to shared folder
    try:
        content = await file.read()
        with open(sdf_host_path, "wb") as f:
            f.write(content)
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Failed to save file: {e}"})

    # Run calculations
    results: dict[str, str] = {}
    errors: dict[str, str] = {}
    try:
        for calc_type in types_to_run:
            output_filename = f"{task_id}_{calc_type.value}.csv"
            try:
                csv_content = await run_cxcalc_on_vm(
                    sdf_filename=sdf_filename,
                    output_filename=output_filename,
                    calc_args=CALC_ARGS[calc_type],
                )
                results[calc_type.value] = csv_content
            except VBoxError as e:
                logger.error("Calculation %s failed: %s", calc_type.value, e)
                errors[calc_type.value] = str(e)
            finally:
                # Clean up output file
                out_path = get_shared_folder_path(output_filename)
                if os.path.exists(out_path):
                    os.remove(out_path)
    finally:
        # Clean up input SDF
        if os.path.exists(sdf_host_path):
            os.remove(sdf_host_path)

    if not results:
        return JSONResponse(
            status_code=500,
            content={"detail": "All calculations failed", "errors": errors},
        )

    # Build response
    if merge and len(results) > 1:
        try:
            merged = _merge_csv_contents(results)
            return {
                "task_id": task_id,
                "merged": True,
                "data": merged,
                "errors": errors if errors else None,
            }
        except Exception as e:
            logger.warning("Merge failed, returning separate results: %s", e)

    return {
        "task_id": task_id,
        "merged": False,
        "results": results,
        "errors": errors if errors else None,
    }
