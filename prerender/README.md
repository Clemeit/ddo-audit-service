# Prerender Local Service

A self-hosted prerender service that renders JavaScript-heavy web pages for search engine crawlers and social media bots. Similar to prerender.io, but fully self-contained and deployable via Docker.

## Features

- **Headless Chrome Rendering**: Uses Puppeteer with Chrome to fully render JavaScript applications
- **Redis Caching**: Configurable TTL-based caching to reduce rendering overhead
- **Flexible URL Handling**: Multiple ways to specify target URLs (query params, headers, path)
- **Status Code Preservation**: Maintains original HTTP status codes (200, 404, 301, etc.)
- **Redirect Handling**: Properly handles and tracks page redirects
- **Domain Whitelisting**: Optional security feature to limit which domains can be rendered
- **Docker Ready**: Fully containerized with health checks and graceful shutdown
- **Production Tested**: Built with error handling, timeouts, and resource management

## Quick Start

### Using Docker Compose

1. **Clone and configure**:

```bash
git clone <repository>
cd prerender-local
cp .env.example .env
# Edit .env with your Redis connection details
```

2. **Build and run**:

```bash
docker-compose up -d
```

3. **Test the service**:

```bash
curl "http://localhost:3000/?url=https://example.com"
```

### Manual Installation

```bash
npm install
cp .env.example .env
# Edit .env as needed
npm start
```

## Configuration

All configuration is done via environment variables. See `.env.example` for details.

### Key Configuration Options

| Variable          | Default        | Description                          |
| ----------------- | -------------- | ------------------------------------ |
| `REDIS_HOST`      | `redis`        | Redis server hostname                |
| `REDIS_PORT`      | `6379`         | Redis server port                    |
| `REDIS_PASSWORD`  | -              | Redis password (if required)         |
| `CACHE_TTL`       | `86400`        | Cache lifetime in seconds (24 hours) |
| `PAGE_TIMEOUT`    | `30000`        | Max page load time in milliseconds   |
| `WAIT_UNTIL`      | `networkidle0` | Page load wait condition             |
| `PORT`            | `3000`         | Service port                         |
| `ALLOWED_DOMAINS` | -              | Comma-separated domain whitelist     |

### Wait Conditions

- `load`: Wait for the load event
- `domcontentloaded`: Wait for DOMContentLoaded event
- `networkidle0`: Wait until no network connections for 500ms (recommended)
- `networkidle2`: Wait until ≤2 network connections for 500ms

## API Usage

### Render a URL

**Query Parameter** (recommended):

```bash
GET /?url=https://example.com/page
```

**Path Format**:

```bash
GET /render/https://example.com/page
```

**Custom Header**:

```bash
GET /
X-Prerender-URL: https://example.com/page
```

### Health Check

```bash
GET /health
```

Response:

```json
{
  "status": "ok",
  "cache": "connected",
  "timestamp": "2024-01-01T12:00:00.000Z"
}
```

## Response Headers

The service adds custom headers to help with debugging:

- `X-Prerender-Cache`: `HIT` or `MISS`
- `X-Prerender-Cache-Age`: Cache age in seconds (only on cache hits)
- `X-Prerender-Render-Time`: Rendering time in milliseconds (only on cache misses)
- `X-Prerender-Redirected`: `true` if the page redirected
- `X-Prerender-Final-URL`: Final URL after redirects

### Honored Meta Tags

Your app can hint the prerenderer using standard meta tags:

- `<meta name="prerender-status-code" content="404" />` — overrides the HTTP status sent to crawlers (e.g., return 404 for a SPA 404 page while still rendering friendly content).
- `<meta name="robots" content="noindex,nofollow" />` — forwarded as `X-Robots-Tag` so crawlers receive the directive even when serving prerendered HTML.

Both values are cached along with the HTML. If you later change these meta tags, clear the cache for affected URLs or wait for TTL expiry.

## Troubleshooting

### Service won't start

- Check logs: `docker-compose logs prerender`
- Ensure port 3000 is not already in use
- Verify Chrome dependencies are installed (automatically handled in Docker)

### Can't connect to Redis

- Verify `REDIS_HOST` matches your Redis container name
- Check that both containers are on the same Docker network
- Test Redis connection: `docker exec -it prerender ping redis`
- If Redis requires password, set `REDIS_PASSWORD`

### Pages timeout

- Increase `PAGE_TIMEOUT` for slow-loading pages
- Try changing `WAIT_UNTIL` to `networkidle2` or `domcontentloaded`
- Check if the target site blocks headless browsers

### High memory usage

- Adjust resource limits in `docker-compose.yml`
- Reduce `CACHE_TTL` to clear cache more frequently
- Consider scaling horizontally with multiple instances

### Cache not working

- Check `/health` endpoint to verify cache status
- Ensure Redis is running and accessible
- Check Redis logs for errors
- Verify sufficient Redis memory

## Performance Tips

1. **Use Redis caching**: Essential for production use
2. **Set appropriate TTL**: Balance freshness vs. performance (24 hours is reasonable)
3. **Tune wait conditions**: Use `networkidle2` for faster rendering if acceptable
4. **Domain whitelist**: Prevent abuse by limiting allowed domains
5. **Resource limits**: Set appropriate CPU/memory limits in docker-compose.yml
6. **Horizontal scaling**: Run multiple instances behind a load balancer

## Security Considerations

- **Domain Whitelist**: Use `ALLOWED_DOMAINS` to prevent rendering arbitrary URLs
- **Network Isolation**: Keep prerender on a private network, only accessible via nginx
- **Resource Limits**: Configure CPU and memory limits to prevent DoS
- **Non-root User**: Container runs as non-root user for security
- **Regular Updates**: Keep dependencies updated for security patches

## License

MIT

## Support

For issues, questions, or contributions, please open an issue in the repository.
