import asyncio,logging,os,time,json,urllib.parse,urllib.request
from pathlib import Path
from urllib.parse import urlparse
from aiogram import Bot,Dispatcher,F
from aiogram.filters import Command
from aiogram.types import Message,CallbackQuery,InlineKeyboardMarkup,InlineKeyboardButton,FSInputFile
from pydantic_settings import BaseSettings,SettingsConfigDict
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column
from sqlalchemy import BigInteger,Integer,String,Boolean,DateTime,func,select
import yt_dlp

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file=".env",extra="ignore")
    bot_token:str="";database_url:str="postgresql+asyncpg://postgres:postgres@localhost:5432/social_downloader";redis_url:str="redis://localhost:6379/0"
    admin_ids:str="";youtube_api_key:str="";temp_dir:str="./temp";max_file_size:int=52428800;download_timeout:int=120;rate_limit:int=10
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
def kb():return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 تحميل",callback_data="download"),InlineKeyboardButton(text="👤 حسابي",callback_data="stats")],[InlineKeyboardButton(text="🎵 فيديو→صوت",callback_data="audio"),InlineKeyboardButton(text="ℹ️ المساعدة",callback_data="help")]])
async def main():
    if not S.bot_token:raise RuntimeError("BOT_TOKEN غير مضبوط")
    bot=Bot(S.bot_token);dp=Dispatcher();hits={}
    async def allowed(m):
        if not m.from_user:return False
        now=time.monotonic();q=hits.setdefault(m.from_user.id,[]);q[:]=[x for x in q if now-x<60]
        if len(q)>=S.rate_limit:return False
        q.append(now);return True
    @dp.message(Command("start"))
    async def start(m):
        if not await allowed(m):return
        async with Session() as db:
            u=await user(db,m.from_user)
            if u.blocked:return
        await m.answer("أهلًا بك 👋\nأرسل رابطًا عامًا من المنصات المدعومة.",reply_markup=kb())
    @dp.message(Command("stats"))
    async def stats(m):
        if not await allowed(m):return
        async with Session() as db:u=await user(db,m.from_user)
        await m.answer(f"👤 الحساب\n📥 {u.downloads}\n🔎 {u.searches}\n🎵 {u.conversions}")
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
            await m.answer("⏳ جاري تنزيل الفيديو وتحويله...")
            src,title=await grab(url);dst=await convert(src);await m.answer_audio(FSInputFile(dst),caption=title)
            async with Session() as db:u=await user(db,m.from_user);u.conversions+=1;await db.commit()
        except Exception as e:await m.answer("❌ فشل التحويل: "+str(e)[:400])
        finally:
            for p in (src,dst):
                if p and os.path.exists(p):
                    try:os.remove(p)
                    except OSError:pass
    @dp.message(Command("admin"))
    async def admin(m):
        if m.from_user.id in S.admins:await m.answer("🛠 الإدارة\n/block ID\n/unblock ID\n/broadcast نص")
    @dp.message(Command("block"))
    async def block(m):
        if m.from_user.id not in S.admins:return
        p=(m.text or "").split()
        if len(p)!=2 or not p[1].isdigit():return await m.answer("/block ID")
        async with Session() as db:x=await db.scalar(select(User).where(User.telegram_id==int(p[1])));x.blocked=True if x else False;await db.commit()
        await m.answer("تم الحظر." if x else "غير موجود.")
    @dp.message(Command("unblock"))
    async def unblock(m):
        if m.from_user.id not in S.admins:return
        p=(m.text or "").split()
        if len(p)!=2 or not p[1].isdigit():return await m.answer("/unblock ID")
        async with Session() as db:x=await db.scalar(select(User).where(User.telegram_id==int(p[1])));x.blocked=False if x else False;await db.commit()
        await m.answer("تم إلغاء الحظر." if x else "غير موجود.")
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
        await m.answer(f"📣 تم الإرسال: {sent}\n❌ فشل: {failed}")
    @dp.callback_query(F.data=="stats")
    async def stats_cb(c):await c.answer();await stats(c.message)
    @dp.callback_query(F.data=="help")
    async def help_cb(c):await c.answer();await c.message.answer("أرسل رابطًا عامًا. لا يوجد تجاوز للخصوصية أو DRM.\nللبحث: /yt كلمة\nللصوت: /audio رابط")
    @dp.callback_query(F.data=="audio")
    async def audio_cb(c):await c.answer();await c.message.answer("استخدم /audio ثم ضع رابط فيديو عام.")
    @dp.callback_query(F.data=="download")
    async def download_cb(c):await c.answer();await c.message.answer("أرسل رابط الفيديو العام مباشرة.")
    @dp.message()
    async def media(m):
        if not await allowed(m):return
        u=(m.text or "").strip();name=platform(u)
        if not name:return
        async with Session() as db:
            x=await user(db,m.from_user)
            if x.blocked:return
        await m.answer(f"🔎 {name}\n⏳ جارٍ التحميل...")
        path=None
        try:
            path,title=await grab(u)
            if os.path.getsize(path)>S.max_file_size:raise RuntimeError("حجم الملف أكبر من الحد المسموح")
            await m.answer_document(FSInputFile(path),caption=f"✅ {title}")
            async with Session() as db:x=await user(db,m.from_user);x.downloads+=1;await db.commit()
        except Exception as e:logging.exception("download");await m.answer("❌ تعذر التحميل: "+str(e)[:400])
        finally:
            if path and os.path.exists(path):
                try:os.remove(path)
                except OSError:pass
    await dp.start_polling(bot)
if __name__=="__main__":asyncio.run(main())
