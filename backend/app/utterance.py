import re


# Split on sentence-final punctuation with the delimiter kept. This mirrors
# tts/app/engines/piper.py::sentence_pattern deliberately rather than sharing it:
# the two services are separately deployable, and they answer different
# questions. Piper decides synthesis piece boundaries *inside* one /speak call;
# here we decide when a separate /speak call is made at all, and therefore when
# sound starts.
sentence_pattern = re.compile(r"(?<=[.!?…])\s+")


def split_utterances(text: str) -> list[str]:
    """Agent text -> the units the device speaks, one /tts/speak call each.

    Text with no sentence-final punctuation stays whole rather than being cut on
    a guess. An empty result for non-empty input is impossible; the caller treats
    an empty agent reply as a failed turn rather than a silent one.
    """
    return [
        stripped
        for piece in sentence_pattern.split(text.strip())
        if (stripped := piece.strip()) != ""
    ]
