import assert from "node:assert/strict";
import test from "node:test";
import { hourLabel, localDay, riskColor, riskLabel, scoreRange, windowHours } from "../lib/hourly-risk.ts";

test("hour windows preserve missing slots and use the profile timezone", () => {
  const hours = Array.from({ length: 30 }, (_, i) => ({
    valid_at: new Date(Date.UTC(2026, 10, 18, 20, 30) + i * 3600000).toISOString(),
    score: i === 1 ? null : i * 2, general_score: i * 2,
  }));
  assert.equal(windowHours(hours, "next", "America/New_York").length, 24);
  assert.equal(windowHours(hours, "next", "America/New_York")[1].score, null);
  assert.equal(windowHours(hours, "2026-11-18", "America/New_York").length, 9);
  assert.equal(windowHours(hours, "2026-11-18", "UTC").length, 4);
  assert.deepEqual(scoreRange(hours), { min: 0, max: 58 });
  assert.equal(scoreRange([{ ...hours[0], score: null }]), null);
});

test("hour labels show interval starts and distinguish repeated DST hours", () => {
  assert.equal(hourLabel("2026-11-18T17:30:00Z", "America/New_York"), "12 PM");
  assert.equal(localDay("2026-11-19T00:30:00Z", "America/New_York"), "2026-11-18");
  const first = hourLabel("2026-11-01T05:30:00Z", "America/New_York", true);
  const second = hourLabel("2026-11-01T06:30:00Z", "America/New_York", true);
  assert.match(first, /1 AM.*EDT/);
  assert.match(second, /1 AM.*EST/);
});

test("risk bands stay fixed across every chart window", () => {
  assert.equal(riskLabel(0), "Low");
  assert.equal(riskLabel(39), "Low");
  assert.equal(riskLabel(40), "Moderate");
  assert.equal(riskLabel(70), "High");
  assert.equal(riskColor(100), riskColor(70));
});
