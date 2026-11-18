import type { Coordinates, LocationSource } from "./logging";
import type { MapForecast, MapFrameImage } from "./forecast-map";

export interface DashboardRisk {
  stale: boolean;
  placeholder: boolean;
  personalization?: { matched_logs: number; pending_logs: number; still_learning: boolean } | null;
  days: { date: string; score: number | null; available_hours: number }[];
}

export interface Profile { user_id: string; name: string | null; timezone: string }
export interface SymptomEntry extends Coordinates {
  id: string;
  log_date: string;
  severity: number;
  location_source: LocationSource;
  timezone: string;
  revision: number;
  finalizes_at: string;
  status: "provisional" | "final";
  exposure_status: "pending" | "matched";
  created_at: string;
  updated_at: string;
}
export interface SymptomWrite extends Coordinates {
  severity: number;
  location_source: LocationSource;
  expected_revision: number;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function createApiClient(options: {
  baseUrl: string;
  getAccessToken: () => Promise<string | null>;
  fetcher?: typeof fetch;
  timeoutMs?: number;
}) {
  const fetcher = options.fetcher ?? fetch;
  async function request<T>(path: string, method = "GET", body?: unknown, authenticated = true, signal?: AbortSignal): Promise<T> {
    const token = authenticated ? await options.getAccessToken() : null;
    if (authenticated && !token) throw new ApiError(401, "Please sign in to continue.");
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort);
    if (signal?.aborted) controller.abort();
    const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 15000);
    try {
      const response = await fetcher(options.baseUrl.replace(/\/$/, "") + path, {
        method, signal: controller.signal,
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(authenticated || body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) {
        const message = response.status === 401 ? "Your session has expired. Please sign in again."
          : response.status === 409 ? "This entry changed elsewhere. Reload it before saving again."
          : response.status === 422 ? "Check the date and values, then try again."
          : response.status === 503 ? "The service is temporarily unavailable. Please try again."
          : "Could not complete the request. Please try again.";
        throw new ApiError(response.status, message);
      }
      return await response.json() as T;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(0, "Could not reach the service. Check your connection and try again.");
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
    }
  }
  return {
    mapForecast: (signal?: AbortSignal) => request<MapForecast>("/v1/map/forecast", "GET", undefined, false, signal),
    mapFrame: (runId: string, index: number, signal?: AbortSignal) => request<MapFrameImage>(
      `/v1/map/frame?run_id=${encodeURIComponent(runId)}&index=${index}`, "GET", undefined, false, signal,
    ),
    async dashboardRisk(lat: number, lon: number, usePersonalization = true) {
      const signedIn = usePersonalization && !!await options.getAccessToken();
      const route = signedIn ? "personal-risk" : "risk";
      return request<DashboardRisk>(`/v1/dashboard/${route}?lat=${lat}&lon=${lon}`, "GET", undefined, signedIn);
    },
    profile: () => request<Profile>("/v1/users/me"),
    setTimezone: (timezone: string) => request<Profile>("/v1/users/me", "PATCH", { timezone }),
    async entry(day: string): Promise<SymptomEntry | null> {
      try { return await request<SymptomEntry>(`/v1/symptoms/${day}`); }
      catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
    },
    save: (day: string, body: SymptomWrite) => request<SymptomEntry>(`/v1/symptoms/${day}`, "PUT", body),
  };
}
