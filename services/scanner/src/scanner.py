"""Core async scanner.

For each website we:
  1. Resolve DNS (A, AAAA, CNAME chain, NS, MX).
  2. Look up GeoIP + ASN for the primary A record.
  3. Fetch HTTPS cert (TLS issuer + expiry).
  4. GET the URL and capture Server/X-Powered-By headers + final URL.
  5. Classify (sovereignty tier, provider, CDN, CMS) via classify.classify().
  6. Write a new row in infrastructure_scans.
  7. Compare to the previous scan and write scan_changes rows if anything moved.
  8. Bump websites.last_scanned_at and last_changed_at.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver
import httpx
import orjson
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from .classify import Classification, classify
from .compare import compare_scans
from .db import Database
from .geoip import GeoIP

console = Console()


@dataclass
class ScanResult:
    website_id: str
    url: str
    scanned_at: datetime
    ip_addresses: list[str] = field(default_factory=list)
    cname_chain: list[str] = field(default_factory=list)
    nameservers: list[str] = field(default_factory=list)
    mx_records: list[str] = field(default_factory=list)
    ip_country: Optional[str] = None
    ip_region: Optional[str] = None
    ip_city: Optional[str] = None
    ip_latitude: Optional[float] = None
    ip_longitude: Optional[float] = None
    ip_asn: Optional[str] = None
    ip_org: Optional[str] = None
    tls_issuer: Optional[str] = None
    tls_subject: Optional[str] = None
    tls_expiry: Optional[datetime] = None
    tls_valid: Optional[bool] = None
    http_status: Optional[int] = None
    http_server_header: Optional[str] = None
    http_powered_by: Optional[str] = None
    http_final_url: Optional[str] = None
    duration_ms: int = 0
    error: Optional[str] = None
    classification: Optional[Classification] = None
    raw: dict = field(default_factory=dict)


async def _resolve(resolver: dns.asyncresolver.Resolver, host: str, rtype: str) -> list[str]:
    try:
        answer = await resolver.resolve(host, rtype)
        return [r.to_text().rstrip(".") for r in answer]
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers,
            dns.exception.Timeout):
        return []
    except Exception:
        return []


async def _resolve_chain(resolver: dns.asyncresolver.Resolver, host: str,
                         max_hops: int = 6) -> tuple[list[str], list[str]]:
    """Follow CNAME chain, returning (cname_chain, ip_addresses)."""
    chain: list[str] = []
    current = host
    for _ in range(max_hops):
        cnames = await _resolve(resolver, current, "CNAME")
        if not cnames:
            break
        chain.append(cnames[0])
        current = cnames[0]
    ips = await _resolve(resolver, current, "A")
    return chain, ips


def _tls_hostname(url: str) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    return parsed.hostname


async def _fetch_tls(host: str, port: int = 443, timeout: float = 8.0) -> dict:
    """Fetch TLS cert and parse issuer/subject/expiry."""
    loop = asyncio.get_event_loop()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ctx, server_hostname=host),
            timeout=timeout,
        )
    except Exception as exc:
        return {"error": str(exc)}
    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        if not ssl_obj:
            return {"error": "no ssl object"}
        der = ssl_obj.getpeercert(binary_form=True)
        if not der:
            return {"error": "no cert"}
        cert = x509.load_der_x509_certificate(der, default_backend())
        issuer = cert.issuer.rfc4514_string()
        subject = cert.subject.rfc4514_string()
        # cryptography 44+: use not_valid_after_utc
        expiry = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return {
            "issuer": issuer,
            "subject": subject,
            "expiry": expiry,
            "valid": expiry > datetime.now(timezone.utc) if expiry else None,
        }
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def scan_one(
    *,
    website_id: str,
    url: str,
    resolver: dns.asyncresolver.Resolver,
    http: httpx.AsyncClient,
    geoip: GeoIP,
) -> ScanResult:
    started = time.monotonic()
    result = ScanResult(
        website_id=website_id,
        url=url,
        scanned_at=datetime.now(timezone.utc),
    )

    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        result.error = "invalid url"
        result.classification = classify(None, None, None, [], None, None, [], True)
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    # ── DNS ─────────────────────────────────────────────────────
    try:
        chain, ips = await _resolve_chain(resolver, host)
        ns_records = await _resolve(resolver, ".".join(host.split(".")[-2:]), "NS")
        mx_records = await _resolve(resolver, ".".join(host.split(".")[-2:]), "MX")
        result.cname_chain = chain
        result.ip_addresses = ips
        result.nameservers = ns_records
        result.mx_records = mx_records
    except Exception as exc:
        result.error = f"dns: {exc}"

    # ── GeoIP ───────────────────────────────────────────────────
    if result.ip_addresses:
        primary = result.ip_addresses[0]
        info = geoip.lookup(primary)
        result.ip_country = info.country
        result.ip_region  = info.region
        result.ip_city    = info.city
        result.ip_latitude  = info.latitude
        result.ip_longitude = info.longitude
        result.ip_asn = info.asn_number
        result.ip_org = info.asn_org

    # ── TLS ─────────────────────────────────────────────────────
    tls_host = _tls_hostname(url)
    if tls_host:
        tls = await _fetch_tls(tls_host)
        if "error" not in tls:
            result.tls_issuer = tls.get("issuer")
            result.tls_subject = tls.get("subject")
            result.tls_expiry = tls.get("expiry")
            result.tls_valid = tls.get("valid")

    # ── HTTP ────────────────────────────────────────────────────
    try:
        resp = await http.get(url, follow_redirects=True)
        result.http_status = resp.status_code
        result.http_server_header = resp.headers.get("server")
        result.http_powered_by = resp.headers.get("x-powered-by")
        result.http_final_url = str(resp.url)
    except Exception as exc:
        if not result.error:
            result.error = f"http: {type(exc).__name__}"

    # ── Classify ────────────────────────────────────────────────
    result.classification = classify(
        ip_country=result.ip_country,
        ip_org=result.ip_org,
        ip_asn=result.ip_asn,
        cname_chain=result.cname_chain,
        http_server_header=result.http_server_header,
        http_powered_by=result.http_powered_by,
        nameservers=result.nameservers,
        had_error=bool(result.error) or not result.ip_addresses,
    )

    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


async def persist(db: Database, res: ScanResult) -> str:
    """Insert scan row and derived changes. Returns the new scan id."""
    c = res.classification
    raw = {
        "tls_subject": res.tls_subject,
        "http_final_url": res.http_final_url,
    }

    # Find previous scan BEFORE inserting the new one — keeps the compare logic simple.
    prev = await db.fetchrow(
        """
        SELECT id, ip_addresses, ip_country, ip_city, hosting_provider, hosting_country,
               sovereignty_tier, cdn_detected, cms_detected, tls_issuer, scanned_at
        FROM infrastructure_scans
        WHERE website_id = $1
        ORDER BY scanned_at DESC LIMIT 1
        """,
        res.website_id,
    )

    row = await db.fetchrow(
        """
        INSERT INTO infrastructure_scans (
          website_id, scanned_at, ip_addresses, cname_chain, nameservers, mx_records,
          ip_country, ip_region, ip_city, ip_latitude, ip_longitude, ip_asn, ip_org,
          hosting_provider, hosting_country, datacenter_region, sovereignty_tier,
          cdn_detected, cms_detected,
          tls_issuer, tls_subject, tls_expiry, tls_valid,
          http_status, http_server_header, http_powered_by, http_final_url,
          duration_ms, error, raw_data
        ) VALUES (
          $1,$2,$3::text[],$4::text[],$5::text[],$6::text[],
          $7,$8,$9,$10,$11,$12,$13,
          $14,$15,$16,$17,
          $18,$19,
          $20,$21,$22,$23,
          $24,$25,$26,$27,
          $28,$29,$30::jsonb
        )
        RETURNING id
        """,
        res.website_id, res.scanned_at,
        res.ip_addresses, res.cname_chain, res.nameservers, res.mx_records,
        res.ip_country, res.ip_region, res.ip_city, res.ip_latitude, res.ip_longitude,
        res.ip_asn, res.ip_org,
        c.hosting_provider, c.hosting_country, c.datacenter_region, c.sovereignty_tier,
        c.cdn_detected, c.cms_detected,
        res.tls_issuer, res.tls_subject, res.tls_expiry, res.tls_valid,
        res.http_status, res.http_server_header, res.http_powered_by, res.http_final_url,
        res.duration_ms, res.error, orjson.dumps(raw).decode(),
    )
    scan_id = row["id"]

    # Write changes
    if prev:
        changes = compare_scans(prev, res)
        for ch in changes:
            await db.execute(
                """
                INSERT INTO scan_changes
                  (website_id, from_scan_id, to_scan_id, change_type,
                   old_value, new_value, severity, details, summary)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                res.website_id, prev["id"], scan_id, ch["type"],
                ch["old"], ch["new"], ch["severity"],
                orjson.dumps(ch.get("details", {})).decode(),
                ch["summary"],
            )
        changed = bool(changes)
    else:
        changed = False

    await db.execute(
        """
        UPDATE websites
        SET last_scanned_at = $2,
            last_changed_at = CASE WHEN $3::boolean THEN $2 ELSE last_changed_at END,
            scan_failures = CASE WHEN $4::text IS NULL THEN 0 ELSE scan_failures + 1 END
        WHERE id = $1
        """,
        res.website_id, res.scanned_at, changed, res.error,
    )

    return scan_id


