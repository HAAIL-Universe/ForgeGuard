"""Tool definitions and implementations for auditor agent."""

import json

TOOL_DEFINITIONS = [
    {
        "name": "audit_complete",
        "description": "Report audit completion with results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "passed": {
                    "type": "boolean",
                    "description": "Whether audit passed (no critical issues)",
                },
                "status": {
                    "type": "string",
                    "enum": ["passed", "failed", "warned"],
                    "description": "Overall audit status",
                },
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "check_name": {"type": "string"},
                            "severity": {
                                "type": "string",
                                "enum": ["info", "warning", "error"],
                            },
                            "message": {"type": "string"},
                            "remediation": {
                                "type": "string",
                                "description": "Optional: how to fix this issue",
                            },
                        },
                        "required": ["check_name", "severity", "message"],
                    },
                    "description": "List of issues found during audit",
                },
                "recommendations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "priority": {
                                "type": "string",
                                "enum": ["low", "medium", "high"],
                            },
                            "suggestion": {"type": "string"},
                        },
                        "required": ["priority", "suggestion"],
                    },
                    "description": "Recommendations for improvement",
                },
            },
            "required": ["passed", "status"],
        },
    }
]


def dispatch_tool(tool_name: str, tool_input: dict) -> dict:
    """
    Execute a tool call.

    Returns result dict that will be sent back to the model.
    """
    if tool_name == "audit_complete":
        return {
            "success": True,
            "audit_result": {
                "passed": tool_input["passed"],
                "status": tool_input["status"],
                "issues": [
                    {
                        "check_name": issue.get("check_name"),
                        "severity": issue.get("severity"),
                        "message": issue.get("message"),
                        "remediation": issue.get("remediation"),
                    }
                    for issue in tool_input.get("issues", [])
                ],
                "recommendations": [
                    {
                        "priority": rec.get("priority"),
                        "suggestion": rec.get("suggestion"),
                    }
                    for rec in tool_input.get("recommendations", [])
                ],
            },
        }

    return {
        "error": f"Unknown tool: {tool_name}",
        "success": False,
    }
