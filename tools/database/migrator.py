"""Database migration manager using Alembic."""

import logging
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from app.core.config import settings

logger = logging.getLogger(__name__)


class DatabaseMigrator:
    """Database migration manager using Alembic."""
    
    def __init__(self):
        self.alembic_cfg = Config("alembic.ini")
        # Update the database URL from settings
        self.alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    
    def get_current_revision(self):
        """Get current database revision."""
        try:
            engine = create_engine(settings.database_url)
            with engine.connect() as connection:
                context = MigrationContext.configure(connection)
                return context.get_current_revision()
        except Exception as e:
            logger.warning(f"Could not get current revision: {e}")
            return None
    
    def get_head_revision(self):
        """Get head revision from script directory."""
        try:
            script_dir = ScriptDirectory.from_config(self.alembic_cfg)
            return script_dir.get_current_head()
        except Exception as e:
            logger.warning(f"Could not get head revision: {e}")
            return None
    
    def upgrade(self):
        """Upgrade database to latest revision."""
        try:
            logger.info("Starting database migration...")
            
            current_rev = self.get_current_revision()
            head_rev = self.get_head_revision()
            
            logger.info(f"Current revision: {current_rev}")
            logger.info(f"Head revision: {head_rev}")
            
            if current_rev != head_rev:
                command.upgrade(self.alembic_cfg, "head")
                logger.info("Database migration completed successfully")
            else:
                logger.info("Database is already up to date")
                
        except Exception as e:
            logger.error(f"Database migration failed: {e}")
            raise
    
    def downgrade(self, revision="base"):
        """Downgrade database to specified revision."""
        try:
            logger.info(f"Downgrading database to revision: {revision}")
            command.downgrade(self.alembic_cfg, revision)
            logger.info("Database downgrade completed successfully")
        except Exception as e:
            logger.error(f"Database downgrade failed: {e}")
            raise
    
    def get_migration_status(self):
        """Get migration status information."""
        try:
            current_rev = self.get_current_revision()
            head_rev = self.get_head_revision()
            
            return {
                "current_revision": current_rev,
                "head_revision": head_rev,
                "is_up_to_date": current_rev == head_rev,
                "pending_migrations": current_rev != head_rev
            }
        except Exception as e:
            logger.error(f"Could not get migration status: {e}")
            return {
                "current_revision": None,
                "head_revision": None,
                "is_up_to_date": False,
                "pending_migrations": True,
                "error": str(e)
            }
    
    def create_migration(self, message: str):
        """Create a new migration."""
        try:
            logger.info(f"Creating new migration: {message}")
            command.revision(self.alembic_cfg, message=message, autogenerate=True)
            logger.info("Migration created successfully")
        except Exception as e:
            logger.error(f"Failed to create migration: {e}")
            raise
    
    def get_migration_history(self):
        """Get migration history."""
        try:
            script_dir = ScriptDirectory.from_config(self.alembic_cfg)
            return script_dir.walk_revisions()
        except Exception as e:
            logger.error(f"Could not get migration history: {e}")
            return []