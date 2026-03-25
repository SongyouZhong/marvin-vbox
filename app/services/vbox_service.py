import asyncio
import base64
import json
import logging
import os
import shlex
import uuid

from app.config import settings

logger = logging.getLogger(__name__)

# Serialize VM access — single VM can only run one command at a time
_vm_lock = asyncio.Lock()

# Preflight check results (loaded from entrypoint or populated at startup)
_preflight_result: dict | None = None


class VBoxError(Exception):
    """Raised when a vboxmanage command fails."""


async def _run_process(cmd: list[str], timeout: int | None = None) -> tuple[str, str, int]:
    """Run a subprocess asynchronously, return (stdout, stderr, returncode)."""
    logger.info("Running: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout or settings.command_timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise VBoxError(f"Command timed out after {timeout or settings.command_timeout}s")

    return stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"), proc.returncode


async def check_vm_running() -> bool:
    """Check if the VM is currently running."""
    stdout, _, rc = await _run_process(["vboxmanage", "showvminfo", settings.vm_name, "--machinereadable"])
    if rc != 0:
        return False
    for line in stdout.splitlines():
        if line.startswith("VMState="):
            state = line.split("=", 1)[1].strip('"')
            return state == "running"
    return False


async def start_vm() -> None:
    """Start the VM in headless mode if not already running."""
    if await check_vm_running():
        logger.info("VM %s is already running", settings.vm_name)
        return
    logger.info("Starting VM %s in headless mode...", settings.vm_name)
    _, stderr, rc = await _run_process(
        ["vboxmanage", "startvm", settings.vm_name, "--type", "headless"],
        timeout=120,
    )
    if rc != 0:
        raise VBoxError(f"Failed to start VM: {stderr}")
    # Wait for guest additions to be ready
    for _ in range(30):
        await asyncio.sleep(5)
        if await check_vm_running():
            # Try a simple guestcontrol command to verify guest additions are ready
            _, _, test_rc = await _run_process([
                "vboxmanage", "guestcontrol", settings.vm_name, "run",
                "--exe", r"C:\Windows\System32\cmd.exe",
                "--username", settings.vm_username,
                "--password", settings.vm_password,
                "--wait-stdout",
                "--", "cmd.exe", "/c", "echo", "ready",
            ], timeout=30)
            if test_rc == 0:
                logger.info("VM %s is ready for guest control", settings.vm_name)
                return
    raise VBoxError("VM started but guest control is not responsive")


def _encode_powershell_command(raw_cmd: str) -> str:
    """Encode a command string for PowerShell -EncodedCommand (UTF-16LE + Base64)."""
    return base64.b64encode(raw_cmd.encode("utf-16-le")).decode("ascii")


async def run_cxcalc_on_vm(
    sdf_filename: str,
    output_filename: str,
    calc_args: str,
) -> str:
    """
    Execute a cxcalc command on the VM via guestcontrol.

    The SDF file must already exist in the shared folder on the host side.
    The output CSV will be written to the shared folder.

    Args:
        sdf_filename: Name of the SDF file (already in shared folder)
        output_filename: Name for the output CSV file
        calc_args: cxcalc calculation arguments (e.g. "logp pka hbonddonoracceptor")

    Returns:
        Content of the output CSV file
    """
    # Strip any trailing backslash from the VM drive path before appending filename
    vm_base = settings.shared_folder_vm.rstrip("\\")
    vm_sdf_path = f"{vm_base}\\{sdf_filename}"
    vm_out_path = f"{vm_base}\\{output_filename}"
    host_out_path = os.path.join(settings.shared_folder_host, output_filename)

    # Build the cxcalc command
    raw_cmd = (
        f'& "{settings.cxcalc_path}" -i "Name" "{vm_sdf_path}" {calc_args} '
        f'| Out-File -FilePath "{vm_out_path}" -Encoding UTF8'
    )
    encoded_cmd = _encode_powershell_command(raw_cmd)

    async with _vm_lock:
        stdout, stderr, rc = await _run_process([
            "vboxmanage", "guestcontrol", settings.vm_name, "run",
            "--exe", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "--username", settings.vm_username,
            "--password", settings.vm_password,
            "--wait-stdout", "--wait-stderr",
            "--", "powershell.exe",
            "-NonInteractive", "-EncodedCommand", encoded_cmd,
        ])

    if rc != 0:
        logger.error("cxcalc failed (rc=%d): stdout=%s, stderr=%s", rc, stdout, stderr)
        raise VBoxError(f"cxcalc command failed (exit code {rc}): {stderr or stdout}")

    # Read result from shared folder on the host side
    # Wait briefly for file sync
    for attempt in range(10):
        if os.path.exists(host_out_path):
            break
        await asyncio.sleep(1)
    else:
        raise VBoxError(f"Output file {output_filename} was not created on the shared folder")

    with open(host_out_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    return content


def generate_task_id() -> str:
    return uuid.uuid4().hex[:12]


def get_shared_folder_path(filename: str) -> str:
    return os.path.join(settings.shared_folder_host, filename)


def get_preflight_result() -> dict | None:
    """Return the preflight check result from docker-entrypoint.sh."""
    global _preflight_result
    if _preflight_result is None:
        path = "/tmp/vm_preflight_result.json"
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    _preflight_result = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to read preflight result: %s", e)
    return _preflight_result


async def run_vm_diagnostics() -> dict:
    """
    Run comprehensive VM environment diagnostics at runtime.
    Returns a dict with check results for each component.
    """
    diag: dict = {
        "vboxmanage_available": False,
        "vm_exists": False,
        "vm_running": False,
        "guest_additions_ok": False,
        "cxcalc_available": False,
        "shared_folder_host_ok": False,
        "details": {},
        "errors": [],
    }

    # 1. Check vboxmanage
    try:
        stdout, _, rc = await _run_process(["vboxmanage", "--version"], timeout=10)
        if rc == 0:
            diag["vboxmanage_available"] = True
            diag["details"]["vboxmanage_version"] = stdout.strip()
    except Exception as e:
        diag["errors"].append(f"vboxmanage check failed: {e}")
        return diag

    # 2. Check VM exists
    try:
        stdout, _, rc = await _run_process(
            ["vboxmanage", "showvminfo", settings.vm_name, "--machinereadable"], timeout=10
        )
        if rc == 0:
            diag["vm_exists"] = True
            for line in stdout.splitlines():
                if line.startswith("VMState="):
                    state = line.split("=", 1)[1].strip('"')
                    diag["details"]["vm_state"] = state
                    diag["vm_running"] = state == "running"
                elif line.startswith("ostype="):
                    diag["details"]["vm_os_type"] = line.split("=", 1)[1].strip('"')
                elif line.startswith("memory="):
                    diag["details"]["vm_memory_mb"] = line.split("=", 1)[1]
                elif line.startswith("cpus="):
                    diag["details"]["vm_cpus"] = line.split("=", 1)[1]
        else:
            diag["errors"].append(f"VM '{settings.vm_name}' not found")
            return diag
    except Exception as e:
        diag["errors"].append(f"VM info check failed: {e}")
        return diag

    # 3. Check shared folder on host
    diag["shared_folder_host_ok"] = os.path.isdir(settings.shared_folder_host) and os.access(
        settings.shared_folder_host, os.W_OK
    )

    if not diag["vm_running"]:
        diag["errors"].append("VM is not running; skipping Guest Additions / cxcalc checks")
        return diag

    # 4. Check Guest Additions
    try:
        stdout, _, rc = await _run_process([
            "vboxmanage", "guestcontrol", settings.vm_name, "run",
            "--exe", r"C:\Windows\System32\cmd.exe",
            "--username", settings.vm_username,
            "--password", settings.vm_password,
            "--wait-stdout",
            "--", "cmd.exe", "/c", "echo", "diag_ok",
        ], timeout=30)
        diag["guest_additions_ok"] = rc == 0 and "diag_ok" in stdout
    except Exception as e:
        diag["errors"].append(f"Guest Additions check failed: {e}")

    if not diag["guest_additions_ok"]:
        return diag

    # 5. Check cxcalc availability
    try:
        stdout, stderr, rc = await _run_process([
            "vboxmanage", "guestcontrol", settings.vm_name, "run",
            "--exe", r"C:\Windows\System32\cmd.exe",
            "--username", settings.vm_username,
            "--password", settings.vm_password,
            "--wait-stdout", "--wait-stderr",
            "--", "cmd.exe", "/c", settings.cxcalc_path, "-h",
        ], timeout=60)
        diag["cxcalc_available"] = rc == 0 and len(stdout.strip()) > 0
    except Exception as e:
        diag["errors"].append(f"cxcalc check failed: {e}")

    # Load preflight result from entrypoint if available
    preflight = get_preflight_result()
    if preflight:
        diag["details"]["preflight"] = preflight

    return diag
