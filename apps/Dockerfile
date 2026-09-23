FROM python:3.12.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements-control-plane.lock ./
RUN pip install --no-cache-dir -r requirements-control-plane.lock
COPY apps ./apps
COPY alembic.ini ./
COPY migrations ./migrations
USER 65534:65534
CMD ["python", "-m", "apps.api"]

FROM runtime AS worker
USER root
COPY requirements-execution.lock ./
RUN pip install --no-cache-dir -r requirements-execution.lock \
    && apt-get update \
    && apt-get install -y --no-install-recommends openssh-client sshpass \
    && rm -rf /var/lib/apt/lists/*
COPY automation/ansible/status.yml ./automation/ansible/status.yml
COPY automation/ansible/managed-status.yml ./automation/ansible/managed-status.yml
COPY automation/ansible/managed-credential.yml ./automation/ansible/managed-credential.yml
COPY automation/ansible/preflight.yml ./automation/ansible/preflight.yml
COPY automation/ansible/group_vars/all.yml ./automation/ansible/group_vars/all.yml
USER 65534:65534
CMD ["python", "-m", "apps.worker"]

FROM runtime AS api
