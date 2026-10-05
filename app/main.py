import asyncio,logging,os,time,json,urllib.parse,urllib.request
from uuid import uuid4

log=logging.getLogger(__name__)
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
        opts={
            "quiet":True,
            "noplaylist":True,
            "outtmpl":f"{S.temp_dir}/%(id)s.%(ext)s",
            "max_filesize":S.max_file_size,
            "format":"bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
            "merge_output_format":"mp4",
            "retries":2,
            "fragment_retries":2,
            "socket_timeout":20,
        }
        with yt_dlp.YoutubeDL(opts) as y:
            i=y.extract_info(url,download=True)
            video_id=str(i.get("id") or "")
            candidates=[
                p for p in Path(S.temp_dir).glob(f"{video_id}.*")
                if p.is_file() and not p.name.endswith((".part",".ytdl"))
            ]
            if not candidates:
                prepared=Path(y.prepare_filename(i))
                if prepared.exists(): candidates=[prepared]
            if not candidates: raise FileNotFoundError("تعذر العثور على الملف بعد اكتمال التحميل.")
            src=max(candidates,key=lambda p:p.stat().st_size)
            return str(src),str(i.get("title") or "وسائط")
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

async def store_media_url(url:str)->str:
    token=uuid4().hex[:16]
    redis=Redis.from_url(S.redis_url,decode_responses=True)
    try: await redis.setex(f"media:{token}",3600,url)
    finally: await redis.aclose()
    return token

async def get_media_url(token:str)->str|None:
    if not token or len(token)>32 or not token.isalnum(): return None
    redis=Redis.from_url(S.redis_url,decode_responses=True)
    try: return await redis.get(f"media:{token}")
    finally: await redis.aclose()

def media_kb(token:str,url:str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎵 تحويل إلى MP3",callback_data=f"media_audio:{token}")],
        [InlineKeyboardButton(text="🔄 تحميل مرة أخرى",callback_data=f"media_redownload:{token}"),
         InlineKeyboardButton(text="🔗 فتح الرابط",url=url)],
        [InlineKeyboardButton(text="🗑️ حذف الرسالة",callback_data=f"media_delete:{token}")],
    ])

