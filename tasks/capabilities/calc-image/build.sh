#!/usr/bin/env bash
# Build (TEST) and optionally push (for INSTALL) the calc-image capability.
#   tasks/capabilities/calc-image/build.sh IMAGE[:TAG] [--base BASE_IMAGE] [--push]
# The build runs smoke.py (a failing smoke test fails the build); then the image is run once more to time a cold
# start of the smoke test. Use the macrae agent image (tasks/common/build.sh) as --base so the live-trace wrapper
# stays. After --push, the backend's installer records IMAGE in the capability registry and fills the flow var
# calc_image for later small-calc runs.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
image="${1:-}"; shift || true
base="ubuntu:24.04"; push=""
while [ $# -gt 0 ]; do
  case "$1" in
    --base) base="$2"; shift 2 ;;
    --push) push=1; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
if [ -z "$image" ] || [ "${image#-}" != "$image" ]; then
  echo "usage: $0 IMAGE[:TAG] [--base BASE_IMAGE] [--push]" >&2
  exit 2
fi
python3 "$here/../../checks.py" image-capability "$here"
docker build --platform linux/amd64 --build-arg "BASE_IMAGE=$base" -t "$image" -f "$here/Dockerfile" "$here"
docker run --rm --platform linux/amd64 "$image" /opt/calc/bin/python /opt/calc-capability/smoke.py
echo "built $image (smoke test OK)"
if [ -n "$push" ]; then
  docker push "$image"
  echo "pushed. Install: set the small-calc flow var calc_image=$image (the capability registry does this)."
fi
