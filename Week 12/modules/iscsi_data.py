from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from pathlib import Path

from .common import (
    DEFAULT_INITIATOR_SELECTOR,
    DEFAULT_TARGET_SELECTOR,
    LunImage,
    build_snapshot,
    count_by_type,
    compare_snapshots,
    detect_node_role,
    emit_output,
    BACKUP_PATHS,
    get_node_labels,
    get_saveconfig,
    get_target_nodes,
    infer_image_type,
    list_config_versions,
    list_acls,
    list_iqns,
    list_luns,
    list_tpgts,
    parse_metric_value,
    read_lun_stats,
    read_backup_config_file,
    render_table,
    resolve_object_path,
    sum_metric,
)
from .error_reporting import collect_node_diagnostics
from .initiator_configuration import (
    build_initiator_mount_status,
    build_initiator_node_summary,
)

CONFIGFS_TARGET_PATH = "/sys/kernel/config/target/iscsi"


def collect_target_images(
    node: str, base_path: str
) -> Tuple[List[LunImage], List[dict], List[str]]:
    errors: List[str] = []
    tpgts: List[dict] = []
    images: List[LunImage] = []
    iqns, iqn_errors = list_iqns(node, base_path)
    errors.extend(iqn_errors)

    def _collect_lun_image(
        iqn: str, tpgt_name: str, lun_path: str, lun_name: str
    ) -> Tuple[Optional[LunImage], List[str]]:
        lun_errors: List[str] = []
        lun_id_match = re.search(r"lun_(\d+)", lun_name)
        if not lun_id_match:
            return None, lun_errors

        lun_id = lun_id_match.group(1)
        object_path, object_error = resolve_object_path(node, lun_path, lun_name)
        if object_error:
            lun_errors.append(
                f"{node}: unable to resolve object for {iqn}/{tpgt_name}/{lun_name}: {object_error}"
            )

        udev_path = object_path

        stats, stat_errors = read_lun_stats(node, lun_path, lun_name)

        lun_errors.extend(stat_errors)
        identity_source = udev_path or object_path or f"{iqn}:{tpgt_name}:{lun_name}"
        image_name = Path(identity_source).name if identity_source else lun_name
        image_type = infer_image_type(identity_source or image_name)

        image = LunImage(
            node=node,
            iqn=iqn,
            tpgt_name=tpgt_name,
            lun_id=lun_id,
            lun_name=lun_name,
            image_name=image_name,
            image_type=image_type,
            udev_path=udev_path or object_path or identity_source,
            object_path=object_path,
            read_mbytes=stats["read_mbytes"],
            read_iops=stats["read_iops"],
        )
        return image, lun_errors

    def _collect_tpgt(
        iqn: str, iqn_path: str, tpgt_name: str
    ) -> Tuple[dict, List[LunImage], List[str]]:
        tpgt_errors: List[str] = []
        tpgt_images: List[LunImage] = []
        tpgt_path = f"{iqn_path}/{tpgt_name}"
        lun_path = f"{tpgt_path}/lun"

        with ThreadPoolExecutor(max_workers=2) as executor:
            luns_future = executor.submit(list_luns, node, lun_path)
            acls_future = executor.submit(list_acls, node, f"{tpgt_path}/acls")
            luns, lun_errors = luns_future.result()
            acls, acl_errors = acls_future.result()

        tpgt_errors.extend(lun_errors)
        tpgt_errors.extend(acl_errors)

        lun_results: List[LunImage] = []
        if luns:
            with ThreadPoolExecutor(max_workers=min(6, len(luns))) as executor:
                future_map = {
                    executor.submit(
                        _collect_lun_image, iqn, tpgt_name, lun_path, lun_name
                    ): lun_name
                    for lun_name in luns
                }
                for future in as_completed(future_map):
                    image, lun_image_errors = future.result()
                    tpgt_errors.extend(lun_image_errors)
                    if image is not None:
                        lun_results.append(image)

        lun_results.sort(key=lambda item: (int(item.lun_id), item.lun_name))
        tpgt_images.extend(lun_results)
        tpgt_entry = {
            "node": node,
            "iqn": iqn,
            "tpgt_name": tpgt_name,
            "luns": [asdict(image) for image in lun_results],
            "acl_names": acls,
            "acl_count": len(acls),
            "lun_count": len(lun_results),
        }
        return tpgt_entry, tpgt_images, tpgt_errors

    for iqn in iqns:
        iqn_path = f"{base_path}/{iqn}"
        tpgt_names, tpgt_errors = list_tpgts(node, iqn_path)
        errors.extend(tpgt_errors)

        if not tpgt_names:
            continue

        tpgt_results: List[Tuple[dict, List[LunImage], List[str]]] = []
        with ThreadPoolExecutor(max_workers=min(16, len(tpgt_names))) as executor:
            future_map = {
                executor.submit(_collect_tpgt, iqn, iqn_path, tpgt_name): tpgt_name
                for tpgt_name in tpgt_names
            }
            for future in as_completed(future_map):
                tpgt_results.append(future.result())

        for tpgt_entry, tpgt_images, tpgt_errors_local in sorted(
            tpgt_results, key=lambda item: item[0]["tpgt_name"]
        ):
            tpgts.append(tpgt_entry)
            images.extend(tpgt_images)
            errors.extend(tpgt_errors_local)

    return images, tpgts, errors


