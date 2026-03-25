#!/bin/bash
# =============================================================================
# export-ova.sh — 将当前 Win11VM 导出为 OVA 标准化交付镜像
#
# 前提条件:
#   - VM 必须处于关机状态
#   - VirtualBox 已安装且 VM 存在
#
# 用法:
#   ./scripts/export-ova.sh [输出路径]
#
# 示例:
#   ./scripts/export-ova.sh                          # 默认输出到 ./images/Win11VM-marvin.ova
#   ./scripts/export-ova.sh /tmp/marvin-vm.ova       # 指定输出路径
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

VM_NAME="${VM_NAME:-Win11VM}"
OUTPUT_DIR="${PROJECT_DIR}/images"
OUTPUT_PATH="${1:-${OUTPUT_DIR}/Win11VM-marvin.ova}"

echo "=== Marvin VM OVA 导出工具 ==="
echo ""

# 检查 vboxmanage 是否可用
if ! command -v vboxmanage &>/dev/null; then
    echo "[ERROR] vboxmanage 未找到，请先安装 VirtualBox"
    exit 1
fi

# 检查 VM 是否存在
if ! vboxmanage showvminfo "$VM_NAME" &>/dev/null; then
    echo "[ERROR] VM '$VM_NAME' 不存在"
    echo "可用的虚拟机:"
    vboxmanage list vms
    exit 1
fi

# 检查 VM 是否已关机
VM_STATE=$(vboxmanage showvminfo "$VM_NAME" --machinereadable | grep "^VMState=" | head -1 | cut -d'"' -f2)
if [ "$VM_STATE" = "running" ]; then
    echo "[WARN] VM 正在运行，正在安全关机..."
    vboxmanage controlvm "$VM_NAME" acpipowerbutton
    echo "等待 VM 关机 (最多 120 秒)..."
    for i in $(seq 1 24); do
        sleep 5
        VM_STATE=$(vboxmanage showvminfo "$VM_NAME" --machinereadable | grep "^VMState=" | head -1 | cut -d'"' -f2)
        if [ "$VM_STATE" != "running" ]; then
            echo "VM 已关机"
            break
        fi
        echo "  等待中... (${i}/24)"
    done
    VM_STATE=$(vboxmanage showvminfo "$VM_NAME" --machinereadable | grep "^VMState=" | head -1 | cut -d'"' -f2)
    if [ "$VM_STATE" = "running" ]; then
        echo "[ERROR] VM 未能在超时时间内关机，请手动关闭后重试"
        exit 1
    fi
fi

# 创建输出目录
mkdir -p "$(dirname "$OUTPUT_PATH")"

# 删除旧的 OVA (如果存在)
if [ -f "$OUTPUT_PATH" ]; then
    echo "[INFO] 删除旧的 OVA 文件: $OUTPUT_PATH"
    rm -f "$OUTPUT_PATH"
fi

echo ""
echo "[1/3] 导出 VM '$VM_NAME' 到 OVA..."
echo "      输出路径: $OUTPUT_PATH"
echo "      这可能需要较长时间，取决于磁盘大小..."
echo ""

vboxmanage export "$VM_NAME" \
    --output "$OUTPUT_PATH" \
    --ovf20 \
    --manifest \
    --options manifest,nomacs

echo ""
echo "[2/3] 验证 OVA 文件..."
OVA_SIZE=$(du -h "$OUTPUT_PATH" | cut -f1)
echo "      文件大小: $OVA_SIZE"

echo ""
echo "[3/3] 生成校验和..."
sha256sum "$OUTPUT_PATH" > "${OUTPUT_PATH}.sha256"
echo "      校验和已保存到: ${OUTPUT_PATH}.sha256"

echo ""
echo "=== 导出完成 ==="
echo ""
echo "交付文件:"
echo "  OVA 镜像:  $OUTPUT_PATH ($OVA_SIZE)"
echo "  校验和:    ${OUTPUT_PATH}.sha256"
echo ""
echo "在目标机器上导入:"
echo "  ./scripts/import-ova.sh $OUTPUT_PATH"
