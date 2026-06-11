from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from pathlib import Path

from .kubernetes import (
    DEFAULT_INITIATOR_SELECTOR,
    DEFAULT_TARGET_SELECTOR,
    get_kubernetes_nodes,
    get_node_labels,
    detect_node_role,
)
from .iscsi_target import (
    filter_images,
    collect_target_images,
    collect_target_tpgts,
    build_snapshot,
    compare_snapshots,
    load_backup_snapshot,
    snapshot_deleted_rows,
    get_saveconfig,
    summarize_requested_node,
    collect_summaries_concurrently,
)
from .iscsi_initiator import (
    build_initiator_mount_status,
    build_initiator_node_summary,
    collect_initiator_summaries_concurrently,
    collect_initiator_mount_status_concurrently,
)
from .error_collector import (
    collect_error_summary,
    collect_recent_logs_for_nodes,
    scan_logs_for_errors,
    collect_service_errors,
)

from .formatter import (
    format_error_summary,
    format_images_output,
    format_luns_output,
    format_mount_status_output,
    format_nodes_output,
    format_report,
    format_sessions_output,
    format_target_summary,
    format_tpgts_output,
    emit_output,
)


def cmd_get_nodes(args) -> None:
    label = None
    if (args.target and args.initiator) or (not args.target and not args.initiator):
        target_nodes, error = get_kubernetes_nodes(
            DEFAULT_TARGET_SELECTOR, full_info=True
        )
        for node in target_nodes.keys():
            target_nodes[node]["role"] = "target"

        initiator_nodes, error = get_kubernetes_nodes(
            DEFAULT_INITIATOR_SELECTOR, full_info=True
        )
        for node in initiator_nodes.keys():
            initiator_nodes[node]["role"] = "initiator"

        label = f"{DEFAULT_INITIATOR_SELECTOR, DEFAULT_TARGET_SELECTOR}"
        nodes = target_nodes | initiator_nodes
    elif args.target:
        label = DEFAULT_TARGET_SELECTOR
        nodes, error = get_kubernetes_nodes(label, full_info=True)
        for node in nodes.keys():
            nodes[node]["role"] = "target"
    elif args.initiator:
        label = DEFAULT_INITIATOR_SELECTOR
        nodes, error = get_kubernetes_nodes(label, full_info=True)
        for node in nodes.keys():
            nodes[node]["role"] = "initiator"
    if error:
        raise SystemExit(error)
    emit_output(
        {"label": label, "nodes": nodes},
        formatter=format_nodes_output,
    )


def cmd_describe_node(args) -> None:
    with_metrics = True if args.metrics else False
    if args.name:
        summary, error = summarize_requested_node(args.name, with_metrics)
        if error:
            raise SystemExit(error)
        emit_output(summary, formatter=format_target_summary)
        return

    label = args.label or DEFAULT_TARGET_SELECTOR
    nodes, error = get_kubernetes_nodes(label)
    if error:
        raise SystemExit(error)
    emit_output(
        {"label": label, "nodes": nodes, "count": len(nodes)},
        formatter=format_nodes_output,
    )


def cmd_get_luns(args) -> None:
    with_metrics = True if args.metrics else False
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels)
        if role != "target":
            raise SystemExit(
                f"{args.name}: role is '{role}', this command is only valid for target nodes"
            )
        images, _, errors = collect_target_images(args.name, with_metrics)
        images = filter_images(images, args.image_type)
        if errors or label_error:
            raise SystemExit("; ".join(errors + ([label_error] if label_error else [])))
        payload = {
            "node": args.name,
            "role": role,
            "luns": [asdict(image) for image in images],
            "count": len(images),
            "image_type": args.image_type,
            "with_metrics": with_metrics,
        }
    else:
        nodes, error = get_kubernetes_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)
        summaries = collect_summaries_concurrently(nodes, with_metrics)
        if args.image_type != "all":
            for summary in summaries:
                summary["images"] = [
                    image
                    for image in summary.get("images", [])
                    if image.get("image_type") == args.image_type
                ]
        payload = {"nodes": summaries, "with_metrics": with_metrics}
    emit_output(payload, formatter=format_luns_output)


def cmd_get_tpgts(args) -> None:
    with_metrics = False
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels)
        if role != "target":
            raise SystemExit(
                f"{args.name}: role is '{role}', this command is only valid for target nodes"
            )
        tpgts, errors = collect_target_tpgts(args.name, with_metrics)
        if errors or label_error:
            raise SystemExit("; ".join(errors + ([label_error] if label_error else [])))
        payload = {"node": args.name, "role": role, "tpgts": tpgts, "count": len(tpgts)}
    else:
        nodes, error = get_kubernetes_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)
        payload = {"nodes": collect_summaries_concurrently(nodes, with_metrics)}
    emit_output(payload, formatter=format_tpgts_output)


