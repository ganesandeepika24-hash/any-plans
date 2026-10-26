"""Distances and geohashes. The model never computes these (principle 5)."""

import math

EARTH_RADIUS_KM = 6371.0088
KM_PER_MILE = 1.609344
_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def km_to_miles(km: float) -> float:
    return km / KM_PER_MILE


def geohash(lat: float, lon: float, precision: int = 9) -> str:
    lat_range, lon_range = [-90.0, 90.0], [-180.0, 180.0]
    chars, bits, bit_count, even = [], 0, 0, True
    while len(chars) < precision:
        rng, value = (lon_range, lon) if even else (lat_range, lat)
        mid = (rng[0] + rng[1]) / 2
        bits <<= 1
        if value >= mid:
            bits |= 1
            rng[0] = mid
        else:
            rng[1] = mid
        even = not even
        bit_count += 1
        if bit_count == 5:
            chars.append(_BASE32[bits])
            bits, bit_count = 0, 0
    return "".join(chars)
