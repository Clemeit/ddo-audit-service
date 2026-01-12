# DDO Audit Service

This repository serves as the backend service for DDO Audit.

## Tech Stack

- **Python**: Programming language used for the backend.
- **Sanic**: An asynchronous web framework for building fast and scalable web applications in Python.
- **Nginx**: A high-performance web server and reverse proxy server used to serve static files and handle incoming requests.
- **Redis**: An in-memory data structure store used as a caching layer for characters and LFMs to improve performance.
- **Postgres**: A powerful, open-source relational database system used for persistent storage of character data.
- **Docker**: Containerization platform used to ensure a consistent and reproducible environment.

## Links:

- **Main website**: [https://www.ddoaudit.com](https://www.ddoaudit.com)
- **Frontend repository**: [https://github.com/Clemeit/ddo-audit-ui](https://github.com/Clemeit/ddo-audit-ui)

### Setup

- Add certbot
- Run certbot certonly --dry-run --webroot -w /var/www/ddoaudit.com/acme-challenge --cert-name ddoaudit.com -d ddoaudit.com,www.ddoaudit.com,api.ddoaudit.com,pgadmin.ddoaudit.com,playeraudit.com,www.playeraudit.com
- Set certbot to auto renew
- Open etc/letsencrypt/renewal/me.com.conf and under [renewalparams] add: renew_hook = docker exec -it html-nginx-1 nginx reload
  - This will automatically reload nginx whenever new certs are generated
- Install docker and docker-compose with apt. See [https://docs.docker.com/engine/install/ubuntu/] walkthrough.

### Traffic investigation (Sanic access logs)

The Sanic API emits a structured JSON access log (one line per request) to stdout. This helps identify abusive IPs, hot endpoints, unusually large responses, and slow/erroring requests.

Tuning knobs (optional):

- `LOG_LEVEL` (default: `INFO`): Standard Python logging level.
- `ACCESS_LOG_ENABLED` (default: `true`): Enable/disable access logging.
- `ACCESS_LOG_SAMPLE_RATE` (default: `1.0`): Sample non-error, non-slow requests. `1.0` logs all; `0.1` logs ~10%.
- `ACCESS_LOG_SLOW_MS` (default: `750`): Always log requests slower than this threshold.
- `ACCESS_LOG_INCLUDE_QUERY` (default: `false`): Include query string in logs (can contain PII; keep off unless needed).

Each response includes `X-Request-ID` to correlate client reports with server logs.

### Traffic investigation (Redis counters)

In addition to access logs, the API increments lightweight Redis counters per request (per-minute buckets). These are useful when you want quick answers like "top IPs" and "top routes" without scanning raw logs.

Config knobs (optional):

- `TRAFFIC_COUNTERS_ENABLED` (default: `true`): Enable/disable counters.
- `TRAFFIC_COUNTERS_TTL_HOURS` (default: `72`): How long buckets are retained.
- `TRAFFIC_COUNTERS_BUCKET_SECONDS` (default: `60`): Bucket size (keep at 60 unless you have a reason).
- `TRAFFIC_COUNTERS_MAX_MINUTES` (default: `1440`): Max window allowed for top-N queries.
- `TRAFFIC_COUNTERS_PREFIX` (default: `traffic`): Redis key prefix.

Protected query endpoints (require `Authorization: Bearer <API_KEY>`):

- `POST /service/v1/traffic/top_ips` body: `{ "minutes": 60, "limit": 25, "metric": "requests"|"bytes_out" }`
- `POST /service/v1/traffic/top_routes` body: `{ "minutes": 60, "limit": 25, "metric": "requests"|"bytes_out" }`

### Blocking abusive IPs (Nginx denylist)

Nginx includes a denylist from `/etc/nginx/denylist.d/*.conf` inside the API `location` blocks.

On the host, create a directory and add rules, for example:

- Production host path: `/var/www/ddoaudit.com/app/denylist.d/` (mounted into the container)
- Staging host path: `/var/www/ddoaudit-stage.com/app/denylist.d/`

Example file contents (e.g. `denylist.d/deny.conf`):

- `deny 203.0.113.45;`
- `deny 203.0.113.0/24;`
- `allow all;`

Then reload nginx inside the container (no rebuild needed): `docker compose exec nginx nginx -s reload`

### Blocking abusive IPs (Nginx denylist)

Nginx includes a denylist file that is evaluated before proxying API requests to Sanic.

- Edit [nginx/denylist.conf](nginx/denylist.conf) (one `deny` per line; CIDR allowed).
- In production/stage, `docker-compose.yml` and `docker-compose.stage.yml` bind-mount a host-managed denylist to `/etc/nginx/conf.d/denylist.conf`.
- Reload Nginx to apply changes (no container rebuild needed): `docker compose exec nginx nginx -s reload`
