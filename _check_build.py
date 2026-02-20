import asyncio, asyncpg
from app.config import settings

async def main():
    conn = await asyncpg.connect(settings.DATABASE_URL)
    rows = await conn.fetch("""
        SELECT be.phase, be.severity, be.source, be.message, be.created_at
        FROM build_errors be
        JOIN builds b ON b.id = be.build_id
        JOIN projects p ON p.id = b.project_id
        WHERE p.name = 'OthelloMini'
        ORDER BY be.created_at DESC
        LIMIT 5
    """)
    if not rows:
        print("No build errors found!")
    for r in rows:
        print(f"Phase: {r['phase']} | Severity: {r['severity']} | Source: {r['source']}")
        print(f"Message: {r['message'][:500]}")
        print(f"At: {r['created_at']}")
        print("---")

    # Also check build_logs for the latest build
    logs = await conn.fetch("""
        SELECT bl.message, bl.created_at
        FROM build_logs bl
        JOIN builds b ON b.id = bl.build_id
        JOIN projects p ON p.id = b.project_id
        WHERE p.name = 'OthelloMini'
        ORDER BY bl.created_at DESC
        LIMIT 20
    """)
    print("\n=== RECENT BUILD LOGS ===")
    for l in reversed(logs):
        print(f"{l['created_at']} | {l['message'][:200]}")

    await conn.close()

asyncio.run(main())
