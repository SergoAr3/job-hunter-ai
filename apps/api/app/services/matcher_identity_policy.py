"""Frozen matcher equivalence, independent of database taxonomy and scoring."""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.services.skill_normalization import normalize_skill


@dataclass(frozen=True)
class MatcherIdentityPolicy:
    algorithm_version: str
    policy_version: str
    aliases: Mapping[str, str]

    def __post_init__(self) -> None:
        # Copy first: a proxy over a caller-owned dict would still be mutable.
        aliases = dict(self.aliases)
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in aliases.items()):
            raise ValueError("invalid_matcher_policy")
        object.__setattr__(self, "aliases", MappingProxyType(aliases))


MATCHER_IDENTITY_POLICY_V22 = MatcherIdentityPolicy(
    algorithm_version="job-match-v2.2",
    policy_version="matcher-identity-v1",
    aliases={
        "postgres": "postgresql", "postgresql": "postgresql",
        "js": "javascript", "javascript": "javascript",
        "rest": "rest api", "rest api": "rest api", "rest-api": "rest api",
        "nodejs": "node.js", "node.js": "node.js",
        "reactjs": "react", "react.js": "react", "react": "react",
    },
)
POLICIES_BY_ALGORITHM = MappingProxyType({
    "job-match-v2.2": MATCHER_IDENTITY_POLICY_V22,
})


def get_matcher_identity_policy(algorithm_version: str) -> MatcherIdentityPolicy:
    try:
        return POLICIES_BY_ALGORITHM[algorithm_version]
    except KeyError:
        raise ValueError("unsupported_matcher_algorithm") from None


def normalize_matcher_skill(value: object) -> str:
    """Foundation's validated NFKC/whitespace/casefold key, preserving punctuation."""
    return normalize_skill(value)[0]


def canonicalize_matcher_skill(value: object, *, algorithm_version: str = "job-match-v2.2") -> str:
    policy = get_matcher_identity_policy(algorithm_version)
    key = normalize_matcher_skill(value)
    return policy.aliases.get(key, key)


def equivalent_matcher_skills(left: object, right: object, *,
                             algorithm_version: str = "job-match-v2.2") -> bool:
    return canonicalize_matcher_skill(left, algorithm_version=algorithm_version) == canonicalize_matcher_skill(
        right, algorithm_version=algorithm_version)
