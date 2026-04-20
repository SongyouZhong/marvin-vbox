import hashlib
import logging
import os
import platform as _platform
from typing import Dict, Any

logger = logging.getLogger(__name__)


def _generate_node_id(hostname: str = None) -> str:
    """
    生成节点唯一标识：hostname-mac_hash[:8]
    与 aidd-toolkit/node_agent/config.py 的 generate_node_id() 保持一致，
    保证同一物理机重启后 node_id 不变。
    """
    from uuid import getnode as get_mac
    hostname = hostname or _platform.node()
    mac = get_mac()
    mac_hex = f"{mac:012x}"
    hash_suffix = hashlib.sha256(mac_hex.encode()).hexdigest()[:8]
    return f"{hostname}-{hash_suffix}"


class Settings:
    # Server
    server_host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    server_port: int = int(os.getenv("SERVER_PORT", "8111"))
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    # VirtualBox VM
    vm_name: str = os.getenv("VM_NAME", "Win11VM")
    vm_username: str = os.getenv("VM_USERNAME", "marvin-box")
    vm_password: str = os.getenv("VM_PASSWORD", "123123")

    # Paths
    shared_folder_host: str = os.getenv("SHARED_FOLDER_HOST", "/home/data/marvin_vbox_sharad")
    # VM drive letter where /home/data/marvin_vbox_sharad is automounted (VBox shared folder name: "shared")
    shared_folder_vm: str = os.getenv("SHARED_FOLDER_VM", "Y:\\")
    # Use 8.3 short path to avoid quoting issues in cmd.exe; PowerShell supports full path too
    cxcalc_path: str = os.getenv(
        "CXCALC_PATH",
        r"C:\Progra~2\ChemAxon\MarvinBeans\bin\cxcalc.bat",
    )

    # Timeouts (seconds)
    command_timeout: int = int(os.getenv("COMMAND_TIMEOUT", "600"))

    # --- Platform 集成（仅需 PLATFORM_URL，其余由注册时下发） ---
    platform_url: str = os.getenv("PLATFORM_URL", "")
    worker_hostname: str = os.getenv("WORKER_HOSTNAME", "marvin-vbox-001")

    # 节点标识（自动生成，与 Node Agent 一致）
    node_id: str = _generate_node_id(os.getenv("WORKER_HOSTNAME", "marvin-vbox-001"))

    # 注册后由 Platform 下发的运行时配置（内存中，不落盘）
    redis: Dict[str, Any] = {}
    heartbeat_interval: int = 30
    worker_env: Dict[str, str] = {}

    # REDIS_URL 仅作为注册失败时的 fallback
    _redis_url_fallback: str = os.getenv("REDIS_URL", "")

    @property
    def redis_url(self) -> str:
        """从 Platform 下发的 redis 配置构建 URL；fallback 到 REDIS_URL 环境变量"""
        if self.redis:
            host = self.redis.get("host", "localhost")
            port = self.redis.get("port", 6379)
            password = self.redis.get("password", "")
            db = self.redis.get("db", 0)
            if password:
                return f"redis://:{password}@{host}:{port}/{db}"
            return f"redis://{host}:{port}/{db}"
        if self._redis_url_fallback:
            return self._redis_url_fallback
        raise ValueError("Redis 未配置：节点注册未完成且 REDIS_URL 环境变量未设置")

    def apply_register_response(self, resp: dict):
        """
        将节点注册响应写入内存。
        与 aidd-toolkit/node_agent/config.py 的 apply_register_response() 对齐。
        响应来自 POST /api/v1/nodes/register。
        """
        self.redis = resp.get("redis", {})
        self.heartbeat_interval = resp.get("heartbeat_interval", 30)
        self.worker_env = resp.get("worker_env", {})
        r = self.redis
        logger.info(
            "Platform 下发配置: Redis=%s:%s/%s, heartbeat=%ds",
            r.get("host"), r.get("port"), r.get("db"), self.heartbeat_interval,
        )


settings = Settings()
