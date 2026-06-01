from __future__ import annotations

from typing import Dict, List, Tuple

from .common import run_pdsh_lines, run_pdsh_text
from .error_reporting import collect_node_diagnostics


def collect_initiator_metrics(node: str) -> Tuple[Dict[str, int], List[str]]:
    errors: List[str] = []
    output, error = run_pdsh_text(
        f'pdsh -w {node} "lsblk -o MOUNTPOINT,TRAN --noheadings | grep iscsi || true"'
    )
    if error:
        errors.append(f"{node}: unable to collect initiator metrics: {error}")
        return {"total": 0, "mounted": 0, "unmounted": 0, "sessions": 0}, errors

    lines = [line for line in output.splitlines() if line.strip()]
    total = len(lines)
    mounted = sum(1 for line in lines if line.strip().startswith("/"))
    unmounted = total - mounted

    sessions_output, sessions_error = run_pdsh_lines(
        f'pdsh -w {node} "sudo iscsiadm -m session 2>/dev/null || true"'
    )
    sessions = (
        0 if sessions_error else len([line for line in sessions_output if line.strip()])
    )

    session_details_output, session_details_error = run_pdsh_text(
        f'pdsh -w {node} "sudo iscsiadm -m session -P 3 2>/dev/null || true"'
    )
    if session_details_error:
        errors.append(
            f"{node}: unable to collect session details: {session_details_error}"
        )

    session_details = [
        line for line in session_details_output.splitlines() if line.strip()
    ]

    return {
        "total": total,
        "mounted": mounted,
        "unmounted": unmounted,
        "sessions": sessions,
        "session_details": session_details,
    }, errors


def build_initiator_node_summary(node: str) -> dict:
    stats, metric_errors = collect_initiator_metrics(node)
    diagnostics, diagnostic_errors = collect_node_diagnostics(node)
    errors = metric_errors + diagnostic_errors
    return {
        "node": node,
        "role": "initiator",
        "sessions": stats.get("sessions", 0),
        "total": stats.get("total", 0),
        "mounted": stats.get("mounted", 0),
        "unmounted": stats.get("unmounted", 0),
        "session_details": stats.get("session_details", []),
        "diagnostics": [diagnostic.__dict__ for diagnostic in diagnostics],
        "errors": errors,
    }
