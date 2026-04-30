"""Tools the agent can invoke.

Each tool opens its OWN short-lived session via deps.session_factory(),
so concurrent tool calls don't collide on a shared session.
"""

from datetime import date, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from sqlmodel import select

from app.agent.agent import AgentDeps
from app.agent.document_gen import generate_document, sanitize_filename_stem
from app.db.models import (
    File,
    FileKind,
    HealthMetric,
    IntensityLevel,
    MealLog,
    MealSlot,
    UserProfile,
    WorkoutLocation,
    WorkoutPlan,
    WorkoutSession,
    WorkoutType,
)


class WorkoutSummary(BaseModel):
    date: date
    type: str
    intensity: str
    duration_min: int
    completed: bool
    rpe: int | None = None
    notes: str | None = None


class MealSummary(BaseModel):
    eaten_at: datetime
    slot: str
    name: str
    calories: int | None
    protein_g: float | None


class HealthMetricSummary(BaseModel):
    recorded_at: datetime
    metric_type: str
    value: float


class ProfileSummary(BaseModel):
    height_cm: float | None
    weight_kg: float | None
    primary_goal: str | None
    weekly_workout_target: int
    equipment: list[str]
    dietary_restrictions: list[str]
    daily_calorie_target: int | None
    macro_targets: dict | None
    notes: str | None


class DocumentExportResult(BaseModel):
    file_id: str
    url: str
    filename: str
    mime_type: str


