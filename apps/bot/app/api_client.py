from typing import Protocol

import httpx
from aiogram.types import User

_PROFILE_RESPONSE_FIELDS = {
    "target_roles",
    "skills",
    "experience",
    "location",
    "workplace_preference",
    "salary_min",
    "salary_currency",
    "salary_period",
    "languages",
}
_EXPERIENCE_VALUES = {"intern", "junior", "middle", "senior", "lead", "unknown"}
_WORKPLACE_VALUES = {"remote", "hybrid", "onsite", "any"}
_SALARY_PERIOD_VALUES = {"month", "year", "unknown"}


class BotApiClient(Protocol):
    async def create_or_get_user(self, telegram_user: User) -> int: ...

    async def save_application(self, user_id: int, source_url: str) -> dict[str, object]: ...

    async def get_application_match(self, user_id: int, application_id: int) -> dict[str, object]: ...

    async def get_user_profile(self, user_id: int) -> dict[str, object] | None: ...

    async def put_user_profile(self, user_id: int, profile: dict[str, object]) -> dict[str, object]: ...

    async def create_profile_draft_from_cv(
        self, user_id: int, *, filename: str, content_type: str, content: bytes
    ) -> dict[str, object]: ...


class JobHunterApiClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=40.0)

    async def create_or_get_user(self, telegram_user: User) -> int:
        response = await self._client.post(
            "/users/telegram",
            json={
                "telegram_id": telegram_user.id,
                "username": telegram_user.username,
                "first_name": telegram_user.first_name,
                "last_name": telegram_user.last_name,
                "language_code": telegram_user.language_code,
            },
        )
        response.raise_for_status()
        payload = _json_object(response)
        user_id = payload.get("id")
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            raise httpx.DecodingError("API response has no integer user id", request=response.request)
        return user_id

    async def save_application(self, user_id: int, source_url: str) -> dict[str, object]:
        response = await self._client.post(
            f"/users/{user_id}/applications", json={"source_url": source_url}
        )
        response.raise_for_status()
        return _json_object(response)

    async def get_application_match(self, user_id: int, application_id: int) -> dict[str, object]:
        response = await self._client.get(f"/users/{user_id}/applications/{application_id}/match")
        response.raise_for_status()
        return _json_object(response)

    async def get_user_profile(self, user_id: int) -> dict[str, object] | None:
        response = await self._client.get(f"/users/{user_id}/profile")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _user_profile_object(response)

    async def put_user_profile(self, user_id: int, profile: dict[str, object]) -> dict[str, object]:
        response = await self._client.put(f"/users/{user_id}/profile", json=profile)
        response.raise_for_status()
        return _json_object(response)

    async def create_profile_draft_from_cv(
        self, user_id: int, *, filename: str, content_type: str, content: bytes
    ) -> dict[str, object]:
        response = await self._client.post(
            f"/users/{user_id}/profile/draft-from-cv",
            files={"file": (filename, content, content_type)},
        )
        response.raise_for_status()
        return _json_object(response)

    async def close(self) -> None:
        await self._client.aclose()


def _json_object(response: httpx.Response) -> dict[str, object]:
    try:
        payload: object = response.json()
    except ValueError as error:
        raise httpx.DecodingError("API response is not valid JSON", request=response.request) from error
    if not isinstance(payload, dict):
        raise httpx.DecodingError("API response must be a JSON object", request=response.request)
    return payload


def _user_profile_object(response: httpx.Response) -> dict[str, object]:
    payload = _json_object(response)
    if not _is_complete_user_profile(payload):
        raise httpx.DecodingError(
            "API response has invalid user profile shape", request=response.request
        )
    return payload


def _is_complete_user_profile(profile: dict[str, object]) -> bool:
    if not _PROFILE_RESPONSE_FIELDS.issubset(profile):
        return False
    if not _is_nonempty_string_list(profile["target_roles"]):
        return False
    if not all(
        (
            _is_string_list(profile["skills"]),
            _is_enum(profile["experience"], _EXPERIENCE_VALUES),
            _is_string_list(profile["location"]),
            _is_enum(profile["workplace_preference"], _WORKPLACE_VALUES),
            _is_enum(profile["salary_period"], _SALARY_PERIOD_VALUES),
            _is_languages(profile["languages"]),
        )
    ):
        return False
    salary_min = profile["salary_min"]
    salary_currency = profile["salary_currency"]
    if salary_min is None:
        return salary_currency is None and profile["salary_period"] == "unknown"
    return (
        isinstance(salary_min, str)
        and isinstance(salary_currency, str)
        and _is_enum(profile["salary_period"], {"month", "year"})
    )


def _is_nonempty_string_list(value: object) -> bool:
    return _is_string_list(value) and bool(value)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_enum(value: object, values: set[str]) -> bool:
    return isinstance(value, str) and value in values


def _is_languages(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("language"), str)
        and isinstance(item.get("level"), str)
        for item in value
    )
