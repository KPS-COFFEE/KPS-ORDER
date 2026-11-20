#!/bin/sh
# Local review artifact only. Does not run the application, push, or deploy.
set -eu
if [ "$#" -ne 1 ]; then
  echo "Usage: tools/security_image_review.sh /absolute/private/evidence-dir" >&2
  exit 2
fi
case "$1" in /*) ;; *) echo 'Evidence path must be absolute.' >&2; exit 2;; esac
repo=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$repo"
if [ -n "$(git status --porcelain)" ]; then
  echo 'Commit the reviewed candidate first; image evidence must identify a clean SHA.' >&2
  exit 2
fi
for required in docker syft trivy; do
  command -v "$required" >/dev/null 2>&1 || { echo "Missing $required" >&2; exit 2; }
done
mkdir -p "$1"
evidence=$(CDPATH= cd -- "$1" && pwd)
sha=$(git rev-parse HEAD)
image="kps-order-security-review:$sha"
printf '%s\n' "$sha" > "$evidence/source-sha.txt"
sha256sum Dockerfile requirements.txt requirements.lock .dockerignore > "$evidence/build-input-sha256.txt"
docker version > "$evidence/docker-version.txt"
syft version > "$evidence/syft-version.txt"
trivy version > "$evidence/trivy-version.txt"
# This uses the immutable base in Dockerfile and never pushes a registry tag.
docker buildx build --platform linux/amd64 --load --provenance=false \
  --label "org.opencontainers.image.revision=$sha" \
  --metadata-file "$evidence/build-metadata.json" --tag "$image" . \
  > "$evidence/build.log" 2>&1
docker image inspect "$image" > "$evidence/image-inspect.json"
docker image save --output "$evidence/candidate-image.tar" "$image"
sha256sum "$evidence/candidate-image.tar" > "$evidence/archive-sha256.txt"
syft scan "docker-archive:$evidence/candidate-image.tar" \
  -o "cyclonedx-json=$evidence/candidate-image.cdx.json"
trivy image --input "$evidence/candidate-image.tar" --scanners vuln,secret \
  --format json --output "$evidence/candidate-image-scan.json" \
  --exit-code 1 --severity HIGH,CRITICAL
printf '%s\n' 'Image built and scanned locally. No container started, pushed, or deployed.'
