/**
 * Formats a duration in seconds into a human-readable duration (e.g. "5 min", "1 h 15 min").
 * Reused from components/source-card.tsx with identical output.
 */
export function formatDuration(durationS: number): string {
  const totalMinutes = Math.max(1, Math.round(durationS / 60));
  if (totalMinutes < 60) {
    return `${totalMinutes} min`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours} h ${minutes} min`;
}

/**
 * Formats seconds into m:ss under an hour and h:mm:ss from an hour,
 * matching the backend citation time format.
 */
export function formatCitationTime(seconds: number): string {
  if (typeof seconds !== "number" || isNaN(seconds) || !isFinite(seconds)) {
    return "0:00";
  }
  const totalSecs = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(totalSecs / 3600);
  const rem = totalSecs % 3600;
  const mins = Math.floor(rem / 60);
  const secs = rem % 60;
  const paddedSecs = secs.toString().padStart(2, "0");
  if (hours > 0) {
    const paddedMins = mins.toString().padStart(2, "0");
    return `${hours}:${paddedMins}:${paddedSecs}`;
  }
  return `${mins}:${paddedSecs}`;
}
