"""The brain host: one resident Claude Agent SDK session for the device.

Day-10 design (day-10.md, brain-memo.txt): the brain layer is *settings*, not
code. Everything about who the brain is lives in its home directory, which the
operator edits with Claude Code itself:

    <BRAIN_HOME>/
      CLAUDE.md             operating rules, speech rules, file conventions (brain may edit)
      system.md             map of the whole device: what it can and cannot do (brain may edit)
      persona.md, memory.md who it is, what it remembers (brain edits by voice)
      host.json             optional host knobs, e.g. the wait notice (brain may edit)
      requests.md           what the brain asks the operator for (brain writes)
      system-prompt.md      optional; replaces the minimal default system prompt
      .claude/settings.json model, permission mode, allow/deny rules - the brain
                            can read it, never write it (the one hard layer)
      schedule/*.json       things to say or do later (scheduler.py fires them)
      session.json          runtime: the session id, so a restart resumes
      .git/                 home_history.py: every change committed by the host

This module only hosts: it keeps the session alive across turns (no per-turn
process spawn), stamps each message with the current time (the model has no
clock), translates the SDK's messages into the gateway's NDJSON lines, turns
a client disconnect into an interrupt rather than a kill, and commits what a
turn changed in the home.
"""

import asyncio
import contextlib
import dataclasses
import datetime
import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from app import home_history
from app.claude_agent import agent_line
from app.sentences import sentence_splitter

weekday_names = ["월", "화", "수", "목", "금", "토", "일"]

# What the brain gets when the home has no system-prompt.md. Deliberately tiny:
# the home's CLAUDE.md is where the operator writes who the brain is. The second
# sentence is what the SDK docs ask every custom prompt to carry.
default_system_prompt = (
    "너는 이 기기의 두뇌다. 사용자의 말이 음성으로 들어오고 네 답은 음성으로 나간다. "
    "애플리케이션이 system reminder로 맥락(프로젝트 지침, 파일 변경)을 넣는다. "
    "그것은 사용자의 말이 아니라 애플리케이션의 맥락으로 취급한다."
)


def stamp(message: str, now: datetime.datetime) -> str:
    """Prefix the user's words with the clock, so "10분 후에" is computable."""
    return f"[지금 {now:%Y-%m-%d} ({weekday_names[now.weekday()]}) {now:%H:%M}]\n{message}"


def reply(text: str) -> agent_line:
    return agent_line(type="reply", text=text, kind=None)


class turn_translator:
    """SDK messages of one turn -> NDJSON lines, sentence by sentence.

    With include_partial_messages on, text arrives twice: as StreamEvent deltas
    while the model writes, and again inside the completed AssistantMessage. The
    deltas are what gets spoken (a sentence goes out as soon as it ends); the
    AssistantMessage then contributes only tool-use notices. If no delta was
    seen for a message (the CLI decided not to stream it), its text blocks are
    spoken from the message itself, so nothing is ever lost.
    """

    def __init__(self) -> None:
        self.splitter = sentence_splitter()
        self.streamed = False

    def on(self, message: object) -> list[agent_line]:
        if isinstance(message, StreamEvent):
            # Subagent output is not the device's voice.
            if message.parent_tool_use_id is not None:
                return []

            event = message.event

            if event.get("type") == "content_block_delta" and event["delta"].get("type") == "text_delta":
                self.streamed = True

                return [reply(sentence) for sentence in self.splitter.feed(event["delta"]["text"])]

            if event.get("type") == "content_block_stop":
                rest = self.splitter.flush()

                return [reply(rest)] if rest is not None else []

            return []

        if isinstance(message, AssistantMessage):
            lines: list[agent_line] = []

            for block in message.content:
                if isinstance(block, TextBlock) and not self.streamed:
                    text = block.text.strip()

                    if text != "":
                        lines.append(reply(text))
                elif isinstance(block, ToolUseBlock):
                    lines.append(agent_line(type="notice", text=block.name, kind="tool_use"))
                # Thinking and tool results: neither speech nor progress.

            # The next assistant message (after a tool call) starts clean.
            self.streamed = False

            return lines

        if isinstance(message, ResultMessage):
            if message.is_error:
                return [agent_line(type="error", text=f"claude: {message.subtype}", kind=None)]

            return [agent_line(type="done", text="", kind=None)]

        return []


def translate_message(message: object) -> list[agent_line]:
    """One SDK message in isolation (no streaming state). Kept for tests."""
    return turn_translator().on(message)


