# Deployment — App Runner via ECR (T10, ADR-13.7 / ADR-11 deploy-early)

One Docker image (FastAPI; the built Vite SPA joins it in Phase 6), hosted on
**AWS App Runner**, delivered by CI: every push to `main` that passes tests is
pushed to ECR as `:latest` + `:<sha>`, and App Runner auto-deploys `:latest`.
No manual deploy step exists after the one-time setup below.

```
push to main ──► GitHub Actions (lint · pytest vs real CockroachDB · image build)
                        │  deploy job (gated on AWS_DEPLOY_ENABLED)
                        ▼
                 ECR ai-fitness-memory-agent:latest
                        │  auto-deploy
                        ▼
                 App Runner service ──► public URL  (health check: GET /healthz, port 8080)
```

## One-time AWS setup (manual, ~15 min)

Region: **ap-south-1** (Mumbai — same as the CockroachDB Cloud cluster). If App
Runner is unavailable there on your account, use `ap-southeast-1` and set the
`AWS_REGION` repo variable to match.

1. **ECR repository** — Console → ECR → Create repository → name
   `ai-fitness-memory-agent` (private, defaults fine).

2. **IAM user for CI** (push-only) — Console → IAM → Users → Create `ci-deploy`,
   no console access, attach this inline policy (replace `<ACCOUNT_ID>`):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       { "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
       { "Effect": "Allow",
         "Action": ["ecr:BatchCheckLayerAvailability", "ecr:CompleteLayerUpload",
                     "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
                     "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
         "Resource": "arn:aws:ecr:ap-south-1:<ACCOUNT_ID>:repository/ai-fitness-memory-agent" }
     ]
   }
   ```

   Create an access key (use case: "Application running outside AWS").

3. **GitHub repo settings** (`Settings → Secrets and variables → Actions`):
   - Secrets: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (from step 2)
   - Variables: `AWS_DEPLOY_ENABLED` = `true`, `AWS_REGION` = `ap-south-1`

4. **First image** — push to `main` (or re-run the workflow); the deploy job
   populates ECR. App Runner can't create a service from an empty repository,
   so do this before step 5.

5. **App Runner service** — Console → App Runner → Create service:
   - Source: **Container registry / Amazon ECR** → `ai-fitness-memory-agent:latest`
   - Deployment trigger: **Automatic** (this is what makes CI pushes go live)
   - ECR access role: let the console create `AppRunnerECRAccessRole`
   - Service name: `ai-fitness-memory-agent` · Port: **8080**
   - Instance: 0.25 vCPU / 0.5 GB (smallest — see budget in
     [office-hours/README.md](office-hours/README.md))
   - Health check: protocol **HTTP**, path **/healthz**
   - Environment variables: none in Phase 1 (`DATABASE_URL` and Bedrock config
     arrive in Phase 2 — add them as App Runner **secrets/env vars**, never in the image)

6. **Verify** — open the default `https://….awsapprunner.com` URL: the hello
   page renders and `/healthz` returns `{"status":"ok"}`. Save the URL in the
   README (hackathon compliance table) when Milestone 1 lands.

## Operational notes

- **Rollback:** App Runner console → service → Deployments → redeploy a previous
  image tag (CI pushes every commit as `:<sha>`).
- **Pause to save budget:** the service can be paused from the console between
  work sessions (billing stops for compute; see budget line-item).
- **Local run (no Docker needed):** `uvicorn api.main:app --port 8080`
- **Image is CI-verified:** every push builds the Dockerfile and smoke-tests
  `/healthz` + `/`, so a broken image can't reach `main` unnoticed.
