"""The recording studio: the voice checklist on the RPi screen, one prompt at a time.

The terminal recorder (voice_recorder.py) makes the operator read every prompt
aloud; here the person being recorded reads it off the screen - big "딸기님",
the conditions as chips, a status band that follows the cues ("말하세요",
"질문하세요") and a live level meter. The same page opens on a phone at
http://<rpi>:8300, so a parent can press the buttons while a child talks.

It owns the ReSpeaker, so the stack must be down (scripts/rpi-record.sh checks).
Any checklist CSV in voice_checklist.py's columns works, so a later recording
campaign is a new CSV, not a new app.

  uv run python -m app.voice_studio ~/claw-data/voices/checklist.csv [--port 8300]

One worker thread does everything that touches audio, in the order the buttons
were pressed; the HTTP threads only queue actions and read the state. "stop"
is the exception: it must reach a recording that is already running.
"""

import argparse
import json
import os
import queue
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol

import numpy as np

from app import voice_checklist as checklist
from app.voice_recorder import level_report, pending, save, stops_on_request, take, whole_take

page_path = os.path.join(os.path.dirname(__file__), "voice_studio.html")

# Which buttons mean something in which phase. Anything else (a double tap that
# queued twice, say) is logged and dropped when its turn comes.
allowed_actions = {
    "choose": {"choose"},
    "prompt": {"record", "skip", "home"},
    "recording": set(),
    "review": {"keep", "play", "record", "skip", "home"},
    "problem": {"record", "skip", "home"},
    "finished": {"home"},
}


class device_like(Protocol):
    live_dbfs: float

    def record(self, item: dict[str, str], stop: threading.Event, announce) -> tuple[take | np.ndarray, int]: ...

    def playback(self, recorded: take | np.ndarray) -> None: ...


def log(message: str) -> None:
    print(f"[studio] {message}", file=sys.stderr, flush=True)


def block_label(session: str, person: str) -> str:
    return f"{session}차 {checklist.people.get(person, '집')}"


def chips(item: dict[str, str]) -> list[str]:
    labels = [
        checklist.distances.get(item["distance"], ""),
        checklist.loudnesses.get(item["loudness"], ""),
        checklist.intonations.get(item["intonation"], ""),
        checklist.backgrounds.get(item["background"], ""),
    ]

    return [label for label in labels if label]


