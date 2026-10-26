"""Date windows over the ingest horizon, split when a query returns too many items."""

from dataclasses import dataclass
from datetime import datetime, timedelta

HORIZON = timedelta(days=90)
WINDOW = timedelta(days=7)
MIN_WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class Window:
    start: datetime  # inclusive
    end: datetime  # exclusive

    def can_split(self) -> bool:
        return self.end - self.start >= 2 * MIN_WINDOW

    def split(self) -> tuple["Window", "Window"]:
        if not self.can_split():
            raise ValueError(f"window {self} is already at the minimum size")
        half = (self.end - self.start) / 2
        mid = self.start + timedelta(seconds=int(half.total_seconds()))
        return Window(self.start, mid), Window(mid, self.end)


def initial_windows(start: datetime, horizon: timedelta = HORIZON) -> list[Window]:
    windows, end = [], start + horizon
    while start < end:
        windows.append(Window(start, min(start + WINDOW, end)))
        start += WINDOW
    return windows
