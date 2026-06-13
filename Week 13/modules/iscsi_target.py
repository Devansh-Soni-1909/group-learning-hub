from __future__ import annotations

import re
import json
import shlex

from datetime import datetime
from typing import Dict, Tuple, Optional, List, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dataclasses import asdict

from .schemas import LunImage, TpgtInfo
from .utils import run_pdsh_text, run_pdsh_lines, parse_metric_value
from .error_collector import collect_node_diagnostics

SAVECONFIG_PATHS = [
    "/etc/rtslib-fb-target/saveconfig.json",
    "/etc/target/saveconfig.json",
]
BACKUP_PATHS = ["/etc/rtslib-fb-target/backup", "/etc/target/backup"]
TARGET_METRICS_BASE_PATH = "/sys/kernel/config/target/iscsi"


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


def list_config_versions(
    node: str,
) -> Tuple[str | None, List[Tuple[str, str]], str | None]:
    last_error = None
    current_config = None

    # Find current config
    for config_path in SAVECONFIG_PATHS:
        cmd = f'pdsh -w {node} "sudo test -f {config_path} && sudo echo {config_path}"'

        output, error = run_pdsh_lines(cmd)

        if output:
            current_config = output[0].strip()
            break

    # Find backup configs
    for backup_path in BACKUP_PATHS:
        cmd = (
            f"pdsh -w {node} "
            f'"sudo find {backup_path} -maxdepth 1 -type f 2>/dev/null | sort"'
        )

        output, error = run_pdsh_lines(cmd)

        if error:
            last_error = error
            continue

        if not output:
            last_error = f"Empty directory or No directory found at {backup_path}"
            continue

        versions: List[Tuple[str, str]] = []

        for filepath in output:
            filename = Path(filepath).name

            match = re.search(
                r"saveconfig-(\d{8})-(\d{2}:\d{2}:\d{2})",
                filename,
            )

            if match:
                dt = datetime.strptime(
                    f"{match.group(1)} {match.group(2)}",
                    "%Y%m%d %H:%M:%S",
                )
                timestamp = dt.strftime("%d %b %Y %I:%M:%S %p")
            else:
                timestamp = "Unknown"

            versions.append((filepath, timestamp))

        return current_config, versions, None

    return current_config, [], last_error


def read_backup_config_file(node: str, path: str):
    remote_reader = (
        f"sudo gzip -dc {path}" if path.endswith(".gz") else f"sudo cat {path}"
    )
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


def list_iqns(node: str) -> Tuple[List[str], List[str]]:
    data, error = get_saveconfig(node)
    if error:
        return [], [error]
    iqns = []
    for target in data.get("targets", []):
        if target.get("fabric") == "iscsi" and "wwn" in target:
            iqns.append(target["wwn"])
    return iqns, []


def list_tpgts(node: str, iqn: str) -> Tuple[List[TpgtInfo], List[str]]:
    data, error = get_saveconfig(node)
    if error:
        return [], [error]
    tpgts: List[TpgtInfo] = []
    for target in data.get("targets", []):
        if target.get("fabric") == "iscsi" and target.get("wwn") == iqn:
            for tpg in target.get("tpgs", []):
                if "tag" in tpg:
                    tag = tpg["tag"]
                    tpgts.append(TpgtInfo(tag=tag, tpgt_name=f"tpgt_{tag}"))
    return tpgts, []


def list_acls(node: str, iqn: str, tpgt_tag: int) -> Tuple[List[str], List[str]]:
    data, error = get_saveconfig(node)
    if error:
        return [], [error]

    acls = []
    for target in data.get("targets", []):
        if target.get("fabric") == "iscsi" and target.get("wwn") == iqn:
            for tpg in target.get("tpgs", []):
                if int(tpg.get("tag")) == tpgt_tag:
                    for acl in tpg.get("node_acls", []):
                        if "node_wwn" in acl:
                            acls.append(acl["node_wwn"])
    return acls, []


def list_luns(node: str, iqn: str, tpgt_tag: int) -> Tuple[List[str], List[str]]:
    data, error = get_saveconfig(node)
    if error:
        return [], [error]

    luns = []
    for target in data.get("targets", []):
        if target.get("fabric") == "iscsi" and target.get("wwn") == iqn:
            for tpg in target.get("tpgs", []):
                if int(tpg.get("tag")) == tpgt_tag:
                    for lun in tpg.get("luns", []):
                        if "index" in lun:
                            luns.append(f"lun_{lun['index']}")
    return luns, []


def get_image_udev_path(node: str, plugin: str, name: str) -> Tuple[str, List[str]]:
    data, error = get_saveconfig(node)
    if error:
        return [], [error]

    for object in data.get("storage_objects"):
        if object.get("plugin") == plugin and object.get("name") == name:
            path = object.get("dev")
            if path:
                return path, None
    return (
        None,
        f"No storage object in the {node} matches the given plugin:{plugin} and name:{name}",
    )


