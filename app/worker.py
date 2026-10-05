import asyncio,json,logging
from redis.asyncio import Redis
from app.main import S
async def main():
    r=Redis.from_url(S.redis_url,decode_responses=True);log=logging.getLogger("worker");logging.basicConfig(level=logging.INFO)
    while True:
        item=await r.blpop("downloads",timeout=10)
        if item:log.info("task %s",json.loads(item[1]))
        await asyncio.sleep(.1)
if __name__=="__main__":asyncio.run(main())
