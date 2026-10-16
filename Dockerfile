FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# 从钉死版本的 requirements.lock 安装：每次构建装到的都是同一套版本（怎么更新见 docs/dev/dependencies.md）。
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY *.py ./
COPY static/ ./static/
COPY assets/ ./assets/
COPY routers/ ./routers/
COPY core/ ./core/
COPY models/ ./models/
COPY extension/ ./extension/

RUN groupadd --gid 10001 notebook \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin notebook \
    && mkdir -p /app/data \
    && chown notebook:notebook /app/data
USER 10001:10001

VOLUME ["/app/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).close()"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
