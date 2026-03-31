#!/bin/bash
# =============================================================================
# docker-entrypoint.sh — Docker 容器启动入口
#
# 职责:
#   1. 验证 vboxmanage 可用性
#   2. 检测宿主机 VirtualBox VM 环境是否满足条件
#   3. 确保共享文件夹存在
#   4. (可选) 自动启动 VM
#   5. 验证 VM 内部环境 (Guest Additions / cxcalc / Java)
#   6. 启动 FastAPI 服务
# =============================================================================
set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

CHECKS_PASSED=0
CHECKS_FAILED=0
CHECKS_WARNED=0

pass() { echo -e "  ${GREEN}[PASS]${NC} $1"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; CHECKS_WARNED=$((CHECKS_WARNED + 1)); }

echo "=============================================="
echo "  Marvin cxcalc API — 启动环境检测"
echo "=============================================="
echo ""
echo "配置信息:"
echo "  VM Name:       ${VM_NAME}"
echo "  VM User:       ${VM_USERNAME}"
echo "  Shared Folder: ${SHARED_FOLDER_HOST}"
echo "  Server Port:   ${SERVER_PORT}"
echo "  cxcalc Path:   ${CXCALC_PATH}"
echo ""

# =========================================================================
# 检查 1: vboxmanage 是否可用 (从宿主机挂载)
# =========================================================================
echo "--- 检查 1/6: VBoxManage 可用性 ---"
if ! command -v vboxmanage &>/dev/null; then
    fail "vboxmanage 命令不可用"
    echo "    请确保在 docker-compose.yml 中正确挂载了宿主机的 vboxmanage 二进制文件"
    echo "    必需的 volume 挂载:"
    echo "      - /usr/bin/vboxmanage:/usr/bin/vboxmanage:ro"
    echo "      - /usr/lib/virtualbox:/usr/lib/virtualbox:ro"
    echo ""
    echo "环境检测失败: VBoxManage 是核心依赖，无法继续"
    exit 1
fi

VBOX_VERSION=$(vboxmanage --version 2>/dev/null || echo "unknown")
pass "vboxmanage 可用 (版本: ${VBOX_VERSION})"

# =========================================================================
# 检查 2: 目标 VM 是否存在
# =========================================================================
echo ""
echo "--- 检查 2/6: VM '${VM_NAME}' 是否存在 ---"

VM_INFO=$(vboxmanage showvminfo "$VM_NAME" --machinereadable 2>/dev/null) || VM_INFO=""

if [ -z "$VM_INFO" ]; then
    fail "VM '${VM_NAME}' 不存在"
    echo "    已注册的虚拟机:"
    vboxmanage list vms 2>/dev/null | sed 's/^/      /' || echo "      (无)"
    echo ""
    echo "    解决方法:"
    echo "      1. 导入 OVA: ./deploy.sh --ova ./images/Win11VM-marvin.ova"
    echo "      2. 或修改 VM_NAME 环境变量为已有的 VM 名称"
    echo ""
    echo -e "${YELLOW}[WARN] VM 未找到，API 仍将启动但计算功能不可用${NC}"
    echo ""
    # 写入空的 preflight 结果
    cat > /tmp/vm_preflight_result.json <<INNEREOF
{
    "vboxmanage_version": "${VBOX_VERSION}",
    "vm_name": "${VM_NAME}",
    "vm_state": "not_found",
    "checks_passed": ${CHECKS_PASSED},
    "checks_failed": ${CHECKS_FAILED},
    "checks_warned": ${CHECKS_WARNED}
}
INNEREOF
    echo "[INFO] 启动 FastAPI 服务 (降级模式)..."
    exec "$@"
fi

VM_STATE=$(echo "$VM_INFO" | grep "^VMState=" | head -1 | cut -d'"' -f2 || echo "unknown")
VM_OS_TYPE=$(echo "$VM_INFO" | grep "^ostype=" | head -1 | cut -d'"' -f2 || echo "unknown")
VM_MEMORY=$(echo "$VM_INFO" | grep "^memory=" | head -1 | cut -d'=' -f2 || echo "?")
VM_CPUS=$(echo "$VM_INFO" | grep "^cpus=" | head -1 | cut -d'=' -f2 || echo "?")

pass "VM '${VM_NAME}' 存在 (OS: ${VM_OS_TYPE}, 内存: ${VM_MEMORY}MB, CPU: ${VM_CPUS}核, 状态: ${VM_STATE})"

# 检查 OS 类型是否为 Windows
if echo "$VM_OS_TYPE" | grep -qi "windows"; then
    pass "OS 类型正确: ${VM_OS_TYPE}"
