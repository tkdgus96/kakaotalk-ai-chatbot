from fastapi import FastAPI

from app.api import router
from app.boss.db import init_schema
from app.boss.services.scheduler import BossScheduler
from app.chat_log import init_chat_log_schema
from app.config import settings
from app.dependencies import boss_repo
from app.services.health_monitor import HealthMonitor
from app.services.iris_service import IrisClient, run_outbox_sender, seed_room_map_from_env


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    scheduler = BossScheduler(boss_repo)
    health_monitor = HealthMonitor(boss_repo)

    @app.on_event("startup")
    async def on_startup():
        init_schema()
        init_chat_log_schema()
        seed_room_map_from_env()
        import asyncio

        app.state.scheduler_task = asyncio.create_task(scheduler.run_forever())
        if settings.enable_iris_sender:
            app.state.iris_sender_task = asyncio.create_task(
                run_outbox_sender(boss_repo, IrisClient())
            )
        if settings.enable_health_monitor:
            app.state.health_task = asyncio.create_task(health_monitor.run_forever())

    @app.on_event("shutdown")
    async def on_shutdown():
        scheduler.stop()
        health_monitor.stop()
        for attr in ("scheduler_task", "iris_sender_task", "health_task"):
            task = getattr(app.state, attr, None)
            if task:
                task.cancel()

    return app
