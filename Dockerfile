FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/app/hf-cache

COPY requirements.txt .

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/hf-cache /app/work

EXPOSE 10000

CMD ["sh","-c","gunicorn --workers 1 --threads 2 --timeout 0 --bind 0.0.0.0:10000 formpilot:app"]
