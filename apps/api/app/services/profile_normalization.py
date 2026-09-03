"""Canonicalization for profile values at the API domain boundary."""


_CANONICAL_SKILL_ALIASES = {
    "python": "Python",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "sql": "SQL",
    "html": "HTML",
    "api": "API",
    "git": "Git",
    "redis": "Redis",
}

_CANONICAL_LANGUAGE_LEVELS = {
    "a1": "A1",
    "a2": "A2",
    "b1": "B1",
    "b2": "B2",
    "c1": "C1",
    "c2": "C2",
    "fluent": "fluent",
    "native": "native",
}

_WORKPLACE_LIKE_LOCATION_VALUES = {
    "any",
    "remote",
    "hybrid",
    "onsite",
    "любой",
    "удаленно",
    "гибрид",
    "на месте работодателя",
}


def normalize_profile_skills(values: list[str]) -> list[str]:
    """Normalize already-separated skills without guessing unknown spellings."""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = " ".join(value.split())
        if not item:
            continue
        canonical = _CANONICAL_SKILL_ALIASES.get(item.casefold(), item)
        key = canonical.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(canonical)
    return normalized


def normalize_profile_language_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("language must not be blank")
    return normalized


def normalize_profile_language_level(value: str) -> str:
    key = " ".join(value.split()).casefold()
    normalized = _CANONICAL_LANGUAGE_LEVELS.get(key)
    if normalized is None:
        allowed = ", ".join(("A1", "A2", "B1", "B2", "C1", "C2", "fluent", "native"))
        raise ValueError(f"language level must be one of: {allowed}")
    return normalized


def profile_language_name_key(value: str) -> str:
    return normalize_profile_language_name(value).casefold()


def is_workplace_like_location(value: str) -> bool:
    normalized = " ".join(value.casefold().replace("ё", "е").split())
    return normalized in _WORKPLACE_LIKE_LOCATION_VALUES
