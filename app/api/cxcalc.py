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


def _fix_double_column_tsv(content: str) -> str:
    """
    Fix cxcalc logS output where each pH value occupies two tab-columns
    (value + empty). Collapse them into single columns to match the headers.

    cxcalc 'logs' outputs 15 pH headers but 30 data values (value\tempty per pH).
    This function detects and fixes the mismatch.
    """
    lines = content.strip().split("\n")
    if len(lines) < 2:
        return content

    headers = lines[0].split("\t")
    # Strip trailing empty headers
    while headers and headers[-1].strip() == "":
        headers.pop()
    num_headers = len(headers)

    fixed_lines = ["\t".join(headers)]
    for line in lines[1:]:
        if not line.strip():
            continue
        values = line.split("\t")
        if len(values) > num_headers * 1.5:
            # Double-column detected: take every other value starting from index 1
            # Index 0 is Name, then pairs of (value, empty) for each pH column
            fixed_values = [values[0]]  # Name
            for j in range(1, len(values), 2):
                fixed_values.append(values[j])
            # Trim to match header count
            fixed_values = fixed_values[:num_headers]
            fixed_lines.append("\t".join(fixed_values))
        else:
            fixed_lines.append("\t".join(values[:num_headers]))

    return "\n".join(fixed_lines) + "\n"


def _parse_tsv_manually(content: str, section_label: str) -> tuple[list[str], list[dict[str, str]]]:
    """
    Parse a TSV content into (columns, rows) with a section label prefix
    for pH columns to distinguish logS vs logD in the merged output.

    pH columns like 'pH=0.0' are renamed to 'logs_pH=0.0' or 'logd_pH=0.00'.
    """
    lines = content.strip().split("\n")
    if not lines:
        return [], []

    headers = lines[0].split("\t")
    while headers and headers[-1].strip() == "":
        headers.pop()

    # Rename pH columns with section prefix
    renamed = []
    for h in headers:
        h_stripped = h.strip()
        if h_stripped.startswith("pH="):
            renamed.append(f"{section_label}_{h_stripped}")
        else:
            renamed.append(h_stripped)

    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        values = line.split("\t")
        row = {}
        for idx, col in enumerate(renamed):
            row[col] = values[idx] if idx < len(values) else ""
        rows.append(row)

    return renamed, rows


def _merge_csv_contents(results: dict[str, str]) -> str:
    """Merge multiple CSV outputs by joining columns on the row index.

    Handles:
    - cxcalc logS double-column bug (value+empty per pH)
    - Prefixes pH columns with 'logs_' or 'logd_' to avoid name collisions
      and enable downstream section-based parsing
    """
    if not results:
        return ""

    all_columns: list[str] = []
    seen: set[str] = set()
    parsed_rows: list[list[dict[str, str]]] = []

    for calc_type, content in results.items():
        section_label = calc_type  # e.g. "logs", "logd", "molecular_properties"

        # Fix logS double-column issue
        if calc_type == "logs":
            content = _fix_double_column_tsv(content)

        if calc_type in ("logs", "logd"):
            columns, rows = _parse_tsv_manually(content, section_label)
        else:
            reader = csv.DictReader(io.StringIO(content), delimiter="\t")
            rows = list(reader)
            columns = list(rows[0].keys()) if rows else []

        for col in columns:
            if col not in seen:
                all_columns.append(col)
                seen.add(col)
        parsed_rows.append(rows)

    if not parsed_rows:
        return ""

    num_rows = max(len(rows) for rows in parsed_rows) if parsed_rows else 0

    merged_rows: list[dict[str, str]] = []
    for i in range(num_rows):
        merged_row: dict[str, str] = {}
        for rows in parsed_rows:
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
