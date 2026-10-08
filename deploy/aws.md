# Backend on AWS

One small EC2 instance runs the backend container behind Caddy, which gets a Let's Encrypt certificate.
`deploy/aws_deploy.sh up` (or `make deploy-backend`) creates everything and prints the HTTPS URL.

```
Cloudflare Worker ──https──► Elastic IP :443 ── Caddy ──► backend container :8080 (uvicorn, server/)
ElevenLabs tools ──https──┘                       (EC2 t3.small, Ubuntu 24.04, Docker)   │
                                                                                         └─► harbor -e modal ─► Modal
```

## Why EC2 + Caddy
The contract allows "a single small EC2 instance or ECS Express service". For this backend EC2 is simpler:

| | EC2 + Caddy (chosen) | ECS Express Mode (Fargate + ALB) | App Runner |
|---|---|---|---|
| HTTPS URL | `https://<ip>.sslip.io` or your domain, Let's Encrypt via Caddy | `https://….ecs.<region>.on.aws`, ACM | `https://….awsapprunner.com` |
| Needs local Docker / ECR | no: image is built on the instance | yes: build + push to ECR | yes (or source build) |
| Run state, Harbor traces, index | on the instance disk, survive restarts and redeploys | lost on every redeploy/task replacement unless you add EFS | lost, no volumes |
| Detached flows (`start_detached`) | normal background processes | killed when a task is replaced | CPU throttled between requests |
| Moving parts | instance, security group, Elastic IP, key pair | cluster, service, ALB, target group, 2 IAM roles, ECR, (EFS) | service, ECR, IAM role |
| Cost/month (eu-central-1) | **≈ $23** | ≈ $45+ (ALB ≈ $20 + Fargate 0.5 vCPU/1 GB ≈ $20 + IPv4) | ≈ $25-50, and closed to new customers |

The backend keeps state on disk: agent_runner runs, Harbor job folders (the traces the page streams) and the RAG
index. It also starts long-running detached flows. A container platform with ephemeral disk would need EFS to
handle that. With one VM, the data is just a folder (`/srv/macrae/data`).

What you give up: no autoscaling, no multi-AZ, and you patch the OS yourself. Ubuntu's unattended security
upgrades are on by default. If the instance dies you redeploy, and runs/traces are lost unless you've snapshotted
the volume. That's fine for a demo and a small group.

## Cost
On-demand, 730 h/month (`deploy/aws_deploy.sh cost`):

| item | us-east-1 | eu-central-1 (Frankfurt) |
|---|---|---|
| t3.small (2 vCPU burstable, 2 GiB) | $15.18 | $17.52 |
| 20 GB gp3 root volume | $1.60 | $1.90 |
| public IPv4 / Elastic IP ($0.005/h) | $3.65 | $3.65 |
| data out (first 100 GB/month free) | $0 | $0 |
| **total running** | **≈ $20.43** | **≈ $23.07** |
| stopped (`aws_deploy.sh stop`): disk + IP only | ≈ $5.25 | ≈ $5.55 |

Cheaper: `MACRAE_INSTANCE_TYPE=t4g.small` (Graviton, ≈ $12-14 + disk + IP; the script picks the arm64 AMI and the
image builds natively). If you need more memory for big indexes or many parallel runs, use t3.medium (4 GiB, ≈ $30-35).
t3 runs in "unlimited" credit mode. A sustained build or busy period above the 20% baseline costs $0.05 per
vCPU-hour of surplus, which is cents per month for this workload.

Not on the AWS bill: Modal compute for agent steps (per second; a small agent run is cents), Claude usage
(subscription token or API key), ElevenLabs conversation minutes, Cloudflare Workers (free tier is enough).

## Deploy
Prerequisites on your machine: AWS CLI v2 with credentials (`aws configure`, or `AWS_PROFILE=…`), `ssh`, `tar`,
`curl`, `python3`. `rsync` is optional and speeds up syncing papers. You don't need Docker.
The AWS user needs EC2 (instances, security groups, addresses, key pairs) and `ssm:GetParameter` for the public
Ubuntu AMI parameter.

