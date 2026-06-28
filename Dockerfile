FROM python:3.12-slim

# Tidssone: korrekt dato, værvinduer og 05:00-tolkning (se MIGRERINGSPLAN.md 1.1 E)
ENV TZ=Europe/Oslo \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    BRIEFING_DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data && chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
