# API keys

Confirmed by reading the actual data-source code in `swi-metobs-caching` (not just this
repo's `.env.example`) — only **two** third-party API keys are needed anywhere in this
stack, both consumed by the same container: `CRON_metobs_cache`. Every other cron job
(`CRON_seaice_cache`, `CRON_avalanche_cache`, `CRON_AAforecast_cache`) hits keyless public
endpoints and needs nothing here.

| Env var (this repo) | Passed to the container as | Used by |
|---|---|---|
| `SWI_METOBS_HOLFUY_API_KEY` | `SWI_HOLFUY_API_KEY` | `HolfuySource` |
| `SWI_METOBS_FROST_API_KEY` | `SWI_FROST_API_KEY` | `FrostSource`, `FrostBoatSource` |

Both are declared as required in `compose.yml` (`${SWI_METOBS_HOLFUY_API_KEY?Variable not
set}` / the Frost equivalent) — if either is missing or blank in `.env`, `docker compose up`
refuses to even start `CRON_metobs_cache`, with an explicit "Variable not set" error rather
than a silent failure. `swi-metobs-caching`'s two other data source classes
(`IWINFixedSource`, `IWOOSSource`) accept an `api_key` parameter in code but never read one
from the environment - they're not wired to a key here, and aren't affected by either of the
two below.

## `SWI_METOBS_HOLFUY_API_KEY`

Authenticates against [Holfuy](https://holfuy.com)'s live/archive station API
(`api.holfuy.com`) — `HolfuySource` sends it as the `pw` query parameter on every request.
Holfuy keys are tied to a Holfuy account/station subscription, not a public self-service
signup page — get one through your organization's Holfuy account, or by contacting Holfuy
directly if this project doesn't already have one.

## `SWI_METOBS_FROST_API_KEY`

Authenticates against [Frost](https://frost.met.no), MET Norway's official weather-data API
— `FrostSource`/`FrostBoatSource` send it as the HTTP Basic Auth username (empty password).
Unlike Holfuy, Frost credentials are free, self-service, and take a minute to get: register
at <https://frost.met.no/auth/requestCredentials.html> with an email address, and the client
ID it gives you *is* this key (no separate secret to look up).

## What's *not* a third-party key

`SWI_REDIS_PWD` looks like it belongs in this document but doesn't - it's an internal
password for this stack's own `redis` instance, not credentials for an external account.
Any sufficiently strong string works; nothing needs to be registered or requested for it.

## Where these values actually live

Never commit real values — `.env.example` (this repo) and `.env` (your local copy, already
git-ignored) only ever hold placeholders for local testing. In production, both keys are set
directly in Dokploy's own environment-variable UI for this project, the same place every
other real secret (`SWI_REDIS_PWD` included) lives - see the root `README.md`'s "Secrets"
section.
