#!/usr/bin/env python3
"""
Test script: run all three cxcalc commands on Win11VM via vboxmanage guestcontrol.
Results are written to /home/data/marvin_vbox_sharad/ (Y: in VM).

Input: real SDF generated from SMILES via RDKit (Compute2DCoords).
The SDF molecule name line is set to the SMILES string so cxcalc -i Name
can use it as the row identifier in the TSV output.
"""
import base64
import subprocess
import os
import time
from io import StringIO

from rdkit import Chem
from rdkit.Chem import AllChem

VM = "Win11VM"
USER = "marvin-box"
PASS = "123123"
CXCALC = r"C:\Progra~2\ChemAxon\MarvinBeans\bin\cxcalc.bat"
SDF = r"Y:\test_api.sdf"
OUT_DIR = r"Y:"
HOST_OUT = "/home/data/marvin_vbox_sharad"

# Sample SMILES for testing (aspirin, caffeine, ibuprofen)
TEST_SMILES = [
    "CC(=O)Oc1ccccc1C(=O)O",
    "Cn1c(=O)c2c(ncn2C)n(c1=O)C",
    "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
]


def build_sdf(smiles_list: list[str]) -> str:
    """Generate a real SDF from SMILES using RDKit."""
    buf = StringIO()
    writer = Chem.SDWriter(buf)
    for smiles in smiles_list:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            print(f"  [!] Cannot parse SMILES, skipping: {smiles}")
            continue
        mol.SetProp("_Name", smiles)  # name line = SMILES for -i Name
        AllChem.Compute2DCoords(mol)
        writer.write(mol)
    writer.close()
    return buf.getvalue()


COMMANDS = {
    "molecular_properties": (
        f'& "{CXCALC}" -i "Name" "{SDF}" '
        f'molecularpolarizability dipole fsp3 psa logp pka hbonddonoracceptor '
        f'| Out-File -FilePath "{OUT_DIR}\\result_props.csv" -Encoding UTF8'
    ),
    "logs": (
        f'& "{CXCALC}" -i "Name" "{SDF}" logs '
        f'| Out-File -FilePath "{OUT_DIR}\\result_logs.csv" -Encoding UTF8'
    ),
    "logd": (
        f'& "{CXCALC}" -i "Name" "{SDF}" logd '
        f'| Out-File -FilePath "{OUT_DIR}\\result_logd.csv" -Encoding UTF8'
    ),
}


def encode_cmd(raw: str) -> str:
    return base64.b64encode(raw.encode("utf-16-le")).decode("ascii")


def run_on_vm(raw_cmd: str, label: str) -> bool:
    b64 = encode_cmd(raw_cmd)
    cmd = [
        "vboxmanage", "guestcontrol", VM, "run",
        "--exe", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "--username", USER,
        "--password", PASS,
        "--wait-stdout", "--wait-stderr",
        "--", "powershell.exe", "-NonInteractive", "-EncodedCommand", b64,
    ]
    print(f"\n[{label}] Running...")
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    result.stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    result.stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    if result.stdout:
        print(f"  stdout: {result.stdout[:500]}")
    if result.stderr:
        print(f"  stderr: {result.stderr[:500]}")
    print(f"  exit code: {result.returncode}")
    return result.returncode == 0


def read_result(filename: str) -> str | None:
    path = os.path.join(HOST_OUT, filename)
    # Wait up to 10s for file sync
    for _ in range(10):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8-sig") as f:
                return f.read()
        time.sleep(1)
    return None


def main():
    print("=== cxcalc API Test ===")

    # Generate real SDF from test SMILES and write to shared folder
    os.makedirs(HOST_OUT, exist_ok=True)
    sdf_host_path = os.path.join(HOST_OUT, "test_api.sdf")
    sdf_content = build_sdf(TEST_SMILES)
    with open(sdf_host_path, "w", encoding="utf-8") as f:
        f.write(sdf_content)
    print(f"Written SDF ({len(TEST_SMILES)} molecules) to: {sdf_host_path}")
    print(f"SDF path in VM: {SDF}")
    print(f"Output dir: {HOST_OUT}")

    for label, raw_cmd in COMMANDS.items():
        ok = run_on_vm(raw_cmd, label)
        fname = f"result_{label.split('_')[0] if '_' in label else label}.csv"
        # fix filename to match what we set in each command
        fname_map = {
            "molecular_properties": "result_props.csv",
            "logs": "result_logs.csv",
            "logd": "result_logd.csv",
        }
        content = read_result(fname_map[label])
        if content:
            lines = [l for l in content.splitlines() if l.strip()]
            print(f"\n--- {label} result ({len(lines)} lines) ---")
            for line in lines[:10]:
                print(f"  {line}")
            if len(lines) > 10:
                print(f"  ... ({len(lines) - 10} more lines)")
        else:
            print(f"  [!] No output file found for {label}")

    print("\n=== Test complete ===")
    print(f"Result files in: {HOST_OUT}")
    for f in ["result_props.csv", "result_logs.csv", "result_logd.csv"]:
        path = os.path.join(HOST_OUT, f)
        if os.path.exists(path):
            print(f"  OK  {path} ({os.path.getsize(path)} bytes)")
        else:
            print(f"  MISS {path}")


if __name__ == "__main__":
    main()
