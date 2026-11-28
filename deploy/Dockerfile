# syntax=docker/dockerfile:1
# Every IntelliOps image. Pick one with --target and --build-arg SERVICE:
#
#   --target service --build-arg SERVICE=ingestion   (any platform service)
#   --target rca     --build-arg SERVICE=rca         (carries the embedding model)
#   --target sim     --build-arg SERVICE=sim         (demo app and Meridian)
#
# Builds natively for the target platform, so arm64 hosts build arm64 images.

# One compile of every binary, shared by all the images: this stage does not
# depend on SERVICE, so BuildKit runs it once however many are built together.
FROM rust:1.100-slim-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates ./crates
RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,target=/src/target,sharing=locked \
    cargo build --release --locked --bins \
    && mkdir /out \
    && find target/release -maxdepth 1 -type f -executable -name 'intelliops-*' -exec cp {} /out/ \; \
    && mkdir -p /out/data/rca_context

# The embedding model rca ranks with, at a pinned revision, verified.
FROM scratch AS model
ARG REVISION=1110a243fdf4706b3f48f1d95db1a4f5529b4d41
ARG BASE=https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve
ADD --chmod=644 --checksum=sha256:953f9c0d463486b10a6871cc2fd59f223b2c70184f49815e7efbcab5d8908b41 \
    ${BASE}/${REVISION}/config.json /minilm/config.json
ADD --chmod=644 --checksum=sha256:be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037 \
    ${BASE}/${REVISION}/tokenizer.json /minilm/tokenizer.json
ADD --chmod=644 --checksum=sha256:53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db \
    ${BASE}/${REVISION}/model.safetensors /minilm/model.safetensors

# The Meridian ops panel, served by the gateway.
FROM node:26-slim AS meridian-ui
WORKDIR /ui
COPY meridian-ui/package*.json ./
RUN npm ci
COPY meridian-ui/ ./
RUN npm run build

# No shell or package manager: the binary is its own healthcheck.
FROM gcr.io/distroless/cc-debian12:nonroot AS service
ARG SERVICE
COPY --from=build /out/intelliops-${SERVICE} /usr/local/bin/service
WORKDIR /app
# A fresh named volume inherits this owner (the Meridian gateway writes deploys).
COPY --from=build --chown=10001:10001 /out/data /app/data
# Seed playbooks, RBAC policy and system context, at the paths services default to.
COPY deploy/playbooks /app/data/playbooks
COPY policies /app/policies
COPY config /app/config
USER 10001
ENV PORT=8000
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/service"]

# rca carries the model; every other service uses the `service` target.
FROM service AS rca
COPY --from=model /minilm /app/models/minilm

# One binary plays the demo app and all four Meridian services.
FROM service AS sim
COPY --from=meridian-ui /ui/dist /app/ui
