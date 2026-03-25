#!/bin/bash
# =============================================================================
# import-ova.sh — 在目标机器上导入 OVA 镜像并配置 VM 环境
#
# 前提条件:
#   - VirtualBox 7.x 已安装 (含 Extension Pack)
#   - OVA 文件可访问
#
# 用法:
#   ./scripts/import-ova.sh <ova-path>
#
# 示例:
#   ./scripts/import-ova.sh ./images/Win11VM-marvin.ova
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

OVA_PATH="${1:-}"
VM_NAME="${VM_NAME:-Win11VM}"
SHARED_HOST="${SHARED_FOLDER_HOST:-/home/data/vbox_shared}"

if [ -z "$OVA_PATH" ]; then
    echo "用法: $0 <ova-path>"
    echo ""
    echo "示例: $0 ./images/Win11VM-marvin.ova"
    exit 1
fi

if [ ! -f "$OVA_PATH" ]; then
    echo "[ERROR] OVA 文件不存在: $OVA_PATH"
    exit 1
fi

echo "=== Marvin VM OVA 导入工具 ==="
echo ""

# 检查 vboxmanage 是否可用
if ! command -v vboxmanage &>/dev/null; then
    echo "[ERROR] vboxmanage 未找到，请先安装 VirtualBox"
    echo ""
    echo "安装方法 (Ubuntu/Debian):"
    echo "  wget https://download.virtualbox.org/virtualbox/7.2.6/virtualbox-7.2_7.2.6-166987~Ubuntu~jammy_amd64.deb"
    echo "  sudo dpkg -i virtualbox-7.2_7.2.6-166987~Ubuntu~jammy_amd64.deb"
    echo "  sudo apt-get install -f"
    exit 1
fi

# 检查是否已存在同名 VM
if vboxmanage showvminfo "$VM_NAME" &>/dev/null 2>&1; then
    echo "[WARN] VM '$VM_NAME' 已存在"
    read -rp "是否删除现有 VM 并重新导入? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "已取消"
        exit 0
    fi
    # 确保 VM 已关闭
    VM_STATE=$(vboxmanage showvminfo "$VM_NAME" --machinereadable 2>/dev/null | grep "^VMState=" | head -1 | cut -d'"' -f2)
    if [ "$VM_STATE" = "running" ]; then
        echo "正在关闭 VM..."
        vboxmanage controlvm "$VM_NAME" poweroff 2>/dev/null || true
        sleep 3
    fi
    echo "正在删除现有 VM..."
    vboxmanage unregistervm "$VM_NAME" --delete 2>/dev/null || true
fi

# 验证 SHA256 (如果校验文件存在)
if [ -f "${OVA_PATH}.sha256" ]; then
    echo "[1/4] 验证文件完整性..."
    if sha256sum --check "${OVA_PATH}.sha256"; then
        echo "      校验通过"
    else
        echo "[ERROR] 文件校验失败，OVA 可能已损坏"
        exit 1
    fi
else
    echo "[1/4] 跳过校验 (未找到 .sha256 文件)"
fi

echo ""
echo "[2/4] 导入 OVA 镜像: $OVA_PATH"
echo "      VM 名称: $VM_NAME"
echo "      这可能需要较长时间..."
echo ""

vboxmanage import "$OVA_PATH" --vsys 0 --vmname "$VM_NAME"

echo ""
echo "[3/4] 配置共享文件夹..."
mkdir -p "$SHARED_HOST"
# 移除可能已存在的 shared folder 配置
vboxmanage sharedfolder remove "$VM_NAME" --name "shared" 2>/dev/null || true
vboxmanage sharedfolder add "$VM_NAME" --name "shared" \
    --hostpath "$SHARED_HOST" \
    --automount

echo "      宿主机路径: $SHARED_HOST"
echo "      VM 驱动器: Y:\\"

echo ""
echo "[4/4] 验证 VM 配置..."
echo "      VM 信息:"
vboxmanage showvminfo "$VM_NAME" --machinereadable 2>/dev/null | grep -E "^(name|memory|cpus|VMState|SharedFolder)" | head -10

echo ""
echo "=== 导入完成 ==="
echo ""
echo "后续步骤:"
echo "  1. 启动部署:  cd $PROJECT_DIR && docker compose up -d"
echo "  2. 健康检查:  curl http://localhost:8111/api/v1/cxcalc/health"
echo ""
echo "手动启动 VM (可选，API 会自动启动):"
echo "  vboxmanage startvm $VM_NAME --type headless"
