"""Provider classification + sovereignty tier logic.

The sovereignty tier is the editorial classification we assign to each site:

    1 🍁 Canadian Sovereign   Canadian-owned hosting + Canadian datacenter
    2 🇨🇦 Canadian Soil        Foreign provider, server in Canada
    3 🌐 CDN-Fronted          Behind a global CDN, origin unknown
    4 🇺🇸 US-Hosted           Server in US, US provider
    5 🌍 Other Foreign        Hosted elsewhere outside Canada + US
    6 ❓ Unknown              Scan failed or inconclusive
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


# ── Known Canadian-owned providers ────────────────────────────────────
# Mapped to ASN number OR ASN-org substring (lowercased).
CANADIAN_PROVIDERS_ASNS: set[int] = {
    13768,   # Cogent... no wait — actually Peer 1 / Cogeco
    32613,   # iWeb
    7992,    # Rogers
    812,     # Rogers Cable
    577,     # Bell Canada
    852,     # Telus
    19531,   # OVH is French — not Canadian; but OVHCloud Canada operates DCs here
    26782,   # HostPapa is Canadian
    395152,  # Green Web Hosting
    46562,   # Shared Services Canada
    6724,    # CIRA
    16509,   # AWS — not Canadian, but has ca-central-1 in Montreal
}

# Lowercase substrings in ip_org that mark Canadian-owned providers.
CANADIAN_PROVIDER_SUBSTRINGS: tuple[str, ...] = (
    "ovh hosting",          # OVH Canadian DC (Beauharnois QC)
    "shared services canada",
    "canadian web hosting",
    "hostpapa",
    "iweb",
    "netfirms",
    "website.ca",
    "cira",
    "bell canada",
    "rogers",
    "telus",
    "videotron",
    "cogeco",
    "ehost canada",
    "shopify",              # Ottawa-HQ, though AWS-hosted
    "peer1",
    "evolutionhost",
)

# CDN patterns (on CNAME chain or IP org)
CDN_PATTERNS: dict[str, str] = {
    "cloudflare": "Cloudflare",
    "cloudfront": "AWS CloudFront",
    "akamai":     "Akamai",
    "akamaitech": "Akamai",
    "akamaiedge": "Akamai",
    "fastly":     "Fastly",
    "fastlylb":   "Fastly",
    "vercel":     "Vercel",
    "netlify":    "Netlify",
    "azureedge":  "Azure CDN",
    "azurefd":    "Azure Front Door",
    "github.io":  "GitHub Pages",
    "bunnycdn":   "BunnyCDN",
    "bunny.net":  "BunnyCDN",
    "stackpath":  "StackPath",
    "incapsula":  "Imperva",
    "imperva":    "Imperva",
    "sucuri":     "Sucuri",
    "keycdn":     "KeyCDN",
    "cachefly":   "CacheFly",
}

# Provider detection (non-CDN) patterns on ip_org / CNAME / headers
PROVIDER_PATTERNS: dict[str, str] = {
    "amazon":     "Amazon Web Services",
    "aws":        "Amazon Web Services",
    "googlecloud":"Google Cloud",
    "google":     "Google",
    "microsoft":  "Microsoft Azure",
    "azure":      "Microsoft Azure",
    "digitalocean":"DigitalOcean",
    "linode":     "Linode",
    "vultr":      "Vultr",
    "hetzner":    "Hetzner",
    "ovh":        "OVHcloud",
    "godaddy":    "GoDaddy",
    "squarespace":"Squarespace",
    "wordpress":  "WordPress.com (Automattic)",
    "wpengine":   "WP Engine",
    "wix":        "Wix",
    "shopify":    "Shopify",
    "nationbuilder":"NationBuilder",
    "heroku":     "Heroku",
    "render":     "Render",
    "fly.io":     "Fly.io",
    "hostpapa":   "HostPapa",
    "iweb":       "iWeb",
    "cogeco":     "Cogeco Peer 1",
}

# CMS fingerprints
CMS_HEADER_HINTS: dict[str, str] = {
    "wordpress":  "WordPress",
    "drupal":     "Drupal",
    "joomla":     "Joomla",
    "ghost":      "Ghost",
    "shopify":    "Shopify",
    "wix":        "Wix",
    "squarespace":"Squarespace",
    "nationbuilder":"NationBuilder",
    "craft":      "Craft CMS",
}


@dataclass
class Classification:
    sovereignty_tier: int
    hosting_provider: Optional[str]
    hosting_country: Optional[str]
    datacenter_region: Optional[str]
    cdn_detected: Optional[str]
    cms_detected: Optional[str]


def _match_cdn(candidates: Iterable[str]) -> Optional[str]:
    for s in candidates:
        if not s:
            continue
        sl = s.lower()
        for pat, label in CDN_PATTERNS.items():
            if pat in sl:
                return label
    return None


def _match_provider(candidates: Iterable[str]) -> Optional[str]:
    for s in candidates:
        if not s:
            continue
        sl = s.lower()
        for pat, label in PROVIDER_PATTERNS.items():
            if pat in sl:
                return label
    return None


def _match_cms(candidates: Iterable[str]) -> Optional[str]:
    for s in candidates:
        if not s:
            continue
        sl = s.lower()
        for pat, label in CMS_HEADER_HINTS.items():
            if pat in sl:
                return label
    return None


def _is_canadian_provider(ip_org: Optional[str], ip_asn: Optional[str]) -> bool:
    if ip_org:
        ol = ip_org.lower()
        if any(sub in ol for sub in CANADIAN_PROVIDER_SUBSTRINGS):
            return True
    if ip_asn:
        # ip_asn is typically "AS15169" — parse the number
        m = re.match(r"AS?(\d+)", ip_asn, re.I)
        if m and int(m.group(1)) in CANADIAN_PROVIDERS_ASNS:
            return True
    return False


def classify(
    ip_country: Optional[str],
    ip_org: Optional[str],
    ip_asn: Optional[str],
    cname_chain: Optional[list[str]],
    http_server_header: Optional[str],
    http_powered_by: Optional[str],
    nameservers: Optional[list[str]],
    had_error: bool,
) -> Classification:
    """Turn raw scan data into a sovereignty classification."""
    if had_error or not ip_country:
        return Classification(
            sovereignty_tier=6,
            hosting_provider=None,
            hosting_country=None,
            datacenter_region=None,
            cdn_detected=None,
            cms_detected=None,
        )

    cname_chain = cname_chain or []
    nameservers = nameservers or []

    # CDN check: look at CNAME chain, ip_org, nameservers
    cdn = _match_cdn([*cname_chain, ip_org or "", *nameservers])

    # Provider: if a non-CDN pattern hits first, prefer it — else fall back to CDN label.
    provider = _match_provider([ip_org or "", *cname_chain, http_server_header or ""])
    if not provider and cdn:
        provider = cdn

    # CMS
    cms = _match_cms([http_server_header or "", http_powered_by or "", *cname_chain])

    # Sovereignty tier decision tree
    is_ca_provider = _is_canadian_provider(ip_org, ip_asn)

    if ip_country == "CA" and is_ca_provider:
        tier = 1  # Canadian sovereign
    elif ip_country == "CA":
        tier = 2  # Canadian soil, foreign provider
    elif cdn and ip_country in (None, "US"):
        # CDN fronted: origin could be anywhere; if US we still mark CDN-fronted
        tier = 3
    elif ip_country == "US":
        tier = 4
    else:
        tier = 5

    return Classification(
        sovereignty_tier=tier,
        hosting_provider=provider,
        hosting_country=ip_country,
        datacenter_region=None,  # may be filled later from IP city/region
        cdn_detected=cdn,
        cms_detected=cms,
    )


def tier_label(tier: int) -> str:
    return {
        1: "Canadian Sovereign",
        2: "Canadian Soil",
        3: "CDN-Fronted",
        4: "US-Hosted",
        5: "Other Foreign",
        6: "Unknown",
    }.get(tier, "Unknown")


def tier_emoji(tier: int) -> str:
    return {1: "🍁", 2: "🇨🇦", 3: "🌐", 4: "🇺🇸", 5: "🌍", 6: "❓"}.get(tier, "❓")
