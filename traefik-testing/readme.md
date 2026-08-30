# Local Traefik testing

A local harness for exercising the full stack (`../compose.yml`) behind Traefik with TLS,
the same way Dokploy routes it in production — so routing and CORS behavior can be checked
before promoting a release. This is for local functional testing; it is not a production
deployment.

## 1. Create the external network

Both this Traefik stack and the main `compose.yml` expect an external `dokploy-network` to
already exist (Dokploy creates this on the real host; locally you create it once yourself):

```bash
docker network create dokploy-network
```

## 2. Generate a local TLS certificate

```bash
mkdir -p certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/local.key -out certs/local.crt \
  -subj "/CN=*.localhost"
```

The `tls.yaml` in `dynamic/` already points at these files. `*.localhost` covers every
hostname used here in one cert - the routed services (`mapserver.localhost`,
`api.localhost`) and the Traefik dashboard (`dashboard.localhost`) are all a single level
under `.localhost`, deliberately (a cert for `*.docker.localhost` would *not* cover
`mapserver.localhost` - different wildcard scope - and `curl -k`/`--insecure` won't catch
that mismatch since it skips hostname verification entirely; a real browser will). These
hostnames resolve to `127.0.0.1` automatically on most systems (Windows, macOS, and modern
Linux resolvers) — no `/etc/hosts` edits needed.

## 3. Start Traefik

```bash
docker compose -f traefik-testing/compose.yml up -d
```

The dashboard is at `https://dashboard.localhost` (password `test123`, per the basic-auth
hash already in `compose.yml` — a local-only convenience credential, not a real secret).

## 4. Start the main stack

```bash
cp .env.example .env
# Then edit .env: set SWI_SERVICE_REPLICAS=1 (see the root README's "Run it locally")
# so this starts 1 container per service instead of production's 3 - everything else in
# .env.example already defaults to *.localhost hosts, fine as-is for local testing.
docker compose -f compose.yml up -d
```

Note: modern Docker Compose (verified with 29.3.1) honors `deploy.replicas` even outside
Swarm mode — bringing this up with plain `docker compose up` really does start
`SWI_SERVICE_REPLICAS` copies of mapproxy-server/met-public-api/met-tilling-api (3 if you
didn't override it). It does *not* honor `update_config` (the `start-first` rolling-update
behavior), since that's a Swarm scheduler feature with no plain-Compose equivalent. To
exercise the actual rolling-update behavior, initialize a local single-node Swarm instead
(`docker swarm init`) and deploy with `docker stack deploy -c compose.yml swi`.

Give services a minute to pass their `start_period` healthchecks, then check status:

```bash
docker compose ps
```

## 5. Verify routing

- MapProxy: `https://mapserver.localhost/`
- Met observations API: `https://api.localhost/public/health` (the bare `/public/` root
  404s - neither this API nor Titiller define a root handler, only specific routes)
- Titiller (tiles): `https://api.localhost/tiles/health`
- Elevation API: `https://api.localhost/oeapi/v1/lookup?locations=78.22,15.62`

(all through the self-signed cert — pass `-k`/`--insecure` with `curl`, or accept the
browser warning, since it's a local CA)

## 6. Verify CORS

Each of these should return an `Access-Control-Allow-Origin` header reflecting
`CORS_ALLOWED_ORIGINS` from `.env` (default `*`). mapproxy, `met-tilling-api` (titiller),
and `elevation_api` enforce this via a Traefik middleware; `met-public-api` (metobs-backend)
enforces it at the application level instead (see `compose.yml`'s top comment for why that
one's intentionally different) — check it the same way, the header should still appear,
just added by the app rather than Traefik.

```bash
curl -k -I -H "Origin: https://example.com" https://mapserver.localhost/
curl -k -I -H "Origin: https://example.com" https://api.localhost/tiles/
curl -k -I -H "Origin: https://example.com" https://api.localhost/oeapi/
curl -k -I -H "Origin: https://example.com" https://api.localhost/public/
```

Look for `access-control-allow-origin` in the response headers of each. A missing header on
one route (and present on the others) usually means that service's Traefik CORS middleware
label isn't applied to its router — check the `traefik.http.routers.<name>.middlewares=`
label includes the `*cors` middleware for that service in `compose.yml`.

## 7. Tear down

```bash
docker compose -f compose.yml down
docker compose -f traefik-testing/compose.yml down
docker network rm dokploy-network
```
