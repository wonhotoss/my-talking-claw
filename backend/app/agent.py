import os

import httpx


class agent_client:
    """Client for the nullclaw-compatible agent gateway.

    Pairs once to obtain a bearer token, then posts user messages to /webhook.
    The same contract points at the stand-in gateway during development and at a
    real nullclaw gateway in production — only the URL changes. Config via
    environment:

    - AGENT_GATEWAY_URL   (default http://127.0.0.1:3000 — nullclaw's default)
    - AGENT_PAIRING_CODE  (default 000000; nullclaw's one-time startup code)
    - AGENT_BEARER_TOKEN  (optional; skip pairing when a token is provided)
    """

    def __init__(self) -> None:
        self.gateway_url = os.environ.get("AGENT_GATEWAY_URL", "http://127.0.0.1:3000").rstrip("/")
        self.pairing_code = os.environ.get("AGENT_PAIRING_CODE", "000000")
        self._token = os.environ.get("AGENT_BEARER_TOKEN", "") or None

    async def _pair(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            f"{self.gateway_url}/pair",
            headers={"X-Pairing-Code": self.pairing_code},
        )
        response.raise_for_status()

        return response.json()["token"]

    async def _post_webhook(self, client: httpx.AsyncClient, message: str) -> httpx.Response:
        return await client.post(
            f"{self.gateway_url}/webhook",
            headers={"Authorization": f"Bearer {self._token}"},
            json={"message": message},
        )

    async def respond(self, message: str) -> str:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if self._token is None:
                self._token = await self._pair(client)

            response = await self._post_webhook(client, message)

            if response.status_code == 401:
                # Token expired or invalid — pair again once and retry.
                self._token = await self._pair(client)
                response = await self._post_webhook(client, message)

            response.raise_for_status()

            return response.json()["reply"]


agent = agent_client()