def collect_target_tpgts(node: str, base_path: str) -> Tuple[List[dict], List[str]]:
    _, tpgts, errors = collect_target_images(node, base_path)
    return tpgts, errors


def _filter_images(images: List[LunImage], image_type: str) -> List[LunImage]:
    if image_type == "all":
        return images
    return [image for image in images if image.image_type == image_type]


def _snapshot_deleted_rows(delta: dict) -> List[dict]:
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


def _load_backup_snapshot(
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

    versions, error = list_config_versions(node)
    if error:
        return None, error, None
    if not versions:
        return None, None, None

    backup_path = versions[0]
    backup_config, backup_error = read_backup_config_file(node, backup_path)
    return backup_config, backup_error, backup_path


def build_target_node_summary(node: str, base_path: str = CONFIGFS_TARGET_PATH) -> dict:
    images, tpgts, errors = collect_target_images(node, base_path)
    by_type = count_by_type(images)
    diagnostics, diagnostic_errors = collect_node_diagnostics(node)
    errors.extend(diagnostic_errors)
    return {
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
    }


def summarize_requested_node(
    node_name: str, base_path: str
) -> Tuple[dict, Optional[str]]:
    labels, label_error = get_node_labels(node_name)
    role = detect_node_role(labels) if labels else "unknown"
    if label_error and role == "unknown":
        return {
            "node": node_name,
            "role": "unknown",
            "errors": [label_error],
        }, label_error
    if role == "initiator":
        return build_initiator_node_summary(node_name), None
    if role == "target":
        return build_target_node_summary(node_name, base_path), None

    target_summary = build_target_node_summary(node_name, base_path)
    if target_summary["errors"]:
        return target_summary, None
    initiator_summary = build_initiator_node_summary(node_name)
    if initiator_summary["errors"]:
        return initiator_summary, None
    target_summary["role"] = "unknown"
    return target_summary, None


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
        deleted_images_for_node = deleted_by_node.get(node_result["node"], [])
        summaries.append(
            {
                "node": node_result["node"],
                "iqns": node_result.get("iqns", []),
                "tpgt_count": node_result.get("tpgt_count", 0),
                "lun_count": node_result.get("lun_count", 0),
                "total_active_images": node_result.get("total_active_images", 0),
                "rootfs_count": node_result.get("rootfs_count", 0),
                "pe_count": node_result.get("pe_count", 0),
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
                "read_mbytes": node_result.get("read_mbytes", 0),
                "read_iops": node_result.get("read_iops", 0),
                "images": node_result.get("images", []),
                "diagnostics": node_result.get("diagnostics", []),
                "role": node_result.get("role", "unknown"),
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
            "node_selector": DEFAULT_TARGET_SELECTOR,
            "initiator_selector": DEFAULT_INITIATOR_SELECTOR,
            "configfs_path": CONFIGFS_TARGET_PATH,
        },
    }


def format_nodes_output(payload: dict) -> str:
    nodes = payload.get("nodes", [])
    lines = [
        f'Nodes matching {payload.get("label", "selector")}: {payload.get("count", len(nodes))}'
    ]
    if nodes:
        for node in nodes:
            lines.append(f"- {node}")
    else:
        lines.append("None")
    return "\n".join(lines)


def format_target_summary(summary: dict) -> str:
    lines = [
        f"Node: {summary.get('node', 'unknown')}",
        f"Role: {summary.get('role', 'unknown')}",
    ]
    if summary.get("role") == "target":
        lines.append(f"IQNs: {', '.join(summary.get('iqns', [])) or 'None'}")
        lines.append(
            f"TPGTs: {summary.get('tpgt_count', 0)}, LUNs: {summary.get('lun_count', 0)}, Images: {summary.get('total_active_images', 0)}"
        )
        if summary.get("errors"):
            lines.append("Warnings:")
            lines.extend(f"- {message}" for message in summary["errors"])
        tpgts = summary.get("tpgts", [])
        if tpgts:
            lines.append("")
            lines.append("TPGTs")
            lines.append(
                render_table(
                    [
                        "IQN",
                        "TPGT",
                        "LUNs",
                        "ACLs",
                        "ACL names",
                    ],
                    [
                        [
                            tpgt["iqn"],
                            tpgt["tpgt_name"],
                            str(tpgt.get("lun_count", 0)),
                            str(tpgt.get("acl_count", 0)),
                            ", ".join(tpgt.get("acl_names", [])) or "None",
                        ]
                        for tpgt in tpgts
                    ],
                )
            )
        images = summary.get("images", [])
        if images:
            lines.append("")
            lines.append("LUNs and images")
            lines.append(
                render_table(
                    [
                        "IQN",
                        "TPGT",
                        "LUN",
                        "Type",
                        "Image",
                        "udev_path",
                        "Read MBytes",
                        "Read IOPs",
                    ],
                    [
                        [
                            image["iqn"],
                            image["tpgt_name"],
                            image["lun_name"],
                            image["image_type"],
                            image["image_name"],
                            image["udev_path"],
                            str(image["read_mbytes"]),
                            str(image["read_iops"]),
                        ]
                        for image in images
                    ],
                )
            )
    elif summary.get("role") == "initiator":
        lines.append(
            f"Sessions: {summary.get('sessions', 0)}, Total mounts: {summary.get('total', 0)}, Mounted: {summary.get('mounted', 0)}, Unmounted: {summary.get('unmounted', 0)}"
        )
        session_details = summary.get("session_details", [])
        if session_details:
            lines.append("Session details:")
            lines.extend(f"- {line}" for line in session_details)
        if summary.get("errors"):
            lines.append("Warnings:")
            lines.extend(f"- {message}" for message in summary["errors"])
    else:
        lines.append("No detailed data available.")
        if summary.get("errors"):
            lines.append("Warnings:")
            lines.extend(f"- {message}" for message in summary["errors"])
    return "\n".join(lines)


def format_luns_output(payload: dict) -> str:
    lines = []
    if "node" in payload:
        lines.append(f"Node: {payload['node']}")
        lines.append(f"Role: {payload.get('role', 'target')}")
        luns = payload.get("luns", payload.get("images", []))
        image_filter = payload.get("image_type", "all")
        if image_filter != "all":
            lines.append(f"Filter: {image_filter}")
        lines.append(f"LUNs: {payload.get('count', len(luns))}")
        if luns:
            lines.append(
                render_table(
                    [
                        "IQN",
                        "TPGT",
                        "LUN",
                        "Type",
                        "Image",
                        "udev_path",
                        "Read MBytes",
                        "Read IOPs",
                    ],
                    [
                        [
                            image["iqn"],
                            image["tpgt_name"],
                            image["lun_name"],
                            image["image_type"],
                            image["image_name"],
                            image["udev_path"],
                            str(image["read_mbytes"]),
                            str(image["read_iops"]),
                        ]
                        for image in luns
                    ],
                )
            )
        else:
            lines.append("None")
    else:
        for node_summary in payload.get("nodes", []):
            lines.append(format_luns_output(node_summary))
            lines.append("")
    return "\n".join(lines).strip()


def format_tpgts_output(payload: dict) -> str:
    lines = []
    if "node" in payload:
        lines.append(f"Node: {payload['node']}")
        lines.append(f"Role: {payload.get('role', 'target')}")
        tpgts = payload.get("tpgts", [])
        lines.append(f"TPGTs: {payload.get('count', len(tpgts))}")
        if tpgts:
            rows = []
            for tpgt in tpgts:
                rows.append(
                    [
                        tpgt.get("iqn", "-"),
                        tpgt.get("tpgt_name", "-"),
                        str(tpgt.get("lun_count", 0)),
                        str(tpgt.get("acl_count", 0)),
                        ", ".join(tpgt.get("acl_names", [])) or "None",
                    ]
                )
            lines.append(
                render_table(["IQN", "TPGT", "LUNs", "ACLs", "ACL names"], rows)
            )
        else:
            lines.append("None")
    else:
        for node_summary in payload.get("nodes", []):
            lines.append(format_tpgts_output(node_summary))
            lines.append("")
    return "\n".join(lines).strip()


def format_images_output(payload: dict) -> str:
    lines = []
    if "node" in payload:
        images = payload.get("images", [])
        lines.append(f"Node: {payload['node']}")
        lines.append(f"Role: {payload.get('role', 'target')}")
        image_filter = payload.get("image_type", "all")
        if image_filter != "all":
            lines.append(f"Filter: {image_filter}")
        lines.append(f"Images: {payload.get('count', len(images))}")
        if images:
            lines.append(
                render_table(
                    ["Image Name", "LUN", "Type"],
                    [
                        [
                            image["image_name"],
                            image["lun_name"],
                            image["image_type"],
                        ]
                        for image in images
                    ],
                )
            )
        else:
            lines.append("None")
    else:
        for node_summary in payload.get("nodes", []):
            lines.append(format_images_output(node_summary))
            lines.append("")
    return "\n".join(lines).strip()


def _format_initiator_sessions_summary(summary: dict) -> str:
    lines = [
        f"Node: {summary.get('node', 'unknown')}",
        f"Role: {summary.get('role', 'initiator')}",
        "",
    ]
    session_lines = summary.get("session_lines", [])
    if session_lines:
        lines.extend(session_lines)
    else:
        lines.append("No active sessions.")
    if summary.get("errors"):
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {message}" for message in summary["errors"])
    return "\n".join(lines)


def format_sessions_output(payload: dict) -> str:
    if "nodes" in payload:
        lines = []
        for summary in payload["nodes"]:
            lines.append(_format_initiator_sessions_summary(summary))
            lines.append("")
        return "\n".join(lines).strip()
    return _format_initiator_sessions_summary(payload)


def _format_mount_status_table(mounts: List[dict]) -> str:
    headers = ["Image Name", "Status"]
    rows = [
        [entry.get("image_name") or entry.get("device", "-"), entry["status"]]
        for entry in mounts
    ]
    if not rows:
        return "No iSCSI devices found."

    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(str(value)))

    lines = [
        "".join(
            str(headers[index]).ljust(widths[index] + 2)
            for index in range(len(headers))
        ).rstrip(),
        "-" * (sum(widths) + 2 * (len(headers) - 1)),
    ]
    for row in rows:
        lines.append(
            "".join(
                str(row[index]).ljust(widths[index] + 2) for index in range(len(row))
            ).rstrip()
        )
    return "\n".join(lines)


