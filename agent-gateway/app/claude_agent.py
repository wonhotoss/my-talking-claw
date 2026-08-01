import asyncio
import collections
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal


agent_line_type = Literal["reply", "notice", "done", "error"]

# asyncio's StreamReader defaults to a 64KiB line limit. A single assistant
# message, or a tool_result carrying a file, goes past that and raises
# "Separator is not found, and chunk exceed the limit" - which would never show
# up on a short test prompt, only on the first real "조사해줘".
stdout_line_limit = 4 * 1024 * 1024

# How long a terminated claude gets to exit before it is killed outright.
kill_grace_seconds = 2.0

# Only the tail of stderr is ever reported, so only the tail is kept.
stderr_tail_bytes = 500


@dataclass(frozen=True)
class agent_line:
    """One line of the gateway's NDJSON webhook response.

    - reply  : speakable text the agent produced
    - notice : progress worth showing but not saying (tool use)
    - done   : the turn finished normally
    - error  : the turn failed. Arrives mid-stream, because by the time a
               streaming response fails the HTTP status is already 200.
    """

    type: agent_line_type
    text: str
    kind: str | None


def translate_block(block: dict) -> list[agent_line]:
    """One content block of an assistant message -> zero or more lines."""
    block_type = block.get("type")

    if block_type == "text":
        text = block["text"].strip()

        return [agent_line(type="reply", text=text, kind=None)] if text != "" else []

    if block_type == "tool_use":
        return [agent_line(type="notice", text=block["name"], kind="tool_use")]

    # thinking / redacted_thinking, and anything the CLI adds later: neither
    # speech nor progress. Thinking blocks in particular must never be spoken.
    return []


def translate_line(payload: dict) -> list[agent_line]:
    """One `claude --output-format stream-json` line -> zero or more lines.

    Pure, so the whole translation is testable against captured fixtures with
    no subprocess. The measured line types are `system` (subtypes init and
    thinking_tokens), `assistant`, `user` (tool_result), `rate_limit_event` and
    `result`; only the last two of those carry anything we forward.
    """
    line_type = payload.get("type")

    if line_type == "assistant":
        return [
            line
            for block in payload["message"]["content"]
            for line in translate_block(block)
        ]

    if line_type == "result":
        if payload.get("is_error"):
            subtype = payload.get("subtype") or "unknown error"

            return [agent_line(type="error", text=f"claude: {subtype}", kind=None)]

        # `result["result"]` repeats the final assistant text verbatim. Emitting
        # it as well would make the device say its last sentence twice.
        return [agent_line(type="done", text="", kind=None)]

    # system / user / rate_limit_event, and unknown future types: dropped. Not
    # a crash on purpose - the CLI grows line types between versions
    # (rate_limit_event and system/thinking_tokens are undocumented), and a
    # gateway that dies on an unrecognised line would take the device with it.
    return []


