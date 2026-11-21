ARG IMLADRI_IMAGE=cloudflare

FROM node:24.22.2-trixie-slim AS frontend
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
ENV CI=true GENERATE_SOURCEMAP=false REACT_APP_BACKEND_URL=/ COREPACK_ENABLE_DOWNLOAD_PROMPT=0
WORKDIR /frontend
COPY frontend/package.json frontend/yarn.lock ./
COPY frontend/patches/ ./patches/
RUN corepack yarn install --frozen-lockfile --production=false
COPY frontend/ ./
COPY backend/seed/couples_cards.json backend/seed/couples_reflections.json backend/seed/intimacy.json /backend/seed/
RUN corepack yarn build

# Use signed Wolfi packages for a Python 3.12 runtime without shells or APK.
# The package database stays in the final image for source-aware security audits.
FROM cgr.dev/chainguard/wolfi-base@sha256:d13acb327aa77acf2752acea8c08456511a5b8142455cc2fe0f966b960bf64c6 AS python-builder
ARG WOLFI_PYTHON_VERSION=3.12.16_git20261030-r0
RUN apk add --no-cache python-3.12=${WOLFI_PYTHON_VERSION} libstdc++ \
    && apk upgrade --no-cache
WORKDIR /app
COPY backend/requirements.txt backend/constraints.txt /app/backend/
COPY security/native-patches /native-inputs/
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --only-binary=:all: --upgrade pip==26.2 \
    && /opt/venv/bin/python -m pip install --no-cache-dir --only-binary=:all: -r backend/requirements.txt \
    && /opt/venv/bin/python -m pip install --no-cache-dir --no-index --no-deps --force-reinstall \
       --require-hashes -r /native-inputs/cryptography/install.lock \
    && /opt/venv/bin/python -m pip check \
    && rm -rf /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.12 \
       /opt/venv/lib/python3.12/site-packages/pip /opt/venv/lib/python3.12/site-packages/pip-*.dist-info
# Retain the reviewed signed glibc package; custom glibc candidates are held.
RUN mkdir -p /runtime \
    && apk --root /runtime --initdb --no-cache --no-scripts \
       --keys-dir /etc/apk/keys --repositories-file /etc/apk/repositories \
       add python-3.12-base=${WOLFI_PYTHON_VERSION} glibc-2.44=2.44-r17 libstdc++ ca-certificates-bundle tzdata wolfi-keys \
    && ln -s python3.12 /runtime/usr/bin/python \
    && mkdir -p /runtime/tmp /runtime/home/nonroot \
    && chmod 1777 /runtime/tmp \
    && chown 65532:65532 /runtime/home/nonroot \
    && printf 'nonroot:x:65532:65532:Imladri runtime:/home/nonroot:/sbin/nologin\n' >> /runtime/etc/passwd \
    && printf 'nonroot:x:65532:\n' >> /runtime/etc/group \
    && cp /etc/apk/repositories /runtime/etc/apk/repositories \
    && /usr/bin/ldconfig -r /runtime \
    && find /runtime -xdev -type f -perm /6000 -exec chmod a-s {} +
COPY scripts/prune_runtime_components.py scripts/prune_dating_image_codecs.py /tmp/
RUN python /tmp/prune_dating_image_codecs.py --venv /opt/venv \
    --manifest /runtime/app/backend/dating_image_runtime.json
RUN python /tmp/prune_runtime_components.py --root /runtime --venv /opt/venv \
    --manifest /runtime/app/backend/runtime_pruning.json --apply
COPY scripts/build_native_inventory.py /tmp/build_native_inventory.py
RUN python /native-inputs/openssl/install_payload.py --root /runtime \
    && python /tmp/build_native_inventory.py --root /runtime --venv /opt/venv \
       --inputs /native-inputs --output /runtime/app/native_security_inventory.json

# Pure Python operator dependencies are isolated from application site-packages.
# This stage is unused by the Cloudflare/API image.
FROM python-builder AS postgres-operator-builder
COPY scripts/requirements-postgres-operator.txt scripts/build_postgres_operator.py /operator-build/
RUN python -m venv /operator-installer \
    && /operator-installer/bin/python -m pip download --no-cache-dir --no-deps \
       --only-binary=:all: --require-hashes --dest /operator-wheels \
       -r /operator-build/requirements-postgres-operator.txt \
    && python /operator-build/build_postgres_operator.py --wheels /operator-wheels \
       --requirements /operator-build/requirements-postgres-operator.txt --output /postgres-operator

# Test tooling is added only to the isolated QA target, never production.
FROM python-builder AS qa-tools
RUN python -m venv /qa-installer \
    && /qa-installer/bin/python -m pip install --no-cache-dir --only-binary=:all: \
       -c /app/backend/constraints.txt --target /qa pytest pytest-xdist \
    && printf '/qa\n' > /qa-tools.pth

FROM scratch AS cloudflare
COPY --from=python-builder /runtime/ /
COPY --from=python-builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:/usr/bin PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/constraints.txt /app/backend/
COPY backend/*.py /app/backend/
COPY backend/dating/*.py /app/backend/dating/
COPY backend/postgres_schema.sql /app/backend/
COPY backend/postgres_store_schema.sql /app/backend/
COPY backend/seed/*.json /app/backend/seed/
COPY backend/seed/reader_ballot.html backend/seed/rehearsal_ballot.html backend/seed/prompt-transcription-sample.png /app/backend/seed/
COPY backend/seed/figure_profiles/*.json /app/backend/seed/figure_profiles/
COPY backend/seed/figure_dating/*.json /app/backend/seed/figure_dating/
COPY backend/seed/media/*.jpg /app/backend/seed/media/
COPY cloudflare/entrypoint.py /app/entrypoint.py
USER 65532:65532
EXPOSE 8080
CMD ["python", "/app/entrypoint.py"]

FROM cloudflare AS render
COPY --from=frontend /frontend/build /app/frontend/build
COPY --from=postgres-operator-builder /postgres-operator /app/postgres-operator
COPY scripts/run_postgres_shadow.py scripts/migrate_postgres.py /app/postgres-operator/

FROM cloudflare AS qa
COPY --from=qa-tools /qa /qa
COPY --from=qa-tools /qa-tools.pth /opt/venv/lib/python3.12/site-packages/qa-tools.pth

# Render selects the SPA image through a non-secret build argument. Cloudflare
# uses the API-only default and BuildKit skips the unused frontend stages.
FROM ${IMLADRI_IMAGE} AS service