@dataclasses.dataclass(frozen=True)
class wait_notice:
    """Spoken when the brain has said nothing for `after_seconds` into a turn.

    The model cannot announce that it is about to think for a long time - it only
    speaks once the thinking is done - so the host fills the silence. Configured
    by the operator in <home>/host.json; absent means never.
    """

    after_seconds: float
    text: str


def read_wait_notice(home: Path) -> wait_notice | None:
    host_file = home / "host.json"

    if not host_file.exists():
        return None

    config = json.loads(host_file.read_text(encoding="utf-8"))

    if "wait_notice" not in config:
        return None

    return wait_notice(after_seconds=float(config["wait_notice_seconds"]), text=config["wait_notice"])


async def relay(
    messages: AsyncIterator[object],
    translator: turn_translator,
    notice: wait_notice | None,
) -> AsyncIterator[agent_line]:
    """Translate a turn's messages, inserting the wait notice if the first
    reply is late. The SDK stream is read by a helper task so the wait can time
    out without cancelling the generator underneath it."""
    queue: asyncio.Queue[object | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for message in messages:
                await queue.put(message)
        finally:
            await queue.put(None)

    pumping = asyncio.create_task(pump())
    loop = asyncio.get_running_loop()
    started = loop.time()
    replied = False
    notified = False

    try:
        while True:
            timeout = None

            if notice is not None and not replied and not notified:
                timeout = max(notice.after_seconds - (loop.time() - started), 0.0)

            try:
                message = await asyncio.wait_for(queue.get(), timeout)
            except asyncio.TimeoutError:
                notified = True
                yield reply(notice.text)
                continue

            if message is None:
                return

            for line in translator.on(message):
                if line.type == "reply":
                    replied = True

                yield line
    finally:
        pumping.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await pumping


def read_session_id(session_file: Path) -> str | None:
    if not session_file.exists():
        return None

    return json.loads(session_file.read_text(encoding="utf-8"))["session_id"]


def write_session_id(session_file: Path, session_id: str) -> None:
    session_file.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")


def log(message: str) -> None:
    # stderr, not the wire: the webhook contract must not carry our telemetry.
    print(f"[sdk_agent] {message}", file=sys.stderr, flush=True)


def claude_config_path() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")

    return Path(config_dir) / ".claude.json" if config_dir != "" else Path.home() / ".claude.json"


def trust_keys(home: Path) -> list[str]:
    """How ~/.claude.json names this directory under "projects".

    The CLI writes forward slashes; on Windows the drive letter's case has been
    seen both ways, so both are accepted.
    """
    text = str(home).replace("\\", "/")

    return list(dict.fromkeys([text, text[:1].lower() + text[1:], text[:1].upper() + text[1:]]))


def is_trusted(config: dict, home: Path) -> bool:
    projects = config.get("projects", {})

    return any(projects.get(key, {}).get("hasTrustDialogAccepted") is True for key in trust_keys(home))


def require_trusted_home(home: Path) -> None:
    """Day-10 live test: an untrusted home silently loses its settings.json
    allow rules ("Ignoring 5 permissions.allow entries ... this workspace has
    not been trusted"), and the brain then cannot write memory.md or schedule/.
    Loud at startup instead of a brain that apologises on every turn."""
    config_path = claude_config_path()
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}

    if not is_trusted(config, home):
        raise RuntimeError(
            f"BRAIN_HOME is not a trusted Claude Code workspace: {home}. "
            f"Run `claude` once in that directory and accept the trust dialog, "
            f'or set projects["{trust_keys(home)[0]}"].hasTrustDialogAccepted = true in {config_path}'
        )


