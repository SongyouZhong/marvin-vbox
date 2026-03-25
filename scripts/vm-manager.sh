#!/bin/bash
# =============================================================================
# vm-manager.sh — VirtualBox VM 生命周期管理
#
# 用法:
#   ./scripts/vm-manager.sh status    # 查看 VM 状态
#   ./scripts/vm-manager.sh start     # 启动 VM
#   ./scripts/vm-manager.sh stop      # 安全关机
#   ./scripts/vm-manager.sh restart   # 重启 VM
#   ./scripts/vm-manager.sh check     # 检查 Guest Additions 可用性
# =============================================================================
set -euo pipefail

VM_NAME="${VM_NAME:-Win11VM}"
VM_USERNAME="${VM_USERNAME:-marvin-box}"
VM_PASSWORD="${VM_PASSWORD:-123123}"

usage() {
    echo "用法: $0 {status|start|stop|restart|check}"
    echo ""
    echo "命令:"
    echo "  status   查看 VM 运行状态"
    echo "  start    以 headless 模式启动 VM"
    echo "  stop     安全关机 (ACPI)"
    echo "  restart  重启 VM"
    echo "  check    检查 Guest Additions 和 cxcalc 可用性"
    exit 1
}

get_vm_state() {
    vboxmanage showvminfo "$VM_NAME" --machinereadable 2>/dev/null \
        | grep "^VMState=" | head -1 | cut -d'"' -f2 || echo "not_found"
}

cmd_status() {
    local state
    state=$(get_vm_state)
    echo "VM '$VM_NAME' 状态: $state"

    if [ "$state" = "running" ]; then
        # 获取更多运行时信息
        local mem cpus
        mem=$(vboxmanage showvminfo "$VM_NAME" --machinereadable | grep "^memory=" | cut -d'=' -f2)
        cpus=$(vboxmanage showvminfo "$VM_NAME" --machinereadable | grep "^cpus=" | cut -d'=' -f2)
        echo "  内存: ${mem}MB | CPU: ${cpus} 核"
    fi
}

cmd_start() {
    local state
    state=$(get_vm_state)

    if [ "$state" = "running" ]; then
        echo "VM '$VM_NAME' 已在运行"
        return 0
    fi

    echo "正在以 headless 模式启动 VM '$VM_NAME'..."
    vboxmanage startvm "$VM_NAME" --type headless

    echo "等待 Guest Additions 就绪..."
    for i in $(seq 1 30); do
        sleep 5
        local result
        result=$(vboxmanage guestcontrol "$VM_NAME" run \
            --exe "C:\\Windows\\System32\\cmd.exe" \
            --username "$VM_USERNAME" \
            --password "$VM_PASSWORD" \
            --wait-stdout \
            -- "cmd.exe" "/c" "echo" "ready" 2>/dev/null) || true
        if echo "$result" | grep -q "ready"; then
            echo "VM '$VM_NAME' 已就绪 (用时 $((i * 5)) 秒)"
            return 0
        fi
        echo "  等待中... (${i}/30)"
    done

    echo "[WARN] VM 已启动但 Guest Additions 未响应"
    return 1
}

cmd_stop() {
    local state
    state=$(get_vm_state)

    if [ "$state" != "running" ]; then
        echo "VM '$VM_NAME' 未在运行 (状态: $state)"
        return 0
    fi

    echo "正在安全关闭 VM '$VM_NAME' (ACPI)..."
    vboxmanage controlvm "$VM_NAME" acpipowerbutton

    for i in $(seq 1 24); do
        sleep 5
        state=$(get_vm_state)
        if [ "$state" != "running" ]; then
            echo "VM 已关闭 (用时 $((i * 5)) 秒)"
            return 0
        fi
        echo "  等待中... (${i}/24)"
    done

    echo "[ERROR] VM 未在 120 秒内关闭"
    read -rp "是否强制关机? (y/N): " confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        vboxmanage controlvm "$VM_NAME" poweroff
        echo "VM 已强制关机"
    fi
}

cmd_restart() {
    cmd_stop
    sleep 3
    cmd_start
}

cmd_check() {
    local state
    state=$(get_vm_state)

    echo "=== VM 环境检查 ==="
    echo ""
    echo "1. VM 状态: $state"

    if [ "$state" != "running" ]; then
        echo "[FAIL] VM 未运行，请先启动: $0 start"
        return 1
    fi

    echo ""
    echo "2. Guest Additions 连通性..."
    local result
    result=$(vboxmanage guestcontrol "$VM_NAME" run \
        --exe "C:\\Windows\\System32\\cmd.exe" \
        --username "$VM_USERNAME" \
        --password "$VM_PASSWORD" \
        --wait-stdout \
        -- "cmd.exe" "/c" "echo" "guest_ok" 2>/dev/null) || true
    if echo "$result" | grep -q "guest_ok"; then
        echo "   [OK] Guest Additions 通信正常"
    else
        echo "   [FAIL] Guest Additions 不可用"
        return 1
    fi

    echo ""
    echo "3. cxcalc 可用性..."
    result=$(vboxmanage guestcontrol "$VM_NAME" run \
        --exe "C:\\Windows\\System32\\cmd.exe" \
        --username "$VM_USERNAME" \
        --password "$VM_PASSWORD" \
        --wait-stdout \
        -- "cmd.exe" "/c" "C:\\Progra~2\\ChemAxon\\MarvinBeans\\bin\\cxcalc.bat" "-h" 2>/dev/null) || true
    if [ -n "$result" ]; then
        echo "   [OK] cxcalc 可用"
    else
        echo "   [WARN] cxcalc 未响应 (可能路径不正确)"
    fi

    echo ""
    echo "4. 共享文件夹..."
    local shared_host="${SHARED_FOLDER_HOST:-/home/data/marvin_vbox_sharad}"
    if [ -d "$shared_host" ]; then
        echo "   [OK] 宿主机共享文件夹存在: $shared_host"
    else
        echo "   [WARN] 宿主机共享文件夹不存在: $shared_host"
    fi

    echo ""
    echo "=== 检查完成 ==="
}

# Main
case "${1:-}" in
    status)  cmd_status ;;
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    check)   cmd_check ;;
    *)       usage ;;
esac
