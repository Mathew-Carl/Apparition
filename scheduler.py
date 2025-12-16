"""
定时任务模块
使用 APScheduler 实现定时打卡

功能：
1. 从数据库读取打卡时间配置
2. 支持动态添加/删除/修改定时任务
3. 配置修改后自动刷新任务
"""

import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from checkin import do_checkin_all

logger = logging.getLogger(__name__)

# 全局调度器实例
scheduler = AsyncIOScheduler()


async def setup_scheduler_from_db():
    """
    从数据库读取配置并设置定时任务

    这个函数会：
    1. 清除所有现有的打卡任务
    2. 从数据库读取启用的时间配置
    3. 为每个配置创建定时任务
    """
    from database import db

    logger.info("从数据库加载打卡时间配置...")

    # 清除所有以 'checkin_' 开头的任务
    for job in scheduler.get_jobs():
        if job.id.startswith('checkin_'):
            scheduler.remove_job(job.id)
            logger.debug(f"移除任务: {job.id}")

    # 从数据库读取启用的配置
    schedules = await db.get_enabled_schedules()

    for schedule in schedules:
        job_id = f"checkin_{schedule.id}"

        # 创建 Cron 触发器
        trigger = CronTrigger(hour=schedule.hour, minute=schedule.minute)

        # 添加任务
        scheduler.add_job(
            do_checkin_all,
            trigger,
            id=job_id,
            name=schedule.name,
            replace_existing=True
        )

        logger.info(f"添加定时任务: {schedule.name} ({schedule.hour:02d}:{schedule.minute:02d})")

    logger.info(f"共加载 {len(schedules)} 个打卡时间配置")


def setup_scheduler():
    """
    初始化调度器（同步版本，用于启动时调用）
    """
    # 在事件循环中运行异步设置
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # 如果事件循环已经在运行，创建任务
        asyncio.create_task(setup_scheduler_from_db())
    else:
        # 否则直接运行
        loop.run_until_complete(setup_scheduler_from_db())


def start_scheduler():
    """启动调度器"""
    if not scheduler.running:
        scheduler.start()
        logger.info("定时任务调度器已启动")

        # 加载配置（在调度器启动后）
        asyncio.create_task(setup_scheduler_from_db())


def stop_scheduler():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("定时任务调度器已停止")


async def refresh_scheduler():
    """
    刷新调度器配置

    当用户修改打卡时间后调用此函数，
    会重新从数据库读取配置并更新任务
    """
    logger.info("刷新定时任务配置...")
    await setup_scheduler_from_db()

    # 打印当前任务列表
    jobs = get_jobs()
    for job in jobs:
        logger.info(f"  - {job['name']}: {job['trigger']}")


def get_jobs():
    """
    获取所有任务列表

    Returns:
        任务信息列表
    """
    return [
        {
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run": str(job.next_run_time) if job.next_run_time else None
        }
        for job in scheduler.get_jobs()
    ]


def get_scheduler_status():
    """
    获取调度器状态

    Returns:
        {
            "running": True/False,
            "job_count": 2,
            "jobs": [...]
        }
    """
    return {
        "running": scheduler.running,
        "job_count": len(scheduler.get_jobs()),
        "jobs": get_jobs()
    }
