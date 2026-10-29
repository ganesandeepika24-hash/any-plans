// The person's device position (docs/api.md sections 8 and 12).
// Held in memory for this session only: never in storage, the URL, analytics or logs.
import type { Location } from "./api";

export type GeoPermission = "granted" | "prompt" | "denied" | "unsupported";

let position: Location | null = null;
let failed = false; // denied, error or timeout: don't ask again this session

export async function geoPermission(): Promise<GeoPermission> {
  if (typeof navigator === "undefined" || !navigator.geolocation) return "unsupported";
  try {
    if (!navigator.permissions?.query) return "prompt";
    const s = await navigator.permissions.query({ name: "geolocation" as PermissionName });
    return s.state as GeoPermission;
  } catch {
    return "prompt";
  }
}

/** Reads the position once. `explicit` is a "Use my location" tap, which may try again unless denied. */
export async function readDevicePosition(opts: { explicit?: boolean } = {}): Promise<Location | null> {
  if (position) return position;
  if (failed && !opts.explicit) return null;
  const perm = await geoPermission();
  if (perm === "denied" || perm === "unsupported") {
    failed = true;
    return null;
  }
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (p) => {
        position = { lat: p.coords.latitude, lon: p.coords.longitude, source: "device" };
        resolve(position);
      },
      () => {
        failed = true;
        resolve(null);
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 },
    );
  });
}

/** The position already read this session, if any (for /refine). */
export function rememberedPosition(): Location | null {
  return position;
}

/** Test helper. */
export function resetGeoForTests() {
  position = null;
  failed = false;
}
