# Deployment

## Termux
pkg update && pkg install python ffmpeg
pip install -r requirements.txt
cp .env.example .env
# PostgreSQL وRedis يمكن تشغيلهما على خادم خارجي أو عبر خدمات متوافقة.
alembic upgrade head
python -m app.main

## VPS
ثبت Python 3.12 وFFmpeg وPostgreSQL وRedis، أنشئ مستخدمًا مخصصًا للبوت، انسخ المشروع إلى /opt/social-downloader، ثم ضع .env وشغّل:
alembic upgrade head
python -m app.main

## Docker
docker compose up -d --build
docker compose logs -f bot

## أمان
لا تضع BOT_TOKEN أو YOUTUBE_API_KEY داخل Git. استخدم .env أو أسرار منصة التشغيل.
