FROM python:3.12.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements-control-plane.lock ./
RUN pip install --no-cache-dir -r requirements-control-plane.lock
COPY apps ./apps
USER 65534:65534
CMD ["python", "-m", "apps.api"]