def infer_image_type(identity: str) -> str:
    lowered = identity.lower()
    if "rootfs" in lowered:
        return "rootfs"
    if re.search(r"(^|[^a-z])pe([^a-z]|$)", lowered) or "pe_" in lowered:
        return "pe"
    return "unknown"


def list_images(node: str, iqn: str, tpgt_tag: int) -> Tuple[List[LunImage], List[str]]:
    data, error = get_saveconfig(node)
    if error:
        return [], [error]

    images: List[LunImage] = []
    for target in data.get("targets", []):
        if target.get("fabric") == "iscsi" and target.get("wwn") == iqn:
            for tpg in target.get("tpgs", []):
                if int(tpg.get("tag")) == tpgt_tag:
                    for lun in tpg.get("luns", []):
                        if "index" in lun:
                            parts = lun.get("storage_object").rstrip("/").split("/")
                            if len(parts) >= 2:
                                plugin = parts[-2]
                                name = parts[-1]
                                udev_path, error = get_image_udev_path(
                                    node, plugin, name
                                )
                                if error:
                                    return [], [error]
                                image_name = udev_path.rstrip("").split("/")[-1]
                                image_type = infer_image_type(udev_path)
                                images.append(
                                    LunImage(
                                        node=node,
                                        iqn=iqn,
                                        tpgt_name=f"tpgt_{tpgt_tag}",
                                        lun_id=lun.get("index"),
                                        lun_name=name,
                                        image_name=image_name,
                                        image_type=image_type,
                                        object_path=lun.get("storage_object"),
                                        udev_path=udev_path,
                                        read_mbytes=0,
                                        read_iops=0,
                                    )
                                )
    return images, []


def read_lun_stats(
    node: str, iqn: str, tpgt_tag: str, lun_index: str
) -> Tuple[Dict[str, int], List[str]]:
    errors: List[str] = []
    stats_path = f"{TARGET_METRICS_BASE_PATH}/{iqn}/tpgt_{tpgt_tag}/lun/lun_{lun_index}/statistics/scsi_tgt_port"

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
        "iqns_added": current_snapshot.get("iqns", set())
        - previous_snapshot.get("iqns", set()),
        "iqns_removed": previous_snapshot.get("iqns", set())
        - current_snapshot.get("iqns", set()),
        "tpgs_added": current_snapshot.get("tpgs", set())
        - previous_snapshot.get("tpgs", set()),
        "tpgs_removed": previous_snapshot.get("tpgs", set())
        - current_snapshot.get("tpgs", set()),
        "luns_added": current_snapshot.get("luns", set())
        - previous_snapshot.get("luns", set()),
        "luns_removed": previous_snapshot.get("luns", set())
        - current_snapshot.get("luns", set()),
        "acls_added": current_snapshot.get("acls", set())
        - previous_snapshot.get("acls", set()),
        "acls_removed": previous_snapshot.get("acls", set())
        - current_snapshot.get("acls", set()),
        "storage_objects_added": current_snapshot.get("storage_objects", set())
        - previous_snapshot.get("storage_objects", set()),
        "storage_objects_removed": previous_snapshot.get("storage_objects", set())
        - current_snapshot.get("storage_objects", set()),
        "rootfs_deleted": previous_snapshot.get("rootfs_images", set())
        - current_snapshot.get("rootfs_images", set()),
        "pe_deleted": previous_snapshot.get("pe_images", set())
        - current_snapshot.get("pe_images", set()),
    }


def count_by_type(images: List[LunImage]) -> Dict[str, int]:
    counts = {"rootfs": 0, "pe": 0, "unknown": 0}
    for image in images:
        counts[image.image_type] = counts.get(image.image_type, 0) + 1
    return counts


def sum_metric(images: List[LunImage], field_name: str) -> int:
    return sum(getattr(image, field_name, 0) for image in images)


