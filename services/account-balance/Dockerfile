FROM python:3.12.8-slim AS base

WORKDIR /app

RUN useradd --create-home --uid 10001 app \
    && pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

FROM base AS test
USER root
RUN pip install --no-cache-dir pytest==8.3.4
USER app

FROM base AS runtime
USER app
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
