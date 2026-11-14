FROM python:3.12.8-slim

WORKDIR /app

RUN useradd --create-home --uid 10001 app \
    && pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

USER app
EXPOSE 8081
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8081", "--no-access-log"]
