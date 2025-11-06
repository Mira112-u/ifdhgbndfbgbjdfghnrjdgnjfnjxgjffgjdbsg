#!/usr/bin/env python3
"""
Database migration script to add tracked_orders field to vehicle_bindings table

This script simply re-creates tables with the new schema. Since SQLAlchemy's create_all()
is idempotent and only creates missing tables/columns, this is safe to run.

For SQLite, this is the recommended approach. For production PostgreSQL/MySQL,
consider using Alembic for proper migrations.
"""
import asyncio
import logging
from sqlalchemy import text
from database import Database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    """Run the migration"""
    logger.info("Starting migration: add tracked_orders to vehicle_bindings")
    
    db = Database()
    
    try:
        async with db.engine.begin() as conn:
            dialect_name = conn.engine.dialect.name
            logger.info("Connected to database dialect: %s", dialect_name)
            tracked_exists = False
            if dialect_name == "sqlite":
                result = await conn.execute(text("PRAGMA table_info(vehicle_bindings)"))
                columns = [row[1] for row in result]
                tracked_exists = "tracked_orders" in columns
            else:
                result = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='vehicle_bindings';"))
                columns = [row[0] for row in result]
                tracked_exists = "tracked_orders" in columns
            
            if tracked_exists:
                logger.info("tracked_orders column already exists. No changes needed.")
            else:
                logger.info("Adding tracked_orders column to vehicle_bindings table")
                await conn.execute(text("ALTER TABLE vehicle_bindings ADD COLUMN tracked_orders TEXT"))
                logger.info("✅ tracked_orders column added successfully")
                logger.info("Existing bindings will have NULL tracked_orders initially")
                logger.info("The monitor will initialize tracked_orders on first check")
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        raise
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(main())
