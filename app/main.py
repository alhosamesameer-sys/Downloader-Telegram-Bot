import asyncio,logging,os,time,json,urllib.parse,urllib.request
from pathlib import Path
from urllib.parse import urlparse
from aiogram import Bot,Dispatcher,F
from aiogram.filters import Command
from aiogram.types import Message,CallbackQuery,InlineKeyboardMarkup,InlineKeyboardButton,FSInputFile
from pydantic_settings import BaseSettings,SettingsConfigDict
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column
from sqlalchemy import BigInteger,Integer,String,Boolean,DateTime,func,select
import yt_dlp

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file=".env",extra="ignore")
    bot_token:str="";database_url:str="postgresql+asyncpg://postgres:postgres@localhost:5432/social_downloader";redis_url:str="redis://localhost:6379/0"
    admin_ids:str="";youtube_api_key:str="";temp_dir:str="./temp";max_file_size:int=52428800;download_timeout:int=120;rate_limit:int=10;temp_cleanup_hours:int=6
    @property
    def admins(self):return {int(x) for x in self.admin_ids.split(",") if x.strip().isdigit()}
S=Settings()
class Base(DeclarativeBase):pass
class User(Base):
    __tablename__="users"
    id:Mapped[int]=mapped_column(primary_key=True);telegram_id:Mapped[int]=mapped_column(BigInteger,unique=True,index=True)
    username:Mapped[str|None]=mapped_column(String(255));first_name:Mapped[str|None]=mapped_column(String(255))
    created_at:Mapped[object]=mapped_column(DateTime(timezone=True),server_default=func.now())
    downloads:Mapped[int]=mapped_column(Integer,default=0);searches:Mapped[int]=mapped_column(Integer,default=0);conversions:Mapped[int]=mapped_column(Integer,default=0);blocked:Mapped[bool]=mapped_column(Boolean,default=False)
engine=create_async_engine(S.database_url,pool_pre_ping=True);Session=async_sessionmaker(engine,class_=AsyncSession,expire_on_commit=False)
DOM={"youtube.com":"YouTube","youtu.be":"YouTube","tiktok.com":"TikTok","instagram.com":"Instagram","facebook.com":"Facebook","twitter.com":"X/Twitter","x.com":"X/Twitter","snapchat.com":"Snapchat","likee.video":"Likee"}
def valid_url(u):
    try:
        p=urlparse(u)
        return p.scheme in {"http","https"} and bool(p.netloc)
    except ValueError:
        return False

def platform(u):
    try:h=(urlparse(u).hostname or "").lower().removeprefix("www.")
    except ValueError:return None
    return next((n for d,n in DOM.items() if h==d or h.endswith("."+d)),None)
async def user(db,u):
    x=await db.scalar(select(User).where(User.telegram_id==u.id))
    if not x:x=User(telegram_id=u.id,username=u.username,first_name=u.first_name);db.add(x)
    await db.commit();await db.refresh(x);return x
async def grab(url):
    Path(S.temp_dir).mkdir(parents=True,exist_ok=True)
    def run():
        with yt_dlp.YoutubeDL({"quiet":True,"noplaylist":True,"outtmpl":f"{S.temp_dir}/%(id)s.%(ext)s","max_filesize":S.max_file_size}) as y:
            i=y.extract_info(url,download=True);return y.prepare_filename(i),str(i.get("title") or "وسائط")
    return await asyncio.wait_for(asyncio.to_thread(run),S.download_timeout)
