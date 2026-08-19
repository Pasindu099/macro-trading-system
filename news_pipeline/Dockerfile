FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.32.0" \
    "asyncpg>=0.30.0" \
    "redis>=5.0.0" \
    "sqlalchemy[asyncio]>=2.0.36" \
    "httpx>=0.27.0" \
    "apscheduler>=3.10.4" \
    "feedparser>=6.0.11" \
    "openai>=1.0.0" \
    "python-telegram-bot>=22.0" \
    "psycopg2-binary>=2.9.9"

COPY . .

EXPOSE 8001

CMD ["uvicorn", "news_pipeline.main:app", "--host", "0.0.0.0", "--port", "8001"]
