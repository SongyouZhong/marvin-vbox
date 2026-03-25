#!/bin/bash
# One-time setup: Configure VirtualBox shared folder for file transfer
# Run this ONCE before starting the API service.
#
# Prerequisites:
#   - Win11VM must be powered off (or use --transient for running VM)
#   - VirtualBox Guest Additions must be installed in the VM

set -e

SHARED_HOST="/home/data/vbox_shared"
VM_NAME="Win11VM"

echo "=== Marvin cxcalc API - Shared Folder Setup ==="

# 1. Create host directory
echo "[1/3] Creating shared folder on host: $SHARED_HOST"
sudo mkdir -p "$SHARED_HOST"
sudo chown -R "$USER:$USER" "$SHARED_HOST"

# 2. Add shared folder to VM
echo "[2/3] Adding shared folder to VM '$VM_NAME'..."
echo "      Host path:  $SHARED_HOST"
echo "      VM mount:   Z:\\shared"

# Remove existing shared folder if present (ignore errors)
vboxmanage sharedfolder remove "$VM_NAME" --name "shared" 2>/dev/null || true

# Check if VM is running
VM_STATE=$(vboxmanage showvminfo "$VM_NAME" --machinereadable | grep "VMState=" | head -1 | cut -d'"' -f2)

if [ "$VM_STATE" = "running" ]; then
    echo "      VM is running — adding as transient shared folder (will need re-run after VM restart)"
    echo "      TIP: Power off the VM first, then run this script again to make it permanent."
    vboxmanage sharedfolder add "$VM_NAME" --name "shared" \
        --hostpath "$SHARED_HOST" \
        --automount \
        --transient
else
    echo "      VM is powered off — adding as permanent shared folder"
    vboxmanage sharedfolder add "$VM_NAME" --name "shared" \
        --hostpath "$SHARED_HOST" \
        --automount
fi

echo ""
echo "Drive mapping in VM:"
echo "  Y: -> \\\\VBoxSvr\\shared -> $SHARED_HOST"

# 3. Install Python dependencies
echo "[3/3] Installing Python dependencies..."
pip install -r "$(dirname "$0")/requirements.txt"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "If the VM is running, the shared folder should be available at Z:\\shared immediately."
echo "If the VM was off, start it and the folder will auto-mount."
echo ""
echo "To start the API service:"
echo "  cd $(dirname "$0") && python run.py"
echo ""
echo "Test with:"
echo '  curl http://localhost:8111/api/v1/cxcalc/health'
echo '  curl -X POST http://localhost:8111/api/v1/cxcalc/calculate -F "file=@test.sdf" -F "calc_types=all"'
