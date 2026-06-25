#!/usr/bin/env bash
set -euo pipefail

IP="${1:-}"
if [[ -z "$IP" ]]; then
  IP="$(hostname -I | awk '{print $1}')"
fi

if [[ -z "$IP" ]]; then
  echo "Usage: $0 <PC_LAN_IP>" >&2
  exit 2
fi

mkdir -p certs

openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout certs/xinyu-key.pem \
  -out certs/xinyu-cert.pem \
  -subj "/CN=${IP}" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:${IP}"

chmod 600 certs/xinyu-key.pem

echo "Generated:"
echo "  certs/xinyu-key.pem"
echo "  certs/xinyu-cert.pem"
echo
echo "Start server with:"
echo "  python recamera_fastapi.py --no-dry-run --ssl-keyfile certs/xinyu-key.pem --ssl-certfile certs/xinyu-cert.pem"
echo
echo "Tablet URL:"
echo "  https://${IP}:8001/home"