def _format_initiator_mount_status_summary(summary: dict) -> str:
    lines = [
        f"Node: {summary.get('node', 'unknown')}",
        f"Role: {summary.get('role', 'initiator')}",
        "",
        _format_mount_status_table(summary.get("mounts", [])),
    ]
    if summary.get("errors"):
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {message}" for message in summary["errors"])
    return "\n".join(lines)


def format_mount_status_output(payload: dict) -> str:
    if "nodes" in payload:
        nodes = payload["nodes"]
        total_mounted = sum(summary.get("mounted", 0) for summary in nodes)
        total_unmounted = sum(summary.get("unmounted", 0) for summary in nodes)
        lines = [
    f"Mounted: {total_mounted}, Unmounted: {total_unmounted}",
    "",
]
        for summary in nodes:
            lines.append(_format_initiator_mount_status_summary(summary))
            lines.append("")
        return "\n".join(lines).strip()
    return _format_initiator_mount_status_summary(payload)


def _collect_summaries_concurrently(nodes: Sequence[str], base_path: str) -> List[dict]:
    if not nodes:
        return []
    results: List[dict] = []
    with ThreadPoolExecutor(max_workers=min(32, len(nodes))) as executor:
        futures = {
            executor.submit(build_target_node_summary, node, base_path): node
            for node in nodes
        }
        for future in as_completed(futures):
            results.append(future.result())
    order = {node: index for index, node in enumerate(nodes)}
    results.sort(key=lambda item: order.get(item.get("node", ""), 0))
    return results


