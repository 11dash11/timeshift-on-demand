"""Unit tests for src/progress_filter.py and src/log_display.py (no display needed).

Run: python3 tests/test_progress.py   (tests/run-tests.sh calls this too)
Set REAL_LOG=/path/to/backup.log to also run the benchmark on a real log.
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from progress_filter import LINE, PROGRESS, ProgressFilter, classify  # noqa: E402


def run(chunks):
    f = ProgressFilter()
    out = []
    for c in chunks:
        out += f.feed(c)
    out += f.finish()
    return out


def split_randomly(data, rng):
    chunks, i = [], 0
    while i < len(data):
        n = rng.choice([1, 1, 2, 3, 7, 50, 500])
        chunks.append(data[i:i + n])
        i += n
    return chunks


SAMPLE = (
    b"Mon Nov  5: [INFO] start\n"
    b"Creating new backup...\n"
    b"  9.99% complete (00:12:01 remaining)\r"
    b" 10.00% complete (??? remaining)\r"
    b"    \r"
    b"100.00% complete (00:00:00 remaining)\r"
    b"101.64% complete (00:00:00 remaining)\r\n"
    b"16:58:27  100.00% complete (00:00:00 remaining)\n"
    b"rsync returned an error \xe2\x80\x94 caf\xc3\xa9\n"
    b"Snapshot saved successfully (187s)\n"
)


class FilterTests(unittest.TestCase):
    def test_shapes(self):
        ev = run([SAMPLE])
        kinds = [e.kind for e in ev]
        self.assertEqual(kinds, [LINE, LINE, PROGRESS, PROGRESS, PROGRESS, PROGRESS,
                                 PROGRESS, LINE, LINE])
        self.assertEqual(ev[2].percent, 9.99)
        self.assertEqual(ev[2].remaining, "00:12:01")
        self.assertEqual(ev[3].remaining, "???")
        self.assertEqual(ev[5].percent, 101.64)
        self.assertEqual(ev[6].text, "16:58:27  100.00% complete (00:00:00 remaining)")
        self.assertIn("café", ev[7].text)

    def test_erase_fragments_dropped_but_blank_lines_kept(self):
        self.assertEqual(run([b"   \r"]), [])
        self.assertEqual([e.text for e in run([b"a\n\nb\n"])], ["a", "", "b"])

    def test_error_text_is_never_progress(self):
        ev = run([b"Failed to create snapshot: 50% complete nonsense\n"])
        self.assertEqual(ev[0].kind, LINE)

    def test_crlf_is_one_terminator_even_when_split(self):
        for chunks in ([b"x\r\ny\n"], [b"x\r", b"\ny\n"], [b"x", b"\r", b"\n", b"y", b"\n"]):
            self.assertEqual([e.text for e in run(chunks)], ["x", "y"])

    def test_trailing_cr_is_held_back_then_flushed(self):
        f = ProgressFilter()
        self.assertEqual(f.feed(b"50.00% complete (00:01:00 remaining)\r"), [])
        self.assertEqual(f.feed(b"51.00% comp"),
                         [classify("50.00% complete (00:01:00 remaining)")])
        self.assertEqual(f.feed(b"lete\r"), [])  # still waiting: \r might start \r\n
        self.assertEqual([e.percent for e in f.finish()], [51.0])

    def test_split_utf8(self):
        data = "naïve — ✓\n".encode()
        for i in range(1, len(data)):
            self.assertEqual(run([data[:i], data[i:]])[0].text, "naïve — ✓")

    def test_any_split_gives_identical_events(self):
        base = run([SAMPLE * 3])
        rng = random.Random(1)
        for _ in range(200):
            self.assertEqual(run(split_randomly(SAMPLE * 3, rng)), base)

    def test_reset_drops_held_bytes(self):
        f = ProgressFilter()
        f.feed(b"half a fra")
        f.reset()
        self.assertEqual(f.finish(), [])


@unittest.skipUnless(os.environ.get("REAL_LOG"), "set REAL_LOG to benchmark")
class RealLogTests(unittest.TestCase):
    def test_real_log(self):
        with open(os.environ["REAL_LOG"], "rb") as fh:
            data = fh.read()
        base = run([data])
        rng = random.Random(7)
        for _ in range(20):
            self.assertEqual(run(split_randomly(data, rng)), base)
        frags = [e for e in base if e.kind == PROGRESS]
        leftovers = [e.text for e in base if e.kind == LINE and "% complete" in e.text]
        print(f"\nreal log: {len(base)} events, {len(frags)} progress, "
              f"unmatched '% complete' lines: {len(leftovers)}")
        self.assertEqual(leftovers, [])


def _gtk():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        return Gtk
    except Exception:
        return None


@unittest.skipUnless(_gtk(), "PyGObject/Gtk3 not available")
class DisplayTests(unittest.TestCase):
    def setUp(self):
        from log_display import LogDisplay
        self.Gtk = _gtk()
        self.buf = self.Gtk.TextBuffer()
        self.d = LogDisplay(self.buf)

    def text(self):
        return self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False)

    def test_progress_replaces_in_place(self):
        self.d.apply(run([b"Start\n1.00% complete (x remaining)\r2.00% complete (y remaining)\r"
                          b"3.00% complete (z remaining)\r"]))
        self.assertEqual(self.text(), "Start\n3.00% complete (z remaining)")

    def test_normal_line_ends_run_and_is_never_overwritten(self):
        self.d.apply(run([b"1.00% complete (a remaining)\r2.00% complete (b remaining)\r"
                          b"ERROR: disk full\n3.00% complete (c remaining)\r4.00% complete (d remaining)\r"
                          b"Done\n"]))
        self.assertEqual(self.text(),
                         "2.00% complete (b remaining)\nERROR: disk full\n"
                         "4.00% complete (d remaining)\nDone\n")

    def test_notice_after_progress_starts_new_line(self):
        self.d.apply(run([b"5.00% complete (a remaining)\r"]))
        self.d.add_line("=== SUCCESS ===")
        self.assertEqual(self.text(), "5.00% complete (a remaining)\n=== SUCCESS ===\n")

    def test_collapse_ratio_on_sample(self):
        self.d.apply(run([SAMPLE]))
        # 9 raw events; the 5 consecutive progress fragments collapse to 1 line
        self.assertEqual(self.text().count("\n"), 5)

    @unittest.skipUnless(os.environ.get("REAL_LOG"), "set REAL_LOG to benchmark")
    def test_real_log_collapses(self):
        with open(os.environ["REAL_LOG"], "rb") as fh:
            data = fh.read()
        self.d.apply(run([data]))
        shown = self.text().count("\n") + 1
        print(f"\nreal log: displayed lines {shown}")
        self.assertLess(shown, 2000)
        self.assertNotIn("% complete (", "\n".join(
            l for l in self.text().splitlines() if "ERROR" in l or "rsync" in l))


if __name__ == "__main__":
    unittest.main()
