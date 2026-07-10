"""Opt-in performance counters (--stats): per-thumbnail FPS, capture time, RSS."""
import os, time
from . import platform  # noqa: F401 — env/gi setup must run first
from gi.repository import GLib

# Pass --stats to print performance counters (per-thumbnail achieved FPS,
# average capture time, and process RSS) to stderr every 5 seconds.
STATS_ENABLED = "--stats" in os.sys.argv


class _Stats:
    """Lightweight performance counters, enabled with the --stats flag.

    Each live thumbnail registers itself and records how long every
    capture+scale cycle took. A GLib timer prints a summary every
    REPORT_INTERVAL_MS. Zero cost when --stats is not passed (STATS is None
    and all hooks are skipped).
    """

    REPORT_INTERVAL_MS = 5000

    def __init__(self):
        self._per_thumb = {}   # id(thumbnail) -> counter dict
        self._started = False

    def register(self, thumb):
        """Create (or reset) the counter record for one thumbnail."""
        rec = {
            "thumb": thumb,
            "frames": 0,               # successful captures since last report
            "capture_ms_total": 0.0,   # summed capture+scale time
            "last_report": time.monotonic(),
        }
        self._per_thumb[id(thumb)] = rec
        return rec

    def unregister(self, thumb):
        self._per_thumb.pop(id(thumb), None)

    @staticmethod
    def record(rec, capture_ms):
        rec["frames"] += 1
        rec["capture_ms_total"] += capture_ms

    @staticmethod
    def _rss_mb():
        """Resident memory of this process in MB, read from /proc (Linux only)."""
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024.0  # kB -> MB
        except Exception:
            pass
        return -1.0

    def start(self):
        if self._started:
            return
        self._started = True
        GLib.timeout_add(self.REPORT_INTERVAL_MS, self._report)

    def _report(self):
        now = time.monotonic()
        lines = []
        for key, rec in list(self._per_thumb.items()):
            thumb = rec["thumb"]
            try:
                name = (thumb.wnck_window.get_name() or "?")[:30]
                target = int(thumb.config.settings.get("refresh_fps", 10))
            except Exception:
                # Window went away — drop the stale record.
                self._per_thumb.pop(key, None)
                continue
            elapsed = now - rec["last_report"]
            if elapsed <= 0:
                continue
            fps = rec["frames"] / elapsed
            avg = (rec["capture_ms_total"] / rec["frames"]) if rec["frames"] else 0.0
            lines.append(
                f"  {name:<30} target={target:>2}fps actual={fps:4.1f}fps "
                f"capture_avg={avg:5.1f}ms frames={rec['frames']}"
            )
            rec["frames"] = 0
            rec["capture_ms_total"] = 0.0
            rec["last_report"] = now
        print(f"[stats] rss={self._rss_mb():.0f}MB thumbnails={len(self._per_thumb)}",
              file=os.sys.stderr)
        for ln in lines:
            print(ln, file=os.sys.stderr)
        return True  # keep the timer repeating


STATS = _Stats() if STATS_ENABLED else None
