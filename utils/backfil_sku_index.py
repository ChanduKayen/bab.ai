from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

if not DB_URL:
    raise RuntimeError("DATABASE_URL not set")

EXTENSION_SQL = [
    "CREATE EXTENSION IF NOT EXISTS unaccent;",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
]

ALTER_SQL = [
    "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS type_norm text;",
    "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS size_mm_primary numeric;",
    "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS size_mm_secondary numeric;",
    "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS primary_size_native numeric;",
    "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS primary_size_unit text;",
    "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS secondary_size_native numeric;",
    "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS secondary_size_unit text;",
    "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS search_text text;",
    "ALTER TABLE public.sku_master ADD COLUMN IF NOT EXISTS tsv tsvector;",
]

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_sku_type_norm ON public.sku_master(type_norm);",
    "CREATE INDEX IF NOT EXISTS idx_sku_size_mm ON public.sku_master(size_mm_primary, size_mm_secondary);",
    "CREATE INDEX IF NOT EXISTS idx_sku_primary_size ON public.sku_master(primary_size_unit, primary_size_native);",
    "CREATE INDEX IF NOT EXISTS idx_sku_secondary_size ON public.sku_master(secondary_size_unit, secondary_size_native);",
    "CREATE INDEX IF NOT EXISTS idx_sku_search_trgm ON public.sku_master USING gin (search_text gin_trgm_ops);",
    "CREATE INDEX IF NOT EXISTS idx_sku_tsv ON public.sku_master USING gin (tsv);",
]

TRIGGER_SQL = [
    "DROP TRIGGER IF EXISTS sku_master_search_update ON public.sku_master;",
    "DROP FUNCTION IF EXISTS public.sku_master_search_update();",
    """
    CREATE FUNCTION public.sku_master_search_update()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        NEW.search_text := concat_ws(' ', NEW.brand, NEW.category, COALESCE(NEW.type_norm,''), COALESCE(NEW.attributes->>'raw_dimension',''), COALESCE(NEW.description,''));
        NEW.tsv := to_tsvector('simple', unaccent(COALESCE(NEW.search_text,'')));
        RETURN NEW;
    END;
    $$;
    """,
    "CREATE TRIGGER sku_master_search_update BEFORE INSERT OR UPDATE ON public.sku_master FOR EACH ROW EXECUTE FUNCTION public.sku_master_search_update();",
]

REFRESH_SQL = [
    "UPDATE public.sku_master SET search_text = concat_ws(' ', brand, category, COALESCE(type_norm,''), COALESCE(attributes->>'raw_dimension',''), COALESCE(description,''));",
    "UPDATE public.sku_master SET tsv = to_tsvector('simple', unaccent(COALESCE(search_text,'')));",
]

async def apply_statements(engine, statements):
    async with engine.begin() as conn:
        for sql in statements:
            await conn.execute(text(sql))

async def main():
    engine = create_async_engine(DB_URL, future=True)
    try:
        print("Ensuring extensions...")
        await apply_statements(engine, EXTENSION_SQL)
        print("Altering sku_master columns...")
        await apply_statements(engine, ALTER_SQL)
        print("Creating indexes...")
        await apply_statements(engine, INDEX_SQL)
        print("Installing trigger...")
        await apply_statements(engine, TRIGGER_SQL)
        print("Refreshing search_text and tsv...")
        await apply_statements(engine, REFRESH_SQL)
        print("Schema update complete.")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
