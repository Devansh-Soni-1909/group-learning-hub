from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from .kubernetes import (
    CLI_CONFIG_PATH,
    get_target_node_label,
    set_target_node_label,
    get_initiator_node_label,
    set_initator_node_label,
    get_kubernetes_nodes,
    get_node_labels,
    detect_node_role,
)
from .iscsi_target import (
    filter_images,
    collect_target_images,
    collect_target_tpgts,
    build_target_node_summary,
    collect_summaries_concurrently,
    list_config_versions,
    build_backup_config_summary,
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
    format_nodes_output,
    format_configs_output,
    format_tpgts_output,
    format_luns_output,
    format_images_output,
    format_sessions_output,
    format_mount_status_output,
    format_error_summary,
    format_target_summary,
    format_initiator_summary,
    format_target_metrics,
    format_initiator_metrics,
    emit_output,
)

DEFAULT_TARGET_SELECTOR, error = get_target_node_label()
if error:
    raise SystemExit("Error getting target node label")

DEFAULT_INITIATOR_SELECTOR, error = get_initiator_node_label()
if error:
    raise SystemExit("Error getting initiator node label")


# get commands


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


def cmd_get_configs(args) -> None:
    if args.name:
        labels, error = get_node_labels(args.name)
        if error:
            raise SystemExit(error)
        role = detect_node_role(labels=labels)
        if role != "target":
            raise SystemExit(
                f"{args.name}: role is '{role},  this command is only valid for target nodes"
            )
        current, versions, error = list_config_versions(args.name)
        if error:
            raise SystemExit(
                f"Error fetching configuration files from {args.name}: {error}"
            )
        emit_output(
            {"node": args.name, "current_config": current, "versions": versions},
            formatter=format_configs_output,
        )
    else:
        raise SystemExit(f"Please provide the node name with the flag --name")


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
    if args.name:
        node_name = args.name
        labels, label_error = get_node_labels(node_name)
        role = detect_node_role(labels) if labels else "unknown"
        if label_error and role == "unknown":
            return {
                "node": node_name,
                "role": "unknown",
                "errors": [label_error],
            }, label_error
        if role == "initiator":
            summary = build_initiator_node_summary(node_name)
            emit_output(summary, formatter=format_initiator_metrics)
            return
        if role == "target":
            summary = build_target_node_summary(node_name, with_metrics=True)
            emit_output(summary, formatter=format_target_metrics)

    else:
        raise SystemExit(f"Provide a node name with --name flag")


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


# set commands


def cmd_set_label(args) -> None:
    if args.target:
        label = args.target
        set_target_node_label(label)
    if args.initiator:
        label = args.initiator
        set_initator_node_label(label)
    if not args.target and not args.initiator:
        raise SystemExit("Provide target/initiator labels")
    print(f"Config saved at {CLI_CONFIG_PATH}")


# describe commands
def cmd_describe_node(args) -> None:
    if args.name:
        node_name = args.name
        labels, label_error = get_node_labels(node_name)
        role = detect_node_role(labels) if labels else "unknown"
        if label_error and role == "unknown":
            return {
                "node": node_name,
                "role": "unknown",
                "errors": [label_error],
            }, label_error
        if role == "initiator":
            summary = build_initiator_node_summary(node_name)
            emit_output(summary, formatter=format_initiator_summary)
            return
        if role == "target":
            summary = build_target_node_summary(node_name, with_metrics=False)
            emit_output(summary, formatter=format_target_summary)
    else:
        raise SystemExit(f"Provide the node name with --name flag")


def cmd_describe_config(args) -> None:
    if args.node:
        if args.file_path:
            payload, error = build_backup_config_summary(args.node, args.file_path)
            if error:
                raise SystemExit(
                    f"Error describing config {args.file_path} in {args.node}: {error}"
                )
            emit_output(payload, formatter=format_target_summary)
        else:
            raise SystemExit(
                "Please provide configuraiton file path with --file-path flag"
            )
    else:
        raise SystemExit("Please provide a node name with --node flag")
