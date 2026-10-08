#!/usr/bin/env bash
# Deploy the macrae backend to one small EC2 instance with an HTTPS URL. See deploy/aws.md.
#
#   deploy/aws_deploy.sh up          create or update everything (idempotent), print the URL   [default]
#   deploy/aws_deploy.sh code        ship the code and rebuild/restart the container only
#   deploy/aws_deploy.sh data        sync papers/ and index/ to the server, restart the backend
#   deploy/aws_deploy.sh ingest      build the RAG index on the server (python -m rag ingest), restart
#   deploy/aws_deploy.sh status      instance state, URL, /api/health
#   deploy/aws_deploy.sh url         print the backend URL
#   deploy/aws_deploy.sh logs [svc]  follow container logs (backend | caddy)
#   deploy/aws_deploy.sh ssh [cmd]   shell on the instance
#   deploy/aws_deploy.sh stop|start  stop the instance (pay only disk + IP) / start it again; the URL stays
#   deploy/aws_deploy.sh down [--yes] delete the instance, Elastic IP, security group and key pair
#   deploy/aws_deploy.sh cost        monthly cost estimate
#
# Needs: aws CLI v2 with credentials (aws configure / AWS_PROFILE), ssh, tar, python3, curl. No local Docker:
# the image is built on the instance. Settings come from the environment, then deploy/.env:
#   AWS_REGION (default: aws configure, else eu-central-1)   MACRAE_INSTANCE_TYPE (t3.small)   MACRAE_DISK_GB (20)
#   MACRAE_DOMAIN (default <ip-with-dashes>.sslip.io)        MACRAE_NAME (macrae-backend)       MACRAE_SUBNET_ID
#   MACRAE_SSH_CIDR (default: your current IP/32)            MACRAE_SSH_KEY (~/.ssh/macrae_deploy)
#   MACRAE_SKIP_DATA=1 (don't sync papers/index in `up`)     ENV_FILE (deploy/.env)
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$DEPLOY_DIR")"
ENV_FILE="${ENV_FILE:-$DEPLOY_DIR/.env}"
STATE_DIR="${MACRAE_STATE_DIR:-$DEPLOY_DIR/.state}"
STATE_FILE="$STATE_DIR/aws.env"
PY="${PYTHON:-python3}"
REMOTE_USER=ubuntu
REMOTE_ROOT=/srv/macrae
PROJECT_TAG=macrae
export AWS_PAGER=""

log() { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ── pure helpers (tested in deploy/tests/test_aws_deploy.py) ─────────────────────────────────────────────

# setting KEY DEFAULT: process env, then $ENV_FILE, then DEFAULT (blank = unset)
setting() { "$PY" "$DEPLOY_DIR/envtool.py" get "$ENV_FILE" "$1" "${2:-}"; }

# 3.120.5.7 → 3-120-5-7.sslip.io (public wildcard DNS: resolves to the IP, so Let's Encrypt can issue for it)
sslip_host() {
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
  printf '%s.sslip.io\n' "${1//./-}"
}

# t4g.small, m7gd.large, c6gn.xlarge → arm64 (Graviton); t3.small, g5.xlarge, m7i.large → amd64
ami_arch() {
  local family="${1%%.*}"
  if [[ "$family" =~ ^[a-z]+[0-9]+([a-z-]*)$ ]] && [[ "${BASH_REMATCH[1]}" == *g* ]]; then
    echo arm64
  else
    echo amd64
  fi
}

ami_param() { echo "/aws/service/canonical/ubuntu/server/24.04/stable/current/$(ami_arch "$1")/hvm/ebs-gp3/ami-id"; }

sha256_hex() { if command -v sha256sum >/dev/null; then sha256sum | cut -d' ' -f1; else shasum -a 256 | cut -d' ' -f1; fi; }

# key pair name derived from the public key, so an existing pair with that name is always ours
key_name_for() { printf 'macrae-%s\n' "$(cut -d' ' -f2 "$1" | tr -d '\n' | sha256_hex | cut -c1-12)"; }

# AWS text output prints "None" for null
nonempty() { [ -n "${1:-}" ] && [ "$1" != "None" ] && [ "$1" != "null" ]; }

# ── settings ────────────────────────────────────────────────────────────────────────────────────────────

load_settings() {
  NAME="$(setting MACRAE_NAME macrae-backend)"
  INSTANCE_TYPE="$(setting MACRAE_INSTANCE_TYPE t3.small)"
  DISK_GB="$(setting MACRAE_DISK_GB 20)"
  DOMAIN_OVERRIDE="$(setting MACRAE_DOMAIN)"
  SUBNET_OVERRIDE="$(setting MACRAE_SUBNET_ID)"
  SSH_CIDR="$(setting MACRAE_SSH_CIDR)"
  SSH_KEY="$(setting MACRAE_SSH_KEY "$HOME/.ssh/macrae_deploy")"
  SSH_KEY="${SSH_KEY/#\~/$HOME}"
  REGION="$(setting AWS_REGION)"
  [ -n "$REGION" ] || REGION="$(setting AWS_DEFAULT_REGION)"
  [ -n "$REGION" ] || REGION="$(state_get REGION)"
  [ -n "$REGION" ] || REGION="$(aws configure get region 2>/dev/null || true)"
  [ -n "$REGION" ] || REGION=eu-central-1
  [[ "$DISK_GB" =~ ^[0-9]+$ ]] && [ "$DISK_GB" -ge 12 ] || die "MACRAE_DISK_GB must be a number >= 12"
}

state_get() { [ -f "$STATE_FILE" ] && sed -n "s/^$1=//p" "$STATE_FILE" | tail -1 || true; }

state_save() {
  mkdir -p "$STATE_DIR"
  cat > "$STATE_FILE" <<EOF
# written by deploy/aws_deploy.sh; AWS resources are found by tag, this file is a cache
REGION=$REGION
NAME=$NAME
INSTANCE_ID=${INSTANCE_ID:-}
ALLOC_ID=${ALLOC_ID:-}
PUBLIC_IP=${PUBLIC_IP:-}
DOMAIN=${DOMAIN:-}
URL=${DOMAIN:+https://$DOMAIN}
EOF
}

aws_() { aws --region "$REGION" --output text "$@"; }

need() { command -v "$1" >/dev/null || die "$1 not found: $2"; }

preflight() {
  need aws "install the AWS CLI v2 (https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)"
  need ssh "install OpenSSH"
  need curl "install curl"
  need tar "install tar"
  local account
  account="$(aws_ sts get-caller-identity --query Account 2>/dev/null)" \
    || die "no AWS credentials: run 'aws configure' (or set AWS_PROFILE)"
  log "AWS account $account, region $REGION, $INSTANCE_TYPE"
}

# ── AWS resources (found by tag Name=$NAME, Project=macrae) ────────────────────────────────────────────────

ensure_key() {
  if [ ! -f "$SSH_KEY" ]; then
    need ssh-keygen "install OpenSSH"
    mkdir -p "$(dirname "$SSH_KEY")"
    ssh-keygen -q -t ed25519 -N "" -C "macrae-deploy" -f "$SSH_KEY"
    log "created SSH key $SSH_KEY"
  fi
  [ -f "$SSH_KEY.pub" ] || die "$SSH_KEY.pub missing (ssh-keygen -y -f $SSH_KEY > $SSH_KEY.pub)"
  KEY_NAME="$(key_name_for "$SSH_KEY.pub")"
  if ! aws_ ec2 describe-key-pairs --key-names "$KEY_NAME" --query 'KeyPairs[0].KeyName' >/dev/null 2>&1; then
    aws_ ec2 import-key-pair --key-name "$KEY_NAME" --public-key-material "fileb://$SSH_KEY.pub" \
      --tag-specifications "ResourceType=key-pair,Tags=[{Key=Project,Value=$PROJECT_TAG}]" >/dev/null
    log "imported key pair $KEY_NAME"
  fi
}

find_network() {
  if [ -n "$SUBNET_OVERRIDE" ]; then
    SUBNET_ID="$SUBNET_OVERRIDE"
    VPC_ID="$(aws_ ec2 describe-subnets --subnet-ids "$SUBNET_ID" --query 'Subnets[0].VpcId')"
  else
    VPC_ID="$(aws_ ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId')"
    nonempty "$VPC_ID" || die "no default VPC in $REGION: set MACRAE_SUBNET_ID to a public subnet"
    SUBNET_ID="$(aws_ ec2 describe-subnets \
      --filters "Name=vpc-id,Values=$VPC_ID" Name=default-for-az,Values=true \
      --query 'sort_by(Subnets,&AvailabilityZone)[0].SubnetId')"
  fi
  nonempty "$SUBNET_ID" || die "no subnet found in $VPC_ID: set MACRAE_SUBNET_ID"
}

my_cidr() {
  if [ -n "$SSH_CIDR" ]; then echo "$SSH_CIDR"; return; fi
  local ip
  ip="$(curl -fsS --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')" \
    || die "cannot detect your public IP: set MACRAE_SSH_CIDR"
  echo "$ip/32"
}

sg_allow() {  # sg_allow GROUP PORT CIDR
  local range out
  if [[ "$3" == *:* ]]; then range="Ipv6Ranges=[{CidrIpv6=$3}]"; else range="IpRanges=[{CidrIp=$3}]"; fi
  if ! out="$(aws_ ec2 authorize-security-group-ingress --group-id "$1" \
        --ip-permissions "IpProtocol=tcp,FromPort=$2,ToPort=$2,$range" 2>&1)"; then
    [[ "$out" == *InvalidPermission.Duplicate* ]] || die "authorize $2 from $3: $out"
  fi
}

ensure_sg() {
  SG_ID="$(aws_ ec2 describe-security-groups \
    --filters "Name=group-name,Values=$NAME" "Name=vpc-id,Values=$VPC_ID" --query 'SecurityGroups[0].GroupId')"
  if ! nonempty "$SG_ID"; then
    SG_ID="$(aws_ ec2 create-security-group --group-name "$NAME" --vpc-id "$VPC_ID" \
      --description "macrae backend: HTTPS + SSH from the deployer" \
      --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=$PROJECT_TAG}]" \
      --query GroupId)"
    log "created security group $SG_ID"
  fi
  sg_allow "$SG_ID" 80 0.0.0.0/0      # ACME HTTP challenge + redirect to HTTPS
  sg_allow "$SG_ID" 443 0.0.0.0/0
  sg_allow "$SG_ID" 443 ::/0
  sg_allow "$SG_ID" 22 "$(my_cidr)"
}

find_instance() {  # sets INSTANCE_ID, INSTANCE_STATE (empty if none)
  local out
  out="$(aws_ ec2 describe-instances \
    --filters "Name=tag:Name,Values=$NAME" "Name=tag:Project,Values=$PROJECT_TAG" \
              Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query 'Reservations[].Instances[].[InstanceId,State.Name,InstanceType]' | head -1)"
  INSTANCE_ID="$(echo "$out" | cut -f1)"
  INSTANCE_STATE="$(echo "$out" | cut -f2)"
  ACTUAL_TYPE="$(echo "$out" | cut -f3)"
  nonempty "$INSTANCE_ID" || { INSTANCE_ID=""; INSTANCE_STATE=""; ACTUAL_TYPE=""; }
}

ensure_instance() {
  find_instance
  if [ -z "$INSTANCE_ID" ]; then
    local ami root
    ami="$(aws_ ssm get-parameter --name "$(ami_param "$INSTANCE_TYPE")" --query Parameter.Value)"
    root="$(aws_ ec2 describe-images --image-ids "$ami" --query 'Images[0].RootDeviceName')"
    log "launching $INSTANCE_TYPE from $ami (Ubuntu 24.04)"
    INSTANCE_ID="$(aws_ ec2 run-instances --image-id "$ami" --instance-type "$INSTANCE_TYPE" --count 1 \
      --key-name "$KEY_NAME" \
      --network-interfaces "DeviceIndex=0,SubnetId=$SUBNET_ID,Groups=$SG_ID,AssociatePublicIpAddress=true" \
      --block-device-mappings "DeviceName=$root,Ebs={VolumeSize=$DISK_GB,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}" \
      --metadata-options HttpTokens=required,HttpEndpoint=enabled \
      --user-data "file://$DEPLOY_DIR/cloud-init.sh" \
      --tag-specifications \
        "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=$PROJECT_TAG}]" \
        "ResourceType=volume,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=$PROJECT_TAG}]" \
      --query 'Instances[0].InstanceId')"
    log "instance $INSTANCE_ID"
  elif [ "$INSTANCE_STATE" = stopped ] || [ "$INSTANCE_STATE" = stopping ]; then
    [ "$INSTANCE_STATE" = stopping ] && aws_ ec2 wait instance-stopped --instance-ids "$INSTANCE_ID"
    log "starting stopped instance $INSTANCE_ID"
    aws_ ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null
  else
    log "instance $INSTANCE_ID is $INSTANCE_STATE"
    [ "$ACTUAL_TYPE" = "$INSTANCE_TYPE" ] || warn "instance is $ACTUAL_TYPE, not $INSTANCE_TYPE (change it in the console, or down + up)"
  fi
  aws_ ec2 wait instance-running --instance-ids "$INSTANCE_ID"
}

