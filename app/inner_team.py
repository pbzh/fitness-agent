"""Inner Team role settings and lightweight role detection."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

INNER_TEAM_DEFAULT_ROLES: list[dict[str, Any]] = [
    {
        "id": "athlete",
        "name": "Athlete",
        "description": "Commits to the next useful training action.",
        "intention": "Build consistency and performance without losing contact with recovery.",
        "strengths": ["Commitment", "Momentum", "Standards"],
        "watch_outs": ["Overdrive", "Ignoring fatigue", "All-or-nothing thinking"],
        "tasks": [
            "Choose the next concrete session",
            "Keep effort aligned with the plan",
            "Track what was actually completed",
        ],
        "tone": "direct",
        "challenge_level": 4,
        "focus_areas": ["training", "consistency", "performance"],
        "avoid": ["reckless volume", "guilt as motivation"],
        "is_custom": False,
    },
    {
        "id": "recovery_guardian",
        "name": "Recovery Guardian",
        "description": "Protects adaptation, sleep, and long-term capacity.",
        "intention": "Prevent overtraining and make recovery an active decision.",
        "strengths": ["Caution", "Body awareness", "Long-term thinking"],
        "watch_outs": ["Avoidance disguised as recovery", "Being too conservative"],
        "tasks": [
            "Check sleep, soreness, and recent RPE",
            "Choose the minimum effective dose",
            "Protect a real rest window",
        ],
        "tone": "calm",
        "challenge_level": 2,
        "focus_areas": ["recovery", "sleep", "training load"],
        "avoid": ["pressure", "punishment workouts"],
        "is_custom": False,
    },
    {
        "id": "planner",
        "name": "Planner",
        "description": "Turns intent into structure.",
        "intention": "Reduce friction by making the next actions obvious and scheduled.",
        "strengths": ["Structure", "Prioritization", "Follow-through"],
        "watch_outs": ["Overplanning", "Hiding from action in details"],
        "tasks": [
            "Pick the next decision",
            "Put training and meals into realistic slots",
            "Remove one source of friction",
        ],
        "tone": "structured",
        "challenge_level": 3,
        "focus_areas": ["planning", "schedule", "habits"],
        "avoid": ["bloated plans", "unclear next steps"],
        "is_custom": False,
    },
    {
        "id": "honest_coach",
        "name": "Honest Coach",
        "description": "Names the pattern without drama.",
        "intention": "Separate real constraints from excuses and choose a clean next move.",
        "strengths": ["Clarity", "Accountability", "Reality checks"],
        "watch_outs": ["Harshness", "Missing legitimate fatigue"],
        "tasks": [
            "Name the trade-off",
            "Distinguish resistance from a real limit",
            "Choose a small accountable action",
        ],
        "tone": "plain",
        "challenge_level": 4,
        "focus_areas": ["avoidance", "accountability", "decisions"],
        "avoid": ["shaming", "lectures"],
        "is_custom": False,
    },
    {
        "id": "compassionate_friend",
        "name": "Compassionate Friend",
        "description": "Keeps setbacks human and recoverable.",
        "intention": "Lower guilt enough that the next healthy action becomes possible.",
        "strengths": ["Encouragement", "Perspective", "Emotional regulation"],
        "watch_outs": ["Letting standards disappear", "Comfort without action"],
        "tasks": [
            "Reset after guilt or emotional eating",
            "Choose one repair action",
            "Keep the tone humane",
        ],
        "tone": "warm",
        "challenge_level": 2,
        "focus_areas": ["guilt", "self-talk", "resetting"],
        "avoid": ["moralizing food", "self-attack"],
        "is_custom": False,
    },
    {
        "id": "nutrition_strategist",
        "name": "Nutrition Strategist",
        "description": "Connects food choices to training and recovery.",
        "intention": "Make nutrition practical, repeatable, and aligned with the current goal.",
        "strengths": ["Fueling", "Preparation", "Trade-off awareness"],
        "watch_outs": ["Macro tunnel vision", "Rigidity"],
        "tasks": [
            "Pick the next meal or snack",
            "Protect protein and fiber",
            "Plan around hunger and training load",
        ],
        "tone": "practical",
        "challenge_level": 3,
        "focus_areas": ["meals", "protein", "hunger"],
        "avoid": ["food guilt", "fake precision"],
        "is_custom": False,
    },
    {
        "id": "future_self",
        "name": "Future Self",
        "description": "Looks past today's mood.",
        "intention": "Keep choices aligned with the person you are building over months.",
        "strengths": ["Perspective", "Patience", "Identity"],
        "watch_outs": ["Becoming too abstract", "Ignoring today's constraints"],
        "tasks": [
            "Choose the option you will respect tomorrow",
            "Keep long-term goals visible",
            "Trade intensity for sustainability when needed",
        ],
        "tone": "grounded",
        "challenge_level": 3,
        "focus_areas": ["long-term goals", "identity", "sustainability"],
        "avoid": ["fantasy planning", "short-term panic"],
        "is_custom": False,
    },
]


def default_inner_team() -> dict[str, Any]:
    return {
        "mode": "auto",
        "active_role_id": "athlete",
        "active_reason": "Default role until your messages suggest a more useful stance.",
        "roles": deepcopy(INNER_TEAM_DEFAULT_ROLES),
        "suggestions": [],
        "updated_at": datetime.now(UTC).isoformat(),
    }


def normalize_inner_team(raw: dict[str, Any] | None) -> dict[str, Any]:
    settings = default_inner_team()
    if isinstance(raw, dict):
        settings.update({k: v for k, v in raw.items() if k in settings})

    mode = settings.get("mode")
    settings["mode"] = mode if mode in {"auto", "manual"} else "auto"

    roles = settings.get("roles")
    if not isinstance(roles, list) or not roles:
        roles = deepcopy(INNER_TEAM_DEFAULT_ROLES)
    settings["roles"] = roles[:10]

    role_ids = {str(r.get("id")) for r in settings["roles"] if isinstance(r, dict)}
    if settings.get("active_role_id") not in role_ids:
        settings["active_role_id"] = settings["roles"][0]["id"]

    if not isinstance(settings.get("active_reason"), str):
        settings["active_reason"] = ""
    if not isinstance(settings.get("suggestions"), list):
        settings["suggestions"] = []

    return settings


def active_role(settings: dict[str, Any]) -> dict[str, Any] | None:
    role_id = settings.get("active_role_id")
    for role in settings.get("roles", []):
        if role.get("id") == role_id:
            return role
    return None


_ROLE_KEYWORDS: dict[str, list[str]] = {
    "recovery_guardian": [
        "tired",
        "wrecked",
        "exhausted",
        "fatigue",
        "sore",
        "sleep",
        "recovery",
        "rest",
        "overtrain",
        "burnout",
    ],
    "compassionate_friend": [
        "guilt",
        "guilty",
        "ashamed",
        "failed",
        "emotional eating",
        "binge",
        "overeating",
        "hate myself",
    ],
    "planner": ["plan", "schedule", "calendar", "routine", "organize", "when should"],
    "honest_coach": [
        "avoid",
        "avoiding",
        "hesitation",
        "can't get myself",
        "cannot get myself",
        "stuck",
        "excuse",
        "conflict",
    ],
    "athlete": [
        "consistency",
        "disciplined",
        "discipline",
        "performance",
        "train hard",
        "push",
    ],
    "nutrition_strategist": [
        "meal",
        "eat",
        "calories",
        "protein",
        "hungry",
        "craving",
        "snack",
        "nutrition",
    ],
    "future_self": ["future", "long term", "next month", "goal weight", "identity"],
}


def detect_inner_team_role(
    message: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Update suggestion state and active role when auto mode is enabled."""

    normalized = normalize_inner_team(settings)
    available = {role["id"]: role for role in normalized["roles"]}
    text = message.lower()
    scored: list[tuple[str, int, list[str]]] = []

    for role_id, keywords in _ROLE_KEYWORDS.items():
        if role_id not in available:
            continue
        hits = [kw for kw in keywords if kw in text]
        if hits:
            scored.append((role_id, len(hits), hits[:3]))

    scored.sort(key=lambda item: item[1], reverse=True)
    suggestions = []
    for role_id, score, hits in scored[:4]:
        role = available[role_id]
        confidence = min(95, 45 + score * 15)
        suggestions.append(
            {
                "role_id": role_id,
                "name": role["name"],
                "confidence": confidence,
                "reason": f"Matched: {', '.join(hits)}",
            }
        )

    normalized["suggestions"] = suggestions
    if normalized["mode"] == "auto" and suggestions:
        normalized["active_role_id"] = suggestions[0]["role_id"]
        normalized["active_reason"] = suggestions[0]["reason"]
        normalized["updated_at"] = datetime.now(UTC).isoformat()

    return normalized
