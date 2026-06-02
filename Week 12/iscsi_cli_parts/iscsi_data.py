from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .common import (
    DEFAULT_BASE_PATH,
    DEFAULT_INITIATOR_SELECTOR,
    DEFAULT_STATE_FILE,
    DEFAULT_TARGET_SELECTOR,
    LunImage,
    count_by_type,
    detect_node_role,
    emit_output,
    get_node_labels,
    get_target_nodes,
    infer_image_type,
    list_acls,
    list_iqns,
    list_luns,
    list_tpgts,
    load_state,
    parse_metric_value,
    read_lun_stats,
    read_udev_path,
    render_table,
    resolve_object_path,
    reset_state_file,
    save_state,
    snapshot_images_by_node,
    sum_metric,
    update_state_and_get_deleted,
)
from .error_reporting import collect_node_diagnostics
from .initiator_configuration import build_initiator_node_summary


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

        udev_path, udev_error = read_udev_path(node, object_path)
        if udev_error:
            lun_errors.append(
                f"{node}: unable to read udev_path for {iqn}/{tpgt_name}/{lun_name}: {udev_error}"
            )

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


def build_target_node_summary(node: str, base_path: str) -> dict:
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
            "base_path": DEFAULT_BASE_PATH,
            "state_file": str(DEFAULT_STATE_FILE),
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
            f"TPGTs: {summary.get('tpgt_count', 0)}, LUNs: {summary.get('lun_count', 0)}, Active images: {summary.get('total_active_images', 0)}"
        )
        lines.append(
            f"Rootfs: {summary.get('rootfs_count', 0)}, PE: {summary.get('pe_count', 0)}, Read MBytes: {summary.get('read_mbytes', 0)}, Read IOPs: {summary.get('read_iops', 0)}"
        )
        if summary.get("errors"):
            lines.append("Warnings:")
            lines.extend(f"- {message}" for message in summary["errors"])
        images = summary.get("images", [])
        if images:
            lines.append("")
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


def format_sessions_output(payload: dict) -> str:
    if "nodes" in payload:
        lines = ["Initiator session status"]
        for summary in payload["nodes"]:
            lines.append(format_target_summary(summary))
            lines.append("")
        return "\n".join(lines).strip()
    return format_target_summary(payload)


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


def format_report(report: dict) -> str:
    lines: List[str] = []
    lines.append("iSCSI Metrics")
    lines.append("=" * 96)
    lines.append(f"State file: {report['state_file']}")
    lines.append(
        "Target nodes: " + (", ".join(report["nodes"]) if report["nodes"] else "None")
    )
    lines.append("")

    # Only render target-related sections when there are target nodes
    if report.get("nodes"):
        lines.append("Configured iSCSI target nodes")
        if report["nodes_summary"]:
            for item in report["nodes_summary"]:
                if item.get("role") == "initiator":
                    continue
                iqn_list = ", ".join(item["iqns"]) if item["iqns"] else "None"
                lines.append(f"- {item['node']}: {iqn_list}")
        else:
            lines.append("None")
        lines.append("")

    # Target-specific summaries and metrics
    if report.get("nodes"):
        target_summaries = [
            item for item in report["nodes_summary"] if item.get("role") == "target"
        ]
        summary_rows: List[List[str]] = []
        for item in target_summaries:
            summary_rows.append(
                [
                    item["node"],
                    ", ".join(item["iqns"]) if item["iqns"] else "None",
                    str(item["tpgt_count"]),
                    str(item["lun_count"]),
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
                    "TPGTs",
                    "LUNs",
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

        total_active = sum(item["total_active_images"] for item in target_summaries)
        total_rootfs = sum(item["rootfs_count"] for item in target_summaries)
        total_pe = sum(item["pe_count"] for item in target_summaries)
        total_deleted_rootfs = sum(item["deleted_rootfs"] for item in target_summaries)
        total_deleted_pe = sum(item["deleted_pe"] for item in target_summaries)

        lines.append(
            f"Cluster totals: total={total_active}, rootfs={total_rootfs}, pe={total_pe}, deleted_rootfs={total_deleted_rootfs}, deleted_pe={total_deleted_pe}"
        )
        lines.append("")

    if report.get("nodes"):
        lines.append("LUN level read I/O metrics per worker node")
        if target_summaries:
            for item in target_summaries:
                lines.append(item["node"])
                lun_rows: List[List[str]] = []
                for image in item["images"]:
                    lun_rows.append(
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
                    )
                if lun_rows:
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
            render_table(["Node", "Severity", "Source", "Message"], diagnostic_rows)
        )
    else:
        lines.append("No storage/network errors detected.")
    lines.append("")

    if report.get("nodes"):
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
            lines.append(
                render_table(["Node", "Type", "Image", "udev_path"], deleted_rows)
            )
        else:
            lines.append("None")
        lines.append("")

    if report.get("initiator_stats"):
        lines.append("Initiator node mount status")
        initiator_rows = [
            [
                node,
                str(stats.get("sessions", 0)),
                str(stats["total"]),
                str(stats["mounted"]),
                str(stats["unmounted"]),
            ]
            for node, stats in report["initiator_stats"].items()
        ]
        lines.append(
            render_table(
                ["Node", "Sessions", "Total", "Mounted", "Unmounted"], initiator_rows
            )
        )

    if report["errors"]:
        lines.append("")
        lines.append("Warnings")
        for node, message in report["errors"].items():
            lines.append(f"- {node}: {message}")

    return "\n".join(lines)


def cmd_get_nodes(args) -> None:
    nodes, error = get_target_nodes(args.label)
    if error:
        raise SystemExit(error)
    emit_output(
        {"label": args.label, "nodes": nodes, "count": len(nodes)},
        args.json,
        formatter=format_nodes_output,
    )


def cmd_get_node(args) -> None:
    summary, error = summarize_requested_node(args.name, args.base_path)
    if error:
        raise SystemExit(error)
    emit_output(summary, args.json, formatter=format_target_summary)


def cmd_get_luns(args) -> None:
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels)
        images, _, errors = collect_target_images(args.name, args.base_path)
        if errors or label_error:
            raise SystemExit("; ".join(errors + ([label_error] if label_error else [])))
        payload = {
            "node": args.name,
            "role": role,
            "luns": [asdict(image) for image in images],
            "count": len(images),
        }
    else:
        nodes, error = get_target_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)
        payload = {
            "nodes": _collect_summaries_concurrently(nodes, args.base_path),
        }
    emit_output(payload, args.json, formatter=format_luns_output)