class studio:
    def __init__(self, path: str, device: device_like) -> None:
        self.path = path
        self.root = os.path.dirname(path)
        self.device = device
        self.rows = checklist.read_rows(path)
        # (session, person) being recorded; None on the start screen.
        self.block: tuple[str, str] | None = None
        # Skipped in this sitting: still to do in the CSV, but not next now.
        self.skipped: set[str] = set()
        self.phase = "choose"
        # What the cues say while recording; the level report or the problem after.
        self.status = ""
        self.message = ""
        self.recorded: take | np.ndarray | None = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.actions: queue.Queue[dict] = queue.Queue()

    def block_rows(self) -> list[dict[str, str]]:
        session, person = self.block
        return [item for item in self.rows if item["session"] == session and item["person"] == person]

    @property
    def current(self) -> dict[str, str] | None:
        if self.block is None:
            return None

        session, person = self.block
        waiting = [item for item in pending(self.rows, person, session) if item["id"] not in self.skipped]

        return waiting[0] if waiting else None

    def next_phase(self) -> str:
        return "prompt" if self.current is not None else "finished"

    def announce(self, text: str) -> None:
        with self.lock:
            self.status = text

    def process(self, action: dict) -> None:
        name = action["action"]

        if name not in allowed_actions[self.phase]:
            log(f"ignored {name!r} in phase {self.phase!r}")
            return

        if name == "choose":
            with self.lock:
                self.block = (action["session"], action["person"])
                self.skipped = set()
                self.message = ""
                self.phase = self.next_phase()

        elif name == "home":
            with self.lock:
                self.block = None
                self.recorded = None
                self.message = ""
                self.phase = "choose"

        elif name == "skip":
            with self.lock:
                self.skipped.add(self.current["id"])
                self.recorded = None
                self.message = ""
                self.phase = self.next_phase()

        elif name == "record":
            self.record()

        elif name == "play":
            self.device.playback(self.recorded)

        elif name == "keep":
            saved = save(self.root, self.current, self.recorded)
            rows = [saved if other["id"] == saved["id"] else other for other in self.rows]
            checklist.write_rows(self.path, rows)

            with self.lock:
                self.rows = rows
                self.recorded = None
                self.message = f"저장했어요: {saved['file']}"
                self.phase = self.next_phase()

    def record(self) -> None:
        item = self.current

        with self.lock:
            self.stop.clear()
            self.recorded = None
            self.status = "삑 소리가 나면 말하세요"
            self.message = ""
            self.phase = "recording"

        recorded, lost = self.device.record(item, self.stop, self.announce)

        with self.lock:
            self.recorded = recorded

            if isinstance(recorded, take) and recorded.problem is not None:
                self.message = recorded.problem
                self.phase = "problem"
            else:
                warnings = [f"녹음 중 프레임 {lost}개 유실 - 다시 하는 게 좋아요"] if lost > 0 else []
                self.message = " / ".join([level_report(whole_take(recorded))[1], *warnings])
                self.phase = "review"

    def state(self) -> dict:
        with self.lock:
            item = self.current
            blocks = sorted(
                {(row["session"], row["person"]) for row in self.rows},
                key=lambda key: (key[0], checklist.person_order.index(key[1])),
            )
            in_block = self.block_rows() if self.block is not None else []

            return {
                "phase": self.phase,
                "status": self.status,
                "message": self.message,
                "live_dbfs": self.device.live_dbfs if self.phase == "recording" else None,
                "block": None if self.block is None else {
                    "label": block_label(*self.block),
                    "done": sum(1 for row in in_block if row["file"]),
                    "total": len(in_block),
                },
                "item": None if item is None else {
                    "id": item["id"],
                    "kind": checklist.kinds[item["kind"]],
                    # "딸기님 ... 질문" -> the word, then the question under it.
                    "prompt": item["prompt"].split(" ... "),
                    "chips": chips(item),
                    "stops_on_request": stops_on_request(item),
                },
                "blocks": [
                    {
                        "session": session,
                        "person": person,
                        "label": block_label(session, person),
                        "done": sum(1 for row in self.rows if (row["session"], row["person"]) == (session, person) and row["file"]),
                        "total": sum(1 for row in self.rows if (row["session"], row["person"]) == (session, person)),
                    }
                    for session, person in blocks
                ],
            }

    def work_forever(self) -> None:
        """A failure here must not leave a page that looks alive but records
        nothing: print it and take the whole process down."""
        try:
            while True:
                self.process(self.actions.get())
        except BaseException:  # noqa: BLE001 - crash loudly, whatever it was
            traceback.print_exc()
            os._exit(1)


def make_handler(app: studio) -> type[BaseHTTPRequestHandler]:
    with open(page_path, "rb") as source:
        page = source.read()

    class handler(BaseHTTPRequestHandler):
        def reply(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/":
                self.reply(200, "text/html; charset=utf-8", page)
            elif self.path == "/state":
                self.reply(200, "application/json", json.dumps(app.state(), ensure_ascii=False).encode("utf-8"))
            else:
                self.reply(404, "text/plain", b"not found")

        def do_POST(self) -> None:
            if self.path != "/action":
                self.reply(404, "text/plain", b"not found")
                return

            action = json.loads(self.rfile.read(int(self.headers["Content-Length"])))

            if action["action"] == "stop":
                app.stop.set()
            else:
                app.actions.put(action)

            self.reply(204, "text/plain", b"")

        def log_message(self, format: str, *args) -> None:
            """The page polls /state several times a second; keep the log for real events."""

    return handler


def main() -> None:
    from app.voice_recorder import recording_device

    parser = argparse.ArgumentParser(description="녹음 스튜디오: 체크리스트를 화면에 한 줄씩")
    parser.add_argument("checklist", help="voice_checklist.py가 만든 CSV")
    parser.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = 폰에서도 조작")
    parser.add_argument("--port", type=int, default=8300)
    arguments = parser.parse_args()

    device = recording_device()
    # Opens the capture stream now: a ReSpeaker still held by the audio loop
    # fails here, at start, not at the first take.
    next(device.frames)
    app = studio(os.path.expanduser(arguments.checklist), device)
    threading.Thread(target=app.work_forever, daemon=True).start()
    log(f"ready: http://{arguments.host}:{arguments.port}/  ({arguments.checklist})")
    ThreadingHTTPServer((arguments.host, arguments.port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()
