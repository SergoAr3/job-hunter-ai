"""Conservative skill identity keys, independent of matching/scoring."""
from dataclasses import dataclass
import unicodedata


# Invisible letters/marks outside Cf: Hangul fillers and Mongolian selectors.
# Reject these explicitly without banning ordinary Lo letters or Mn marks.
_INVISIBLE_CODE_POINTS = frozenset({
    0x115F, 0x1160, 0x3164, 0xFFA0,
    0x180B, 0x180C, 0x180D, 0x180F,
})


class InvalidSkillValue(ValueError):
    """Only a safe reason code, never the rejected user-derived value."""


def _validate_characters(value: str) -> None:
    for char in value:
        code = ord(char)
        category = unicodedata.category(char)
        if (category in {"Cf", "Cs"}
                or code in _INVISIBLE_CODE_POINTS
                or (category == "Cc" and char not in "\t\n\r\x85")
                or code == 0x034F or 0xFE00 <= code <= 0xFE0F
                or 0xE0100 <= code <= 0xE01EF):
            raise InvalidSkillValue("forbidden_character")


def normalize_skill(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise InvalidSkillValue("not_string")
    _validate_characters(value)
    display = " ".join(value.split())
    key = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    _validate_characters(key)
    if not display or not key:
        raise InvalidSkillValue("blank")
    if len(display) > 100 or len(key) > 512:
        raise InvalidSkillValue("too_long")
    return key, display


@dataclass
class SkillSnapshot:
    labels: dict[str, str]
    invalid_count: int = 0
    quarantined: bool = False


def prepare_skill_snapshot(values: object) -> SkillSnapshot:
    if not isinstance(values, list):
        return SkillSnapshot({}, quarantined=True)
    result = SkillSnapshot({})
    for value in values:
        try:
            key, display = normalize_skill(value)
        except InvalidSkillValue:
            result.invalid_count += 1
        else:
            result.labels.setdefault(key, display)
    return result
