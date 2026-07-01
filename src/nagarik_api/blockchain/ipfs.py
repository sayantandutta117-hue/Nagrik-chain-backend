from __future__ import annotations

import json

import httpx


class IPFSError(RuntimeError):
    pass


class IPFSClient:
    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")

    async def add_bytes(self, data: bytes, filename: str) -> str:
        files = {"file": (filename, data)}
        result = await self._post_json("/api/v0/add", files=files, params={"pin": "true"})
        cid = result.get("Hash")
        if not cid:
            raise IPFSError(f"Kubo add response did not include a CID: {result}")
        await self.pin(cid)
        if not await self.verify_cid(cid):
            raise IPFSError(f"Kubo could not verify CID after add: {cid}")
        return cid

    async def pin(self, cid: str) -> None:
        result = await self._post_json("/api/v0/pin/add", params={"arg": cid})
        pins = result.get("Pins") or []
        if cid not in pins:
            raise IPFSError(f"CID was not pinned by Kubo: {cid}")

    async def verify_cid(self, cid: str) -> bool:
        result = await self._post_json("/api/v0/block/stat", params={"arg": cid})
        return int(result.get("Size", 0)) > 0 and bool(result.get("Key"))

    async def retrieve(self, cid: str) -> bytes:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.api_url}/api/v0/cat", params={"arg": cid})
            response.raise_for_status()
            return response.content

    async def _post_json(self, path: str, **kwargs) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.api_url}{path}", **kwargs)
            response.raise_for_status()
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise IPFSError(f"Kubo returned non-JSON response for {path}") from exc
