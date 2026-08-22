# Production Deployment Notes

This project can be deployed with Docker Compose on a single cloud server.

1. Copy `.env.production.example` to `.env.production`.
2. Replace `AUTH_SECRET`, `API_KEY_ENCRYPTION_SECRET`, `POSTGRES_PASSWORD`, `CORS_ORIGINS`, and the SMTP placeholders.
3. Set `POSTGRES_DATA_DIR` and `BACKEND_STORAGE_DIR` to directories on the server data disk. The backend mount holds uploaded files, parsed artifacts, Cognee caches, and Hugging Face / FastEmbed caches.
4. Create those directories on the server and make sure the Docker daemon can write to them.
5. Keep `FASTEMBED_LOCAL_FILES_ONLY=true` on servers without Hugging Face network access. Preload the model into `${BACKEND_STORAGE_DIR}/fastembed` before starting document processing.
6. LLM API keys are not configured through Docker environment files. Each user enters their own LLM profile and API key in the frontend settings; the backend stores the key encrypted with `API_KEY_ENCRYPTION_SECRET`.
7. Start with:

```bash
# Adjust these paths to match POSTGRES_DATA_DIR and BACKEND_STORAGE_DIR.
sudo mkdir -p /data/valueverse/postgres /data/valueverse/storage
# postgres:15-alpine normally runs as the postgres user with UID/GID 70.
sudo chown -R 70:70 /data/valueverse/postgres
sudo chmod 750 /data/valueverse/postgres /data/valueverse/storage

docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

Only the frontend Nginx service exposes the configured `${FRONTEND_PORT}`. PostgreSQL, Redis, and the backend API stay on the Docker network.

Use a URL-safe `POSTGRES_PASSWORD` (letters, digits, `_`, and `-`) because the current Compose file embeds it in `DATABASE_URL`. Do not expose PostgreSQL or Redis ports on the host.

For a host-level Nginx/Caddy or cloud load balancer, set:

```text
FRONTEND_BIND_ADDRESS=127.0.0.1
FRONTEND_PORT=8080
```

Terminate HTTPS at that proxy and forward to `127.0.0.1:8080`. Keep `AUTH_COOKIE_SECURE=true`; direct HTTP access will not carry the production login cookie.

Do not publish the current frontend container as `443:80` and treat it as HTTPS. The frontend image already contains Nginx, but it only listens for plain HTTP on container port `80`; a domain certificate must terminate at a TLS layer in front of it, or the frontend Nginx config must be changed to listen on `443` with mounted certificate files.

Recommended host-level Nginx shape:

```nginx
server {
  listen 80;
  server_name your-domain.example;
  return 301 https://$host$request_uri;
}

server {
  listen 443 ssl http2;
  server_name your-domain.example;

  ssl_certificate /etc/letsencrypt/live/your-domain.example/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/your-domain.example/privkey.pem;

  client_max_body_size 200m;

  location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
  }
}
```

If the server does not already have a host-level proxy, use either host Nginx/Caddy or add one dedicated reverse-proxy service to Compose. Do not run both for the same public `443` entrypoint.

Redis is currently ephemeral here; the app does not rely on it for durable state, so no separate data-disk mount is required.

Storage layout:

- PostgreSQL: `${POSTGRES_DATA_DIR}`
- App data: `${BACKEND_STORAGE_DIR}`
- Frontend: container image only

Docker container logs are separate from application data. Configure Docker log rotation or a host logging driver so logs cannot fill the system disk.

## Existing local data

Do not copy a live PostgreSQL data directory while PostgreSQL is running. For a new server, use a logical backup:

```bash
# On the source host
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > valueverse.sql

# On the server after the stack is up
cat valueverse.sql | docker compose --env-file .env.production -f docker-compose.prod.yml exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Copy the existing backend storage directory into `${BACKEND_STORAGE_DIR}` separately. Preserve the `raw/`, `cognee/`, `huggingface/`, and `fastembed/` subdirectories.

When the server cannot reach Hugging Face, copy a verified FastEmbed cache from a networked machine into `${BACKEND_STORAGE_DIR}/fastembed`. The cache must contain the model directory, `files_metadata.json`, `refs/main`, `snapshots/`, and `blobs/`; do not copy only the large ONNX file.

## Tencent Enterprise Mail

The account center uses SMTP only when a user requests an email change verification code. Configure these variables in `.env.production`:

```text
SMTP_HOST=smtp.exmail.qq.com
SMTP_PORT=465
SMTP_USERNAME=your-mailbox@your-company.example
SMTP_PASSWORD=your-smtp-or-client-authorization-code
SMTP_FROM_EMAIL=your-mailbox@your-company.example
SMTP_FROM_NAME=valueverse
SMTP_USE_SSL=true
SMTP_USE_TLS=false
SMTP_TIMEOUT_SECONDS=20
```

`SMTP_USERNAME` is normally the full Tencent Enterprise Mail address. `SMTP_PASSWORD` should be the SMTP/client authorization password if the mailbox administrator has enabled that mode, not a value exposed to the browser. Tencent Enterprise Mail usually uses `smtp.exmail.qq.com` with port `465`, `SMTP_USE_SSL=true`, and TLS 1.2 or later. If the mailbox policy requires STARTTLS, use port `587`, set `SMTP_USE_SSL=false`, and set `SMTP_USE_TLS=true`.

After changing the variables, restart the backend so the new settings are loaded:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build backend
```

Production hardening defaults:

- `APP_ENV=production` disables `/api/docs` and `/api/openapi.json` unless `API_DOCS_ENABLED=true`.
- `AUTH_SECRET` is required and must not use the development default.
- `API_KEY_ENCRYPTION_SECRET` is required and must remain stable; changing it makes stored LLM and Web Search API keys undecryptable.
- LLM endpoints are limited by `ALLOWED_LLM_HOSTS`.
- Web search MCP commands are limited by `ALLOWED_WEB_SEARCH_COMMANDS` and `ALLOWED_WEB_SEARCH_PACKAGES`.
- Nginx adds CSP, clickjacking, MIME sniffing, referrer, and permissions headers.
