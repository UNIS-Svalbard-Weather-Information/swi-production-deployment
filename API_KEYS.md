# API keys

Third-party API keys are needed, used by `CRON_metobs_cache`. Other cron
job (`CRON_seaice_cache`, `CRON_avalanche_cache`, `CRON_AAforecast_cache`) hits keyless
public endpoints and needs nothing here.

| Env var | Where to get it |
|---|---|
| `SWI_METOBS_HOLFUY_API_KEY` | Ask UNIS / the dev team - not a public signup, it's tied to an existing Holfuy account. |
| `SWI_METOBS_FROST_API_KEY` | Free, self-service - register at <https://frost.met.no/auth/requestCredentials.html>. The client ID you get back *is* the key. |
| `SWI_FELTLOGG_API_KEY` | From <https://feltlogg.com/developers> - one key per site/partner, shown only once (keep it safe). Used by the `feltlogg-cache` reverse proxy, which injects it server-side. |

The two metobs keys are required by `CRON_metobs_cache`, and `SWI_FELTLOGG_API_KEY` by the
`feltlogg-cache` proxy — if any is missing from `.env`, `docker compose up` refuses to start
that service outright rather than failing silently.

Not a third-party key: `SWI_REDIS_PWD` is just this stack's own `redis` password - any
strong string works, nothing to request.
