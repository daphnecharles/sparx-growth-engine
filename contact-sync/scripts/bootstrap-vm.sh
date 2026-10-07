#!/usr/bin/env bash
# One-time setup on the Oracle Cloud VM (Ubuntu 24.04, Ampere A1).
# Run as the default "ubuntu" user:  bash scripts/bootstrap-vm.sh
#
# Installs Docker + the compose plugin and opens ports 80/443 in the VM's own
# firewall. Oracle's Ubuntu images ship with iptables rules that REJECT
# everything except SSH, *in addition to* the VCN security list — both must
# allow 80/443 or Let's Encrypt cannot issue the certificate.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 iptables-persistent
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 1 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
  fi
done
if ! sudo iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT 1 -p udp --dport 443 -j ACCEPT
fi
sudo netfilter-persistent save

echo
echo "Done. Log out and back in (so the docker group applies), then:"
echo "  cp .env.example .env && chmod 600 .env   # fill it in"
echo "  docker compose up -d"
echo
echo "Reminder: the VCN security list in the Oracle Cloud console must ALSO allow"
echo "ingress TCP 80 and 443 (and UDP 443) from 0.0.0.0/0. That is a manual console step."
