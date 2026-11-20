#!/bin/bash

#
# Build NS8 Mail Toolbox OCI images:
#   - ${repobase}/mail-toolbox-app (Python 3.12 FastAPI + Milter + Secretariat Web Portal)
#   - ${repobase}/mail-toolbox     (NS8 rootless module image: /imageroot + /ui)
#

set -e

images=()
repobase="${REPOBASE:-ghcr.io/generablack}"
reponame="mail-toolbox"

cd "$(dirname "$0")"

# 1. Build Secretariat Web Portal (app/frontend/dist) and NS8 Cockpit UI (ui/dist)
#    unless SKIP_UI_BUILD is set or prebuilt bundles already exist (override with FORCE_UI_BUILD=1).
if [[ -z "${SKIP_UI_BUILD:-}" ]]; then
    if [[ "${FORCE_UI_BUILD:-0}" == "1" || ! -f app/frontend/dist/index.html ]]; then
        echo "Building Secretariat Web Portal (app/frontend/dist)..."
        podman run --rm \
            -v "${PWD}/app/frontend:/app:Z" \
            -w /app \
            docker.io/library/node:20-alpine \
            sh -c "npm ci && npm run build"
    fi

    if [[ "${FORCE_UI_BUILD:-0}" == "1" || ! -f ui/dist/index.html ]]; then
        echo "Building NS8 Cockpit UI (ui/dist)..."
        podman run --rm \
            -e NODE_OPTIONS=--openssl-legacy-provider \
            -v "${PWD}/ui:/ui:Z" \
            -w /ui \
            docker.io/library/node:20-alpine \
            sh -c "(npm ci --legacy-peer-deps || npm install --legacy-peer-deps) && npm run build"
    fi
fi

# 2. Build the application runtime image (${repobase}/mail-toolbox-app)
echo "Building ${repobase}/${reponame}-app..."
buildah bud \
    --isolation "${BUILDAH_ISOLATION:-chroot}" \
    --force-rm \
    --tag "${repobase}/${reponame}-app" \
    --file "${PWD}/app/Containerfile" \
    "${PWD}"
images+=("${repobase}/${reponame}-app")

# 3. Stage imageroot cleanly: remove Python cache files, strip any CRLF (\r),
#    and set executable permissions before adding to the scratch module image.
tmp_imageroot="$(mktemp -d)"
cleanup() {
    rm -rf "${tmp_imageroot}"
    if [[ -n "${container:-}" ]]; then
        buildah rm "${container}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

cp -a imageroot/. "${tmp_imageroot}/"
find "${tmp_imageroot}" -name "__pycache__" -type d -exec rm -rf {} +
find "${tmp_imageroot}" -name "*.pyc" -delete
find "${tmp_imageroot}" -type f -exec sed -i 's/\r$//' {} +
chmod +x \
    "${tmp_imageroot}"/actions/*/[0-9]* \
    "${tmp_imageroot}"/events/*/[0-9]* \
    "${tmp_imageroot}"/update-module.d/* \
    "${tmp_imageroot}"/bin/*

# 4. Build the NS8 module metadata/imageroot image (${repobase}/mail-toolbox) from scratch
echo "Building ${repobase}/${reponame}..."
container=$(buildah from scratch)
buildah add "${container}" "${tmp_imageroot}" /imageroot
buildah add "${container}" ui/dist /ui
buildah config --entrypoint=/ \
    --label="org.nethserver.authorizations=traefik@node:routeadm mail@any:mailadm cluster:accountconsumer" \
    --label="org.nethserver.tcp-ports-demand=2" \
    --label="org.nethserver.rootfull=0" \
    --label="org.nethserver.min-core=3.20.1" \
    --label="org.nethserver.images=${repobase}/mail-toolbox-app:${IMAGETAG:-latest}" \
    --label="org.opencontainers.image.title=NS8 Mail Toolbox" \
    --label="org.opencontainers.image.description=Delegated sick-leave auto-responder, Before-DKIM company signatures, quarantine digest, Rspamd mail tracking, and POP3/IMAP connectors for NethServer 8" \
    --label="org.opencontainers.image.vendor=GeneraBlack" \
    --label="org.opencontainers.image.licenses=GPL-3.0-or-later" \
    --label="org.opencontainers.image.source=https://github.com/GeneraBlack/ns8-mailtoolbox" \
    "${container}"

buildah commit "${container}" "${repobase}/${reponame}"
images+=("${repobase}/${reponame}")

# 5. Tag and optionally push built images (standard NS8 CI integration)
printf "Publish the images with:\n\n"
for image in "${images[@],,}"; do
    printf "  buildah push %s docker://%s:%s\n" "${image}" "${image}" "${IMAGETAG:-latest}"
done
printf "\n"

if [[ -n "${CI:-}" ]]; then
    read -r -a tags_to_apply <<< "${IMAGETAGS:-${IMAGETAG:-latest}}"
    # Deduplicate tags while preserving order
    unique_tags=()
    for candidate_tag in "${tags_to_apply[@]}"; do
        [[ -z "${candidate_tag}" ]] && continue
        already_seen=0
        for existing_tag in "${unique_tags[@]}"; do
            if [[ "${existing_tag}" == "${candidate_tag}" ]]; then
                already_seen=1
                break
            fi
        done
        if [[ "${already_seen}" == "0" ]]; then
            unique_tags+=("${candidate_tag}")
        fi
    done

    images_output=()
    for image in "${images[@],,}"; do
        for tag in "${unique_tags[@]}"; do
            if [[ "${tag}" != "latest" ]]; then
                buildah tag "${image}" "${image}:${tag}"
            fi
            images_output+=("${image}:${tag}")
            if [[ "${PUBLISH_IMAGES:-0}" == "1" ]]; then
                buildah push "${image}:${tag}" "docker://${image}:${tag}"
            fi
        done
    done
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        printf "images=%s\n" "${images_output[*]}" >> "${GITHUB_OUTPUT}"
    fi
fi
