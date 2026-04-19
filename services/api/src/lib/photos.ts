/**
 * Resolve a politician's photo URL, preferring the locally mirrored
 * copy under `/assets/` over the upstream URL.
 *
 * The shape matches the DB columns added in migration 0026:
 *   - `photo_path`  volume-relative path, e.g. 'politicians/<uuid>.jpg'
 *   - `photo_url`   original upstream URL (kept for attribution and
 *                   re-fetch — nothing in the UI dereferences it once
 *                   photo_path is populated)
 *
 * Consumers should keep emitting the key `photo_url` in API responses;
 * only the value changes. That way the frontend doesn't need to know
 * about the new column.
 */
export function resolvePhotoUrl(row: {
  photo_path?: string | null;
  photo_url?: string | null;
}): string | null {
  if (row.photo_path) return `/assets/${row.photo_path}`;
  if (row.photo_url) return row.photo_url;
  return null;
}
