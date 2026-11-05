# Optional container build (Render "Docker" runtime, on-prem edge/analytics host, CI).
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY app ./app
COPY config ./config
COPY data ./data
COPY alembic ./alembic
COPY alembic.ini ./
RUN useradd --create-home --uid 10001 bf && chown -R bf /srv
USER bf
ENV DATA_DIR=/srv/data PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
