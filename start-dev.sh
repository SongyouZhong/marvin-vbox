#!/usr/bin/env bash
# =============================================================================
# start-dev.sh — 开发环境启动脚本（宿主机直接运行，不使用 Docker）
#
# 用法:
#   bash start-dev.sh -s 10.18.85.10:8333
#   bash start-dev.sh --server https://platform.createrna.com
#   bash start-dev.sh --rest-only                # 仅 REST 模式，不连接 Platform
#   bash start-dev.sh                            # 使用 .env 中已有配置
#
# 选项:
#   -s, --server     Platform 地址 (host:port 或完整 URL)
#   --hostname       Worker 主机名 (默认: marvin-vbox-dev)
#   --rest-only      仅启动 REST API，跳过 Worker/Platform 注册
#   --debug          开启调试模式（热重载）
#   --port           API 端口 (默认: 8111)
#   -e, --conda-env  指定 conda 环境名
#   -h, --help       显示帮助
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 默认值 ────────────────────────────────────────────────────────────────
PLATFORM_SERVER=""
WORKER_HOSTNAME=""
REST_ONLY=false
DEBUG=""
PORT=""
CONDA_ENV=""

# ── 参数解析 ──────────────────────────────────────────────────────────────
show_help() {
    sed -n '2,/^# =====/{ /^#/!d; s/^# \{0,1\}//; p }' "$0"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--server)     PLATFORM_SERVER="$2"; shift 2 ;;
        --hostname)      WORKER_HOSTNAME="$2"; shift 2 ;;
        --rest-only)     REST_ONLY=true;       shift   ;;
        --debug)         DEBUG=true;           shift   ;;
        --port)          PORT="$2";            shift 2 ;;
        -e|--conda-env)  CONDA_ENV="$2";       shift 2 ;;
        -h|--help)       show_help ;;
        *)               echo "未知参数: $1"; exit 1 ;;
    esac
done

# ── 规范化 Platform URL ──────────────────────────────────────────────────
normalize_url() {
    local addr="$1"
    if [[ "$addr" =~ ^https?:// ]]; then
        echo "$addr"
    else
        echo "http://$addr"
    fi
}

# ── 加载 .env（如果存在）─────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ── 应用命令行参数（优先级高于 .env）─────────────────────────────────────
if [[ -n "$PLATFORM_SERVER" ]]; then
    export PLATFORM_URL=$(normalize_url "$PLATFORM_SERVER")
fi
if [[ -n "$WORKER_HOSTNAME" ]]; then
    export WORKER_HOSTNAME="$WORKER_HOSTNAME"
else
    export WORKER_HOSTNAME="${WORKER_HOSTNAME:-marvin-vbox-dev}"
fi
if [[ -n "$DEBUG" ]]; then
    export DEBUG=true
fi
if [[ -n "$PORT" ]]; then
    export SERVER_PORT="$PORT"
fi
if [[ "$REST_ONLY" == true ]]; then
    # 清空 PLATFORM_URL，Worker 会检测到为空并跳过注册
    export PLATFORM_URL=""
fi

# ── 检查依赖 ──────────────────────────────────────────────────────────────
check_deps() {
    local missing=()
    if ! command -v vboxmanage &>/dev/null; then
        missing+=("vboxmanage")
    fi
    if ! python3 -c "import fastapi, uvicorn" 2>/dev/null; then
        missing+=("Python 依赖 (fastapi/uvicorn)")
    fi
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "[WARN] 缺少依赖: ${missing[*]}"
        if [[ " ${missing[*]} " =~ "Python" ]]; then
            echo "  运行: pip install -r requirements.txt"
            exit 1
        fi
    fi
}

# ── 启动 ──────────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"

echo "======================================="
echo "  Marvin cxcalc API — 开发模式"
echo "======================================="
echo "  端口:       ${SERVER_PORT:-8111}"
echo "  调试:       ${DEBUG:-false}"
if [[ "$REST_ONLY" == true ]]; then
    echo "  模式:       REST-only (无 Worker)"
else
    echo "  Platform:   ${PLATFORM_URL:-未配置 (Worker 将跳过注册)}"
    echo "  Hostname:   ${WORKER_HOSTNAME}"
fi
echo "======================================="
echo ""

check_deps

CMD="python3 run.py"

if [[ -n "$CONDA_ENV" ]]; then
    if ! conda info --envs 2>/dev/null | grep -q "$CONDA_ENV"; then
        echo "[ERROR] conda 环境 '$CONDA_ENV' 不存在" >&2
        exit 1
    fi
    exec conda run -n "$CONDA_ENV" --no-capture-output $CMD
else
    exec $CMD
fi
