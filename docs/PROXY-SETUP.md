# Reverse Proxy Setup

## Goals

The production dashboard is published through the existing nginx instance on the NAS at
`https://server.alexbomber.com/raid/`. FastAPI still listens on the private local port
`127.0.0.1:8090`; nginx owns the public HTTPS endpoint.

The proxy provides:

- TLS termination with the existing wildcard certificate.
- A stable `/raid/` path namespace on `server.alexbomber.com`.
- `X-Forwarded-Prefix: /raid` so the app generates prefixed links and asset URLs.
- Security headers at the edge.
- A narrow rate limit on the auth endpoint before traffic reaches the app.
- HTTP-to-HTTPS redirects for accidental plain HTTP requests.

This repository does not install or manage nginx. The config below is a copy-paste-ready
fragment for the NAS host.

## Prerequisites

Before editing nginx, confirm these pieces are already true on the NAS:

- nginx is installed and serves `server.alexbomber.com`.
- The dashboard process listens on `127.0.0.1:8090`.
- The app's `/healthz` route returns a JSON liveness response locally.
- The wildcard certificate for `*.alexbomber.com` is present on disk.
- You can run `sudo nginx -t` and `sudo systemctl reload nginx`.

Local app check:

```bash
curl -i http://127.0.0.1:8090/healthz
```

Expected result: HTTP 200 when the app is healthy, or HTTP 503 with a degraded JSON payload
when the app is running but one of its checks fails.

## Cert Reuse

Reuse the existing wildcard certificate rather than issuing a separate certificate for this
service. The expected Let's Encrypt live directory is:

```text
/etc/letsencrypt/live/alexbomber.com/
```

The nginx server block uses:

```nginx
ssl_certificate     /etc/letsencrypt/live/alexbomber.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/alexbomber.com/privkey.pem;
```

Do not copy the private key into this repository or into the application directory. nginx
should read it from the normal certificate store on the host.

## Server Block

Place the fragment in `/etc/nginx/conf.d/megaraid.conf`, or include the same content from the
host's main nginx configuration. Keep `limit_req_zone` at the `http` context level. On common
nginx layouts, files under `conf.d/` are included from `http`, so the sample placement works.

```nginx
# megaraid-dashboard reverse proxy fragment
# Place under: /etc/nginx/conf.d/megaraid.conf
# Reload: sudo nginx -t && sudo systemctl reload nginx

limit_req_zone $binary_remote_addr zone=raid_login:10m rate=5r/m;

server {
    listen 443 ssl http2;
    server_name server.alexbomber.com;

    ssl_certificate     /etc/letsencrypt/live/alexbomber.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/alexbomber.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-Frame-Options "DENY" always;

    location = /raid/healthz {
        proxy_pass http://127.0.0.1:8090/healthz;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Prefix /raid;
        proxy_read_timeout 30s;
    }

    location ~ ^/raid/(login|auth) {
        limit_req zone=raid_login burst=2 nodelay;
        limit_req_status 429;

        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Prefix /raid;
        proxy_read_timeout 30s;
    }

    location /raid/ {
        proxy_pass http://127.0.0.1:8090/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Prefix /raid;
        proxy_read_timeout 30s;
    }
}

server {
    listen 80;
    server_name server.alexbomber.com;

    return 301 https://$host$request_uri;
}
```

After writing the file, test and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Location Block

The main dashboard location is:

```nginx
location /raid/ {
    proxy_pass http://127.0.0.1:8090/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Prefix /raid;
    proxy_read_timeout 30s;
}
```

The trailing slash on `proxy_pass http://127.0.0.1:8090/` matters. With the slash, nginx
strips the matching `/raid/` prefix and forwards the remaining path to FastAPI. For example:

```text
/raid/                 -> /
/raid/events           -> /events
/raid/static/css/app.css -> /static/css/app.css
```

Without the trailing slash, nginx would forward the prefix verbatim and the app would receive
paths such as `/raid/events`, which are not the app's local routes.

