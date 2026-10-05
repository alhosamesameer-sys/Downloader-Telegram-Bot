import asyncio,json,logging,os,time
from pathlib import Path
from redis.asyncio import Redis
from aiogram import Bot
from aiogram.types import FSInputFile
from sqlalchemy import select
from app.main import S,Session,User,grab,convert

logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"))
log=logging.getLogger("worker")

async def cleanup():
    root=Path(S.temp_dir)
    if not root.exists(): return
    cutoff=time.time()-S.temp_cleanup_hours*3600
    for p in root.iterdir():
        try:
            if p.is_file() and p.stat().st_mtime<cutoff:p.unlink()
        except OSError: log.warning("cleanup failed: %s",p)

async def process(task,bot):
    src=dst=None
    try:
        src,title=await grab(task["url"])
        if os.path.getsize(src)>S.max_file_size: raise RuntimeError("حجم الملف أكبر من الحد المسموح")
        if task["type"]=="audio":
            dst=await convert(src)
            await bot.send_audio(task["chat_id"],FSInputFile(dst),caption=title[:1000])
            field="conversions"
        else:
            await bot.send_document(task["chat_id"],FSInputFile(src),caption=("✅ "+title)[:1000])
            field="downloads"
        async with Session() as db:
            u=await db.scalar(select(User).where(User.telegram_id==task["user_id"]))
            if u: setattr(u,field,getattr(u,field)+1); await db.commit()
    except Exception as e:
        log.exception("task failed")
        await bot.send_message(task["chat_id"],"❌ تعذر تنفيذ الطلب: "+str(e)[:500])
    finally:
        for p in (src,dst):
            if p:
                try: Path(p).unlink(missing_ok=True)
                except OSError: pass

async def main():
    if not S.bot_token: raise RuntimeError("BOT_TOKEN غير مضبوط")
    redis=Redis.from_url(S.redis_url,decode_responses=True)
    bot=Bot(S.bot_token); last=0
    try:
        while True:
            if time.time()-last>3600: await cleanup(); last=time.time()
            raw=await redis.brpoplpush("downloads","downloads:processing",timeout=10)
            if raw:
                try:
                    await process(json.loads(raw),bot)
                except Exception:
                    log.exception("worker loop error")
                finally:
                    await redis.lrem("downloads:processing",1,raw)
    finally:
        await bot.session.close(); await redis.aclose()

if __name__=="__main__": asyncio.run(main())
