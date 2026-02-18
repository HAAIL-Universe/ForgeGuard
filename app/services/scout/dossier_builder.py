"""Dossier builder — LLM-powered project analysis & score history."""

import json
import logging
from uuid import UUID

from app.clients.llm_client import chat
from app.repos.scout_repo import get_score_history, get_scout_run

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM Dossier generation
# ---------------------------------------------------------------------------

_DOSSIER_SYSTEM_PROMPT = """You are a senior software architect performing a comprehensive project review.
You will receive:
  1. Measured metrics (deterministic, already computed) with scores per dimension.
  2. Detected smells / risks (concrete findings with file paths).
  3. Stack profile, architecture map, and selected code samples.

Your job is to NARRATE the measured data — do NOT invent your own score.
Use the computed_score provided in the metrics block as the quality score.
Explain why each dimension scored as it did and add any qualitative insights
the code samples reveal that the metrics could not capture.

Produce a Project Dossier as valid JSON with exactly this schema:
{
  "executive_summary": "2-3 sentence overview of what this project is and does",
  "intent": "One-line description of the project's purpose",
  "quality_assessment": {
    "score": <use computed_score from metrics — do NOT change it>,
    "strengths": ["strength 1", ...],
    "weaknesses": ["weakness 1", ...]
  },
  "risk_areas": [
    {"area": "<category>", "severity": "low|medium|high", "detail": "..."}
  ],
  "recommendations": [
    {"priority": "low|medium|high", "suggestion": "..."}
  ]
}

RULES:
- The quality_assessment.score MUST equal the computed_score integer.
- Every detected smell MUST appear in risk_areas.
- Strengths / weaknesses must reference specific metric dimensions.
- Recommendations must be concrete and actionable.
Return ONLY the JSON object. No markdown fences, no extra text."""


async def _generate_dossier(
    full_name: str,
    metadata: dict,
    stack_profile: dict,
    arch_map: dict,
    file_contents: dict[str, str],
    metrics: dict | None = None,
) -> dict | None:
    """Generate a project dossier via a single LLM call.

    Returns the parsed dossier dict, or None if LLM is unavailable or fails.
    """
    from app.config import settings

    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        logger.info("No API key configured -- skipping LLM dossier")
        return None

    # Build a compact context for the LLM (cap at ~6K chars of code samples)
    code_samples = ""
    budget = 6000
    for fpath in ("README.md", "readme.md", "README.rst"):
        if fpath in file_contents and budget > 0:
            snippet = file_contents[fpath][:2000]
            code_samples += f"\n--- {fpath} ---\n{snippet}\n"
            budget -= len(snippet)

    for fpath, content in file_contents.items():
        if budget <= 0:
            break
        if fpath.lower().startswith("readme"):
            continue
        snippet = content[:1500]
        code_samples += f"\n--- {fpath} ---\n{snippet}\n"
        budget -= len(snippet)

    metrics_block = ""
    if metrics:
        metrics_block = (
            f"\n\nMeasured Metrics (deterministic):\n"
            f"{json.dumps(metrics.get('scores', {}), indent=2)}\n"
            f"Computed Score: {metrics.get('computed_score', 'N/A')}\n"
            f"File Stats: {json.dumps(metrics.get('file_stats', {}), indent=2)}\n"
        )
        smells = metrics.get("smells", [])
        if smells:
            metrics_block += f"\nDetected Smells ({len(smells)}):\n"
            for s in smells:
                metrics_block += f"  - [{s['severity']}] {s['name']}: {s['detail']}\n"

    user_msg = (
        f"Repository: {full_name}\n"
        f"Description: {metadata.get('description', 'N/A')}\n"
        f"Stars: {metadata.get('stargazers_count', 0)}, "
        f"Forks: {metadata.get('forks_count', 0)}\n"
        f"{metrics_block}\n"
        f"Stack Profile:\n{json.dumps(stack_profile, indent=2)}\n\n"
        f"Architecture Map:\n{json.dumps(arch_map, indent=2, default=str)}\n\n"
        f"Code Samples:\n{code_samples}"
    )

    try:
        model = settings.LLM_PLANNER_MODEL  # use the cheaper planner model
        result = await chat(
            api_key=api_key,
            model=model,
            system_prompt=_DOSSIER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=2048,
            provider="anthropic",
        )
        text = result["text"] if isinstance(result, dict) else result
        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        dossier = json.loads(text)
        # Validate expected keys
        if "executive_summary" not in dossier:
            logger.warning("LLM dossier missing executive_summary")
            return None
        return dossier
    except Exception:
        logger.exception("Failed to generate LLM dossier for %s", full_name)
        return None


async def get_scout_dossier(user_id: UUID, run_id: UUID) -> dict | None:
    """Retrieve the dossier from a completed deep scan."""
    run = await get_scout_run(run_id)
    if run is None or str(run["user_id"]) != str(user_id):
        raise ValueError("Scout run not found")
    if run.get("scan_type") != "deep":
        raise ValueError("Dossier is only available for deep scan runs")

    results = run.get("results")
    if results is None:
        return None
    if isinstance(results, str):
        results = json.loads(results)

    return {
        "metadata": results.get("metadata"),
        "stack_profile": results.get("stack_profile"),
        "architecture": results.get("architecture"),
        "dossier": results.get("dossier"),
        "metrics": results.get("metrics"),
        "checks": results.get("checks", []),
        "warnings": results.get("warnings", []),
        "files_analysed": results.get("files_analysed", 0),
        "tree_size": results.get("tree_size", 0),
        "head_sha": results.get("head_sha"),
    }


async def get_scout_score_history(
    user_id: UUID,
    repo_id: UUID,
    limit: int = 30,
) -> list[dict]:
    """Return computed score history for score-over-time charts."""
    from app.repos.repo_repo import get_repo_by_id as _get_repo

    repo = await _get_repo(repo_id)
    if repo is None or str(repo["user_id"]) != str(user_id):
        raise ValueError("Repo not found or not owned by user")

    rows = await get_score_history(repo_id, user_id, limit)
    return [
        {
            "run_id": str(r["id"]),
            "computed_score": r["computed_score"],
            "checks_passed": r["checks_passed"],
            "checks_failed": r["checks_failed"],
            "checks_warned": r["checks_warned"],
            "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
            "completed_at": r["completed_at"].isoformat() if r.get("completed_at") else None,
        }
        for r in rows
    ]
