#!/usr/bin/env bash
# ponytail: stop-the-world snapshot. ~60s downtime buys a consistent copy of every
# DB (postgres, mongo, sqlite) with zero per-service dump logic, and stays correct
# for services added later. Move to live pg_dump/mongodump only if downtime bites.
#
# Restore: as root on a fresh host, tar xzf homelab-YYYY-MM-DD.tar.gz -C /
#          then `docker compose up -d` in each ~/homelab/<service> dir.
set -euo pipefail

OUT=$HOME/backups
mkdir -p "$OUT"

# ponytail: a new service binding a path outside the three roots gets named, not guessed at.
docker ps -q | xargs -r docker inspect -f \
  "{{range .Mounts}}{{if eq .Type \"bind\"}}{{println .Source}}{{end}}{{end}}" \
  | grep "^/" | grep -Ev "^(/home/bebaz/homelab|/etc/caddy)" \
  | grep -Ev "^(/proc|/sys|/var/log|/etc/(os-release|passwd|group|localtime)|/var/run/docker.sock|/)$" \
  | sort -u | sed "s/^/WARNING: not backed up: /" || true

RUNNING=$(docker ps -q | tr "\n" " ")
# ponytail: restart order is docker ps order, not dependency order. Apps retry; fine here.
trap "docker start $RUNNING >/dev/null" EXIT
docker stop $RUNNING >/dev/null

# ponytail: tar as root in a container - bebaz cannot read root-owned volume data and
# sudo wants a password, but the docker group already grants root.
docker run --rm -v /:/host:ro -v "$OUT:/out" alpine \
  tar czf "/out/homelab-$(date +%F).tar.gz" \
    --exclude="var/lib/docker/volumes/netdata_netdatacache" \
    --exclude="home/bebaz/homelab/homepage/config/logs" \
    -C /host home/bebaz/homelab var/lib/docker/volumes etc/caddy

find "$OUT" -name "homelab-*.tar.gz" -mtime +2 -delete
