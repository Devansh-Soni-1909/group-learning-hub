from __future__ import annotations

import json
import os
import re
import subprocess
import shlex
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_TARGET_SELECTOR = "iscsi-target=true"
DEFAULT_INITIATOR_SELECTOR = "iscsi-role=initiator"
SAVECONFIG_PATHS = ["/etc/rtslib-fb-target/saveconfig.json", "/etc/target/saveconfig.json"]
BACKUP_PATHS = ["/etc/rtslib-fb-target/backup", "/etc/target/backup"]


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


_saveconfig_cache: Dict[str, Tuple[Optional[dict], Optional[str]]] = {}


def get_saveconfig(node: str) -> Tuple[Optional[dict], Optional[str]]:
    if node in _saveconfig_cache:
        cached_data, cached_error = _saveconfig_cache[node]
        return cached_data, cached_error

    last_error = None
    for path in SAVECONFIG_PATHS:
        cmd = f'pdsh -w {node} "sudo cat {path} 2>/dev/null"'
        output, error = run_pdsh_text(cmd)
        if error:
            last_error = error
            continue
        if not output.strip():
            last_error = f"Empty file or file not found at {path}"
            continue
        try:
            data = json.loads(output)
            _saveconfig_cache[node] = (data, None)
            return data, None
        except Exception as e:
            last_error = f"Failed to parse JSON from {path}: {e}"
            continue

    err_msg = f"{node}: unable to load saveconfig.json: {last_error}"
    _saveconfig_cache[node] = (None, err_msg)
    return None, err_msg


def _parse_path(path: str) -> Tuple[Optional[str], Optional[int]]:
    parts = path.rstrip("/").split("/")
    iqn = None
    tpgt_tag = None
    for part in parts:
        if part.startswith("iqn."):
            iqn = part
        elif part.startswith("tpgt_"):
            try:
                tpgt_tag = int(part.split("_")[1])
            except (ValueError, IndexError):
                pass
    return iqn, tpgt_tag


def list_config_versions(node: str) -> Tuple[List[str], str | None]:
    last_error = None
    for path in BACKUP_PATHS:
        cmd = f'pdsh -w {node} "sudo find {path} -maxdepth 1 -type f 2>/dev/null | sort"'
        output, error = run_pdsh_lines(cmd)
        if error:
            last_error = error
            continue
        if not output:
            last_error = f"Empty directory or No directory found at the {path}"
            continue
        return output, None
    return None, last_error


def read_backup_config_file(node: str, path: str):
    remote_reader = f"sudo gzip -dc {path}" if path.endswith(".gz") else f"sudo cat {path}"
    cmd = f'pdsh -w {node} "{remote_reader}"'
    output, error = run_pdsh_text(cmd)
    if error:
        return None, error
    if not output.strip():
        return None, f"Empty file or file not found at {path}"
    try:
        data = json.loads(output)
        return data, None
    except Exception as e:
        return None, f"Failed to parse JSON from {path}: {e}"


def list_iqns(node: str, base_path: str) -> Tuple[List[str], List[str]]:
    data, error = get_saveconfig(node)
    if error:
        return [], [error]
    iqns = []
    for target in data.get("targets", []):
        if target.get("fabric") == "iscsi" and "wwn" in target:
            iqns.append(target["wwn"])
    return iqns, []


def list_tpgts(node: str, iqn_path: str) -> Tuple[List[str], List[str]]:
    data, error = get_saveconfig(node)
    if error:
        return [], [error]
    iqn = os.path.basename(iqn_path)
    tpgts = []
    for target in data.get("targets", []):
        if target.get("fabric") == "iscsi" and target.get("wwn") == iqn:
            for tpg in target.get("tpgs", []):
                if "tag" in tpg:
                    tpgts.append(f"tpgt_{tpg['tag']}")
    return tpgts, []


def list_luns(node: str, lun_path: str) -> Tuple[List[str], List[str]]:
    data, error = get_saveconfig(node)
    if error:
        return [], [error]
    iqn, tpgt_tag = _parse_path(lun_path)
    if not iqn or tpgt_tag is None:
        return [], [f"{node}: could not parse IQN/TPGT from path: {lun_path}"]

    luns = []
    for target in data.get("targets", []):
        if target.get("fabric") == "iscsi" and target.get("wwn") == iqn:
            for tpg in target.get("tpgs", []):
                if tpg.get("tag") == tpgt_tag:
                    for lun in tpg.get("luns", []):
                        if "index" in lun:
                            luns.append(f"lun_{lun['index']}")
    return luns, []


