# FlashML Cloud API image (private).
# Build from the Zolli-Labs workspace root (needs the sibling flashruntime
# checkout for the protocol package):
#   docker build -f flashml-cloud/apps/api/Dockerfile -t flashml/cloud-api:poc-v1 .
FROM python:3.11-slim

RUN useradd --create-home --uid 10001 flashml
WORKDIR /app

COPY flashruntime/pyproject.toml flashruntime/README.md flashruntime/LICENSE ./flashruntime/
COPY flashruntime/flashruntime ./flashruntime/flashruntime
COPY flashruntime/flashml_workloads ./flashruntime/flashml_workloads
COPY flashml-cloud/apps/api/pyproject.toml ./api/
COPY flashml-cloud/apps/api/flashml_cloud_api ./api/flashml_cloud_api

RUN pip install --no-cache-dir ./flashruntime ./api

USER flashml
EXPOSE 8000
CMD ["uvicorn", "flashml_cloud_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
