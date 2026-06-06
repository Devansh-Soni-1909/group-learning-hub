from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from .common import emit_output, run_command
from .iscsi_data import collect_target_images

CONFIGFS_TARGET_PATH = "/sys/kernel/config/target/iscsi"


def _confirm(prompt: str) -> bool:
    try:
        response = input(f"{prompt} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return response in {"y", "yes"}


def find_target_image(
    node: str, tpgt_name: str, image_name: str, base_path: str
) -> Tuple[Optional[dict], List[str]]:
    images, _, errors = collect_target_images(node, base_path)
    if errors:
        return None, errors
    for image in images:
        if image.tpgt_name != tpgt_name:
            continue
        identity = " ".join(
            [image.image_name, image.udev_path, image.object_path, image.lun_name]
        )
        if image_name in identity or os.path.basename(image.udev_path) == image_name:
            return image.__dict__, []
    return None, [f"{node}: image {image_name} not found under {tpgt_name}"]


def remove_target_image(
    node: str, tpgt_name: str, image_name: str, base_path: str, force: bool
) -> dict:
    image, errors = find_target_image(node, tpgt_name, image_name, base_path)
    if errors:
        raise SystemExit("; ".join(errors))
    if image is None:
        raise SystemExit(f"{node}: image {image_name} not found under {tpgt_name}")

    tpgt_path = f"{base_path}/{image['iqn']}/{tpgt_name}/lun/{image['lun_name']}"
    if not force and not _confirm(
        f"Delete projected image {image['image_name']} from {node} {tpgt_name} ({image['lun_name']})?"
    ):
        raise SystemExit("Deletion cancelled")

    command = f'pdsh -w {node} "rmdir {tpgt_path} 2>/dev/null || rm -rf {tpgt_path}"'
    result = run_command(command)
    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        raise SystemExit(f"{node}: unable to delete image: {message}")

    return {"deleted": image, "command": command}


def cmd_delete_image(args) -> None:
    nodes = [node.strip() for node in args.name.split(",") if node.strip()]

    if not nodes:
        raise SystemExit("No target node provided")

    if len(nodes) == 1:
        result = remove_target_image(
            nodes[0], args.tpgt, args.image_id, CONFIGFS_TARGET_PATH, args.force
        )
    else:
        results = []
        errors: List[str] = []
        with ThreadPoolExecutor() as executor:
            future_map = {
                executor.submit(
                    remove_target_image,
                    node,
                    args.tpgt,
                    args.image_id,
                    CONFIGFS_TARGET_PATH,
                    args.force,
                ): node
                for node in nodes
            }
            for future in as_completed(future_map):
                try:
                    results.append(future.result())
                except SystemExit as exc:
                    errors.append(str(exc))

        if errors:
            raise SystemExit("; ".join(errors))

        result = {"results": results}

    def formatter(payload: dict) -> str:
        if "results" in payload:
            lines = ["Deleted projected image"]
            for item in payload.get("results", []):
                deleted = item.get("deleted", {})
                lines.extend(
                    [
                        f"Node: {deleted.get('node', '-')}",
                        "Role: target",
                        f"TPGT: {deleted.get('tpgt_name', '-')}",
                        f"LUN: {deleted.get('lun_name', '-')}",
                        f"Image: {deleted.get('image_name', '-')}",
                        f"Command: {item.get('command', '-')}",
                        "",
                    ]
                )
            return "\n".join(lines).rstrip()

        deleted = payload.get("deleted", {})
        lines = [
            "Deleted projected image",
            f"Node: {deleted.get('node', '-')}",
            "Role: target",
            f"TPGT: {deleted.get('tpgt_name', '-')}",
            f"LUN: {deleted.get('lun_name', '-')}",
            f"Image: {deleted.get('image_name', '-')}",
            f"Command: {payload.get('command', '-')}",
        ]
        return "\n".join(lines)

    emit_output(result, args.json, formatter=formatter)
