#!/usr/bin/env python3

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_NODE_SELECTOR = "iscsi-target=true"
DEFAULT_INITIATOR_SELECTOR = "iscsi-role=initiator"
DEFAULT_BASE_PATH = "/sys/kernel/config/target/iscsi"
DEFAULT_STATE_FILE = Path(__file__).with_name("collect-iscsi-metrics-state.json")

ERROR_PATTERNS = [
    {
        "pattern": r"connection.*lost",
        "severity": "warning",
        "source": "iscsi",
    },
    {
        "pattern": r"session.*failure",
        "severity": "warning",
        "source": "iscsi",
    },
    {
        "pattern": r"iscsi.*timed out",
        "severity": "warning",
        "source": "network",
    },
    {
        "pattern": r"blocked for more than",
        "severity": "critical",
        "source": "kernel",
    },
    {
        "pattern": r"I/O error",
        "severity": "critical",
        "source": "storage",
    },
    {
        "pattern": r"rejecting I/O to offline device",
        "severity": "critical",
        "source": "storage",
    },
    {
         "pattern": r"blk_update_request",
         "severity": "critical",
         "source": "storage",
    },
    {
         "pattern": r"Buffer I/O error",
         "severity": "critical",
         "source": "storage",
    },
    {
         "pattern": r"link is down",
         "severity": "warning",
         "source": "network",
    },
    {
         "pattern": r"task .* hung",
         "severity": "critical",
         "source": "kernel",
    },
    {
         "pattern": r"multipath.*faulty",
         "severity": "critical",
         "source": "multipath",
    },
]

@dataclass
class LunImage:
    node: str
    iqn: str
    lun_id: str
    lun_name: str
    image_name: str
    image_type: str
    udev_path: str
    object_path: str
    read_mbytes: int
    in_cmds: int

@dataclass
class NodeErrorReport:
    node: str
    source: str
    severity: str
    message: str