class sdk_agent:
    """Same interface as claude_agent (stream / respond), different host.

    Configuration via environment:

    - BRAIN_HOME    (required) the brain's home directory, see the module docstring
    - CLAUDE_BIN    (optional) path to the claude executable; default: on PATH
    - CLAUDE_MODEL  (optional) overrides the home's settings.json model
    """

    def __init__(self) -> None:
        self.home = Path(os.environ["BRAIN_HOME"]).resolve()
        self.cli_path = os.environ.get("CLAUDE_BIN", "") or None
        self.model = os.environ.get("CLAUDE_MODEL", "") or None
        self.session_file = self.home / "session.json"
        self.wait_notice = read_wait_notice(self.home)
        self.client: ClaudeSDKClient | None = None
        # One turn at a time: the session is a single conversation.
        self.lock = asyncio.Lock()
        self.strip_nesting_markers()

        if not (self.home / "CLAUDE.md").exists():
            raise FileNotFoundError(f"BRAIN_HOME has no CLAUDE.md: {self.home}")

        require_trusted_home(self.home)
        home_history.ensure(self.home)

    def strip_nesting_markers(self) -> None:
        # The CLI refuses to nest inside another Claude Code session; these are
        # the markers it checks. Harmless from a plain terminal.
        for key in list(os.environ):
            if key == "CLAUDECODE" or key.startswith("CLAUDE_CODE_"):
                del os.environ[key]

    def system_prompt(self) -> str | dict:
        prompt_file = self.home / "system-prompt.md"

        if prompt_file.exists():
            return {"type": "file", "path": str(prompt_file)}

        return default_system_prompt

    def options(self, resume: str | None) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=str(self.home),
            cli_path=self.cli_path,
            model=self.model,
            system_prompt=self.system_prompt(),
            # The home's .claude/settings.json and CLAUDE.md; never the operator's
            # own ~/.claude - the brain must not inherit a developer's tool rules.
            setting_sources=["project"],
            resume=resume,
            # Sentence-level early output: text deltas while the model writes.
            include_partial_messages=True,
            stderr=lambda line: log(f"cli: {line.rstrip()[:300]}"),
        )

    async def connect(self) -> ClaudeSDKClient:
        if self.client is not None:
            return self.client

        resume = read_session_id(self.session_file)
        client = ClaudeSDKClient(options=self.options(resume))

        try:
            await client.connect()
        except Exception as error:  # noqa: BLE001 - a stale session id is the expected failure
            if resume is None:
                raise

            log(f"resume of session {resume} failed ({error}); starting a new session")
            self.session_file.unlink()
            client = ClaudeSDKClient(options=self.options(None))
            await client.connect()

        log(f"connected (resume={resume})")
        self.client = client

        return client

    async def stream(self, message: str) -> AsyncIterator[agent_line]:
        async with self.lock:
            client = await self.connect()
            await client.query(stamp(message, datetime.datetime.now()))

            try:
                async for line in relay(self.observe(client.receive_response()), turn_translator(), self.wait_notice):
                    yield line
            except asyncio.CancelledError:
                # The backend dropped us (barge-in). Stop the turn, keep the session.
                log("turn cancelled by the client; interrupting")
                await asyncio.shield(self.interrupt_and_drain(client))
                raise
            finally:
                # Whatever the turn changed in the home, finished or interrupted,
                # with its words as the reason. Off the event loop: git is a
                # subprocess.
                await asyncio.shield(
                    asyncio.to_thread(home_history.record_quietly, self.home, f"턴: {message}", "brain")
                )

    async def observe(self, messages: AsyncIterator[object]) -> AsyncIterator[object]:
        """Pass-through that logs the init and result and persists the session id."""
        started = asyncio.get_running_loop().time()
        deltas = 0
        first_delta_ms = None

        async for sdk_message in messages:
            if isinstance(sdk_message, StreamEvent) and sdk_message.event.get("type") == "content_block_delta":
                deltas += 1

                if first_delta_ms is None:
                    first_delta_ms = int((asyncio.get_running_loop().time() - started) * 1000)

            if isinstance(sdk_message, SystemMessage) and sdk_message.subtype == "init":
                log(
                    f"init session={sdk_message.data.get('session_id')} "
                    f"model={sdk_message.data.get('model')} "
                    f"tools={len(sdk_message.data.get('tools', []))}"
                )

            if isinstance(sdk_message, ResultMessage):
                write_session_id(self.session_file, sdk_message.session_id)
                log(
                    f"turns={sdk_message.num_turns} duration_ms={sdk_message.duration_ms} "
                    f"api_ms={sdk_message.duration_api_ms} cost_usd={sdk_message.total_cost_usd} "
                    f"deltas={deltas} first_delta_ms={first_delta_ms}"
                )

            yield sdk_message

    async def interrupt_and_drain(self, client: ClaudeSDKClient) -> None:
        await client.interrupt()

        # interrupt() does not clear the buffer: the interrupted turn has to be
        # read to its result before the next query (SDK docs).
        async for sdk_message in client.receive_response():
            if isinstance(sdk_message, ResultMessage):
                write_session_id(self.session_file, sdk_message.session_id)

    async def respond(self, message: str) -> str:
        lines = [line async for line in self.stream(message)]
        errors = [line.text for line in lines if line.type == "error"]

        if errors != []:
            raise RuntimeError(errors[0])

        return "\n".join(line.text for line in lines if line.type == "reply")
