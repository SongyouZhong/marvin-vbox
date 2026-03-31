# --- Single Stage Build (Ubuntu 24.04 matching host for VBox binary compatibility) ---
FROM ubuntu:24.04

LABEL maintainer="marvin-vbox"
LABEL description="Marvin cxcalc API - REST wrapper for ChemAxon cxcalc on VirtualBox VM"

# 安装 Python + pip + VBoxManage 所需的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    liblzf1 \
    libxml2 \
    libcurl4t64 \
    libssl3t64 \
    libpng16-16t64 \
    libvpx9 \
    procps \
    kmod \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装 Python 依赖
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# 复制应用代码
COPY app/ ./app/
COPY run.py .

# 创建与宿主机一致的用户 (uid=1003, username=songyou)
RUN groupadd --gid 1003 songyou && \
    useradd --uid 1003 --gid songyou --shell /bin/bash --create-home --home-dir /home/songyou songyou && \
    mkdir -p /home/songyou/.config/VirtualBox && \
    chown -R songyou:songyou /app /home/songyou

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
CMD ["python3", "run.py"]
