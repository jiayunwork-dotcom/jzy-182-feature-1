# syntax=docker/dockerfile:1

# ---------- stage 1: build the React static bundle ----------
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
# build output directory is overridden to an absolute image path
RUN npx vite build --outDir /out --emptyOutDir

# ---------- stage 2: python runtime serving API + static ----------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CCPM_DB_PATH=/data/ccpm.db \
    CCPM_STATIC_DIR=/srv/static
WORKDIR /srv

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend /out ./static

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
