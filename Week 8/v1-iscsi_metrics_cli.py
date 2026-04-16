#!/usr/bin/env python3
"""
Cluster-wide iSCSI utility metrics CLI.

Fetches data from the Week 7 daemonset HTTP endpoint running on each iscsi-target node
and computes these metrics:
1. List of workers configured as iSCSI targets
2. Number of projected rootfs/PE images per worker node
3. Total number of images projected
4. Number of deleted images per worker node (tracked over time via local state)

Deleted-image counts are not available from LIO/configfs directly. This CLI keeps a local
state file and increments counters when previously seen image IDs disappear.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set
from urllib.error import URLError
from urllib.request import urlopen

from kubernetes import client, config
from kubernetes.client import CoreV1Api
from kubernetes.client.rest import ApiException


DEFAULT_NAMESPACE = "default"
DEFAULT_NODE_LABEL = "node-role.kubernetes.io/iscsi-target"
DEFAULT_POD_LABEL = "app=iscsi-target-http"
DEFAULT_STATE_FILE = Path.cwd() / ".cache" / "iscsi-metrics" / "state.json"


class CliError(RuntimeError):
    pass


def build_core_v1() -> CoreV1Api:
    try:
        config.load_kube_config()
    except Exception:
        try:
            config.load_incluster_config()
        except Exception as exc:
            raise CliError(
                "Could not load Kubernetes config. Ensure kubeconfig is available "
                "or run inside a Kubernetes pod with service account access."
            ) from exc
    return client.CoreV1Api()


def get_iscsi_target_nodes(v1: CoreV1Api, node_label: str) -> List[str]:
    try:
        payload = v1.list_node(label_selector=node_label)
    except ApiException as exc:
        raise CliError(f"Failed to list nodes: {exc}") from exc

    return sorted(
        item.metadata.name
        for item in payload.items
        if item.metadata and item.metadata.name
    )


def get_daemonset_pods(
    v1: CoreV1Api, namespace: str, pod_label: str
) -> Dict[str, Dict[str, str]]:
    try:
        payload = v1.list_namespaced_pod(namespace=namespace, label_selector=pod_label)
    except ApiException as exc:
        raise CliError(
            f"Failed to list pods in namespace '{namespace}': {exc}"
        ) from exc

    mapping: Dict[str, Dict[str, str]] = {}
    for item in payload.items:
        metadata = item.metadata
        spec = item.spec
        status = item.status
        phase = status.phase if status else None
        pod_name = metadata.name if metadata else None
        node_name = spec.node_name if spec else None
        pod_ip = status.pod_ip if status else None
        if pod_name and node_name and phase == "Running" and pod_ip:
            mapping[node_name] = {"pod_name": pod_name, "pod_ip": pod_ip}
    return mapping


def get_metrics_raw_from_pod_ip(pod_ip: str) -> dict:
    url = f"http://{pod_ip}:9000/metrics/raw"
    try:
        with urlopen(url, timeout=12) as resp:
            output = resp.read().decode("utf-8")
    except URLError as exc:
        raise CliError(f"HTTP request failed for {url}: {exc}") from exc
    except Exception as exc:
        raise CliError(f"Unable to query endpoint {url}: {exc}") from exc

    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CliError(f"Endpoint {url} returned non-JSON response") from exc

    if payload.get("status") != "success":
        raise CliError(f"Endpoint {url} error response: {payload}")

    return payload.get("metrics", {})


def choose_workers(all_workers: List[str], node_arg: str) -> List[str]:
    if node_arg.lower() == "all":
        return all_workers

    requested = [n.strip() for n in node_arg.split(",") if n.strip()]
    missing = sorted(set(requested) - set(all_workers))
    if missing:
        raise CliError(
            "Requested node(s) not found in iscsi-target worker list: "
            + ", ".join(missing)
        )
    return sorted(set(requested))


def print_target_nodes(
    workers: List[str], pods_by_node: Dict[str, Dict[str, str]]
) -> None:
    print("Discovered iSCSI target nodes and daemonset endpoints")
    print("=" * 70)
    if not workers:
        print("No nodes found for the iscsi-target node label selector.")
        return

    for worker in workers:
        pod_info = pods_by_node.get(worker)
        if pod_info:
            pod_name = pod_info.get("pod_name", "-")
            pod_ip = pod_info.get("pod_ip", "-")
            print(
                f"- node={worker} pod={pod_name} endpoint=http://{pod_ip}:9000/metrics/raw"
            )
        else:
            print(f"- node={worker} pod=<none> endpoint=<unavailable>")


def extract_node_image_set(metrics: dict) -> Set[str]:
    """
    Build a stable per-node image identity set from LUN mappings.

    Preferred identity is backend object name from symlink target (for example disk01).
    Fallback identity is IQN + LUN directory name.
    """
    image_ids: Set[str] = set()

    iscsi = metrics.get("iscsi", {})
    if not isinstance(iscsi, dict):
        return image_ids

    for iqn, iqn_data in iscsi.items():
        if not isinstance(iqn_data, dict):
            continue

        tpgt_1 = iqn_data.get("tpgt_1")
        if not isinstance(tpgt_1, dict):
            continue

        lun_root = tpgt_1.get("lun")
        if not isinstance(lun_root, dict):
            continue

        for lun_name, lun_data in lun_root.items():
            if not (isinstance(lun_name, str) and lun_name.startswith("lun_")):
                continue

            identity = None
            if isinstance(lun_data, dict):
                for _, value in lun_data.items():
                    if isinstance(value, str) and value.startswith("symlink ->"):
                        target = value.split("->", 1)[1].strip()
                        if "/target/core/" in target:
                            identity = target.split("/")[-1]
                            break

            if not identity:
                identity = f"{iqn}:{lun_name}"

            image_ids.add(identity)

    return image_ids


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "nodes": {}}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("version", 1)
                data.setdefault("nodes", {})
                return data
    except (OSError, json.JSONDecodeError):
        pass

    return {"version": 1, "nodes": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def update_deleted_counters(
    state: dict,
    current_node_images: Dict[str, Set[str]],
    workers: List[str],
) -> Dict[str, int]:
    nodes_state = state.setdefault("nodes", {})
    deleted_counts: Dict[str, int] = {}

    for worker in workers:
        prev = nodes_state.get(worker, {})
        prev_images = set(prev.get("images", [])) if isinstance(prev, dict) else set()
        prev_deleted = (
            int(prev.get("deleted_count", 0)) if isinstance(prev, dict) else 0
        )

        current_images = current_node_images.get(worker, set())
        removed = prev_images - current_images
        deleted_count = prev_deleted + len(removed)

        nodes_state[worker] = {
            "images": sorted(current_images),
            "deleted_count": deleted_count,
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        deleted_counts[worker] = deleted_count

    return deleted_counts


def format_report(
    workers: List[str],
    projected_per_worker: Dict[str, int],
    total_projected: int,
    deleted_per_worker: Dict[str, int],
) -> str:
    lines: List[str] = []
    lines.append("iSCSI Utility Metrics")
    lines.append("=" * 70)
    lines.append("1) List of workers configured as iSCSI targets")
    lines.append("   " + (", ".join(workers) if workers else "None"))
    lines.append("")
    lines.append("2) Number of projected rootfs/PE images per worker node")
    if workers:
        for worker in workers:
            lines.append(f"   - {worker}: {projected_per_worker.get(worker, 0)}")
    else:
        lines.append("   None")
    lines.append("")
    lines.append(f"3) Total number of images projected: {total_projected}")
    lines.append("")
    lines.append("4) Number of deleted images per worker node")
    if workers:
        for worker in workers:
            lines.append(f"   - {worker}: {deleted_per_worker.get(worker, 0)}")
    else:
        lines.append("   None")
    lines.append("")
    lines.append(
        "Note: Deleted-image counters are tracked by this CLI state file over time."
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch iSCSI utility metrics from iscsi-target daemonset pods"
    )
    parser.add_argument(
        "--namespace", default=DEFAULT_NAMESPACE, help="Kubernetes namespace"
    )
    parser.add_argument(
        "--node-label",
        default=DEFAULT_NODE_LABEL,
        help="Node label selector for iscsi-target workers",
    )
    parser.add_argument(
        "--pod-label",
        default=DEFAULT_POD_LABEL,
        help="Pod label selector for daemonset pods",
    )
    parser.add_argument(
        "--state-file",
        default=str(DEFAULT_STATE_FILE),
        help="Path to state JSON used for deleted-image counters",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report",
    )
    parser.add_argument(
        "--node",
        default="all",
        help="Node name (or comma-separated nodes) to fetch. Use 'all' for cluster-wide metrics",
    )
    parser.add_argument(
        "--list-nodes",
        action="store_true",
        help="List iscsi-target nodes with daemonset pod IP endpoints and exit",
    )
    parser.add_argument(
        "--no-state-update",
        action="store_true",
        help="Do not update deleted-image counters (read-only run)",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset state file and exit",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    state_path = Path(args.state_file).expanduser()

    if args.reset_state:
        save_state(state_path, {"version": 1, "nodes": {}})
        print(f"State reset: {state_path}")
        return 0

    try:
        v1 = build_core_v1()
        all_workers = get_iscsi_target_nodes(v1, args.node_label)
        pods_by_node = get_daemonset_pods(v1, args.namespace, args.pod_label)

        if args.list_nodes:
            print_target_nodes(all_workers, pods_by_node)
            return 0

        workers = choose_workers(all_workers, args.node)

        current_node_images: Dict[str, Set[str]] = {}
        errors: Dict[str, str] = {}
        endpoints: Dict[str, str] = {}

        for worker in workers:
            pod_info = pods_by_node.get(worker)
            if not pod_info:
                errors[worker] = "No running daemonset pod on node"
                current_node_images[worker] = set()
                continue

            pod_ip = pod_info.get("pod_ip")
            if not pod_ip:
                errors[worker] = "Daemonset pod has no pod IP"
                current_node_images[worker] = set()
                continue

            endpoints[worker] = f"http://{pod_ip}:9000/metrics/raw"

            try:
                metrics = get_metrics_raw_from_pod_ip(pod_ip)
                current_node_images[worker] = extract_node_image_set(metrics)
            except CliError as exc:
                errors[worker] = str(exc)
                current_node_images[worker] = set()

        projected_per_worker = {
            worker: len(current_node_images.get(worker, set())) for worker in workers
        }
        total_projected = sum(projected_per_worker.values())

        state = load_state(state_path)
        if args.no_state_update:
            deleted_per_worker = {
                worker: int(
                    state.get("nodes", {}).get(worker, {}).get("deleted_count", 0)
                )
                for worker in workers
            }
        else:
            deleted_per_worker = update_deleted_counters(
                state, current_node_images, workers
            )
            save_state(state_path, state)

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "selected_nodes": workers,
            "selected_mode": args.node,
            "workers_configured_as_iscsi_targets": workers,
            "endpoints_per_worker": endpoints,
            "projected_images_per_worker": projected_per_worker,
            "total_projected_images": total_projected,
            "deleted_images_per_worker": deleted_per_worker,
            "errors": errors,
            "state_file": str(state_path),
            "notes": [
                "Deleted-image counters are inferred from state snapshots maintained by this CLI.",
                "If state is reset or missing, deleted counters restart from zero.",
            ],
        }

        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(
                format_report(
                    workers,
                    projected_per_worker,
                    total_projected,
                    deleted_per_worker,
                )
            )
            if errors:
                print("\nWarnings:")
                for node, err in sorted(errors.items()):
                    print(f"- {node}: {err}")
                return 2

        return 0

    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
