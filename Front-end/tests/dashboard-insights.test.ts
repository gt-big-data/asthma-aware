import assert from "node:assert/strict";
import test from "node:test";
import { groupForecastInputs, lowerRiskPeriods } from "../lib/dashboard-insights.ts";

const start = Date.parse("2026-11-19T12:00:00Z");
function hours(scores: (number | null)[]) {
  return scores.map((score, i) => ({ valid_at: new Date(start + (i + .5) * 3600000).toISOString(), score, general_score: 90 }));
}

test("lower periods use the displayed score and retain high-risk classification data", () => {
  const result = lowerRiskPeriods(hours([90, 90, 75, 76, 90, 90, 90, 90]), start);
  assert.equal(result.periods.length, 1);
  assert.equal(result.periods[0].start, "2026-11-19T14:00:00.000Z");
  assert.equal(result.periods[0].end, "2026-11-19T16:00:00.000Z");
  assert.equal(result.periods[0].peak, 76);
  assert.equal(result.typical, 90);
});

test("missing scores and missing timestamps break windows instead of bridging gaps", () => {
  assert.equal(lowerRiskPeriods(hours([20, null, 20, null, 20]), start).periods.length, 0);
  const gap = hours([20, 20, 20]).filter((_, i) => i !== 1);
  assert.equal(lowerRiskPeriods(gap, start).periods.length, 0);
  const result = lowerRiskPeriods(hours([20, 20, null, 25, 25]), start);
  assert.equal(result.periods.length, 2);
});

test("flat high forecasts do not invent lower windows; flat low forecasts remain useful", () => {
  assert.equal(lowerRiskPeriods(hours(Array(24).fill(90)), start).periods.length, 0);
  const low = lowerRiskPeriods(hours(Array(24).fill(39.5)), start);
  assert.equal(low.periods.length, 1);
  assert.equal(low.periods[0].hours, 24);
});

test("periods exclude past hours, limit the outlook, and require two remaining hours", () => {
  const input = hours(Array(96).fill(20));
  const result = lowerRiskPeriods(input, start + 15 * 60000);
  assert.equal(result.availableHours, 48);
  assert.equal(result.periods[0].start, "2026-11-19T12:15:00.000Z");
  assert.equal(result.periods[0].end, "2026-11-21T12:00:00.000Z");
  assert.equal(lowerRiskPeriods(hours([20, 20]), start + 15 * 60000).periods.length, 0);
});

test("choose at most three strongest nonoverlapping periods and present chronologically", () => {
  const scores = [30, 30, 90, 90, 90, 10, 10, 90, 90, 90, 25, 25, 90, 90, 90, 15, 15, 90, 90, 90];
  const periods = lowerRiskPeriods(hours(scores), start).periods;
  assert.deepEqual(periods.map(period => period.peak), [10, 25, 15]);
  assert.ok(periods.every((period, i) => !i || Date.parse(period.start) >= Date.parse(periods[i - 1].end)));
});

test("relative troughs remain highlighted when every hour is in the low band", () => {
  const result = lowerRiskPeriods(hours([35, 35, 35, 35, 15, 15, 35, 35]), start);
  assert.equal(result.periods.length, 1);
  assert.equal(result.periods[0].peak, 15);
  assert.equal(result.periods[0].hours, 2);
});

test("empty and invalid data produce no suggestions and do not count as valid hours", () => {
  assert.equal(lowerRiskPeriods([], start).typical, null);
  assert.equal(lowerRiskPeriods(hours([null, NaN, -1, 101]), start).availableHours, 0);
});

test("forecast sources group equivalent timezones, preserve differing runs, and mark unknown times", () => {
  const input = (variable: string, time: string | null) => ({ variable, source_run: "run", issued_at: time, collection: null });
  const groups = groupForecastInputs([input("o3", "2026-11-18T09:00:00Z"), input("pm25", "2026-11-18T04:00:00-05:00"),
    input("air_temperature", "2026-11-17T09:00:00Z"), input("so2", null)]);
  assert.equal(groups.length, 3);
  assert.deepEqual(groups[0].variables, ["o3", "pm25"]);
  assert.equal(groups[1].issuedAt, "2026-11-17T09:00:00.000Z");
  assert.equal(groups[2].issuedAt, null);
  assert.deepEqual(groupForecastInputs([]), []);
});
