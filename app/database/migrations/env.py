from alembic import context
from sqlalchemy import create_engine
from app.main import Base,S
config=context.config
config.set_main_option("sqlalchemy.url",S.database_url.replace("+asyncpg","").replace("%","%%"))
target_metadata=Base.metadata
if context.is_offline_mode():
    context.configure(url=config.get_main_option("sqlalchemy.url"),target_metadata=target_metadata,literal_binds=True);context.run_migrations()
else:
    with create_engine(S.database_url.replace("+asyncpg","")).connect() as c:
        context.configure(connection=c,target_metadata=target_metadata);context.run_migrations()
