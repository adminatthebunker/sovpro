/**
 * Reject email addresses at domains that cannot legitimately receive
 * mail: RFC 2606 reserved TLDs and example domains, plus the IETF
 * nullMX'd hosts. Catches typos ("test@localhost"), deliberate test
 * signups ("uitest@example.com"), and low-effort bot fodder in one
 * rule.
 *
 * We only block names that have a guaranteed-undeliverable contract
 * via the DNS/IETF — not broad "disposable email" lists, which decay
 * fast and sometimes include real mailboxes. Add those separately if
 * needed.
 *
 * Callers pre-lowercase the address (zod .toLowerCase() on the edge),
 * so this compares against lowercase literals only.
 */

// RFC 2606 §2 — reserved TLDs that must not resolve.
const RESERVED_TLDS = new Set<string>([
  "test",
  "example",
  "invalid",
  "localhost",
]);

// RFC 2606 §3 — reserved second-level names.
const RESERVED_DOMAINS = new Set<string>([
  "example.com",
  "example.net",
  "example.org",
]);

export interface DomainCheck {
  ok: boolean;
  /** Machine-readable reason code for logs; not user-facing. */
  reason?: "reserved_tld" | "reserved_domain" | "malformed";
}

export function checkDeliverableDomain(lowercasedEmail: string): DomainCheck {
  const at = lowercasedEmail.lastIndexOf("@");
  if (at < 0 || at === lowercasedEmail.length - 1) {
    return { ok: false, reason: "malformed" };
  }
  const domain = lowercasedEmail.slice(at + 1);

  if (RESERVED_DOMAINS.has(domain)) {
    return { ok: false, reason: "reserved_domain" };
  }

  const tld = domain.slice(domain.lastIndexOf(".") + 1);
  if (RESERVED_TLDS.has(tld)) {
    return { ok: false, reason: "reserved_tld" };
  }
  // Bare "localhost" with no dot also lands here.
  if (RESERVED_TLDS.has(domain)) {
    return { ok: false, reason: "reserved_tld" };
  }

  return { ok: true };
}
