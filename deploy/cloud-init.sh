#!/bin/bash
# EC2 user data for the macrae backend host (Ubuntu 24.04). Runs once at first boot; aws_deploy.sh waits for it
# (`cloud-init status --wait`) before shipping the code. Installs Docker + compose + buildx, adds swap so the
# image build fits in 2 GB of RAM, and prepares /srv/macrae (app/ = code, data/ = papers, index, runs, traces).
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

for i in 1 2 3 4 5; do
  if apt-get update -q && apt-get install -y -q docker.io docker-buildx docker-compose-v2 rsync jq; then
    break
  fi
  [ "$i" = 5 ] && exit 1
  sleep 15
done

systemctl enable --now docker
usermod -aG docker ubuntu

# uid 1000 = ubuntu on the host = macrae inside the container, so the bind-mounted data stays writable
mkdir -p /srv/macrae/app /srv/macrae/data/papers /srv/macrae/data/index /srv/macrae/data/agent-runner
chown -R 1000:1000 /srv/macrae

touch /var/lib/macrae-host-ready
