"""The family voice-recording checklist: who says what, under which conditions.

One CSV row per take. The recorder (voice_recorder.py) walks the rows in order,
and saving a take fills that row's `file`, so the checklist is at once the
to-do list, the progress and the labels. Every person gets the same rows, so
the data is balanced by construction rather than by remembering.

What the takes are for (다음-개선-후보.md 1-1 hotword, 1-4 speaker):
  wake          "딸기님" alone - positives for the custom openWakeWord model.
  wake_command  "딸기님" -> chime -> a question, as the real loop runs: the
                first part is another positive, the question is what speaker
                identification sees (it embeds the post-wake utterance).
  free          a minute of free talk - speaker enrollment.
  ambient       the house with nobody calling - negatives and the threshold.

`split` holds a third of each kind out as test (never trained on), spread so
every condition has a test take; `session` spreads the rows over two days,
because a morning voice is not an evening voice.

Usage (on the RPi; voices are biometric data and stay on the host, not in git):
  uv run python -m app.voice_checklist ~/claw-data/voices/checklist.csv
"""

import csv
import itertools
import os
import sys

people = {"dad": "아빠", "mom": "엄마", "child": "어린이"}
distances = {"near": "1m", "mid": "3m", "far": "방 끝"}
loudnesses = {"normal": "보통", "soft": "작게", "loud": "크게"}
intonations = {"calling": "부르듯이", "asking": "묻듯이", "flat": "무심하게"}
backgrounds = {"quiet": "조용히", "music": "음악 틀고"}
kinds = {
    "wake": '"딸기님"만',
    "wake_command": '"딸기님" -> 차임 -> 질문',
    "free": "자유 발화",
    "ambient": "집 소리 (아무도 부르지 않음)",
}

wake_word = "딸기님"

# One per wake_command take; every person says all of them, in the same rows.
commands = [
    "오늘 날씨 어때?",
    "지금 몇 시야?",
    "내일 아침 일곱 시에 깨워 줘.",
    "재미있는 이야기 해 줘.",
    "신나는 노래 틀어 줘.",
    "십 분 뒤에 알려 줘.",
    "오늘 무슨 요일이야?",
    "공룡은 왜 사라졌어?",
    "저녁 메뉴 추천해 줘.",
    "우산 가져가야 할까?",
    "수수께끼 하나 내 줘.",
    "볼륨 좀 줄여 줘.",
    "하늘은 왜 파래?",
    "이번 주말에 뭐 할까?",
    "고마워, 이제 됐어.",
    "내 이름 기억해?",
    "끝말잇기 하자.",
    "오늘 기분 어때?",
]

free_topics = {
    ("near", "quiet"): "오늘 있었던 일 (1분쯤)",
    ("mid", "quiet"): "좋아하는 음식 이야기 (1분쯤)",
    ("near", "music"): "주말에 하고 싶은 것 (1분쯤)",
    ("mid", "music"): "아무 책이나 소리 내어 읽기 (1분쯤)",
}

ambient_minutes = 10

columns = [
    "id", "person", "kind", "distance", "loudness", "intonation", "background",
    "session", "split", "prompt",
    # Filled by the recorder when a take is kept; an empty `file` is a to-do.
    "file", "command_file", "seconds", "peak_dbfs", "recorded_at",
]

kind_order = list(kinds)
person_order = [*people, "house"]


def row(person: str, kind: str, number: int, distance: str, loudness: str, intonation: str,
        background: str, session: int, split: str, prompt: str) -> dict[str, str]:
    return {
        "id": f"{person}-{kind}-{number:03d}",
        "person": person,
        "kind": kind,
        "distance": distance,
        "loudness": loudness,
        "intonation": intonation,
        "background": background,
        "session": str(session),
        "split": split,
        "prompt": prompt,
        "file": "",
        "command_file": "",
        "seconds": "",
        "peak_dbfs": "",
        "recorded_at": "",
    }


