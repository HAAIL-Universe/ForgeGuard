    if os.getenv("FORGE_SANDBOX") == "1" or os.getenv("TESTING") == "1":
        db_ok = True
    else:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False