def cmd_get_images(args) -> None:
    with_metrics = True if args.metrics else False
    if args.name:
        labels, label_error = get_node_labels(args.name)
        role = detect_node_role(labels)
        if role != "target":
            raise SystemExit(
                f"{args.name}: role is '{role}', this command is only valid for target nodes"
            )
        images, tpgts, errors = collect_target_images(args.name, with_metrics)
        images = filter_images(images, args.image_type)
        filtered_tpgts = []
        if args.image_type == "all":
            filtered_tpgts = tpgts
        else:
            allowed_ids = {image.lun_id for image in images}
            for tpgt in tpgts:
                filtered_luns = [
                    lun for lun in tpgt["luns"] if lun["lun_id"] in allowed_ids
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
            "with_metrics": with_metrics,
        }
    else:
        nodes, error = get_kubernetes_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)
        summaries = collect_summaries_concurrently(nodes, with_metrics)
        if args.image_type != "all":
            for summary in summaries:
                summary["images"] = [
                    image
                    for image in summary.get("images", [])
                    if image.get("image_type") == args.image_type
                ]
        payload = {"nodes": summaries, "with_metrics": with_metrics}
    emit_output(payload, formatter=format_images_output)


def cmd_get_metrics(args) -> None:
    node_results: List[dict] = []
    errors: Dict[str, str] = {}
    metrics_rows: List[List[str]] = []
    deleted_by_node: Dict[str, List[dict]] = {}
    comparison_summary: Dict[str, Dict[str, int]] = {}
    comparison_sources: Dict[str, str] = {}
    initiator_stats: Dict[str, Dict] = {}

    if args.name:
        summary, error = summarize_requested_node(args.name, True)
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
                backup_config, backup_error, backup_source = load_backup_snapshot(
                    summary["node"], args.compare_config
                )
                if backup_error:
                    errors[f"{summary['node']}:backup"] = backup_error
                elif backup_config is not None:
                    current_snapshot = build_snapshot(current_config)
                    previous_snapshot = build_snapshot(backup_config)
                    delta = compare_snapshots(current_snapshot, previous_snapshot)
                    deleted_by_node[summary["node"]] = snapshot_deleted_rows(delta)
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
        target_nodes, error = get_kubernetes_nodes(DEFAULT_TARGET_SELECTOR)
        if error:
            raise SystemExit(error)

        for summary in collect_summaries_concurrently(target_nodes, True):
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
                backup_config, backup_error, backup_source = load_backup_snapshot(
                    summary["node"], args.compare_config
                )
                if backup_error:
                    errors[f"{summary['node']}:backup"] = backup_error
                elif backup_config is not None:
                    current_snapshot = build_snapshot(current_config)
                    previous_snapshot = build_snapshot(backup_config)
                    delta = compare_snapshots(current_snapshot, previous_snapshot)
                    deleted_by_node[summary["node"]] = snapshot_deleted_rows(delta)
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

        initiator_nodes, initiator_error = get_kubernetes_nodes(args.initiator_selector)
        if initiator_error:
            errors["initiator_cluster"] = initiator_error

        for summary in collect_initiator_summaries_concurrently(initiator_nodes):
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
    emit_output(report, formatter=format_report)


def cmd_get_sessions(args) -> None:
    if args.name:
        labels, label_error = get_node_labels(args.name)
        if label_error:
            raise SystemExit("Error getting sessions: " + label_error)
        role = detect_node_role(labels) if labels else "unknown"
        if role != "initiator":
            raise SystemExit(
                f"{args.name}: role is '{role}', this command is only valid for initiator nodes"
            )
        payload = build_initiator_node_summary(args.name)
    else:
        nodes, error = get_kubernetes_nodes(args.label)
        if error:
            raise SystemExit(error)
        payload = {"nodes": collect_initiator_summaries_concurrently(nodes)}
    emit_output(payload, formatter=format_sessions_output)


def cmd_get_mount_status(args) -> None:
    if args.name:
        labels, label_error = get_node_labels(args.name)
        if label_error:
            raise SystemExit("Error getting mount-status: " + label_error)
        role = detect_node_role(labels) if labels else "unknown"
        if role != "initiator":
            raise SystemExit(
                f"{args.name}: role is '{role}', this command is only valid for initiator nodes"
            )
        payload = build_initiator_mount_status(args.name)
    else:
        nodes, error = get_kubernetes_nodes(args.label)
        if error:
            raise SystemExit(error)
        payload = {"nodes": collect_initiator_mount_status_concurrently(nodes)}
    emit_output(payload, formatter=format_mount_status_output)


def cmd_get_errors(args) -> None:
    if args.name:
        payload = collect_error_summary(args.name, args.lines)
    else:
        nodes, error = get_kubernetes_nodes(args.label)
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
                service_errors, service_error_messages = collect_service_errors(
                    node=node,
                    days=2,
                    lines=100,
                )
                payload["nodes"].append(
                    {
                        "node": node,
                        "lines": args.lines,
                        "log_error": errors_by_node.get(node),
                        "logs": logs_by_node.get(node, ""),
                        "service_errors": service_errors,
                        "service_error_messages": service_error_messages,
                        "diagnostics": diagnostics,
                    }
                )

    emit_output(payload, args.json, formatter=format_error_summary)
