import asyncio
import logging
from typing import Dict, Any

import aiohttp

logger = logging.getLogger(__name__)


class WhatsAppSender:
    def __init__(self, session: aiohttp.ClientSession, api_url: str, auth_token: str, recipient: str):
        self._session = session
        self._api_url = api_url
        self._auth_token = auth_token
        self._recipient = recipient

    async def send_items(self, messages: list[str], spacing_seconds: float = 0.7):
        logger.info(f"WhatsAppSender received {len(messages)} messages to send.")
        for message in messages:
            await self._send_one(message)
            await asyncio.sleep(spacing_seconds)

    async def _send_one(self, body: str) -> None:
        payload: Dict[str, Any] = {
            "typing_time": 0,
            "to": self._recipient,
            "body": body,
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {self._auth_token}",
        }
        async with self._session.post(self._api_url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                txt = await resp.text()
                logger.error(f"Failed to send WhatsApp message: {txt}")
            else:
                logger.info("WhatsApp message sent successfully") 