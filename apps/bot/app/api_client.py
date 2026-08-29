import httpx


class JobHunterApiClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=40.0)

    async def create_or_get_user(self, telegram_user: object) -> int:
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
        return response.json()["id"]

    async def save_application(self, user_id: int, source_url: str) -> dict[str, object]:
        response = await self._client.post(
            f"/users/{user_id}/applications", json={"source_url": source_url}
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