async def scan_all(
    db: Database,
    *,
    limit: Optional[int] = None,
    stale_hours: int = 24,
    concurrency: Optional[int] = None,
    owner_type: Optional[str] = None,
) -> None:
    concurrency = concurrency or int(os.environ.get("SCANNER_CONCURRENCY", 16))
    http_timeout = float(os.environ.get("SCANNER_HTTP_TIMEOUT", 15))
    dns_timeout = float(os.environ.get("SCANNER_DNS_TIMEOUT", 5))
    user_agent = os.environ.get(
        "SCANNER_USER_AGENT",
        "CanadianPoliticalDataBot/1.0 (+https://canadianpoliticaldata.ca)",
    )
    city_path = os.environ.get("GEOIP_CITY_PATH", "/data/GeoLite2-City.mmdb")
    asn_path = os.environ.get("GEOIP_ASN_PATH", "/data/GeoLite2-ASN.mmdb")

    # Pick websites to scan
    conds = ["w.is_active = true"]
    params: list = []
    if stale_hours > 0:
        conds.append(f"(w.last_scanned_at IS NULL OR w.last_scanned_at < now() - interval '{stale_hours} hours')")
    if owner_type:
        params.append(owner_type)
        conds.append(f"w.owner_type = ${len(params)}")
    where = " AND ".join(conds)
    sql = f"SELECT w.id, w.url FROM websites w WHERE {where} ORDER BY w.last_scanned_at NULLS FIRST"
    if limit:
        sql += f" LIMIT {int(limit)}"

    rows = await db.fetch(sql, *params)
    if not rows:
        console.print("[yellow]No websites due for scan[/yellow]")
        return
    console.print(f"[cyan]Scanning {len(rows)} websites with concurrency={concurrency}[/cyan]")

    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = dns_timeout
    resolver.lifetime = dns_timeout
    resolver.nameservers = ["1.1.1.1", "8.8.8.8"]

    geoip = GeoIP(city_path=city_path, asn_path=asn_path)

    async with httpx.AsyncClient(
        timeout=http_timeout,
        headers={"User-Agent": user_agent},
        verify=False,  # we collect TLS info separately; many political sites have weird certs
        http2=False,
    ) as http:
        sem = asyncio.Semaphore(concurrency)

        async def worker(row) -> None:
            async with sem:
                try:
                    res = await scan_one(
                        website_id=str(row["id"]),
                        url=row["url"],
                        resolver=resolver,
                        http=http,
                        geoip=geoip,
                    )
                    await persist(db, res)
                except Exception as exc:
                    console.print(f"[red]scan failed {row['url']}: {exc}[/red]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Scanning", total=len(rows))

            async def tracked(row) -> None:
                try:
                    await worker(row)
                finally:
                    progress.update(task, advance=1)

            await asyncio.gather(*(tracked(r) for r in rows))

    geoip.close()

    # Refresh map views so the API sees the new data
    try:
        await db.execute("SELECT refresh_map_views();")
    except Exception as exc:
        console.print(f"[yellow]view refresh skipped: {exc}[/yellow]")
    console.print("[green]Scan complete.[/green]")
