"""Small, explicit aliases used by the deterministic v1 matcher."""

# Keys and values are normalized by ``normalize_text`` in job_matching.py.
SKILL_ALIASES = {
    "postgres": "postgresql",
    "js": "javascript",
}

LANGUAGE_ALIASES = {
    "english": "english",
    "russian": "russian",
}

# These aliases only make title token comparison resilient to wording variants.
ROLE_TOKEN_ALIASES = {
    "developer": "engineer",
    "dev": "engineer",
}

SENIORITY_TOKENS = {
    "intern",
    "internship",
    "junior",
    "jr",
    "middle",
    "mid",
    "senior",
    "sr",
    "lead",
    "techlead",
    "tech",
    "team",
}
