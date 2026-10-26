from datetime import UTC, datetime, timedelta

import pytest

from jobs.geo import geohash, haversine_km, km_to_miles
from jobs.windows import HORIZON, MIN_WINDOW, Window, initial_windows


def test_geohash_matches_the_reference_example():
    assert geohash(57.64911, 10.40744, precision=11) == "u4pruydqqvj"


def test_geohash_of_charing_cross():
    assert geohash(51.507333, -0.127654).startswith("gcpvj")


def test_haversine_charing_cross_to_the_o2_is_about_9_km():
    assert haversine_km(51.507333, -0.127654, 51.50304, 0.00317) == pytest.approx(9.1, abs=0.2)


def test_25_km_is_about_15_5_miles():
    assert km_to_miles(25) == pytest.approx(15.53, abs=0.01)


def test_initial_windows_cover_the_horizon_without_gaps():
    start = datetime(2026, 11, 1, tzinfo=UTC)
    windows = initial_windows(start)
    assert len(windows) == 13
    assert windows[0].start == start
    assert windows[-1].end == start + HORIZON
    assert all(a.end == b.start for a, b in zip(windows, windows[1:], strict=False))


def test_split_halves_a_window():
    w = Window(datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 8, tzinfo=UTC))
    left, right = w.split()
    assert (left.start, left.end, right.end) == (
        w.start,
        datetime(2026, 11, 4, 12, tzinfo=UTC),
        w.end,
    )


def test_windows_never_split_below_one_hour():
    start = datetime(2026, 11, 1, tzinfo=UTC)
    w = Window(start, start + 2 * MIN_WINDOW - timedelta(seconds=1))
    assert not w.can_split()
    with pytest.raises(ValueError):
        w.split()
