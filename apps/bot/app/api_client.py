from typing import Protocol

import httpx
from aiogram.types import User


class BotApiClient(Protocol):
    async def create_or_get_user(self, telegram_user: User) -> int: ...

    async def save_application(self, user_id: int, source_url: str) -> dict[str, object]: ...

    async def put_user_profile(self, user_id: int, profile: dict[str, object]) -> dict[str, object]: ...


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

    async def put_user_profile(self, user_id: int, profile: dict[str, object]) -> dict[str, object]:
        response = await self._client.put(f"/users/{user_id}/profile", json=profile)
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
