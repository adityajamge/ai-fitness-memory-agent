# Deployment — Amazon ECS Express Mode via ECR (T10, ADR-13.3 as amended / ADR-11 deploy-early)

> **Status: VERIFIED IN PRODUCTION — last verified 2026-07-19.**
> Current target: Amazon ECS Express Mode (originally App Runner; ADR-13.3 amendment).
> Live URL: <https://ai-2e921ede8718444985c5b24e7fb23497.ecs.us-east-1.on.aws>
> (health: `/healthz` → `{"status":"ok"}`). The full pipeline below — push to `main` →
> CI tests vs real CockroachDB → image build → ECR → Express deploy — has been exercised
> end-to-end; this guide is tested, not aspirational.

One Docker image (FastAPI; the built Vite SPA joins it in Phase 6), hosted on
**Amazon ECS Express Mode** (Fargate + shared ALB with a managed HTTPS URL),
delivered by CI: every push to `main` that passes tests is pushed to ECR and
deployed via the official `aws-actions/amazon-ecs-deploy-express-service`
action. No manual deploy step exists after the one-time setup below.

> **Why not App Runner?** The original decision (ADR-13.3) chose App Runner,
> but AWS closed it to new customers on 2026-04-30 and recommends ECS Express
> Mode instead. Express Mode removes the setup cost that made us reject plain
> ECS+ALB originally: one wizard provisions the Fargate service, load balancer,
> health checks, auto scaling, and networking. See the ADR-13.3 amendment in
> [office-hours/09-decisions.md](office-hours/09-decisions.md).

```
push to main ──► GitHub Actions (lint · pytest vs real CockroachDB · image build + smoke test)
                        │  deploy job (gated on AWS_DEPLOY_ENABLED)
                        ├── push image to ECR  (:<sha> for traceable rollbacks, :latest for humans)
                        └── amazon-ecs-deploy-express-service@v1  (deploys :<sha>)
                                     │
                                     ▼
                 ECS Express service ──► public HTTPS URL  (ALB health check, port 8080)
```

Region: **us-east-1** (N. Virginia — where the ECR repository lives; the ECS
service must be in the same region). The CockroachDB Cloud cluster stays in
ap-south-1 — the cross-region app→DB hop adds latency that the T12 latency
profile (Phase 5) should measure and document.

## One-time AWS setup — ✅ ALL COMPLETED 2026-07-19

Everything below is done: ECR repository
(`<AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/ai-fitness-memory-agent`), IAM user
`ci-deploy` (ECR push + ECS Express + PassRole), GitHub secrets and variables, the
Express service, and the first CI-driven deploy. The steps are kept for
reproducibility (e.g. recreating the stack in another region):

1. **Extend the `ci-deploy` IAM policy** so CI can also deploy the Express
   service (add these statements alongside the existing ECR ones; the PassRole
   ARNs come from step 3):

   ```json
   { "Effect": "Allow",
     "Action": ["ecs:CreateExpressGatewayService", "ecs:UpdateExpressGatewayService",
                 "ecs:DescribeExpressGatewayService", "ecs:DescribeServices",
                 "ecs:RegisterTaskDefinition"],
     "Resource": "*" },
   { "Effect": "Allow",
     "Action": "iam:PassRole",
     "Resource": ["<EXECUTION_ROLE_ARN>", "<INFRASTRUCTURE_ROLE_ARN>", "<TASK_ROLE_ARN>"] }
   ```

   > The task role ARN was added on 2026-08-06 when the deploy became declarative.
   > Without it, `ecs:RegisterTaskDefinition` fails with an `iam:PassRole` denial the
   > moment the workflow starts passing `task-role-arn`.

2. **First image into ECR** — push to `main` with the `AWS_DEPLOY_ENABLED`
   variable still unset: the deploy job is skipped, but you can then run
   step 3's wizard against a manually pushed image — OR simply set
   `AWS_DEPLOY_ENABLED=true` *after* step 3. Easiest: temporarily set only the
   ECR push half by letting CI run once with `AWS_DEPLOY_ENABLED=true` and
   the role variables unset — the push step succeeds, the deploy step fails,
   and ECR is populated. Then continue with step 3 and re-run the job.

