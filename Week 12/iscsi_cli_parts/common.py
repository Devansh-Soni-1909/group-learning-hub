from __future__ import annotations

import json
import os
import re
import subprocess
import shlex
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_TARGET_SELECTOR = "iscsi-target=true"
DEFAULT_INITIATOR_SELECTOR = "iscsi-role=initiator"
DEFAULT_BASE_PATH = "/sys/kernel/config/target/iscsi"
DEFAULT_STATE_FILE = Path.cwd() / ".cache" / "iscsi-metrics" / "state.json"


@dataclass
class LunImage:
    node: str
    iqn: str
    tpgt_name: str
    lun_id: str
    lun_name: str
    image_name: str
    image_type: str
    udev_path: str
    object_path: str
    read_mbytes: int
    read_iops: int


@dataclass
class NodeErrorReport:
    node: str
    source: str
    severity: str
    message: str


def run_command(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(command, shell=True, capture_output=True, text=True)


def _clean_pdsh_output(output: str) -> List[str]:
    lines: List[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ": " in line:
            line = line.split(": ", 1)[1].strip()
        if line:
            lines.append(line)
    return lines


def run_pdsh_lines(command: str) -> Tuple[List[str], Optional[str]]:
    result = run_command(command)
    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        return [], message
    return _clean_pdsh_output(result.stdout), None


def run_pdsh_text(command: str) -> Tuple[str, Optional[str]]:
    lines, error = run_pdsh_lines(command)
    if error:
        return "", error
    return "\n".join(lines).strip(), None


def _parse_pdsh_output_by_node(
    output: str, expected_nodes: Optional[Sequence[str]] = None
) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    expected = set(expected_nodes) if expected_nodes else None
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line or ": " not in line:
            continue
        node, text = line.split(": ", 1)
        node = node.strip()
        if expected is not None and node not in expected:
            continue
        grouped.setdefault(node, []).append(text.strip())
    return grouped


def run_pdsh_text_by_node(
    nodes: Sequence[str], remote_command: str
) -> Tuple[Dict[str, str], Optional[str]]:
    normalized = [node.strip() for node in nodes if node and node.strip()]
    if not normalized:
        return {}, None

    node_expr = ",".join(sorted(set(normalized)))
    result = run_command(f'pdsh -w {node_expr} "{remote_command}"')
    grouped = _parse_pdsh_output_by_node(result.stdout, normalized)
    payload = {
        node: "\n".join(lines).strip()
        for node, lines in grouped.items()
        if "\n".join(lines).strip()
    }

    if result.returncode != 0 and not payload:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        return {}, message

    return payload, None


def run_kubectl_json(command: str) -> Tuple[dict, Optional[str]]:
    result = run_command(command)
    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        return {}, message
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON output: {exc}"


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
    return [node for node in output.split() if node], None


def get_node_json(node_name: str) -> Tuple[dict, Optional[str]]:
    return run_kubectl_json(f"kubectl get node {node_name} -o json")


def get_node_labels(node_name: str) -> Tuple[Dict[str, str], Optional[str]]:
    payload, error = get_node_json(node_name)
    if error:
        return {}, error
    labels = payload.get("metadata", {}).get("labels", {})
    if not isinstance(labels, dict):
        return {}, f"{node_name}: labels were not a mapping"
    return labels, None


def detect_node_role(labels: Dict[str, str]) -> str:
    target_value = str(labels.get("iscsi-target", "")).lower()
    role_value = str(labels.get("iscsi-role", "")).lower()
    initiator_value = str(labels.get("iscsi-initiator", "")).lower()
    if target_value in {"true", "yes", "1", "enabled"}:
        return "target"
    if role_value == "initiator" or initiator_value in {"true", "yes", "1", "enabled"}:
        return "initiator"
    return "unknown"


def list_iqns(node: str, base_path: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    iqns, error = run_pdsh_lines(
        f"pdsh -w {node} \"find {base_path} -mindepth 1 -maxdepth 1 -type d -name 'iqn.*' -printf '%f\\n'\""
    )
    if error:
        errors.append(f"{node}: unable to list targets: {error}")
        return [], errors
    return iqns, errors


def list_tpgts(node: str, iqn_path: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    tpgts, error = run_pdsh_lines(
        f"pdsh -w {node} \"find {iqn_path} -mindepth 1 -maxdepth 1 -type d -name 'tpgt_*' -printf '%f\\n'\""
    )
    if error:
        errors.append(f"{node}: unable to list TPGTs under {iqn_path}: {error}")
        return [], errors
    return tpgts, errors


def list_luns(node: str, lun_path: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    luns, error = run_pdsh_lines(
        f"pdsh -w {node} \"find {lun_path} -mindepth 1 -maxdepth 1 -type d -name 'lun_*' -printf '%f\\n'\""
    )
    if error:
        errors.append(f"{node}: unable to list LUNs under {lun_path}: {error}")
        return [], errors
    return luns, errors


def list_acls(node: str, acl_path: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    acls, error = run_pdsh_lines(
        f"pdsh -w {node} \"find {acl_path} -mindepth 1 -maxdepth 1 -type d -printf '%f\\n'\""
    )
    if error:
        errors.append(f"{node}: unable to list ACLs under {acl_path}: {error}")
        return [], errors
    return acls, errors


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
    commands = [
        (
            f'pdsh -w {node} "find {lun_path}/{lun_name} -mindepth 1 -maxdepth 1 '
            '-type l -exec readlink -f {} \\; 2>/dev/null | grep /target/core/ | head -n1"'
        ),
        (
            f'pdsh -w {node} "readlink -f {lun_path}/{lun_name}/* 2>/dev/null | '
            'grep /target/core/ | head -n1"'
        ),
    ]

    last_error: Optional[str] = None
    for command in commands:
        output, error = run_pdsh_text(command)
        if output:
            return output, None
        if error:
            last_error = error

    if last_error:
        return "", last_error
    return "", f"{node}: no target core object link found for {lun_name}"


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

    remote_script = (
        f"mbytes=$(cat {stats_path}/read_mbytes 2>/dev/null); "
        f"iops=$(cat {stats_path}/in_cmds 2>/dev/null); "
        'printf "read_mbytes=%s\\nread_iops=%s\\n" "$mbytes" "$iops"'
    )
    command = f"pdsh -w {node} {shlex.quote(f'sh -c {shlex.quote(remote_script)}')}"

    def _parse_metrics(output_text: str) -> Tuple[str, str]:
        metric_map: Dict[str, str] = {}
        for line in output_text.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            metric_map[key.strip()] = value.strip()
        return metric_map.get("read_mbytes", ""), metric_map.get("read_iops", "")

    output, read_error = run_pdsh_text(command)
    read_mbytes_output, read_iops_output = _parse_metrics(output)

    if not read_mbytes_output or not read_iops_output:
        retry_output, retry_error = run_pdsh_text(command)
        if retry_output:
            retry_mbytes, retry_iops = _parse_metrics(retry_output)
            if retry_mbytes:
                read_mbytes_output = retry_mbytes
            if retry_iops:
                read_iops_output = retry_iops
        if read_error is None:
            read_error = retry_error

    if read_error:
        errors.append(f"{node}: unable to read {stats_path}: {read_error}")

    if read_mbytes_output == "":
        errors.append(f"{node}: missing read_mbytes value under {stats_path}")
    if read_iops_output == "":
        errors.append(f"{node}: missing read_iops value under {stats_path}")

    return {
        "read_mbytes": parse_metric_value(read_mbytes_output),
        "read_iops": parse_metric_value(read_iops_output),
    }, errors


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
    state: dict,
    current_snapshots: Dict[str, Dict[str, dict]],
    nodes: Sequence[str],
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


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(str(value)))

    def render_row(values: Sequence[str]) -> str:
        return " | ".join(
            str(value).ljust(widths[index]) for index, value in enumerate(values)
        )

    separator = "-+-".join("-" * width for width in widths)
    lines = [render_row(headers), separator]
    lines.extend(render_row(row) for row in rows)
    return "\n".join(lines)


def emit_output(payload: dict, as_json: bool, formatter=None) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(formatter(payload) if formatter else str(payload))
