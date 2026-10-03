from alembic import context
from app.config import settings
from app.db import make_engine
from app.models import Base


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        # Restore supplies the empty recovery target's connection. Never open the
        # globally configured application database for that migration.
        run_migrations(connection)
    else:
        engine = make_engine(settings.database_url)
        try:
            with engine.connect() as connection:
                run_migrations(connection)
        finally:
            engine.dispose()