3. **Create the service once via the console wizard** (this is also what
   creates the two roles with the right managed policies) — Console → ECS →
   **Deploy with Express Mode**:
   - Image: `<AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/ai-fitness-memory-agent:latest`
   - Task execution role / infrastructure role: **Create new role** in each dropdown
   - Additional configurations: service name `ai-fitness-memory-agent`,
     container port **8080**, health check path **/healthz**,
     CPU **0.25 vCPU** / memory **0.5 GB** (smallest — see budget line-item),
     environment variables: **leave empty** — they are supplied by the deploy
     workflow on every run and console values do not survive a redeploy (see
     [Runtime configuration](#runtime-configuration-declarative) below)
   - Note the two role ARNs it created and the service URL
     (`…ecs.us-east-1.on.aws`).

4. **Finish GitHub repo settings** (`Settings → Secrets and variables → Actions`):
   - Variables: `AWS_DEPLOY_ENABLED` = `true`, `AWS_REGION` = `us-east-1`,
     `ECS_EXECUTION_ROLE_ARN` and `ECS_INFRASTRUCTURE_ROLE_ARN` = the ARNs from step 3
   - Backfill step 1's PassRole statement with those same two ARNs.

5. **Verify** — push to `main` (or re-run the workflow): the deploy job pushes
   the image and the Express action redeploys the service; the hello page
   renders at the service URL and `/healthz` returns `{"status":"ok"}`. Save
   the URL in the README (hackathon compliance table) when Milestone 1 lands.

## Runtime configuration (declarative)

**Configuration lives in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), not in the
ECS console.** Do not set environment variables by hand on the service — they will not
survive the next deploy.

### Why: the deploy action rebuilds the service config every run

`aws-actions/amazon-ecs-deploy-express-service@v1` does **not** read your existing
container configuration and merge into it. Reading its source (`index.js`):

- it calls `DescribeServices` only to decide create-vs-update and to capture **tags**
  (`index.js:295-328`) — nothing else about the live service is read back;
- it builds `serviceConfig.primaryContainer` fresh from action inputs, starting from
  `{ image }` and adding `environment` / `secrets` / `containerPort` **only if the
  corresponding input is non-empty** (`index.js:341-375`);
- on update it sends `UpdateExpressGatewayServiceCommand({ serviceArn, ...serviceConfig })`
  (`index.js:505-516`).

So any setting the workflow does not pass is a setting the deploy does not carry forward.
This applies to more than the environment — `task-role-arn` (`index.js:415-417`) and
`health-check-path` (`index.js:443-445`) are built the same way. A workflow that omits
them can strip the container's Bedrock permissions and revert the ALB health check to
Express Mode's `/ping` default, which fails against this app.

The workflow therefore passes **all** of them explicitly, and a preflight step fails the
job if any required repo variable is unset, so a half-configured deploy stops before it
touches the running service.

### What is set where

Non-sensitive values are literals in the workflow (version-controlled, reviewable).
Secrets are ECS `secrets` entries — ECS resolves them from Secrets Manager at task start,
so the values never appear in the task definition, the workflow, or CI logs.

| Variable | Kind | Source |
|---|---|---|
| `DATABASE_URL` | **secret** | Secrets Manager → `vars.DATABASE_URL_SECRET_ARN`. Must include `sslmode=verify-full&sslrootcert=/app/certs/cockroachdb-ca.crt` — that file is baked into the image from [`deploy/cockroachdb-ca.crt`](../deploy/cockroachdb-ca.crt) (public trust-anchor data, not a secret). Without it, `verify-full` has nowhere to find a root cert and libpq refuses to connect — see the incident note below. |
| `ANTHROPIC_API_KEY` | **secret** | Secrets Manager → `vars.ANTHROPIC_API_KEY_SECRET_ARN` |
| `LLM_PROVIDER` = `claude_api` | env | workflow literal |
| `EMBEDDING_PROVIDER` = `bedrock` | env | workflow literal |
| `CLAUDE_API_MODEL_ID` = `claude-haiku-4-5-20251001` | env | workflow literal |
| `CLAUDE_API_EFFORT` = `low` | env | workflow literal |
| `DEFAULT_TZ` = `Asia/Kolkata` | env | workflow literal |
| `AWS_REGION` | env | `vars.AWS_REGION`, default `us-east-1` |
| `EMBED_DIMS` | **not set** | defaults to 512 in `engine/config.py`; a second source of truth could drift from `VECTOR(512)` in `schema.sql` |
| AWS credentials | — | never env vars: the **task role** (`vars.ECS_TASK_ROLE_ARN`) grants `bedrock:InvokeModel` |

### One-time AWS setup for the above

Run once per account; after this every deploy is fully automatic.

```bash
# 1. Store the two secrets (reads from .env so the values never enter shell history).
#    DATABASE_URL's sslrootcert must point at the in-image cert path, not a local one — see
#    "What is set where" above. Confirm .env has that before running this, or the deployed
#    task will fail exactly as the 2026-08-15 incident did (see below).
#    `tr -d '\n\r'` is required, not decorative — the 2026-08-16 incident (see below) was a
#    trailing newline that slipped into the stored secret, which h11/httpcore then rejected
#    as an illegal header value and which the Anthropic SDK reported as a generic
#    "Connection error.", indistinguishable from a real network outage without reading the
#    full exception chain.
aws secretsmanager create-secret --name ai-fitness/DATABASE_URL \
  --secret-string "$(grep -E '^DATABASE_URL=' .env | cut -d= -f2- | tr -d '\n\r')"
aws secretsmanager create-secret --name ai-fitness/ANTHROPIC_API_KEY \
  --secret-string "$(grep -E '^ANTHROPIC_API_KEY=' .env | cut -d= -f2- | tr -d '\n\r')"

# 2. Let the EXECUTION role read them (it is what injects secrets at task start)
aws iam put-role-policy --role-name <EXECUTION_ROLE_NAME> \
  --policy-name read-app-secrets --policy-document '{"Version":"2012-10-17","Statement":[{
    "Effect":"Allow","Action":"secretsmanager:GetSecretValue",
    "Resource":["<DATABASE_URL_SECRET_ARN>","<ANTHROPIC_API_KEY_SECRET_ARN>"]}]}'

# 3. Create the TASK role (what the container itself assumes) with Bedrock access
aws iam create-role --role-name ai-fitness-task-role \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{
    "Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},
    "Action":"sts:AssumeRole"}]}'
aws iam put-role-policy --role-name ai-fitness-task-role \
  --policy-name invoke-bedrock --policy-document '{"Version":"2012-10-17","Statement":[{
    "Effect":"Allow","Action":["bedrock:InvokeModel"],
    "Resource":"arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0"}]}'
```

Then add three repo variables (`Settings → Secrets and variables → Actions → Variables`):
`DATABASE_URL_SECRET_ARN`, `ANTHROPIC_API_KEY_SECRET_ARN`, `ECS_TASK_ROLE_ARN`.

> Only the embedding role runs on Bedrock in the current configuration, so the task-role
> policy above is scoped to the Titan model. Widen it to include the Claude inference
> profile if `LLM_PROVIDER` ever moves back to `bedrock`.

### Reference: every variable `engine/config.py` reads

The container reads its config through [`engine/config.py`](../engine/config.py):

| Variable | Purpose | Notes |
|---|---|---|
| `DATABASE_URL` | CockroachDB Cloud connection string | **required in practice** — the default is `127.0.0.1`, so an unset value fails silently (see the status note below). Delivered as an ECS secret, never baked into the image |
| `AWS_REGION` | Bedrock region | optional; defaults to `us-east-1` |
| `EXTRACTION_MODEL_ID` / `EMBEDDING_MODEL_ID` | model overrides | optional; defaults in `engine/config.py` |
| `EMBED_DIMS` | embedding dimensions | optional; defaults to `512` — **must match `VECTOR(512)`** in `engine/schema.sql` |
| `DEFAULT_TZ` | fallback timezone for events the model can't place, and the zone aggregation buckets are computed in | optional; defaults to `Asia/Kolkata` |
| `LLM_PROVIDER` | provider for `extract_events` / `plan` / `narrate` | optional; falls back to `MODEL_PROVIDER`, then `bedrock`. `claude_api` is a supported production value here (needs `ANTHROPIC_API_KEY`) |
| `EMBEDDING_PROVIDER` | provider for `embed` | optional; falls back to `MODEL_PROVIDER`, then `bedrock`. **Effectively a one-way door** — see the note below |
| `MODEL_PROVIDER` | shorthand setting *both* roles at once | optional; defaults to `bedrock`. Retained for backward compatibility; the per-role variables above win when set |
| `SESSION_TTL_SECONDS` / `BACKFILL_BATCH` | session lifetime, opportunistic backfill page size | optional; defaults in `engine/config.py` |

Bedrock access comes from the **task role** (`vars.ECS_TASK_ROLE_ARN`), not from keys in env
vars. That is a different role from the execution role: the execution role pulls the image
and resolves the ECS secrets, while the task role is what the running container assumes to
call `bedrock:InvokeModel`.

> **Provider roles ([ADR-13.2](office-hours/09-decisions.md#adr-13), amended 2026-08-02).**
> The LLM and embedding roles are selected independently, because Bedrock model access is
> granted per model — an account can hold Titan embeddings without Claude inference, which is
> exactly the case this project hit. A mixed deployment is configured as:
>
> ```
> LLM_PROVIDER=claude_api        # + ANTHROPIC_API_KEY
> EMBEDDING_PROVIDER=bedrock     # + bedrock:InvokeModel on the task role
> ```
>
> When both roles resolve to the same provider the concrete instance is used directly; only a
> genuinely mixed configuration builds a `CompositeProvider`.
>
> ⚠️ **`EMBEDDING_PROVIDER` is effectively a one-way door.** Vectors from different embedding
> models are not comparable, so changing it after memories exist requires nulling and
> re-embedding every row (`python -m cli.backfill`). Treat it as fixed once data is written.
> `LLM_PROVIDER` carries no such constraint and can be changed freely.

The same list, annotated for local development, is [`.env.example`](../.env.example) at the
repo root. That file is a **development** template only: the deployed image has no `.env`
(the `python-dotenv` loader is a dev-only dependency and is absent from the image), so
production values reach the container solely as real environment variables — which is
exactly what the workflow's `environment-variables` and `secrets` inputs supply.

> **Status: configuration is now declarative but NOT yet verified on the running service.**
> As of 2026-08-06 the workflow supplies everything above, but the AWS-side prerequisites
> (the two Secrets Manager secrets, `secretsmanager:GetSecretValue` on the execution role,
> the task role, and the three repo variables) still have to be created, and no deploy has
> run since. Until a deploy completes and a real signup + ingest succeeds against the
> deployed URL, treat the write path as unverified.
>
> The failure mode to watch for: the app deliberately tolerates an unreachable
> database at startup so the ALB health check stays green
> ([`api/main.py`](../api/main.py) lifespan). That means **`/healthz` can report ok while
> every `/api/*` route fails** — do not treat a green health check as proof the write path
> is live. Verify with a real signup + ingest against the deployed URL, and update this
> line with the result.

> **Incident, 2026-08-15 — first real deploy attempt failed on two independent gaps.**
> The first ECS task to actually start crashed with `ModuleNotFoundError: No module named
> 'anthropic'`, and CloudWatch also showed the DB connection failing because
> `/home/app/.postgresql/root.crt` did not exist. Root causes:
> 1. `anthropic` was declared under `[dependency-groups].dev` in `pyproject.toml`, not
>    `[project].dependencies` — so the production image (`pip install .`, no dev group)
>    never had it, even though the workflow sets `LLM_PROVIDER=claude_api` for every deploy.
>    This was the one that actually crashed the task: `api/main.py`'s `lifespan()` has no
>    exception handling around `build_default_provider`, unlike the DB calls around it.
> 2. `DATABASE_URL` used `sslmode=verify-full` with no `sslrootcert`, so libpq fell back to
>    its default lookup path (`~/.postgresql/root.crt`) and found nothing — nothing in the
>    image or task definition ever provisioned a cert. This one did *not* crash the task —
>    `db.setup_schema()` catches `OperationalError` and logs a warning — which is exactly
>    the silent-degradation trap the note above warns about: fixing only the `anthropic` gap
>    would have shipped a task that passes `/healthz` while every DB-backed route stays dead.
>
> Fixed: `anthropic` moved to `[project].dependencies`; the real CockroachDB Cloud CA cert
> (which chains to Let's Encrypt's ISRG Root X1/X2, not a private CockroachDB root) is now
> committed at [`deploy/cockroachdb-ca.crt`](../deploy/cockroachdb-ca.crt) and baked into the
> image (`Dockerfile`), and `DATABASE_URL` must set
> `sslrootcert=/app/certs/cockroachdb-ca.crt` explicitly (see "What is set where" above).

> **Incident, 2026-08-16 — every chat turn failed with `claude api planning failed:
> Connection error.`, despite `/healthz` and every DB-backed route (`/api/profile`,
> `/api/threads`, `/api/turns`, `/api/stats`, `/api/auth/*`) returning 200.** CloudWatch's
> first log line pointed at the network — `anthropic.APIConnectionError`'s `str()` is a
> hardcoded `"Connection error."` regardless of cause, which reads exactly like a blocked
> egress path. A live audit of the running task's security group, NACL, route table (→ IGW),
> subnet public-IP mapping, VPC DNS settings, and Route53 DNS Firewall/Network
> Firewall/Transit Gateway/VPC-endpoint config found nothing wrong at any layer — consistent
> with CockroachDB Cloud (an equally external, TLS-secured endpoint) connecting successfully
> through the same task. The infra was never the problem.
>
> Root cause, once [`api/routers/chat.py`](../api/routers/chat.py)'s `exc_info=True` logging
> (added for exactly this reason, then sitting undeployed for several hours until this
> incident's redeploy) surfaced the full chain: `httpcore.LocalProtocolError: Illegal header
> value b'sk-ant-...\n'`. The `ANTHROPIC_API_KEY` secret in Secrets Manager had a **literal
> trailing newline** baked into its value. `h11`/`httpcore` reject a header value containing
> `\n` client-side, before ever opening a socket — the SDK maps that rejection to
> `APIConnectionError`, the same exception class (and message) a real DNS/TCP/TLS failure
> would produce. The request never reached the network layer at all.
>
> The likely source: the one-time setup script below originally read secrets with
> `--secret-string "$(...)"` typed/pasted across multiple lines in a terminal, which can
> embed a trailing `\n` in the captured value; the corresponding local `.env` had a stray,
> uncommented multi-line `aws secretsmanager create-secret` fragment matching this exact
> failure shape. Fixed: the script now pipes through `tr -d '\n\r'` so a wrapped or
> copy-pasted invocation can't reintroduce a newline into the stored secret.
>
> **Takeaway:** a hardcoded, cause-agnostic exception message (`APIConnectionError` here) can
> make a client-side validation failure look identical to a network outage. When a "connection
> error" survives every infra check, check what's actually in the secret before spending more
> time on the network path.

## Operational notes

- **Rollback:** re-run the deploy action with a previous `:<sha>` tag (CI
  pushes every commit by SHA), or redeploy from the ECS console.
- **Cost shape** (differs from App Runner — see T13 budget): the Fargate task
  runs continuously (0.25 vCPU / 0.5 GB ≈ $9–10/mo in us-east-1) plus an ALB
  share (Express Mode shares one ALB across up to 25 of your Express services;
  with only this service, the full ALB ≈ $16–18/mo). No scale-to-zero. Delete
  or scale down the service between work sessions if budget demands.
- **Local run (no Docker needed):** `uvicorn api.main:app --port 8080`
- **Image is CI-verified:** every push builds the Dockerfile and smoke-tests
  `/healthz` + `/`, so a broken image can't reach `main` unnoticed.
