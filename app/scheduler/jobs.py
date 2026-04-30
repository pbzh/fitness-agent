"""Scheduled background jobs.

The big one: generate next week's plan every Sunday evening so it's ready
for Monday. Uses Claude API explicitly because plan quality matters and
this runs once a week — cost is negligible.
"""

from datetime import date, datetime, timedelta

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic_ai import Agent
from sqlalchemy import text
from sqlmodel import select

from app.agent.agent import AgentDeps
from app.agent.effective_config import load_effective_config, resolve_api_key
from app.agent.prompts import resolve_prompt
from app.agent.router import Provider, TaskClass, _env_provider_for, build_model
from app.agent.tools import register_tools
from app.config import get_settings
from app.db.models import User, UserProfile, WorkoutSession
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

    settings = get_settings()
    week_end = next_monday + timedelta(days=7)
    async with AsyncSessionLocal() as session:
        users = (
            await session.execute(select(User).where(User.is_approved == True))  # noqa: E712
        ).scalars().all()

    for user in users:
        async with AsyncSessionLocal() as session:
            profile = (
                await session.execute(select(UserProfile).where(UserProfile.user_id == user.id))
            ).scalar_one_or_none()
        eff = await load_effective_config(user.id)
        if profile and profile.local_only:
            provider = Provider.LOCAL
        else:
            provider = eff.provider_for(
                TaskClass.PLAN_GENERATION.value,
                _env_provider_for(TaskClass.PLAN_GENERATION),
            )
            if provider == Provider.ANTHROPIC and not (
                eff.key_for(provider) or settings.anthropic_api_key
            ):
                provider = Provider.LOCAL
            if provider == Provider.OPENAI and not (
                eff.key_for(provider) or settings.openai_api_key
            ):
                provider = Provider.LOCAL

        agent: Agent[AgentDeps, str] = Agent(
            model=build_model(provider, api_key=resolve_api_key(provider, eff)),
            deps_type=AgentDeps,
            system_prompt=resolve_prompt(
                TaskClass.PLAN_GENERATION,
                profile.coach_prompts if profile else None,
            ),
        )
        register_tools(agent)
        deps = AgentDeps(session_factory=AsyncSessionLocal, user_id=user.id)

        try:
            async with AsyncSessionLocal() as session:
                stale = (
                    await session.execute(
                        select(WorkoutSession)
                        .where(WorkoutSession.user_id == user.id)
                        .where(WorkoutSession.scheduled_date >= next_monday)
                        .where(WorkoutSession.scheduled_date < week_end)
                        .where(WorkoutSession.completed == False)  # noqa: E712
                    )
                ).scalars().all()
                for session_obj in stale:
                    await session.delete(session_obj)
                await session.commit()

            result = await agent.run(prompt, deps=deps)
            log.info(
                "Weekly plan generated",
                user_id=str(user.id),
                provider=provider.value,
                summary=result.output[:200],
            )
        except Exception as e:
            log.exception("Weekly plan generation failed", user_id=str(user.id), error=str(e))


async def purge_old_chat_messages() -> None:
    """Honour each user's ``chat_retention_days`` setting.

    Users with a positive integer retention have their chat history rows
    older than ``today - N days`` hard-deleted. Users with null retention
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
