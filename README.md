# swi-production-deployment

Deploys the SWI production stack to [Dokploy](https://dokploy.com) as a single Docker Swarm
stack (`compose.yml`, single node, chosen for Dokploy's zero-downtime rolling-update
support).

## What's in the stack

`compose.yml` is one file, organized into three commented sections:

| Section | Services | Source repo |
|---|---|---|
| map-service | `mapproxy-server`, `CRON_seaice_cache`, `CRON_avalanche_cache` | `swi-mapproxy`, `swi-mapcache-seaice`, `swi-avalanche-caching` |
| met-service | `met-public-api`, `met-tilling-api`, `CRON_metobs_cache`, `CRON_AAforecast_cache` | `swi-metobs-backend`, `swi-titiller`, `swi-metobs-caching`, `swi-aromearctic-caching` |
| elevation-service | `elevation_api` | `swi-elevationapi` |

Plus a shared `redis` used by both mapproxy (tile cache) and met-public-api (response
cache). Routing and CORS are handled by Traefik via Docker labels (Dokploy runs Traefik on
the `dokploy-network` external network); `met-public-api` is the one service that instead
enforces CORS at the application level — see the comment at the top of `compose.yml`.

Two repos feed config into running containers at boot rather than build time —
`swi-mapproxy-configuration` (mapproxy.yaml) and `swi-metobs-station-configuration`
(station lists) are `git pull`ed live by `swi-mapproxy` and `swi-metobs-caching`
respectively. A change there takes effect on the next container restart, not a new image
tag.

## Secrets

Real values for everything in `.env.example` live in Dokploy's own environment-variable
UI — nothing sensitive is committed here.

## Release flow: `release/X.Y.Z` → `pre-prod` → `main`

1. **`release/X.Y.Z`** — one branch per release cycle, cut from `main`. Renovate opens
   image-bump PRs here as new versions land on GHCR (`renovate.json`,
   `baseBranchPatterns: ["/^release\\//"]`, auto-merge for those bumps). `info/version.json`'s
   `release` field is kept continuously up to date during the cycle — its value is derived
   automatically from the highest-severity version bump seen across the services that
   actually changed (see `update-info.py`), not incremented by hand.
2. Merging the release branch into **`pre-prod`**: mints a GitHub pre-release at that
   version, locks the release branch (kept as a historical record, no further pushes), and
   Dokploy auto-deploys `pre-prod` to staging.
3. A standing PR **`pre-prod` → `main`** is kept up to date automatically. It's gated by a
   smoke-test workflow that actually brings the stack up and checks every service's
   healthcheck (plus the CORS checks from `traefik-testing/readme.md`) before the PR is
   mergeable — a broken stack can't reach production. Merging it mints the real GitHub
   Release and Dokploy auto-deploys `main` to production; the next `release/X.Y.Z+1` branch
   is cut immediately after.

See `.github/workflows/` for the workflows implementing each step, and
`traefik-testing/readme.md` for how to exercise the stack locally before relying on the
smoke test alone.

## Per-service repos

The 8 image-producing services (excluding `swi-mapproxy`, which still tags manually — its
tag scheme isn't semver) use [release-please](https://github.com/googleapis/release-please)
for their own releases: commits accumulate into a standing release PR, and merging it cuts
the tag that triggers that repo's existing `docker-build-push.yml`. Renovate is also enabled
in each of those repos for their own dependency/Action updates, independent of the
deployment-repo automation above.