def run_command(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(command, shell=True, capture_output=True, text=True)


def run_pdsh_lines(command: str) -> Tuple[List[str], Optional[str]]:
    result = run_command(command)
    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        return [], message

    lines: List[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ": " in line:
            line = line.split(": ", 1)[1].strip()
        lines.append(line)
    return lines, None


def run_pdsh_text(command: str) -> Tuple[str, Optional[str]]:
    result = run_command(command)
    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        return "", message

    cleaned_lines: List[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ": " in line:
            line = line.split(": ", 1)[1].strip()
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip(), None

def collect_recent_logs(
    node: str,
    lines: int = 200,
) -> Tuple[str, Optional[str]]:
    commands = [
        f"journalctl -n {lines} --no-pager 2>/dev/null",
        f"tail -n {lines} /var/log/messages 2>/dev/null",
        f"tail -n {lines} /var/log/syslog 2>/dev/null",
        f"dmesg | tail -n {lines}",
    ]

    for command in commands:
        output, error = run_pdsh_text(
            f'pdsh -w {node} "{command}"'
        )

        if output:
            return output, None

    return "", f"{node}: unable to collect logs"

def scan_logs_for_errors(
    node: str,
    log_text: str,
) -> List[NodeErrorReport]:
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

def collect_node_diagnostics(
    node: str,
) -> Tuple[List[NodeErrorReport], List[str]]:
    errors: List[str] = []

    logs, log_error = collect_recent_logs(node)

    if log_error:
        return [], [log_error]

    findings = scan_logs_for_errors(node, logs)

    return findings, errors

def get_target_nodes(node_selector: str) -> Tuple[List[str], Optional[str]]:
    command = (
        "kubectl get nodes "
        f"-l {node_selector} "
        "-o jsonpath='{.items[*].metadata.name}'"
    )
    result = run_command(command)
    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        return [], message

    output = result.stdout.replace("'", "").strip()
    nodes = [node for node in output.split() if node]
    return nodes, None


def list_iqns(node: str, base_path: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    iqns, error = run_pdsh_lines(
        f"pdsh -w {node} \"find {base_path} -mindepth 1 -maxdepth 1 -type d -name 'iqn.*' -printf '%f\\n'\""
    )
    if error:
        errors.append(f"{node}: unable to list targets: {error}")
        return [], errors

    filtered: List[str] = []
    for iqn in iqns:
        check_output, check_error = run_pdsh_text(
            f'pdsh -w {node} "test -d {base_path}/{iqn}/tpgt_1 && echo exists"'
        )
        if check_error:
            errors.append(f"{node}: unable to validate target {iqn}: {check_error}")
            continue
        if "exists" in check_output:
            filtered.append(iqn)
    return filtered, errors


def list_luns(node: str, lun_path: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    luns, error = run_pdsh_lines(
        f"pdsh -w {node} \"find {lun_path} -mindepth 1 -maxdepth 1 -type d -name 'lun_*' -printf '%f\\n'\""
    )
    if error:
        errors.append(f"{node}: unable to list LUNs under {lun_path}: {error}")
        return [], errors
    return luns, errors


def parse_metric_value(raw_output: str) -> int:
    if not raw_output:
        return 0

    match = re.search(r"(-?\d+)\s*$", raw_output)
    if not match:
        return 0

    try:
        return int(match.group(1))
    except ValueError:
        return 0


def resolve_object_path(
    node: str, lun_path: str, lun_name: str
) -> Tuple[str, Optional[str]]:
    command = (
        f'pdsh -w {node} "readlink -f {lun_path}/{lun_name}/* 2>/dev/null | '
        'grep /target/core/ | head -n1"'
    )
    output, error = run_pdsh_text(command)
    if error:
        return "", error
    return output, None


def read_udev_path(node: str, object_path: str) -> Tuple[str, Optional[str]]:
    if not object_path:
        return "", None

    command = f'pdsh -w {node} "cat {object_path}/udev_path 2>/dev/null"'
    output, error = run_pdsh_text(command)
    if error:
        return "", error
    return output, None


def infer_image_type(identity: str) -> str:
    lowered = identity.lower()
    if "rootfs" in lowered:
        return "rootfs"
    if re.search(r"(^|[^a-z])pe([^a-z]|$)", lowered) or "pe_" in lowered:
        return "pe"
    return "unknown"


def read_lun_stats(
    node: str, lun_path: str, lun_name: str
) -> Tuple[Dict[str, int], List[str]]:
    errors: List[str] = []
    stats_path = f"{lun_path}/{lun_name}/statistics/scsi_tgt_port"

    read_mbytes_output, read_error = run_pdsh_text(
        f'pdsh -w {node} "cat {stats_path}/read_mbytes 2>/dev/null"'
    )
    if read_error:
        errors.append(f"{node}: unable to read {stats_path}/read_mbytes: {read_error}")

    in_cmds_output, in_error = run_pdsh_text(
        f'pdsh -w {node} "cat {stats_path}/in_cmds 2>/dev/null"'
    )
    if in_error:
        errors.append(f"{node}: unable to read {stats_path}/in_cmds: {in_error}")

    return (
        {
            "read_mbytes": parse_metric_value(read_mbytes_output),
            "in_cmds": parse_metric_value(in_cmds_output),
        },
        errors,
    )

def collect_initiator_metrics(node: str) -> Tuple[Dict[str, int], List[str]]:
    errors: List[str] = []

    output, error = run_pdsh_text(
        f'pdsh -w {node} "lsblk -o MOUNTPOINT,TRAN --noheadings | grep iscsi"'
    )

    if error:
        errors.append(f"{node}: unable to collect initiator metrics: {error}")
        return {"total": 0, "mounted": 0, "unmounted": 0}, errors

    lines = [line for line in output.splitlines() if line.strip()]

    total = len(lines)
    mounted = sum(1 for line in lines if line.strip().startswith("/"))
    unmounted = total - mounted

    return {
        "total": total,
        "mounted": mounted,
        "unmounted": unmounted,
    }, errors
def collect_node_images(
    node: str, base_path: str
) -> Tuple[List[LunImage], List[str], List[str]]:
    errors: List[str] = []
    iqns, iqn_errors = list_iqns(node, base_path)
    errors.extend(iqn_errors)

    images: List[LunImage] = []
    for iqn in iqns:
        lun_path = f"{base_path}/{iqn}/tpgt_1/lun"
        luns, lun_errors = list_luns(node, lun_path)
        errors.extend(lun_errors)

        for lun_name in luns:
            lun_id_match = re.search(r"lun_(\d+)", lun_name)
            if not lun_id_match:
                continue

            lun_id = lun_id_match.group(1)
            object_path, object_error = resolve_object_path(node, lun_path, lun_name)
            if object_error:
                errors.append(
                    f"{node}: unable to resolve object for {iqn}/{lun_name}: {object_error}"
                )

            udev_path, udev_error = read_udev_path(node, object_path)
            if udev_error:
                errors.append(
                    f"{node}: unable to read udev_path for {iqn}/{lun_name}: {udev_error}"
                )

            identity_source = udev_path or object_path or f"{iqn}:{lun_name}"
            image_name = Path(identity_source).name if identity_source else lun_name
            image_type = infer_image_type(identity_source or image_name)
            stats, stat_errors = read_lun_stats(node, lun_path, lun_name)
            errors.extend(stat_errors)

            images.append(
                LunImage(
                    node=node,
                    iqn=iqn,
                    lun_id=lun_id,
                    lun_name=lun_name,
                    image_name=image_name,
                    image_type=image_type,
                    udev_path=udev_path or object_path or identity_source,
                    object_path=object_path,
                    read_mbytes=stats["read_mbytes"],
                    in_cmds=stats["in_cmds"],
                )
            )

    return images, iqns, errors


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "generated_at": None, "nodes": {}}

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, dict):
                data.setdefault("version", 1)
                data.setdefault("generated_at", None)
                data.setdefault("nodes", {})
                return data
    except (OSError, json.JSONDecodeError):
        pass

    return {"version": 1, "generated_at": None, "nodes": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def reset_state_file(path: Path) -> None:
    if path.exists():
        path.unlink()


def snapshot_images_by_node(images: List[LunImage]) -> Dict[str, Dict[str, dict]]:
    snapshot: Dict[str, Dict[str, dict]] = {}
    for image in images:
        snapshot.setdefault(image.node, {})[image.udev_path] = asdict(image)
    return snapshot


def update_state_and_get_deleted(
    state: dict, current_snapshots: Dict[str, Dict[str, dict]], nodes: List[str]
) -> Dict[str, List[dict]]:
    deleted_by_node: Dict[str, List[dict]] = {}
    nodes_state = state.setdefault("nodes", {})
    now = datetime.now(timezone.utc).isoformat()

    for node in nodes:
        current_images = current_snapshots.get(node, {})
        previous_state = nodes_state.get(node, {})
        previous_images = {}
        deleted_history: List[dict] = []

        if isinstance(previous_state, dict):
            previous_images = previous_state.get("previous_images", {}) or {}
            deleted_history = list(previous_state.get("deleted_history", []))

        removed_keys = sorted(set(previous_images) - set(current_images))
        removed_images: List[dict] = []
        for key in removed_keys:
            record = previous_images.get(key, {})
            if isinstance(record, dict):
                removed_images.append(record)

        deleted_history.extend(removed_images)
        deleted_by_node[node] = removed_images

        nodes_state[node] = {
            "previous_images": current_images,
            "deleted_history": deleted_history,
            "last_seen": now,
        }

    return deleted_by_node


def count_by_type(images: List[LunImage]) -> Dict[str, int]:
    counts = {"rootfs": 0, "pe": 0, "unknown": 0}
    for image in images:
        counts[image.image_type] = counts.get(image.image_type, 0) + 1
    return counts


def sum_metric(images: List[LunImage], field_name: str) -> int:
    return sum(getattr(image, field_name, 0) for image in images)


def render_table(headers: List[str], rows: List[List[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(str(value)))

    def render_row(values: List[str]) -> str:
        return " | ".join(
            str(value).ljust(widths[index]) for index, value in enumerate(values)
        )

    separator = "-+-".join("-" * width for width in widths)
    lines = [render_row(headers), separator]
    lines.extend(render_row(row) for row in rows)
    return "\n".join(lines)


def build_report(
    nodes: List[str],
    node_results: List[dict],
    deleted_by_node: Dict[str, List[dict]],
    state_path: Path,
    errors: Dict[str, str],
    initiator_stats: Dict[str, Dict],
) -> dict:
    summaries: List[dict] = []
    deleted_images: List[dict] = []
    for node_result in node_results:
        images = node_result["images"]
        by_type = count_by_type(images)
        deleted_images_for_node = deleted_by_node.get(node_result["node"], [])

        summaries.append(
            {
                "node": node_result["node"],
                "iqns": node_result["iqns"],
                "total_active_images": len(images),
                "rootfs_count": by_type.get("rootfs", 0),
                "pe_count": by_type.get("pe", 0),
                "deleted_rootfs": sum(
                    1
                    for image in deleted_images_for_node
                    if image.get("image_type") == "rootfs"
                ),
                "deleted_pe": sum(
                    1
                    for image in deleted_images_for_node
                    if image.get("image_type") == "pe"
                ),
                "read_mbytes": sum_metric(images, "read_mbytes"),
                "in_cmds": sum_metric(images, "in_cmds"),
                "images": [asdict(image) for image in images],
                "diagnostics": [
                  asdict(diagnostic)
                  for diagnostic in node_result.get("diagnostics", [])
                ],
            }
        )

        for image in deleted_images_for_node:
            deleted_images.append({"node": node_result["node"], **image})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "nodes_summary": summaries,
        "deleted_images": deleted_images,
        "errors": errors,
        "initiator_stats": initiator_stats,
        "state_file": str(state_path),
        "defaults": {
            "node_selector": DEFAULT_NODE_SELECTOR,
            "base_path": DEFAULT_BASE_PATH,
            "state_file": str(DEFAULT_STATE_FILE),
        },
    }


def format_report(report: dict) -> str:
    lines: List[str] = []
    lines.append("iSCSI Metrics")
    lines.append("=" * 96)
    lines.append(f"State file: {report['state_file']}")
    lines.append(
        "Target nodes: " + (", ".join(report["nodes"]) if report["nodes"] else "None")
    )
    lines.append("")

    lines.append("Configured iSCSI target nodes")
    if report["nodes_summary"]:
        for item in report["nodes_summary"]:
            iqn_list = ", ".join(item["iqns"]) if item["iqns"] else "None"
            lines.append(f"- {item['node']}: {iqn_list}")
    else:
        lines.append("None")
    lines.append("")

    summary_rows: List[List[str]] = []
    for item in report["nodes_summary"]:
        summary_rows.append(
            [
                item["node"],
                ", ".join(item["iqns"]) if item["iqns"] else "None",
                str(item["total_active_images"]),
                str(item["rootfs_count"]),
                str(item["pe_count"]),
                str(item["deleted_rootfs"]),
                str(item["deleted_pe"]),
            ]
        )

    lines.append("Projected image summary")
    lines.append(
        render_table(
            [
                "Node",
                "Targets",
                "Total",
                "Rootfs",
                "PE",
                "Deleted Rootfs",
                "Deleted PE",
            ],
            summary_rows,
        )
        if summary_rows
        else "No iSCSI target nodes found."
    )
    lines.append("")

    total_active = sum(item["total_active_images"] for item in report["nodes_summary"])
    total_rootfs = sum(item["rootfs_count"] for item in report["nodes_summary"])
    total_pe = sum(item["pe_count"] for item in report["nodes_summary"])
    total_deleted_rootfs = sum(
        item["deleted_rootfs"] for item in report["nodes_summary"]
    )
    total_deleted_pe = sum(item["deleted_pe"] for item in report["nodes_summary"])

    lines.append(
        f"Cluster totals: total={total_active}, rootfs={total_rootfs}, pe={total_pe}, "
        f"deleted_rootfs={total_deleted_rootfs}, deleted_pe={total_deleted_pe}"
    )
    lines.append("")

    lines.append("LUN level read I/O metrics per worker node")
    if report["nodes_summary"]:
        for item in report["nodes_summary"]:
            lines.append(f"{item['node']}")
            lun_rows: List[List[str]] = []
            for image in item["images"]:
                lun_rows.append(
                    [
                        image["iqn"],
                        image["lun_name"],
                        image["image_type"],
                        image["image_name"],
                        image["udev_path"],
                        str(image["read_mbytes"]),
                        str(image["in_cmds"]),
                    ]
                )
            if lun_rows:
                lines.append(
                    render_table(
                        [
                            "IQN",
                            "LUN",
                            "Type",
                            "Image",
                            "udev_path",
                            "Read MBytes",
                            "In Cmds",
                        ],
                        lun_rows,
                    )
                )
            else:
                lines.append("No active LUNs found.")
            lines.append("")
    else:
        lines.append("No target nodes found.")
        lines.append("")
    lines.append("Detected storage/network errors")
    
    diagnostic_rows: List[List[str]] = []
    
    for item in report["nodes_summary"]:
        for diagnostic in item.get("diagnostics", []):
            diagnostic_rows.append(
                [
                    diagnostic["node"],
                    diagnostic["severity"],
                    diagnostic["source"],
                    diagnostic["message"][:120],
                ]
            )
    
    if diagnostic_rows:
        lines.append(
            render_table(
                ["Node", "Severity", "Source", "Message"],
                diagnostic_rows,
            )
        )
    else:
        lines.append("No storage/network errors detected.")
    
    lines.append("")
    
    lines.append("Deleted PE/rootfs images since the last run")
    if report["deleted_images"]:
        deleted_rows = [
            [
                image["node"],
                image.get("image_type", "unknown"),
                image.get("image_name", "-"),
                image.get("udev_path", "-"),
            ]
            for image in report["deleted_images"]
        ]
        lines.append(render_table(["Node", "Type", "Image", "udev_path"], deleted_rows))
    else:
        lines.append("None")

        lines.append("")
    lines.append("Initiator node mount status")

    initiator_stats = report.get("initiator_stats", {})

    if initiator_stats:
        initiator_rows = [
            [
                node,
                str(stats["total"]),
                str(stats["mounted"]),
                str(stats["unmounted"]),
            ]
            for node, stats in initiator_stats.items()
        ]

        lines.append(
            render_table(
                ["Node", "Total", "Mounted", "Unmounted"],
                initiator_rows,
            )
        )
    else:
        lines.append("None")
    if report["errors"]:
        lines.append("")
        lines.append("Warnings")
        for node, message in report["errors"].items():
            lines.append(f"- {node}: {message}")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect iSCSI target metrics from configfs on the target nodes"
    )
    parser.add_argument(
        "--node-selector",
        default=DEFAULT_NODE_SELECTOR,
        help="Kubernetes label selector used to discover iSCSI target nodes",
    )
    parser.add_argument(
    "--initiator-selector",
    default=DEFAULT_INITIATOR_SELECTOR,
    help="Kubernetes label selector used to discover iSCSI initiator nodes",
    )
    parser.add_argument(
        "--base-path",
        default=DEFAULT_BASE_PATH,
        help="Base configfs path containing the iSCSI target tree",
    )
    parser.add_argument(
        "--state-file",
        default=str(DEFAULT_STATE_FILE),
        help="JSON file used to store the previous image snapshot",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full report as JSON instead of text tables",
    )
    parser.add_argument(
        "--no-state-update",
        action="store_true",
        help="Do not write the updated image snapshot back to the state file",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Clear the state file before collecting metrics",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    state_path = Path(args.state_file).expanduser()
    if args.reset_state:
        reset_state_file(state_path)

    nodes, node_error = get_target_nodes(args.node_selector)
    errors: Dict[str, str] = {}
    if node_error:
        errors["cluster"] = node_error

    node_results: List[dict] = []
    current_snapshots: Dict[str, Dict[str, dict]] = {}

    for node in nodes:
        images, iqns, collection_errors = collect_node_images(
            node,
            args.base_path,
        )

        diagnostics, diagnostic_errors = collect_node_diagnostics(node)
        collection_errors.extend(diagnostic_errors)

        if collection_errors:
            errors[node] = "; ".join(collection_errors)
        current_snapshots[node] = snapshot_images_by_node(images).get(node, {})
        node_results.append(
          {
            "node": node,
            "iqns": iqns,
            "images": images,
            "diagnostics": diagnostics,
          }
        )
        initiator_nodes, initiator_error = get_target_nodes(
        args.initiator_selector
    )

    if initiator_error:
        errors["initiator_cluster"] = initiator_error

    initiator_stats: Dict[str, Dict] = {}

    for initiator_node in initiator_nodes:
        stats, initiator_errors = collect_initiator_metrics(
            initiator_node
        )

        initiator_stats[initiator_node] = stats

        if initiator_errors:
            errors[initiator_node] = "; ".join(initiator_errors)
    state = load_state(state_path)
    deleted_by_node = update_state_and_get_deleted(state, current_snapshots, nodes)
    state["generated_at"] = datetime.now(timezone.utc).isoformat()

    if not args.no_state_update:
        save_state(state_path, state)

    report = build_report(
    nodes,
    node_results,
    deleted_by_node,
    state_path,
    errors,
    initiator_stats,
)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
