"""
run_builder.py — CLI entry point for the Forge Builder Agent.

Usage:
    python run_builder.py <plan_json_path> [--phase N] [--file path]

Examples:
    # Build every file in every phase of the plan
    python run_builder.py ../ForgeBuilds/plan_my-api_20240101_120000.json

    # Build only phase 1
    python run_builder.py plan.json --phase 1

    # Build a single specific file from the plan
    python run_builder.py plan.json --phase 1 --file app/main.py

This mirrors planner/run_builder.py in structure.
Set ANTHROPIC_API_KEY in the environment before running.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure the ForgeGuard package root is on sys.path so `app.*` is importable
# when running this script directly (e.g. `python builder/run_builder.py ...`)
_THIS_DIR = Path(__file__).parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from builder_agent import run_builder, BuilderError  # noqa: E402 — after sys.path fix


def _print_summary(result, file_path: str) -> None:
    u = result.token_usage
    inp = u.get("input_tokens", 0)
    out = u.get("output_tokens", 0)
    iters = result.iterations

    print(f"\n[BUILDER] ════════════════════════════════════")
    print(f"[BUILDER] FILE COMPLETE: {file_path}")
    print(f"[BUILDER]   Status:     {result.status}")
    if result.error:
        print(f"[BUILDER]   Error:      {result.error}")
    print(f"[BUILDER]   Iterations: {iters}  (sub-agent calls)")
    print(f"[BUILDER] TOKEN USAGE:")
    print(f"[BUILDER]   Input:      {inp:>10,}")
    print(f"[BUILDER]   Output:     {out:>10,}")
    print(f"[BUILDER] ════════════════════════════════════")


def _load_plan(plan_path: str) -> dict:
    p = Path(plan_path)
    if not p.exists():
        sys.exit(f"[BUILDER] ERROR: plan file not found: {plan_path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[BUILDER] ERROR: invalid JSON in plan file: {e}")


def _extract_phases(plan: dict, phase_filter: int | None) -> list[dict]:
    phases = plan.get("phases", [])
    if not phases:
        sys.exit("[BUILDER] ERROR: plan has no phases")
    if phase_filter is not None:
        phases = [p for p in phases if p.get("number") == phase_filter]
        if not phases:
            sys.exit(f"[BUILDER] ERROR: phase {phase_filter} not found in plan")
    return phases


def _extract_contracts(plan: dict) -> list[str]:
    """Extract contract text blocks from plan summary."""
    contracts: list[str] = []
    summary = plan.get("summary", {})
    key_constraints = summary.get("key_constraints", [])
    if key_constraints:
        contracts.append("## Key Constraints\n" + "\n".join(f"- {c}" for c in key_constraints))
    stack = plan.get("stack", {})
    if stack:
        contracts.append(f"## Stack\n{json.dumps(stack, indent=2)}")
    return contracts


async def _run_plan(
    plan_path: str,
    phase_filter: int | None,
    file_filter: str | None,
    api_key: str,
) -> None:
    plan = _load_plan(plan_path)
    phases = _extract_phases(plan, phase_filter)
    contracts = _extract_contracts(plan)

    # Use a temp directory for CLI builds (no persistent workspace)
    import tempfile
    working_dir = tempfile.mkdtemp(prefix="forge_builder_")
    print(f"[BUILDER] Working dir: {working_dir}")

    # Use a stable build_id and user_id for CLI runs
    import uuid
    cli_build_id = str(uuid.uuid4())
    cli_user_id = str(uuid.uuid4())

    total_input = 0
    total_output = 0
    files_built = 0
    files_failed = 0

    for phase in phases:
        phase_num = phase.get("number", "?")
        phase_name = phase.get("name", f"Phase {phase_num}")
        manifest = phase.get("files", [])

        if not manifest:
            print(f"[BUILDER] Phase {phase_num} ({phase_name}): no files — skipping")
            continue

        print(f"\n[BUILDER] ── Phase {phase_num}: {phase_name} ({len(manifest)} files) ──")

        phase_deliverables = []
        for obj in phase.get("objectives", []):
            phase_deliverables.append(str(obj))
        for ac in phase.get("acceptance_criteria", []):
            phase_deliverables.append(str(ac))

        for file_entry in manifest:
            fpath = file_entry.get("path", "unknown")

            if file_filter and fpath != file_filter:
                continue

            print(f"[BUILDER]   Building: {fpath}")

            try:
                result = await run_builder(
                    file_entry=file_entry,
                    contracts=contracts,
                    context=[],
                    phase_deliverables=phase_deliverables,
                    working_dir=working_dir,
                    build_id=cli_build_id,
                    user_id=cli_user_id,
                    api_key=api_key,
                    verbose=True,
                )
            except BuilderError as e:
                print(f"[BUILDER]   FAILED: {e}")
                files_failed += 1
                continue

            _print_summary(result, fpath)
            total_input += result.token_usage.get("input_tokens", 0)
            total_output += result.token_usage.get("output_tokens", 0)

            if result.status == "completed":
                files_built += 1
            else:
                files_failed += 1

    print(f"\n[BUILDER] ════════════════════════════════════")
    print(f"[BUILDER] BUILD SESSION COMPLETE")
    print(f"[BUILDER]   Files built:  {files_built}")
    print(f"[BUILDER]   Files failed: {files_failed}")
    print(f"[BUILDER] TOTAL TOKENS:")
    print(f"[BUILDER]   Input:        {total_input:>10,}")
    print(f"[BUILDER]   Output:       {total_output:>10,}")
    print(f"[BUILDER] ════════════════════════════════════")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Forge Builder Agent CLI — builds files from a plan.json",
    )
    parser.add_argument("plan_json", help="Path to the plan.json file")
    parser.add_argument(
        "--phase", type=int, default=None,
        help="Build only this phase number (default: all phases)",
    )
    parser.add_argument(
        "--file", default=None,
        help="Build only this specific file path (default: all files in phase)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        sys.exit("[BUILDER] ERROR: ANTHROPIC_API_KEY is not set")

    asyncio.run(_run_plan(
        plan_path=args.plan_json,
        phase_filter=args.phase,
        file_filter=args.file,
        api_key=api_key,
    ))


if __name__ == "__main__":
    main()
