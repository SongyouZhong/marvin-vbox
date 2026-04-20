#!/bin/bash
# =============================================================================
# deploy.sh — 一键部署 Marvin cxcalc API
#
# 此脚本在目标机器上执行以下操作:
#   1. 检查依赖 (VirtualBox, Docker)
#   2. 导入 OVA 镜像 (如果提供)
#   3. 配置共享文件夹
#   4. 构建并启动 Docker 容器
#   5. 验证部署
#
# 用法:
#   ./deploy.sh -s 10.18.85.10:8333                        # 指定 Platform 地址
#   ./deploy.sh --server https://platform.createrna.com    # 支持完整 URL
#   ./deploy.sh -s 10.18.85.10:8333 --ova ./images/Win11VM-marvin.ova
#   ./deploy.sh --rebuild                                  # 重新构建镜像
#
# 选项:
#   -s, --server   Platform 地址 (host:port 或完整 URL)，首次部署必填
#   --ova          导入 OVA 镜像路径
#   --rebuild      重新构建 Docker 镜像
#   --hostname     Worker 主机名 (默认: marvin-vbox-001)
#   -h, --help     显示帮助
#
# 环境变量:
#   VM_NAME             VM 名称 (默认: Win11VM)
#   VM_USERNAME         VM 用户名 (默认: marvin-box)
#   VM_PASSWORD         VM 密码 (默认: 123123)
#   SHARED_FOLDER_HOST  共享文件夹路径 (默认: /home/data/marvin_vbox_sharad)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 默认值
OVA_PATH=""
REBUILD=false
PLATFORM_SERVER=""
WORKER_HOSTNAME=""
VM_NAME="${VM_NAME:-Win11VM}"
SHARED_FOLDER_HOST="${SHARED_FOLDER_HOST:-/home/data/marvin_vbox_sharad}"

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--server)
            PLATFORM_SERVER="$2"
            shift 2
            ;;
        --hostname)
            WORKER_HOSTNAME="$2"
            shift 2
            ;;
        --ova)
            OVA_PATH="$2"
            shift 2
            ;;
        --rebuild)
            REBUILD=true
            shift
            ;;
        --help|-h)
            sed -n '2,/^# =====/{ /^#/!d; s/^# \{0,1\}//; p }' "$0"
            exit 0
            ;;
        *)
            echo "未知参数: $1"
            exit 1
            ;;
    esac
done