async def convert(src):
    dst=str(Path(S.temp_dir)/(Path(src).stem+".mp3"))
    p=await asyncio.create_subprocess_exec("ffmpeg","-y","-i",src,"-vn","-codec:a","libmp3lame","-q:a","2",dst,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
    _,err=await p.communicate()
    if p.returncode:raise RuntimeError(err.decode(errors="ignore")[-1000:])
    return dst
async def yt_search(q):
    if not S.youtube_api_key:raise RuntimeError("ضع YOUTUBE_API_KEY لاستخدام بحث YouTube.")
    params=urllib.parse.urlencode({"part":"snippet","q":q,"type":"video","maxResults":5,"key":S.youtube_api_key})
    def run():
        with urllib.request.urlopen("https://www.googleapis.com/youtube/v3/search?"+params,timeout=15) as r:return json.loads(r.read())
    data=await asyncio.to_thread(run)
    return [(x["snippet"]["title"],"https://youtube.com/watch?v="+x["id"]["videoId"]) for x in data.get("items",[])]

async def enqueue(task:dict)->None:
    redis=Redis.from_url(S.redis_url,decode_responses=True)
    try: await redis.rpush("downloads",json.dumps(task,ensure_ascii=False))
    finally: await redis.aclose()

def user_kb(is_admin=False):
    rows=[
        [InlineKeyboardButton(text="📥 تحميل فيديو",callback_data="download"),InlineKeyboardButton(text="🎵 تحويل إلى صوت",callback_data="audio")],
        [InlineKeyboardButton(text="🔎 بحث YouTube",callback_data="search"),InlineKeyboardButton(text="🏷️ بحث هاشتاغ",callback_data="hashtag")],
        [InlineKeyboardButton(text="📊 حسابي",callback_data="stats"),InlineKeyboardButton(text="ℹ️ المساعدة",callback_data="help")],
    ]
    if is_admin: rows.append([InlineKeyboardButton(text="🛠️ لوحة الإدارة",callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 الإحصائيات",callback_data="admin_stats"),InlineKeyboardButton(text="👥 المستخدمون",callback_data="admin_users")],
        [InlineKeyboardButton(text="📢 إذاعة رسالة",callback_data="admin_broadcast"),InlineKeyboardButton(text="💬 رسالة خاصة",callback_data="admin_message")],
        [InlineKeyboardButton(text="🚫 حظر مستخدم",callback_data="admin_block"),InlineKeyboardButton(text="✅ إلغاء الحظر",callback_data="admin_unblock")],
        [InlineKeyboardButton(text="🏠 لوحة المستخدم",callback_data="user_panel"),InlineKeyboardButton(text="❌ إغلاق",callback_data="admin_close")],
    ])

def back_admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ العودة للوحة الإدارة",callback_data="admin_panel")]])

WELCOME="""👋 أهلاً بك في SamirNet

بوت تحميل ومعالجة الوسائط من الروابط العامة بسهولة.

✨ ماذا يمكنك أن تفعل؟
• 📥 تحميل فيديو من المنصات المدعومة
• 🎵 تحويل الفيديو إلى ملف صوتي MP3
• 🔎 البحث عن فيديوهات YouTube
• 🏷️ البحث بالهاشتاغ
• 📊 متابعة إحصائيات حسابك

🌐 المنصات المدعومة:
YouTube • TikTok • Instagram • Facebook • X • Snapchat • Likee

📌 أرسل رابط فيديو عام أو اختر الخدمة من الأزرار بالأسفل.

⚠️ لا يدعم البوت المحتوى الخاص أو تجاوز تسجيل الدخول أو DRM."""
async def main():
    if not S.bot_token:raise RuntimeError("BOT_TOKEN غير مضبوط")
    bot=Bot(S.bot_token);dp=Dispatcher();hits={};pending_admin={}
    async def allowed(m):
        if not m.from_user:return False
        now=time.monotonic();q=hits.setdefault(m.from_user.id,[]);q[:]=[x for x in q if now-x<60]
        if len(q)>=S.rate_limit:return False
        q.append(now);return True
    async def show_admin(m):
        await m.answer("🛠️ لوحة الإدارة\n\nاختر العملية التي تريد تنفيذها:",reply_markup=admin_kb())

    @dp.message(Command("start"))
    async def start(m):
        pending_admin.pop(m.from_user.id,None)
        async with Session() as db:
            u=await user(db,m.from_user)
            if u.blocked:return await m.answer("🚫 حسابك محظور من استخدام البوت.")
        await m.answer(WELCOME,reply_markup=user_kb(m.from_user.id in S.admins))
    @dp.message(Command("stats"))
    async def stats(m):
        if not await allowed(m):return
        async with Session() as db:u=await user(db,m.from_user)
        await m.answer(f"👤 حسابي\n\n🆔 {u.telegram_id}\n📥 التنزيلات: {u.downloads}\n🔎 عمليات البحث: {u.searches}\n🎵 التحويلات: {u.conversions}",reply_markup=user_kb(m.from_user.id in S.admins))
    @dp.message(Command("yt"))
    async def yt(m):
        q=(m.text or "").partition(" ")[2].strip()
        if not q:return await m.answer("الاستخدام: /yt اسم الفيديو")
        try:
            rows=await yt_search(q)
            async with Session() as db:u=await user(db,m.from_user);u.searches+=1;await db.commit()
            await m.answer("\n".join(f"• {t}\n{u}" for t,u in rows) or "لا توجد نتائج.")
        except Exception as e:await m.answer("⚠️ "+str(e)[:400])
    @dp.message(Command("audio"))
    async def audio(m):
        url=(m.text or "").partition(" ")[2].strip()
        if not url or not platform(url):return await m.answer("الاستخدام: /audio رابط_فيديو_عام")
        src=dst=None
        try:
            await enqueue({"type":"audio","url":url,"chat_id":m.chat.id,"user_id":m.from_user.id})
            await m.answer("🟡 تمت إضافة التحويل إلى قائمة الانتظار.")
        except Exception as e:await m.answer("❌ فشل التحويل: "+str(e)[:400])
        finally:
            for p in (src,dst):
                if p and os.path.exists(p):
                    try:os.remove(p)
                    except OSError:pass
    @dp.message(Command("hashtag"))
    async def hashtag(m):
        raw=(m.text or "").partition(" ")[2].strip().lstrip("#").split()[0] if (m.text or "").partition(" ")[2].strip() else ""
        if not raw or len(raw)>80 or not raw.replace("_","").isalnum(): return await m.answer("الاستخدام: /hashtag اسم_الهاشتاغ")
        try:
            rows=await yt_search("#"+raw)
            async with Session() as db: x=await user(db,m.from_user);x.searches+=1;await db.commit()
            await m.answer("\n\n".join(f"• {t}\n{u}" for t,u in rows) or "لا توجد نتائج.")
        except Exception as e: await m.answer("⚠️ "+str(e)[:300])

    @dp.message(Command("users"))
    async def users(m):
        if m.from_user.id not in S.admins:return
        async with Session() as db:
            total=await db.scalar(select(func.count(User.id))) or 0
            blocked=await db.scalar(select(func.count(User.id)).where(User.blocked==True)) or 0
            downloads=await db.scalar(select(func.sum(User.downloads))) or 0
        await m.answer(f"📊 المستخدمون: {total}\n🚫 المحظورون: {blocked}\n📥 التنزيلات: {downloads}")

    @dp.message(Command("user"))
    async def user_info(m):
        if m.from_user.id not in S.admins:return
        p=(m.text or "").split()
        if len(p)!=2 or not p[1].isdigit():return await m.answer("/user ID")
        async with Session() as db:x=await db.scalar(select(User).where(User.telegram_id==int(p[1])))
        if not x:return await m.answer("المستخدم غير موجود.")
        await m.answer(f"👤 {x.first_name or '-'}\nID: {x.telegram_id}\n@{x.username or '-'}\n📥 {x.downloads} | 🔎 {x.searches} | 🎵 {x.conversions}\n🚫 {'نعم' if x.blocked else 'لا'}")

    @dp.message(Command("message"))
    async def private_message(m):
        if m.from_user.id not in S.admins:return
        p=(m.text or "").split(maxsplit=2)
        if len(p)<3 or not p[1].isdigit():return await m.answer("/message ID النص")
        try: await bot.send_message(int(p[1]),p[2]);await m.answer("✅ تم إرسال الرسالة.")
        except Exception as e: await m.answer("❌ "+str(e)[:300])

    @dp.message(Command("admin"))
    async def admin(m):
        if m.from_user.id in S.admins:await show_admin(m)
    @dp.message(Command("block"))
    async def block(m):
        if m.from_user.id not in S.admins:return
        p=(m.text or "").split()
        if len(p)!=2 or not p[1].isdigit():return await m.answer("/block ID")
        async with Session() as db:x=await db.scalar(select(User).where(User.telegram_id==int(p[1])));x.blocked=True if x else False;await db.commit()
        await m.answer("تم الحظر." if x else "غير موجود.",reply_markup=admin_kb())
    @dp.message(Command("unblock"))
    async def unblock(m):
        if m.from_user.id not in S.admins:return
        p=(m.text or "").split()
        if len(p)!=2 or not p[1].isdigit():return await m.answer("/unblock ID")
        async with Session() as db:x=await db.scalar(select(User).where(User.telegram_id==int(p[1])));x.blocked=False if x else False;await db.commit()
        await m.answer("تم إلغاء الحظر." if x else "غير موجود.",reply_markup=admin_kb())
    @dp.message(Command("broadcast"))
    async def broadcast(m):
        if m.from_user.id not in S.admins:return
        text=m.text.partition(" ")[2].strip()
        if not text:return await m.answer("/broadcast النص")
        async with Session() as db:users=list((await db.scalars(select(User).where(User.blocked==False))).all())
        sent=failed=0
        for u in users:
            try:await bot.send_message(u.telegram_id,text);sent+=1
            except Exception:failed+=1
            await asyncio.sleep(.05)
        await m.answer(f"📣 تم الإرسال: {sent}\n❌ فشل: {failed}",reply_markup=admin_kb())
    @dp.callback_query(F.data=="stats")
    async def stats_cb(c):await c.answer();await stats(c.message)

    @dp.callback_query(F.data=="help")
    async def help_cb(c):
        await c.answer()
        await c.message.answer("ℹ️ المساعدة\n\n📥 أرسل رابط فيديو عام لتحميله.\n🎵 استخدم تحويل إلى صوت لإخراج MP3.\n🔎 للبحث: /yt كلمة\n🏷️ للهاشتاغ: /hashtag اسم_الهاشتاغ\n\n⚠️ المحتوى الخاص وتجاوز تسجيل الدخول وDRM غير مدعوم.",reply_markup=user_kb(c.from_user.id in S.admins))

    @dp.callback_query(F.data=="audio")
    async def audio_cb(c):await c.answer();await c.message.answer("🎵 أرسل الآن رابط فيديو عام لتحويله إلى MP3.")

    @dp.callback_query(F.data=="download")
    async def download_cb(c):await c.answer();await c.message.answer("📥 أرسل الآن رابط الفيديو العام مباشرة، وسأضيفه إلى قائمة الانتظار.")

    @dp.callback_query(F.data=="search")
    async def search_cb(c):await c.answer();await c.message.answer("🔎 أرسل أمر البحث بهذا الشكل:\n/yt اسم الفيديو")

    @dp.callback_query(F.data=="hashtag")
    async def hashtag_cb(c):await c.answer();await c.message.answer("🏷️ أرسل الهاشتاغ بهذا الشكل:\n/hashtag اسم_الهاشتاغ")

    @dp.callback_query(F.data=="admin_panel")
    async def admin_panel_cb(c):
        await c.answer()
        if c.from_user.id in S.admins:await c.message.answer("🛠️ لوحة الإدارة\n\nاختر العملية:",reply_markup=admin_kb())

    @dp.callback_query(F.data=="admin_stats")
    async def admin_stats_cb(c):
        await c.answer()
        if c.from_user.id not in S.admins:return
        async with Session() as db:
            total=await db.scalar(select(func.count(User.id))) or 0
            blocked=await db.scalar(select(func.count(User.id)).where(User.blocked==True)) or 0
            downloads=await db.scalar(select(func.sum(User.downloads))) or 0
            searches=await db.scalar(select(func.sum(User.searches))) or 0
            conversions=await db.scalar(select(func.sum(User.conversions))) or 0
        await c.message.answer(f"📊 إحصائيات البوت\n\n👥 المستخدمون: {total}\n🚫 المحظورون: {blocked}\n📥 التنزيلات: {downloads}\n🔎 عمليات البحث: {searches}\n🎵 التحويلات: {conversions}",reply_markup=back_admin_kb())

    @dp.callback_query(F.data=="admin_users")
    async def admin_users_cb(c):
        await c.answer()
        if c.from_user.id not in S.admins:return
        async with Session() as db:users=list((await db.scalars(select(User).order_by(User.id.desc()).limit(20))).all())
        text="👥 لا يوجد مستخدمون بعد." if not users else "👥 آخر المستخدمين:\n\n"+"\n".join(f"• {u.telegram_id} — {u.first_name or '-'} — 📥 {u.downloads}" for u in users)
        await c.message.answer(text,reply_markup=back_admin_kb())

    async def admin_prompt(c,action,prompt):
        if c.from_user.id not in S.admins:return
        pending_admin[c.from_user.id]=action
        await c.answer()
        await c.message.answer(prompt,reply_markup=back_admin_kb())

    @dp.callback_query(F.data=="admin_block")
    async def admin_block_cb(c):await admin_prompt(c,"block","🚫 أرسل الآن Telegram ID للمستخدم الذي تريد حظره.")

    @dp.callback_query(F.data=="admin_unblock")
    async def admin_unblock_cb(c):await admin_prompt(c,"unblock","✅ أرسل الآن Telegram ID للمستخدم الذي تريد إلغاء حظره.")

    @dp.callback_query(F.data=="admin_broadcast")
    async def admin_broadcast_cb(c):await admin_prompt(c,"broadcast","📢 أرسل الآن نص الرسالة التي تريد إرسالها لجميع المستخدمين غير المحظورين.")

    @dp.callback_query(F.data=="admin_message")
    async def admin_message_cb(c):await admin_prompt(c,"message","💬 أرسل بهذا الشكل:\nID النص\nمثال: 123456789 مرحباً بك.")

    @dp.callback_query(F.data=="user_panel")
    async def user_panel_cb(c):
        pending_admin.pop(c.from_user.id,None)
        await c.answer()
        await c.message.answer(WELCOME,reply_markup=user_kb(c.from_user.id in S.admins))

    @dp.callback_query(F.data=="admin_close")
    async def admin_close_cb(c):
        pending_admin.pop(c.from_user.id,None)
        await c.answer()
        await c.message.delete()

    @dp.callback_query(F.data=="admin_cancel")
    async def admin_cancel_cb(c):
        pending_admin.pop(c.from_user.id,None)
        await c.answer()
        await c.message.answer("تم إلغاء العملية.",reply_markup=admin_kb())
    @dp.message()
    async def media(m):
        if not m.from_user:return
        action=pending_admin.get(m.from_user.id)
        if action and m.from_user.id in S.admins:
            text=(m.text or "").strip()
            if action in {"block","unblock"}:
                if not text.isdigit():return await m.answer("⚠️ أرسل Telegram ID رقميًا فقط.",reply_markup=back_admin_kb())
                async with Session() as db:
                    x=await db.scalar(select(User).where(User.telegram_id==int(text)))
                    if x:
                        x.blocked=action=="block"
                        await db.commit()
                pending_admin.pop(m.from_user.id,None)
                return await m.answer(("🚫 تم حظر المستخدم." if action=="block" else "✅ تم إلغاء حظر المستخدم.") if x else "⚠️ المستخدم غير موجود.",reply_markup=admin_kb())
            if action=="message":
                parts=text.split(maxsplit=1)
                if len(parts)<2 or not parts[0].isdigit():return await m.answer("⚠️ الصيغة الصحيحة: ID النص",reply_markup=back_admin_kb())
                try:
                    await bot.send_message(int(parts[0]),parts[1]);reply="✅ تم إرسال الرسالة بنجاح."
                except Exception as e:reply="❌ فشل الإرسال: "+str(e)[:300]
                pending_admin.pop(m.from_user.id,None)
                return await m.answer(reply,reply_markup=admin_kb())
            if action=="broadcast":
                if not text:return await m.answer("⚠️ أرسل نص الرسالة.",reply_markup=back_admin_kb())
                async with Session() as db:users=list((await db.scalars(select(User).where(User.blocked==False))).all())
                sent=failed=0
                for u in users:
                    try:await bot.send_message(u.telegram_id,text);sent+=1
                    except Exception:failed+=1
                    await asyncio.sleep(.05)
                pending_admin.pop(m.from_user.id,None)
                return await m.answer(f"📢 تم الإرسال: {sent}\n❌ فشل: {failed}",reply_markup=admin_kb())
        if not await allowed(m):return
        u=(m.text or "").strip();name=platform(u)
        if not name:return
        async with Session() as db:
            x=await user(db,m.from_user)
            if x.blocked:return
        await enqueue({"type":"download","url":u,"chat_id":m.chat.id,"user_id":m.from_user.id})
        await m.answer(f"🟡 {name}\nتمت إضافة الرابط إلى قائمة الانتظار.")
    await dp.start_polling(bot)
if __name__=="__main__":asyncio.run(main())
