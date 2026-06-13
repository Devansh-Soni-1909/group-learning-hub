from __future__ import annotations

import os
from typing import Dict, List, Tuple, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from .utils import run_pdsh_lines, run_pdsh_text
from .error_collector import collect_node_diagnostics


def _parse_lsblk_iscsi_lines(output: str) -> List[dict]:
    mounts: List[dict] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2 or parts[-1] != "iscsi":
            continue
        device_name = parts[0]
        middle = parts[1:-1]
        mount_point = ""
        for part in middle:
            if part.startswith("/"):
                mount_point = part
            break
        if mount_point.startswith("/"):
            image_name = os.path.basename(mount_point.rstrip("/"))
        else:
            last_char = device_name[-1]
            lun_number = ord(last_char) - ord("a")
            image_name = f"lun{lun_number}"
        status = "mounted" if mount_point.startswith("/") else "unmounted"
        if not mount_point:
            mount_point = "-"
        mounts.append(
            {
                "device": f"/dev/{device_name}",
                "image_name": image_name,
                "mount_point": mount_point,
                "status": status,
            }
        )
    mounts.sort(key=lambda entry: entry["device"])
    return mounts


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

    session_lines, sessions_error = run_pdsh_lines(
        f'pdsh -w {node} "sudo iscsiadm -m session 2>/dev/null || true"'
    )
    if sessions_error:
        errors.append(f"{node}: unable to collect sessions: {sessions_error}")
        session_lines = []

    sessions = len([line for line in session_lines if line.strip()])

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
        "session_lines": [line for line in session_lines if line.strip()],
        "session_details": session_details,
    }, errors


def collect_initiator_mount_entries(node: str) -> Tuple[List[dict], List[str]]:
    errors: List[str] = []
    output, error = run_pdsh_text(
        f'pdsh -w {node} "lsblk -o NAME,LABEL,MOUNTPOINT,TRAN --noheadings 2>/dev/null | grep iscsi || true"'
    )
    if error:
        errors.append(f"{node}: unable to collect mount status: {error}")
        return [], errors
    return _parse_lsblk_iscsi_lines(output), errors


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
        "session_lines": stats.get("session_lines", []),
        "session_details": stats.get("session_details", []),
        "diagnostics": [diagnostic.__dict__ for diagnostic in diagnostics],
        "errors": errors,
    }


def build_initiator_mount_status(node: str) -> dict:
    mounts, mount_errors = collect_initiator_mount_entries(node)
    mounted = sum(1 for entry in mounts if entry["status"] == "mounted")
    unmounted = sum(1 for entry in mounts if entry["status"] == "unmounted")
    return {
        "node": node,
        "role": "initiator",
        "mounted": mounted,
        "unmounted": unmounted,
        "mounts": mounts,
        "errors": mount_errors,
    }


def collect_initiator_summaries_concurrently(nodes: Sequence[str]) -> List[dict]:
    if not nodes:
        return []
    results: List[dict] = []
    with ThreadPoolExecutor(max_workers=min(32, len(nodes))) as executor:
        futures = {
            executor.submit(build_initiator_node_summary, node): node for node in nodes
        }
        for future in as_completed(futures):
            results.append(future.result())
    order = {node: index for index, node in enumerate(nodes)}
    results.sort(key=lambda item: order.get(item.get("node", ""), 0))
    return results


def collect_initiator_mount_status_concurrently(nodes: Sequence[str]) -> List[dict]:
    if not nodes:
        return []
    results: List[dict] = []
    with ThreadPoolExecutor(max_workers=min(32, len(nodes))) as executor:
        futures = {
            executor.submit(build_initiator_mount_status, node): node for node in nodes
        }
        for future in as_completed(futures):
            results.append(future.result())
    order = {node: index for index, node in enumerate(nodes)}
    results.sort(key=lambda item: order.get(item.get("node", ""), 0))
    return results
