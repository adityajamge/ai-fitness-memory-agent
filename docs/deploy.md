# Deployment — Amazon ECS Express Mode via ECR (T10, ADR-13.3 as amended / ADR-11 deploy-early)

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

## One-time AWS setup

Already done (2026-07-19): ✅ ECR repository
(`589077667696.dkr.ecr.us-east-1.amazonaws.com/ai-fitness-memory-agent`),
✅ IAM user `ci-deploy` with the ECR push policy, ✅ GitHub secrets
(`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`).

Remaining steps, in order:

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
     environment variables: none in Phase 1 (`DATABASE_URL` + Bedrock config
     arrive in Phase 2 as env vars/secrets, never baked into the image)
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