def _collect_initiator_summaries_concurrently(nodes: Sequence[str]) -> List[dict]:
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


def _collect_initiator_mount_status_concurrently(nodes: Sequence[str]) -> List[dict]:
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


def format_report(report: dict) -> str:
    lines: List[str] = []
    lines.append("iSCSI Metrics")
    lines.append("=" * 96)
    lines.append(f"Generated At: {report.get('generated_at', '-')}")

    nodes = report.get("nodes", [])
    lines.append("Target nodes: " + (", ".join(nodes) if nodes else "None"))
    lines.append("")

    metrics_rows = report.get("metrics_rows", [])
    lines.append("LUN read metrics")
    if metrics_rows:
        lines.append(
            render_table(
                ["Node", "IQN", "TPGT", "LUN", "Image", "Read MBytes", "Read IOPs"],
                metrics_rows,
            )
        )
    else:
        lines.append("No target LUN metrics found.")
    lines.append("")

    deleted_rows: List[List[str]] = []
    for node, rows in report.get("deleted_by_node", {}).items():
        for row in rows:
            deleted_rows.append(
                [
                    node,
                    row.get("type", "unknown"),
                    row.get("image_name", "-"),
                    row.get("path", "-"),
                ]
            )

    lines.append("Removed images since backup comparison")
    if deleted_rows:
        lines.append(render_table(["Node", "Type", "Image", "Path"], deleted_rows))
        sources = report.get("comparison_sources", {})
        if sources:
            lines.append("")
            lines.append("Comparison sources")
            for node, source in sources.items():
                lines.append(f"- {node}: {source}")
    else:
        lines.append("None")

    comparison_summary = report.get("comparison_summary", {})
    if comparison_summary:
        lines.append("")
        lines.append("Snapshot change summary")
        rows = []
        for node, summary in comparison_summary.items():
            rows.append(
                [
                    node,
                    str(summary.get("iqns_added", 0)),
                    str(summary.get("iqns_removed", 0)),
                    str(summary.get("tpgs_added", 0)),
                    str(summary.get("tpgs_removed", 0)),
                    str(summary.get("luns_added", 0)),
                    str(summary.get("luns_removed", 0)),
                    str(summary.get("acls_added", 0)),
                    str(summary.get("acls_removed", 0)),
                    str(summary.get("storage_objects_added", 0)),
                    str(summary.get("storage_objects_removed", 0)),
                    str(summary.get("rootfs_deleted", 0)),
                    str(summary.get("pe_deleted", 0)),
                ]
            )
        lines.append(
            render_table(
                [
                    "Node",
                    "IQNs +",
                    "IQNs -",
                    "TPGTs +",
                    "TPGTs -",
                    "LUNs +",
                    "LUNs -",
                    "ACLs +",
                    "ACLs -",
                    "Storage +",
                    "Storage -",
                    "Rootfs -",
                    "PE -",
                ],
                rows,
            )
        )

    if report.get("errors"):
        lines.append("")
        lines.append("Warnings")
        for node, message in report["errors"].items():
            lines.append(f"- {node}: {message}")

    return "\n".join(lines)

