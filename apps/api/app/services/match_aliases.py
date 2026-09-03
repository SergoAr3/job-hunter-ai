"""Small, explicit aliases used by the deterministic v1 matcher."""

# Keys and values are normalized by ``normalize_text`` in job_matching.py.
SKILL_ALIASES = {
    "postgres": "postgresql",
    "js": "javascript",
    "rest": "rest api",
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

# Exact role phrases only.  Do not turn common Russian words such as
# ``разработчик`` or ``по`` into global token aliases: they are too ambiguous.
ROLE_PHRASE_ALIASES = {
    "backend разработчик": "backend engineer",
    "python разработчик": "python engineer",
    "разработчик по": "software engineer",
}

# Parenthetical title suffixes are removable only when every comma-separated
# qualifier is known to be non-conflicting metadata for an exact role alias.
SAFE_ROLE_TRAILING_QUALIFIERS = {
    "backend",
    "intern",
    "internship",
    "junior",
    "jr",
    "middle",
    "mid",
    "senior",
    "sr",
    "lead",
    "tech lead",
    "team lead",
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