def collect_target_images(
    node: str, with_metrics: bool = False
) -> Tuple[List[LunImage], List[dict], List[str]]:
    errors: List[str] = []
    all_tpgts: List[dict] = []
    images: List[LunImage] = []

    iqns, iqn_errors = list_iqns(node)
    errors.extend(iqn_errors)

    def _collect_tpgt(
        iqn: str, tpgt_tag: int
    ) -> Tuple[dict, List[LunImage], List[str]]:
        tpgt_errors: List[str] = []
        tpgt_images: List[LunImage] = []

        luns, lun_errors = list_luns(node, iqn, tpgt_tag)
        acls, acl_errors = list_acls(node, iqn, tpgt_tag)

        tpgt_errors.extend(lun_errors)
        tpgt_errors.extend(acl_errors)

        lun_results: List[LunImage] = []
        if luns:
            lun_results, image_errors = list_images(node, iqn, tpgt_tag)
            tpgt_errors.extend(image_errors)
            if with_metrics:

                def fetch_lun_stats(lun: LunImage) -> Tuple[LunImage, dict, List[str]]:
                    data, error = read_lun_stats(
                        node,
                        iqn,
                        tpgt_tag,
                        lun.lun_id,
                    )
                    return lun, data, error

                with ThreadPoolExecutor(max_workers=10) as executor:
                    futures = [
                        executor.submit(fetch_lun_stats, lun) for lun in lun_results
                    ]
                    for future in as_completed(futures):
                        try:
                            lun, data, error = future.result()
                        except Exception as exc:
                            tpgt_errors.append(str(exc))
                            continue
                        if error:
                            tpgt_errors.extend(error)
                        if data:
                            lun.read_mbytes = data.get("read_mbytes")
                            lun.read_iops = data.get("read_iops")

        lun_results.sort(key=lambda item: (int(item.lun_id), item.lun_name))
        tpgt_images.extend(lun_results)
        tpgt_entry = {
            "node": node,
            "iqn": iqn,
            "tpgt_name": f"tpgt_{tpgt_tag}",
            "luns": [asdict(image) for image in lun_results],
            "acl_names": acls,
            "acl_count": len(acls),
            "lun_count": len(lun_results),
        }
        return tpgt_entry, tpgt_images, tpgt_errors

    for iqn in iqns:
        tpgt_info_list, tpgt_errors = list_tpgts(node, iqn)
        errors.extend(tpgt_errors)

        if not tpgt_info_list:
            continue

        tpgt_results: List[Tuple[dict, List[LunImage], List[str]]] = []
        for tpgt_info in tpgt_info_list:
            tpgt_results.append(_collect_tpgt(iqn, tpgt_info.tag))

        for tpgt_entry, tpgt_images, tpgt_errors_local in sorted(
            tpgt_results, key=lambda item: item[0]["tpgt_name"]
        ):
            all_tpgts.append(tpgt_entry)
            images.extend(tpgt_images)
            errors.extend(tpgt_errors_local)

    return images, all_tpgts, errors


def collect_target_tpgts(
    node: str, with_metrics: bool = False
) -> Tuple[List[dict], List[str]]:
    _, tpgts, errors = collect_target_images(node, with_metrics)
    return tpgts, errors


def filter_images(images: List[LunImage], image_type: str) -> List[LunImage]:
    if image_type == "all":
        return images
    return [image for image in images if image.image_type == image_type]


def snapshot_deleted_rows(delta: dict) -> List[dict]:
    deleted_rows: List[dict] = []
    for image_type, items in (
        ("rootfs", delta.get("rootfs_deleted", set())),
        ("pe", delta.get("pe_deleted", set())),
    ):
        for image_name, image_path in sorted(items):
            deleted_rows.append(
                {
                    "type": image_type,
                    "image_name": image_name or "-",
                    "path": image_path or "-",
                }
            )
    return deleted_rows


def load_backup_snapshot(
    node: str, compare_config: Optional[str]
) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
    if compare_config:
        candidate_paths = [compare_config]
        if not compare_config.startswith("/"):
            candidate_paths = [f"{base}/{compare_config}" for base in BACKUP_PATHS]

        last_error = None
        for candidate_path in candidate_paths:
            backup_config, error = read_backup_config_file(node, candidate_path)
            if backup_config is not None:
                return backup_config, None, candidate_path
            last_error = error
        return None, last_error, compare_config

    _, versions, error = list_config_versions(node)
    if error:
        return None, error, None
    if not versions:
        return None, None, None

    backup_path = versions[0][0]
    backup_config, backup_error = read_backup_config_file(node, backup_path)
    return backup_config, backup_error, backup_path


