from alembic import op
import sqlalchemy as sa
revision="0001_initial";down_revision=None;branch_labels=None;depends_on=None
def upgrade():
    op.create_table("users",sa.Column("id",sa.Integer,primary_key=True),sa.Column("telegram_id",sa.BigInteger,unique=True),sa.Column("username",sa.String(255)),sa.Column("first_name",sa.String(255)),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now()),sa.Column("downloads",sa.Integer,server_default="0"),sa.Column("searches",sa.Integer,server_default="0"),sa.Column("conversions",sa.Integer,server_default="0"),sa.Column("blocked",sa.Boolean,server_default=sa.false()))
def downgrade():op.drop_table("users")
