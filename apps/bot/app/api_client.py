import httpx


class JobHunterApiClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def create_or_get_user(self, telegram_user: object) -> None:
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

    async def close(self) -> None:
        await self._client.aclose()
