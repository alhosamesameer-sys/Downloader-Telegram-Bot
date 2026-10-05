# Telegram Social Downloader

بوت تيليجرام عربي لتحميل المحتوى العام باستخدام Python وaiogram وyt-dlp وPostgreSQL وRedis وFFmpeg.

## التشغيل
انسخ .env.example إلى .env، ضع BOT_TOKEN وADMIN_IDS، ثم شغّل PostgreSQL وRedis وFFmpeg.
نفّذ alembic upgrade head ثم شغّل python -m app.main وفي عملية ثانية python -m app.worker.

## الأوامر
/start /stats /yt /hashtag /audio /admin /users /user ID /block ID /unblock ID /message ID النص /broadcast النص

## المنصات
YouTube وTikTok وInstagram وFacebook وX/Twitter وSnapchat وLikee، حسب دعم yt-dlp للمحتوى العام.

## الأمان
لا يدعم الحسابات الخاصة أو تجاوز تسجيل الدخول أو cookies/session أو DRM. التنزيلات تعمل عبر Redis Worker مع حدود الحجم والوقت وتنظيف الملفات المؤقتة.

## CI
compileall وRuff وBlack وmypy وpytest وdocker compose config.

> اختبار Telegram الحقيقي، وقاعدة PostgreSQL وRedis وFFmpeg على بيئة النشر، يجب إجراؤها قبل اعتبار النشر Production-Ready.
