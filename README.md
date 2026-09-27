<div align="center">

# 🍚 arroz — my homelab

**One box, 19 self-hosted services, no port forwarding: remote access is Tailscale only.**

<p>
  <img src="https://img.shields.io/badge/Docker_Compose-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker Compose">
  <img src="https://img.shields.io/badge/Caddy-1F88C0?style=flat-square&logo=caddy&logoColor=white" alt="Caddy">
  <img src="https://img.shields.io/badge/Tailscale-242424?style=flat-square&logo=tailscale&logoColor=white" alt="Tailscale">
  <img src="https://img.shields.io/badge/AdGuard_Home-68BC71?style=flat-square&logo=adguard&logoColor=white" alt="AdGuard Home">
  <img src="https://img.shields.io/badge/Komodo-2E3440?style=flat-square&logo=rust&logoColor=white" alt="Komodo">
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/services-19-8A2BE2?style=flat-square" alt="19 services">
</p>

</div>

This is the full config for the Linux box under my desk (hostname `arroz`, Portuguese for rice). It runs the stuff my family and I actually use every day: passwords, shopping lists, fitness tracking, location history, Spotify stats, a retro game library. It also runs a couple of things I wrote myself.

Every service is a small Docker Compose stack in its own folder. They all share one `homelab` network behind a single Caddy reverse proxy. Nothing is exposed to the public internet. At home I reach everything through local DNS names, and away from home I go through Tailscale.

## Architecture

- **Two ways in, one proxy.** Plain-HTTP `*.homelab` names for the LAN, resolved by AdGuard. Services that need HTTPS (Vaultwarden, the PWAs, OAuth callbacks) get their own port on the Tailscale hostname, using a real cert from `tailscale cert`.
- **Databases stay private.** Postgres, MariaDB, Mongo and Redis only live on per-stack internal networks. Only the frontends join `homelab`.
- **Ops:** [Homepage](homepage/) is the dashboard, [Komodo](komodo/) manages the stacks, [WUD](wud/) checks for image updates every morning, and [Netdata](netdata/) watches the host.

## Services

| | Service | What it does here |
|---|---|---|
| **Infra** | [AdGuard Home](https://github.com/AdguardTeam/AdGuardHome) | Network-wide DNS, ad blocking and the `*.homelab` names |
| | [Caddy](https://caddyserver.com) | The one reverse proxy in front of everything |
| | [Homepage](https://gethomepage.dev) | Dashboard with live widgets for every service |
| | [Komodo](https://komo.do) | Web UI to deploy and manage the compose stacks |
| | [What's Up Docker](https://getwud.github.io/wud/) | Tells me when an image has a new version |
| | [Netdata](https://www.netdata.cloud) | Real-time host and container metrics |
| **Family** | [Vaultwarden](https://github.com/dani-garcia/vaultwarden) | Bitwarden-compatible password manager |
| | [KitchenOwl](https://kitchenowl.org) | Shared grocery list and recipes |
| | [Dawarich](https://dawarich.app) | Self-hosted Google Timeline replacement |
| | [SparkyFitness](https://github.com/CodeWithCJ/SparkyFitness) | Nutrition and workout tracking |
| | [Home Assistant](https://www.home-assistant.io) | Home automation |
| **Productivity** | [ConvertX](https://github.com/C4illin/ConvertX) | Convert any file to any format |
| | [Stirling PDF](https://www.stirlingpdf.com) | Every PDF tool you'd ever need |
| | [Excalidraw](https://excalidraw.com) | Whiteboard and diagrams |
| | [Obsidian LiveSync](https://github.com/vrtmrz/obsidian-livesync) | CouchDB backend syncing my Obsidian vault across devices |
| | **[PR Review](prreview/)** ⭐ | *Custom:* static-analysis bot for my GitHub PRs |
| **Fun** | [Your Spotify](https://github.com/Yooooomi/your_spotify) | Spotify Wrapped, but all year round |
| | [RomM](https://romm.app) | Retro game library that plays in the browser |
| | **[insta-bot](insta-bot/)** ⭐ | *Custom:* answers League of Legends invites in the group chat |

## Things I built

### 🔍 [`prreview/`](prreview/): self-hosted PR reviewer
A Python service that polls GitHub for open PRs and mirrors each repo. It runs **ruff, bandit, semgrep** plus a raw syntax check (YAML/JSON/XML) on the files the PR changes, separates findings on the lines it actually touched from the rest, and publishes an HTML report at `prreview.homelab`. It remembers the head SHA of every PR, so each push is analysed exactly once. The GitHub token goes through `GIT_CONFIG_*` env vars, never argv or a stored remote URL, so it never shows up in `ps`.

### 🎮 [`insta-bot/`](insta-bot/): "bora" as a service
My friends invite each other to play LoL in an Instagram group chat, and this bot answers for me. It drives a real headful Chrome under Xvfb with Playwright and watches the thread with a DOM `MutationObserver`. When someone sends an invite ("siga jogar?", "alguém rankeds?", "flex?") it replies with a random "bora", with a global cooldown and accent-insensitive wildcard triggers. It runs as a systemd timer during evening hours only, and a League-themed control panel behind Caddy basic auth handles on/off, dry-run, schedule, triggers and replies, all live without a restart.

### 💾 [`scripts/backup.sh`](scripts/backup.sh): consistent backups the dumb way
It stops every container, tars the homelab folder, the Docker volumes and the certs from a throwaway Alpine container, then starts everything again. About 60 seconds of downtime buys a crash-consistent copy of every Postgres, Mongo and SQLite database, with zero per-service dump logic. It also warns about any new bind mount outside the backed-up paths.

## Secrets

Nothing sensitive lives in this repo:

- Every stack reads its credentials from a local `.env`, which is gitignored. Each one has a committed **`.env.example`** that lists the keys it needs.
- Machine-specific values (my tailnet hostname and Tailscale IP) are env vars too: `${TS_HOST}` in compose, `{$TS_HOST}` in the Caddyfile, `{{HOMEPAGE_VAR_TS_HOST}}` in Homepage.
- App data (`data/`, databases, Home Assistant config, logs) is gitignored.

## Layout

```
.
├── caddy/            Caddyfile + reverse proxy stack
├── <service>/        docker-compose.yml, .env.example, config (if any)
├── homepage/config/  dashboard: services, widgets, theme
├── prreview/         custom PR reviewer (Dockerfile + Python)
├── insta-bot/        custom Instagram bot (Python + systemd units + web panel)
└── scripts/          backup
```

## Running a stack

```bash
docker network create homelab          # once
cd vaultwarden
cp .env.example .env && $EDITOR .env
docker compose up -d
```

For the Tailscale HTTPS ports, put the cert from `tailscale cert <host>` in `/etc/caddy/certs/` and set `TS_HOST` in `caddy/.env`.

---

<div align="center"><sub>Built and broken at home by <a href="https://github.com/Bebaz0">@Bebaz0</a>.</sub></div>
