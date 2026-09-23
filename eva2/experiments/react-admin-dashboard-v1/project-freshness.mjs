/** Freshness of an explicit project-status review, independent of polling. */
export function projectFreshness(reviewedAt, now) {
  if (typeof reviewedAt !== "string" || !reviewedAt.trim()) return "unknown";
  // Require an unambiguous timestamp; a date alone is not a review time.
  const parts = /^(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/i.exec(reviewedAt);
  if (!parts) return "unknown";
  const [, year, month, day] = parts.map(Number);
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  if (month < 1 || month > 12 || day < 1 || day > lastDay) return "unknown";
  const reviewedMs = Date.parse(reviewedAt);
  const nowMs = now instanceof Date ? now.getTime()
    : typeof now === "number" ? now
    : typeof now === "string" ? Date.parse(now) : NaN;
  if (!Number.isFinite(reviewedMs) || !Number.isFinite(nowMs)) return "unknown";
  const ageMs = nowMs - reviewedMs;
  if (ageMs < -5 * 60 * 1000) return "unknown";
  return ageMs <= 60 * 60 * 1000 ? "current" : "outdated";
}
