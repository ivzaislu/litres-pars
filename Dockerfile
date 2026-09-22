FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

RUN mkdir -p /data
ENV LITRES_DB_PATH=/data/litres-aggregator.sqlite3
ENV LITRES_CACHE_TTL_SECONDS=604800

VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "litres_parser.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
