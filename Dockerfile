# صورة تشغيل البوت على أي سيرفر/استضافة بتدعم Docker (VPS, Railway, Fly.io, Render...).
#   docker build -t feed-bot .                      # بدون ميزة السلالة: --build-arg WITH_BREED=0 (أخف وأسرع)
#   docker run -d --restart unless-stopped --env-file .env -v feedbot-data:/data feed-bot
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 DATA_FILE=/data/bot_data.json
RUN apt-get update && apt-get install -y --no-install-recommends openssl libreoffice-calc && rm -rf /var/lib/apt/lists/*
WORKDIR /app
EXPOSE 8080
COPY requirements.txt requirements-cattle.txt ./
ARG WITH_BREED=1
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$WITH_BREED" = "1" ]; then pip install --no-cache-dir -r requirements-cattle.txt; fi
COPY . .
VOLUME /data
CMD ["./entrypoint.sh"]