```bash
cp deploy/env.example deploy/.env      # fill in MACRAE_TOOL_SECRET, Claude login(s), ElevenLabs
make index                             # optional: build index/ locally from papers/ (synced to the server)
make deploy-backend                    # = deploy/aws_deploy.sh up  → prints https://<ip>.sslip.io
make deploy-web web-secrets            # Worker, then BACKEND_URL + secrets as Worker secrets
make voice-setup                       # ElevenLabs agent tools → https://<ip>.sslip.io/api/tools/...
make smoke                             # GET /api/health and /api/tasks on the deployed backend
```

What `up` does, idempotently (resources are found by tags `Name=macrae-backend`, `Project=macrae`):
1. checks `deploy/.env` (`MACRAE_TOOL_SECRET` required; warns if there's no Modal or Claude login),
2. creates `~/.ssh/macrae_deploy` and imports it as key pair `macrae-<hash>`,
3. security group `macrae-backend` in the default VPC: 80 and 443 open, 22 only from your current IP,
4. launches Ubuntu 24.04 (Canonical's SSM parameter), 20 GB encrypted gp3, IMDSv2 only, user data
   `deploy/cloud-init.sh` (Docker, compose, buildx, 2 GB swap, `/srv/macrae`). A stopped instance is started,
5. allocates an Elastic IP and associates it, so the URL survives stop/start,
6. ships the repo (no `.git`, papers, index or secrets) to `/srv/macrae/app`, uploads `deploy/.env` with
   `MACRAE_DOMAIN`/`MACRAE_DATA_DIR` added, and, if the Modal token pair is empty, the pair of
   `[$MODAL_PROFILE]` from your `~/.modal.toml`,
7. syncs `papers/` and `index/` to `/srv/macrae/data` (`MACRAE_SKIP_DATA=1` to skip),
8. `docker compose --profile https up -d --build` (backend + Caddy), then waits for
   `https://<domain>/api/health`.

Re-running `up` after code changes is safe. `deploy/aws_deploy.sh code` is the fast path: it skips the
infrastructure checks.

### Your own domain
Point an A record at the Elastic IP (`deploy/aws_deploy.sh status`), set `MACRAE_DOMAIN=api.example.org` in
`deploy/.env`, then run `deploy/aws_deploy.sh code`. Caddy fetches the new certificate. Without a domain, the
script uses `<ip-with-dashes>.sslip.io`, a public wildcard DNS that resolves to the IP, so Let's Encrypt can issue
for it with no DNS setup.

## Operate
```bash
deploy/aws_deploy.sh status        # instance, URL, /api/health
deploy/aws_deploy.sh logs          # follow backend + Caddy logs (logs backend | logs caddy)
deploy/aws_deploy.sh ssh           # shell; data in /srv/macrae/data, code in /srv/macrae/app
deploy/aws_deploy.sh data          # re-sync papers/ + index/, restart the backend
deploy/aws_deploy.sh ingest        # build the index on the server instead (python -m rag ingest)
deploy/aws_deploy.sh stop | start  # pause billing for compute; same URL after start
deploy/aws_deploy.sh down          # delete instance (and its disk!), IP, security group, key pair
```
On the instance, runs and traces are in `/srv/macrae/data/agent-runner/{runs,jobs}`. For a backup, take an EBS
snapshot of the volume tagged `macrae-backend`, or `rsync` that folder home.

## Troubleshooting
- **SSH times out**: your IP changed. Re-run `up`, which adds your current IP to port 22, or set `MACRAE_SSH_CIDR`.
- **HTTPS not healthy after `up`**: `deploy/aws_deploy.sh logs caddy`. Let's Encrypt needs port 80 open and
  the domain to resolve to the Elastic IP. Rate limits are per domain; sslip.io is on the Public Suffix List,
  so each IP has its own limit.
- **`no default VPC`**: set `MACRAE_SUBNET_ID` to a public subnet (one with a route to an internet gateway).
- **Agent steps fail with Modal auth errors**: check `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` in `deploy/.env`
  (or your `~/.modal.toml` profile), then `deploy/aws_deploy.sh code`. The container writes them to
  `~/.modal.toml` under `$MODAL_PROFILE` at start.
- **Out of memory during build**: the instance has 2 GB of swap. If that's not enough, use
  `MACRAE_INSTANCE_TYPE=t3.medium`, then `down` + `up`. The type of an existing instance isn't changed in place.
