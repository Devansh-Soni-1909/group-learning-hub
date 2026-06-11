from typing import List, Sequence


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


def emit_output(payload: dict, formatter=None) -> None:
    print(formatter(payload) if formatter else str(payload))


def format_nodes_output(payload: dict) -> str:
    nodes = payload.get("nodes", {})
    lines = [f'Nodes matching {payload.get("label", "selector")}: {len(nodes)}']
    if not nodes:
        lines.append("None")
        return "\n".join(lines)

    headers = [
        "NAME",
        "STATUS",
        "ROLE",
        "ARCH",
        "OS",
        "OS IMAGE",
    ]
    rows = []
    for node in nodes.values():
        node_info = node.get("node_info", {})
        rows.append(
            [
                node.get("name", ""),
                node.get("status", ""),
                node.get("role", ""),
                node_info.get("arch", ""),
                node_info.get("os", ""),
                node_info.get("os_image", ""),
            ]
        )
    lines.append("")
    lines.append(render_table(headers, rows))
    return "\n".join(lines)


def format_target_summary(summary: dict) -> str:
    lines = [
        f"Node: {summary.get('node', 'unknown')}",
        f"Role: {summary.get('role', 'unknown')}",
    ]
    if summary.get("role") == "target":
        with_metrics = summary.get("with_metrics", False)
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
                            str(tpgt["lun_count"]),
                            str(tpgt["acl_count"]),
                            ", ".join(tpgt["acl_names"]) or "None",
                        ]
                        for tpgt in tpgts
                    ],
                )
            )
        images = summary.get("images", [])
        if images:
            headers = ["IQN", "TPGT", "LUN", "Type", "Image", "udev_path"]
            if with_metrics:
                headers.extend(["Read MBytes", "Read IOPs"])
            rows = []
            for image in images:
                row = [
                    image["iqn"],
                    image["tpgt_name"],
                    image["lun_name"],
                    image["image_type"],
                    image["image_name"],
                    image["udev_path"],
                ]
                if with_metrics:
                    row.extend([str(image["read_mbytes"]), str(image["read_iops"])])
                rows.append(row)
            lines.append("")
            lines.append("LUNs and images")
            lines.append(render_table(headers, rows))
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
            with_metrics = payload.get("with_metrics", False)
            headers = ["IQN", "TPGT", "LUN", "Type", "Image", "udev_path"]
            if with_metrics:
                headers.extend(["Read MBytes", "Read IOPs"])
            rows = []
            for image in luns:
                row = [
                    image["iqn"],
                    image["tpgt_name"],
                    image["lun_name"],
                    image["image_type"],
                    image["image_name"],
                    image["udev_path"],
                ]
                if with_metrics:
                    row.extend(
                        [
                            str(image["read_mbytes"]),
                            str(image["read_iops"]),
                        ]
                    )
                rows.append(row)
            lines.append(render_table(headers, rows))
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
                        tpgt["iqn"],
                        tpgt["tpgt_name"],
                        str(tpgt["lun_count"]),
                        str(tpgt["acl_count"]),
                        ", ".join(tpgt["acl_names"]) or "None",
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
            with_metrics = payload.get("with_metrics", False)
            headers = ["Image Name", "LUN", "Type"]
            if with_metrics:
                headers.extend(["Read MBytes", "Read IOPs"])
            rows = []
            for image in images:
                row = [
                    image["image_name"],
                    image["lun_name"],
                    image["image_type"],
                ]
                if with_metrics:
                    row.extend(
                        [
                            str(image["read_mbytes"]),
                            str(image["read_iops"]),
                        ]
                    )
                rows.append(row)
            lines.append(render_table(headers, rows))
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


def format_error_summary(payload: dict) -> str:
    if "nodes" in payload:
        lines = [
            f"Label: {payload.get('label', '-')}",
            f"Lines: {payload.get('lines', '-')}",
            "",
            "Node summaries",
        ]

        node_rows = [
            [
                item.get("node", "-"),
                str(len(item.get("diagnostics", []))),
                item.get("log_error") or "ok",
            ]
            for item in payload.get("nodes", [])
        ]

        if node_rows:
            lines.append(
                render_table(
                    ["Node", "Findings", "Log status"],
                    node_rows,
                )
            )
        else:
            lines.append("None")

        # Recent Service Errors
        lines.append("")
        lines.append("Recent Service Errors")

        for item in payload.get("nodes", []):
            service_errors = item.get("service_errors", {})

            lines.append("")
            lines.append(f"Node: {item.get('node', '-')}")

            if not service_errors:
                lines.append("No recent service errors found.")
                continue

            for service, output in service_errors.items():
                lines.append("")
                lines.append(f"Service: {service}")

                if output and output.strip():
                    lines.append(output)
                else:
                    lines.append("No recent errors found.")

        # Detected Errors
        diagnostic_rows = []

        for item in payload.get("nodes", []):
            for diagnostic in item.get("diagnostics", []):
                diagnostic_rows.append(
                    [
                        diagnostic.get("node", "-"),
                        diagnostic.get("severity", "-"),
                        diagnostic.get("source", "-"),
                        diagnostic.get("message", "-")[:120],
                    ]
                )

        lines.append("")
        lines.append("Detected errors")

        if diagnostic_rows:
            lines.append(
                render_table(
                    ["Node", "Severity", "Source", "Message"],
                    diagnostic_rows,
                )
            )
        else:
            lines.append("None")

        return "\n".join(lines)

    # Single-node mode
    lines = [
        f"Node: {payload.get('node', '-')}",
        f"Lines: {payload.get('lines', '-')}",
    ]

    if payload.get("log_error"):
        lines.append(f"Log error: {payload['log_error']}")

    service_errors = payload.get("service_errors", {})

    if service_errors:
        lines.append("")
        lines.append("Recent Service Errors")

        for service, output in service_errors.items():
            lines.append("")
            lines.append(service)

            if output and output.strip():
                lines.append(output)
            else:
                lines.append("No recent errors found.")

    diagnostics = payload.get("diagnostics", [])

    lines.append("")
    lines.append("Detected errors")

    if diagnostics:
        rows = [
            [
                diagnostic.get("severity", "-"),
                diagnostic.get("source", "-"),
                diagnostic.get("message", "-")[:120],
            ]
            for diagnostic in diagnostics
        ]

        lines.append(
            render_table(
                ["Severity", "Source", "Message"],
                rows,
            )
        )
    else:
        lines.append("None")

    if payload.get("logs"):
        lines.append("")
        lines.append("Recent logs")
        lines.append(payload["logs"])

    return "\n".join(lines)