class claude_agent:
    """Headless Claude Code brain for the stand-in agent gateway.

    Each request runs `claude` in print mode as a subprocess and reuses its
    stored (subscription) auth, so no API key is needed. Configuration via
    environment:

    - CLAUDE_BIN            (default "claude"; on Windows set the full claude.exe path)
    - CLAUDE_MODEL          (default "sonnet")
    - CLAUDE_ALLOWED_TOOLS  (default ""; see the warning below)
    - CLAUDE_SYSTEM_PROMPT  (default: a concise spoken-Korean assistant)
    - CLAUDE_WORKDIR        (default: process cwd; the directory claude runs in)
    - DEVICE_PUSH_URL       (optional; the backend's /api/push)
    - DEVICE_PUSH_TOKEN     (optional; must match the backend's PUSH_TOKEN)

    When both DEVICE_PUSH_* are set the system prompt gains the outbound channel
    that lets the agent make the device speak on its own initiative. Scheduling
    is the agent's responsibility, not ours - the device only exposes "say this".
    A one-shot `claude -p` cannot hold a timer, so the stand-in has to delegate to
    the OS (at / cron / schtasks), which needs CLAUDE_ALLOWED_TOOLS opened up. A
    real nullclaw is resident and needs none of that.

    The session id is generated here rather than read back from claude's output,
    so it is loggable before the first process even starts (the /proc/<pid>/cmdline
    step of the latency runbook needs it on turn one). The first turn creates the
    session with --session-id, later turns resume it, giving multi-turn context.

    WARNING: CLAUDE_ALLOWED_TOOLS does not restrict anything. Measured against
    CLI 2.1.220, `--allowedTools` is an auto-approve list, not a sandbox: with it
    empty, `claude -p` still reports all 33 tools available and will happily run
    Read/Bash with zero permission denials. Use --disallowedTools to actually
    deny. Do not read an empty value here as "conversational / no tools".

    Note: launching this from inside another Claude Code session nests the CLI
    and may block; run the gateway from a plain terminal for live use.
    """

    default_system_prompt = (
        "너는 음성 비서다. 사용자의 말을 듣고 한국어로 짧고 자연스럽게, 말하듯이 대답한다. "
        "마크다운, 목록 기호, 코드블록은 쓰지 않는다."
    )

    def __init__(self) -> None:
        self.bin = os.environ.get("CLAUDE_BIN", "claude")
        self.model = os.environ.get("CLAUDE_MODEL", "sonnet")
        self.allowed_tools = os.environ.get("CLAUDE_ALLOWED_TOOLS", "")
        self.push_url = os.environ.get("DEVICE_PUSH_URL", "")
        self.push_token = os.environ.get("DEVICE_PUSH_TOKEN", "")
        self.system_prompt = self.build_system_prompt()
        self.workdir = os.environ.get("CLAUDE_WORKDIR", "")
        self.session_id = str(uuid.uuid4())
        # Whether claude has created the session yet, which decides
        # --session-id vs --resume. Not derivable: it records that an external
        # side effect happened.
        self.session_created = False
        # Two `claude --resume <same id>` processes write the same session
        # transcript file. That is a data race inside the CLI's own store, not
        # just on our fields, and it corrupts rather than raises.
        self.lock = asyncio.Lock()

    def build_system_prompt(self) -> str:
        """The persona, plus the outbound channel when one is configured.

        Appended to whatever base prompt is in use rather than replacing it: the
        channel is a capability, not a personality.
        """
        base = os.environ.get("CLAUDE_SYSTEM_PROMPT", self.default_system_prompt)

        if self.push_url == "" or self.push_token == "":
            return base

        return base + (
            " 나중에 사용자에게 전할 말이 있으면 기기가 직접 말하게 할 수 있다. "
            f"이 명령을 그때 실행하면 된다: curl -X POST {self.push_url} "
            f"-H 'X-Push-Token: {self.push_token}' -H 'Content-Type: application/json' "
            '-d \'{"source":"agent","prompt":null,"utterances":["할 말"]}\'. '
            "예약은 네 책임이다 — at이나 cron 같은 OS 기능으로 예약해 두고 그때 위 명령이 실행되게 해라. "
            "지금 턴에서 하는 대답은 이미 사용자에게 들리므로 이 채널로 다시 보내지 마라. "
            "특히 자기 턴이 도는 동안 호출하면 그 턴 자체가 취소된다."
        )

    def child_env(self) -> dict[str, str]:
        # Strip the markers that tell claude it is running inside another Claude
        # Code session; without this a nested `claude -p` is blocked. Harmless
        # when the gateway runs in a plain terminal (the markers aren't set).
        env = dict(os.environ)

        for key in list(env):
            if key == "CLAUDECODE" or key.startswith("CLAUDE_CODE_"):
                del env[key]

        return env

    def build_argv(self, message: str) -> list[str]:
        argv = [
            self.bin,
            "-p",
            message,
            "--output-format",
            "stream-json",
            # Mandatory: "When using --print, --output-format=stream-json
            # requires --verbose". It does not add noise to stdout.
            "--verbose",
            "--model",
            self.model,
            "--append-system-prompt",
            self.system_prompt,
        ]

        if self.allowed_tools != "":
            argv += ["--allowedTools", self.allowed_tools]

        if self.session_created:
            return argv + ["--resume", self.session_id]

        return argv + ["--session-id", self.session_id]

    async def stop(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return

        # On Windows, terminating the npm .cmd shim leaves the real node process
        # alive and burning tokens, so CLAUDE_BIN must point at claude.exe. That
        # was already the advice for latency; with cancellation it is a
        # correctness requirement.
        process.terminate()

        try:
            await asyncio.wait_for(process.wait(), kill_grace_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def stream(self, message: str) -> AsyncIterator[agent_line]:
        async with self.lock:
            process = await asyncio.create_subprocess_exec(
                *self.build_argv(message),
                # Without this claude inherits the server's stdin and waits 3s
                # for piped input that never comes ("Warning: no stdin data
                # received in 3s"), costing 2-3s on every single turn.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workdir or None,
                env=self.child_env(),
                limit=stdout_line_limit,
            )
            # Drained concurrently: a full stderr pipe would deadlock a process
            # we are reading stdout from one line at a time.
            stderr_tail: collections.deque[bytes] = collections.deque(maxlen=64)
            stderr_task = asyncio.create_task(self.drain(process.stderr, stderr_tail))

            try:
                saw_result = False

                async for raw in process.stdout:
                    line = raw.decode(errors="replace").strip()

                    if line == "":
                        continue

                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        yield agent_line(
                            type="error",
                            text=f"claude wrote a non-json line: {line[:200]}",
                            kind=None,
                        )
                        return

                    if payload.get("type") == "system" and payload.get("subtype") == "init":
                        # The session exists from here on, so the next turn resumes.
                        self.session_created = True
                        self.log_init(payload)

                    if payload.get("type") == "result":
                        saw_result = True
                        self.log_result(payload)

                    for translated in translate_line(payload):
                        yield translated

                if not saw_result:
                    # Truncated output or a crash. Loud, because a stream that
                    # stops without done or error would otherwise read as success.
                    await process.wait()
                    detail = b"".join(stderr_tail).decode(errors="replace").strip()[:500]

                    yield agent_line(
                        type="error",
                        text=f"claude exited with {process.returncode}: {detail}",
                        kind=None,
                    )
            finally:
                stderr_task.cancel()
                await self.stop(process)

    async def drain(self, stream: asyncio.StreamReader, into: collections.deque[bytes]) -> None:
        async for chunk in stream:
            # Truncated on the way in: the 4MiB line limit applies to stderr too,
            # so keeping whole lines could pin hundreds of megabytes to build the
            # 500-character tail this is used for.
            into.append(chunk[-stderr_tail_bytes:])

    def log_init(self, payload: dict) -> None:
        # stderr, not the wire: nullclaw's contract must not carry our telemetry.
        print(
            f"[claude_agent] session={payload.get('session_id')} model={payload.get('model')} "
            f"permission_mode={payload.get('permissionMode')} tools={len(payload.get('tools', []))}",
            file=sys.stderr,
            flush=True,
        )

    def log_result(self, payload: dict) -> None:
        print(
            f"[claude_agent] turns={payload.get('num_turns')} "
            f"duration_ms={payload.get('duration_ms')} api_ms={payload.get('duration_api_ms')} "
            f"ttft_ms={payload.get('ttft_ms')} cost_usd={payload.get('total_cost_usd')}",
            file=sys.stderr,
            flush=True,
        )

    async def respond(self, message: str) -> str:
        """Collect a whole turn into one reply, for the non-streaming contract.

        Built on stream() so both webhook shapes share one code path. Unlike the
        old implementation this joins *every* assistant text block rather than
        returning only claude's final `result` field - a tool-using turn used to
        silently drop its own intermediate speech.
        """
        lines = [line async for line in self.stream(message)]
        errors = [line.text for line in lines if line.type == "error"]

        if errors != []:
            raise RuntimeError(errors[0])

        return "\n".join(line.text for line in lines if line.type == "reply")
