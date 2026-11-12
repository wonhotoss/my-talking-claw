"""Fires the brain's scheduled items: files the brain wrote under <home>/schedule/.

The brain has no timer of its own, so "10분 후에 불러줘" ends as a file:

    schedule/2026-11-01T15-14-abc.json
    {"at": "2026-11-01T15:14:00", "say": "부르셨죠, 10분 지났어요."}
    {"at": "2026-11-01T15:14:00", "prompt": "사용자에게 약 먹을 시간이라고 알려줘"}

`say` is spoken verbatim through POST /api/push (no agent hop); `prompt` runs an
unsolicited brain turn whose answer is spoken. A naive `at` is local time.
Fired files move to schedule/done/, unreadable ones to schedule/failed/ - the one
place in this stack where a bad input is skipped loudly instead of crashing: the
author of the file is the model, and a typo there must not take the device down.
"""

import asyncio
import datetime
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class schedule_entry:
    path: Path
    at: datetime.datetime
    say: str | None
    prompt: str | None


def local_timezone() -> datetime.tzinfo:
    return datetime.datetime.now().astimezone().tzinfo


def parse_entry(path: Path, text: str) -> schedule_entry:
    payload = json.loads(text)
    at = datetime.datetime.fromisoformat(payload["at"])

    if at.tzinfo is None:
        at = at.replace(tzinfo=local_timezone())

    say = payload.get("say")
    prompt = payload.get("prompt")

    if (say is None) == (prompt is None):
        raise ValueError("exactly one of say or prompt must be set")

    return schedule_entry(path=path, at=at, say=say, prompt=prompt)


def push_body(entry: schedule_entry) -> dict:
    if entry.say is not None:
        return {"source": "agent", "prompt": None, "utterances": [entry.say]}

    return {"source": "agent", "prompt": entry.prompt, "utterances": None}


def log(message: str) -> None:
    print(f"[scheduler] {message}", file=sys.stderr, flush=True)


class scheduler:
    def __init__(
        self,
        home: Path,
        push_url: str,
        push_token: str,
        http: httpx.AsyncClient,
        record: Callable[[str], object],
    ) -> None:
        self.directory = home / "schedule"
        self.push_url = push_url
        self.push_token = push_token
        self.http = http
        # Called with a summary after every move, so the home's history shows
        # what fired and what was rejected (home_history.py).
        self.record = record

    def move(self, path: Path, bucket: str) -> None:
        target = self.directory / bucket
        target.mkdir(parents=True, exist_ok=True)
        path.rename(target / path.name)
        self.record(f"스케줄러: {path.name} → {bucket}/")

    async def fire(self, entry: schedule_entry) -> None:
        response = await self.http.post(
            self.push_url,
            json=push_body(entry),
            headers={"X-Push-Token": self.push_token},
            timeout=10.0,
        )
        response.raise_for_status()

    async def tick(self, now: datetime.datetime) -> None:
        """One pass: parse every pending file, fire the due ones."""
        if not self.directory.exists():
            return

        for path in sorted(self.directory.glob("*.json")):
            try:
                entry = parse_entry(path, path.read_text(encoding="utf-8"))
            except (ValueError, KeyError, TypeError) as error:
                log(f"{path.name}: unreadable ({error}); moved to failed/")
                self.move(path, "failed")
                continue

            if entry.at > now:
                continue

            try:
                await self.fire(entry)
            except httpx.HTTPError as error:
                # The backend may be restarting; the file stays and the next tick retries.
                log(f"{path.name}: push failed ({error}); will retry")
                continue

            log(f"{path.name}: fired ({'say' if entry.say is not None else 'prompt'})")
            self.move(path, "done")

    async def run(self, interval_seconds: float) -> None:
        while True:
            await self.tick(datetime.datetime.now(local_timezone()))
            await asyncio.sleep(interval_seconds)
