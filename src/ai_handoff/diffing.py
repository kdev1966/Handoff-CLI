"""Word-level differences between two texts."""

import re
from difflib import SequenceMatcher
from typing import Literal

Operation = Literal["equal", "delete", "insert"]

# Words, runs of whitespace, and single punctuation marks are the units compared.
_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text)


def word_diff(old: str, new: str) -> list[tuple[Operation, str]]:
    """Return (operation, text) chunks that turn old into new."""
    before, after = tokenize(old), tokenize(new)
    chunks: list[tuple[Operation, str]] = []
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            chunks.append(("equal", "".join(before[i1:i2])))
            continue
        if i2 > i1:
            chunks.append(("delete", "".join(before[i1:i2])))
        if j2 > j1:
            chunks.append(("insert", "".join(after[j1:j2])))
    return chunks
