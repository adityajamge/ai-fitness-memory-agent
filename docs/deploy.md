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
(`589077667696.dkr.ecr.us-east-1.amazonaws.com/ai-fitness-memory-agent`), IAM user
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
     "Resource": ["<EXECUTION_ROLE_ARN>", "<INFRASTRUCTURE_ROLE_ARN>"] }
   ```

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
   - Image: `589077667696.dkr.ecr.us-east-1.amazonaws.com/ai-fitness-memory-agent:latest`
   - Task execution role / infrastructure role: **Create new role** in each dropdown
   - Additional configurations: service name `ai-fitness-memory-agent`,
     container port **8080**, health check path **/healthz**,
     CPU **0.25 vCPU** / memory **0.5 GB** (smallest — see budget line-item),
     environment variables: none were set in Phase 1 — Phase 2's `DATABASE_URL` +
     Bedrock config are still outstanding (see
     [Runtime configuration](#runtime-configuration-phase-2-onward) below)
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

## Runtime configuration (Phase 2 onward)

Phase 1 needed no environment variables — the hello page and `/healthz` have no
dependencies. **The Phase 2 write path does.** The container reads its config through
[`engine/config.py`](../engine/config.py); the deployed service must have at least:

| Variable | Purpose | Notes |
|---|---|---|
| `DATABASE_URL` | CockroachDB Cloud connection string | secret — set on the Express service, never baked into the image |
| `AWS_REGION` | Bedrock region | optional; defaults to `us-east-1` |
| `EXTRACTION_MODEL_ID` / `EMBEDDING_MODEL_ID` | model overrides | optional; defaults in `engine/config.py` |
| `EMBED_DIMS` | embedding dimensions | optional; defaults to `512` — **must match `VECTOR(512)`** in `engine/schema.sql` |
| `DEFAULT_TZ` | fallback timezone for events the model can't place, and the zone aggregation buckets are computed in | optional; defaults to `Asia/Kolkata` |
| `LLM_PROVIDER` | provider for `extract_events` / `plan` / `narrate` | optional; falls back to `MODEL_PROVIDER`, then `bedrock`. `claude_api` is a supported production value here (needs `ANTHROPIC_API_KEY`) |
| `EMBEDDING_PROVIDER` | provider for `embed` | optional; falls back to `MODEL_PROVIDER`, then `bedrock`. **Effectively a one-way door** — see the note below |
| `MODEL_PROVIDER` | shorthand setting *both* roles at once | optional; defaults to `bedrock`. Retained for backward compatibility; the per-role variables above win when set |
| `SESSION_TTL_SECONDS` / `BACKFILL_BATCH` | session lifetime, opportunistic backfill page size | optional; defaults in `engine/config.py` |

Bedrock access comes from the task role, not from keys in env vars — the task execution /
infrastructure roles created by the Express wizard need `bedrock:InvokeModel` added for
ingestion to work.

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

The full variable list, with which are required and what each defaults to, is
[`.env.example`](../.env.example) at the repo root. That file is a **development** template:
the deployed service has no `.env` (the `python-dotenv` loader is a dev-only dependency and is
absent from the image), so every value above must be set as a real environment variable on the
Express service.

> **Status: not yet verified on the deployed service.** As of 2026-07-21 there is no record
> that these were configured in ECS, and the app deliberately tolerates an unreachable
> database at startup so the ALB health check stays green
> ([`api/main.py`](../api/main.py) lifespan). That means **`/healthz` can report ok while
> every `/api/*` route fails** — do not treat a green health check as proof the write path
> is live. Verify with a real signup + ingest against the deployed URL before the Phase 2
> demo checkpoint, and update this line with the result.

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
