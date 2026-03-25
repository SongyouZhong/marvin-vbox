# Marvin cxcalc API — 部署操作记录

**日期**: 2026年3月25日  
**环境**: Linux 宿主机 + VirtualBox 7.2.6 + Win11VM

---

## 一、背景

Marvin cxcalc API 是一个 Python FastAPI 服务，通过 VirtualBox `guestcontrol` 调用 Windows VM 中的 ChemAxon `cxcalc.bat`，提供分子性质计算（logP、pKa、logS、logD 等）REST 接口。

本次工作目标：为该项目建立完整的部署体系，包括 VM 标准化交付（OVA）、API 容器化（Docker）、一键编排（docker-compose），以及将 OVA 镜像上传至 MinIO 对象存储。

---

## 二、新增文件清单

### 2.1 容器化相关

| 文件 | 说明 |
|------|------|
| `Dockerfile` | 两阶段构建 FastAPI 镜像，基于 `python:3.12-slim` |
| `docker-compose.yml` | 编排 API 容器，使用 host 网络模式，挂载宿主机 VBoxManage |
| `.env.example` | 环境变量模板（VM 配置、路径、超时等） |
| `.dockerignore` | Docker 构建时忽略的文件（OVA、.env、缓存等） |

**容器设计要点**：
- 容器使用 `network_mode: host`，确保能访问宿主机 VirtualBox 进程
- 通过 volume 挂载 `/usr/bin/vboxmanage`、`/usr/lib/virtualbox`、`~/.config/VirtualBox`，避免在容器内安装 VirtualBox
- 共享文件夹 `/home/data/marvin_vbox_sharad` 双向挂载，容器与 VM 均可读写

### 2.2 脚本工具

| 文件 | 说明 |
|------|------|
| `scripts/export-ova.sh` | 关闭 VM → 导出 OVA → 生成 sha256 校验文件 |
| `scripts/import-ova.sh` | 验证校验和 → 导入 OVA → 配置共享文件夹 |
| `scripts/vm-manager.sh` | VM 生命周期管理（status / start / stop / restart / check） |
| `scripts/docker-entrypoint.sh` | 容器启动入口：检查 vboxmanage → 创建共享目录 → 可选启动 VM → 运行 API |
| `scripts/upload_ova_to_minio.py` | 将 OVA 大文件分片上传到 MinIO 对象存储 |
| `deploy.sh` | 一键部署：检查依赖 → 可选导入 OVA → 配置共享文件夹 → 构建容器 → 验证 |

### 2.3 OVA 镜像目录

| 路径 | 说明 |
|------|------|
| `images/Win11VM-marvin.ova` | 导出的 VM 镜像（11.85 GB，已上传 MinIO，本地可删除） |
| `images/.gitignore` | 忽略 .ova / .sha256 大文件，不纳入 Git 版本控制 |

---

## 三、实际执行操作

### 3.1 创建专用 Python 虚拟环境

```bash
micromamba create -n marvin-vbox python=3.12 -y
micromamba install -n marvin-vbox pip -y
micromamba run -n marvin-vbox pip install minio fastapi uvicorn python-multipart
```

新建独立环境 `marvin-vbox`，安装项目所需依赖，不污染已有环境。

### 3.2 关闭 Win11VM

```bash
vboxmanage controlvm "Win11VM" acpipowerbutton  # ACPI 关机（超时）
vboxmanage controlvm "Win11VM" poweroff          # 强制关机
```

OVA 导出要求 VM 处于关机状态。ACPI 软关机等待 120 秒超时后，执行强制关机。

### 3.3 导出 OVA 镜像

```bash
vboxmanage export "Win11VM" \
    --output images/Win11VM-marvin.ova \
    --ovf20
```

导出结果：
- 文件路径：`images/Win11VM-marvin.ova`
- 文件大小：**11.85 GB**
- 格式：OVF 2.0

### 3.4 上传 OVA 到 MinIO

使用 `scripts/upload_ova_to_minio.py`，通过 MinIO Python SDK 分片上传（64MB/片）：

```bash
micromamba run -n marvin-vbox python3 scripts/upload_ova_to_minio.py \
    --file images/Win11VM-marvin.ova
```

上传结果：
- **MinIO 地址**: `172.19.80.100:9090`
- **Bucket**: `aidd-files`
- **Object 路径**: `marvin-vbox/Win11VM-marvin.ova`
- **ETag**: `8f1e021f32ed6ff259c9590de4601d84-190`

### 3.5 重新启动 VM

```bash
vboxmanage startvm "Win11VM" --type headless
```

导出完成后恢复 VM 运行。

---

## 四、部署流程（后续使用参考）

### 全新机器部署

```bash
# 1. 从 MinIO 下载 OVA（需要 mc 客户端）
mc cp myminio/aidd-files/marvin-vbox/Win11VM-marvin.ova ./images/

# 2. 一键部署
./deploy.sh --ova ./images/Win11VM-marvin.ova
```

### 日常启停

```bash
docker compose up -d         # 启动
docker compose down          # 停止
docker compose logs -f       # 查看日志
./scripts/vm-manager.sh status   # 查看 VM 状态
```

### 重新导出最新 OVA 并上传

```bash
# 导出
./scripts/export-ova.sh

# 上传
micromamba run -n marvin-vbox python3 scripts/upload_ova_to_minio.py \
    --file images/Win11VM-marvin.ova
```

---

## 五、整体架构

```
┌─────────────────────────────────────────────────────────┐
│  Linux 宿主机                                            │
│                                                          │
│  ┌──────────────────────────────────────────────┐        │
│  │  Docker Container (marvin-cxcalc-api)         │        │
│  │                                              │        │
│  │  FastAPI (Uvicorn :8111)                     │        │
│  │  ├─ GET  /api/v1/cxcalc/health              │        │
│  │  └─ POST /api/v1/cxcalc/calculate           │        │
│  │                                              │        │
│  │  [挂载] /usr/bin/vboxmanage (宿主机)         │        │
│  │  [挂载] ~/.config/VirtualBox  (宿主机)       │        │
│  │  [挂载] /home/data/marvin_vbox_sharad ←────┐ │        │
│  └────────────────────────────────────────────┼─┘        │
│                                               │          │
│  ┌────────────────────────────────────────────┼──┐       │
│  │  VirtualBox                                │  │       │
│  │  Win11VM (headless)                        │  │       │
│  │  ├─ Java 8 (32-bit)                       │  │       │
│  │  ├─ MarvinBeans / cxcalc.bat              │  │       │
│  │  └─ Y:\ ←── 共享文件夹 ───────────────────┘  │       │
│  └───────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────┘

                         ↕ OVA 镜像 (11.85 GB)

┌──────────────────────────────────────┐
│  MinIO 对象存储 (172.19.80.100:9090)  │
│  aidd-files/marvin-vbox/             │
│  └─ Win11VM-marvin.ova               │
└──────────────────────────────────────┘
```