The proxy then re-injects the public prefix with:

```nginx
proxy_set_header X-Forwarded-Prefix /raid;
```

`ForwardedPrefixMiddleware` reads that header and assigns `/raid` to the ASGI `root_path`.
FastAPI and the templates can then continue using `request.url_for(...)`; generated links and
static asset URLs include `/raid` for production traffic and omit it for local development.

## Security Headers

The sample sets these headers at the TLS edge:

```nginx
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header X-Frame-Options "DENY" always;
```

`Strict-Transport-Security` tells browsers to keep using HTTPS for `server.alexbomber.com`
and subdomains after the first successful HTTPS visit. The `max-age=63072000` value is two
years.

`X-Content-Type-Options: nosniff` prevents browser MIME sniffing. `Referrer-Policy` limits
cross-origin referrer leakage while preserving useful same-origin behavior. `X-Frame-Options:
DENY` prevents framing the dashboard in another page.

The proxy does not set `Content-Security-Policy`. The app may add a CSP later when the final
set of scripts, styles, and HTMX behavior is stable enough to avoid accidental breakage.

## Rate Limit Zone

The edge rate limit is intentionally narrow. It protects the auth challenge surface without
rate-limiting health checks, static assets, or normal read-only dashboard traffic.

Define the shared zone once:

```nginx
limit_req_zone $binary_remote_addr zone=raid_login:10m rate=5r/m;
```

Apply it to the likely auth endpoint paths:

```nginx
location ~ ^/raid/(login|auth) {
    limit_req zone=raid_login burst=2 nodelay;
    limit_req_status 429;

    proxy_pass http://127.0.0.1:8090;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Prefix /raid;
    proxy_read_timeout 30s;
}
```

`5r/m` matches the planned in-app auth throttle. Keeping the same number at both layers makes
the operating model simple: nginx rejects bursts at the edge, and the app rejects bursts that
reach it through direct LAN access or a future proxy change.

The regex location is deliberately limited to `/raid/login` and `/raid/auth` while the auth
surface settles. If a later PR pins a single endpoint, tighten this block to an exact
location.

## Healthz

The health endpoint has a dedicated exact-match location:

```nginx
location = /raid/healthz {
    proxy_pass http://127.0.0.1:8090/healthz;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Prefix /raid;
    proxy_read_timeout 30s;
}
```

This route skips nginx-level basic auth and rate limiting. The app already whitelists
`/healthz`, so uptime checks can probe it without credentials while normal dashboard routes
remain protected by HTTP basic auth at the application layer and by nginx TLS at the edge.

## Verification Checklist

Run these commands from a workstation that resolves `server.alexbomber.com` to the NAS.

1. Confirm `/raid/healthz` reaches the app and returns HSTS:

   ```bash
   curl -I https://server.alexbomber.com/raid/healthz
   ```

   Expected: HTTP 200 when healthy, plus `Strict-Transport-Security`.

2. Confirm plain HTTP redirects to HTTPS:

   ```bash
   curl -I http://server.alexbomber.com/raid/
   ```

   Expected: HTTP 301 with `Location: https://server.alexbomber.com/raid/`.

3. Confirm the dashboard is auth-protected:

   ```bash
   curl -i https://server.alexbomber.com/raid/
   ```

   Expected: HTTP 401 with `WWW-Authenticate: Basic realm="megaraid-dashboard"`.

4. Confirm the auth endpoint rate limit triggers:

   ```bash
   for i in {1..10}; do curl -o /dev/null -s -w "%{http_code}\n" https://server.alexbomber.com/raid/login; done
   ```

   Expected: several `429` responses after the first five to seven requests within a minute.

5. Confirm prefix injection works for authenticated HTML:

   ```bash
   curl -i -u admin:test-password https://server.alexbomber.com/raid/
   ```

   Expected: HTTP 200 and HTML containing `<a class="brand" href="/raid/">`.