find_eip() {  # sets ALLOC_ID, PUBLIC_IP, EIP_INSTANCE
  local out
  out="$(aws_ ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME" "Name=tag:Project,Values=$PROJECT_TAG" \
    --query 'Addresses[0].[AllocationId,PublicIp,InstanceId]')"
  ALLOC_ID="$(echo "$out" | cut -f1)"; PUBLIC_IP="$(echo "$out" | cut -f2)"; EIP_INSTANCE="$(echo "$out" | cut -f3)"
  nonempty "$ALLOC_ID" || { ALLOC_ID=""; PUBLIC_IP=""; EIP_INSTANCE=""; }
}

ensure_eip() {
  find_eip
  if [ -z "$ALLOC_ID" ]; then
    local out
    out="$(aws_ ec2 allocate-address --domain vpc \
      --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=$PROJECT_TAG}]" \
      --query '[AllocationId,PublicIp]')"
    ALLOC_ID="$(echo "$out" | cut -f1)"; PUBLIC_IP="$(echo "$out" | cut -f2)"; EIP_INSTANCE=""
    log "allocated Elastic IP $PUBLIC_IP"
  fi
  if [ "$EIP_INSTANCE" != "$INSTANCE_ID" ]; then
    aws_ ec2 associate-address --allocation-id "$ALLOC_ID" --instance-id "$INSTANCE_ID" --allow-reassociation >/dev/null
    log "Elastic IP $PUBLIC_IP → $INSTANCE_ID"
  fi
}

