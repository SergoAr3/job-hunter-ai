import re
from datetime import date, datetime
from typing import Protocol, TypeGuard
from urllib.parse import urlsplit

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
_APPLICATION_STATUS_VALUES = {
    "saved", "applied", "recruiter_response", "interview", "offer", "hired", "withdrawn", "rejected",
}
_FOLLOW_UP_DUE_STATES = {"overdue", "today", "upcoming"}
_MATCH_LEARNING_BUCKETS = ("high", "medium", "low")
_MATCH_LEARNING_OUTCOMES = {
    "recruiter_response", "interview", "offer", "hired", "withdrawn",
}


def _is_discover_item(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    url = value.get("source_url")
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        valid_port = parsed.port in (None, 443)
    except ValueError:
        return False
    preview = value.get("preview_match")
    return (
        parsed.scheme == "https"
        and parsed.hostname == "trudvsem.ru"
        and parsed.path.startswith("/vacancy/card/")
        and not parsed.username
        and not parsed.password
        and valid_port
        and value.get("source") == "trudvsem"
        and all(isinstance(value.get(key), str) and bool(value[key]) for key in (
            "source_scope", "external_id", "title", "company",
        ))
        and all(value.get(key) is None or isinstance(value.get(key), str) for key in (
            "location", "description", "requirements_text", "salary_currency", "salary_text",
        ))
        and all(value.get(key) is None or type(value.get(key)) in (int, float) for key in (
            "salary_min", "salary_max",
        ))
        and value.get("workplace_type") in {"remote", "unknown", "onsite", "hybrid"}
        and type(value.get("already_saved_for_user")) is bool
        and isinstance(preview, dict)
        and type(preview.get("available")) is bool
        and (preview.get("score") is None or type(preview.get("score")) is int)
        and (preview.get("verdict") is None or isinstance(preview.get("verdict"), str))
    )


def _is_aware_iso_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _is_match_learning_summary(payload: dict[str, object]) -> bool:
    coverage = payload.get("snapshot_coverage")
    versions = payload.get("algorithm_versions")
    coverage_fields = {
        "applied_application_count", "captured_count", "unavailable_count",
        "legacy_without_snapshot_count", "invalid_anchor_count",
    }
    if (
        not _is_aware_iso_datetime(payload.get("as_of"))
        or not isinstance(coverage, dict)
        or set(coverage) != coverage_fields
        or not all(_is_nonnegative_int(coverage.get(field)) for field in coverage_fields)
        or not isinstance(versions, list)
    ):
        return False
    return all(_is_match_learning_version(version) for version in versions)


def _is_match_learning_version(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    buckets = value.get("score_buckets")
    if (
        not isinstance(value.get("algorithm_version"), str)
        or not value["algorithm_version"]
        or not all(_is_nonnegative_int(value.get(field)) for field in (
            "captured_count", "scored_count", "insufficient_data_count",
        ))
        or not isinstance(buckets, list)
        or [bucket.get("bucket") if isinstance(bucket, dict) else None for bucket in buckets]
        != list(_MATCH_LEARNING_BUCKETS)
    ):
        return False
    return all(_is_match_learning_bucket(bucket) for bucket in buckets)


def _is_match_learning_bucket(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    outcomes = value.get("outcomes")
    if (
        value.get("bucket") not in _MATCH_LEARNING_BUCKETS
        or not all(_is_nonnegative_int(value.get(field)) for field in (
            "score_min", "score_max", "application_count",
        ))
        or not isinstance(outcomes, dict)
        or set(outcomes) != _MATCH_LEARNING_OUTCOMES
    ):
        return False
    return all(_is_match_learning_conversion(conversion) for conversion in outcomes.values())


def _is_match_learning_conversion(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"numerator", "denominator", "percentage"}:
        return False
    percentage = value.get("percentage")
    return (
        _is_nonnegative_int(value.get("numerator"))
        and _is_nonnegative_int(value.get("denominator"))
        and (percentage is None or type(percentage) in (int, float))
    )


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


class BotApiClient(Protocol):
    async def discover_jobs(self, user_id: int, *, query: str, limit: int, offset: int) -> dict[str, object]: ...

    async def save_discovered_job(self, user_id: int, *, source: str, source_scope: str, external_id: str) -> dict[str, object]: ...

    async def list_work_experiences(self, user_id: int) -> list[dict[str, object]]: ...
    async def save_work_experience(self, user_id: int, payload: dict, entry_id: int | None = None) -> dict: ...
    async def delete_work_experience(self, user_id: int, entry_id: int) -> None: ...
    async def generate_cover_letter(self, user_id: int, application_id: int, language: str) -> dict[str, object]: ...

    async def create_or_get_user(self, telegram_user: User) -> int: ...

    async def save_application(self, user_id: int, source_url: str) -> dict[str, object]: ...

    async def get_application_match(self, user_id: int, application_id: int) -> dict[str, object]: ...

    async def list_applications(
        self, user_id: int, *, limit: int, offset: int, status: str | None = None,
        q: str | None = None, sort: str = "newest",
    ) -> dict[str, object]: ...

    async def get_application_learning_summary(self, user_id: int) -> dict[str, object]: ...

    async def get_application_match_learning_summary(self, user_id: int) -> dict[str, object]: ...

    async def list_application_follow_ups(
        self, user_id: int, *, limit: int, offset: int,
    ) -> dict[str, object]: ...

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

    async def list_profile_experience_facts(self, user_id: int) -> list[dict[str, object]]: ...

    async def create_profile_experience_fact(self, user_id: int, text: str) -> dict[str, object]: ...

    async def update_profile_experience_fact(self, user_id: int, fact_id: int, text: str) -> dict[str, object]: ...

    async def delete_profile_experience_fact(self, user_id: int, fact_id: int) -> None: ...

    async def normalize_profile_skills(self, skills: list[str]) -> list[str]: ...

    async def normalize_profile_languages(
        self, languages: list[dict[str, str]]
    ) -> list[dict[str, str]]: ...

    async def create_profile_draft_from_cv(
        self, user_id: int, *, filename: str, content_type: str, content: bytes
    ) -> dict[str, object]: ...


class JobHunterApiClient:
    async def discover_jobs(self, user_id: int, *, query: str, limit: int, offset: int) -> dict[str, object]:
        response = await self._client.get(
            f"/users/{user_id}/discover/jobs",
            params={"market_country": "RU", "query": query, "limit": limit, "offset": offset},
            timeout=35.0,
        )
        response.raise_for_status()
        payload = _json_object(response)
        items = payload.get("items")
        next_offset = payload.get("next_offset")
        if (
            not isinstance(items, list)
            or len(items) > limit
            or "next_offset" not in payload
            or type(payload.get("offset")) is not int
            or payload["offset"] != offset
            or (next_offset is not None and (type(next_offset) is not int or next_offset <= offset))
            or not all(_is_discover_item(item) for item in items)
        ):
            raise httpx.DecodingError("Invalid Discover search response", request=response.request)
        return payload

    async def save_discovered_job(self, user_id: int, *, source: str, source_scope: str, external_id: str) -> dict[str, object]:
        response = await self._client.post(
            f"/users/{user_id}/discover/jobs/save",
            json={"source": source, "source_scope": source_scope, "external_id": external_id},
            timeout=60.0,
        )
        response.raise_for_status()
        payload = _json_object(response)
        application, job = payload.get("application"), payload.get("job")
        if (
            not isinstance(application, dict)
            or not isinstance(job, dict)
            or type(application.get("id")) is not int
            or type(application.get("user_id")) is not int
            or application["user_id"] != user_id
            or type(application.get("job_id")) is not int
            or type(job.get("id")) is not int
            or application["job_id"] != job["id"]
            or job.get("source") != source
            or job.get("source_scope") != source_scope
            or job.get("external_id") != external_id
            or type(payload.get("application_created")) is not bool
            or type(payload.get("job_created")) is not bool
        ):
            raise httpx.DecodingError("Invalid Discover save response", request=response.request)
        return payload

    async def list_work_experiences(self, user_id: int) -> list[dict[str, object]]:
        response = await self._client.get(f"/users/{user_id}/profile/work-experiences")
        response.raise_for_status()
        items = _json_object(response).get("items")
        if not isinstance(items, list) or any(not isinstance(item, dict) or type(item.get("id")) is not int for item in items):
            raise httpx.DecodingError("Invalid work history response", request=response.request)
        return items

    async def save_work_experience(self, user_id: int, payload: dict, entry_id: int | None = None) -> dict:
        path = f"/users/{user_id}/profile/work-experiences"
        response = await self._client.post(path, json=payload) if entry_id is None else await self._client.put(f"{path}/{entry_id}", json=payload)
        response.raise_for_status()
        return _json_object(response)

    async def delete_work_experience(self, user_id: int, entry_id: int) -> None:
        response = await self._client.delete(f"/users/{user_id}/profile/work-experiences/{entry_id}")
        response.raise_for_status()

    @staticmethod
    def _experience_fact(payload: object, response: httpx.Response) -> dict[str, object]:
        if not isinstance(payload, dict) or type(payload.get("id")) is not int or not isinstance(payload.get("text"), str):
            raise httpx.DecodingError("API response has invalid experience fact shape", request=response.request)
        return payload

    async def generate_cover_letter(self, user_id: int, application_id: int, language: str) -> dict[str, object]:
        response = await self._client.post(f"/users/{user_id}/applications/{application_id}/cover-letter", json={"language": language})
        response.raise_for_status()
        payload = _json_object(response)
        if not isinstance(payload.get("letter"), str) or not payload["letter"].strip():
            raise httpx.DecodingError("Invalid cover letter response", request=response.request)
        return payload

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

    async def list_applications(
        self, user_id: int, *, limit: int, offset: int, status: str | None = None,
        q: str | None = None, sort: str = "newest",
    ) -> dict[str, object]:
        params: dict[str, str | int] = {"limit": limit, "offset": offset, "sort": sort}
        if status is not None:
            params["status"] = status
        if q is not None:
            params["q"] = q
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

    async def get_application_learning_summary(self, user_id: int) -> dict[str, object]:
        response = await self._client.get(f"/users/{user_id}/applications/learning-summary")
        response.raise_for_status()
        payload = _json_object(response)
        conversion_keys = {"numerator", "denominator", "percentage"}
        conversions = (
            payload.get("applied_to_recruiter_response"), payload.get("applied_to_interview"),
            payload.get("applied_to_offer"), payload.get("applied_to_hired"),
        )
        if (
            not all(type(payload.get(key)) is int and payload[key] >= 0 for key in (
                "total_applications", "applied_count", "recruiter_response_count", "interview_count",
                "offer_count", "hired_count", "withdrawn_count",
                "history_missing_count", "funnel_incomplete_count",
            ))
            or not _is_aware_iso_datetime(payload.get("as_of"))
            or not all(
                isinstance(conversion, dict)
                and conversion_keys == set(conversion)
                and type(conversion.get("numerator")) is int
                and type(conversion.get("denominator")) is int
                and (conversion.get("percentage") is None or type(conversion.get("percentage")) in (int, float))
                for conversion in conversions
            )
        ):
            raise httpx.DecodingError("API response has invalid learning summary shape", request=response.request)
        return payload

    async def get_application_match_learning_summary(self, user_id: int) -> dict[str, object]:
        response = await self._client.get(
            f"/users/{user_id}/applications/match-learning-summary"
        )
        response.raise_for_status()
        payload = _json_object(response)
        if not _is_match_learning_summary(payload):
            raise httpx.DecodingError(
                "API response has invalid match learning summary shape",
                request=response.request,
            )
        return payload

    async def list_application_follow_ups(
        self, user_id: int, *, limit: int, offset: int,
    ) -> dict[str, object]:
        response = await self._client.get(
            f"/users/{user_id}/applications/follow-ups",
            params={"limit": limit, "offset": offset},
        )
        response.raise_for_status()
        payload = _json_object(response)
        items = payload.get("items")
        if (
            not isinstance(items, list)
            or not isinstance(payload.get("has_next"), bool)
            or not all(
                isinstance(item, dict)
                and type(item.get("application_id")) is int
                and (item.get("title") is None or isinstance(item.get("title"), str))
                and (item.get("company") is None or isinstance(item.get("company"), str))
                and item.get("status") in _APPLICATION_STATUS_VALUES
                and isinstance(item.get("next_action"), str)
                and 1 <= len(item["next_action"]) <= 500
                and isinstance(item.get("next_action_due_on"), str)
                and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", item["next_action_due_on"]) is not None
                and item.get("due_state") in _FOLLOW_UP_DUE_STATES
                for item in items
            )
        ):
            raise httpx.DecodingError("API response has invalid follow-up queue shape", request=response.request)
        for item in items:
            try:
                date.fromisoformat(str(item["next_action_due_on"]))
            except ValueError as error:
                raise httpx.DecodingError(
                    "API response has invalid follow-up due date", request=response.request
                ) from error
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
            or application.get("status") not in _APPLICATION_STATUS_VALUES
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
            and item.get("status") in _APPLICATION_STATUS_VALUES
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
            or application.get("status") not in _APPLICATION_STATUS_VALUES
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

    async def list_profile_experience_facts(self, user_id: int) -> list[dict[str, object]]:
        response = await self._client.get(f"/users/{user_id}/profile/experience-facts")
        response.raise_for_status()
        items = _json_object(response).get("items")
        if not isinstance(items, list):
            raise httpx.DecodingError("API response has invalid experience facts shape", request=response.request)
        return [self._experience_fact(item, response) for item in items]

    async def create_profile_experience_fact(self, user_id: int, text: str) -> dict[str, object]:
        response = await self._client.post(f"/users/{user_id}/profile/experience-facts", json={"text": text})
        response.raise_for_status()
        return self._experience_fact(_json_object(response), response)

    async def update_profile_experience_fact(self, user_id: int, fact_id: int, text: str) -> dict[str, object]:
        response = await self._client.put(
            f"/users/{user_id}/profile/experience-facts/{fact_id}", json={"text": text}
        )
        response.raise_for_status()
        return self._experience_fact(_json_object(response), response)

    async def delete_profile_experience_fact(self, user_id: int, fact_id: int) -> None:
        response = await self._client.delete(f"/users/{user_id}/profile/experience-facts/{fact_id}")
        response.raise_for_status()

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
