# Telegram Social Downloader

بوت تيليجرام عربي لتحميل المحتوى العام باستخدام Python وaiogram وyt-dlp وPostgreSQL وRedis وFFmpeg.

## التشغيل
انسخ .env.example إلى .env، ضع BOT_TOKEN، شغّل PostgreSQL وRedis، ثم:
alembic upgrade head
python -m app.main

## Docker
docker compose up --build

## المنصات
YouTube وTikTok وInstagram وFacebook وX/Twitter وSnapchat وLikee عند دعم المحتوى العام من yt-dlp.

لا يدعم المشروع الحسابات الخاصة أو تجاوز تسجيل الدخول أو cookies/session أو DRM.

هذه النسخة لا تُسمى Production-Ready قبل نجاح CI واختبار Telegram وPostgreSQL وRedis وFFmpeg فعليًا.
