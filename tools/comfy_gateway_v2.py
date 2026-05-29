from __future__ import annotations

import json
import os
import time
import uuid
import hashlib
import base64
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


COMFY_ROOT = Path(os.getenv("COMFY_ROOT", r"D:\comfyui\ComfyUI_windows_portable\ComfyUI")).resolve()
PORTABLE_ROOT = COMFY_ROOT.parent
COMFY_URL = os.getenv("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
HOST = os.getenv("COMFY_GATEWAY_HOST", "0.0.0.0")
PORT = int(os.getenv("COMFY_GATEWAY_PORT", "9000"))
TOKEN = os.getenv("COMFY_GATEWAY_TOKEN", "").strip()
WORKFLOW_ROOTS = [
    ("user", (COMFY_ROOT / "user" / "default" / "workflows").resolve()),
    ("api", (PORTABLE_ROOT / "api_workflows").resolve()),
]
CONVERTER_VERSION = "2026-05-28.1"
CONVERTED_ROOT = WORKFLOW_ROOTS[1][1] / "__converted__"
CONVERT_MANIFEST_PATH = CONVERTED_ROOT / "manifest.json"


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _json_hash(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(payload)


def _classify_workflow(data: Any) -> str:
    if isinstance(data, dict) and isinstance(data.get("prompt"), dict):
        return "api_wrapper"
    if isinstance(data, dict) and data and all(
        str(k).isdigit() and isinstance(v, dict) and "class_type" in v for k, v in data.items()
    ):
        return "api_prompt"
    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        return "ui_workflow"
    return "unknown"


def _safe_workflow_path(value: Any) -> Path:
    text = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not text:
        raise ValueError("workflow path is required")
    parts = Path(text).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid workflow path")
    for _name, root in WORKFLOW_ROOTS:
        candidate = (root / text).resolve()
        if root == candidate or root in candidate.parents:
            if candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".json":
                return candidate
    raise FileNotFoundError(text)


def _safe_workflow_write_path(root_name: Any, value: Any) -> Path:
    root_key = str(root_name or "user").strip().lower()
    roots = {name: root for name, root in WORKFLOW_ROOTS}
    if root_key not in roots:
        raise ValueError("workflow root must be user or api")
    text = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not text:
        raise ValueError("workflow path is required")
    if not text.lower().endswith(".json"):
        raise ValueError("workflow path must end with .json")
    parts = Path(text).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid workflow path")
    root = roots[root_key]
    candidate = (root / text).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("workflow path escapes root")
    return candidate


def _decode_upload_content(body: dict[str, Any]) -> bytes:
    if isinstance(body.get("content_b64"), str):
        return base64.b64decode(body["content_b64"], validate=True)
    if isinstance(body.get("content"), str):
        return body["content"].encode("utf-8")
    raise ValueError("content or content_b64 is required")


def _upload_workflow(body: dict[str, Any]) -> dict[str, Any]:
    target = _safe_workflow_write_path(body.get("root"), body.get("path"))
    raw = _decode_upload_content(body)
    if len(raw) > 50 * 1024 * 1024:
        raise ValueError("workflow upload is too large")
    data = json.loads(raw.decode("utf-8-sig"))
    kind = _classify_workflow(data)
    if kind == "unknown":
        raise ValueError("uploaded JSON is not a recognized workflow")
    expected_sha = str(body.get("sha256") or "").strip().lower()
    actual_sha = _sha256_bytes(raw)
    if expected_sha and expected_sha != actual_sha:
        raise ValueError("sha256 mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    root = {name: path for name, path in WORKFLOW_ROOTS}[str(body.get("root") or "user").strip().lower()]
    return {
        "ok": True,
        "root": str(body.get("root") or "user"),
        "path": target.relative_to(root).as_posix(),
        "kind": kind,
        "bytes": len(raw),
        "sha256": actual_sha,
    }


def _safe_model_path(category: Any, value: Any) -> Path:
    category_text = str(category or "").replace("\\", "/").strip().strip("/")
    path_text = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not category_text or not path_text:
        raise ValueError("category and path are required")
    category_parts = Path(category_text).parts
    path_parts = Path(path_text).parts
    if any(part in {"", ".", ".."} for part in [*category_parts, *path_parts]):
        raise ValueError("invalid model path")
    root = (COMFY_ROOT / "models" / category_text).resolve()
    candidate = (root / path_text).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("model path escapes category root")
    return candidate


def _check_models(body: dict[str, Any]) -> dict[str, Any]:
    items = body.get("items")
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "")
        rel = str(item.get("path") or "")
        try:
            path = _safe_model_path(category, rel)
            exists = path.exists() and path.is_file()
            rows.append(
                {
                    "category": category,
                    "path": rel,
                    "exists": exists,
                    "bytes": path.stat().st_size if exists else 0,
                }
            )
        except Exception as exc:
            rows.append({"category": category, "path": rel, "exists": False, "error": str(exc)})
    return {"ok": True, "items": rows}


def _list_workflows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root_name, root in WORKFLOW_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            if path.resolve() == CONVERT_MANIFEST_PATH.resolve():
                continue
            try:
                data = _read_json(path)
                kind = _classify_workflow(data)
            except Exception:
                kind = "invalid_json"
            rel = path.relative_to(root).as_posix()
            rows.append(
                {
                    "root": root_name,
                    "path": rel,
                    "name": path.name,
                    "kind": kind,
                    "can_run": kind in {"api_prompt", "api_wrapper"},
                    "bytes": path.stat().st_size,
                    "updated_at": int(path.stat().st_mtime),
                }
            )
    return rows


def _load_api_prompt(path: Path) -> dict[str, Any]:
    data = _read_json(path)
    kind = _classify_workflow(data)
    if kind == "api_wrapper":
        prompt = data.get("prompt")
    elif kind == "api_prompt":
        prompt = data
    else:
        raise ValueError(f"workflow is {kind}, export API format first")
    if not isinstance(prompt, dict):
        raise ValueError("invalid API prompt")
    return prompt


def _api_output_path_for_ui_workflow(path: Path) -> Path:
    source_root = WORKFLOW_ROOTS[0][1]
    try:
        rel = path.resolve().relative_to(source_root)
    except Exception:
        rel = Path(path.name)
    safe_parts = [part for part in rel.parts if part not in {"", ".", ".."}]
    rel = Path(*safe_parts) if safe_parts else Path(path.name)
    return (CONVERTED_ROOT / rel).with_suffix(".api.json").resolve()


def _source_rel_for_manifest(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKFLOW_ROOTS[0][1]).as_posix()
    except Exception:
        return path.name


def _load_convert_manifest() -> dict[str, Any]:
    if not CONVERT_MANIFEST_PATH.exists():
        return {"version": 1, "items": {}}
    try:
        data = _read_json(CONVERT_MANIFEST_PATH)
    except Exception:
        return {"version": 1, "items": {}}
    if not isinstance(data, dict):
        return {"version": 1, "items": {}}
    if not isinstance(data.get("items"), dict):
        data["items"] = {}
    return data


def _write_convert_manifest(manifest: dict[str, Any]) -> None:
    CONVERT_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONVERT_MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _object_input_order(object_info: dict[str, Any], class_type: str) -> list[str]:
    item = object_info.get(class_type) if isinstance(object_info, dict) else None
    inputs = item.get("input") if isinstance(item, dict) else None
    if not isinstance(inputs, dict):
        return []
    ordered: list[str] = []
    for group in ("required", "optional"):
        values = inputs.get(group)
        if isinstance(values, dict):
            ordered.extend([str(key) for key in values.keys()])
    return ordered


def _ui_link_origins(data: dict[str, Any]) -> dict[int, list[Any]]:
    origins: dict[int, list[Any]] = {}
    for item in data.get("links") or []:
        if isinstance(item, list) and len(item) >= 3:
            try:
                origins[int(item[0])] = [str(item[1]), int(item[2])]
            except Exception:
                continue
        elif isinstance(item, dict):
            try:
                link_id = int(item.get("id"))
                origin_id = str(item.get("origin_id") or item.get("from_node") or item.get("source_id"))
                origin_slot = int(item.get("origin_slot") or item.get("from_slot") or item.get("source_slot") or 0)
                origins[link_id] = [origin_id, origin_slot]
            except Exception:
                continue
    return origins


def _ui_workflow_to_api_prompt(data: dict[str, Any], object_info: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("workflow does not contain UI nodes")
    link_origins = _ui_link_origins(data)
    prompt: dict[str, Any] = {}
    warnings: list[str] = []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        class_type = str(node.get("type") or "").strip()
        if not node_id or not class_type:
            continue
        inputs_payload: dict[str, Any] = {}

        for input_item in node.get("inputs") or []:
            if not isinstance(input_item, dict):
                continue
            name = str(input_item.get("name") or "").strip()
            if not name:
                continue
            link_id = input_item.get("link")
            if link_id is None:
                continue
            try:
                origin = link_origins.get(int(link_id))
            except Exception:
                origin = None
            if origin:
                inputs_payload[name] = origin

        order = _object_input_order(object_info, class_type)
        if not order:
            warnings.append(f"{node_id}:{class_type} object_info missing; widget mapping may be incomplete")

        widgets = node.get("widgets_values")
        if isinstance(widgets, dict):
            for key, value in widgets.items():
                name = str(key)
                if name and name not in inputs_payload:
                    inputs_payload[name] = value
        elif isinstance(widgets, list):
            widget_index = 0
            for name in order:
                if name in inputs_payload:
                    continue
                if widget_index >= len(widgets):
                    break
                inputs_payload[name] = widgets[widget_index]
                widget_index += 1
            if widget_index < len(widgets):
                warnings.append(
                    f"{node_id}:{class_type} has {len(widgets) - widget_index} unmapped widget value(s)"
                )

        title = str((node.get("properties") or {}).get("Node name for S&R") or node.get("title") or "").strip()
        prompt[node_id] = {
            "class_type": class_type,
            "inputs": inputs_payload,
            "_meta": {"title": title or class_type},
        }

    if not prompt:
        raise ValueError("no runnable nodes found in UI workflow")
    return prompt, warnings


def _convert_ui_workflow(
    path: Path,
    *,
    object_info: dict[str, Any],
    object_info_hash: str,
    manifest: dict[str, Any],
    force: bool = False,
    overwrite: bool = True,
    comfyui_version: str = "",
) -> dict[str, Any]:
    data = _read_json(path)
    kind = _classify_workflow(data)
    if kind in {"api_prompt", "api_wrapper"}:
        return {
            "ok": True,
            "source_path": path.name,
            "kind": kind,
            "already_api": True,
            "skipped": True,
            "skip_reason": "already API format",
            "output_path": path.name,
            "warnings": [],
        }
    if kind != "ui_workflow" or not isinstance(data, dict):
        raise ValueError(f"workflow is {kind}, cannot convert")
    output_path = _api_output_path_for_ui_workflow(path)
    source_rel = _source_rel_for_manifest(path)
    source_hash = _sha256_file(path)
    output_rel = output_path.relative_to(WORKFLOW_ROOTS[1][1]).as_posix()
    manifest_items = manifest.setdefault("items", {})
    previous = manifest_items.get(source_rel) if isinstance(manifest_items, dict) else None
    if (
        not force
        and output_path.exists()
        and isinstance(previous, dict)
        and previous.get("source_hash") == source_hash
        and previous.get("object_info_hash") == object_info_hash
        and previous.get("converter_version") == CONVERTER_VERSION
        and previous.get("output_path") == output_rel
    ):
        return {
            "ok": True,
            "source_path": source_rel,
            "kind": "api_prompt",
            "already_api": False,
            "skipped": True,
            "skip_reason": "source unchanged",
            "output_path": output_rel,
            "warnings": [],
        }
    prompt, warnings = _ui_workflow_to_api_prompt(data, object_info)
    if output_path.exists() and not overwrite:
        raise FileExistsError(str(output_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(prompt, ensure_ascii=False, indent=2), encoding="utf-8")
    output_hash = _sha256_file(output_path)
    manifest_items[source_rel] = {
        "source_hash": source_hash,
        "output_hash": output_hash,
        "output_path": output_rel,
        "object_info_hash": object_info_hash,
        "converter_version": CONVERTER_VERSION,
        "comfyui_version": comfyui_version,
        "converted_at": int(time.time()),
        "nodes": len(prompt),
    }
    return {
        "ok": True,
        "source_path": source_rel,
        "kind": "api_prompt",
        "already_api": False,
        "skipped": False,
        "output_path": output_rel,
        "warnings": warnings,
        "nodes": len(prompt),
    }


def _convert_workflows(body: dict[str, Any]) -> dict[str, Any]:
    overwrite = bool(body.get("overwrite", True))
    force = bool(body.get("force", False))
    paths = body.get("paths")
    if isinstance(paths, list) and paths:
        targets = [_safe_workflow_path(path) for path in paths]
    else:
        targets = []
        for item in _list_workflows():
            if item.get("root") == "user" and item.get("kind") == "ui_workflow":
                targets.append(_safe_workflow_path(item.get("path")))
    if not targets:
        return {
            "ok": True,
            "converted": 0,
            "skipped": 0,
            "failed": 0,
            "force": force,
            "items": [],
        }
    object_info = _comfy_request("GET", "/object_info", timeout=60)
    object_info = object_info if isinstance(object_info, dict) else {}
    object_info_hash = _json_hash(object_info)
    try:
        stats = _comfy_request("GET", "/system_stats", timeout=30)
        comfyui_version = str(((stats or {}).get("system") or {}).get("comfyui_version") or "")
    except Exception:
        comfyui_version = ""
    manifest = _load_convert_manifest()
    items: list[dict[str, Any]] = []
    manifest_changed = False
    for path in targets:
        try:
            item = _convert_ui_workflow(
                path,
                object_info=object_info,
                object_info_hash=object_info_hash,
                manifest=manifest,
                force=force,
                overwrite=overwrite,
                comfyui_version=comfyui_version,
            )
            items.append(item)
            if item.get("ok") and not item.get("skipped"):
                manifest_changed = True
        except Exception as exc:
            items.append({"ok": False, "source_path": path.name, "error": str(exc)})
    if manifest_changed:
        _write_convert_manifest(manifest)
    converted = sum(1 for item in items if item.get("ok") and not item.get("already_api") and not item.get("skipped"))
    skipped = sum(1 for item in items if item.get("ok") and item.get("skipped"))
    failed = sum(1 for item in items if not item.get("ok"))
    return {
        "ok": failed == 0,
        "converted": converted,
        "skipped": skipped,
        "failed": failed,
        "force": force,
        "object_info_hash": object_info_hash,
        "converter_version": CONVERTER_VERSION,
        "items": items,
    }


def _apply_prompt_overrides(prompt: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    positive = str(body.get("prompt_text") or body.get("prompt") or "").strip()
    negative = str(body.get("negative_prompt") or "").strip()
    width = _int_or_none(body.get("width"))
    height = _int_or_none(body.get("height"))
    steps = _int_or_none(body.get("steps"))
    seed = _int_or_none(body.get("seed"))
    batch_size = _int_or_none(body.get("batch_size"))

    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        title = str((node.get("_meta") or {}).get("title") or "").lower()

        if positive and class_type == "CLIPTextEncode" and isinstance(inputs.get("text"), str):
            if ("negative" in title or "neg" in title) and negative:
                inputs["text"] = negative
            elif "negative" not in title and "neg" not in title:
                inputs["text"] = positive

        if width is not None and isinstance(inputs.get("width"), int):
            inputs["width"] = width
        if height is not None and isinstance(inputs.get("height"), int):
            inputs["height"] = height
        if steps is not None and isinstance(inputs.get("steps"), int):
            inputs["steps"] = steps
        if seed is not None and "seed" in inputs and isinstance(inputs.get("seed"), int):
            inputs["seed"] = seed
        if batch_size is not None and isinstance(inputs.get("batch_size"), int):
            inputs["batch_size"] = batch_size

    explicit = body.get("node_inputs")
    if isinstance(explicit, dict):
        for node_id, values in explicit.items():
            node = prompt.get(str(node_id))
            if not isinstance(node, dict) or not isinstance(values, dict):
                continue
            inputs = node.setdefault("inputs", {})
            if isinstance(inputs, dict):
                inputs.update(values)
    return prompt


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _comfy_request(method: str, path: str, body: Any = None, timeout: int = 60) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = _json_bytes(body)
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(COMFY_URL + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    server_version = "ComfyGatewayV2/1.0"

    def _send(self, status: int, data: Any) -> None:
        raw = _json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _auth_ok(self) -> bool:
        if not TOKEN:
            return True
        return self.headers.get("Authorization", "").strip() == f"Bearer {TOKEN}"

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _guard(self) -> bool:
        if self._auth_ok():
            return True
        self._send(401, {"ok": False, "error": "unauthorized"})
        return False

    def do_GET(self) -> None:
        if not self._guard():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/api/health":
                self._send(200, _comfy_request("GET", "/system_stats"))
            elif path == "/api/object_info":
                self._send(200, _comfy_request("GET", "/object_info"))
            elif path == "/api/queue":
                self._send(200, _comfy_request("GET", "/queue"))
            elif path == "/api/history":
                self._send(200, _comfy_request("GET", "/history"))
            elif path.startswith("/api/history/"):
                prompt_id = urllib.parse.quote(path.split("/", 3)[-1])
                self._send(200, _comfy_request("GET", f"/history/{prompt_id}"))
            elif path == "/api/view":
                self._proxy_view(parsed.query)
            elif path == "/api/workflows":
                self._send(200, {"ok": True, "items": _list_workflows()})
            elif path == "/api/jobs":
                prompt_id = str((query.get("prompt_id") or [""])[0]).strip()
                if not prompt_id:
                    raise ValueError("prompt_id is required")
                self._send(200, _job_payload(prompt_id))
            else:
                self._send(404, {"ok": False, "error": "not_found"})
        except Exception as exc:
            self._send(502, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        if not self._guard():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            body = self._read_body()
            if path == "/api/prompt":
                self._send(200, _comfy_request("POST", "/prompt", body=body))
            elif path == "/api/workflows/run":
                workflow_path = _safe_workflow_path(body.get("path"))
                prompt = _load_api_prompt(workflow_path)
                prompt = json.loads(json.dumps(prompt))
                prompt = _apply_prompt_overrides(prompt, body)
                response = _comfy_request(
                    "POST",
                    "/prompt",
                    body={"prompt": prompt, "client_id": str(body.get("client_id") or uuid.uuid4())},
                )
                self._send(
                    200,
                    {
                        "ok": True,
                        "prompt_id": response.get("prompt_id"),
                        "workflow": workflow_path.name,
                        "raw": response,
                    },
                )
            elif path == "/api/workflows/convert":
                self._send(200, _convert_workflows(body))
            elif path == "/api/workflows/upload":
                self._send(200, _upload_workflow(body))
            elif path == "/api/models/check":
                self._send(200, _check_models(body))
            else:
                self._send(404, {"ok": False, "error": "not_found"})
        except urllib.error.HTTPError as exc:
            self._send(exc.code, {"ok": False, "error": exc.read().decode("utf-8", errors="replace")})
        except Exception as exc:
            self._send(502, {"ok": False, "error": str(exc)})

    def _proxy_view(self, query: str) -> None:
        req = urllib.request.Request(COMFY_URL + "/view?" + query, method="GET")
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
            self.send_response(200)
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/octet-stream"))
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), fmt % args))


def _job_payload(prompt_id: str) -> dict[str, Any]:
    history = _comfy_request("GET", f"/history/{urllib.parse.quote(prompt_id)}")
    item = history.get(prompt_id) if isinstance(history, dict) else None
    if not isinstance(item, dict):
        return {"ok": True, "done": False, "prompt_id": prompt_id, "outputs": []}
    outputs: list[dict[str, Any]] = []
    for node_id, output in (item.get("outputs") or {}).items():
        if not isinstance(output, dict):
            continue
        for kind in ("images", "videos", "gifs", "audio"):
            for file_item in output.get(kind, []) or []:
                if isinstance(file_item, dict):
                    outputs.append({"node": node_id, "kind": kind, **file_item})
    return {"ok": True, "done": True, "prompt_id": prompt_id, "outputs": outputs}


def main() -> None:
    for _name, root in WORKFLOW_ROOTS:
        root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Comfy gateway v2 listening on http://{HOST}:{PORT}")
    print(f"ComfyUI upstream: {COMFY_URL}")
    print("Auth: " + ("enabled" if TOKEN else "disabled"))
    server.serve_forever()


if __name__ == "__main__":
    main()
