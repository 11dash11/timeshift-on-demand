"""
log_display.py — render ProgressFilter events into a Gtk.TextBuffer.

Progress events REPLACE the current progress line; every other line is
appended and ends the run of progress updates, so an error message is
never overwritten. Also gives the log view "sticky bottom" scrolling:
it follows new output only while the user is at the bottom, and no
longer jumps back to wherever they last clicked.

The buffer logic needs no display, so tests run it headless.
"""

from __future__ import annotations

from typing import Iterable, Optional

from gi.repository import Gtk

from progress_filter import PROGRESS, Event


class LogDisplay:
    def __init__(self, buffer: Gtk.TextBuffer, scroller: Optional[Gtk.ScrolledWindow] = None):
        self.buffer = buffer
        # Start of the live progress line (left gravity: stays put when
        # replacement text is inserted at it). None when no run is active.
        # While a run is active, the buffer ends with the progress text and
        # NO trailing newline; a normal line terminates it first.
        self._mark: Optional[Gtk.TextMark] = None
        self._stick = True
        self._adj = None
        if scroller is not None:
            self._adj = scroller.get_vadjustment()
            self._adj.connect("value-changed", self._on_value_changed)
            self._adj.connect("changed", self._on_range_changed)

    # -- public API ---------------------------------------------------

    def apply(self, events: Iterable[Event]) -> None:
        for ev in events:
            if ev.kind == PROGRESS:
                self._progress(ev.text)
            else:
                self.add_line(ev.text)

    def add_line(self, text: str) -> None:
        """Append a normal line; it ends any progress run."""
        self.end_run()
        self.buffer.insert(self.buffer.get_end_iter(), text + "\n")

    def end_run(self) -> None:
        """Terminate the live progress line (if any) so the next output
        starts on a fresh line."""
        if self._mark is not None:
            self.buffer.insert(self.buffer.get_end_iter(), "\n")
            self.buffer.delete_mark(self._mark)
            self._mark = None

    # -- internals ----------------------------------------------------

    def _progress(self, text: str) -> None:
        if self._mark is None:
            end = self.buffer.get_end_iter()
            if not end.starts_line():
                self.buffer.insert(end, "\n")
                end = self.buffer.get_end_iter()
            self._mark = self.buffer.create_mark(None, end, True)
        else:
            self.buffer.delete(
                self.buffer.get_iter_at_mark(self._mark), self.buffer.get_end_iter()
            )
        self.buffer.insert(self.buffer.get_end_iter(), text)

    def _on_value_changed(self, adj) -> None:
        self._stick = adj.get_value() + adj.get_page_size() >= adj.get_upper() - 2

    def _on_range_changed(self, adj) -> None:
        if self._stick:
            adj.set_value(adj.get_upper() - adj.get_page_size())