def cmd_get_nodes(args) -> None:
    label = args.label or args.default_target_label
    nodes, error = get_target_nodes(label)
    if error:
        raise SystemExit(error)
    emit_output(
        {"label": label, "nodes": nodes, "count": len(nodes)},
        formatter=format_nodes_output,
    )

def cmd_describe_node(args) -> None:
    if args.name:
        summary, error = summarize_requested_node(args.name, args.base_path)
        if error:
            raise SystemExit(error)
        emit_output(summary, formatter=format_target_summary)
        return

    label = args.label or DEFAULT_TARGET_SELECTOR
    nodes, error = get_target_nodes(label)
    if error:
        raise SystemExit(error)
    emit_output(
        {"label": label, "nodes": nodes, "count": len(nodes)},
        formatter=format_nodes_output,
    )


def cmd_get_luns(args) -> None:
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels)
        if role != "target":
            raise SystemExit(f"{args.name}: role is '{role}', this command is only valid for target nodes")
        images, _, errors = collect_target_images(args.name, CONFIGFS_TARGET_PATH)
        images = _filter_images(images, args.image_type)
        if errors or label_error:
            raise SystemExit("; ".join(errors + ([label_error] if label_error else [])))
        payload = {
            "node": args.name,
            "role": role,
            "luns": [asdict(image) for image in images],
            "count": len(images),
            "image_type": args.image_type,
        }
    else:
        nodes, error = get_target_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)
        summaries = _collect_summaries_concurrently(nodes, CONFIGFS_TARGET_PATH)
        if args.image_type != "all":
            for summary in summaries:
                summary["images"] = [
                    image
                    for image in summary.get("images", [])
                    if image.get("image_type") == args.image_type
                ]
        payload = {"nodes": summaries}
    emit_output(payload, formatter=format_luns_output)


