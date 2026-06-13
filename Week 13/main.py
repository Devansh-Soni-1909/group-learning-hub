from __future__ import annotations

import argparse

from modules import (
    DEFAULT_INITIATOR_SELECTOR,
    DEFAULT_TARGET_SELECTOR,
    cmd_get_nodes,
    cmd_get_configs,
    cmd_get_tpgts,
    cmd_get_luns,
    cmd_get_images,
    cmd_get_metrics,
    cmd_get_sessions,
    cmd_get_mount_status,
    cmd_get_errors,
    cmd_describe_node,
    cmd_describe_config,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iscsi", description="Standalone iSCSI CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    get_parser = subparsers.add_parser("get", help="Read-only iSCSI commands")
    get_subparsers = get_parser.add_subparsers(dest="get_command", required=True)

    # cmd: get node
    nodes_parser = get_subparsers.add_parser("nodes", help="List iSCSI target nodes")
    nodes_parser.add_argument(
        "--target",
        action="store_true",
        default=False,
        help="Fetches iSCSI target nodes",
    )
    nodes_parser.add_argument(
        "--initiator",
        action="store_true",
        default=False,
        help="Fetches iSCSI initiator nodes",
    )
    nodes_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )
    nodes_parser.set_defaults(func=cmd_get_nodes)

    # cmd: get configs
    configs_parser = get_subparsers.add_parser(
        "configs", help="List all the target node configuration versions"
    )
    configs_parser.add_argument("--name", default=None, help="Target node to inspect")
    configs_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )

    configs_parser.set_defaults(func=cmd_get_configs)

    # cmd: get luns
    luns_parser = get_subparsers.add_parser(
        "luns", help="List LUNs for one or more target nodes"
    )
    luns_parser.add_argument("--name", default=None, help="Target node to inspect")
    luns_parser.add_argument(
        "--image-type",
        choices=["all", "pe", "rootfs"],
        default="all",
        help="Limit output to PE or rootfs LUNs",
    )
    luns_parser.add_argument(
        "--metrics", action="store_true", default=False, help="Include LUN metrics"
    )
    luns_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )

    luns_parser.set_defaults(func=cmd_get_luns)

    # cmd: get tpgts
    tpgts_parser = get_subparsers.add_parser(
        "tpgts", help="List TPGTs for one or more target nodes"
    )
    tpgts_parser.add_argument("--name", default=None, help="Target node to inspect")
    tpgts_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )

    tpgts_parser.set_defaults(func=cmd_get_tpgts)

    # cmd: get images
    images_parser = get_subparsers.add_parser("images", help="List projected images")
    images_parser.add_argument("--name", default=None, help="Target node to inspect")
    images_parser.add_argument(
        "--image-type",
        choices=["all", "pe", "rootfs"],
        default="all",
        help="Limit output to PE or rootfs images",
    )
    images_parser.add_argument(
        "--metrics", action="store_true", default=False, help="Include LUN metrics"
    )
    images_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )

    images_parser.set_defaults(func=cmd_get_images)

    # cmd: get metrics
    metrics_parser = get_subparsers.add_parser("metrics", help="Show iSCSI metrics")
    metrics_parser.add_argument("--name", default=None, help="Target node to inspect")
    metrics_parser.add_argument(
        "--config-file",
        default=None,
        help="Backup config file path to compare against; defaults to the latest backup version",
    )
    metrics_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )

    metrics_parser.set_defaults(func=cmd_get_metrics)

    # cmd: get sessions
    sessions_parser = get_subparsers.add_parser(
        "sessions", help="Show initiator mount/session state"
    )
    sessions_parser.add_argument(
        "--name", default=None, help="Initiator node to inspect"
    )
    sessions_parser.add_argument(
        "--label",
        default=DEFAULT_INITIATOR_SELECTOR,
        help="Kubernetes label selector for initiator nodes",
    )
    sessions_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )

    sessions_parser.set_defaults(func=cmd_get_sessions)

    # cmd: get mount-status
    mount_status_parser = get_subparsers.add_parser(
        "mount-status", help="Show initiator mount status"
    )
    mount_status_parser.add_argument(
        "--name", default=None, help="Initiator node to inspect"
    )
    mount_status_parser.add_argument(
        "--label",
        default=DEFAULT_INITIATOR_SELECTOR,
        help="Kubernetes label selector for initiator nodes",
    )
    mount_status_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )

    mount_status_parser.set_defaults(func=cmd_get_mount_status)

    # cmd: get errors
    errors_parser = get_subparsers.add_parser(
        "errors", help="Scan recent logs for storage and network errors"
    )
    errors_parser.add_argument("--name", default=None, help="Node to inspect")
    errors_parser.add_argument(
        "--label",
        default=DEFAULT_TARGET_SELECTOR,
        help="Kubernetes label selector for target nodes",
    )
    errors_parser.add_argument(
        "--lines",
        type=int,
        default=200,
        help="Number of recent log lines to collect per node",
    )
    errors_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )

    errors_parser.set_defaults(func=cmd_get_errors)

    describe_parser = subparsers.add_parser(
        "describe", help="Detailed iSCSI resource descriptions"
    )
    describe_subparsers = describe_parser.add_subparsers(
        dest="describe_command", required=True
    )
    # cmd: describe node
    node_parser = describe_subparsers.add_parser(
        "node", help="Show a detailed iSCSI summary for one node"
    )
    node_parser.add_argument("--name", default=None, help="Node name to inspect")
    node_parser.add_argument(
        "--label",
        default=DEFAULT_TARGET_SELECTOR,
        help="Kubernetes label selector for iSCSI nodes when listing",
    )
    node_parser.add_argument(
        "--metrics", action="store_true", default=False, help="Include LUN metrics"
    )
    node_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )

    node_parser.set_defaults(func=cmd_describe_node)

    # cmd: describe config
    config_parser = describe_subparsers.add_parser(
        "config", help="Show a detailed summary of the mentioned node"
    )
    config_parser.add_argument("--node", default=None, help="None name to inspect")
    config_parser.add_argument(
        "--file-path", default=None, help="Path of the configuration file to describe"
    )
    config_parser.add_argument(
        "--out-file",
        default=None,
        help="Save output to file (default: iscsi-output.txt)",
    )

    config_parser.set_defaults(func=cmd_describe_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
