"""
progress_filter.py — turn Timeshift's raw output bytes into display events.

No GTK here, so the monitor window, the Backup tab and the unit tests
can all share it.

Timeshift redraws its progress with carriage returns: a fragment like
"42.17% complete (00:03:12 remaining)\\r", no newline. A terminal
overwrites the line each time; a plain text widget would show every
fragment as its own line (a long backup logs thousands). This module
splits the stream into fragments and classifies each one as a progress
update (which the display should REPLACE) or a normal line (which is
always appended and never overwritten).

Feed it BYTES, not text. Python's text-mode file reading turns "\\r"
into "\\n" (universal newlines) before any caller sees it, which erases
the very signal this module needs.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

# Matches every real fragment in a 670 KB production log, including:
# space-padded single digits ("  9.99%"), values over 100, "(??? remaining)"
# and the "HH:MM:SS  " prefix the Backup tab adds to its own log lines.
PROGRESS_RE = re.compile(
    r"^(?:\d\d:\d\d:\d\d +)?\s*(\d+(?:\.\d+)?)% complete(?: \((.*?) remaining\))?\s*$"
)

PROGRESS = "progress"
LINE = "line"


class Event(NamedTuple):
    kind: str  # PROGRESS or LINE
    text: str
    percent: Optional[float] = None
    remaining: Optional[str] = None


def classify(text: str) -> Event:
    """Classify one complete fragment/line (terminator already removed).
    Dropping erase fragments needs the terminator, so ProgressFilter does it."""
    m = PROGRESS_RE.match(text)
    if m:
        return Event(PROGRESS, text.strip(), float(m.group(1)), m.group(2))
    return Event(LINE, text)


class ProgressFilter:
    """Incremental byte-stream splitter. feed() as often as you like, in
    chunks of any size; the events produced are identical however the
    same bytes are split.

    Anything not yet terminated stays held back (the whole fragment, not
    just a trailing "\\r": a "\\r" at the very end of the data might be the
    first half of a "\\r\\n", so it waits for the next byte). Call
    finish() at the end to flush what is left.
    """

    def __init__(self) -> None:
        self._buf = b""

    def reset(self) -> None:
        """Forget held-back bytes (use when the source was truncated)."""
        self._buf = b""

    def feed(self, data: bytes) -> list[Event]:
        self._buf += data
        events: list[Event] = []
        buf = self._buf
        start = 0
        n = len(buf)
        while True:
            i = start
            while i < n and buf[i] != 0x0D and buf[i] != 0x0A:
                i += 1
            if i >= n:
                break  # unterminated tail: hold it back
            if buf[i] == 0x0A:
                ev = self._make(buf[start:i], bare_cr=False)
                start = i + 1
            else:  # carriage return
                if i + 1 >= n:
                    break  # could be the first half of \r\n: wait
                if buf[i + 1] == 0x0A:
                    ev = self._make(buf[start:i], bare_cr=False)
                    start = i + 2
                else:
                    ev = self._make(buf[start:i], bare_cr=True)
                    start = i + 1
            if ev is not None:
                events.append(ev)
        self._buf = buf[start:]
        return events

    def finish(self) -> list[Event]:
        """Flush the held-back tail as if the stream ended here."""
        rest, self._buf = self._buf, b""
        if rest.endswith(b"\r"):
            rest = rest[:-1]
            ev = self._make(rest, bare_cr=True)
        elif rest:
            ev = self._make(rest, bare_cr=False)
        else:
            ev = None
        return [ev] if ev is not None else []

    @staticmethod
    def _make(frag: bytes, bare_cr: bool) -> Optional[Event]:
        text = frag.decode("utf-8", errors="replace")
        if bare_cr and not text.strip():
            return None  # erase fragment (see classify)
        return classify(text)
