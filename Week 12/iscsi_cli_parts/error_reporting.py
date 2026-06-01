from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import List, Optional, Tuple

from .common import (
    NodeErrorReport,
    emit_output,
    get_target_nodes,
    render_table,
    run_pdsh_text_by_node,
    run_pdsh_text,
)

ERROR_PATTERNS = [
    {"pattern": r"connection.*lost", "severity": "warning", "source": "iscsi"},
    {"pattern": r"session.*failure", "severity": "warning", "source": "iscsi"},
    {"pattern": r"iscsi.*timed out", "severity": "warning", "source": "network"},
    {"pattern": r"blocked for more than", "severity": "critical", "source": "kernel"},
    {"pattern": r"I/O error", "severity": "critical", "source": "storage"},
    {
        "pattern": r"rejecting I/O to offline device",
        "severity": "critical",
        "source": "storage",
    },
    {"pattern": r"blk_update_request", "severity": "critical", "source": "storage"},
    {"pattern": r"Buffer I/O error", "severity": "critical", "source": "storage"},
    {"pattern": r"link is down", "severity": "warning", "source": "network"},
    {"pattern": r"task .* hung", "severity": "critical", "source": "kernel"},
    {"pattern": r"multipath.*faulty", "severity": "critical", "source": "multipath"},
]


def collect_recent_logs(node: str, lines: int = 200) -> Tuple[str, Optional[str]]:
    commands = [
        f"journalctl -n {lines} --no-pager 2>/dev/null",
        f"tail -n {lines} /var/log/messages 2>/dev/null",
        f"tail -n {lines} /var/log/syslog 2>/dev/null",
        f"dmesg | tail -n {lines}",
    ]
    for command in commands:
        output, error = run_pdsh_text(f'pdsh -w {node} "{command}"')
        if output:
            return output, None
        if error:
            continue
    return "", f"{node}: unable to collect logs"


def collect_recent_logs_for_nodes(
    nodes: List[str], lines: int = 200
) -> Tuple[dict, dict]:
    commands = [
        f"journalctl -n {lines} --no-pager 2>/dev/null",
        f"tail -n {lines} /var/log/messages 2>/dev/null",
        f"tail -n {lines} /var/log/syslog 2>/dev/null",
        f"dmesg | tail -n {lines}",
    ]

    logs_by_node: dict = {}
    remaining = {node for node in nodes if node}

    for command in commands:
        if not remaining:
            break
        outputs, _ = run_pdsh_text_by_node(sorted(remaining), command)
        for node, output in outputs.items():
            if output:
                logs_by_node[node] = output
                remaining.discard(node)

    errors_by_node = {
        node: f"{node}: unable to collect logs" for node in sorted(remaining)
    }
    return logs_by_node, errors_by_node


def scan_logs_for_errors(node: str, log_text: str) -> List[NodeErrorReport]:
    findings: List[NodeErrorReport] = []
    for line in log_text.splitlines():
        lowered = line.lower()
        for entry in ERROR_PATTERNS:
            if re.search(entry["pattern"], lowered, re.IGNORECASE):
                findings.append(
                    NodeErrorReport(
                        node=node,
                        source=entry["source"],
                        severity=entry["severity"],
                        message=line.strip(),
                    )
                )
                break
    return findings


def collect_node_diagnostics(node: str) -> Tuple[List[NodeErrorReport], List[str]]:
    logs, log_error = collect_recent_logs(node)
    if log_error:
        return [], [log_error]
    return scan_logs_for_errors(node, logs), []


def _collect_error_summary(node: str, lines: int) -> dict:
    logs, log_error = collect_recent_logs(node, lines)
    diagnostics = scan_logs_for_errors(node, logs) if logs else []
    return {
        "node": node,
        "lines": lines,
        "log_error": log_error,
        "logs": logs,
        "diagnostics": [asdict(diagnostic) for diagnostic in diagnostics],
    }


def format_error_summary(payload: dict) -> str:
    if "nodes" in payload:
        lines = [
            f"Label: {payload.get('label', '-')}",
            f"Lines: {payload.get('lines', '-')}",
            "",
            "Node summaries",
        ]
        node_rows = [
            [
                item.get("node", "-"),
                str(len(item.get("diagnostics", []))),
                item.get("log_error") or "ok",
            ]
            for item in payload.get("nodes", [])
        ]
        if node_rows:
            lines.append(render_table(["Node", "Findings", "Log status"], node_rows))
        else:
            lines.append("None")

        diagnostic_rows = []
        for item in payload.get("nodes", []):
            for diagnostic in item.get("diagnostics", []):
                diagnostic_rows.append(
                    [
                        diagnostic.get("node", "-"),
                        diagnostic.get("severity", "-"),
                        diagnostic.get("source", "-"),
                        diagnostic.get("message", "-")[:120],
                    ]
                )
        lines.append("")
        lines.append("Detected errors")
        if diagnostic_rows:
            lines.append(
                render_table(["Node", "Severity", "Source", "Message"], diagnostic_rows)
            )
        else:
            lines.append("None")
        return "\n".join(lines)

    lines = [f"Node: {payload.get('node', '-')}", f"Lines: {payload.get('lines', '-')}"]
    if payload.get("log_error"):
        lines.append(f"Log error: {payload['log_error']}")

    diagnostics = payload.get("diagnostics", [])
    lines.append("")
    lines.append("Detected errors")
    if diagnostics:
        rows = [
            [
                diagnostic.get("severity", "-"),
                diagnostic.get("source", "-"),
                diagnostic.get("message", "-")[:120],
            ]
            for diagnostic in diagnostics
        ]
        lines.append(render_table(["Severity", "Source", "Message"], rows))
    else:
        lines.append("None")

    if payload.get("logs"):
        lines.append("")
        lines.append("Recent logs")
        lines.append(payload["logs"])

    return "\n".join(lines)


def cmd_get_errors(args) -> None:
    if args.name:
        payload = _collect_error_summary(args.name, args.lines)
    else:
        nodes, error = get_target_nodes(args.label)
        if error:
            raise SystemExit(error)

        logs_by_node, errors_by_node = collect_recent_logs_for_nodes(nodes, args.lines)
        payload = {
            "label": args.label,
            "lines": args.lines,
            "nodes": [],
        }
        with ThreadPoolExecutor() as executor:
            future_map = {
                executor.submit(
                    scan_logs_for_errors, node, logs_by_node.get(node, "")
                ): node
                for node in nodes
            }
            for future in as_completed(future_map):
                node = future_map[future]
                diagnostics = [asdict(item) for item in future.result()]
                payload["nodes"].append(
                    {
                        "node": node,
                        "lines": args.lines,
                        "log_error": errors_by_node.get(node),
                        "logs": logs_by_node.get(node, ""),
                        "diagnostics": diagnostics,
                    }
                )

    emit_output(payload, args.json, formatter=format_error_summary)
