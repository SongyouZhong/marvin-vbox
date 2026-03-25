# --- Build Stage ---
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Runtime Stage ---
FROM python:3.12-slim

LABEL maintainer="marvin-vbox"
LABEL description="Marvin cxcalc API - REST wrapper for ChemAxon cxcalc on VirtualBox VM"

# 安装 VirtualBox guest control CLI (只需 vboxmanage)
# 使用宿主机的 vboxmanage 二进制文件 (通过 volume 挂载)
# 不在容器内安装 VirtualBox — 依赖宿主机

WORKDIR /app

# 从 builder 复制已安装的 Python 包
COPY --from=builder /install /usr/local

# 复制应用代码
COPY app/ ./app/
COPY run.py .

# 创建非 root 用户
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

# 默认环境变量
ENV SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8111 \
    DEBUG=false \
    VM_NAME=Win11VM \
    VM_USERNAME=marvin-box \
    VM_PASSWORD=123123 \
    SHARED_FOLDER_HOST=/data/shared \
    SHARED_FOLDER_VM="Y:\\" \
    CXCALC_PATH="C:\\Progra~2\\ChemAxon\\MarvinBeans\\bin\\cxcalc.bat" \
    COMMAND_TIMEOUT=600

EXPOSE 8111

# 使用 entrypoint 脚本处理启动逻辑
COPY scripts/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "run.py"]