resolve_domain() {
  if [ -n "$DOMAIN_OVERRIDE" ]; then DOMAIN="$DOMAIN_OVERRIDE"; else DOMAIN="$(sslip_host "$PUBLIC_IP")"; fi
}

# ── remote ────────────────────────────────────────────────────────────────────────────────────────────

ssh_opts() {
  mkdir -p "$STATE_DIR"
  SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30
            -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$STATE_DIR/known_hosts.$INSTANCE_ID"
            -o LogLevel=ERROR)
}

remote() { ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$PUBLIC_IP" "$@"; }

wait_ssh() {
  ssh_opts
  log "waiting for SSH on $PUBLIC_IP"
  local i
  for i in $(seq 1 40); do
    if remote true 2>/dev/null; then break; fi
    [ "$i" = 40 ] && die "no SSH after ~7 min (is port 22 open for $(my_cidr)?)"
    sleep 10
  done
  log "waiting for first-boot setup (Docker install, ~2 min on a new instance)"
  local rc=0
  remote 'sudo cloud-init status --wait >/dev/null; test -f /var/lib/macrae-host-ready' || rc=$?
  if [ "$rc" != 0 ]; then
    remote 'sudo tail -40 /var/log/cloud-init-output.log' >&2 || true
    die "first-boot setup failed (log above)"
  fi
}

ship_code() {
  log "shipping code to $REMOTE_ROOT/app"
  # papers, index and secrets don't go with the code; data lives in $REMOTE_ROOT/data
  COPYFILE_DISABLE=1 tar -C "$REPO_DIR" -czf - \
      --exclude=./.git --exclude=./.reference --exclude=./papers --exclude=./index \
      --exclude=./deploy/.state --exclude=./deploy/data \
      --exclude=.venv --exclude=node_modules --exclude=.wrangler --exclude=__pycache__ --exclude='*.pyc' \
      --exclude=.pytest_cache --exclude='*.egg-info' --exclude=.env --exclude='.env.*' --exclude=.dev.vars \
      . \
    | remote "set -e; rm -rf $REMOTE_ROOT/app.new; mkdir -p $REMOTE_ROOT/app.new; tar -xzf - -C $REMOTE_ROOT/app.new"
  "$PY" "$DEPLOY_DIR/envtool.py" render "$ENV_FILE" --domain "$DOMAIN" --data-dir "$REMOTE_ROOT/data" \
    | remote "umask 077; cat > $REMOTE_ROOT/app.new/deploy/.env"
  remote "set -e; cd $REMOTE_ROOT; rm -rf app.old; if [ -d app ]; then mv app app.old; fi; mv app.new app"
}

local_dir() {  # local_dir VAR DEFAULT → absolute path of papers/index as configured locally
  local d; d="$(setting "$1" "$2")"
  case "$d" in /*) echo "$d" ;; *) echo "$REPO_DIR/${d%/}" ;; esac
}

sync_dir() {  # sync_dir LOCAL REMOTE_SUBDIR [--delete]
  local src="$1" dst="$REMOTE_ROOT/data/$2"
  [ -d "$src" ] || { warn "no $src locally, skipping"; return 0; }
  log "syncing $src → $dst"
  if command -v rsync >/dev/null; then
    rsync -az ${3:-} -e "ssh ${SSH_OPTS[*]}" "$src/" "$REMOTE_USER@$PUBLIC_IP:$dst/"
  else
    COPYFILE_DISABLE=1 tar -C "$src" -czf - . | remote "mkdir -p $dst && tar -xzf - -C $dst"
  fi
}

sync_data() {
  sync_dir "$(local_dir MACRAE_PAPERS_DIR papers)" papers
  sync_dir "$(local_dir MACRAE_INDEX_DIR index)" index --delete   # an index must be replaced as a whole
}

compose() { remote "cd $REMOTE_ROOT/app/deploy && docker compose --profile https $*"; }

start_containers() {
  log "building the image on the instance and starting backend + Caddy (first build ~5 min)"
  compose up -d --build --remove-orphans
  remote 'docker image prune -f >/dev/null' || true
}

wait_https() {
  local url="https://$DOMAIN/api/health" i body
  log "waiting for $url (certificate + startup)"
  for i in $(seq 1 60); do
    if body="$(curl -fsS --max-time 10 "$url" 2>/dev/null)"; then
      log "healthy: $body"
      return 0
    fi
    sleep 5
  done
  warn "$url not healthy after 5 min; recent logs:"
  compose logs --tail 60 >&2 || true
  return 1
}

require_instance() {
  find_instance
  [ -n "$INSTANCE_ID" ] || die "no instance named $NAME in $REGION (run: deploy/aws_deploy.sh up)"
  find_eip
  [ -n "$PUBLIC_IP" ] || die "no Elastic IP for $NAME (run: deploy/aws_deploy.sh up)"
  resolve_domain
  [ -f "$SSH_KEY" ] || die "SSH key $SSH_KEY not found (the instance was created with another key)"
  ssh_opts
}

# ── commands ──────────────────────────────────────────────────────────────────────────────────────────

cmd_up() {
  "$PY" "$DEPLOY_DIR/envtool.py" check "$ENV_FILE" || die "fix $ENV_FILE first"
  preflight
  ensure_key
  find_network
  ensure_sg
  ensure_instance
  ensure_eip
  resolve_domain
  state_save
  wait_ssh
  ship_code
  [ "$(setting MACRAE_SKIP_DATA)" = 1 ] || sync_data
  start_containers
  state_save
  wait_https || die "the backend didn't come up; check: deploy/aws_deploy.sh logs"
  cat >&2 <<EOF

Backend: https://$DOMAIN
Next:
  make deploy-web web-secrets    # Worker with BACKEND_URL=https://$DOMAIN (read from deploy/.state)
  make voice-setup               # ElevenLabs agent tools → https://$DOMAIN/api/tools/...
  deploy/aws_deploy.sh ingest    # if the server has papers but no index yet
EOF
  echo "https://$DOMAIN"
}

cmd_code() {
  "$PY" "$DEPLOY_DIR/envtool.py" check "$ENV_FILE" || die "fix $ENV_FILE first"
  require_instance
  ship_code
  start_containers
  state_save
  wait_https
}

cmd_data() { require_instance; sync_data; compose restart backend; wait_https; }

cmd_ingest() {
  require_instance
  log "python -m rag ingest on the server"
  compose exec -T backend python -m rag ingest
  compose restart backend
  wait_https
}

cmd_status() {
  find_instance
  if [ -z "$INSTANCE_ID" ]; then echo "no instance named $NAME in $REGION"; return 0; fi
  find_eip; resolve_domain
  echo "instance  $INSTANCE_ID ($INSTANCE_STATE, $ACTUAL_TYPE)"
  echo "ip        ${PUBLIC_IP:-none}"
  echo "url       https://$DOMAIN"
  if [ "$INSTANCE_STATE" = running ]; then
    echo "health    $(curl -fsS --max-time 10 "https://$DOMAIN/api/health" 2>&1 || true)"
  fi
}

cmd_url() {
  local url; url="$(state_get URL)"
  if [ -z "$url" ]; then find_eip; [ -n "$PUBLIC_IP" ] || die "not deployed"; resolve_domain; url="https://$DOMAIN"; fi
  echo "$url"
}

cmd_logs() { require_instance; compose logs -f --tail 200 "${1:-}"; }

cmd_ssh() { require_instance; ssh "${SSH_OPTS[@]}" -t "$REMOTE_USER@$PUBLIC_IP" "$@"; }

cmd_stop() {
  find_instance; [ -n "$INSTANCE_ID" ] || die "no instance"
  aws_ ec2 stop-instances --instance-ids "$INSTANCE_ID" >/dev/null
  aws_ ec2 wait instance-stopped --instance-ids "$INSTANCE_ID"
  log "stopped $INSTANCE_ID (still billed: disk + Elastic IP, about \$5/month). Start with: deploy/aws_deploy.sh start"
}

cmd_start() {
  find_instance; [ -n "$INSTANCE_ID" ] || die "no instance (run: deploy/aws_deploy.sh up)"
  aws_ ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null
  aws_ ec2 wait instance-running --instance-ids "$INSTANCE_ID"
  find_eip; resolve_domain
  wait_https   # containers restart on boot (restart: unless-stopped)
}

cmd_down() {
  if [ "${1:-}" != "--yes" ]; then
    printf 'Delete the macrae backend %s in %s (instance, disk with runs/traces, Elastic IP)? [y/N] ' "$NAME" "$REGION"
    local answer=""; read -r answer || true
    [ "$answer" = y ] || [ "$answer" = Y ] || die "aborted"
  fi
  find_instance
  if [ -n "$INSTANCE_ID" ]; then
    aws_ ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null
    log "terminating $INSTANCE_ID"
    aws_ ec2 wait instance-terminated --instance-ids "$INSTANCE_ID"
  fi
  find_eip
  if [ -n "$ALLOC_ID" ]; then aws_ ec2 release-address --allocation-id "$ALLOC_ID" >/dev/null; log "released $PUBLIC_IP"; fi
  local sg
  sg="$(aws_ ec2 describe-security-groups --filters "Name=group-name,Values=$NAME" "Name=tag:Project,Values=$PROJECT_TAG" \
    --query 'SecurityGroups[0].GroupId')"
  if nonempty "$sg"; then
    local i
    for i in 1 2 3 4 5 6; do  # the terminated instance's network interface can hold on to it for a few seconds
      if aws_ ec2 delete-security-group --group-id "$sg" >/dev/null 2>&1; then log "deleted security group $sg"; break; fi
      [ "$i" = 6 ] && warn "could not delete security group $sg yet; delete it later in the EC2 console"
      sleep 10
    done
  fi
  if [ -f "$SSH_KEY.pub" ]; then
    aws_ ec2 delete-key-pair --key-name "$(key_name_for "$SSH_KEY.pub")" >/dev/null 2>&1 || true
  fi
  rm -f "$STATE_FILE" "$STATE_DIR"/known_hosts.*
  log "done; nothing of $NAME is left in $REGION"
}

cmd_cost() {
  cat <<EOF
Monthly estimate for $NAME ($INSTANCE_TYPE, ${DISK_GB} GB gp3, Elastic IP), on-demand, 730 h:
  t3.small   2 vCPU, 2 GiB   us-east-1 \$15.18   eu-central-1 \$17.52
  t4g.small  2 vCPU, 2 GiB   us-east-1 \$12.26   eu-central-1 \$14.02   (Graviton, arm64)
  t3.medium  2 vCPU, 4 GiB   us-east-1 \$30.37   eu-central-1 \$35.04
  gp3 disk   \$0.08 (us-east-1) / \$0.0952 (eu-central-1) per GB-month → ${DISK_GB} GB = \$$(awk "BEGIN{printf \"%.2f\", $DISK_GB*0.08}") / \$$(awk "BEGIN{printf \"%.2f\", $DISK_GB*0.0952}")
  public IPv4 (Elastic IP) \$0.005/h → \$3.65
  data out   first 100 GB/month free (the API is small JSON)
  Default (t3.small, 20 GB): ≈ \$20.43/month in us-east-1, ≈ \$23.07 in eu-central-1.
  Stopped (deploy/aws_deploy.sh stop): disk + IP only ≈ \$5.25-5.55/month.
Not included: Modal compute for agent steps (per second, see modal.com/pricing),
Claude usage (subscription token or API key), ElevenLabs minutes, Cloudflare Workers (free tier).
EOF
}

main() {
  local cmd="${1:-up}"; shift || true
  load_settings
  case "$cmd" in
    up|code|data|ingest|status|url|logs|ssh|stop|start|down|cost) "cmd_$cmd" "$@" ;;
    -h|--help|help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) die "unknown command $cmd (see deploy/aws_deploy.sh help)" ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
