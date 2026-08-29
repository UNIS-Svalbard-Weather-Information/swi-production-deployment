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
  -subj "/CN=*.docker.localhost"
```

The `tls.yaml` in `dynamic/` already points at these files. `*.docker.localhost` and
`*.localhost` hostnames resolve to `127.0.0.1` automatically on most systems (Windows,
macOS, and modern Linux resolvers) — no `/etc/hosts` edits needed.

## 3. Start Traefik

```bash
docker compose -f traefik-testing/compose.yml up -d
```

The dashboard is at `https://dashboard.docker.localhost` (password `test123`, per the
basic-auth hash already in `compose.yml` — a local-only convenience credential, not a real
secret).

## 4. Start the main stack

```bash
cp .env.example .env   # values already default to *.localhost hosts, fine as-is for local testing
docker compose -f compose.yml up -d
```

Note: locally this runs as plain Docker Compose, so the `deploy:` keys (replicas, rolling
`start-first` updates) are ignored — that's fine for functional/CORS testing. To also
exercise the zero-downtime rolling-update behavior itself, initialize a local single-node
Swarm instead (`docker swarm init`) and deploy with `docker stack deploy -c compose.yml
swi`.

Give services a minute to pass their `start_period` healthchecks, then check status:

```bash
docker compose ps
```

## 5. Verify routing

- MapProxy: `https://mapserver.localhost/`
- Met observations API: `https://api.localhost/public/`
- Titiller (tiles): `https://api.localhost/tiles/`
- Elevation API: `https://api.localhost/oeapi/`

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
