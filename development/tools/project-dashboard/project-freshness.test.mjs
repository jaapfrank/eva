import test from "node:test";
import assert from "node:assert/strict";
import { projectFreshness } from "./project-freshness.mjs";

const reviewedAt = "2026-09-23T12:00:00Z";
const reviewedMs = Date.parse(reviewedAt);

test("review is current through the exact 60-minute boundary", () => {
  assert.equal(projectFreshness(reviewedAt, reviewedMs), "current");
  assert.equal(projectFreshness(reviewedAt, reviewedMs + 3_600_000), "current");
  assert.equal(projectFreshness(reviewedAt, reviewedMs + 3_600_001), "outdated");
});

test("only a small future clock skew is tolerated", () => {
  assert.equal(projectFreshness(reviewedAt, reviewedMs - 300_000), "current");
  assert.equal(projectFreshness(reviewedAt, reviewedMs - 300_001), "unknown");
});

test("missing, invalid or ambiguous review times are unknown", () => {
  for (const value of [undefined, null, "", " ", "invalid", "2026-09-23",
    "2026-09-23T12:00:00", "2026-02-30T12:00:00Z", 0, new Date(reviewedMs)]) {
    assert.equal(projectFreshness(value, reviewedMs), "unknown");
  }
});

test("invalid or missing current time cannot assert freshness", () => {
  for (const value of [undefined, null, NaN, Infinity, "invalid", new Date(NaN)]) {
    assert.equal(projectFreshness(reviewedAt, value), "unknown");
  }
});

test("explicit offsets and supported current-time representations agree", () => {
  const offsetReview = "2026-09-23T14:00:00+02:00";
  assert.equal(projectFreshness(offsetReview, reviewedAt), "current");
  assert.equal(projectFreshness(offsetReview, new Date(reviewedMs)), "current");
});

test("a later poll does not renew the original review", () => {
  assert.equal(projectFreshness(reviewedAt, reviewedMs + 30 * 60_000), "current");
  assert.equal(projectFreshness(reviewedAt, reviewedMs + 90 * 60_000), "outdated");
  assert.equal(projectFreshness(undefined, reviewedMs), "unknown");
});