def cmd_get_tpgts(args) -> None:
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels)
        tpgts, errors = collect_target_tpgts(args.name, args.base_path)
        if errors or label_error:
            raise SystemExit("; ".join(errors + ([label_error] if label_error else [])))
        payload = {"node": args.name, "role": role, "tpgts": tpgts, "count": len(tpgts)}
    else:
        nodes, error = get_target_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)
        payload = {"nodes": _collect_summaries_concurrently(nodes, args.base_path)}
    emit_output(payload, args.json, formatter=format_tpgts_output)


def cmd_get_images(args) -> None:
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels)
        images, tpgts, errors = collect_target_images(args.name, args.base_path)
        if errors or label_error:
            raise SystemExit("; ".join(errors + ([label_error] if label_error else [])))
        payload = {
            "node": args.name,
            "role": role,
            "images": [asdict(image) for image in images],
            "tpgts": tpgts,
            "count": len(images),
        }
    else:
        nodes, error = get_target_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)
        payload = {"nodes": _collect_summaries_concurrently(nodes, args.base_path)}
    emit_output(payload, args.json, formatter=format_images_output)


def cmd_get_metrics(args) -> None:
    state_path = Path(args.state_file).expanduser()
    if args.reset_state:
        reset_state_file(state_path)

    node_results: List[dict] = []
    errors: Dict[str, str] = {}
    current_snapshots: Dict[str, Dict[str, dict]] = {}
    deleted_by_node: Dict[str, List[dict]] = {}
    initiator_stats: Dict[str, Dict] = {}

    if args.name:
        summary, error = summarize_requested_node(args.name, args.base_path)
        if error:
            raise SystemExit(error)

        node_results.append(summary)
        current_errors = summary.get("errors", [])
        if current_errors:
            errors[summary["node"]] = "; ".join(current_errors)

        if summary.get("role") == "target":
            current_snapshots[summary["node"]] = snapshot_images_by_node(
                [LunImage(**image) for image in summary["images"]]
            ).get(summary["node"], {})
            state = load_state(state_path)
            deleted_by_node = update_state_and_get_deleted(
                state, current_snapshots, [summary["node"]]
            )
            state["generated_at"] = datetime.now(timezone.utc).isoformat()
            if not args.no_state_update:
                save_state(state_path, state)
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

        for summary in _collect_summaries_concurrently(target_nodes, args.base_path):
            if summary["errors"]:
                errors[summary["node"]] = "; ".join(summary["errors"])
            node_results.append(summary)
            current_snapshots[summary["node"]] = snapshot_images_by_node(
                [LunImage(**image) for image in summary["images"]]
            ).get(summary["node"], {})

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

        state = load_state(state_path)
        deleted_by_node = update_state_and_get_deleted(
            state, current_snapshots, target_nodes
        )
        state["generated_at"] = datetime.now(timezone.utc).isoformat()
        if not args.no_state_update:
            save_state(state_path, state)

    # If a single node was requested and it's an initiator, do not list it
    # as a target node in the report. This prevents printing target metrics
    # placeholders when the user asked only for an initiator node.
    if args.name and node_results and node_results[0].get("role") == "initiator":
        report_target_nodes: List[str] = []
    else:
        report_target_nodes = (
            [item["node"] for item in node_results] if args.name else target_nodes
        )

    report = build_report(
        report_target_nodes,
        node_results,
        deleted_by_node,
        state_path,
        errors,
        initiator_stats,
    )
    emit_output(report, args.json, formatter=format_report)


def cmd_get_sessions(args) -> None:
    if args.name:
        payload = build_initiator_node_summary(args.name)
    else:
        nodes, error = get_target_nodes(args.label)
        if error:
            raise SystemExit(error)
        payload = {"nodes": _collect_initiator_summaries_concurrently(nodes)}
    emit_output(payload, args.json, formatter=format_sessions_output)
