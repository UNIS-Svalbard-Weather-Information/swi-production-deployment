# swi-production-deployment

Deploys the SWI production stack to [Dokploy](https://dokploy.com) as a single Docker Swarm
stack (`compose.yml`, single node, chosen for Dokploy's zero-downtime rolling-update
support).

## Run it locally

Everything below runs the real stack on your own machine, behind the same Traefik routing
and TLS setup Dokploy uses in production - the fastest way to see whether a change actually
works before it goes anywhere near a promotion PR.

**Prerequisites**: Docker Desktop (or another Docker Engine + Compose v2), and `openssl`
(for the local TLS cert - already on macOS/Linux; on Windows, Git Bash ships it).

```bash
# 1. Create the external network Dokploy provides in production
docker network create dokploy-network

# 2. Generate a local TLS cert and start Traefik (full detail: traefik-testing/readme.md)
cd traefik-testing
mkdir -p certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/local.key -out certs/local.crt -subj "/CN=*.localhost"
docker compose -f compose.yml up -d
cd ..

# 3. Configure your local .env
cp .env.example .env
```

Now edit `.env`: everything already defaults to `*.localhost` hosts, fine as-is for local
testing, but set **`SWI_SERVICE_REPLICAS=1`** - the example file defaults to `3` (production's
value) since it's meant to double as a template for real deployment, and running 9
containers for 3 services on a laptop is unnecessary weight for a dev loop.

```bash
# 4. Bring the stack up (cron containers excluded - they need real upstream API keys,
#    see API_KEYS.md if you want to run those too)
docker compose -f compose.yml up -d redis mapproxy-server met-public-api met-tilling-api elevation_api

# Give services a minute to pass their start_period healthchecks
docker compose ps
```

**Try it**:
- MapProxy: `https://mapserver.localhost/`
- Met observations API: `https://api.localhost/public/health` (the bare `/public/` root
  404s - neither this API nor Titiller define a root handler, only specific routes)
- Titiller (tiles): `https://api.localhost/tiles/health`
- Elevation API: `https://api.localhost/oeapi/v1/lookup?locations=78.22,15.62`
- Traefik dashboard: `https://dashboard.localhost` (password `test123`, a local-only
  convenience credential, not a real secret)

All through the self-signed local cert - pass `-k`/`--insecure` with `curl`, or click
through the browser warning.

**A CORS asymmetry to know about going in**: mapproxy, titiller, and elevation_api enforce
CORS at the Traefik layer (a `*cors` middleware label on each router in `compose.yml`);
`met-public-api` instead enforces it at the application level (FastAPI's own CORS
middleware, driven by the same `CORS_ALLOWED_ORIGINS` env var). Both end up producing the
same `Access-Control-Allow-Origin` header from the client's point of view, just added by a
different layer - this is intentional (stacking both on `met-public-api` would produce
duplicate/conflicting headers), not something to "fix" if you notice it while poking around
Traefik's config. See the comment at the top of `compose.yml` and
`traefik-testing/readme.md` (which also covers verifying it directly with `curl`) for more.

**Tear down**:
```bash
docker compose -f compose.yml down -v
docker compose -f traefik-testing/compose.yml down -v
docker network rm dokploy-network
```

## What's in the stack

`compose.yml` is one file, organized into three commented sections:

| Section | Services | Source repo |
|---|---|---|
| map-service | `mapproxy-server`, `CRON_seaice_cache`, `CRON_avalanche_cache` | `swi-mapproxy`, `swi-mapcache-seaice`, `swi-avalanche-caching` |
| met-service | `met-public-api`, `met-tilling-api`, `CRON_metobs_cache`, `CRON_AAforecast_cache` | `swi-metobs-backend`, `swi-titiller`, `swi-metobs-caching`, `swi-aromearctic-caching` |
| elevation-service | `elevation_api` | `swi-elevationapi` |

Plus a shared `redis` used by both mapproxy (tile cache) and met-public-api (response
cache). Routing and CORS are handled by Traefik via Docker labels (Dokploy runs Traefik on
the `dokploy-network` external network) - see the CORS note above for the one exception.

`mapproxy-server`, `met-public-api`, and `met-tilling-api` read their replica count from
`SWI_SERVICE_REPLICAS` (default `3`, production's value - unset in Dokploy, it behaves
exactly as before this variable existed). `elevation_api` always runs 1 - it's already
minimal in both environments, nothing to override.

Two repos feed config into running containers at boot rather than build time —
`swi-mapproxy-configuration` (mapproxy.yaml) and `swi-metobs-station-configuration`
(station lists) are `git pull`ed live by `swi-mapproxy` and `swi-metobs-caching`
respectively. A change there takes effect on the next container restart, not a new image
tag.

## Secrets

Real values for everything in `.env.example` live in Dokploy's own environment-variable
UI — nothing sensitive is committed here. See `API_KEYS.md` for exactly which env vars are
real third-party API keys, what each one is for, and how to get one.

## Release flow: `staging/X.Y.Z` → `pre-prod` → `main`

1. **`staging/X.Y.Z`** — one branch per release cycle, cut from `main` the moment the
   previous cycle ships. Renovate opens image-bump PRs here as new versions land on GHCR
   (`renovate.json`, `baseBranchPatterns: ["/^staging\\//"]`, auto-merge for those bumps).
   `info/version.json`'s `release` field is kept continuously up to date during the cycle —
   its value is derived automatically from the highest-severity version bump seen across
   the services that actually changed (see `update-info.py`), not incremented by hand. A
   collaborator with write access can also mark the cycle as a pre-release line by
   commenting `/alpha` or `/beta` (`/stable` to clear it) on this branch's promotion PR —
   see `.github/workflows/set-prerelease-type.yml`.
2. Merging the staging branch into **`pre-prod`** (a PR opened and kept up to date
   automatically - `ensure-preprod-pr.yml`): mints a GitHub pre-release at that version
   (`X.Y.Z-rc1`, or `X.Y.Z-alphaN-rc1` if marked alpha/beta), and Dokploy auto-deploys
   `pre-prod` to staging. The staging branch is *not* retired here - a fix can still land
   on it and get re-promoted, minting `-rc2`, `-rc3`, etc.
3. A standing PR **`pre-prod` → `main`** is kept up to date automatically. It's gated by a
   smoke-test workflow that actually brings the stack up and checks every service's
   healthcheck (plus the CORS checks from `traefik-testing/readme.md`) before the PR is
   mergeable — a broken stack can't reach production. It also blocks the merge outright if
   a staging fix is still mid-promotion, so production can't ship ahead of it. Merging it
   mints the real GitHub Release (`X.Y.Z`, or `X.Y.Z-alphaN` marked as a pre-release if the
   cycle was tagged alpha/beta), creates a `release/X.Y.Z` branch as a pure historical
   marker (never touched again after creation), syncs `pre-prod` to match what just shipped,
   and cuts the next `staging/X.Y.Z+1` cycle - all with no manual steps beyond the two
   promotion-PR merges.

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

Made with puffins and polar bears helped by Claude :-)
