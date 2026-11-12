"""The brain home's history: every change to it becomes a git commit, by the host.

2026-11-12 decision (뇌-상호작용-로드맵.md 3-1): the brain may read the whole
device and rewrite everything in its home - its own operating rules
(CLAUDE.md), host.json, persona, memory, schedule, system.md - except
.claude/settings.json, which it can only read. Instead of forbidding, the host
keeps a record the brain cannot forget to write:

- at startup, one commit of whatever changed while the gateway was down (the
  operator's hand edits, a copied template);
- after every brain turn, one commit if the turn changed anything, with the
  words that started the turn in the message - the "why";
- after the scheduler fires or rejects a file, one commit for the move.

The repository lives in the home itself and never leaves the device (the RPi
has no push credentials). The operator reads it with `git -C ~/claw-home log -p`
and undoes a change with `git revert`.

session.json is runtime state rewritten on every turn, so it is ignored.
"""

import subprocess
import sys
from pathlib import Path

ignored = ["session.json"]
# Who made the commit, so `git log` tells the brain's edits from the host's.
authors = {
    "brain": ("claw-brain", "brain@claw.local"),
    "host": ("claw-host", "host@claw.local"),
    "scheduler": ("claw-scheduler", "scheduler@claw.local"),
}
subject_limit = 80


def log(message: str) -> None:
    print(f"[home_history] {message}", file=sys.stderr, flush=True)


def git(home: Path, arguments: list[str], author: str) -> str:
    name, email = authors[author]
    completed = subprocess.run(
        ["git", "-C", str(home), "-c", f"user.name={name}", "-c", f"user.email={email}", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    return completed.stdout


def is_repository(home: Path) -> bool:
    return (home / ".git").is_dir()


def changed_paths(home: Path) -> list[str]:
    # --untracked-files=all lists new files one by one, not their directory.
    lines = git(home, ["status", "--porcelain", "--untracked-files=all"], "host").splitlines()

    return [line[3:] for line in lines if line.strip() != ""]


def subject(text: str) -> str:
    first = " ".join(text.split())

    return first if len(first) <= subject_limit else first[: subject_limit - 1] + "…"


def record(home: Path, summary: str, author: str) -> list[str]:
    """Commit everything that changed in the home. The changed paths, or []."""
    paths = changed_paths(home)

    if paths == []:
        return []

    body = "\n".join(paths)
    git(home, ["add", "--all"], author)
    git(home, ["commit", "--quiet", "--message", f"{subject(summary)}\n\n{body}"], author)
    log(f"{author}: {len(paths)} change(s) - {subject(summary)}")

    return paths


def ensure(home: Path) -> None:
    """Make the home a repository if it is not one, then snapshot it.

    A failure here is a configuration error (no git on the host) and is meant
    to stop the gateway: without the record, the brain's self-edits would be
    invisible.
    """
    if not is_repository(home):
        git(home, ["init", "--quiet"], "host")
        (home / ".gitignore").write_text("".join(f"{name}\n" for name in ignored), encoding="utf-8")
        log(f"initialised {home}")

    record(home, "기동 스냅샷: 게이트웨이가 꺼져 있던 동안의 변경(관리자 편집 등)", "host")


def record_quietly(home: Path, summary: str, author: str) -> list[str]:
    """record() for the turn and scheduler paths: a failed commit is logged
    loudly and dropped. The record is not worth a lost voice turn; ensure() at
    startup has already proven git works, so this only covers a transient
    failure (a full disk, a lock left by a crash)."""
    try:
        return record(home, summary, author)
    except subprocess.CalledProcessError as error:
        log(f"commit failed ({error.returncode}): {error.stderr.strip()[:300]}")
        return []
