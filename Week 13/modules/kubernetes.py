import json
from typing import Tuple, List, Optional, Dict
from .utils import run_command
from pathlib import Path
import yaml

DEFAULT_TARGET_SELECTOR_VALUE = "iscsi-role=target"
DEFAULT_INITIATOR_SELECTOR_VALUE = "iscsi-role=initiator"

CLI_CONFIG_PATH = Path("/etc/iscsi/config.yml")
TARGET_SELECTOR_KEY = "target-selector"
INITIATOR_SELECTOR_KEY = "initiator-selector"


def _load_config() -> dict:
    if not CLI_CONFIG_PATH.exists():
        return {}
    with CLI_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, dict) else {}


def _save_config(config: dict) -> None:
    CLI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CLI_CONFIG_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)


def _get_config_value(key: str, default: str) -> Tuple[str, str | None]:
    try:
        config = _load_config()
        return config.get(key, default), None
    except Exception as exc:
        return default, str(exc)


def _set_config_value(key: str, value: str) -> Tuple[str, str | None]:
    try:
        config = _load_config()
        config[key] = value
        _save_config(config)
        return value, None
    except Exception as exc:
        return "", str(exc)


def get_target_node_label() -> Tuple[str, str | None]:
    return _get_config_value(TARGET_SELECTOR_KEY, DEFAULT_TARGET_SELECTOR_VALUE)


def set_target_node_label(label: str) -> Tuple[str, str | None]:
    return _set_config_value(TARGET_SELECTOR_KEY, label)


def get_initiator_node_label() -> Tuple[str, str | None]:
    return _get_config_value(INITIATOR_SELECTOR_KEY, DEFAULT_INITIATOR_SELECTOR_VALUE)


def set_initator_node_label(label: str) -> Tuple[str, str | None]:
    return _set_config_value(INITIATOR_SELECTOR_KEY, label)


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


def get_kubernetes_nodes(
    node_selector: str, full_info: bool = False
) -> Tuple[List[str] | Dict[str, Dict], Optional[str]]:
    if full_info:
        command = f"kubectl get nodes -l {node_selector}  -o json"
        result = run_command(command)
        if result.returncode != 0:
            message = (
                result.stderr.strip()
                or result.stdout.strip()
                or f"exit {result.returncode}"
            )
            return {}, message
        try:
            data = json.loads(result.stdout)
            nodes_data = {}
            for item in data.get("items", []):
                metadata = item.get("metadata", {})
                status = item.get("status", {})
                node_info = status.get("nodeInfo", {})
                ready_condition = next(
                    (
                        condition
                        for condition in status.get("conditions", [])
                        if condition.get("type") == "Ready"
                    ),
                    None,
                )
                uid = metadata.get("uid")
                nodes_data[uid] = {
                    "name": metadata.get("name"),
                    "addresses": status.get("addresses", []),
                    "node_info": {
                        "arch": node_info.get("architecture"),
                        "os": node_info.get("operatingSystem"),
                        "os_image": node_info.get("osImage"),
                    },
                    "status": (
                        "Ready"
                        if ready_condition and ready_condition.get("status") == "True"
                        else "NotReady"
                    ),
                }
            return nodes_data, None
        except json.JSONDecodeError as exc:
            return [], f"invalid JSON output: {exc}"
    else:
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


def get_kubernetes_node_json(node_name: str) -> Tuple[dict, Optional[str]]:
    return run_kubectl_json(f"kubectl get node {node_name} -o json")


def get_node_labels(node_name: str) -> Tuple[Dict[str, str], Optional[str]]:
    payload, error = get_kubernetes_node_json(node_name)
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
