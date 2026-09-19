FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "Xvfb :99 -screen 0 1280x900x24 -ac >/tmp/xvfb.log 2>&1 & export DISPLAY=:99; python formpilot.py"]
