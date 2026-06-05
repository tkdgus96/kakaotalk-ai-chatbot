from fastapi import FastAPI

from app.api import router
from app.boss.db import init_schema
from app.boss.services.scheduler import BossScheduler
from app.chat_log import init_chat_log_schema
from app.dependencies import boss_repo


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    scheduler = BossScheduler(boss_repo)

    @app.on_event("startup")
    async def on_startup():
        init_schema()
        init_chat_log_schema()
        import asyncio

        app.state.scheduler_task = asyncio.create_task(scheduler.run_forever())

    @app.on_event("shutdown")
    async def on_shutdown():
        scheduler.stop()
        task = getattr(app.state, "scheduler_task", None)
        if task:
            task.cancel()

    return app
