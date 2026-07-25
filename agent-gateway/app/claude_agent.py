import asyncio
import json
import os


class claude_agent:
    """Headless Claude Code brain for the stand-in agent gateway.

    Each request runs `claude` in print mode as a subprocess and reuses its
    stored (subscription) auth, so no API key is needed. Configuration via
    environment:

    - CLAUDE_BIN            (default "claude"; on Windows set the full claude.cmd path)
    - CLAUDE_MODEL          (default "sonnet")
    - CLAUDE_ALLOWED_TOOLS  (default ""; empty keeps it conversational / no tools)
    - CLAUDE_SYSTEM_PROMPT  (default: a concise spoken-Korean assistant)
    - CLAUDE_WORKDIR        (default: process cwd; the directory claude runs in)

    The session id returned by claude is kept so the next turn resumes the same
    conversation, giving multi-turn voice context.

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
        self.system_prompt = os.environ.get("CLAUDE_SYSTEM_PROMPT", self.default_system_prompt)
        self.workdir = os.environ.get("CLAUDE_WORKDIR", "")
        self._session_id: str | None = None

    def child_env(self) -> dict[str, str]:
        # Strip the markers that tell claude it is running inside another Claude
        # Code session; without this a nested `claude -p` is blocked. Harmless
        # when the gateway runs in a plain terminal (the markers aren't set).
        env = dict(os.environ)

        for key in list(env):
            if key == "CLAUDECODE" or key.startswith("CLAUDE_CODE_"):
                del env[key]

        return env

    async def respond(self, message: str) -> str:
        argv = [
            self.bin,
            "-p",
            message,
            "--output-format",
            "json",
            "--model",
            self.model,
            "--append-system-prompt",
            self.system_prompt,
        ]

        if self.allowed_tools != "":
            argv += ["--allowedTools", self.allowed_tools]

        if self._session_id is not None:
            argv += ["--resume", self._session_id]

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workdir or None,
            env=self.child_env(),
        )

        stdout_bytes, stderr_bytes = await process.communicate()

        if process.returncode != 0:
            detail = stderr_bytes.decode(errors="replace").strip()[:500]
            raise RuntimeError(f"claude exited with {process.returncode}: {detail}")

        payload = json.loads(stdout_bytes.decode(errors="replace"))
        # Keep the session so the next turn continues the same conversation.
        self._session_id = payload.get("session_id", self._session_id)

        return payload["result"]
