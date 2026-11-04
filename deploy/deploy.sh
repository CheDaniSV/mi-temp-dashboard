#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${MI_IMAGE:?Set MI_IMAGE to the built image tag}"
test -S /run/dbus/system_bus_socket || { echo 'Host BlueZ/D-Bus is required'; exit 1; }
previous_container=$(docker compose ps -aq app)
previous_image=''
if [ -n "$previous_container" ]; then
  previous_image=$(docker inspect --format '{{.Image}}' "$previous_container")
  docker tag "$previous_image" mi-temp:rollback
fi
if docker compose up -d --no-build --wait --wait-timeout 90; then
  echo 'Deployed: http://192.168.1.102:8088'
else
  echo 'Deployment failed; checking previous image.' >&2
  docker compose logs --tail=50 app
  if [ -n "$previous_image" ]; then
    MI_IMAGE=mi-temp:rollback docker compose up -d --no-build --wait --wait-timeout 90
  else
    docker compose stop app
  fi
  exit 1
fi
