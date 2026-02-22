"""Entry point for running auditor as standalone service."""

import asyncio
import json
import logging
import sys
from argparse import ArgumentParser
from uuid import UUID

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from auditor_agent import run_auditor

logger = logging.getLogger(__name__)


async def main():
    """Main entry point."""
    parser = ArgumentParser(description="Run Forge auditor")
    parser.add_argument("--mode", choices=["plan", "phase"], required=True, help="Audit mode")
    parser.add_argument("--build-id", type=UUID, required=True, help="Build UUID")
    parser.add_argument("--project-id", type=UUID, required=True, help="Project UUID")
    parser.add_argument("--plan-json", type=str, help="JSON plan (mode=plan)")
    parser.add_argument("--phase", type=int, help="Phase number (mode=phase)")
    parser.add_argument("--phase-files", type=str, help="Comma-separated files (mode=phase)")
    parser.add_argument("--test-coverage", type=float, help="Test coverage % (mode=phase)")
    parser.add_argument("--violations", type=str, help="JSON violations list (mode=phase)")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Parse optional arguments
    plan_json = None
    if args.plan_json:
        try:
            plan_json = json.loads(args.plan_json)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON plan: {e}")
            sys.exit(1)

    phase_files = []
    if args.phase_files:
        phase_files = [f.strip() for f in args.phase_files.split(",")]

    violations = []
    if args.violations:
        try:
            violations = json.loads(args.violations)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON violations: {e}")
            sys.exit(1)

    # Run auditor
    try:
        result = await run_auditor(
            mode=args.mode,
            build_id=args.build_id,
            project_id=args.project_id,
            plan_json=plan_json,
            phase_number=args.phase,
            phase_files=phase_files,
            test_coverage=args.test_coverage,
            violations=violations,
            verbose=args.verbose,
        )

        print("\n" + "=" * 60)
        print("AUDIT RESULT")
        print("=" * 60)
        print(f"Status: {result.status}")
        print(f"Passed: {result.passed}")
        print(f"Duration: {result.duration_seconds:.1f}s")
        print(f"Issues: {len(result.issues)}")
        print(f"Recommendations: {len(result.recommendations)}")
        print()

        if result.issues:
            print("Issues:")
            for issue in result.issues:
                print(f"  [{issue.severity}] {issue.check_name}: {issue.message}")
                if issue.remediation:
                    print(f"        Remediation: {issue.remediation}")

        if result.recommendations:
            print("\nRecommendations:")
            for rec in result.recommendations:
                print(f"  [{rec.priority}] {rec.suggestion}")

        print(f"\nToken usage: {result.token_usage.input_tokens} in, {result.token_usage.output_tokens} out")

        sys.exit(0 if result.passed else 1)

    except Exception as e:
        logger.error(f"Auditor failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