def cmd_get_tpgts(args) -> None:
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels)
        if role != "target":
            raise SystemExit(f"{args.name}: role is '{role}', this command is only valid for target nodes")
        tpgts, errors = collect_target_tpgts(args.name, CONFIGFS_TARGET_PATH)
        if errors or label_error:
            raise SystemExit("; ".join(errors + ([label_error] if label_error else [])))
        payload = {"node": args.name, "role": role, "tpgts": tpgts, "count": len(tpgts)}
    else:
        nodes, error = get_target_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)
        payload = {
            "nodes": _collect_summaries_concurrently(nodes, CONFIGFS_TARGET_PATH)
        }
    emit_output(payload,formatter=format_tpgts_output)


def cmd_get_images(args) -> None:
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels)
        if role != "target":
            raise SystemExit(f"{args.name}: role is '{role}', this command is only valid for target nodes")
        images, tpgts, errors = collect_target_images(args.name, CONFIGFS_TARGET_PATH)
        images = _filter_images(images, args.image_type)
        filtered_tpgts = []
        if args.image_type == "all":
            filtered_tpgts = tpgts  
        else:
            allowed_ids = {image.lun_id for image in images}
            for tpgt in tpgts:
                filtered_luns = [
                    lun
                    for lun in tpgt.get("luns", [])
                    if lun.get("lun_id") in allowed_ids
                ]
                if filtered_luns:
                    filtered_tpgt = dict(tpgt)
                    filtered_tpgt["luns"] = filtered_luns
                    filtered_tpgt["lun_count"] = len(filtered_luns)
                    filtered_tpgts.append(filtered_tpgt)
        if errors or label_error:
            raise SystemExit("; ".join(errors + ([label_error] if label_error else [])))
        payload = {
            "node": args.name,
            "role": role,
            "images": [asdict(image) for image in images],
            "tpgts": filtered_tpgts,
            "count": len(images),
            "image_type": args.image_type,
        }
    else:
        nodes, error = get_target_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)
        summaries = _collect_summaries_concurrently(nodes, CONFIGFS_TARGET_PATH)
        if args.image_type != "all":
            for summary in summaries:
                summary["images"] = [
                    image
                    for image in summary.get("images", [])
                    if image.get("image_type") == args.image_type
                ]
        payload = {"nodes": summaries}
    emit_output(payload,formatter=format_images_output)