def register_tools(agent: Agent[AgentDeps, str]) -> None:
    @agent.tool
    async def get_user_profile(ctx: RunContext[AgentDeps]) -> ProfileSummary:
        """Get the user's profile: goals, equipment, dietary preferences, targets."""
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(UserProfile).where(UserProfile.user_id == ctx.deps.user_id)
            )
            profile = result.scalar_one_or_none()
            if not profile:
                return ProfileSummary(
                    height_cm=None,
                    weight_kg=None,
                    primary_goal=None,
                    weekly_workout_target=4,
                    equipment=[],
                    dietary_restrictions=[],
                    daily_calorie_target=None,
                    macro_targets=None,
                    notes="No profile yet. Ask the user about goals and equipment.",
                )
            return ProfileSummary(
                height_cm=profile.height_cm,
                weight_kg=profile.weight_kg,
                primary_goal=profile.primary_goal,
                weekly_workout_target=profile.weekly_workout_target,
                equipment=profile.equipment,
                dietary_restrictions=profile.dietary_restrictions,
                daily_calorie_target=profile.daily_calorie_target,
                macro_targets=profile.macro_targets,
                notes=profile.notes,
            )

    @agent.tool
    async def get_recent_workouts(
        ctx: RunContext[AgentDeps],
        days: int = Field(default=14, description="Number of days to look back"),
    ) -> list[WorkoutSummary]:
        """Get workouts (planned + completed) from the last N days. Always call
        this before generating a new plan to inform progressive overload."""
        cutoff = date.today() - timedelta(days=days)
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.user_id == ctx.deps.user_id)
                .where(WorkoutSession.scheduled_date >= cutoff)
                .order_by(WorkoutSession.scheduled_date.desc())
            )
            sessions = result.scalars().all()
            return [
                WorkoutSummary(
                    date=s.scheduled_date,
                    type=s.workout_type.value,
                    intensity=s.intensity.value,
                    duration_min=s.duration_min,
                    completed=s.completed,
                    rpe=s.perceived_exertion,
                    notes=s.completion_notes or s.notes,
                )
                for s in sessions
            ]

    @agent.tool
    async def log_completed_workout(
        ctx: RunContext[AgentDeps],
        scheduled_date: date,
        rpe: int = Field(ge=1, le=10, description="Rate of Perceived Exertion 1-10"),
        notes: str | None = None,
    ) -> str:
        """Mark a planned workout as completed. Use when the user reports finishing one."""
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.user_id == ctx.deps.user_id)
                .where(WorkoutSession.scheduled_date == scheduled_date)
                .where(WorkoutSession.completed == False)  # noqa: E712
            )
            session_obj = result.scalar_one_or_none()
            if not session_obj:
                return f"No planned workout found for {scheduled_date}. Create one first."

            session_obj.completed = True
            session_obj.completed_at = datetime.utcnow()
            session_obj.perceived_exertion = rpe
            session_obj.completion_notes = notes
            await session.commit()
            return f"Logged: {session_obj.workout_type.value} on {scheduled_date}, RPE {rpe}."

    @agent.tool
    async def create_workout_session(
        ctx: RunContext[AgentDeps],
        scheduled_date: date,
        workout_type: WorkoutType,
        intensity: IntensityLevel,
        duration_min: int,
        exercises: list[dict],
        scheduled_time: str | None = Field(
            default=None,
            description="Optional HH:MM 24h time the user plans to train",
        ),
        target_rpe: int | None = Field(default=None, ge=1, le=10),
        location: WorkoutLocation | None = None,
        warmup: str | None = None,
        cooldown: str | None = None,
        notes: str | None = None,
        plan_id: UUID | None = None,
    ) -> str:
        """Create a planned workout session. Use when generating weekly plans
        or when the user requests an ad-hoc workout."""
        from datetime import time as _time

        sched_time = None
        if scheduled_time:
            hh, mm = scheduled_time.split(":")[:2]
            sched_time = _time(int(hh), int(mm))

        async with ctx.deps.session_factory() as session:
            if plan_id is not None:
                plan = (
                    await session.execute(
                        select(WorkoutPlan)
                        .where(WorkoutPlan.id == plan_id)
                        .where(WorkoutPlan.user_id == ctx.deps.user_id)
                    )
                ).scalar_one_or_none()
                if plan is None:
                    return "Cannot schedule workout: plan_id was not found for this user."

            ws = WorkoutSession(
                user_id=ctx.deps.user_id,
                plan_id=plan_id,
                scheduled_date=scheduled_date,
                scheduled_time=sched_time,
                workout_type=workout_type,
                intensity=intensity,
                duration_min=duration_min,
                target_rpe=target_rpe,
                location=location,
                exercises=exercises,
                warmup=warmup,
                cooldown=cooldown,
                notes=notes,
            )
            session.add(ws)
            await session.commit()
            when = f"{scheduled_date} {scheduled_time}" if scheduled_time else str(scheduled_date)
            return f"Scheduled {workout_type.value} for {when} ({duration_min} min)."

    @agent.tool
    async def get_recent_meals(
        ctx: RunContext[AgentDeps],
        days: int = 7,
    ) -> list[MealSummary]:
        """Get logged meals from the last N days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(MealLog)
                .where(MealLog.user_id == ctx.deps.user_id)
                .where(MealLog.eaten_at >= cutoff)
                .order_by(MealLog.eaten_at.desc())
            )
            meals = result.scalars().all()
            return [
                MealSummary(
                    eaten_at=m.eaten_at,
                    slot=m.slot.value,
                    name=m.name,
                    calories=m.calories,
                    protein_g=m.protein_g,
                )
                for m in meals
            ]

    @agent.tool
    async def log_meal(
        ctx: RunContext[AgentDeps],
        slot: MealSlot,
        name: str,
        calories: int | None = None,
        protein_g: float | None = None,
        carbs_g: float | None = None,
        fat_g: float | None = None,
        eaten_at: datetime | None = None,
    ) -> str:
        """Log a meal the user has eaten. Estimate macros if not specified."""
        async with ctx.deps.session_factory() as session:
            meal = MealLog(
                user_id=ctx.deps.user_id,
                eaten_at=eaten_at or datetime.utcnow(),
                slot=slot,
                name=name,
                calories=calories,
                protein_g=protein_g,
                carbs_g=carbs_g,
                fat_g=fat_g,
            )
            session.add(meal)
            await session.commit()
            macros = ""
            if protein_g:
                macros = f" ({calories} kcal, {protein_g:.0f}g P)"
            return f"Logged {slot.value}: {name}{macros}"

    @agent.tool
    async def generate_document_export(
        ctx: RunContext[AgentDeps],
        file_format: str = Field(description="One of: pdf, docx, xlsx, pptx"),
        title: str = Field(description="Human-readable document title"),
        content: str = Field(
            description=(
                "Plain text body for the export. Use short paragraphs or one "
                "line per bullet or section."
            )
        ),
        table_rows: list[list[str]] | None = None,
        kind: str = Field(
            default="document_export",
            description="Short label such as workout_plan, report, checklist, presentation",
        ),
        filename_stem: str | None = Field(
            default=None,
            description="Optional filename stem without extension. If omitted, derived from title.",
        ),
    ) -> DocumentExportResult:
        """Create a downloadable PDF, Word, Excel, or PowerPoint file.

        Use this when the user explicitly wants a file deliverable instead of
        only a chat reply."""
        normalized = file_format.lower().strip()
        if normalized not in {"pdf", "docx", "xlsx", "pptx"}:
            raise ValueError("file_format must be one of: pdf, docx, xlsx, pptx")

        document = generate_document(
            file_format=normalized,
            title=title,
            content=content,
            table_rows=table_rows,
        )
        filename = f"{sanitize_filename_stem(filename_stem or title)}.{document.extension}"

        from app.files import storage

        fid, rel = storage.write_bytes(
            document.data,
            filename=filename,
            mime_type=document.mime_type,
        )
        async with ctx.deps.session_factory() as session:
            f = File(
                id=fid,
                user_id=ctx.deps.user_id,
                kind=FileKind.GENERATED,
                filename=filename,
                mime_type=document.mime_type,
                size_bytes=len(document.data),
                storage_path=rel,
                description=kind,
                prompt=content[:4000],
            )
            session.add(f)
            await session.commit()
        return DocumentExportResult(
            file_id=str(fid),
            url=f"/files/{fid}",
            filename=filename,
            mime_type=document.mime_type,
        )

    @agent.tool
    async def generate_plan_image(
        ctx: RunContext[AgentDeps],
        prompt: str = Field(
            description=(
                "Visual description of the image to generate. Be explicit: "
                "'A clean weekly workout calendar with Mon-Sun rows, day labels, "
                "exercise icons, sans-serif font, light theme.'"
            )
        ),
        kind: str = Field(
            default="workout_plan",
            description="One of: workout_plan, meal_plan, progress_chart, other",
        ),
        linked_workout_plan_id: UUID | None = None,
        linked_meal_plan_id: UUID | None = None,
    ) -> dict:
        """Generate an image (e.g. weekly workout/meal calendar) via OpenAI gpt-image-1.

        Saves the PNG to the file store and returns its file id and URL so you
        can reference it in your reply (e.g. ![plan](/files/<id>))."""
        from sqlmodel import select as _select

        from app.agent.effective_config import load_effective_config, resolve_api_key
        from app.agent.image_gen import generate_image
        from app.agent.router import Provider
        from app.db.models import UserProfile
        from app.files import storage

        # Respect local-only mode — image gen requires OpenAI.
        async with ctx.deps.session_factory() as _s:
            _profile = (
                await _s.execute(
                    _select(UserProfile).where(UserProfile.user_id == ctx.deps.user_id)
                )
            ).scalar_one_or_none()
        if _profile and _profile.local_only:
            return {
                "error": (
                    "Image generation is disabled in local-only mode. Disable "
                    "'Local-only mode' in Settings to use it (image generation "
                    "requires OpenAI's gpt-image-1)."
                ),
            }

        eff = await load_effective_config(ctx.deps.user_id)
        img = await generate_image(
            prompt,
            api_key=resolve_api_key(Provider.OPENAI, eff),
        )
        filename = f"{kind}-{date.today().isoformat()}.png"
        fid, rel = storage.write_bytes(img.data, filename=filename, mime_type=img.mime_type)
        async with ctx.deps.session_factory() as session:
            f = File(
                id=fid,
                user_id=ctx.deps.user_id,
                kind=FileKind.GENERATED,
                filename=filename,
                mime_type=img.mime_type,
                size_bytes=len(img.data),
                storage_path=rel,
                prompt=prompt,
                description=kind,
                linked_workout_plan_id=linked_workout_plan_id,
                linked_meal_plan_id=linked_meal_plan_id,
            )
            session.add(f)
            await session.commit()
        return {"file_id": str(fid), "url": f"/files/{fid}", "filename": filename}

    @agent.tool
    async def log_mental_state(
        ctx: RunContext[AgentDeps],
        mood_score: int | None = Field(
            default=None, ge=1, le=10, description="1=worst, 10=best"
        ),
        stress_level: int | None = Field(default=None, ge=1, le=10),
        energy_level: int | None = Field(default=None, ge=1, le=10),
        sleep_quality: int | None = Field(default=None, ge=1, le=10),
        note: str | None = None,
    ) -> str:
        """Persist a mental-state snapshot. Use when the user shares mood, stress,
        energy, or sleep quality so trends accumulate. Anything provided is logged
        as a HealthMetric row; missing values are skipped."""
        rows: list[HealthMetric] = []
        now = datetime.utcnow()
        for metric_type, value in (
            ("mood_score", mood_score),
            ("stress_level", stress_level),
            ("energy_level", energy_level),
            ("sleep_quality", sleep_quality),
        ):
            if value is None:
                continue
            rows.append(
                HealthMetric(
                    user_id=ctx.deps.user_id,
                    recorded_at=now,
                    metric_type=metric_type,
                    value=float(value),
                    source="mental_health_coach",
                    raw_data={"note": note} if note else None,
                )
            )
        if not rows:
            return (
                "Nothing to log — provide at least one of mood_score, stress_level, "
                "energy_level, sleep_quality."
            )
        async with ctx.deps.session_factory() as session:
            session.add_all(rows)
            await session.commit()
        logged = ", ".join(r.metric_type for r in rows)
        return f"Logged: {logged} at {now.isoformat(timespec='minutes')}Z"

    @agent.tool
    async def update_body_metrics(
        ctx: RunContext[AgentDeps],
        weight_kg: float | None = Field(
            default=None, ge=20, le=350, description="Current body weight in kg"
        ),
        height_cm: float | None = Field(
            default=None, ge=50, le=250, description="Height in cm"
        ),
        target_weight_kg: float | None = Field(
            default=None, ge=20, le=350, description="Goal weight in kg"
        ),
        primary_goal: str | None = Field(
            default=None,
            max_length=80,
            description="e.g. 'lose fat', 'build strength', 'maintain'",
        ),
        weekly_workout_target: int | None = Field(
            default=None, ge=0, le=14, description="Target training sessions per week"
        ),
        daily_calorie_target: int | None = Field(
            default=None, ge=800, le=8000, description="Daily kcal target"
        ),
    ) -> str:
        """Update the user's body metrics or fitness goals in their profile.
        Only provide the fields the user has explicitly stated. Leave others as None."""
        if all(
            v is None
            for v in (
                weight_kg,
                height_cm,
                target_weight_kg,
                primary_goal,
                weekly_workout_target,
                daily_calorie_target,
            )
        ):
            return "Nothing to update — provide at least one metric."
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(UserProfile).where(UserProfile.user_id == ctx.deps.user_id)
            )
            profile = result.scalar_one_or_none()
            if not profile:
                profile = UserProfile(user_id=ctx.deps.user_id)
                session.add(profile)
            if weight_kg is not None:
                profile.weight_kg = weight_kg
            if height_cm is not None:
                profile.height_cm = height_cm
            if target_weight_kg is not None:
                profile.target_weight_kg = target_weight_kg
            if primary_goal is not None:
                profile.primary_goal = primary_goal
            if weekly_workout_target is not None:
                profile.weekly_workout_target = weekly_workout_target
            if daily_calorie_target is not None:
                profile.daily_calorie_target = daily_calorie_target
            profile.updated_at = datetime.utcnow()
            await session.commit()
        updated = [k for k, v in {
            "weight_kg": weight_kg, "height_cm": height_cm,
            "target_weight_kg": target_weight_kg, "primary_goal": primary_goal,
            "weekly_workout_target": weekly_workout_target,
            "daily_calorie_target": daily_calorie_target,
        }.items() if v is not None]
        return f"Updated profile: {', '.join(updated)}."

    @agent.tool
    async def get_recent_health_metrics(
        ctx: RunContext[AgentDeps],
        metric_type: str = Field(
            description="e.g. 'weight_kg', 'resting_hr', 'hrv_ms', 'sleep_h', 'steps'"
        ),
        days: int = 30,
    ) -> list[HealthMetricSummary]:
        """Pull a time-series of a health metric ingested from Garmin/Apple Health."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(HealthMetric)
                .where(HealthMetric.user_id == ctx.deps.user_id)
                .where(HealthMetric.metric_type == metric_type)
                .where(HealthMetric.recorded_at >= cutoff)
                .order_by(HealthMetric.recorded_at.desc())
            )
            metrics = result.scalars().all()
            return [
                HealthMetricSummary(
                    recorded_at=m.recorded_at,
                    metric_type=m.metric_type,
                    value=m.value,
                )
                for m in metrics
            ]
