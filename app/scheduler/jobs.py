"""Scheduled background jobs.

The big one: generate next week's plan every Sunday evening so it's ready
for Monday. Uses Claude API explicitly because plan quality matters and
this runs once a week — cost is negligible.
"""

from datetime import date, timedelta

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.agent.agent import AgentDeps, build_agent
from app.agent.router import TaskClass
from app.config import get_settings
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
    _scheduler.start()
    log.info("Scheduler started")


def stop_scheduler() -> None:
    if _scheduler:
        _scheduler.shutdown()
        log.info("Scheduler stopped")
