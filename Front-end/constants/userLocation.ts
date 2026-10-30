/**
 * Shared user location.
 *
 * Looks up the user's position once and remembers it, so the dashboard and
 * the map don't each ask the phone separately. If a screen asks while a
 * lookup is already running, it waits for that same lookup.
 */

import * as Location from "expo-location";

export interface Coords {
  latitude: number;
  longitude: number;
}

// A cached fix from the last hour is close enough for a metro-sized view
// and is returned instantly, instead of waiting for a fresh GPS reading.
const MAX_CACHED_AGE_MS = 60 * 60 * 1000;
const MAX_CACHED_ERROR_M = 5000;

let cached: Coords | null = null;
let pending: Promise<Coords | null> | null = null;

async function lookUpLocation(): Promise<Coords | null> {
  try {
    const permission = await Location.requestForegroundPermissionsAsync();
    if (permission.status !== "granted") return null;

    const lastKnown = await Location.getLastKnownPositionAsync({
      maxAge: MAX_CACHED_AGE_MS,
      requiredAccuracy: MAX_CACHED_ERROR_M,
    });
    const position =
      lastKnown ??
      (await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.Balanced,
      }));

    return {
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
    };
  } catch (error) {
    console.error("Unable to retrieve the user's location.", error);
    return null;
  }
}

/** The location if it has already been found, otherwise null. */
export function getCachedUserLocation(): Coords | null {
  return cached;
}

/** Finds the user's location, reusing an earlier or in-progress lookup. */
export function getUserLocation(): Promise<Coords | null> {
  if (cached) return Promise.resolve(cached);

  if (!pending) {
    pending = lookUpLocation().then((coords) => {
      cached = coords;
      // On failure, let a later call try again (e.g. after the user
      // turns location on).
      pending = null;
      return coords;
    });
  }
  return pending;
}
