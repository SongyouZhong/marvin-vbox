import asyncio
import logging

import uvicorn

from app.config import settings

logger = logging.getLogger(__name__)


async def main():
    """并发运行 FastAPI 服务 + CxCalcWorker"""

    # FastAPI server
    config = uvicorn.Config(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)

    # CxCalcWorker（降级安全：Redis 不可达时仅运行 REST）
    async def run_worker():
        try:
            from app.worker.cxcalc_worker import CxCalcWorker
            worker = CxCalcWorker()
            await worker.start()
        except Exception as e:
            logger.error(f"CxCalcWorker 启动失败，降级为仅 REST 模式: {e}")

    await asyncio.gather(server.serve(), run_worker())


if __name__ == "__main__":
    asyncio.run(main())
