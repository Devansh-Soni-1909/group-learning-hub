#!/usr/bin/env python3
from __future__ import annotations

import argparse

from iscsi_cli_parts.common import DEFAULT_BASE_PATH, DEFAULT_INITIATOR_SELECTOR, DEFAULT_STATE_FILE, DEFAULT_TARGET_SELECTOR
from iscsi_cli_parts.error_reporting import cmd_get_errors
from iscsi_cli_parts.iscsi_data import (
    cmd_get_images,
    cmd_get_luns,
    cmd_get_metrics,
    cmd_get_node,
    cmd_get_nodes,
    cmd_get_sessions,
    cmd_get_tpgts,
)
from iscsi_cli_parts.target_configuration import cmd_delete_image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iscsi", description="Standalone iSCSI CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    get_parser = subparsers.add_parser("get", help="Read-only iSCSI commands")
    get_subparsers = get_parser.add_subparsers(dest="get_command", required=True)

    nodes_parser = get_subparsers.add_parser("nodes", help="List iSCSI target nodes")
    nodes_parser.add_argument("--label", default=DEFAULT_TARGET_SELECTOR, help="Kubernetes label selector for iSCSI nodes")
    nodes_parser.add_argument("--json", action="store_true", help="Print JSON output")
    nodes_parser.set_defaults(func=cmd_get_nodes)

    node_parser = get_subparsers.add_parser("node", help="Show the iSCSI configuration of one node")
    node_parser.add_argument("--name", required=True, help="Node name to inspect")
    node_parser.add_argument("--base-path", default=DEFAULT_BASE_PATH, help="Base configfs path containing the iSCSI target tree")
    node_parser.add_argument("--json", action="store_true", help="Print JSON output")
    node_parser.set_defaults(func=cmd_get_node)

    luns_parser = get_subparsers.add_parser("luns", help="List LUNs for one or more target nodes")
    luns_parser.add_argument("--name", default=None, help="Target node to inspect")
    luns_parser.add_argument("--base-path", default=DEFAULT_BASE_PATH, help="Base configfs path containing the iSCSI target tree")
    luns_parser.add_argument("--json", action="store_true", help="Print JSON output")
    luns_parser.set_defaults(func=cmd_get_luns)

    tpgts_parser = get_subparsers.add_parser("tpgts", help="List TPGTs for one or more target nodes")
    tpgts_parser.add_argument("--name", default=None, help="Target node to inspect")
    tpgts_parser.add_argument("--base-path", default=DEFAULT_BASE_PATH, help="Base configfs path containing the iSCSI target tree")
    tpgts_parser.add_argument("--json", action="store_true", help="Print JSON output")
    tpgts_parser.set_defaults(func=cmd_get_tpgts)

    images_parser = get_subparsers.add_parser("images", help="List projected images")
    images_parser.add_argument("--name", default=None, help="Target node to inspect")
    images_parser.add_argument("--base-path", default=DEFAULT_BASE_PATH, help="Base configfs path containing the iSCSI target tree")
    images_parser.add_argument("--json", action="store_true", help="Print JSON output")
    images_parser.set_defaults(func=cmd_get_images)

    metrics_parser = get_subparsers.add_parser("metrics", help="Show iSCSI metrics")
    metrics_parser.add_argument("--name", default=None, help="Target node to inspect")
    metrics_parser.add_argument("--base-path", default=DEFAULT_BASE_PATH, help="Base configfs path containing the iSCSI target tree")
    metrics_parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="JSON file used to store the previous image snapshot")
    metrics_parser.add_argument("--initiator-selector", default=DEFAULT_INITIATOR_SELECTOR, help="Kubernetes label selector for initiator nodes")
    metrics_parser.add_argument("--json", action="store_true", help="Print JSON output")
    metrics_parser.add_argument("--no-state-update", action="store_true", help="Do not write the updated image snapshot back to the state file")
    metrics_parser.add_argument("--reset-state", action="store_true", help="Clear the state file before collecting metrics")
    metrics_parser.set_defaults(func=cmd_get_metrics)

    sessions_parser = get_subparsers.add_parser("sessions", help="Show initiator mount/session state")
    sessions_parser.add_argument("--name", default=None, help="Initiator node to inspect")
    sessions_parser.add_argument("--label", default=DEFAULT_INITIATOR_SELECTOR, help="Kubernetes label selector for initiator nodes")
    sessions_parser.add_argument("--json", action="store_true", help="Print JSON output")
    sessions_parser.set_defaults(func=cmd_get_sessions)

    errors_parser = get_subparsers.add_parser("errors", help="Scan recent logs for storage and network errors")
    errors_parser.add_argument("--name", default=None, help="Node to inspect")
    errors_parser.add_argument("--label", default=DEFAULT_TARGET_SELECTOR, help="Kubernetes label selector for target nodes")
    errors_parser.add_argument("--lines", type=int, default=200, help="Number of recent log lines to collect per node")
    errors_parser.add_argument("--json", action="store_true", help="Print JSON output")
    errors_parser.set_defaults(func=cmd_get_errors)

    delete_parser = subparsers.add_parser("delete", help="Destructive iSCSI commands")
    delete_subparsers = delete_parser.add_subparsers(dest="delete_command", required=True)

    delete_image_parser = delete_subparsers.add_parser("image", help="Delete one projected image")
    delete_image_parser.add_argument("--name", required=True, help="Target node name")
    delete_image_parser.add_argument("--tpgt", required=True, help="TPGT name, for example tpgt_1")
    delete_image_parser.add_argument("--base-path", default=DEFAULT_BASE_PATH, help="Base configfs path containing the iSCSI target tree")
    delete_image_parser.add_argument("--force", action="store_true", help="Delete without prompting for confirmation")
    delete_image_parser.add_argument("--json", action="store_true", help="Print JSON output")
    delete_image_parser.add_argument("image_id", help="File path or image identifier")
    delete_image_parser.set_defaults(func=cmd_delete_image)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
