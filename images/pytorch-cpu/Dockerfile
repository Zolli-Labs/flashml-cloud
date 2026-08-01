# Curated image: pytorch-cpu
#
# Alias resolved in apps/api/flashml_cloud_api/images.py -> CURATED["pytorch-cpu"].
# packages = stdlib + {torch, numpy}. The upstream pytorch/pytorch:2.3.1-cpu
# base already provides both — this Dockerfile's job is not to add
# packages but to add the non-root USER the upstream image does not
# declare (it runs as root by default).
#
# SECURITY (load-bearing — see ../README.md and
# flashml-cloud/docs/superpowers/plans/2026-08-01-windows-hosts.md):
# the final USER here is what lets flashnode omit `--user uid:gid` on
# Windows hosts. Do not add a later `USER root`, and keep FLASHML_UID equal
# to the other two curated images (python-slim, sklearn) so /work ownership
# is predictable regardless of which image a task uses.
FROM docker.io/pytorch/pytorch:2.3.1-cpu

ARG FLASHML_UID=10001
ARG FLASHML_GID=10001

RUN groupadd --gid "${FLASHML_GID}" flashml \
    && useradd --uid "${FLASHML_UID}" --gid "${FLASHML_GID}" \
       --no-create-home --shell /usr/sbin/nologin flashml \
    && mkdir -p /work \
    && chown "${FLASHML_UID}:${FLASHML_GID}" /work

WORKDIR /work

# Fixed, non-"nobody" uid, matching python-slim and sklearn.
USER 10001:10001
