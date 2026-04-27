"""Scheduled background jobs.

The big one: generate next week's plan every Sunday evening so it's ready
for Monday. Uses Claude API explicitly because plan quality matters and
this runs once a week — cost is negligible.
"""

from datetime import date, datetime, timedelta

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
from sqlmodel import select

from app.agent.agent import AgentDeps, build_agent
from app.agent.router import TaskClass
from app.config import get_settings
from app.db.models import UserProfile
from app.db.session import AsyncSessionLocal

log = structlog.get_logger()
_scheduler: AsyncIOScheduler | None = None


async def generate_next_week_plan() -> None:
    """Generate workout + meal plan for the upcoming week."""
    next_monday = date.today() + timedelta(days=(7 - date.today().weekday()))

    log.info("Generating weekly plan", week_start=next_monday.isoformat())

    prompt = f"""Generate a workout plan for the week starting {next_monday.isoformat()}.

Steps:
1. Call get_user_profile to load goals, equipment, dietary preferences.
2. Call get_recent_workouts(days=21) to see recent training and apply progressive overload.
3. Call get_recent_health_metrics for weight and sleep trends if available.
4. For each training day in the week, call create_workout_session with appropriate
   exercises, intensity, and duration.
5. Schedule rest days appropriately based on the user's typical rest days
   (defaults: Wednesday and Saturday unless profile notes say otherwise).
6. End with a 2-3 sentence summary of the week's focus and what changed vs last week.
"""

    agent = build_agent(task=TaskClass.PLAN_GENERATION)
    settings = get_settings()
    deps = AgentDeps(session_factory=AsyncSessionLocal, user_id=settings.scheduler_user_id)

    try:
        result = await agent.run(prompt, deps=deps)
        log.info("Weekly plan generated", summary=result.output[:200])
    except Exception as e:
        log.exception("Weekly plan generation failed", error=str(e))


async def purge_old_chat_messages() -> None:
    """Honour each user's ``chat_retention_days`` setting.

    Users with a positive integer retention have their chat history rows
    older than ``today − N days`` hard-deleted. Users with null retention
    keep everything. Runs daily at 03:15 local; idempotent — safe to retry.
    """
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(UserProfile).where(UserProfile.chat_retention_days.is_not(None))
            )
        ).scalars().all()

        total_deleted = 0
        for profile in rows:
            days = profile.chat_retention_days
            if not days or days <= 0:
                continue
            cutoff = datetime.utcnow() - timedelta(days=days)
            res = await session.execute(
                text(
                    "DELETE FROM agentmessage "
                    "WHERE user_id = :u AND created_at < :c"
                ),
                {"u": str(profile.user_id), "c": cutoff},
            )
            deleted = res.rowcount or 0
            total_deleted += deleted
            if deleted:
                log.info(
                    "Retention purge",
                    user_id=str(profile.user_id),
                    days=days,
                    deleted=deleted,
                )
        await session.commit()
        if total_deleted:
            log.info("Retention purge complete", total_deleted=total_deleted)


def start_scheduler() -> None:
    global _scheduler
    settings = get_settings()
    _scheduler = AsyncIOScheduler(timezone=settings.timezone)
    # Sunday 19:00 — gives you the evening to glance at the plan before Monday
    _scheduler.add_job(
        generate_next_week_plan,
        CronTrigger(day_of_week="sun", hour=19, minute=0),
        id="weekly_plan",
        replace_existing=True,
    )
    # Daily chat-retention purge at 03:15 local.
    _scheduler.add_job(
        purge_old_chat_messages,
        CronTrigger(hour=3, minute=15),
        id="chat_retention_purge",
        replace_existing=True,
    )
    _scheduler.start()
    log.info("Scheduler started")


def stop_scheduler() -> None:
    if _scheduler:
        _scheduler.shutdown()
        log.info("Scheduler stopped")
