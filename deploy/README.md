# Deploying NextStep on a small server (DigitalOcean droplet)

One server, Docker, and Caddy for free https. Assumes Ubuntu 24.04 with Docker installed.

## First time
1. Point your DuckDNS name at the server's IPv4 address.
2. On the server:
   ```
   git clone https://github.com/Dhruv-Gupta-2695/job-tracker-mcp.git
   cd job-tracker-mcp
   cp deploy/.env.example deploy/.env
   nano deploy/.env        # set NEXTSTEP_DOMAIN and the other values
   docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
   ```
3. Open `https://YOUR-NAME.duckdns.org`. The first visit takes a few seconds while Caddy gets the certificate.

## Update after a merge to main
```
cd job-tracker-mcp
git pull
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

## Useful commands
```
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs -f nextstep
docker compose -f deploy/docker-compose.yml --env-file deploy/.env restart nextstep
```

## Sign-in settings to change once for the new address
- Firebase console > Authentication > Settings > Authorized domains: add your DuckDNS name.
- Google Cloud console > APIs & Services > Credentials > your OAuth client: add `https://YOUR-NAME.duckdns.org` as an authorized JavaScript origin.
- OAuth consent screen: update the Privacy and Terms links to the new address.

## Adding another project later
Add a service to `docker-compose.yml`, a block to `Caddyfile` with its own DuckDNS name, and an `mem_limit` so one app cannot starve the others.
