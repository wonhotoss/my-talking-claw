"""Sentence boundaries in streamed text, so the first sentence can be spoken
while the model is still writing the rest (day-10 "문장 단위 선출력").

Only punctuation ends a sentence: `. ! ? …` and their full-width forms, followed
by whitespace or the end of the text. "3.5" and "v2.0" are not boundaries. The
brain's CLAUDE.md asks for plain sentences, so this is enough; anything left
over when the block closes is flushed as its own piece.
"""

import re

# A terminator that is not part of a number, followed by whitespace. The lookahead
# for whitespace means a terminator at the very end of the buffer waits for more
# text - the next delta may continue the sentence ("..." or "?!").
boundary = re.compile(r"(?<!\d)[.!?…。！？]+(?=\s)")


class sentence_splitter:
    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, text: str) -> list[str]:
        """Add streamed text; return every sentence completed by it."""
        self.buffer += text
        sentences: list[str] = []

        while True:
            match = boundary.search(self.buffer)

            if match is None:
                return sentences

            sentence = self.buffer[: match.end()].strip()
            self.buffer = self.buffer[match.end() :]

            if sentence != "":
                sentences.append(sentence)

    def flush(self) -> str | None:
        """Whatever is left when the block ends; None if nothing."""
        rest = self.buffer.strip()
        self.buffer = ""

        return rest if rest != "" else None
