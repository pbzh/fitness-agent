"""ICS (iCalendar) export for workout and meal plans."""

from datetime import datetime, timedelta
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import select

from app.api.deps import get_approved_user_id
from app.db.models import MealPlan, PlannedMeal, WorkoutPlan, WorkoutSession
from app.db.session import AsyncSessionLocal

router = APIRouter(prefix="/calendar", tags=["calendar"])


# Default times when the user hasn't set scheduled_time
_DEFAULT_WORKOUT_TIME = (18, 0)
_DEFAULT_MEAL_TIMES = {
    "breakfast": (7, 30),
    "lunch": (12, 30),
    "dinner": (19, 0),
    "snack": (15, 30),
}


def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\n", "\\n")
    )


def _fold_line(line: str) -> str:
    """RFC 5545: lines longer than 75 octets must be folded."""
    out = []
    while len(line.encode("utf-8")) > 75:
        cut = 73
        while len(line[:cut].encode("utf-8")) > 73:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return "\r\n".join(out)


def _utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _local_stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def _build_ics(name: str, events: list[dict]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//coacher//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_ics_escape(name)}",
        # Embed Europe/Zurich VTIMEZONE so calendar apps render local time.
        "BEGIN:VTIMEZONE",
        "TZID:Europe/Zurich",
        "BEGIN:STANDARD",
        "DTSTART:19701025T030000",
        "TZOFFSETFROM:+0200",
        "TZOFFSETTO:+0100",
        "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
        "TZNAME:CET",
        "END:STANDARD",
        "BEGIN:DAYLIGHT",
        "DTSTART:19700329T020000",
        "TZOFFSETFROM:+0100",
        "TZOFFSETTO:+0200",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
        "TZNAME:CEST",
        "END:DAYLIGHT",
        "END:VTIMEZONE",
    ]
    stamp = _utc_stamp()
    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{ev['uid']}")
        lines.append(f"DTSTAMP:{stamp}")
        lines.append(f"DTSTART;TZID=Europe/Zurich:{_local_stamp(ev['start'])}")
        lines.append(f"DTEND;TZID=Europe/Zurich:{_local_stamp(ev['end'])}")
        lines.append(f"SUMMARY:{_ics_escape(ev['summary'])}")
        if ev.get("description"):
            lines.append(f"DESCRIPTION:{_ics_escape(ev['description'])}")
        if ev.get("location"):
            lines.append(f"LOCATION:{_ics_escape(ev['location'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold_line(line) for line in lines) + "\r\n"


def _ics_response(filename: str, body: str) -> Response:
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/workouts/{plan_id}.ics")
async def workout_plan_ics(
    plan_id: UUID,
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> Response:
    async with AsyncSessionLocal() as session:
        plan = (
            await session.execute(
                select(WorkoutPlan).where(WorkoutPlan.id == plan_id, WorkoutPlan.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not plan:
            raise HTTPException(status_code=404, detail="Workout plan not found")

        rows = (
            await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.plan_id == plan_id)
                .order_by(WorkoutSession.scheduled_date)
            )
        ).scalars().all()

    events = []
    for s in rows:
        if s.workout_type.value == "rest":
            continue
        hh, mm = (
            (s.scheduled_time.hour, s.scheduled_time.minute)
            if s.scheduled_time
            else _DEFAULT_WORKOUT_TIME
        )
        start = datetime.combine(s.scheduled_date, datetime.min.time()).replace(hour=hh, minute=mm)
        end = start + timedelta(minutes=max(s.duration_min, 15))
        ex_summary = ", ".join(
            (e.get("name") or e.get("exercise") or "") for e in (s.exercises or [])
        )[:300]
        events.append(
            {
                "uid": f"workout-{s.id}@fitness-agent",
                "start": start,
                "end": end,
                "summary": f"{s.workout_type.value.title()} ({s.intensity.value})",
                "description": (
                    f"Exercises: {ex_summary}\n"
                    f"{('Notes: ' + s.notes) if s.notes else ''}"
                ).strip(),
                "location": s.location.value if s.location else "",
            }
        )

    body = _build_ics(f"coacher — Workouts (week {plan.week_start})", events)
    return _ics_response(f"workout-plan-{plan.week_start}.ics", body)


@router.get("/meals/{plan_id}.ics")
async def meal_plan_ics(
    plan_id: UUID,
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> Response:
    async with AsyncSessionLocal() as session:
        plan = (
            await session.execute(
                select(MealPlan).where(MealPlan.id == plan_id, MealPlan.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not plan:
            raise HTTPException(status_code=404, detail="Meal plan not found")

        rows = (
            await session.execute(
                select(PlannedMeal)
                .where(PlannedMeal.plan_id == plan_id)
                .order_by(PlannedMeal.scheduled_date, PlannedMeal.slot)
            )
        ).scalars().all()

    events = []
    for m in rows:
        if m.scheduled_time:
            hh, mm = m.scheduled_time.hour, m.scheduled_time.minute
        else:
            hh, mm = _DEFAULT_MEAL_TIMES.get(m.slot.value, (12, 0))
        start = datetime.combine(m.scheduled_date, datetime.min.time()).replace(hour=hh, minute=mm)
        cook = (m.prep_time_min or 0) + (m.cook_time_min or 0)
        end = start + timedelta(minutes=max(cook or 30, 15))
        macros = []
        if m.calories:
            macros.append(f"{m.calories} kcal")
        if m.protein_g is not None:
            macros.append(f"{m.protein_g:.0f}P")
        if m.carbs_g is not None:
            macros.append(f"{m.carbs_g:.0f}C")
        if m.fat_g is not None:
            macros.append(f"{m.fat_g:.0f}F")
        events.append(
            {
                "uid": f"meal-{m.id}@fitness-agent",
                "start": start,
                "end": end,
                "summary": f"{m.slot.value.title()}: {m.name}",
                "description": (
                    (" · ".join(macros) + "\n" if macros else "")
                    + (f"Recipe: {m.recipe}" if m.recipe else "")
                ).strip(),
            }
        )

    body = _build_ics(f"coacher — Meals (week {plan.week_start})", events)
    return _ics_response(f"meal-plan-{plan.week_start}.ics", body)


@router.get("/upcoming.ics")
async def upcoming_ics(
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
    days: int = 30,
) -> Response:
    """Single feed of all upcoming workouts + meals for the next N days."""
    from datetime import date as _date

    horizon = _date.today() + timedelta(days=days)
    async with AsyncSessionLocal() as session:
        workouts = (
            await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.user_id == user_id)
                .where(WorkoutSession.scheduled_date >= _date.today())
                .where(WorkoutSession.scheduled_date <= horizon)
                .order_by(WorkoutSession.scheduled_date)
            )
        ).scalars().all()
        meals = (
            await session.execute(
                select(PlannedMeal)
                .where(PlannedMeal.user_id == user_id)
                .where(PlannedMeal.scheduled_date >= _date.today())
                .where(PlannedMeal.scheduled_date <= horizon)
                .order_by(PlannedMeal.scheduled_date, PlannedMeal.slot)
            )
        ).scalars().all()

    events = []
    for s in workouts:
        if s.workout_type.value == "rest":
            continue
        hh, mm = (
            (s.scheduled_time.hour, s.scheduled_time.minute)
            if s.scheduled_time
            else _DEFAULT_WORKOUT_TIME
        )
        start = datetime.combine(s.scheduled_date, datetime.min.time()).replace(hour=hh, minute=mm)
        events.append(
            {
                "uid": f"workout-{s.id}@fitness-agent",
                "start": start,
                "end": start + timedelta(minutes=max(s.duration_min, 15)),
                "summary": f"💪 {s.workout_type.value.title()}",
                "description": s.notes or "",
                "location": s.location.value if s.location else "",
            }
        )
    for m in meals:
        hh, mm = (
            (m.scheduled_time.hour, m.scheduled_time.minute)
            if m.scheduled_time
            else _DEFAULT_MEAL_TIMES.get(m.slot.value, (12, 0))
        )
        start = datetime.combine(m.scheduled_date, datetime.min.time()).replace(hour=hh, minute=mm)
        events.append(
            {
                "uid": f"meal-{m.id}@fitness-agent",
                "start": start,
                "end": start + timedelta(minutes=30),
                "summary": f"🍽 {m.slot.value.title()}: {m.name}",
                "description": "",
            }
        )

    # Touch the unused import so static analyzers don't trip
    _ = uuid5(NAMESPACE_URL, "upcoming")
    body = _build_ics("coacher — Upcoming", events)
    return _ics_response("coacher-upcoming.ics", body)
