import test from "node:test";
import assert from "node:assert/strict";
import { availableDates, localDate, parseCoordinates } from "../lib/logging.ts";
import { ApiError, createApiClient } from "../lib/api-client.ts";

test("calendar dates respect user timezone around midnight", () => {
  const now = new Date("2026-11-02T04:30:00Z");
  assert.equal(localDate(now, "America/New_York"), "2026-11-01");
  assert.equal(localDate(now, "UTC"), "2026-11-02");
});
test("backfill is five calendar days across daylight saving and year boundaries", () => {
  const dates = availableDates("America/New_York", new Date("2026-11-02T05:00:00Z"));
  assert.deepEqual(dates, ["2026-11-02", "2026-11-01", "2026-10-31", "2026-10-30", "2026-10-29", "2026-10-28"]);
  assert.equal(availableDates("UTC", new Date("2027-01-01T00:00:00Z"))[5], "2026-12-27");
});
test("manual coordinates reject missing and invalid measurements", () => {
  for (const pair of [["", "0"], ["0", " "], ["NaN", "0"], ["91", "0"], ["0", "181"], ["Infinity", "0"]]) {
    assert.equal(parseCoordinates(pair[0], pair[1]), null);
  }
  assert.deepEqual(parseCoordinates("0", "0"), { latitude: 0, longitude: 0 });
  assert.deepEqual(parseCoordinates("33.749", "-84.388"), { latitude: 33.749, longitude: -84.388 });
});
test("API requests carry the latest token and optimistic revision", async () => {
  let token = "first";
  const requests: { path: string; options?: RequestInit }[] = [];
  const client = createApiClient({ baseUrl: "https://api.example/", getAccessToken: async () => token,
    fetcher: async (url, options) => { requests.push({ path: String(url), options }); return new Response("{}", { status: 200 }); },
  });
  await client.profile(); token = "refreshed";
  await client.save("2026-11-01", { severity: 3, latitude: 33.749, longitude: -84.388, location_source: "selected", expected_revision: 4 });
  assert.equal(requests[1].path, "https://api.example/v1/symptoms/2026-11-01");
  assert.equal((requests[1].options?.headers as Record<string, string>).Authorization, "Bearer refreshed");
  assert.equal(JSON.parse(String(requests[1].options?.body)).expected_revision, 4);
});
test("missing session makes no authenticated API request", async () => {
  const client = createApiClient({ baseUrl: "https://api.example", getAccessToken: async () => null,
    fetcher: async () => { throw new Error("must not make a request"); },
  });
  await assert.rejects(client.profile(), (error: unknown) => error instanceof ApiError && error.status === 401);
});
test("absent entry is distinct from authentication or service failure", async () => {
  let status = 404;
  const client = createApiClient({ baseUrl: "https://api.example", getAccessToken: async () => "token",
    fetcher: async () => new Response("{}", { status }),
  });
  assert.equal(await client.entry("2026-11-01"), null);
  for (status of [401, 409, 503]) {
    await assert.rejects(client.entry("2026-11-01"), (error: unknown) => error instanceof ApiError && error.status === status);
  }
});
test("a timed-out save is reported as uncertain and is not automatically retried", async () => {
  let calls = 0;
  const client = createApiClient({ baseUrl: "https://api.example", getAccessToken: async () => "token", timeoutMs: 5,
    fetcher: async (_url, options) => new Promise((_resolve, reject) => {
      calls += 1; options?.signal?.addEventListener("abort", () => reject(new Error("timeout")));
    }),
  });
  await assert.rejects(client.save("2026-11-01", { severity: 1, latitude: 0, longitude: 0, location_source: "current", expected_revision: 0 }), ApiError);
  assert.equal(calls, 1);
});