def cmd_get_metrics(args) -> None:
    node_results: List[dict] = []
    errors: Dict[str, str] = {}
    metrics_rows: List[List[str]] = []
    deleted_by_node: Dict[str, List[dict]] = {}
    comparison_summary: Dict[str, Dict[str, int]] = {}
    comparison_sources: Dict[str, str] = {}
    initiator_stats: Dict[str, Dict] = {}

    if args.name:
        summary, error = summarize_requested_node(args.name, CONFIGFS_TARGET_PATH)
        if error:
            raise SystemExit(error)

        node_results.append(summary)
        current_errors = summary.get("errors", [])
        if current_errors:
            errors[summary["node"]] = "; ".join(current_errors)

        if summary.get("role") == "target":
            for image in summary.get("images", []):
                metrics_rows.append(
                    [
                        summary["node"],
                        image["iqn"],
                        image["tpgt_name"],
                        image["lun_name"],
                        image["image_name"],
                        str(image["read_mbytes"]),
                        str(image["read_iops"]),
                    ]
                )
            current_config, current_error = get_saveconfig(summary["node"])
            if current_error:
                errors[summary["node"]] = current_error
            else:
                backup_config, backup_error, backup_source = _load_backup_snapshot(
                    summary["node"], args.compare_config
                )
                if backup_error:
                    errors[f"{summary['node']}:backup"] = backup_error
                elif backup_config is not None:
                    current_snapshot = build_snapshot(current_config)
                    previous_snapshot = build_snapshot(backup_config)
                    delta = compare_snapshots(current_snapshot, previous_snapshot)
                    deleted_by_node[summary["node"]] = _snapshot_deleted_rows(delta)
                    comparison_summary[summary["node"]] = {
                        "iqns_added": len(delta["iqns_added"]),
                        "iqns_removed": len(delta["iqns_removed"]),
                        "tpgs_added": len(delta["tpgs_added"]),
                        "tpgs_removed": len(delta["tpgs_removed"]),
                        "luns_added": len(delta["luns_added"]),
                        "luns_removed": len(delta["luns_removed"]),
                        "acls_added": len(delta["acls_added"]),
                        "acls_removed": len(delta["acls_removed"]),
                        "storage_objects_added": len(delta["storage_objects_added"]),
                        "storage_objects_removed": len(
                            delta["storage_objects_removed"]
                        ),
                        "rootfs_deleted": len(delta["rootfs_deleted"]),
                        "pe_deleted": len(delta["pe_deleted"]),
                    }
                    if backup_source:
                        comparison_sources[summary["node"]] = backup_source
        else:
            initiator_stats[summary["node"]] = {
                "total": summary.get("total", 0),
                "mounted": summary.get("mounted", 0),
                "unmounted": summary.get("unmounted", 0),
                "sessions": summary.get("sessions", 0),
            }
    else:
        target_nodes, error = get_target_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)

        for summary in _collect_summaries_concurrently(
            target_nodes, CONFIGFS_TARGET_PATH
        ):
            if summary["errors"]:
                errors[summary["node"]] = "; ".join(summary["errors"])
            node_results.append(summary)
            for image in summary.get("images", []):
                metrics_rows.append(
                    [
                        summary["node"],
                        image["iqn"],
                        image["tpgt_name"],
                        image["lun_name"],
                        image["image_name"],
                        str(image["read_mbytes"]),
                        str(image["read_iops"]),
                    ]
                )

            current_config, current_error = get_saveconfig(summary["node"])
            if current_error:
                errors[summary["node"]] = current_error
            else:
                backup_config, backup_error, backup_source = _load_backup_snapshot(
                    summary["node"], args.compare_config
                )
                if backup_error:
                    errors[f"{summary['node']}:backup"] = backup_error
                elif backup_config is not None:
                    current_snapshot = build_snapshot(current_config)
                    previous_snapshot = build_snapshot(backup_config)
                    delta = compare_snapshots(current_snapshot, previous_snapshot)
                    deleted_by_node[summary["node"]] = _snapshot_deleted_rows(delta)
                    comparison_summary[summary["node"]] = {
                        "iqns_added": len(delta["iqns_added"]),
                        "iqns_removed": len(delta["iqns_removed"]),
                        "tpgs_added": len(delta["tpgs_added"]),
                        "tpgs_removed": len(delta["tpgs_removed"]),
                        "luns_added": len(delta["luns_added"]),
                        "luns_removed": len(delta["luns_removed"]),
                        "acls_added": len(delta["acls_added"]),
                        "acls_removed": len(delta["acls_removed"]),
                        "storage_objects_added": len(delta["storage_objects_added"]),
                        "storage_objects_removed": len(
                            delta["storage_objects_removed"]
                        ),
                        "rootfs_deleted": len(delta["rootfs_deleted"]),
                        "pe_deleted": len(delta["pe_deleted"]),
                    }
                    if backup_source:
                        comparison_sources[summary["node"]] = backup_source

        initiator_nodes, initiator_error = get_target_nodes(args.initiator_selector)
        if initiator_error:
            errors["initiator_cluster"] = initiator_error

        for summary in _collect_initiator_summaries_concurrently(initiator_nodes):
            initiator_node = summary["node"]
            initiator_stats[initiator_node] = {
                "total": summary["total"],
                "mounted": summary["mounted"],
                "unmounted": summary["unmounted"],
                "sessions": summary["sessions"],
            }
            if summary["errors"]:
                errors[initiator_node] = "; ".join(summary["errors"])

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": [
            item["node"] for item in node_results if item.get("role") == "target"
        ],
        "metrics_rows": metrics_rows,
        "deleted_by_node": deleted_by_node,
        "comparison_summary": comparison_summary,
        "comparison_sources": comparison_sources,
        "errors": errors,
        "initiator_stats": initiator_stats,
    }
    emit_output(report,formatter=format_report)

def cmd_get_sessions(args) -> None:
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels) if labels else "unknown"
        if role != "initiator":
            raise SystemExit(f"{args.name}: role is '{role}', this command is only valid for initiator nodes")
        payload = build_initiator_node_summary(args.name)
    else:
        nodes, error = get_target_nodes(args.label)
        if error:
            raise SystemExit(error)
        payload = {"nodes": _collect_initiator_summaries_concurrently(nodes)}
    emit_output(payload,formatter=format_sessions_output)

def cmd_get_mount_status(args) -> None:
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels) if labels else "unknown"
        if role != "initiator":
            raise SystemExit(f"{args.name}: role is '{role}', this command is only valid for initiator nodes")
        payload = build_initiator_mount_status(args.name)
    else:
        nodes, error = get_target_nodes(args.label)
        if error:
            raise SystemExit(error)
        payload = {"nodes": _collect_initiator_mount_status_concurrently(nodes)}
    emit_output(payload, formatter=format_mount_status_output)