def list_acls(node: str, acl_path: str) -> Tuple[List[str], List[str]]:
    data, error = get_saveconfig(node)
    if error:
        return [], [error]
    iqn, tpgt_tag = _parse_path(acl_path)
    if not iqn or tpgt_tag is None:
        return [], [f"{node}: could not parse IQN/TPGT from path: {acl_path}"]

    acls = []
    for target in data.get("targets", []):
        if target.get("fabric") == "iscsi" and target.get("wwn") == iqn:
            for tpg in target.get("tpgs", []):
                if tpg.get("tag") == tpgt_tag:
                    for acl in tpg.get("node_acls", []):
                        if "node_wwn" in acl:
                            acls.append(acl["node_wwn"])
    return acls, []


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
    data, error = get_saveconfig(node)
    if error:
        return "", error
    iqn, tpgt_tag = _parse_path(lun_path)
    if not iqn or tpgt_tag is None:
        return "", f"{node}: could not parse IQN/TPGT from path: {lun_path}"

    try:
        lun_index = int(lun_name.split("_")[1])
    except (ValueError, IndexError):
        return "", f"{node}: invalid LUN name format: {lun_name}"

    for target in data.get("targets", []):
        if target.get("fabric") == "iscsi" and target.get("wwn") == iqn:
            for tpg in target.get("tpgs", []):
                if tpg.get("tag") == tpgt_tag:
                    for lun in tpg.get("luns", []):
                        if lun.get("index") == lun_index:
                            storage_object = lun.get("storage_object")
                            if storage_object:
                                parts = storage_object.rstrip("/").split("/")
                                if len(parts) >= 2:
                                    plugin = parts[-2]
                                    name = parts[-1]
                                    for obj in data.get("storage_objects", []):
                                        if (
                                            obj.get("plugin") == plugin
                                            and obj.get("name") == name
                                        ):
                                            dev = obj.get("dev", "")
                                            return dev, None
                                    return (
                                        "",
                                        f"{node}: storage object not found for plugin {plugin} name {name}",
                                    )
                                return (
                                    "",
                                    f"{node}: invalid storage_object path format: {storage_object}",
                                )

    return (
        "",
        f"{node}: no storage_object found for target {iqn} TPGT {tpgt_tag} LUN {lun_index}",
    )


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


def build_snapshot(config: dict) -> dict:
    snapshot = {
        "iqns": set(),
        "tpgs": set(),
        "luns": set(),
        "acls": set(),
        "storage_objects": set(),
        "rootfs_images": set(),
        "pe_images": set(),
    }

    for obj in config.get("storage_objects", []):
        name = obj.get("name", "")
        plugin = obj.get("plugin", "")
        path = obj.get("dev", "")

        if not name and not path:
            continue

        snapshot["storage_objects"].add((plugin, name, path))
        image_type = infer_image_type(name)
        if image_type == "unknown":
            image_type = infer_image_type(path)

        if image_type == "rootfs":
            snapshot["rootfs_images"].add((name, path))
        elif image_type == "pe":
            snapshot["pe_images"].add((name, path))

    for target in config.get("targets", []):
        if target.get("fabric") != "iscsi":
            continue

        iqn = target.get("wwn")
        if not iqn:
            continue
        snapshot["iqns"].add(iqn)

        for tpg in target.get("tpgs", []):
            tag = tpg.get("tag")
            if tag is None:
                continue
            snapshot["tpgs"].add((iqn, tag))

            for lun in tpg.get("luns", []):
                snapshot["luns"].add(
                    (iqn, tag, lun.get("index"), lun.get("storage_object"))
                )

            for acl in tpg.get("node_acls", []):
                snapshot["acls"].add(
                    (
                        iqn,
                        tag,
                        acl.get("node_wwn"),
                    )
                )
    return snapshot


def compare_snapshots(
    current_snapshot: dict,
    previous_snapshot: dict,
) -> dict:
    return {
        "iqns_added": current_snapshot.get("iqns", set()) - previous_snapshot.get("iqns", set()),
        "iqns_removed": previous_snapshot.get("iqns", set()) - current_snapshot.get("iqns", set()),
        "tpgs_added": current_snapshot.get("tpgs", set()) - previous_snapshot.get("tpgs", set()),
        "tpgs_removed": previous_snapshot.get("tpgs", set()) - current_snapshot.get("tpgs", set()),
        "luns_added": current_snapshot.get("luns", set()) - previous_snapshot.get("luns", set()),
        "luns_removed": previous_snapshot.get("luns", set()) - current_snapshot.get("luns", set()),
        "acls_added": current_snapshot.get("acls", set()) - previous_snapshot.get("acls", set()),
        "acls_removed": previous_snapshot.get("acls", set()) - current_snapshot.get("acls", set()),
        "storage_objects_added": current_snapshot.get("storage_objects", set()) - previous_snapshot.get("storage_objects", set()),
        "storage_objects_removed": previous_snapshot.get("storage_objects", set()) - current_snapshot.get("storage_objects", set()),
        "rootfs_deleted": previous_snapshot.get("rootfs_images", set()) - current_snapshot.get("rootfs_images", set()),
        "pe_deleted": previous_snapshot.get("pe_images", set()) - current_snapshot.get("pe_images", set()),
    }


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
