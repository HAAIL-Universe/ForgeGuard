"""clean_directives.py — One-shot migration: strip AEM/boot_script contamination
from ALL builder_directives stored in the Neon DB.

Run from the ForgeGuard root:

    python clean_directives.py [--dry-run]

With --dry-run, prints a preview of what would change without writing to the DB.
Without --dry-run, cleans and updates all affected directives.

Exit codes:
    0 — success (even if nothing needed cleaning)
    1 — fatal error (DB connection failed, etc.)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

# Allow importing from app/ when running from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv()


async def main(dry_run: bool) -> int:
    import asyncpg
    from app.services.directive_cleaner import clean_builder_directive

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set. Source your .env file first.", file=sys.stderr)
        return 1

    print(f"{'[DRY RUN] ' if dry_run else ''}Connecting to DB…")
    conn = await asyncpg.connect(db_url)

    try:
        rows = await conn.fetch(
            """
            SELECT p.id AS project_id, p.name AS project_name,
                   pc.id AS contract_id, pc.content, pc.version
            FROM project_contracts pc
            JOIN projects p ON p.id = pc.project_id
            WHERE pc.contract_type = 'builder_directive'
            ORDER BY p.name
            """
        )
    except Exception as exc:
        print(f"ERROR fetching rows: {exc}", file=sys.stderr)
        await conn.close()
        return 1

    print(f"Found {len(rows)} builder_directive(s) to inspect.\n")

    cleaned_count = 0
    skipped_count = 0

    for row in rows:
        project_id = row["project_id"]
        project_name = row["project_name"] or str(project_id)
        content = row["content"]
        version = row["version"]

        cleaned, changes = clean_builder_directive(content)

        if not changes:
            print(f"  OK  {project_name}  (v{version}) -- already clean")
            skipped_count += 1
            continue

        print(f"  **  {project_name}  (v{version}) -- {len(changes)} change(s):")
        for c in changes:
            print(f"       - {c}")

        if dry_run:
            # Show a brief diff context
            old_lines = content.splitlines()
            new_lines = cleaned.splitlines()
            removed = [l for l in old_lines if l not in new_lines and l.strip()]
            if removed:
                print("     Removed lines preview:")
                for l in removed[:5]:
                    print(f"       - {l[:120]}")
                if len(removed) > 5:
                    print(f"       … and {len(removed) - 5} more")
        else:
            try:
                await conn.execute(
                    """
                    UPDATE project_contracts
                    SET content = $1, version = version + 1, updated_at = now()
                    WHERE project_id = $2 AND contract_type = 'builder_directive'
                    """,
                    cleaned,
                    project_id,
                )
                # Invalidate any cached plan
                await conn.execute(
                    "UPDATE projects SET cached_plan_json = NULL, plan_cached_at = NULL WHERE id = $1",
                    project_id,
                )
                print(f"     -> Updated to v{version + 1}")
            except Exception as exc:
                print(f"     ERROR updating {project_name}: {exc}", file=sys.stderr)

        cleaned_count += 1

    await conn.close()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Done.")
    print(f"  Cleaned:  {cleaned_count}")
    print(f"  Already clean: {skipped_count}")
    if dry_run:
        print("\nRe-run without --dry-run to apply changes.")
    return 0


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv or "-n" in sys.argv
    sys.exit(asyncio.run(main(dry_run=dry)))
