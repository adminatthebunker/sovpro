/**
 * Scheme whitelist for user- or scanner-supplied URLs that are
 * rendered as <a href>. Returns the URL only if its scheme is
 * http(s); otherwise returns undefined so callers can degrade to
 * plain text.
 *
 * Why defense-in-depth when the API already refines? Two reasons:
 *   1. The admin surface renders data from multiple sources (public
 *      corrections, scanner-populated politician_socials, Wikidata
 *      harvests) — not all of those run through the same input
 *      validator.
 *   2. React 18 does not sanitize javascript: hrefs; React 19 does.
 *      Keeping this guard means we do not silently rely on a version
 *      upgrade to stay safe.
 */
export function safeHttpHref(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  return /^https?:\/\//i.test(value) ? value : undefined;
}