def wake_rows(person: str) -> list[dict[str, str]]:
    """distance x loudness x background (18 conditions) x intonation (3) = 54.
    (condition + intonation) % 3 == 0 is test: one test take per condition and
    six per intonation. The session alternates over distance x loudness, not
    over background or intonation - otherwise a whole (background, intonation)
    pair lands on one day and the two days differ in more than the day."""
    conditions = list(itertools.product(distances, loudnesses, backgrounds))
    pairs = itertools.product(enumerate(conditions), enumerate(intonations))
    background_count = len(backgrounds)

    return [
        row(person, "wake", c * len(intonations) + i, distance, loudness, intonation, background,
            1 + (c // background_count + i + c % background_count) % 2,
            "test" if (c + i) % 3 == 0 else "train", wake_word)
        for (c, (distance, loudness, background)), (i, intonation) in pairs
    ]


def wake_command_rows(person: str) -> list[dict[str, str]]:
    """distance x background (6 conditions) x 3 takes = 18, one question each.
    The third take of each condition is test."""
    conditions = list(itertools.product(distances, backgrounds))
    takes = 3
    background_count = len(backgrounds)

    return [
        row(person, "wake_command", c * takes + t, distance, "normal", "calling", background,
            1 + (c // background_count + t + c % background_count) % 2, "test" if t == takes - 1 else "train",
            f"{wake_word} ... {commands[c * takes + t]}")
        for (c, (distance, background)), t in itertools.product(enumerate(conditions), range(takes))
    ]


def free_rows(person: str) -> list[dict[str, str]]:
    """All train: enrollment wants every second of it; speaker tests come from
    the wake_command test takes, which are what the device will really hear."""
    sessions = {("near", "quiet"): 1, ("mid", "quiet"): 2, ("near", "music"): 2, ("mid", "music"): 1}

    return [
        row(person, "free", n, distance, "normal", "", background, sessions[(distance, background)],
            "train", topic)
        for n, ((distance, background), topic) in enumerate(free_topics.items())
    ]


def ambient_rows() -> list[dict[str, str]]:
    takes = 3
    prompts = {
        "quiet": f"아무도 부르지 않는 평소 집 소리, {ambient_minutes}분",
        "music": f"음악만 틀어 둔 집 소리, {ambient_minutes}분",
    }

    return [
        row("house", "ambient", b * takes + t, "", "", "", background, 1 + (b * takes + t) % 2,
            "test" if t == takes - 1 else "train", prompts[background])
        for (b, background), t in itertools.product(enumerate(backgrounds), range(takes))
    ]


def operator_order(item: dict[str, str]) -> tuple:
    """Grouped so the operator moves people and the music as rarely as possible:
    one sitting is one session of one person, music off then on, near to far."""
    return (
        item["session"],
        person_order.index(item["person"]),
        kind_order.index(item["kind"]),
        list(backgrounds).index(item["background"]),
        list(distances).index(item["distance"]) if item["distance"] else 0,
        item["id"],
    )


def build_rows() -> list[dict[str, str]]:
    per_person = [
        made
        for person in people
        for made in [*wake_rows(person), *wake_command_rows(person), *free_rows(person)]
    ]

    return sorted([*per_person, *ambient_rows()], key=operator_order)


def read_rows(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def write_rows(path: str, rows: list[dict[str, str]]) -> None:
    """Atomically: the recorder rewrites the file after every kept take, and a
    crash mid-write must not lose the labels of everything recorded so far.
    utf-8-sig so Excel shows the Korean."""
    temporary = f"{path}.tmp"

    with open(temporary, "w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    os.replace(temporary, path)


def summary(rows: list[dict[str, str]]) -> str:
    keys = sorted({(item["session"], item["person"]) for item in rows},
                  key=lambda key: (key[0], person_order.index(key[1])))
    lines = [
        f"  {session}차 {people.get(person, '집'):<4} "
        f"{sum(1 for item in rows if (item['session'], item['person']) == (session, person) and item['file'])}"
        f"/{sum(1 for item in rows if (item['session'], item['person']) == (session, person))}"
        for session, person in keys
    ]
    done = sum(1 for item in rows if item["file"])

    return "\n".join([f"녹음 {done}/{len(rows)}", *lines])


def main() -> None:
    path = os.path.expanduser(sys.argv[1])

    if os.path.exists(path):
        # Recorded labels live in this file; regenerating would orphan them.
        raise FileExistsError(f"{path} already exists - it holds the recording progress; not overwriting")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows = build_rows()
    write_rows(path, rows)
    print(f"wrote {path}\n{summary(rows)}")


if __name__ == "__main__":
    main()
