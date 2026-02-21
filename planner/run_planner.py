"""
run_planner.py — CLI entry point for the Forge Planner Agent.

Usage
-----
# Greenfield API project
python run_planner.py "Build a FastAPI task management API with PostgreSQL and JWT auth"

# Remediation project (fixing existing code)
python run_planner.py "Remediation: refactor the god-file at app/main.py into proper layers"

# API-only project (skips ui_template.md to save tokens)
python run_planner.py "Build a REST API only — no frontend needed"

# Quiet mode (suppress per-turn output, show summary only)
python run_planner.py --quiet "Build a FastAPI task API"

Environment
-----------
Requires ANTHROPIC_API_KEY in your environment:
  PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."
  bash:        export ANTHROPIC_API_KEY="sk-ant-..."
"""

from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Forge Planner Agent — produce a build plan from a project request.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "request",
        help="Project request in natural language.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-turn verbose output. Shows summary only.",
    )
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="Print the full plan JSON to stdout after completion.",
    )
    args = parser.parse_args()

    # ── Pre-flight checks ────────────────────────────────────────────────────
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY environment variable is not set.\n"
            "  PowerShell: $env:ANTHROPIC_API_KEY = 'sk-ant-...'\n"
            "  bash:       export ANTHROPIC_API_KEY='sk-ant-...'",
            file=sys.stderr,
        )
        sys.exit(1)

    contracts_dir = Path(__file__).parent.parent / "Forge" / "Contracts"
    if not contracts_dir.exists():
        print(
            f"ERROR: Contracts directory not found: {contracts_dir}\n"
            "Make sure you are running from z:\\ForgeCollection\\planner\\ "
            "and that Forge/Contracts/ exists.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Run ──────────────────────────────────────────────────────────────────
    # Import here so import errors surface clearly
    try:
        from planner_agent import run_planner, PlannerError
    except ImportError as exc:
        print(f"ERROR: Could not import planner_agent: {exc}", file=sys.stderr)
        print("Make sure pydantic and anthropic are installed:", file=sys.stderr)
        print("  pip install anthropic pydantic", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print("[FORGE PLANNER] Starting planning session...")
        preview = args.request[:80] + ("..." if len(args.request) > 80 else "")
        print(f"[FORGE PLANNER] Request: {preview}")
        print()

    try:
        result = run_planner(
            project_request=args.request,
            verbose=not args.quiet,
        )
    except PlannerError as exc:
        print(f"\n[FORGE PLANNER] FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[FORGE PLANNER] Interrupted by user.", file=sys.stderr)
        sys.exit(1)

    # ── Summary (always shown, even in --quiet mode) ─────────────────────────
    plan = result["plan"]
    phases = plan.get("phases", [])
    phase_names = ", ".join(f"Phase {p['number']} ({p['name']})" for p in phases)

    if args.quiet:
        # Compact summary for --quiet mode
        print(f"\nPlan complete: {result['plan_path']}")
        print(f"Phases: {phase_names}")
        u = result["token_usage"]
        savings_pct = u["cache_read_input_tokens"] / max(u["input_tokens"], 1) * 100
        print(
            f"Tokens: {u['input_tokens']:,} in / {u['output_tokens']:,} out  "
            f"(cache saved {savings_pct:.0f}%)"
        )

    if args.print_plan:
        print("\n" + "=" * 60)
        print("PLAN CONTENT:")
        print("=" * 60)
        print(json.dumps(plan, indent=2))

    # ── Validation reminder ──────────────────────────────────────────────────
    if not args.quiet:
        print()
        print("[FORGE PLANNER] To validate the plan manually:")
        print(
            f"  python -c \""
            f"import json; from plan_schema import validate_plan; "
            f"p=json.load(open(r'{result['plan_path']}')); "
            f"e=validate_plan(p); print('VALID' if not e else e)\""
        )


if __name__ == "__main__":
    main()
