# NextStep (multi-user portal) container image.
# Build context is the repo root; secrets are never baked in (see .dockerignore).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY fonts ./fonts
COPY multiuser ./multiuser

# Run as a normal user, not root.
RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000
# One worker: the server is stateless and the droplet has little memory.
# --proxy-headers so client IPs and https come through Caddy.
CMD ["uvicorn", "multiuser.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