# 规范化 Platform URL（与 node_agent 一致：裸地址自动补 http://）
normalize_url() {
    local addr="$1"
    if [[ "$addr" =~ ^https?:// ]]; then
        echo "$addr"
    else
        echo "http://$addr"
    fi
}

if [[ -n "$PLATFORM_SERVER" ]]; then
    PLATFORM_URL=$(normalize_url "$PLATFORM_SERVER")
else
    PLATFORM_URL=""
fi

echo "=============================================="
echo "  Marvin cxcalc API — 一键部署"
echo "=============================================="
if [[ -n "$PLATFORM_URL" ]]; then
    echo "  Platform:  $PLATFORM_URL"
fi
if [[ -n "$WORKER_HOSTNAME" ]]; then
    echo "  Hostname:  $WORKER_HOSTNAME"
fi
echo ""

# =============================================================================
# Step 1: 检查依赖
# =============================================================================
echo "[Step 1/5] 检查系统依赖..."

MISSING=()

if ! command -v vboxmanage &>/dev/null; then
    MISSING+=("VirtualBox (vboxmanage)")
fi

if ! command -v docker &>/dev/null; then
    MISSING+=("Docker")
fi

# 兼容 docker compose V2 和 docker-compose V1
DOCKER_COMPOSE=""
if docker compose version &>/dev/null 2>&1; then
    DOCKER_COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
    DOCKER_COMPOSE="docker-compose"
else
    MISSING+=("Docker Compose (V1 docker-compose 或 V2 docker compose)")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "[ERROR] 缺少以下依赖:"
    for dep in "${MISSING[@]}"; do
        echo "  - $dep"
    done
    echo ""
    echo "请先安装缺失的依赖后重新运行此脚本"
    exit 1
fi

echo "  VirtualBox: $(vboxmanage --version)"
echo "  Docker:     $(docker --version | cut -d' ' -f3 | tr -d ',')"
echo "  Compose:    $($DOCKER_COMPOSE version --short 2>/dev/null || $DOCKER_COMPOSE version 2>/dev/null | head -1)"
echo "  [OK] 所有依赖已就绪"

# =============================================================================
# Step 2: 导入 OVA (如果提供)
# =============================================================================
echo ""
echo "[Step 2/5] VM 配置..."

if [ -n "$OVA_PATH" ]; then
    echo "  导入 OVA 镜像: $OVA_PATH"
    bash "${SCRIPT_DIR}/scripts/import-ova.sh" "$OVA_PATH"
else
    # 检查 VM 是否存在
    if vboxmanage showvminfo "$VM_NAME" &>/dev/null 2>&1; then
        echo "  [OK] VM '$VM_NAME' 已存在"
    else
        echo "  [ERROR] VM '$VM_NAME' 不存在"
        echo ""
        echo "  请先导入 OVA 镜像:"
        echo "    $0 --ova <path-to-ova>"
        echo ""
        echo "  或手动创建 VM (参考 README.md)"
        exit 1
    fi
fi

# =============================================================================
# Step 3: 配置共享文件夹
# =============================================================================
echo ""
echo "[Step 3/5] 配置共享文件夹..."

mkdir -p "$SHARED_FOLDER_HOST"
echo "  宿主机路径: $SHARED_FOLDER_HOST"

# 检查 VM 上的共享文件夹配置
SF_EXISTS=$(vboxmanage showvminfo "$VM_NAME" --machinereadable 2>/dev/null | grep 'SharedFolderNameMachineMapping.*=.*"shared"' || true)
if [ -z "$SF_EXISTS" ]; then
    echo "  添加共享文件夹到 VM..."
    VM_STATE=$(vboxmanage showvminfo "$VM_NAME" --machinereadable | grep "^VMState=" | head -1 | cut -d'"' -f2)
    if [ "$VM_STATE" = "running" ]; then
        vboxmanage sharedfolder add "$VM_NAME" --name "shared" \
            --hostpath "$SHARED_FOLDER_HOST" --automount --transient
        echo "  [WARN] VM 正在运行，共享文件夹以临时方式添加"
        echo "         建议关机后重新运行 deploy.sh 使其永久生效"
    else
        vboxmanage sharedfolder add "$VM_NAME" --name "shared" \
            --hostpath "$SHARED_FOLDER_HOST" --automount
    fi
else
    echo "  [OK] 共享文件夹已配置"
fi

# =============================================================================
# Step 4: 构建并启动 Docker 容器
# =============================================================================
echo ""
echo "[Step 4/5] 构建并启动 Docker 服务..."

cd "$SCRIPT_DIR"

# 创建或更新 .env 配置
if [ ! -f .env ]; then
    echo "  生成 .env 配置文件..."
    cp .env.example .env
    echo "  [INFO] 已创建 .env"
fi

# 如果用户通过 --server 指定了 Platform 地址，写入 .env
if [[ -n "$PLATFORM_URL" ]]; then
    if grep -q '^PLATFORM_URL=' .env 2>/dev/null; then
        sed -i "s|^PLATFORM_URL=.*|PLATFORM_URL=${PLATFORM_URL}|" .env
    else
        echo "PLATFORM_URL=${PLATFORM_URL}" >> .env
    fi
    echo "  Platform URL: $PLATFORM_URL"
else
    # 没有通过参数指定，检查 .env 中是否已配置
    EXISTING_URL=$(grep '^PLATFORM_URL=' .env 2>/dev/null | cut -d'=' -f2- || true)
    if [[ -z "$EXISTING_URL" ]]; then
        echo "  [ERROR] 未指定 Platform 地址"
        echo "  请使用 -s/--server 参数指定，例如:"
        echo "    $0 -s 10.18.85.10:8333"
        echo "    $0 --server https://platform.createrna.com"
        exit 1
    fi
    echo "  Platform URL: $EXISTING_URL (来自 .env)"
fi

# 如果用户通过 --hostname 指定了 Worker 主机名，写入 .env
if [[ -n "$WORKER_HOSTNAME" ]]; then
    if grep -q '^WORKER_HOSTNAME=' .env 2>/dev/null; then
        sed -i "s|^WORKER_HOSTNAME=.*|WORKER_HOSTNAME=${WORKER_HOSTNAME}|" .env
    else
        echo "WORKER_HOSTNAME=${WORKER_HOSTNAME}" >> .env
    fi
    echo "  Worker 主机名: $WORKER_HOSTNAME"
fi

if [ "$REBUILD" = true ]; then
    echo "  重新构建镜像..."
    $DOCKER_COMPOSE build --no-cache
else
    $DOCKER_COMPOSE build
fi

echo "  启动容器..."
$DOCKER_COMPOSE up -d

echo "  [OK] 容器已启动"

# =============================================================================
# Step 5: 验证部署
# =============================================================================
echo ""
echo "[Step 5/5] 验证部署..."

echo "  等待服务就绪 (最多 90 秒)..."
for i in $(seq 1 18); do
    sleep 5
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8111/" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        echo "  [OK] API 服务已就绪"
        break
    fi
    echo "  等待中... (${i}/18)"
done

# 最终健康检查
echo ""
echo "  健康检查:"
HEALTH=$(curl -s "http://localhost:8111/api/v1/cxcalc/health" 2>/dev/null || echo '{"error": "无法连接"}')
echo "  $HEALTH"

echo ""
echo "=============================================="
echo "  部署完成!"
echo "=============================================="
echo ""
echo "  API 地址:     http://localhost:8111"
echo "  Swagger 文档: http://localhost:8111/docs"
echo "  健康检查:     http://localhost:8111/api/v1/cxcalc/health"
echo ""
echo "  管理命令:"
echo "    查看日志:    $DOCKER_COMPOSE logs -f"
echo "    停止服务:    $DOCKER_COMPOSE down"
echo "    重启服务:    $DOCKER_COMPOSE restart"
echo "    VM 管理:     ./scripts/vm-manager.sh {status|start|stop|check}"
echo ""
echo "  测试命令:"
echo "    curl -X POST http://localhost:8111/api/v1/cxcalc/calculate \\"
echo "      -F 'file=@test_api.sdf' -F 'calc_types=all'"
