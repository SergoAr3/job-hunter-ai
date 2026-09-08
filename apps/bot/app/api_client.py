import re
from datetime import date, datetime
from typing import Protocol, TypeGuard

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
_APPLICATION_LIST_ITEM_FIELDS = {
    "app_id", "job_id", "created_at", "title", "company", "location",
    "workplace_type", "parsing_status", "ai_enrichment_status",
}


def _is_aware_iso_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


class BotApiClient(Protocol):
    async def create_or_get_user(self, telegram_user: User) -> int: ...

    async def save_application(self, user_id: int, source_url: str) -> dict[str, object]: ...

    async def get_application_match(self, user_id: int, application_id: int) -> dict[str, object]: ...

    async def list_applications(self, user_id: int, *, limit: int, offset: int, status: str | None = None) -> dict[str, object]: ...

    async def get_application(self, user_id: int, application_id: int) -> dict[str, object]: ...

    async def put_application_status(self, user_id: int, application_id: int, status: str) -> dict[str, object]: ...

    async def get_application_status_history(
        self, user_id: int, application_id: int
    ) -> dict[str, object]: ...

    async def put_application_note(self, user_id: int, application_id: int, note: str) -> dict[str, object]: ...

    async def delete_application_note(self, user_id: int, application_id: int) -> dict[str, object]: ...

    async def set_application_next_action(self, user_id: int, application_id: int, action: str, due_on: str) -> dict[str, object]: ...

    async def delete_application_next_action(self, user_id: int, application_id: int) -> dict[str, object]: ...

    async def get_user_profile(self, user_id: int) -> dict[str, object] | None: ...

    async def put_user_profile(self, user_id: int, profile: dict[str, object]) -> dict[str, object]: ...

    async def normalize_profile_skills(self, skills: list[str]) -> list[str]: ...

    async def normalize_profile_languages(
        self, languages: list[dict[str, str]]
    ) -> list[dict[str, str]]: ...

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

    async def list_applications(self, user_id: int, *, limit: int, offset: int, status: str | None = None) -> dict[str, object]:
        params: dict[str, str | int] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        response = await self._client.get(
            f"/users/{user_id}/applications", params=params
        )
        response.raise_for_status()
        payload = _json_object(response)
        if not isinstance(payload.get("items"), list) or not isinstance(payload.get("has_next"), bool):
            raise httpx.DecodingError("API response has invalid applications page shape", request=response.request)
        if not all(
            isinstance(item, dict)
            and _APPLICATION_LIST_ITEM_FIELDS.issubset(item)
            and isinstance(item.get("app_id"), int)
            and isinstance(item.get("job_id"), int)
            for item in payload["items"]
        ):
            raise httpx.DecodingError("API response has invalid application list item", request=response.request)
        return payload

    async def get_application(self, user_id: int, application_id: int) -> dict[str, object]:
        response = await self._client.get(f"/users/{user_id}/applications/{application_id}")
        response.raise_for_status()
        payload = _json_object(response)
        application, job = payload.get("application"), payload.get("job")
        if (
            not isinstance(application, dict)
            or not isinstance(job, dict)
            or not isinstance(application.get("id"), int)
            or not isinstance(job.get("id"), int)
        ):
            raise httpx.DecodingError("API response has invalid application detail shape", request=response.request)
        return payload

    async def put_application_status(self, user_id: int, application_id: int, status: str) -> dict[str, object]:
        response = await self._client.put(
            f"/users/{user_id}/applications/{application_id}/status", json={"status": status}
        )
        response.raise_for_status()
        payload = _json_object(response)
        application, job = payload.get("application"), payload.get("job")
        if (
            not isinstance(application, dict) or not isinstance(job, dict)
            or type(application.get("id")) is not int or application["id"] != application_id
            or type(application.get("user_id")) is not int
            or application.get("user_id") != user_id
            or type(application.get("job_id")) is not int
            or type(job.get("id")) is not int or application.get("job_id") != job["id"]
            or application.get("status") not in ("saved", "applied", "interview", "rejected", "offer")
        ):
            raise httpx.DecodingError("API response has invalid status detail shape", request=response.request)
        return payload

    async def get_application_status_history(
        self, user_id: int, application_id: int
    ) -> dict[str, object]:
        response = await self._client.get(
            f"/users/{user_id}/applications/{application_id}/status-history"
        )
        response.raise_for_status()
        payload = _json_object(response)
        items = payload.get("items")
        if not isinstance(items, list) or not all(
            isinstance(item, dict)
            and item.get("status") in ("saved", "applied", "interview", "rejected", "offer")
            and _is_aware_iso_datetime(item.get("occurred_at"))
            for item in items
        ):
            raise httpx.DecodingError(
                "API response has invalid application status history shape",
                request=response.request,
            )
        return payload

    async def put_application_note(self, user_id: int, application_id: int, note: str) -> dict[str, object]:
        response = await self._client.put(
            f"/users/{user_id}/applications/{application_id}/note", json={"note": note}
        )
        return self._note_detail(response, user_id, application_id)

    async def delete_application_note(self, user_id: int, application_id: int) -> dict[str, object]:
        response = await self._client.delete(f"/users/{user_id}/applications/{application_id}/note")
        return self._note_detail(response, user_id, application_id)

    @staticmethod
    def _note_detail(response: httpx.Response, user_id: int, application_id: int) -> dict[str, object]:
        response.raise_for_status()
        payload = _json_object(response)
        application, job = payload.get("application"), payload.get("job")
        if (
            not isinstance(application, dict) or not isinstance(job, dict)
            or type(application.get("id")) is not int or application["id"] != application_id
            or type(application.get("user_id")) is not int or application["user_id"] != user_id
            or type(job.get("id")) is not int or application.get("job_id") != job["id"]
            or application.get("status") not in ("saved", "applied", "interview", "rejected", "offer")
            or "note" not in application
            or (application["note"] is not None and not isinstance(application["note"], str))
        ):
            raise httpx.DecodingError("API response has invalid note detail shape", request=response.request)
        return payload

    async def set_application_next_action(self, user_id: int, application_id: int, action: str, due_on: str) -> dict[str, object]:
        response = await self._client.put(
            f"/users/{user_id}/applications/{application_id}/next-action",
            json={"next_action": action, "next_action_due_on": due_on},
        )
        return self._next_action_detail(response, user_id, application_id)

    async def delete_application_next_action(self, user_id: int, application_id: int) -> dict[str, object]:
        response = await self._client.delete(f"/users/{user_id}/applications/{application_id}/next-action")
        return self._next_action_detail(response, user_id, application_id)

    @staticmethod
    def _next_action_detail(response: httpx.Response, user_id: int, application_id: int) -> dict[str, object]:
        payload = JobHunterApiClient._note_detail(response, user_id, application_id)
        application = payload["application"]
        assert isinstance(application, dict)
        action, due_on = application.get("next_action"), application.get("next_action_due_on")
        valid = "next_action" in application and "next_action_due_on" in application
        if action is not None or due_on is not None:
            valid = valid and isinstance(action, str) and 1 <= len(action) <= 500
            valid = valid and isinstance(due_on, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", due_on) is not None
            if valid:
                try:
                    date.fromisoformat(str(due_on))
                except ValueError:
                    valid = False
        if not valid:
            raise httpx.DecodingError("API response has invalid next action detail shape", request=response.request)
        return payload

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

    async def normalize_profile_skills(self, skills: list[str]) -> list[str]:
        response = await self._client.post("/profile/skills/normalize", json={"skills": skills})
        response.raise_for_status()
        payload = _json_object(response)
        normalized = payload.get("skills")
        if not isinstance(normalized, list) or not all(isinstance(skill, str) for skill in normalized):
            raise httpx.DecodingError(
                "API response has invalid normalized skills shape", request=response.request
            )
        return normalized

    async def normalize_profile_languages(
        self, languages: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        response = await self._client.post("/profile/languages/normalize", json={"languages": languages})
        response.raise_for_status()
        payload = _json_object(response)
        normalized = payload.get("languages")
        if not _is_language_list(normalized):
            raise httpx.DecodingError(
                "API response has invalid normalized languages shape", request=response.request
            )
        return normalized

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


def _is_language_list(value: object) -> TypeGuard[list[dict[str, str]]]:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and set(item) == {"language", "level"}
        and isinstance(item.get("language"), str)
        and isinstance(item.get("level"), str)
        for item in value
    )


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
