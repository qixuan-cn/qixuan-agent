"""
启煊智能体 - 定时任务调度器
功能：解析 config.yaml 中的 cron 任务配置，按计划触发技能执行

当前配置的定时任务（来自 config.yaml）：
  - daily_ops_inspection：工作日 08:00，触发 qixuan-ops-sentinel 执行早间巡检

用法：python scripts/cron_runner.py [--config /app/config/config.yaml]
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

# 将项目根加入路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from croniter import croniter
except ImportError:
    print("请安装 croniter：pip install croniter")
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("请安装 pyyaml：pip install pyyaml")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("qixuan.cron")


def load_cron_config(config_path: str) -> list[dict]:
    """从 config.yaml 加载定时任务配置。"""
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cron_cfg = cfg.get("cron", {})
    if not cron_cfg.get("enabled", False):
        return []
    return cron_cfg.get("jobs", [])


async def execute_cron_job(job: dict):
    """
    执行单个定时任务。
    生产实现：通过内部 ACP 或消息队列触发对应技能。
    """
    job_name = job.get("name", "unknown")
    skill = job.get("skill", "")
    prompt = job.get("prompt", "")

    logger.info(f"⏰ 触发定时任务：{job_name} → 技能：{skill}")
    logger.info(f"   指令：{prompt[:100]}")

    # TODO: 替换为实际的 Agent 核心调用
    # event = {
    #     "type": "cron",
    #     "skill": skill,
    #     "prompt": prompt,
    #     "triggered_by": f"cron:{job_name}",
    #     "timestamp": datetime.now().isoformat(),
    # }
    # await agent_queue.put(event)

    # 占位输出（实际部署时移除）
    logger.info(f"   ✅ 任务 {job_name} 已入队（待 Agent 核心接管）")


async def run_scheduler(config_path: str):
    """主调度循环：每分钟检查一次是否有需要触发的 cron 任务。"""
    jobs = load_cron_config(config_path)
    if not jobs:
        logger.warning("没有已启用的定时任务，调度器退出。")
        return

    logger.info(f"🕐 定时调度器启动，共加载 {len(jobs)} 个任务：")
    for job in jobs:
        logger.info(f"   - {job['name']}: {job['schedule']} → {job['skill']}")

    while True:
        now = datetime.now()
        for job in jobs:
            schedule = job.get("schedule", "")
            if not schedule:
                continue
            try:
                cron = croniter(schedule, now)
                # 获取上一次应该触发的时间
                prev_trigger = cron.get_prev(datetime)
                # 如果上一次触发时间在过去 60 秒内，则执行
                delta = (now - prev_trigger).total_seconds()
                if 0 <= delta < 60:
                    await execute_cron_job(job)
            except Exception as e:
                logger.error(f"任务 {job.get('name')} cron 解析/执行失败：{e}")

        # 每 60 秒检查一次
        await asyncio.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="启煊智能体定时任务调度器")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config" / "config.yaml"),
        help="config.yaml 路径",
    )
    args = parser.parse_args()

    if not Path(args.config).exists():
        logger.error(f"配置文件不存在：{args.config}")
        sys.exit(1)

    logger.info(f"加载配置文件：{args.config}")
    asyncio.run(run_scheduler(args.config))


if __name__ == "__main__":
    main()