else
    warn "OS 类型为 '${VM_OS_TYPE}'，预期为 Windows 系列"
fi

# =========================================================================
# 检查 3: 共享文件夹配置
# =========================================================================
echo ""
echo "--- 检查 3/6: 共享文件夹配置 ---"

# 检查宿主机侧目录
mkdir -p "${SHARED_FOLDER_HOST}" 2>/dev/null || true
if [ -d "${SHARED_FOLDER_HOST}" ]; then
    pass "宿主机共享目录存在: ${SHARED_FOLDER_HOST}"
    if [ -w "${SHARED_FOLDER_HOST}" ]; then
        pass "宿主机共享目录可写"
    else
        fail "宿主机共享目录不可写: ${SHARED_FOLDER_HOST}"
    fi
else
    fail "宿主机共享目录不存在且无法创建: ${SHARED_FOLDER_HOST}"
fi

# 检查 VM 侧共享文件夹映射
SF_CONFIGURED=$(echo "$VM_INFO" | grep -i 'SharedFolderNameMachineMapping' || true)
if [ -n "$SF_CONFIGURED" ]; then
    pass "VM 已配置共享文件夹映射"
    echo "$SF_CONFIGURED" | while read -r line; do
        SF_NAME=$(echo "$line" | cut -d'"' -f2)
        echo "      映射名称: ${SF_NAME}"
    done
else
    warn "VM 未检测到共享文件夹映射 (可能是运行时临时挂载)"
fi

# =========================================================================
# 检查 4: (可选) 启动 VM
# =========================================================================
echo ""
echo "--- 检查 4/6: VM 运行状态 ---"

if [ "${AUTO_START_VM:-true}" = "true" ]; then
    if [ "$VM_STATE" = "running" ]; then
        pass "VM '${VM_NAME}' 正在运行"
    elif [ "$VM_STATE" = "poweroff" ] || [ "$VM_STATE" = "saved" ] || [ "$VM_STATE" = "aborted" ]; then
        echo "  [INFO] VM '${VM_NAME}' 状态: ${VM_STATE}，正在启动..."
        if vboxmanage startvm "$VM_NAME" --type headless 2>/dev/null; then
            echo "  [INFO] 等待 VM 就绪..."
            VM_READY=false
            for i in $(seq 1 30); do
                sleep 5
                RESULT=$(vboxmanage guestcontrol "$VM_NAME" run \
                    --exe "C:\\Windows\\System32\\cmd.exe" \
                    --username "$VM_USERNAME" \
                    --password "$VM_PASSWORD" \
                    --wait-stdout \
                    -- "cmd.exe" "/c" "echo" "ready" 2>/dev/null) || true
                if echo "$RESULT" | grep -q "ready"; then
                    VM_READY=true
                    pass "VM '${VM_NAME}' 已启动并就绪 (用时 $((i * 5)) 秒)"
                    VM_STATE="running"
                    break
                fi
                echo "    等待中... (${i}/30)"
            done
            if [ "$VM_READY" = "false" ]; then
                warn "VM 已发送启动命令但 Guest Additions 未响应 (API 将在请求时重试)"
            fi
        else
            warn "VM 启动失败，API 仍将启动 (请求时会自动重试启动)"
        fi
    else
        warn "VM '${VM_NAME}' 状态异常: ${VM_STATE} (API 将在请求时尝试启动)"
    fi
else
    if [ "$VM_STATE" = "running" ]; then
        pass "VM '${VM_NAME}' 正在运行 (AUTO_START_VM=false)"
    else
        warn "VM '${VM_NAME}' 未运行 (状态: ${VM_STATE})，且 AUTO_START_VM=false"
    fi
fi

# =========================================================================
# 检查 5: Guest Additions 连通性 (仅在 VM 运行时)
# =========================================================================
echo ""
echo "--- 检查 5/6: Guest Additions 连通性 ---"

if [ "$VM_STATE" = "running" ]; then
    GA_RESULT=$(vboxmanage guestcontrol "$VM_NAME" run \
        --exe "C:\\Windows\\System32\\cmd.exe" \
        --username "$VM_USERNAME" \
        --password "$VM_PASSWORD" \
        --wait-stdout \
        -- "cmd.exe" "/c" "echo" "ga_test_ok" 2>/dev/null) || GA_RESULT=""

    if echo "$GA_RESULT" | grep -q "ga_test_ok"; then
        pass "Guest Additions guestcontrol 通信正常"
    else
        fail "Guest Additions guestcontrol 通信失败 (认证或 GA 服务异常)"
        echo "    请检查: VM 用户名/密码是否正确，Guest Additions 是否已安装"
    fi
