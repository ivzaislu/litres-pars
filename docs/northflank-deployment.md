# Northflank deployment

This branch is the Northflank/PostgreSQL production variant of the LitRes
aggregator.

## Target topology

```text
APK
 |
 | HTTPS
 v
Northflank combined service: litres-api
 |
 +-- private PostgreSQL addon
 |      POSTGRES_URI -> DATABASE_URL
 |
 +-- LitRes Foundation API on cache miss / stale series
```

SQLite is still available for explicit local development, but the normal server
startup path expects `DATABASE_URL`.

## 1. PostgreSQL addon

In the same Northflank project create one PostgreSQL addon using the Sandbox
database allocation.

Recommended settings:

- name: `litres-postgres`;
- public access: disabled;
- PostgreSQL: a currently supported version such as 16 or newer;
- TLS: optional for project-private access, according to your Northflank setup.

Do not expose the database publicly.

Create or use a secret group and link the addon's `POSTGRES_URI`. Set its
alias to:

```text
DATABASE_URL
```

The application accepts both `postgres://` and `postgresql://` URLs.

The database schema is created idempotently by the service on startup. No
separate migration command is required for the current schema.

## 2. Application secret

In the same secret group add:

```text
LITRES_APP_TOKEN=<long random secret>
LITRES_CACHE_TTL_SECONDS=604800
```

Generate the token outside the repository, for example:

```bash
openssl rand -hex 32
```

Never commit the real value.

Apply this secret group only to `litres-api`.

## 3. Combined service

Create a **combined service** with:

```text
Name: litres-api
Repository: ivzaislu/litres-pars
Branch: northflank-postgres
Build type: Dockerfile
Dockerfile: /Dockerfile
Build context: /
Instances: 1
```

Let the Developer Sandbox UI select the free service/build resource plans for
your account. The repository intentionally does not pin billing plan IDs so an
IaC file cannot accidentally select a paid plan.

Northflank builds Dockerfiles directly from Git repositories and combined
services build and deploy in one resource. The service should rebuild when new
commits are pushed to `northflank-postgres`.

## 4. Networking

Expose one port:

```text
Name: http
Internal port: 8000
Protocol: HTTP
Public: yes
```

Only the API service is public. PostgreSQL remains private.

## 5. Health check

Configure a liveness probe:

```text
Protocol: HTTP
Path: /health
Port: 8000
Initial delay: 10 seconds
Interval: 30 seconds
Timeout: 5 seconds
Failure threshold: 3
Success threshold: 1
```

The exact reusable health-check payload is in
`northflank/health-check.json`.

The endpoint intentionally does not require the APK bearer token so Northflank
can probe it.

## 6. Start command

The Docker image already contains the correct command:

```text
uvicorn litres_parser.api:create_app --factory --host 0.0.0.0 --port 8000
```

Northflank should normally use the Dockerfile default command. If you choose a
CMD override, use the command above exactly.

## 7. Runtime variables visible to the container

The final environment must contain:

```text
DATABASE_URL=<private PostgreSQL POSTGRES_URI>
LITRES_APP_TOKEN=<secret>
LITRES_CACHE_TTL_SECONDS=604800
```

`LITRES_DB_PATH` is not used in Northflank production.

## 8. First deploy behavior

At startup the service:

1. validates `LITRES_APP_TOKEN`;
2. reads `DATABASE_URL`;
3. connects to PostgreSQL;
4. creates missing cache tables/indexes;
5. starts the FastAPI service on port 8000.

If PostgreSQL is unavailable during startup, startup fails instead of silently
falling back to ephemeral SQLite. This is deliberate: a successful Northflank
deployment must be using persistent PostgreSQL.

## 9. Verification

After deployment:

```bash
curl https://<northflank-domain>/health
```

Expected:

```json
{"status":"ok"}
```

Then verify the private route:

```bash
curl -X POST https://<northflank-domain>/v1/series/resolve \
  -H "Authorization: Bearer $LITRES_APP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "author": "Роман Злотников",
    "book_title": "Обреченный на бой",
    "series_name": "Грон"
  }'
```

The first uncached call may reach LitRes. Repeating the same request while the
cache is fresh should be served from PostgreSQL without another LitRes series
download.

## Repository deployment manifest

`northflank/litres-api.json` records all application-specific Northflank
settings that do not depend on account-specific Sandbox billing plan IDs.
