"""Compare two scans and emit a list of change records."""
from __future__ import annotations

from typing import Any, Mapping


def _diff(old: Any, new: Any, key: str, severity: str, summary_fmt: str,
          change_type: str) -> list[dict]:
    if old is None and new is None:
        return []
    if str(old or "") == str(new or ""):
        return []
    return [{
        "type": change_type,
        "old": str(old or ""),
        "new": str(new or ""),
        "severity": severity,
        "summary": summary_fmt.format(old=old, new=new),
        "details": {key: {"old": old, "new": new}},
    }]


def compare_scans(prev: Mapping, new) -> list[dict]:
    """prev is a DB row, new is a ScanResult."""
    changes: list[dict] = []

    # Country move — major
    changes += _diff(
        prev.get("ip_country"), new.ip_country,
        "ip_country", "major",
        "Host country changed {old!s} → {new!s}",
        "country_changed",
    )

    # City move — notable
    changes += _diff(
        prev.get("ip_city"), new.ip_city,
        "ip_city", "notable",
        "Host city changed {old!s} → {new!s}",
        "city_changed",
    )

    # Hosting provider move — major
    changes += _diff(
        prev.get("hosting_provider"), (new.classification.hosting_provider if new.classification else None),
        "hosting_provider", "major",
        "Hosting provider changed {old!s} → {new!s}",
        "provider_changed",
    )

    # Sovereignty tier move — severity scales with gap
    old_tier = prev.get("sovereignty_tier")
    new_tier = new.classification.sovereignty_tier if new.classification else None
    if old_tier is not None and new_tier is not None and old_tier != new_tier:
        sev = "major" if abs(new_tier - old_tier) >= 2 else "notable"
        changes.append({
            "type": "tier_changed",
            "old": str(old_tier),
            "new": str(new_tier),
            "severity": sev,
            "summary": f"Sovereignty tier {old_tier} → {new_tier}",
            "details": {"tier": {"old": old_tier, "new": new_tier}},
        })

    # CDN change — info
    changes += _diff(
        prev.get("cdn_detected"), (new.classification.cdn_detected if new.classification else None),
        "cdn_detected", "info",
        "CDN {old!s} → {new!s}", "cdn_changed",
    )

    # CMS change — info
    changes += _diff(
        prev.get("cms_detected"), (new.classification.cms_detected if new.classification else None),
        "cms_detected", "info",
        "CMS {old!s} → {new!s}", "cms_changed",
    )

    # IP set change (unordered)
    old_ips = set(prev.get("ip_addresses") or [])
    new_ips = set(new.ip_addresses or [])
    if old_ips != new_ips and (old_ips or new_ips):
        added = sorted(new_ips - old_ips)
        removed = sorted(old_ips - new_ips)
        changes.append({
            "type": "ip_changed",
            "old": ",".join(sorted(old_ips)) or None,
            "new": ",".join(sorted(new_ips)) or None,
            "severity": "info",
            "summary": f"IPs changed (+{len(added)} / -{len(removed)})",
            "details": {"added": added, "removed": removed},
        })

    # TLS issuer change
    changes += _diff(
        prev.get("tls_issuer"), new.tls_issuer,
        "tls_issuer", "info",
        "TLS issuer {old!s} → {new!s}", "tls_issuer_changed",
    )

    return changes
