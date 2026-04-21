"""
CxCalc Worker

从 Redis 队列消费 cxcalc 任务，生成 SDF，调用 VM 计算，
将 merged TSV 结果通过 Redis 回传给 aidd-platform。

启动流程（与 aidd-toolkit Node Agent 对齐）：
1. POST /api/v1/nodes/register → 获取 Redis 等配置
2. 连接 Redis，启动 HSET 心跳
3. BRPOP 消费 cxcalc 队列
"""

import asyncio
import json
import logging
import os
import platform as _platform
import socket
import uuid
from datetime import datetime, timezone

import httpx
import redis.asyncio as redis
from rdkit import Chem
from rdkit.Chem import AllChem

from app.config import settings
from app.services.vbox_service import run_cxcalc_on_vm, get_shared_folder_path, start_vm, VBoxError
from app.api.cxcalc import CALC_ARGS, CalcType, _merge_csv_contents

logger = logging.getLogger(__name__)


def _get_local_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


class CxCalcWorker:
    """
    CxCalc Worker — 主动从 Redis 队列取任务并执行

    生命周期（与 Node Agent 对齐）：
    1. 向 Platform 注册为节点（POST /api/v1/nodes/register），获取 Redis 配置
    2. 连接 Redis，启动 HSET 心跳（与 Node Agent 相同机制）
    3. 循环从 aidd:queue:service:cxcalc BRPOP 取任务
    4. 读取 aidd:tasks:{task_id} hash 获取任务详情
    5. 生成 SDF → 调用 VM cxcalc → 合并 TSV
    6. 将结果写入 task hash，推入 completed 队列
    7. 失败时写入错误信息，推入 failed 队列
    """

    SERVICE_QUEUE = "aidd:queue:service:cxcalc"
    TASK_KEY_PREFIX = "aidd:tasks:"
    RUNNING_SET = "aidd:queue:running"
    COMPLETED_QUEUE = "aidd:queue:completed"
    FAILED_QUEUE = "aidd:queue:failed"

    def __init__(self):
        self._redis: redis.Redis | None = None
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None
        self._current_task_id: str | None = None

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    # =========================================================================
    # 注册 & 心跳（与 Node Agent 对齐）
    # =========================================================================

    async def _register_node(self):
        """
        向 Platform 注册为节点（与 Node Agent 使用同一 API）。
        成功后 settings.redis / heartbeat_interval / worker_env 被填充。
        """
        url = f"{settings.platform_url.rstrip('/')}/api/v1/nodes/register"
        payload = {
            "node_id": settings.node_id,
            "hostname": settings.worker_hostname,
            "ip_address": _get_local_ip(),
            "capabilities": ["cxcalc"],
            "os_info": f"{_platform.system()} {_platform.release()} ({_platform.machine()})",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            settings.apply_register_response(resp.json())

    async def _heartbeat_loop(self):
        """Redis HSET 心跳（与 Node Agent 的 _heartbeat_loop 对齐）"""
        key = f"aidd:nodes:{settings.node_id}:info"
        while self._running:
            try:
                r = await self._get_redis()
                await r.hset(key, mapping={
                    "node_id": settings.node_id,
                    "hostname": settings.worker_hostname,
                    "status": "online",
                    "capabilities": json.dumps(["cxcalc"]),
                    "active_workers": json.dumps(
                        {"cxcalc": "running"} if self._current_task_id else {}
                    ),
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                })
                await r.expire(key, settings.heartbeat_interval * 3)
            except Exception as e:
                logger.warning(f"心跳上报失败: {e}")
            await asyncio.sleep(settings.heartbeat_interval)

    # =========================================================================
    # 生命周期
    # =========================================================================

    async def start(self) -> None:
        """启动 Worker：注册节点 → 连接 Redis → 心跳 → 消费循环"""
        logger.info("CxCalcWorker 启动中 (node_id=%s)...", settings.node_id)

        if not settings.platform_url:
            logger.error(
                "PLATFORM_URL 未配置，无法注册节点。"
                "请在部署时通过 -s/--server 参数指定 Platform 地址，例如: "
                "./deploy.sh -s 10.18.85.10:8333"
            )
            return

        # 1. 向 Platform 注册节点，获取 Redis 等配置
        registered = False
        for attempt in range(1, 4):
            try:
                await self._register_node()
                registered = True
                logger.info("节点注册成功, Redis: %s", settings.redis_url)
                break
            except Exception as e:
                logger.warning("节点注册失败 (尝试 %d/3): %s", attempt, e)
                if attempt < 3:
                    await asyncio.sleep(5)

        if not registered:
            if settings._redis_url_fallback:
                logger.warning(
                    "节点注册失败，使用 REDIS_URL 环境变量 fallback: %s",
                    settings._redis_url_fallback,
                )
            else:
                logger.error("节点注册失败且无 REDIS_URL fallback，无法启动")
                return

        # 2. 启动 Redis HSET 心跳
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("心跳已启动 (interval=%ds)", settings.heartbeat_interval)

        # 3. 消费循环
        try:
            await self._consume_loop()
        finally:
            self._running = False
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
            if self._redis:
                await self._redis.aclose()
                self._redis = None
            logger.info("CxCalcWorker 已停止")

    async def stop(self) -> None:
        """优雅停机：等待当前任务完成"""
        logger.info("CxCalcWorker 收到停机信号")
        self._running = False

    async def _consume_loop(self) -> None:
        """主消费循环"""
        r = await self._get_redis()

        while self._running:
            try:
                # BRPOP 阻塞等待，timeout=5s 以便检查 _running 标志
                result = await r.brpop(self.SERVICE_QUEUE, timeout=5)
                if result is None:
                    continue

                _, task_id = result
                await self._process_task(task_id)

            except asyncio.CancelledError:
                logger.info("CxCalcWorker 消费循环被取消")
                break
            except Exception as e:
                logger.error(f"CxCalcWorker 消费循环异常: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _process_task(self, task_id: str) -> None:
        """处理单个任务"""
        r = await self._get_redis()
        task_key = f"{self.TASK_KEY_PREFIX}{task_id}"

        # 读取任务详情
        task_data = await r.hgetall(task_key)
        if not task_data:
            logger.warning(f"任务 {task_id} 的 hash 不存在，跳过")
            return

        service = task_data.get("service", "")
        if service != "cxcalc":
            logger.warning(f"任务 {task_id} 的 service={service}，非 cxcalc，跳过")
            return

        logger.info(f"开始处理 cxcalc 任务: {task_id}")
        self._current_task_id = task_id

        # 更新状态为 running
        now_str = datetime.now(timezone.utc).isoformat()
        await r.hset(task_key, mapping={
            "status": "running",
            "worker_id": settings.node_id,
            "started_at": now_str,
        })
        await r.sadd(self.RUNNING_SET, task_id)
        # 从 pending 队列移除
        await r.zrem("aidd:queue:pending", task_id)

        # 记录当前任务（用于心跳上报）
        self._current_task_id = task_id

        try:
            input_params = json.loads(task_data.get("input_params", "{}"))
            smiles_list = input_params.get("smiles", [])

            if not smiles_list:
                raise ValueError("任务 input_params 中无 smiles 列表")

            # 生成 SDF
            sdf_content = self._build_sdf(smiles_list)
            task_short_id = task_id[:12]
            sdf_filename = f"cxcalc_{task_short_id}.sdf"
            sdf_host_path = get_shared_folder_path(sdf_filename)

            os.makedirs(settings.shared_folder_host, exist_ok=True)
            with open(sdf_host_path, "w", encoding="utf-8") as f:
                f.write(sdf_content)

            # 确保 VM 运行
            try:
                await start_vm()
            except VBoxError as e:
                raise RuntimeError(f"VM 启动失败: {e}")

            # 执行所有计算类型
            results: dict[str, str] = {}
            errors: dict[str, str] = {}

            calc_types = [CalcType.molecular_properties, CalcType.logs, CalcType.logd]
            for calc_type in calc_types:
                output_filename = f"cxcalc_{task_short_id}_{calc_type.value}.csv"
                try:
                    csv_content = await run_cxcalc_on_vm(
                        sdf_filename=sdf_filename,
                        output_filename=output_filename,
                        calc_args=CALC_ARGS[calc_type],
                    )
                    results[calc_type.value] = csv_content
                except VBoxError as e:
                    logger.error(f"计算 {calc_type.value} 失败: {e}")
                    errors[calc_type.value] = str(e)
                finally:
                    out_path = get_shared_folder_path(output_filename)
                    if os.path.exists(out_path):
                        os.remove(out_path)

            # 清理输入 SDF
            if os.path.exists(sdf_host_path):
                os.remove(sdf_host_path)

            if not results:
                raise RuntimeError(
                    f"所有计算均失败: {json.dumps(errors, ensure_ascii=False)}"
                )

            # 合并结果
            merged_tsv = _merge_csv_contents(results)

            # 写入成功结果
            completed_at = datetime.now(timezone.utc).isoformat()
            result_data = {
                "merged_tsv": merged_tsv,
                "calc_types": list(results.keys()),
                "errors": errors if errors else None,
                "compound_count": len(smiles_list),
            }
            await r.hset(task_key, mapping={
                "status": "success",
                "result": json.dumps(result_data, ensure_ascii=False),
                "completed_at": completed_at,
                "error_message": "",
            })
            await r.srem(self.RUNNING_SET, task_id)
            await r.lpush(self.COMPLETED_QUEUE, task_id)

            logger.info(
                f"cxcalc 任务 {task_id} 完成: "
                f"{len(results)} 类计算成功, {len(errors)} 类失败, "
                f"{len(smiles_list)} 个化合物"
            )

        except Exception as e:
            logger.error(f"cxcalc 任务 {task_id} 执行失败: {e}", exc_info=True)

            error_msg = str(e)
            completed_at = datetime.now(timezone.utc).isoformat()
            await r.hset(task_key, mapping={
                "status": "failed",
                "error_message": error_msg,
                "completed_at": completed_at,
            })
            await r.srem(self.RUNNING_SET, task_id)
            await r.lpush(self.FAILED_QUEUE, task_id)

        finally:
            self._current_task_id = None

    @staticmethod
    def _build_sdf(smiles_list: list[str]) -> str:
        """
        用 RDKit 将 SMILES 列表转为标准 SDF。
        分子名称行（SDF 第一行）设为 SMILES 字符串，
        供 cxcalc -i Name 读取，也用于结果回溯匹配。
        无法解析的 SMILES 会被跳过并记录警告。
        """
        import io
        buf = io.StringIO()
        writer = Chem.SDWriter(buf)
        for smiles in smiles_list:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                logger.warning("无法解析 SMILES，将跳过: %s", smiles)
                continue
            mol.SetProp("_Name", smiles)  # SDF 首行 = SMILES，供 cxcalc -i Name 使用
            AllChem.Compute2DCoords(mol)
            writer.write(mol)
        writer.close()
        return buf.getvalue()