def build_target_node_summary(
    node: str,
    with_metrics: bool = False,
    compare_config: str | None = None,
) -> dict:
    images, tpgts, errors = collect_target_images(node, with_metrics)

    by_type = count_by_type(images)

    diagnostics, diagnostic_errors = collect_node_diagnostics(node)
    errors.extend(diagnostic_errors)

    summary = {
        "node": node,
        "role": "target",
        "iqns": sorted({image.iqn for image in images}),
        "tpgts": tpgts,
        "tpgt_count": len(tpgts),
        "lun_count": len(images),
        "total_active_images": len(images),
        "rootfs_count": by_type.get("rootfs", 0),
        "pe_count": by_type.get("pe", 0),
        "unknown_count": by_type.get("unknown", 0),
        "read_mbytes": sum_metric(images, "read_mbytes"),
        "read_iops": sum_metric(images, "read_iops"),
        "images": [asdict(image) for image in images],
        "diagnostics": [asdict(diagnostic) for diagnostic in diagnostics],
        "errors": errors,
        "with_metrics": with_metrics,
        "deleted_images": [],
        "comparison_summary": {},
        "comparison_source": None,
    }

    current_config, current_error = get_saveconfig(node)

    if current_error:
        summary["errors"].append(current_error)
        return summary

    backup_config, backup_error, backup_source = load_backup_snapshot(
        node,
        compare_config,
    )

    if backup_error:
        summary["errors"].append(backup_error)
        return summary

    if backup_config is None:
        return summary

    current_snapshot = build_snapshot(current_config)
    previous_snapshot = build_snapshot(backup_config)

    delta = compare_snapshots(
        current_snapshot,
        previous_snapshot,
    )

    summary["deleted_images"] = snapshot_deleted_rows(delta)

    summary["comparison_summary"] = {
        "iqns_added": len(delta["iqns_added"]),
        "iqns_removed": len(delta["iqns_removed"]),
        "tpgs_added": len(delta["tpgs_added"]),
        "tpgs_removed": len(delta["tpgs_removed"]),
        "luns_added": len(delta["luns_added"]),
        "luns_removed": len(delta["luns_removed"]),
        "acls_added": len(delta["acls_added"]),
        "acls_removed": len(delta["acls_removed"]),
        "storage_objects_added": len(delta["storage_objects_added"]),
        "storage_objects_removed": len(delta["storage_objects_removed"]),
        "rootfs_deleted": len(delta["rootfs_deleted"]),
        "pe_deleted": len(delta["pe_deleted"]),
    }

    summary["comparison_source"] = backup_source

    return summary


def collect_summaries_concurrently(
    nodes: Sequence[str], with_metrics: bool = False
) -> List[dict]:
    if not nodes:
        return []
    results: List[dict] = []
    with ThreadPoolExecutor(max_workers=min(32, len(nodes))) as executor:
        futures = {
            executor.submit(build_target_node_summary, node, with_metrics): node
            for node in nodes
        }
        for future in as_completed(futures):
            results.append(future.result())
    order = {node: index for index, node in enumerate(nodes)}
    results.sort(key=lambda item: order.get(item.get("node", ""), 0))
    return results


def build_backup_config_summary(
    node: str,
    file_path: str,
) -> Tuple[dict, str | None]:
    config_data, error = read_backup_config_file(node, file_path)
    if error:
        return {}, error

    storage_objects = config_data.get("storage_objects", [])
    targets = config_data.get("targets", [])

    iqns = []
    tpgts = []
    images = []

    rootfs_count = 0
    pe_count = 0
    unknown_count = 0

    storage_by_name = {
        storage_object["name"]: storage_object for storage_object in storage_objects
    }

    for target in targets:
        iqn = target.get("wwn", "")
        iqns.append(iqn)

        for tpgt in target.get("tpgs", []):
            tpgt_name = str(tpgt.get("tag", ""))

            tpgts.append(
                {
                    "iqn": iqn,
                    "tpgt_name": tpgt_name,
                    "lun_count": len(tpgt.get("luns", [])),
                    "acl_count": len(tpgt.get("node_acls", [])),
                    "acl_names": [
                        acl.get("node_wwn", "") for acl in tpgt.get("node_acls", [])
                    ],
                }
            )

            for lun in tpgt.get("luns", []):
                storage_path = lun.get("storage_object", "")
                storage_name = storage_path.split("/")[-1]

                storage_object = storage_by_name.get(storage_name, {})

                if storage_name.startswith("rootfs_"):
                    image_type = "rootfs"
                    rootfs_count += 1
                elif storage_name.startswith("pe_"):
                    image_type = "pe"
                    pe_count += 1
                else:
                    image_type = "unknown"
                    unknown_count += 1

                images.append(
                    {
                        "iqn": iqn,
                        "tpgt_name": tpgt_name,
                        "lun_name": str(lun.get("index", "")),
                        "image_type": image_type,
                        "image_name": storage_name,
                        "udev_path": storage_object.get("dev", ""),
                        "read_mbytes": "N/A",
                        "read_iops": "N/A",
                    }
                )

    summary = {
        "node": node,
        "role": "target",
        "config_file": Path(file_path).name,
        "file_path": file_path,
        "iqns": sorted(set(iqns)),
        "tpgts": tpgts,
        "tpgt_count": len(tpgts),
        "lun_count": len(images),
        "total_active_images": len(images),
        "rootfs_count": rootfs_count,
        "pe_count": pe_count,
        "unknown_count": unknown_count,
        "images": images,
        "with_metrics": False,
        "errors": [],
    }
    return summary, None