async def search_kb(rows):
    buttons=[]
    for title,url in rows:
        token=await store_media_url(url)
        buttons.append([InlineKeyboardButton(text=f"📥 {title[:45]}",callback_data=f"media_redownload:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

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

def back_user_kb(is_admin=False):
    rows=[[InlineKeyboardButton(text="🏠 القائمة الرئيسية",callback_data="user_panel")]]
    if is_admin: rows.append([InlineKeyboardButton(text="🛠️ لوحة الإدارة",callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

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
    bot=Bot(S.bot_token);dp=Dispatcher();hits={};pending_admin={};pending_user={}
    async def allowed(m):
        if not m.from_user:return False
        now=time.monotonic();q=hits.setdefault(m.from_user.id,[]);q[:]=[x for x in q if now-x<60]
        if len(q)>=S.rate_limit:return False
        q.append(now);return True
    async def show_admin(m):
        await m.answer("🛠️ لوحة الإدارة\n\nاختر العملية التي تريد تنفيذها:",reply_markup=admin_kb())

    @dp.message(Command("start"))
    async def start(m):
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
        async with Session() as db:
            u=await user(db,m.from_user)
            if u.blocked:return await m.answer("🚫 حسابك محظور من استخدام البوت.")
        await m.answer(WELCOME,reply_markup=user_kb(m.from_user.id in S.admins))
    @dp.message(Command("stats"))
    async def stats(m):
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
        if not await allowed(m):return
        async with Session() as db:u=await user(db,m.from_user)
        await m.answer(f"👤 حسابي\n\n🆔 {u.telegram_id}\n📥 التنزيلات: {u.downloads}\n🔎 عمليات البحث: {u.searches}\n🎵 التحويلات: {u.conversions}",reply_markup=user_kb(m.from_user.id in S.admins))
    @dp.message(Command("yt"))
    async def yt(m):
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
        q=(m.text or "").partition(" ")[2].strip()
        if not q:return await m.answer("الاستخدام: /yt اسم الفيديو")
        try:
            rows=await yt_search(q)
            async with Session() as db:u=await user(db,m.from_user);u.searches+=1;await db.commit()
            await m.answer("\n".join(f"• {t}\n{u}" for t,u in rows) or "لا توجد نتائج.")
        except Exception as e:await m.answer("⚠️ "+str(e)[:400])
    @dp.message(Command("audio"))
    async def audio(m):
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
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
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
        raw=(m.text or "").partition(" ")[2].strip().lstrip("#").split()[0] if (m.text or "").partition(" ")[2].strip() else ""
        if not raw or len(raw)>80 or not raw.replace("_","").isalnum(): return await m.answer("الاستخدام: /hashtag اسم_الهاشتاغ")
        try:
            rows=await yt_search("#"+raw)
            async with Session() as db: x=await user(db,m.from_user);x.searches+=1;await db.commit()
            await m.answer("\n\n".join(f"• {t}\n{u}" for t,u in rows) or "لا توجد نتائج.")
        except Exception as e: await m.answer("⚠️ "+str(e)[:300])

    @dp.message(Command("users"))
    async def users(m):
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
        if m.from_user.id not in S.admins:return
        async with Session() as db:
            total=await db.scalar(select(func.count(User.id))) or 0
            blocked=await db.scalar(select(func.count(User.id)).where(User.blocked==True)) or 0
            downloads=await db.scalar(select(func.sum(User.downloads))) or 0
        await m.answer(f"📊 المستخدمون: {total}\n🚫 المحظورون: {blocked}\n📥 التنزيلات: {downloads}")

    @dp.message(Command("user"))
    async def user_info(m):
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
        if m.from_user.id not in S.admins:return
        p=(m.text or "").split()
        if len(p)!=2 or not p[1].isdigit():return await m.answer("/user ID")
        async with Session() as db:x=await db.scalar(select(User).where(User.telegram_id==int(p[1])))
        if not x:return await m.answer("المستخدم غير موجود.")
        await m.answer(f"👤 {x.first_name or '-'}\nID: {x.telegram_id}\n@{x.username or '-'}\n📥 {x.downloads} | 🔎 {x.searches} | 🎵 {x.conversions}\n🚫 {'نعم' if x.blocked else 'لا'}")

    @dp.message(Command("message"))
    async def private_message(m):
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
        if m.from_user.id not in S.admins:return
        p=(m.text or "").split(maxsplit=2)
        if len(p)<3 or not p[1].isdigit():return await m.answer("/message ID النص")
        try: await bot.send_message(int(p[1]),p[2]);await m.answer("✅ تم إرسال الرسالة.")
        except Exception as e: await m.answer("❌ "+str(e)[:300])

    @dp.message(Command("admin"))
    async def admin(m):
        if m.from_user.id in S.admins:
            pending_user.pop(m.from_user.id,None)
            pending_admin.pop(m.from_user.id,None)
            await show_admin(m)
    @dp.message(Command("block"))
    async def block(m):
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
        if m.from_user.id not in S.admins:return
        p=(m.text or "").split()
        if len(p)!=2 or not p[1].isdigit():return await m.answer("/block ID")
        async with Session() as db:x=await db.scalar(select(User).where(User.telegram_id==int(p[1])));x.blocked=True if x else False;await db.commit()
        await m.answer("تم الحظر." if x else "غير موجود.",reply_markup=admin_kb())
    @dp.message(Command("unblock"))
    async def unblock(m):
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
        if m.from_user.id not in S.admins:return
        p=(m.text or "").split()
        if len(p)!=2 or not p[1].isdigit():return await m.answer("/unblock ID")
        async with Session() as db:x=await db.scalar(select(User).where(User.telegram_id==int(p[1])));x.blocked=False if x else False;await db.commit()
        await m.answer("تم إلغاء الحظر." if x else "غير موجود.",reply_markup=admin_kb())
    @dp.message(Command("broadcast"))
    async def broadcast(m):
        pending_admin.pop(m.from_user.id,None);pending_user.pop(m.from_user.id,None)
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
    async def stats_cb(c):
        await c.answer()
        async with Session() as db:
            u=await user(db,c.from_user)
            if u.blocked:return await c.message.answer("🚫 حسابك محظور من استخدام البوت.")
        await c.message.answer(
            f"👤 حسابي\n\n🆔 {u.telegram_id}\n📥 التنزيلات: {u.downloads}\n🔎 عمليات البحث: {u.searches}\n🎵 التحويلات: {u.conversions}",
            reply_markup=user_kb(c.from_user.id in S.admins),
        )

    @dp.callback_query(F.data=="help")
    async def help_cb(c):
        await c.answer()
        await c.message.answer("ℹ️ المساعدة\n\n📥 أرسل رابط فيديو عام لتحميله.\n🎵 استخدم تحويل إلى صوت لإخراج MP3.\n🔎 للبحث: /yt كلمة\n🏷️ للهاشتاغ: /hashtag اسم_الهاشتاغ\n\n⚠️ المحتوى الخاص وتجاوز تسجيل الدخول وDRM غير مدعوم.",reply_markup=user_kb(c.from_user.id in S.admins))

    @dp.callback_query(F.data=="audio")
    async def audio_cb(c):
        await c.answer()
        pending_user[c.from_user.id]="audio"
        await c.message.answer("🎵 أرسل الآن رابط الفيديو العام لتحويله إلى MP3.",reply_markup=back_user_kb(c.from_user.id in S.admins))

    @dp.callback_query(F.data=="download")
    async def download_cb(c):
        await c.answer()
        pending_user[c.from_user.id]="download"
        await c.message.answer("📥 أرسل الآن رابط الفيديو العام.",reply_markup=back_user_kb(c.from_user.id in S.admins))

    @dp.callback_query(F.data=="search")
    async def search_cb(c):
        await c.answer()
        pending_user[c.from_user.id]="search"
        await c.message.answer("🔎 أرسل الآن كلمة البحث عن فيديو في YouTube.",reply_markup=back_user_kb(c.from_user.id in S.admins))

    @dp.callback_query(F.data=="hashtag")
    async def hashtag_cb(c):
        await c.answer()
        pending_user[c.from_user.id]="hashtag"
        await c.message.answer("🏷️ أرسل اسم الهاشتاغ بدون #.",reply_markup=back_user_kb(c.from_user.id in S.admins))

    @dp.callback_query(F.data.startswith("media_audio:"))
    async def media_audio_cb(c):
        await c.answer("جاري إضافة التحويل...")
        token=c.data.split(":",1)[1]
        url=await get_media_url(token)
        if not url:return await c.message.answer("⚠️ انتهت صلاحية هذا الزر، أرسل الرابط من جديد.")
        await enqueue({"type":"audio","url":url,"chat_id":c.message.chat.id,"user_id":c.from_user.id})
        await c.message.answer("🟡 تمت إضافة تحويل MP3 إلى قائمة الانتظار.")

    @dp.callback_query(F.data.startswith("media_redownload:"))
    async def media_redownload_cb(c):
        await c.answer("جاري إضافة التحميل...")
        token=c.data.split(":",1)[1]
        url=await get_media_url(token)
        if not url:return await c.message.answer("⚠️ انتهت صلاحية هذا الزر، أرسل الرابط من جديد.")
        await enqueue({"type":"download","url":url,"chat_id":c.message.chat.id,"user_id":c.from_user.id})
        await c.message.answer("🟡 تمت إعادة إضافة الرابط إلى قائمة الانتظار.")

    @dp.callback_query(F.data.startswith("media_delete:"))
    async def media_delete_cb(c):
        await c.answer()
        try: await c.message.delete()
        except Exception as e: log.debug("message deletion failed: %s",e)

    @dp.callback_query(F.data=="admin_panel")
    async def admin_panel_cb(c):
        await c.answer()
        if c.from_user.id in S.admins:
            pending_user.pop(c.from_user.id,None)
            await c.message.answer("🛠️ لوحة الإدارة\n\nاختر العملية:",reply_markup=admin_kb())

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
        pending_user.pop(c.from_user.id,None)
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

    @dp.callback_query(F.data=="user_cancel")
    async def user_cancel_cb(c):
        pending_user.pop(c.from_user.id,None)
        await c.answer()
        await c.message.answer("تم إلغاء العملية.",reply_markup=user_kb(c.from_user.id in S.admins))

    @dp.callback_query(F.data=="user_panel")
    async def user_panel_cb(c):
        pending_admin.pop(c.from_user.id,None)
        await c.answer()
        await c.message.answer(WELCOME,reply_markup=user_kb(c.from_user.id in S.admins))

    @dp.callback_query(F.data=="admin_close")
    async def admin_close_cb(c):
        pending_admin.pop(c.from_user.id,None);pending_user.pop(c.from_user.id,None)
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
        user_action=pending_user.get(m.from_user.id)
        if user_action:
            text=(m.text or "").strip()
            if user_action in {"download","audio"}:
                pending_user.pop(m.from_user.id,None)
                if not valid_url(text) or not platform(text):
                    return await m.answer("⚠️ أرسل رابط فيديو عام صحيحًا من منصة مدعومة.",reply_markup=back_user_kb(m.from_user.id in S.admins))
                async with Session() as db:
                    x=await user(db,m.from_user)
                    if x.blocked:return await m.answer("🚫 حسابك محظور من استخدام البوت.")
                task_type="audio" if user_action=="audio" else "download"
                await enqueue({"type":task_type,"url":text,"chat_id":m.chat.id,"user_id":m.from_user.id})
                return await m.answer("🟡 تمت إضافة الطلب إلى قائمة الانتظار.\n⏳ سيصل الملف تلقائيًا عند اكتمال التحميل.")
            if user_action=="search":
                pending_user.pop(m.from_user.id,None)
                if not text:return await m.answer("⚠️ أرسل كلمة بحث.",reply_markup=back_user_kb(m.from_user.id in S.admins))
                try:
                    rows=await yt_search(text)
                    async with Session() as db:
                        x=await user(db,m.from_user);x.searches+=1;await db.commit()
                    kb=await search_kb(rows)
                    return await m.answer(("🔎 نتائج البحث:\n\n"+"\n".join(f"• {t}" for t,_ in rows)) if rows else "لا توجد نتائج.",reply_markup=kb)
                except Exception as e:return await m.answer("⚠️ "+str(e)[:400],reply_markup=back_user_kb(m.from_user.id in S.admins))
            if user_action=="hashtag":
                pending_user.pop(m.from_user.id,None)
                raw=text.lstrip("#").split()[0] if text else ""
                if not raw or len(raw)>80 or not raw.replace("_","").isalnum():
                    return await m.answer("⚠️ أرسل هاشتاغ صحيحًا.",reply_markup=back_user_kb(m.from_user.id in S.admins))
                try:
                    rows=await yt_search("#"+raw)
                    async with Session() as db:
                        x=await user(db,m.from_user);x.searches+=1;await db.commit()
                    kb=await search_kb(rows)
                    return await m.answer(("🏷️ نتائج الهاشتاغ #"+raw+"\n\n"+"\n".join(f"• {t}" for t,_ in rows)) if rows else "لا توجد نتائج.",reply_markup=kb)
                except Exception as e:return await m.answer("⚠️ "+str(e)[:400],reply_markup=back_user_kb(m.from_user.id in S.admins))
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
