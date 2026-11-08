# Swiss Student Kit: the app with its packages baked in. If a build fails, the running container is untouched.
FROM python:3.12-alpine
WORKDIR /app
COPY kit/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY kit/ kit/
COPY data/seed.json seed.json
ENV DATA_DIR=/data SEED_FILE=/app/seed.json PYTHONUNBUFFERED=1
EXPOSE 8800
HEALTHCHECK --interval=60s --timeout=5s CMD wget -qO- http://127.0.0.1:8800/health || exit 1
CMD ["python", "-m", "kit.server", "--host", "0.0.0.0", "--port", "8800"]
