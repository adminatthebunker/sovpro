"""Thin wrapper around MaxMind GeoLite2 City + ASN databases."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import geoip2.database
import geoip2.errors

log = logging.getLogger(__name__)


@dataclass
class GeoInfo:
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    asn_number: Optional[str] = None
    asn_org: Optional[str] = None


class GeoIP:
    """Reader pair for GeoLite2-City and GeoLite2-ASN.

    If the database files aren't present, lookups degrade gracefully to empty
    results so the rest of the scanner keeps working in dev.
    """

    def __init__(self, city_path: str, asn_path: str) -> None:
        self._city = self._open(city_path)
        self._asn = self._open(asn_path)

    @staticmethod
    def _open(path: str):
        if not os.path.isfile(path):
            log.warning("GeoIP DB missing at %s — lookups will return empty", path)
            return None
        try:
            return geoip2.database.Reader(path)
        except Exception as exc:
            log.warning("Could not open %s: %s", path, exc)
            return None

    def lookup(self, ip: str) -> GeoInfo:
        info = GeoInfo()
        if self._city is not None:
            try:
                r = self._city.city(ip)
                info.country = r.country.iso_code
                info.region = r.subdivisions.most_specific.name if r.subdivisions else None
                info.city = r.city.name
                info.latitude = r.location.latitude
                info.longitude = r.location.longitude
            except (geoip2.errors.AddressNotFoundError, ValueError):
                pass
            except Exception as exc:
                log.debug("city lookup failed for %s: %s", ip, exc)
        if self._asn is not None:
            try:
                r = self._asn.asn(ip)
                info.asn_number = f"AS{r.autonomous_system_number}" if r.autonomous_system_number else None
                info.asn_org = r.autonomous_system_organization
            except (geoip2.errors.AddressNotFoundError, ValueError):
                pass
            except Exception as exc:
                log.debug("asn lookup failed for %s: %s", ip, exc)
        return info

    def close(self) -> None:
        for r in (self._city, self._asn):
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
