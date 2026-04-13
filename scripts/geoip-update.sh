#!/usr/bin/env bash
# Refresh MaxMind GeoLite2 databases. Reads MAXMIND_LICENSE_KEY from .env.
# Usage: scripts/geoip-update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env"; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a
[ -n "${MAXMIND_LICENSE_KEY:-}" ] || { echo "MAXMIND_LICENSE_KEY not set in .env"; exit 1; }

mkdir -p data
for ed in GeoLite2-City GeoLite2-ASN; do
    echo "→ refreshing $ed"
    tmp="data/$ed.tar.gz"
    curl -fsSL -o "$tmp" \
        "https://download.maxmind.com/app/geoip_download?edition_id=${ed}&license_key=${MAXMIND_LICENSE_KEY}&suffix=tar.gz"
    tar -xzf "$tmp" -C data --strip-components=1 --wildcards "*/${ed}.mmdb"
    rm -f "$tmp"
done
echo "✓ done — restart scanner to pick up the new files"