else
    warn "VM 未运行，跳过 Guest Additions 检查"
fi

# =========================================================================
# 检查 6: VM 内部环境 (Java + cxcalc) (仅在 VM 运行且 GA 可用时)
# =========================================================================
echo ""
echo "--- 检查 6/6: VM 内部环境 (Java / cxcalc) ---"

if [ "$VM_STATE" = "running" ] && echo "$GA_RESULT" | grep -q "ga_test_ok" 2>/dev/null; then
    # 检查 Java
    JAVA_RESULT=$(vboxmanage guestcontrol "$VM_NAME" run \
        --exe "C:\\Windows\\System32\\cmd.exe" \
        --username "$VM_USERNAME" \
        --password "$VM_PASSWORD" \
        --wait-stdout --wait-stderr \
        -- "cmd.exe" "/c" "java" "-version" 2>/dev/null) || JAVA_RESULT=""

    if [ -n "$JAVA_RESULT" ]; then
        JAVA_VER=$(echo "$JAVA_RESULT" | head -1)
        pass "Java 可用: ${JAVA_VER}"
    else
        fail "Java 不可用或未安装"
        echo "    cxcalc 需要 32 位 JRE 8，请在 VM 中安装 jre-8uXXX-windows-i586.exe"
    fi

    # 检查 cxcalc
    CXCALC_RESULT=$(vboxmanage guestcontrol "$VM_NAME" run \
        --exe "C:\\Windows\\System32\\cmd.exe" \
        --username "$VM_USERNAME" \
        --password "$VM_PASSWORD" \
        --wait-stdout --wait-stderr \
        -- "cmd.exe" "/c" "${CXCALC_PATH}" "-h" 2>/dev/null) || CXCALC_RESULT=""

    if [ -n "$CXCALC_RESULT" ]; then
        pass "cxcalc 可用 (路径: ${CXCALC_PATH})"
    else
        fail "cxcalc 不可用 (路径: ${CXCALC_PATH})"
        echo "    请确认 MarvinBeans 已安装且路径正确"
    fi

    # 检查共享文件夹在 VM 内是否可访问
    SF_VM_RESULT=$(vboxmanage guestcontrol "$VM_NAME" run \
        --exe "C:\\Windows\\System32\\cmd.exe" \
        --username "$VM_USERNAME" \
        --password "$VM_PASSWORD" \
        --wait-stdout \
        -- "cmd.exe" "/c" "dir" "${SHARED_FOLDER_VM}\\" 2>/dev/null) || SF_VM_RESULT=""

    if [ -n "$SF_VM_RESULT" ]; then
        pass "VM 内共享文件夹可访问: ${SHARED_FOLDER_VM}"
    else
        warn "VM 内共享文件夹不可访问: ${SHARED_FOLDER_VM} (可能需要手动 net use 映射)"
    fi
else
    warn "VM 未运行或 Guest Additions 不可用，跳过 VM 内部环境检查"
fi

# =========================================================================
# 检测结果汇总
# =========================================================================
echo ""
echo "=============================================="
echo "  环境检测结果汇总"
echo "=============================================="
echo -e "  ${GREEN}通过: ${CHECKS_PASSED}${NC}  |  ${RED}失败: ${CHECKS_FAILED}${NC}  |  ${YELLOW}警告: ${CHECKS_WARNED}${NC}"
echo ""

if [ "$CHECKS_FAILED" -gt 0 ]; then
    echo -e "${YELLOW}[WARN] 存在 ${CHECKS_FAILED} 项检测失败，API 仍将启动但部分功能可能不可用${NC}"
    echo ""
fi

# 将检测结果写入文件，供 Python 应用读取
cat > /tmp/vm_preflight_result.json <<EOF
{
    "vboxmanage_version": "${VBOX_VERSION}",
    "vm_name": "${VM_NAME}",
    "vm_state": "${VM_STATE}",
    "vm_os_type": "${VM_OS_TYPE}",
    "vm_memory_mb": "${VM_MEMORY}",
    "vm_cpus": "${VM_CPUS}",
    "checks_passed": ${CHECKS_PASSED},
    "checks_failed": ${CHECKS_FAILED},
    "checks_warned": ${CHECKS_WARNED}
}
EOF

echo "[INFO] 启动 FastAPI 服务..."
echo ""

# 执行 CMD (默认: python run.py)
exec "$@"
