"""Telegram-style Web Bot console.

This module mirrors the Tool R18 Telegram bot navigation in a deterministic
web console. Actions that already exist in this project are executed directly;
source-project workflow actions are exposed with the same Telegram-style steps
so the operator can drive them from the web UI.
"""
from __future__ import annotations

import re
import time
import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core import vmos_client
from core import persona_dashboard as persona_dashboard_module
from core.persona_dashboard import build_overview, find_persona
from core.runtime_paths import DATA_DIR
from core.traditional import to_traditional, traditionalize_task_entries
from db.repo import AccountRepo, Device, DeviceRepo, OperatorRepo, Persona, PersonaRepo, PostMemory, PostMemoryRepo, SourceWorkflowJobRepo, TaskRepo


SOURCE_ROOT = r"D:\workflow_delivery_package_source"
SOURCE_WEB_BOT_CHAT_ID = int(os.getenv("SOURCE_WEB_BOT_CHAT_ID", "8080001"))
SOURCE_API_TIMEOUT = 18
PERSONA_MENU_CACHE_TTL_SECONDS = 30.0
CREATE_PERSONA_MAX_SELECTED_KEYWORDS = 2
STORED_POSTS_PAGE_SIZE = 3
GENPOST_MAX_COUNT = 20
GENPOST_IMAGE_BATCH_SIZE = STORED_POSTS_PAGE_SIZE
GENPOST_IMAGE_CANDIDATE_COUNT = 4
PERSONA_SETTING_COOLDOWN_DAYS = 60
PERSONA_SETTING_COOLDOWN_SECONDS = PERSONA_SETTING_COOLDOWN_DAYS * 24 * 60 * 60
LINK_ENDING_SOURCE_TYPE = "link_ending_settings"
PERSONA_IMAGE_DIR = DATA_DIR / "persona_images"
POST_IMAGE_DIR = DATA_DIR / "post_images"
LOCAL_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
TOOL_R18_PROJECT_DIR = Path(SOURCE_ROOT) / "tool_r18"
TOOL_R18_SKILLS_DIR = TOOL_R18_PROJECT_DIR / "scripts" / "skills"
_PERSONA_MENU_CACHE: dict[str, Any] = {"at": 0.0, "rows": []}
_PERSONA_OVERVIEW_REFRESH_LOCK = threading.Lock()
_TO_TRADITIONAL_IMPL = to_traditional


def _cjk_count(text: str) -> int:
    return sum(1 for ch in str(text or "") if "\u4e00" <= ch <= "\u9fff")


def to_traditional(value: Any) -> str:
    text = str(value or "")
    try:
        converted = _TO_TRADITIONAL_IMPL(text)
    except Exception:
        return text
    if not isinstance(converted, str):
        converted = str(converted or "")
    original_cjk = _cjk_count(text)
    converted_cjk = _cjk_count(converted)
    if original_cjk and converted.count("?") > text.count("?") and converted_cjk < max(1, original_cjk // 3):
        return text
    return converted


def _active_devices() -> list[Device]:
    return [device for device in DeviceRepo.list_all() if vmos_client.is_active_account_name(device.vmos_account)]


def _active_accounts():
    active_pads = {device.pad_code for device in _active_devices()}
    return [account for account in AccountRepo.list_all() if not account.pad_code or account.pad_code in active_pads]


def _active_task_usernames() -> set[str]:
    active_pads = {device.pad_code for device in _active_devices()}
    return {account.username for account in AccountRepo.list_all() if account.pad_code in active_pads}


def _visible_tasks(status: str | None = None, limit: int = 500) -> list:
    active_usernames = _active_task_usernames()
    return [
        task
        for task in TaskRepo.list_all(status=status, limit=limit)
        if task.username in active_usernames
    ]


def _task_counts(tasks: list) -> dict[str, int]:
    counts: dict[str, int] = {
        "pending": 0,
        "publishing": 0,
        "done": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    return counts


def _tasks_for_pad_code(pad_code: str, limit: int = 80) -> list:
    usernames = {
        account.username
        for account in AccountRepo.list_all()
        if getattr(account, "pad_code", "") == pad_code
    }
    if not usernames:
        return []
    return [task for task in TaskRepo.list_all(limit=limit) if task.username in usernames]


def _publish_status_lines_for_pad(pad_code: str, *, limit: int = 5) -> list[str]:
    tasks = _tasks_for_pad_code(pad_code, limit=80)
    counts = _task_counts(tasks)
    lines = [
        "雲手機發布狀況：",
        f"待發 {counts.get('pending', 0)}｜發布中 {counts.get('publishing', 0)}｜完成 {counts.get('done', 0)}｜失敗 {counts.get('failed', 0)}｜取消 {counts.get('cancelled', 0)}",
    ]
    if tasks:
        lines.append("最近任務：")
        for task in tasks[:limit]:
            lines.append(f"#{task.id}｜{_task_status_label(task.status)}｜{_task_media_label(task)}｜{_task_preview(task.text, 54)}")
    else:
        lines.append("最近任務：這台雲機目前沒有發布任務。")
    return lines


def _source_api_candidates() -> list[str]:
    configured = [
        os.getenv("SOURCE_WEBAPP_URL"),
        os.getenv("TOOL_R18_SOURCE_WEBAPP_URL"),
        os.getenv("TOOL_R18_INTERNAL_WEBAPP_BASE_URL"),
        os.getenv("TG_INTERNAL_WEBAPP_BASE_URL"),
    ]
    candidates: list[str] = []
    for item in [*configured, "http://workflow-delivery-r18:8098", "http://127.0.0.1:8000", "http://127.0.0.1:8091"]:
        value = str(item or "").strip().rstrip("/")
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _source_api_candidates() -> list[str]:
    configured = [
        os.getenv("SOURCE_WEBAPP_URL"),
        os.getenv("TOOL_R18_SOURCE_WEBAPP_URL"),
        os.getenv("TOOL_R18_INTERNAL_WEBAPP_BASE_URL"),
        os.getenv("TG_INTERNAL_WEBAPP_BASE_URL"),
    ]
    candidates: list[str] = []
    preferred = [
        "http://workflow-delivery-r18:8098",
        "http://172.17.0.1:8098",
    ]
    fallback = [
        "http://127.0.0.1:8098",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8091",
    ]
    for item in [*preferred, *configured, *fallback]:
        value = str(item or "").strip().rstrip("/")
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _source_http_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    timeout: int = SOURCE_API_TIMEOUT,
) -> tuple[str, dict[str, Any]]:
    headers = {"Accept": "application/json"}
    token = str(os.getenv("TG_INTERNAL_API_TOKEN") or "").strip()
    if token:
        headers["x-tg-internal-token"] = token
    body: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    clean_path = "/" + str(path or "").lstrip("/")
    last_error = ""
    for base in _source_api_candidates():
        url = f"{base}{clean_path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw or "{}")
            if not isinstance(data, dict):
                raise RuntimeError(f"来源 API 返回不是 JSON 对象：{raw[:200]}")
            return base, data
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {404, 405}:
                last_error = f"{base} HTTP {exc.code}: {detail[:300]}"
                continue
            raise RuntimeError(f"{base} HTTP {exc.code}: {_source_error_detail(detail)}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = f"{base}: {exc}"
            continue
    raise RuntimeError(last_error or "无法连接来源工作流 API")


def _source_error_detail(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return "来源 API 没有返回错误内容"
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            detail = data.get("detail") or data.get("message") or data.get("error")
            if detail:
                return str(detail)
    except Exception:
        pass
    return text[:500]


def _local_source_runtime_config_data() -> tuple[str, dict[str, Any]]:
    candidates = [
        DATA_DIR / "workflow_source_snapshot" / "webapp_data" / "runtime_config.json",
        Path(SOURCE_ROOT) / "webapp_data" / "runtime_config.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        except Exception:
            continue
        if isinstance(data, dict):
            return f"file://{path}", data
    return "", {}


def _source_runtime_config_data() -> tuple[str, dict[str, Any]]:
    try:
        base, data = _source_http_request("GET", "/api/internal/tg/runtime_config")
        runtime = data.get("runtime_config") if isinstance(data.get("runtime_config"), dict) else {}
        if runtime:
            return base, runtime
    except Exception:
        pass
    base, runtime = _local_source_runtime_config_data()
    if runtime:
        return base, runtime
    return "", {}


def _source_submit_task(task_type: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return _source_http_request(
        "POST",
        "/api/internal/tg/submit",
        payload={"task_type": str(task_type), "tg_chat_id": SOURCE_WEB_BOT_CHAT_ID, "params": params or {}},
        timeout=45,
    )


def _submit_source_task_job(job_id: str, task_type: str, params: dict[str, Any]) -> None:
    try:
        base, data = _source_submit_task(task_type, params)
        SourceWorkflowJobRepo.update(
            job_id,
            status="submitted",
            result=data,
            source_task_id=str(data.get("id") or ""),
            source_base_url=base,
        )
    except Exception as exc:
        SourceWorkflowJobRepo.update(job_id, status="failed", error=str(exc))


def _submit_source_task_job_async(job_id: str, task_type: str, params: dict[str, Any]) -> None:
    thread = threading.Thread(
        target=_submit_source_task_job,
        args=(job_id, task_type, params),
        name=f"source-submit-{job_id[:6]}",
        daemon=True,
    )
    thread.start()


def _source_agent_submit(message_text: str, files: list[dict[str, str]] | None = None, duration_seconds: int = 15) -> tuple[str, dict[str, Any]]:
    return _source_http_request(
        "POST",
        "/api/internal/tg/agent_submit",
        payload={
            "message": str(message_text or "").strip(),
            "tg_chat_id": SOURCE_WEB_BOT_CHAT_ID,
            "files": files or [],
            "use_ai_copy": True,
            "duration_seconds": int(duration_seconds or 15),
        },
        timeout=60,
    )


def _resolve_tool_r18_node_executable() -> str:
    for env_key in ("TOOL_R18_NODE_EXE", "NODE_EXE"):
        value = str(os.getenv(env_key) or "").strip().strip("\"'")
        if value and Path(value).exists():
            return value
    found = shutil.which("node")
    if found:
        return found
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / ("node.exe" if os.name == "nt" else "node")
    if bundled.exists():
        return str(bundled)
    return "node"


def _tool_r18_node_env() -> dict[str, str]:
    env = os.environ.copy()
    runtime_dir = Path(os.getenv("TOOL_R18_RUNTIME_DIR", str(TOOL_R18_PROJECT_DIR / ".runtime" / "automatic-script"))).resolve()
    env.setdefault("TOOL_R18_RUNTIME_DIR", str(runtime_dir))
    env.setdefault("AUTO_TWEET_RUNTIME_DIR", str(runtime_dir))
    node_modules = TOOL_R18_PROJECT_DIR / "node_modules"
    current_node_path = str(env.get("NODE_PATH") or "").strip()
    parts = [str(node_modules)]
    if current_node_path:
        parts.append(current_node_path)
    env["NODE_PATH"] = os.pathsep.join(dict.fromkeys(parts))
    return env


def _tool_r18_tsx_args(script: Path, payload: dict[str, Any]) -> list[str]:
    return [
        _resolve_tool_r18_node_executable(),
        "--import",
        "tsx",
        str(script),
        json.dumps(payload, ensure_ascii=False),
    ]


def _tool_r18_node_dependency_hint(stdout: str = "", stderr: str = "") -> str:
    combined = f"{stdout}\n{stderr}"
    if "Cannot find package 'tsx'" in combined or ("ERR_MODULE_NOT_FOUND" in combined and "tsx" in combined):
        return "Tool_R18 Node dependencies are missing. Run npm install in tool_r18, or set NODE_PATH to a directory that contains tsx."
    if "is not recognized" in combined and "node" in combined.lower():
        return "Node.js was not found. Install Node.js or set TOOL_R18_NODE_EXE/NODE_EXE."
    return ""


def _run_tool_r18_script_job(job_id: str, script_name: str, payload: dict[str, Any], timeout_seconds: int) -> None:
    safe_name = str(script_name or "").strip().replace("\\", "/").split("/")[-1]
    try:
        if not safe_name or not safe_name.endswith((".ts", ".mjs", ".js")):
            raise RuntimeError("invalid Tool_R18 script name")
        script = (TOOL_R18_SKILLS_DIR / safe_name).resolve()
        skills_dir = TOOL_R18_SKILLS_DIR.resolve()
        if skills_dir not in script.parents or not script.exists():
            raise RuntimeError(f"Tool_R18 script not found: {safe_name}")
        SourceWorkflowJobRepo.update(job_id, status="running")
        if safe_name.endswith(".ts"):
            args = _tool_r18_tsx_args(script, payload)
        else:
            args = [_resolve_tool_r18_node_executable(), str(script), json.dumps(payload, ensure_ascii=False)]
        proc = subprocess.run(
            args,
            cwd=str(TOOL_R18_PROJECT_DIR),
            env=_tool_r18_node_env(),
            text=True,
            capture_output=True,
            timeout=max(int(timeout_seconds or 900), 60),
            encoding="utf-8",
            errors="replace",
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        parsed: Any = None
        if stdout:
            start = stdout.find("{")
            try:
                parsed = json.loads(stdout[start if start >= 0 else 0 :])
            except Exception:
                parsed = {"ok": False, "raw": stdout[-4000:]}
        if proc.returncode != 0 or not isinstance(parsed, dict) or parsed.get("ok") is False:
            message = ""
            if isinstance(parsed, dict):
                message = str(parsed.get("error") or parsed.get("detail") or "").strip()
            hint = _tool_r18_node_dependency_hint(stdout, stderr)
            raise RuntimeError(message or hint or stderr[-1200:] or stdout[-1200:] or f"{safe_name} failed")
        SourceWorkflowJobRepo.update(job_id, status="success", result=parsed)
    except Exception as exc:
        SourceWorkflowJobRepo.update(job_id, status="failed", error=str(exc))


def _submit_tool_r18_script_job(label: str, script_name: str, payload: dict[str, Any], *, timeout_seconds: int = 900) -> Any:
    job = SourceWorkflowJobRepo.create(
        "tool_r18_script",
        label,
        {
            "script": script_name,
            "payload": payload,
            "timeout_seconds": timeout_seconds,
            "source": SOURCE_ROOT,
        },
        status="queued_external",
    )
    thread = threading.Thread(
        target=_run_tool_r18_script_job,
        args=(job.id, script_name, payload, timeout_seconds),
        name=f"tool-r18-{job.id[:6]}",
        daemon=True,
    )
    thread.start()
    return job


def _source_tasks(limit: int = 8) -> tuple[str, list[dict[str, Any]]]:
    base, data = _source_http_request(
        "GET",
        "/api/internal/tg/tasks",
        query={"chat_id": SOURCE_WEB_BOT_CHAT_ID, "limit": int(limit or 8)},
    )
    tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
    return base, [item for item in tasks if isinstance(item, dict)]


def _source_status_data() -> tuple[str, dict[str, Any]]:
    return _source_http_request("GET", "/api/internal/tg/status", query={"chat_id": SOURCE_WEB_BOT_CHAT_ID})


def _source_cancel_latest_data() -> tuple[str, dict[str, Any]]:
    return _source_http_request("POST", "/api/internal/tg/tasks/cancel_latest", query={"chat_id": SOURCE_WEB_BOT_CHAT_ID})


def _source_task_detail_data(task_id: str) -> tuple[str, dict[str, Any]]:
    return _source_http_request("GET", f"/api/internal/tg/tasks/{task_id}", query={"chat_id": SOURCE_WEB_BOT_CHAT_ID})


def _split_path_list(value: str) -> list[str]:
    return [item.strip().strip('"').strip("'") for item in re.split(r"[;\n,]+", str(value or "")) if item.strip()]


def _normalize_source_path(value: str) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    path = Path(text).expanduser()
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def _validate_source_path(value: str, label: str) -> tuple[bool, str, str]:
    path = _normalize_source_path(value)
    if not path:
        return False, "", f"{label} 不能为空"
    if not Path(path).is_file():
        return False, "", f"{label} 找不到文件：{path}"
    return True, path, ""


SOURCE_WORKFLOW_CATALOG: dict[str, dict[str, Any]] = {
    "text_to_image": {
        "label": "文生图",
        "task_type": "text_to_image",
        "intro": "按来源 Telegram 的文生图流程提交。最终提示词会交给来源后台进行 Grok/工作流处理。",
        "defaults": {"tg_use_llm_prompt": True},
        "steps": [
            {"key": "prompt", "label": "图片需求", "type": "text", "prompt": "请输入这次文生图需求或完整提示词。"},
        ],
    },
    "single_image_edit": {
        "label": "单图编辑",
        "task_type": "single_image_edit",
        "intro": "上传一张图并输入编辑要求，来源后台会走单图编辑工作流。",
        "defaults": {"tg_use_llm_prompt": True},
        "steps": [
            {"key": "input_image_local_path", "label": "原图路径", "type": "path", "prompt": "请贴上要编辑的图片本机路径。"},
            {"key": "prompt", "label": "编辑要求", "type": "text", "prompt": "请输入这张图要如何编辑。"},
        ],
    },
    "image_edit": {
        "label": "图片编辑",
        "task_type": "get_nano_banana",
        "intro": "上传原图与参考图，来源后台会走双图编辑工作流。",
        "defaults": {"tg_use_llm_prompt": True},
        "steps": [
            {"key": "input_image_local_path", "label": "原图路径", "type": "path", "prompt": "请贴上第一张原图路径。"},
            {"key": "reference_image_local_path", "label": "参考图路径", "type": "path", "prompt": "请贴上第二张参考图路径。"},
            {"key": "prompt", "label": "编辑要求", "type": "text", "prompt": "请输入图片编辑需求。"},
        ],
    },
    "multi_image": {
        "label": "多图生成",
        "task_type": "image_generate",
        "intro": "对应来源 Telegram 的多图生成：第一张参考图 + 第二张参考图 + 生成需求。",
        "defaults": {"tg_use_llm_prompt": True},
        "steps": [
            {"key": "product_image_local_path", "label": "第一张参考图", "type": "path", "prompt": "请贴上第一张参考图路径。"},
            {"key": "model_image_local_path", "label": "第二张参考图", "type": "path", "prompt": "请贴上第二张参考图路径。"},
            {"key": "prompt", "label": "生成需求", "type": "text", "prompt": "请输入这次多图生成需求。"},
        ],
    },
    "image_replace": {
        "label": "图片替换",
        "task_type": "image_generate",
        "intro": "对应来源 Telegram 的图片替换：原图 + 要替换成的参考图 + 需求。",
        "defaults": {"tg_use_llm_prompt": True},
        "steps": [
            {"key": "product_image_local_path", "label": "原图路径", "type": "path", "prompt": "请贴上原图路径。"},
            {"key": "model_image_local_path", "label": "替换参考图路径", "type": "path", "prompt": "请贴上要替换成的参考图路径。"},
            {"key": "prompt", "label": "替换要求", "type": "text", "prompt": "请输入图片替换需求。"},
        ],
    },
    "face_swap": {
        "label": "人物换脸",
        "task_type": "face_swap",
        "intro": "上传目标图和脸部来源图，来源后台会走人物换脸工作流。",
        "defaults": {"prompt": "自然换脸，保持原图姿态、服装、光线和背景，只替换人物脸部身份。", "tg_use_llm_prompt": True},
        "steps": [
            {"key": "target_image_local_path", "label": "目标图路径", "type": "path", "prompt": "请贴上目标图路径。"},
            {"key": "source_image_local_path", "label": "脸部来源图路径", "type": "path", "prompt": "请贴上脸部来源图路径。"},
            {"key": "prompt", "label": "换脸要求", "type": "optional_text", "prompt": "请输入换脸要求；直接按略过则使用自然换脸默认要求。"},
        ],
    },
    "video_i2v": {
        "label": "图生视频",
        "task_type": "video_i2v",
        "intro": "对应来源 Telegram 的图生视频流程。",
        "defaults": {"tg_use_llm_prompt": True},
        "steps": [
            {"key": "resolution", "label": "分辨率", "type": "choice", "choices": ["720p", "1080p"], "prompt": "请选择输出分辨率。"},
            {"key": "duration_seconds", "label": "视频秒数", "type": "number", "min": 2, "max": 15, "prompt": "请输入视频秒数，范围 2-15。"},
            {"key": "image_local_path", "label": "输入图片路径", "type": "path", "prompt": "请贴上用来生成视频的图片路径。"},
            {"key": "prompt", "label": "视频需求", "type": "text", "prompt": "请输入视频动作、镜头或场景需求。"},
            {"key": "audio_local_path", "label": "音频路径", "type": "optional_path", "prompt": "可贴上音频路径；不需要请按略过。"},
        ],
    },
    "digital_human": {
        "label": "数字人视频生成",
        "task_type": "create_video",
        "intro": "对应来源 Telegram 的数字人视频生成。",
        "defaults": {"tg_use_llm_prompt": True},
        "steps": [
            {"key": "model_image_local_path", "label": "人物/模特图", "type": "path", "prompt": "请贴上人物或模特图片路径。"},
            {"key": "product_image_local_path", "label": "商品/场景图", "type": "optional_path", "prompt": "可贴上商品或场景图路径；不需要请按略过，系统会沿用人物图。"},
            {"key": "prompt_text", "label": "视频要求", "type": "text", "prompt": "请输入数字人视频要求或口播风格。"},
            {"key": "duration_seconds", "label": "视频秒数", "type": "number", "min": 1, "max": 60, "prompt": "请输入视频秒数，建议 10-15。"},
        ],
    },
    "digital_human_realistic": {
        "label": "写实带货视频",
        "task_type": "create_video",
        "intro": "对应来源 Telegram 的写实带货视频。",
        "defaults": {"prompt_text": "写实电商带货视频，人物自然展示商品，镜头干净，真实质感，无文字水印。", "tg_use_llm_prompt": True},
        "steps": [
            {"key": "model_image_local_path", "label": "人物/模特图", "type": "path", "prompt": "请贴上人物或模特图片路径。"},
            {"key": "product_image_local_path", "label": "商品图", "type": "path", "prompt": "请贴上商品图片路径。"},
            {"key": "duration_seconds", "label": "视频秒数", "type": "number", "min": 1, "max": 60, "prompt": "请输入视频秒数，建议 10-15。"},
        ],
    },
    "digital_human_live": {
        "label": "直播口播视频",
        "task_type": "create_video",
        "intro": "对应来源 Telegram 的直播口播视频。",
        "defaults": {"prompt_text": "直播间口播风格，人物正面自然讲解商品，光线柔和，节奏清晰，适合短视频带货。", "tg_use_llm_prompt": True},
        "steps": [
            {"key": "model_image_local_path", "label": "人物/模特图", "type": "path", "prompt": "请贴上人物或模特图片路径。"},
            {"key": "product_image_local_path", "label": "商品图", "type": "path", "prompt": "请贴上商品图片路径。"},
            {"key": "duration_seconds", "label": "视频秒数", "type": "number", "min": 1, "max": 60, "prompt": "请输入视频秒数，建议 10-15。"},
        ],
    },
    "digital_human_product": {
        "label": "产品展示视频",
        "task_type": "create_video",
        "intro": "对应来源 Telegram 的产品展示视频。",
        "defaults": {"prompt_text": "产品展示型数字人视频，突出商品细节和使用场景，人物动作自然，画面高级干净。", "tg_use_llm_prompt": True},
        "steps": [
            {"key": "model_image_local_path", "label": "人物/模特图", "type": "path", "prompt": "请贴上人物或模特图片路径。"},
            {"key": "product_image_local_path", "label": "商品图", "type": "path", "prompt": "请贴上商品图片路径。"},
            {"key": "duration_seconds", "label": "视频秒数", "type": "number", "min": 1, "max": 60, "prompt": "请输入视频秒数，建议 10-15。"},
        ],
    },
    "digital_human_custom": {
        "label": "自定义数字人要求",
        "task_type": "create_video",
        "intro": "先输入自定义要求，再提交数字人视频。",
        "defaults": {"tg_use_llm_prompt": True},
        "steps": [
            {"key": "model_image_local_path", "label": "人物/模特图", "type": "path", "prompt": "请贴上人物或模特图片路径。"},
            {"key": "product_image_local_path", "label": "商品/场景图", "type": "optional_path", "prompt": "可贴上商品或场景图路径；不需要请按略过。"},
            {"key": "prompt_text", "label": "自定义视频要求", "type": "text", "prompt": "请输入完整数字人视频要求。"},
            {"key": "duration_seconds", "label": "视频秒数", "type": "number", "min": 1, "max": 60, "prompt": "请输入视频秒数，建议 10-15。"},
        ],
    },
    "replace_model": {
        "label": "视频模特替换",
        "task_type": "replace_model",
        "intro": "对应来源 Telegram 的视频模特替换。",
        "defaults": {"mode": "original", "tg_use_llm_prompt": True},
        "steps": [
            {"key": "video_local_path", "label": "原视频路径", "type": "path", "prompt": "请贴上原视频路径。"},
            {"key": "image_local_path", "label": "新模特图路径", "type": "path", "prompt": "请贴上新模特图片路径。"},
            {"key": "prompt", "label": "替换要求", "type": "optional_text", "prompt": "可输入替换要求；不需要请按略过。"},
            {"key": "duration_seconds", "label": "视频秒数", "type": "number", "min": 1, "max": 60, "prompt": "请输入视频秒数，建议 10-15。"},
        ],
    },
    "replace_product": {
        "label": "视频商品替换",
        "task_type": "replace_product",
        "intro": "对应来源 Telegram 的视频商品替换。",
        "defaults": {"tg_use_llm_prompt": True},
        "steps": [
            {"key": "video_local_path", "label": "原视频路径", "type": "path", "prompt": "请贴上原视频路径。"},
            {"key": "image_local_path", "label": "商品图路径", "type": "path", "prompt": "请贴上商品图片路径。"},
            {"key": "product_name", "label": "商品名称", "type": "text", "prompt": "请输入商品名称。"},
            {"key": "prompt_text", "label": "替换要求", "type": "optional_text", "prompt": "可输入商品替换要求；不需要请按略过。"},
            {"key": "duration_seconds", "label": "视频秒数", "type": "number", "min": 1, "max": 60, "prompt": "请输入视频秒数，建议 10-15。"},
        ],
    },
    "replace_union": {
        "label": "联合替换工作流",
        "task_type": "replace_productANDmodel",
        "intro": "对应来源 Telegram 的联合替换：同一支视频同时替换模特与商品。",
        "defaults": {"tg_use_llm_prompt": True},
        "steps": [
            {"key": "video_local_path", "label": "原视频路径", "type": "path", "prompt": "请贴上原视频路径。"},
            {"key": "model_image_local_path", "label": "新模特图路径", "type": "path", "prompt": "请贴上新模特图片路径。"},
            {"key": "product_image_local_path", "label": "新商品图路径", "type": "path", "prompt": "请贴上新商品图片路径。"},
            {"key": "product_name", "label": "商品名称", "type": "text", "prompt": "请输入商品名称。"},
            {"key": "duration_seconds", "label": "视频秒数", "type": "number", "min": 1, "max": 60, "prompt": "请输入视频秒数，建议 10-15。"},
        ],
    },
    "create_audio": {
        "label": "生成口播音频",
        "task_type": "create_audio",
        "intro": "来源后台 create_audio，可用于数字人口播素材。",
        "steps": [
            {"key": "speech_text", "label": "口播文案", "type": "text", "prompt": "请输入要生成的口播文案。"},
        ],
    },
}

SOURCE_ACTION_ALIASES = {
    "文生图": "text_to_image",
    "單圖編輯": "single_image_edit",
    "单图编辑": "single_image_edit",
    "圖片編輯": "image_edit",
    "图片编辑": "image_edit",
    "多圖生成": "multi_image",
    "多图生成": "multi_image",
    "人物換臉": "face_swap",
    "人物换脸": "face_swap",
    "圖片替換": "image_replace",
    "图片替换": "image_replace",
    "圖生視頻": "video_i2v",
    "图生视频": "video_i2v",
    "數字人視頻生成": "digital_human",
    "数字人视频生成": "digital_human",
    "視頻生成": "video_i2v",
    "视频生成": "video_i2v",
    "視頻模特替換": "replace_model",
    "视频模特替换": "replace_model",
    "視頻商品替換": "replace_product",
    "视频商品替换": "replace_product",
    "聯合替換工作流": "replace_union",
    "联合替换工作流": "replace_union",
}


def _num(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _compact(value: Any) -> str:
    number = _num(value)
    if abs(number) >= 10000:
        text = f"{number / 10000:.1f}".rstrip("0").rstrip(".")
        return f"{text}万"
    return str(number)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "asset"


def _stable_id(prefix: str, *parts: Any) -> str:
    seed = "|".join(str(part or "").strip() for part in parts if str(part or "").strip())
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _image_data_url(path: str | Path) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    mime = "image/jpeg" if file_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _safe_web_media_url(value: Any) -> str:
    text = str(value or "").strip()
    if re.match(r"^data:(?:image|video)/[a-z0-9.+-]+;base64,[a-z0-9+/=\r\n]+$", text, re.I):
        return text
    if (
        text.startswith(("/persona_media/", "/tool_r18_uploads/"))
        and not text.startswith("//")
        and "\\" not in text
        and ".." not in text.split("/")
    ):
        return text
    if not re.match(r"^https?://", text, re.I):
        return ""
    parsed = urllib.parse.urlsplit(text)
    if not parsed.netloc or parsed.username or parsed.password:
        return ""
    return text


def _is_web_image_url(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(
        re.match(r"^data:image/", text, re.I)
        or re.search(r"\.(?:png|jpe?g|webp|gif)(?:[?#].*)?$", text, re.I)
    )


def _persona_reference_image_url(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    return _safe_web_media_url(row.get("reference_image_url") or row.get("referenceImageUrl"))


def _fresh_persona_row(persona_id: str, local: Persona | None, row: dict[str, Any] | None) -> dict[str, Any] | None:
    target_id = _tool_r18_archive_id(persona_id, local, row) or str(persona_id or "").strip()
    if not target_id:
        return row
    try:
        overview = build_overview(force_remote=True)
        fresh = find_persona(overview, target_id)
        if fresh:
            return fresh
    except Exception:
        pass
    return row


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    candidates = [
        r"C:\Windows\Fonts\NotoSansTC-VF.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\mingliu.ttc",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
    ]
    for path in candidates:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_width(draw: Any, text: str, font: Any) -> int:
    try:
        box = draw.textbbox((0, 0), text, font=font)
        return int(box[2] - box[0])
    except Exception:
        return len(text) * 16


def _wrap_text(draw: Any, text: str, font: Any, max_width: int, max_lines: int = 6) -> list[str]:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return []
    lines: list[str] = []
    current = ""
    for ch in raw:
        trial = current + ch
        if current and _text_width(draw, trial, font) > max_width:
            lines.append(current)
            current = ch
            if len(lines) >= max_lines:
                break
        else:
            current = trial
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and _text_width(draw, lines[-1] + "...", font) > max_width:
        while lines[-1] and _text_width(draw, lines[-1] + "...", font) > max_width:
            lines[-1] = lines[-1][:-1]
    if len(lines) == max_lines and raw and "".join(lines) != raw:
        lines[-1] = lines[-1].rstrip("，。,. ") + "..."
    return lines


def _palette(seed: str) -> dict[str, tuple[int, int, int]]:
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).digest()
    hue = digest[0]
    palettes = [
        {"bg1": (237, 246, 255), "bg2": (203, 233, 224), "hair": (42, 48, 67), "cloth": (88, 139, 174), "accent": (231, 98, 125)},
        {"bg1": (255, 241, 234), "bg2": (236, 227, 255), "hair": (70, 45, 55), "cloth": (138, 93, 161), "accent": (242, 145, 92)},
        {"bg1": (242, 249, 232), "bg2": (218, 238, 244), "hair": (45, 54, 40), "cloth": (69, 158, 117), "accent": (249, 190, 75)},
        {"bg1": (251, 243, 231), "bg2": (229, 237, 255), "hair": (35, 42, 56), "cloth": (213, 103, 96), "accent": (85, 132, 196)},
    ]
    return palettes[hue % len(palettes)]


def _avatar_exists(persona: Persona | None) -> bool:
    if not persona or not persona.avatar_path:
        return False
    path = Path(persona.avatar_path)
    if not path.exists():
        return False
    return not _is_legacy_persona_placeholder_image(path)


def _is_legacy_persona_placeholder_image(path: str | Path) -> bool:
    try:
        file_path = Path(path)
        if file_path.parent.resolve() != PERSONA_IMAGE_DIR.resolve():
            return False
    except Exception:
        return False
    name = file_path.name
    lowered = name.lower()
    if "_source_" in lowered and not _image_source_meta_path(file_path).exists():
        return True
    if "_custom_" in lowered or "_source_" in lowered or "_upload_" in lowered:
        return False
    return bool(re.fullmatch(r"[0-9A-Za-z._-]+_\d{10}(?:_\d+)?\.png", name))


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default)) or default))
    except Exception:
        return default


def _persona_reference_prompt(persona: Persona, instruction: str = "") -> str:
    name = (persona.name or "未命名人设").strip()
    description = (persona.description or "").strip()
    style = (persona.style_prompt or "").strip()
    parts = [
        "写实真人角色参考图，单人，成年人，半身肖像或胸像，脸部清晰可见，真实摄影质感，自然光，干净背景。",
        f"人设名称：{name}",
        "用途：锁定人物长相，后续用于推文配图或视频参考图；请保持可复用的人物五官、发型、气质和穿搭方向。",
        "严格禁止：卡通、插画、Q版、矢量头像、模板头像、海报排版、文字、水印、logo、边框、拼贴、表情包。",
    ]
    if description:
        parts.insert(2, f"人设描述：{description}")
    if style:
        parts.insert(3 if description else 2, f"风格/性格：{style}")
    if instruction.strip():
        parts.append(f"额外要求：{instruction.strip()}")
    return "\n".join(parts)


def _source_image_from_task(task: dict[str, Any]) -> str:
    candidates: list[str] = []
    image_paths = task.get("image_paths")
    if isinstance(image_paths, list):
        candidates.extend(str(item or "").strip() for item in image_paths)
    elif isinstance(image_paths, str):
        candidates.append(image_paths.strip())
    for key in ("download_path", "image_path", "result_path", "output_path"):
        value = str(task.get(key) or "").strip()
        if value:
            candidates.append(value)
    for value in candidates:
        path = Path(value)
        if path.is_file() and path.suffix.lower() in LOCAL_IMAGE_EXTS:
            return str(path)
    return ""


def _wait_for_source_image_task(task_id: str) -> tuple[str, dict[str, Any]]:
    timeout_seconds = max(30, _env_int("PERSONA_IMAGE_SOURCE_TIMEOUT", 240))
    poll_seconds = max(1.0, _env_float("PERSONA_IMAGE_SOURCE_POLL_SECONDS", 3.0))
    deadline = time.time() + timeout_seconds
    last_status = ""
    last_event = ""
    while True:
        _base, data = _source_task_detail_data(task_id)
        task = data.get("task") if isinstance(data.get("task"), dict) else {}
        status = str(task.get("status") or "").strip().lower()
        last_status = status or last_status
        latest_event = task.get("latest_event") if isinstance(task.get("latest_event"), dict) else {}
        last_event = str(latest_event.get("message") or task.get("error") or last_event or "").strip()
        if status == "success":
            image_path = _source_image_from_task(task)
            if image_path:
                return image_path, task
            raise RuntimeError(f"来源 text_to_image 任务 {task_id} 已完成，但没有返回图片文件")
        if status in {"failed", "cancelled", "canceled"}:
            detail = str(task.get("error") or last_event or status).strip()
            raise RuntimeError(f"来源 text_to_image 任务 {task_id} {status}：{detail}")
        if time.time() >= deadline:
            suffix = f"，最后状态：{last_status}" if last_status else ""
            event = f"，最新进度：{last_event}" if last_event else ""
            raise RuntimeError(f"来源 text_to_image 任务 {task_id} 等待超时{suffix}{event}")
        time.sleep(poll_seconds)


def _runninghub_api_url(base_url: str, endpoint: str) -> str:
    base = str(base_url or "").strip().rstrip("/") or "https://www.runninghub.ai"
    clean_endpoint = str(endpoint or "").strip()
    if clean_endpoint.startswith(("http://", "https://")):
        return clean_endpoint
    clean_endpoint = "/" + clean_endpoint.lstrip("/")
    if clean_endpoint.startswith("/openapi/v2/"):
        return base + clean_endpoint
    return base + "/openapi/v2" + clean_endpoint


def _runninghub_json_request(url: str, api_key: str, payload: dict[str, Any], *, timeout: int = 60) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {api_key}",
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"RunningHub HTTP {exc.code}: {_source_error_detail(detail)}") from exc
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        raise RuntimeError(f"RunningHub 返回不是 JSON 对象：{raw[:200]}")
    return data


def _runninghub_task_id(data: dict[str, Any]) -> str:
    for key in ("taskId", "task_id", "id"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    for key in ("taskId", "task_id", "id"):
        value = str(nested.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_image_urls(value: Any, *, key_hint: str = "") -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            urls.extend(_extract_image_urls(item, key_hint=str(key)))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_extract_image_urls(item, key_hint=key_hint))
    elif isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if text.startswith("data:image/"):
            urls.append(text)
        elif lowered.startswith(("http://", "https://")):
            hint = key_hint.lower()
            parsed = urllib.parse.urlparse(text)
            path_lower = parsed.path.lower()
            looks_image = path_lower.endswith(tuple(LOCAL_IMAGE_EXTS)) or "image" in hint or "url" in hint or "result" in hint
            if looks_image:
                urls.append(text)
    return list(dict.fromkeys(urls))


def _runninghub_result_url(data: dict[str, Any]) -> str:
    for key in ("results", "result", "data", "output", "outputs"):
        urls = _extract_image_urls(data.get(key), key_hint=key)
        if urls:
            return urls[0]
    urls = _extract_image_urls(data)
    return urls[0] if urls else ""


def _image_source_meta_path(path: str | Path) -> Path:
    return Path(str(path) + ".source.json")


def _write_image_source_meta(path: str | Path, *, source_url: str = "", source_label: str = "") -> None:
    if not source_url or not str(source_url).strip().startswith(("http://", "https://")):
        return
    meta_path = _image_source_meta_path(path)
    payload = {
        "source_url": str(source_url),
        "source_label": str(source_label or "runninghub"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _read_image_source_url(path: str | Path) -> str:
    meta_path = _image_source_meta_path(path)
    if not meta_path.exists():
        return ""
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    url = str(data.get("source_url") or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


def _guess_image_mime(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _runninghub_upload_media(base_url: str, api_key: str, file_path: str | Path) -> str:
    path = Path(file_path)
    if not path.exists():
        raise RuntimeError(f"Reference image not found: {path}")
    cached = _read_image_source_url(path)
    if cached:
        return cached
    boundary = f"----WebBotRunningHub{hashlib.sha1(str(time.time_ns()).encode('ascii')).hexdigest()}"
    content = path.read_bytes()
    filename = path.name.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore") or "image.png"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {_guess_image_mime(path)}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        _runninghub_api_url(base_url, "/media/upload/binary"),
        data=head + content + tail,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(30, _env_int("RUNNINGHUB_UPLOAD_TIMEOUT", 90))) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"RunningHub upload HTTP {exc.code}: {_source_error_detail(detail)}") from exc
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        raise RuntimeError(f"RunningHub upload returned non-JSON: {raw[:200]}")
    if "code" in data and int(data.get("code") or 0) != 0:
        raise RuntimeError(f"RunningHub upload failed: {_source_error_detail(json.dumps(data, ensure_ascii=False))}")
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    download_url = str(
        payload.get("download_url")
        or payload.get("downloadUrl")
        or payload.get("url")
        or payload.get("fileUrl")
        or ""
    ).strip()
    file_name = str(payload.get("fileName") or payload.get("file_name") or "").strip()
    if download_url:
        result = download_url if download_url.startswith(("http://", "https://")) else str(base_url or "").strip().rstrip("/") + "/" + download_url.lstrip("/")
    elif file_name:
        result = file_name
    else:
        raise RuntimeError(f"RunningHub upload response missing URL: {data}")
    if result.startswith(("http://", "https://")):
        _write_image_source_meta(path, source_url=result, source_label="runninghub-upload")
    return result


def _save_persona_image_from_url(persona: Persona, image_url: str, *, variant: int = 0, source_label: str = "runninghub") -> str:
    PERSONA_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = _safe_filename(persona.id)
    if image_url.startswith("data:image/"):
        match = re.match(r"data:(image/[^;]+);base64,(.+)", image_url, flags=re.S)
        if not match:
            raise RuntimeError("RunningHub 返回了无法解析的 data:image 图片")
        mime = match.group(1).lower()
        suffix = ".jpg" if "jpeg" in mime or "jpg" in mime else ".webp" if "webp" in mime else ".png"
        data = base64.b64decode(match.group(2))
        dest = PERSONA_IMAGE_DIR / f"{safe_id}_source_{source_label}_{int(time.time())}_{variant or 0}{suffix}"
        dest.write_bytes(data)
        _write_image_source_meta(dest, source_url=image_url, source_label=source_label)
        return str(dest)

    request = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=max(30, _env_int("PERSONA_IMAGE_DOWNLOAD_TIMEOUT", 90))) as response:
        content = response.read()
        content_type = str(response.headers.get("Content-Type") or "").lower()
    parsed_suffix = Path(urllib.parse.urlparse(image_url).path).suffix.lower()
    if parsed_suffix not in LOCAL_IMAGE_EXTS:
        if "jpeg" in content_type or "jpg" in content_type:
            parsed_suffix = ".jpg"
        elif "webp" in content_type:
            parsed_suffix = ".webp"
        else:
            parsed_suffix = ".png"
    dest = PERSONA_IMAGE_DIR / f"{safe_id}_source_{source_label}_{int(time.time())}_{variant or 0}{parsed_suffix}"
    dest.write_bytes(content)
    _write_image_source_meta(dest, source_url=image_url, source_label=source_label)
    return str(dest)


def _wait_for_runninghub_image(task_id: str, *, base_url: str, api_key: str) -> tuple[str, dict[str, Any]]:
    timeout_seconds = max(45, _env_int("PERSONA_IMAGE_RUNNINGHUB_TIMEOUT", 300))
    poll_seconds = max(2.0, _env_float("PERSONA_IMAGE_RUNNINGHUB_POLL_SECONDS", 5.0))
    query_url = _runninghub_api_url(base_url, "/query")
    deadline = time.time() + timeout_seconds
    last_status = ""
    last_error = ""
    while True:
        data = _runninghub_json_request(query_url, api_key, {"taskId": task_id}, timeout=60)
        status = str(data.get("status") or data.get("state") or "").strip().upper()
        last_status = status or last_status
        last_error = str(data.get("errorMessage") or data.get("error") or data.get("message") or last_error or "").strip()
        image_url = _runninghub_result_url(data)
        if image_url and status not in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED"}:
            return image_url, data
        if status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "DONE"}:
            raise RuntimeError(f"RunningHub 任务 {task_id} 已完成但没有返回图片 URL")
        if status in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED"}:
            raise RuntimeError(f"RunningHub 任务 {task_id} {status}：{last_error or data}")
        if time.time() >= deadline:
            suffix = f"，最后状态：{last_status}" if last_status else ""
            detail = f"，错误：{last_error}" if last_error else ""
            raise RuntimeError(f"RunningHub 任务 {task_id} 等待超时{suffix}{detail}")
        time.sleep(poll_seconds)


def _generate_persona_reference_image_via_runninghub(persona: Persona, *, variant: int = 0, instruction: str = "") -> str:
    _base, runtime = _source_runtime_config_data()
    api_key = str(runtime.get("new_persona_runninghub_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("来源 runtime_config 没有配置 new_persona_runninghub_api_key")
    base_url = str(runtime.get("new_persona_runninghub_base_url") or "https://www.runninghub.ai").strip()
    endpoint = str(runtime.get("new_persona_runninghub_persona_t2i_endpoint") or "/rhart-image-g-2/text-to-image").strip()
    submit_url = _runninghub_api_url(base_url, endpoint)
    prompt = _persona_reference_prompt(persona, instruction)
    payload = {"prompt": prompt, "aspectRatio": "1:1", "resolution": "1k"}
    data = _runninghub_json_request(submit_url, api_key, payload, timeout=90)
    task_id = _runninghub_task_id(data)
    image_url = _runninghub_result_url(data)
    if not image_url and not task_id:
        raise RuntimeError(f"RunningHub 未返回 taskId 或图片 URL：{data}")
    if not image_url:
        image_url, _result = _wait_for_runninghub_image(task_id, base_url=base_url, api_key=api_key)
    dest = _save_persona_image_from_url(persona, image_url, variant=variant, source_label="runninghub")
    PersonaRepo.upsert(_persona_payload(persona, avatar_path=str(dest)))
    return str(dest)


def _generate_persona_reference_image_from_source(persona: Persona, *, variant: int = 0, instruction: str = "") -> str:
    runninghub_error = ""
    try:
        return _generate_persona_reference_image_via_runninghub(persona, variant=variant, instruction=instruction)
    except Exception as exc:
        runninghub_error = str(exc)

    PERSONA_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    prompt = _persona_reference_prompt(persona, instruction)
    seed_source = f"{persona.id}:{persona.name}:{variant}:{instruction}:{time.time_ns()}"
    seed = int(hashlib.sha256(seed_source.encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
    params = {
        "prompt": prompt,
        "prompt_text": prompt,
        "message": prompt,
        "tg_user_instruction": prompt,
        "tg_generation_context": "persona_reference_image",
        "tg_use_llm_prompt": False,
        "aspect_ratio": "1:1",
        "ratio": "1:1",
        "width": 1024,
        "height": 1024,
        "batch_size": 1,
        "seed": seed,
        "negative_prompt": "cartoon, illustration, anime, vector, flat avatar, icon, poster layout, text, watermark, logo, frame, collage, emoji, blurry, low quality, distorted face, bad anatomy",
        "text_to_image_return_count": 1,
        "text_to_image_auto_qa_enabled": True,
        "text_to_image_auto_qa_max_attempts": 3,
    }
    base, data = _source_submit_task("text_to_image", params)
    if not data.get("ok"):
        raise RuntimeError(f"来源 text_to_image 提交失败：{data.get('detail') or data.get('message') or data}")
    task_id = str(data.get("id") or "").strip()
    if not task_id:
        raise RuntimeError(f"来源 text_to_image 未返回任务 ID：{data}")
    try:
        source_path, _task = _wait_for_source_image_task(task_id)
    except Exception as exc:
        if runninghub_error:
            raise RuntimeError(f"RunningHub 人设图失败：{runninghub_error}；来源 text_to_image 也失败：{exc}") from exc
        raise
    source = Path(source_path)
    suffix = source.suffix.lower() if source.suffix.lower() in LOCAL_IMAGE_EXTS else ".png"
    filename = f"{_safe_filename(persona.id)}_source_{int(time.time())}_{variant or 0}{suffix}"
    dest = PERSONA_IMAGE_DIR / filename
    shutil.copy2(source, dest)
    PersonaRepo.upsert(_persona_payload(persona, avatar_path=str(dest)))
    return str(dest)


def _generate_persona_reference_image(persona: Persona, *, variant: int = 0, instruction: str = "") -> str:
    return _generate_persona_reference_image_from_source(persona, variant=variant, instruction=instruction)

    from PIL import Image, ImageDraw, ImageFilter

    PERSONA_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    seed = f"{persona.id}:{persona.name}:{persona.description}:{persona.style_prompt}:{variant}:{instruction}:{time.time_ns()}"
    colors = _palette(seed)
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).digest()
    width = height = 1024
    img = Image.new("RGB", (width, height), colors["bg1"])
    px = img.load()
    bg1, bg2 = colors["bg1"], colors["bg2"]
    for y in range(height):
        ratio = y / max(1, height - 1)
        for x in range(width):
            wave = ((x // 64 + y // 64) % 2) * 10
            px[x, y] = tuple(
                max(0, min(255, int(bg1[i] * (1 - ratio) + bg2[i] * ratio) + wave))
                for i in range(3)
            )
    img = img.filter(ImageFilter.GaussianBlur(radius=0.35))
    draw = ImageDraw.Draw(img)

    title_font = _font(54, bold=True)
    name_font = _font(46, bold=True)
    body_font = _font(28)
    small_font = _font(22)
    tag_font = _font(24, bold=True)

    draw.rounded_rectangle((70, 70, 954, 954), radius=44, fill=(255, 255, 255), outline=(255, 255, 255), width=2)
    draw.rounded_rectangle((92, 92, 932, 932), radius=34, outline=(225, 232, 240), width=2)

    # Stylized portrait area.
    draw.ellipse((275, 150, 749, 624), fill=(250, 217, 198), outline=(255, 255, 255), width=8)
    hair = colors["hair"]
    draw.pieslice((250, 118, 774, 565), 185, 360, fill=hair)
    draw.rounded_rectangle((330, 110, 704, 250), radius=80, fill=hair)
    draw.ellipse((390, 310, 430, 350), fill=(42, 42, 50))
    draw.ellipse((594, 310, 634, 350), fill=(42, 42, 50))
    draw.arc((448, 390, 576, 462), 15, 165, fill=(170, 82, 82), width=8)
    draw.ellipse((330, 365, 400, 410), fill=(246, 174, 174))
    draw.ellipse((624, 365, 694, 410), fill=(246, 174, 174))
    draw.rounded_rectangle((294, 620, 730, 875), radius=64, fill=colors["cloth"])
    draw.polygon([(512, 622), (438, 735), (586, 735)], fill=(255, 245, 235))
    draw.rounded_rectangle((170, 780, 854, 900), radius=40, fill=(255, 255, 255), outline=(232, 238, 244), width=2)

    name = persona.name or "未命名人设"
    desc = persona.description or persona.style_prompt or "尚未填写人设简介"
    tags = [item.strip() for item in re.split(r"[；;,、\n]+", persona.style_prompt or "") if item.strip()][:4]
    if not tags:
        tags = [item for item in re.split(r"[，。,.\s]+", desc) if len(item) >= 2][:4]
    tags = tags[:4] or ["Threads", "真人感", "日常感"]

    draw.text((120, 112), "人设参考图", fill=(28, 40, 58), font=title_font)
    draw.text((120, 182), name[:16], fill=(20, 31, 45), font=name_font)

    tag_x = 120
    tag_y = 246
    for tag in tags:
        label = tag[:12]
        w = _text_width(draw, label, tag_font) + 32
        if tag_x + w > 900:
            tag_x = 120
            tag_y += 48
        draw.rounded_rectangle((tag_x, tag_y, tag_x + w, tag_y + 36), radius=18, fill=colors["accent"])
        draw.text((tag_x + 16, tag_y + 4), label, fill=(255, 255, 255), font=tag_font)
        tag_x += w + 10

    y = 790
    draw.text((205, y), "角色设定", fill=(56, 72, 92), font=small_font)
    y += 36
    for line in _wrap_text(draw, desc, body_font, 620, max_lines=3):
        draw.text((205, y), line, fill=(25, 35, 50), font=body_font)
        y += 36
    draw.text((620, 900), "Generated by Web Bot", fill=(116, 128, 146), font=small_font)

    filename = f"{_safe_filename(persona.id)}_{int(time.time())}_{variant}.png"
    path = PERSONA_IMAGE_DIR / filename
    img.save(path, format="PNG", optimize=True)
    PersonaRepo.upsert(_persona_payload(persona, avatar_path=str(path), style_prompt=persona.style_prompt or "；".join(tags)))
    return str(path)


def _post_image_prompt(persona: Persona, post_text: str) -> str:
    name = (persona.name or "persona").strip()
    description = (persona.description or "").strip()
    style = (persona.style_prompt or "").strip()
    parts = [
        "The attached persona reference image is the identity anchor. Keep the same recognizable face: face shape, eyes, nose, mouth, age impression, hairline, hairstyle, skin tone, and overall temperament. Do not create a different person.",
        "Only the face identity must remain locked. Clothing, pose, scene, action, camera angle, lighting, and props should follow the current Threads post context instead of copying the reference image background.",
        "Create a realistic non-cartoon social-feed photo for Threads. No text, no watermark, no logo, no poster layout, no speech bubbles, no UI frame.",
        "Composition: natural phone-camera lifestyle photo, medium shot or three-quarter composition, face visible and unobstructed, both eyes/nose/mouth clearly in frame, hands away from the face.",
        f"Persona name: {name}",
    ]
    if description:
        parts.append(f"Persona profile: {description[:1200]}")
    if style:
        parts.append(f"Persona style cues: {style[:800]}")
    parts.append(f"Current Threads post: {str(post_text or '').strip()[:1400]}")
    return "\n".join(parts)


def _save_post_image_from_url(persona: Persona, image_url: str, index: int, *, source_label: str = "runninghub_post") -> str:
    POST_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = _safe_filename(persona.id)
    if image_url.startswith("data:image/"):
        match = re.match(r"data:(image/[^;]+);base64,(.+)", image_url, flags=re.S)
        if not match:
            raise RuntimeError("RunningHub returned an invalid data:image result")
        mime = match.group(1).lower()
        suffix = ".jpg" if "jpeg" in mime or "jpg" in mime else ".webp" if "webp" in mime else ".png"
        content = base64.b64decode(match.group(2))
    else:
        request = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=max(30, _env_int("PERSONA_IMAGE_DOWNLOAD_TIMEOUT", 90))) as response:
            content = response.read()
            content_type = str(response.headers.get("Content-Type") or "").lower()
        suffix = Path(urllib.parse.urlparse(image_url).path).suffix.lower()
        if suffix not in LOCAL_IMAGE_EXTS:
            if "jpeg" in content_type or "jpg" in content_type:
                suffix = ".jpg"
            elif "webp" in content_type:
                suffix = ".webp"
            else:
                suffix = ".png"
    path = POST_IMAGE_DIR / f"{safe_id}_post_{index + 1}_{source_label}_{int(time.time())}{suffix}"
    path.write_bytes(content)
    _write_image_source_meta(path, source_url=image_url, source_label=source_label)
    return str(path)


def _generate_post_image_via_runninghub(persona: Persona, post_text: str, index: int) -> str:
    if not _avatar_exists(persona):
        raise RuntimeError("Persona reference image is required before generating post images.")
    _base, runtime = _source_runtime_config_data()
    api_key = str(runtime.get("new_persona_runninghub_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("source runtime_config missing new_persona_runninghub_api_key")
    base_url = str(runtime.get("new_persona_runninghub_base_url") or "https://www.runninghub.ai").strip()
    endpoint = str(runtime.get("new_persona_runninghub_tweet_i2i_endpoint") or "/rhart-image-n-g31-flash/image-to-image").strip()
    reference_url = _read_image_source_url(persona.avatar_path)
    if not reference_url:
        reference_url = _runninghub_upload_media(base_url, api_key, persona.avatar_path)
    payload = {
        "imageUrls": [reference_url],
        "prompt": _post_image_prompt(persona, post_text),
        "aspectRatio": os.getenv("PERSONA_POST_IMAGE_ASPECT_RATIO", "1:1"),
        "resolution": os.getenv("PERSONA_POST_IMAGE_RESOLUTION", "1k"),
    }
    data = _runninghub_json_request(_runninghub_api_url(base_url, endpoint), api_key, payload, timeout=90)
    task_id = _runninghub_task_id(data)
    image_url = _runninghub_result_url(data)
    if not image_url and not task_id:
        raise RuntimeError(f"RunningHub post image returned no taskId or image URL: {data}")
    if not image_url:
        image_url, _result = _wait_for_runninghub_image(task_id, base_url=base_url, api_key=api_key)
    return _save_post_image_from_url(persona, image_url, index, source_label="runninghub_post")


def _generate_post_image(persona: Persona, post_text: str, index: int) -> str:
    try:
        return _generate_post_image_via_runninghub(persona, post_text, index)
    except Exception:
        if os.getenv("PERSONA_POST_IMAGE_ALLOW_LOCAL_FALLBACK", "0").strip().lower() not in {"1", "true", "yes"}:
            raise

    from PIL import Image, ImageDraw

    POST_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    canvas_w, canvas_h = 1080, 1350
    colors = _palette(f"post:{persona.id}:{index}:{post_text}")
    img = Image.new("RGB", (canvas_w, canvas_h), colors["bg1"])
    draw = ImageDraw.Draw(img)
    for y in range(canvas_h):
        ratio = y / max(1, canvas_h - 1)
        fill = tuple(int(colors["bg1"][i] * (1 - ratio) + colors["bg2"][i] * ratio) for i in range(3))
        draw.line((0, y, canvas_w, y), fill=fill)
    draw.rounded_rectangle((64, 64, 1016, 1286), radius=52, fill=(255, 255, 255))

    if _avatar_exists(persona):
        try:
            avatar = Image.open(persona.avatar_path).convert("RGB")
            avatar.thumbnail((420, 420))
            ax = (canvas_w - avatar.width) // 2
            img.paste(avatar, (ax, 120))
        except Exception:
            pass

    title_font = _font(42, bold=True)
    body_font = _font(42, bold=True)
    small_font = _font(26)
    draw.text((120, 590), persona.name[:18], fill=(30, 41, 59), font=title_font)
    y = 680
    for line in _wrap_text(draw, post_text, body_font, 840, max_lines=8):
        draw.text((120, y), line, fill=(17, 24, 39), font=body_font)
        y += 62
    draw.rounded_rectangle((120, 1164, 420, 1222), radius=28, fill=colors["accent"])
    draw.text((150, 1177), "Threads Draft", fill=(255, 255, 255), font=small_font)
    draw.text((690, 1178), datetime.now().strftime("%Y-%m-%d"), fill=(100, 116, 139), font=small_font)

    filename = f"{_safe_filename(persona.id)}_post_{index + 1}_{int(time.time())}.png"
    path = POST_IMAGE_DIR / filename
    img.save(path, format="PNG", optimize=True)
    return str(path)


def _persona_image_keyboard(persona_id: str) -> list[list[dict[str, str]]]:
    return _rows(
        [_btn("✍️ 用此人设图新建推文", f"genpost:{persona_id}", "primary")],
        [_btn("🔄 AI 重新生成图片", f"regenimg:{persona_id}"), _btn("📤 上传自定义图片替换", f"uploadimg:{persona_id}")],
        [_btn("🧾 查看人设详情", f"pd:{persona_id}")],
    )


def _persona_image_message(persona: Persona, path: str | Path, *, regenerated: bool = False) -> dict[str, Any]:
    verb = "重新生成" if regenerated else "生成"
    text = "\n".join(
        [
            f"✅ 已为人设「{persona.name}」{verb}人设图",
            "",
            "用途：锁定人物长相、作为推文配图/视频参考图。",
            "下一步可以直接生成推文，系统会自动带入这张人设图。",
        ]
    )
    return _message(text, _persona_image_keyboard(persona.id), image=_image_data_url(path))


def _generate_persona_image_response(persona_id: str, *, regenerate: bool = False, instruction: str = "") -> dict[str, Any]:
    persona, _row = _resolve_persona_for_action(persona_id)
    if not persona:
        return _response(_message("没有找到这个本地人设，不能生成人设图。", [[_btn("◀️ 返回人设列表", "list_personas")]]))
    persona_id = persona.id
    messages = [
        _message(f"🎨 正在通过来源人设图工作流为人设「{persona.name}」{'重新' if regenerate else ''}生成人设图...", kind="status")
    ]
    try:
        path = _generate_persona_reference_image(persona, variant=int(time.time()) if regenerate else 0, instruction=instruction)
        updated = PersonaRepo.get(persona_id) or persona
        messages.append(_persona_image_message(updated, path, regenerated=regenerate))
    except Exception as exc:
        messages.append(
            _message(
                f"人设图生成失败：{exc}\n\n请确认 D:\\workflow_delivery_package_source 服务已启动，并且 text_to_image 工作流可用。",
                _rows([_btn("🔄 再试一次", f"regenimg:{persona_id}")], [_btn("◀️ 返回人设详情", f"pd:{persona_id}")]),
            )
        )
    return _response(messages, state={"flow": "persona_image", "draft": {"persona_id": persona_id, "name": persona.name}})


def _view_persona_image(persona_id: str) -> dict[str, Any]:
    persona, row = _resolve_persona_for_action(persona_id)
    if not persona and not row:
        return _response(_message("没有找到这个本地人设。", [[_btn("◀️ 返回人设列表", "list_personas")]]))
    if persona and _avatar_exists(persona):
        return _response(
            _persona_image_message(persona, persona.avatar_path),
            state={"flow": "persona_image", "draft": {"persona_id": persona.id, "name": persona.name}},
        )
    source_image = _persona_reference_image_url(row)
    if not source_image:
        row = _fresh_persona_row(persona_id, persona, row)
        source_image = _persona_reference_image_url(row)
    if source_image:
        name = persona.name if persona else _persona_row_name(row or {})
        archive_id = _tool_r18_archive_id(persona_id, persona, row) or persona_id
        return _response(
            _message(
                f"👁 人设「{name}」当前参考图",
                [[_btn("◀️ 返回设置", f"settings_{archive_id}")]],
                image=source_image,
            ),
            state={"flow": "persona_image", "draft": {"persona_id": archive_id, "name": name}},
        )
    archive_id = _tool_r18_archive_id(persona_id, persona, row) or persona_id
    return _response(
        _message(
            "❌ 这个非工作流人設还没有人设图，请先生成。",
            [[_btn("◀️ 返回设置", f"settings_{archive_id}")]],
        ),
        state={"flow": ""},
    )


def _run_persona_image_job(job_id: str, persona_id: str, regenerate: bool, instruction: str) -> None:
    try:
        SourceWorkflowJobRepo.update(job_id, status="running")
        persona = PersonaRepo.get(persona_id)
        if not persona:
            raise RuntimeError("persona not found")
        path = _generate_persona_reference_image(
            persona,
            variant=int(time.time()) if regenerate else 0,
            instruction=instruction,
        )
        SourceWorkflowJobRepo.update(job_id, status="success", result={"persona_id": persona_id, "image_path": path})
    except Exception as exc:
        SourceWorkflowJobRepo.update(job_id, status="failed", error=str(exc))


def _submit_persona_image_job(persona: Persona, *, regenerate: bool = False, instruction: str = "") -> Any:
    job = SourceWorkflowJobRepo.create(
        "persona_reference_image",
        f"{'重新生成' if regenerate else '生成'}人設圖：{persona.name}",
        {"persona_id": persona.id, "regenerate": bool(regenerate), "instruction": instruction},
        status="queued_external",
    )
    thread = threading.Thread(
        target=_run_persona_image_job,
        args=(job.id, persona.id, bool(regenerate), instruction),
        name=f"persona-image-{job.id[:6]}",
        daemon=True,
    )
    thread.start()
    return job


def _generate_persona_image_response(persona_id: str, *, regenerate: bool = False, instruction: str = "") -> dict[str, Any]:
    persona, row = _resolve_persona_for_action(persona_id)
    if not persona and not row:
        return _response(_message("沒有找到這個人設，不能生成人設圖。", [[_btn("◀️ 返回人設列表", "list_personas")]]))
    if _is_workflow_persona_row(row, persona_id):
        return _response(
            _message("⭐ 工作流人設不需要單獨生成人設圖。", [[_btn("◀️ 返回設定", f"settings_{persona_id}")]]),
            state={"flow": "", "draft": {"persona_id": persona_id}},
        )
    source_archive_id = _tool_r18_archive_id(persona_id, persona, row)
    if not source_archive_id:
        return _response(
            _message("這個 Web 本地人設尚未同步到 Tool R18 人設庫，不能執行 TG Bot 人設圖流程。", [[_btn("◀️ 返回人設詳情", f"pd_{persona_id}")]]),
            state={"flow": ""},
        )
    name = persona.name if persona else _persona_row_name(row or {})
    return _submit_source_post_task(
        "persona_generate_image",
        source_archive_id,
        "",
        {"archiveId": source_archive_id, "regenerate": bool(regenerate), "instruction": str(instruction or "").strip()},
        f"{'重新生成' if regenerate else '生成'}人設圖：{name}",
    )


def _replace_persona_image_start(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("没有找到这个本地人设，不能替换人设图。", [[_btn("◀️ 返回人设列表", "list_personas")]]))
    text = "\n".join(
        [
            "📤 上传自定义人设图替换",
            "",
            f"人设：{persona.name}",
            "请在输入框发送本机图片路径。",
            "",
            r"例如：D:\素材\persona.png",
        ]
    )
    return _response(
        _message(text, [[_btn("❌ 取消", f"viewimg:{persona_id}")]]),
        state={"flow": "replace_persona_image", "draft": {"persona_id": persona_id}},
    )


def _replace_persona_image_from_text(text: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    persona_id = str(draft.get("persona_id") or "")
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("没有找到这个本地人设。", [[_btn("◀️ 返回人设列表", "list_personas")]]), state={"flow": ""})
    raw_path = text.strip().strip('"').strip("'")
    if raw_path.lower().startswith(("http://", "https://")):
        return _response(_message("Web 版目前请发送本机图片路径，暂不直接下载网址图片。", [[_btn("❌ 取消", f"viewimg:{persona_id}")]]), state=state)
    source = Path(raw_path).expanduser()
    if not source.exists() or not source.is_file():
        return _response(_message("找不到这张图片，请确认路径后重新发送。", [[_btn("❌ 取消", f"viewimg:{persona_id}")]]), state=state)
    if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return _response(_message("请发送 png、jpg、jpeg 或 webp 图片路径。", [[_btn("❌ 取消", f"viewimg:{persona_id}")]]), state=state)
    PERSONA_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    dest = PERSONA_IMAGE_DIR / f"{_safe_filename(persona.id)}_custom_{int(time.time())}{source.suffix.lower()}"
    shutil.copy2(source, dest)
    PersonaRepo.upsert(_persona_payload(persona, avatar_path=str(dest)))
    updated = PersonaRepo.get(persona_id) or persona
    return _response(
        [
            _message(f"✅ 已替换「{updated.name}」的人设图。", kind="status"),
            _persona_image_message(updated, dest),
        ],
        state={"flow": ""},
    )


def _genpost_memory_prompt(persona_id: str, name: str) -> dict[str, Any]:
    return _message(
        "\n".join(
            [
                "✍️ 新建推文",
                "",
                f"人设：{name}",
                "内容类型：免费群内容",
                "模式：早上文案 + 配图 / 视频",
                "",
                "请先输入这次要带入的记忆、素材或方向。",
                "例如：今天想聊夏天健身、低卡饮食，语气像真实日常分享。",
                "",
                "也可以按「略过记忆」，直接输入生成数量。",
            ]
        ),
        _rows([_btn("⏭ 略过记忆", "genpost_memory_skip")], [_btn("◀️ 返回", f"pd:{persona_id}")]),
    )


def _genpost_count_prompt(persona_id: str, name: str, memory: str = "") -> dict[str, Any]:
    memory = to_traditional(str(memory or "").strip())
    memory_status = "已加入 1 条" if memory else "未指定"
    memory_preview = f"记忆摘要：{memory[:80]}{'...' if len(memory) > 80 else ''}" if memory else "记忆摘要：-"
    return _message(
        "\n".join(
            [
                "✍️ 新建推文",
                "",
                f"人设：{name}",
                "内容类型：免费群内容",
                "模式：早上文案 + 配图 / 视频",
                f"指定记忆：{memory_status}",
                memory_preview,
                "",
                "⭐ 请输入生成数量 ⭐",
                "只需要发送数字即可。",
                "",
                "例如：3",
            ]
        ),
        [[_btn("◀️ 返回", f"pd:{persona_id}")]],
    )


MEMORY_GRANULARITY_LABELS = {
    "all": "全部記憶",
    "daily": "每日記憶",
    "topic": "主題記憶",
    "persona": "人設長期記憶",
    "hot": "熱點輿情",
}


def _memory_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _memory_granularity_label(value: str) -> str:
    return MEMORY_GRANULARITY_LABELS.get(str(value or "daily"), "每日記憶")


def _memory_excerpt(text: Any, size: int = 100) -> str:
    compact = re.sub(r"\s+", " ", to_traditional(str(text or "")).strip())
    return compact[:size] + ("..." if len(compact) > size else "")


def _record_post_memory(
    persona_id: str,
    content: str,
    *,
    granularity: str = "daily",
    source_type: str = "",
    source_ref: str = "",
    title: str = "",
    favorite: bool = False,
    payload: dict[str, Any] | None = None,
) -> PostMemory | None:
    content = to_traditional(str(content or "").strip())
    persona_id = str(persona_id or "").strip()
    if not persona_id or not content:
        return None
    title = to_traditional(str(title or "").strip()) or _memory_excerpt(content, 28)
    return PostMemoryRepo.create(
        persona_id,
        content,
        memory_date=_memory_date(),
        granularity=str(granularity or "daily"),
        source_type=source_type,
        source_ref=source_ref,
        title=title,
        favorite=favorite,
        payload=payload or {},
    )


def _link_ending_settings_record(persona_id: str) -> PostMemory | None:
    for memory in PostMemoryRepo.list_for_persona(persona_id, limit=200):
        if memory.source_type == LINK_ENDING_SOURCE_TYPE:
            return memory
    return None


def _normalize_link_url(value: str) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if url.startswith("www."):
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse(parsed._replace(fragment="")).strip()


def _link_ending_settings(persona_id: str) -> dict[str, Any]:
    memory = _link_ending_settings_record(persona_id)
    payload = memory.payload if memory else {}
    presets = payload.get("linkEndingPresets") if isinstance(payload.get("linkEndingPresets"), list) else []
    clean: list[dict[str, Any]] = []
    for preset in presets:
        if not isinstance(preset, dict):
            continue
        item = {
            "id": re.sub(r"[^a-zA-Z0-9-]", "", str(preset.get("id") or ""))[:40],
            "name": str(preset.get("name") or "").strip()[:40],
            "endingText": str(preset.get("endingText") or "").strip()[:240],
            "linkUrl": _normalize_link_url(str(preset.get("linkUrl") or "")),
            "enabled": preset.get("enabled") is not False,
            "createdAt": str(preset.get("createdAt") or "").strip(),
            "updatedAt": str(preset.get("updatedAt") or "").strip(),
        }
        if item["id"] and (item["endingText"] or item["linkUrl"]):
            clean.append(item)
    active_id = re.sub(r"[^a-zA-Z0-9-]", "", str(payload.get("activeLinkEndingPresetId") or ""))[:40]
    if active_id and not any(item["id"] == active_id for item in clean):
        active_id = ""
    return {"linkEndingPresets": clean, "activeLinkEndingPresetId": active_id}


def _save_link_ending_settings(persona_id: str, settings: dict[str, Any], title: str = "链接设置") -> None:
    presets = settings.get("linkEndingPresets") if isinstance(settings.get("linkEndingPresets"), list) else []
    active_id = str(settings.get("activeLinkEndingPresetId") or "").strip()
    lines = ["链接结尾预设"]
    for index, preset in enumerate(presets, start=1):
        mark = "✓" if preset.get("id") == active_id and preset.get("enabled") is not False else "☐"
        lines.append(f"{index}. {mark} {preset.get('name') or preset.get('endingText') or preset.get('linkUrl')}")
        if preset.get("endingText"):
            lines.append(str(preset.get("endingText")))
        if preset.get("linkUrl"):
            lines.append(str(preset.get("linkUrl")))
    _record_post_memory(
        persona_id,
        "\n".join(lines),
        granularity="persona",
        source_type=LINK_ENDING_SOURCE_TYPE,
        title=title,
        favorite=False,
        payload={"linkEndingPresets": presets, "activeLinkEndingPresetId": active_id},
    )


def _parse_link_ending_preset(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    url_match = re.search(r"https?://[^\s]+|www\.[^\s]+", raw)
    link_url = _normalize_link_url(url_match.group(0).rstrip(".,;，。；)") if url_match else "")
    ending = raw
    if url_match:
        ending = (raw[: url_match.start()] + raw[url_match.end() :]).strip()
    ending_text = "\n".join(line.strip() for line in ending.splitlines() if line.strip())[:240]
    if not link_url and not ending_text:
        return None
    name = re.sub(r"\s+", " ", ending_text or link_url).strip()[:24]
    return {"name": name, "linkUrl": link_url, "endingText": ending_text}


def _active_link_ending_preset(persona_id: str) -> dict[str, Any] | None:
    settings = _link_ending_settings(persona_id)
    presets = settings.get("linkEndingPresets", [])
    active_id = str(settings.get("activeLinkEndingPresetId") or "")
    for preset in presets:
        if preset.get("enabled") is not False and (not active_id or preset.get("id") == active_id):
            return preset
    return None


def _apply_link_ending_to_text(text: str, preset: dict[str, Any] | None) -> str:
    next_text = str(text or "").strip()
    if not preset:
        return next_text
    for segment in (str(preset.get("endingText") or "").strip(), str(preset.get("linkUrl") or "").strip()):
        if not segment:
            continue
        next_text = re.sub(re.escape(segment), "", next_text).strip()
        next_text = re.sub(r"\n{3,}", "\n\n", next_text).strip()
        next_text = f"{next_text}\n{segment}".strip()
    return next_text


def _apply_link_ending_to_posts(persona_id: str, posts: list[str]) -> list[str]:
    preset = _active_link_ending_preset(persona_id)
    if not preset:
        return posts
    return [_apply_link_ending_to_text(post, preset) for post in posts]


def _link_ending_menu(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("没有找到这个人设。", [[_btn("返回主菜单", "menu")]]), state={"flow": ""})
    settings = _link_ending_settings(persona_id)
    presets = settings.get("linkEndingPresets", [])
    active_id = str(settings.get("activeLinkEndingPresetId") or "")
    active = next((item for item in presets if item.get("enabled") is not False and item.get("id") == active_id), None)
    lines = [
        "🔗 链接设置",
        "",
        f"人设：{_local_persona_display_name(persona)}",
        "",
        f"当前启用：{(active or {}).get('name') or (active or {}).get('endingText') or (active or {}).get('linkUrl') or '未启用'}",
        "",
        "预设模板：",
    ]
    if presets:
        for index, preset in enumerate(presets[:8], start=1):
            mark = "✅" if preset.get("enabled") is not False and preset.get("id") == active_id else "☐"
            lines.append(f"{index}. {mark} {preset.get('name') or '模板'}")
            lines.append(f"结尾语句：{preset.get('endingText') or ''}")
            lines.append(f"链接：{preset.get('linkUrl') or ''}")
    else:
        lines.append("尚未设定。")
    lines.extend(["", "新增模板时只需要发送结尾语句和链接；不要发送整篇推文正文。"])
    rows = [[_btn("➕ 新增模板", f"linkpreset_add_{persona_id}")]]
    for index, preset in enumerate(presets[:8]):
        mark = "✅" if preset.get("enabled") is not False and preset.get("id") == active_id else "☐"
        rows.append([
            _btn(f"{mark} {preset.get('name') or '模板'}", f"lpu_{persona_id}_{index}"),
            _btn("编辑", f"lpe_{persona_id}_{index}"),
            _btn("删除", f"lpd_{persona_id}_{index}"),
        ])
    if active:
        rows.append([_btn("⏸ 停用結尾預設", f"linkpreset_off_{persona_id}")])
    rows.append([_btn("◀️ 返回人设设置", f"settings_{persona_id}")])
    return _response(_message("\n".join(lines), rows), state={"flow": ""})


def _link_ending_add_prompt(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("没有找到这个人设。", [[_btn("返回主菜单", "menu")]]), state={"flow": ""})
    return _response(
        _message(
            "\n".join([
                "🔗 新增链接设置模板",
                "",
                f"人设：{_local_persona_display_name(persona)}",
                "",
                "请发送结尾语句和链接。",
                "格式示例：",
                "想看更多整理，我放这里",
                "https://example.com/more",
                "",
                "这里只保存结尾语句和链接，不要发送整篇推文正文。",
            ]),
            [[_btn("取消", f"linksettings:{persona_id}")]],
        ),
        state={"flow": "link_ending_add", "draft": {"persona_id": persona_id}},
    )


def _link_ending_edit_menu(persona_id: str, index: int) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    settings = _link_ending_settings(persona_id)
    presets = settings.get("linkEndingPresets", [])
    if not persona or index < 0 or index >= len(presets):
        return _link_ending_menu(persona_id)
    preset = presets[index]
    return _response(
        _message(
            "\n".join([
                "🔗 编辑链接设置模板",
                "",
                f"人设：{_local_persona_display_name(persona)}",
                f"模板名称：{preset.get('name') or '模板'}",
                f"结尾语句：{preset.get('endingText') or ''}",
                f"链接：{preset.get('linkUrl') or ''}",
                "",
                "可以单独修改模板名称。",
            ]),
            _rows(
                [_btn("✏️ 修改模板名称", f"lpn_{persona_id}_{index}")],
                [_btn("🔗 修改模板内容", f"lpc_{persona_id}_{index}")],
                [_btn("◀️ 返回链接设置", f"linksettings_{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _link_ending_input_prompt(persona_id: str, index: int, kind: str) -> dict[str, Any]:
    prompt = "请发送新的模板名称。" if kind == "name" else "\n".join([
        "请发送新的结尾语句和链接。",
        "",
        "格式示例：",
        "想看更多整理，我放这里",
        "https://example.com/more",
    ])
    return _response(
        _message(prompt, [[_btn("取消", f"lpe_{persona_id}_{index}")]]),
        state={"flow": f"link_ending_edit_{kind}", "draft": {"persona_id": persona_id, "index": index}},
    )


def _save_link_ending_input(text: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    persona_id = str(draft.get("persona_id") or "").strip()
    flow = str(state.get("flow") or "")
    settings = _link_ending_settings(persona_id)
    presets = list(settings.get("linkEndingPresets", []))
    now = datetime.now().isoformat()
    if flow == "link_ending_add":
        parsed = _parse_link_ending_preset(text)
        if not parsed:
            return _response(_message("❌ 没有读取到结尾语句或链接。请重新进入「链接设置」后再新增。", [[_btn("◀️ 返回链接设置", f"linksettings:{persona_id}")]]), state={"flow": ""})
        preset = {"id": f"lp-{int(time.time() * 1000):x}", **parsed, "enabled": True, "createdAt": now, "updatedAt": now}
        presets = [{**item, "enabled": False} for item in presets] + [preset]
        settings = {"linkEndingPresets": presets, "activeLinkEndingPresetId": preset["id"]}
        _save_link_ending_settings(persona_id, settings, "链接设置模板")
        return _response(_message("✅ 链接设置模板已保存并启用。", [[_btn("🔗 查看链接设置", f"linksettings:{persona_id}")]]), state={"flow": ""})
    index = int(draft.get("index") or -1)
    if index < 0 or index >= len(presets):
        return _link_ending_menu(persona_id)
    if flow == "link_ending_edit_name":
        name = re.sub(r"\s+", " ", str(text or "").strip())[:40]
        if not name:
            return _response(_message("❌ 模板名称不能为空。", [[_btn("◀️ 返回链接设置", f"linksettings:{persona_id}")]]), state={"flow": ""})
        presets[index] = {**presets[index], "name": name, "updatedAt": now}
        _save_link_ending_settings(persona_id, {**settings, "linkEndingPresets": presets}, "链接设置模板名称")
        return _response(_message(f"✅ 模板名称已更新：{name}", [[_btn("🔗 查看链接设置", f"linksettings:{persona_id}")]]), state={"flow": ""})
    if flow == "link_ending_edit_content":
        parsed = _parse_link_ending_preset(text)
        if not parsed:
            return _response(_message("❌ 没有读取到结尾语句或链接。", [[_btn("◀️ 返回链接设置", f"linksettings:{persona_id}")]]), state={"flow": ""})
        presets[index] = {**presets[index], **parsed, "updatedAt": now}
        _save_link_ending_settings(persona_id, {**settings, "linkEndingPresets": presets}, "链接设置模板内容")
        return _response(_message("✅ 模板内容已更新。", [[_btn("🔗 查看链接设置", f"linksettings:{persona_id}")]]), state={"flow": ""})
    return _link_ending_menu(persona_id)


def _memory_text(memory: PostMemory) -> str:
    star = "⭐ " if int(memory.favorite or 0) else ""
    title = memory.title or _memory_excerpt(memory.content, 28)
    return f"{star}{title}｜{_memory_granularity_label(memory.granularity)}｜{memory.memory_date}\n{_memory_excerpt(memory.content, 160)}"


def _genpost_memory_prompt(persona_id: str, name: str) -> dict[str, Any]:
    recent = [memory for memory in PostMemoryRepo.list_for_persona(persona_id, limit=10) if memory.source_type != LINK_ENDING_SOURCE_TYPE][:3]
    recent_lines = ["最近記憶："]
    if recent:
        for index, memory in enumerate(recent, start=1):
            recent_lines.append(f"{index}. {_memory_excerpt(memory.title or memory.content, 48)}")
    else:
        recent_lines.append("尚未保存記憶，可以先使用熱點輿情或手動輸入。")
    return _message(
        "\n".join(
            [
                "✍️ 新建推文",
                "",
                f"人設：{name}",
                "請選擇這次推文要繼承的記憶顆粒。",
                "每日記憶會每天保存；收藏記憶可長期反覆使用；熱點抓取會按此人設從 Threads / Instagram 取得候選推文。",
                "",
                *recent_lines,
            ]
        ),
        _rows(
            [_btn("➕ 新增熱點/輿情", "genpost_hot_manual"), _btn("📝 普通推文", f"genpost_memlist:{persona_id}:daily:0")],
            [_btn("📅 每日記憶", f"genpost_memlist:{persona_id}:daily:0"), _btn("🧩 主題記憶", f"genpost_memlist:{persona_id}:topic:0")],
            [_btn("🧠 長期人設記憶", f"genpost_memlist:{persona_id}:persona:0"), _btn("⭐ 收藏記憶", f"genpost_favorites:{persona_id}:all:0")],
            [_btn("✍️ 手動輸入記憶", "genpost_memory_manual"), _btn("⏭ 不使用記憶", "genpost_memory_skip")],
            [_btn("◀️ 返回人設詳情", f"pd:{persona_id}")],
        ),
    )


def _genpost_count_prompt(persona_id: str, name: str, memory: str = "") -> dict[str, Any]:
    memory = to_traditional(str(memory or "").strip())
    memory_status = "已加入" if memory else "未指定"
    memory_preview = _memory_excerpt(memory, 140) if memory else "-"
    return _message(
        "\n".join(
            [
                "✍️ 新建推文",
                "",
                f"人設：{name}",
                f"指定記憶：{memory_status}",
                f"記憶摘要：{memory_preview}",
                "",
                "請輸入要生成的推文數量。",
                f"上限：{GENPOST_MAX_COUNT} 篇",
                "",
                "例如：3",
            ]
        ),
        _rows(
            [_btn("📚 重新選擇記憶", f"genpost:{persona_id}")],
            [_btn("◀️ 返回人設詳情", f"pd:{persona_id}")],
        ),
    )


def _genpost_manual_prompt(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    persona_id = str(draft.get("persona_id") or "")
    return _response(
        _message(
            "✍️ 手動輸入記憶\n\n請直接貼上這次要繼承的記憶、素材、舊推文方向或受眾設定。\n送出後會保存成今天的每日記憶。",
            _rows([_btn("◀️ 返回記憶選擇", f"genpost:{persona_id}")]),
        ),
        state={"flow": "genpost_memory_manual", "draft": draft},
    )


def _genpost_memory_list(action: str, state: dict[str, Any], *, favorite_only: bool = False) -> dict[str, Any]:
    parts = action.split(":")
    persona_id = parts[1] if len(parts) > 1 else str((state.get("draft") or {}).get("persona_id") or "")
    granularity = parts[2] if len(parts) > 2 else "daily"
    page = _num(parts[3]) if len(parts) > 3 else 0
    persona = PersonaRepo.get(persona_id)
    name = _local_persona_display_name(persona) if persona else persona_id
    memories = PostMemoryRepo.list_for_persona(
        persona_id,
        limit=200,
        favorite_only=favorite_only,
        granularity="" if granularity == "all" else granularity,
    )
    memories = [memory for memory in memories if memory.source_type != LINK_ENDING_SOURCE_TYPE]
    page_size = 5
    total_pages = max(1, (len(memories) + page_size - 1) // page_size)
    safe_page = max(0, min(page, total_pages - 1))
    visible = memories[safe_page * page_size : (safe_page + 1) * page_size]
    title = "⭐ 收藏記憶" if favorite_only else f"📚 {_memory_granularity_label(granularity)}"
    lines = [title, "", f"人設：{name}", f"第 {safe_page + 1}/{total_pages} 頁，共 {len(memories)} 筆", ""]
    if visible:
        for index, memory in enumerate(visible, start=safe_page * page_size + 1):
            lines.extend([f"{index}. {_memory_text(memory)}", ""])
    else:
        lines.append("目前沒有這類記憶。可以改用熱點輿情或手動輸入。")
    keyboard: list[list[dict[str, str]]] = []
    for memory in visible:
        keyboard.append(
            [
                _btn(f"繼承：{_memory_excerpt(memory.title or memory.content, 18)}", f"genpost_usemem:{memory.id}", "primary"),
                _btn("取消收藏" if int(memory.favorite or 0) else "收藏", f"memfav:{memory.id}:{persona_id}:{granularity}:{safe_page}"),
            ]
        )
    if total_pages > 1:
        prefix = "genpost_favorites" if favorite_only else "genpost_memlist"
        keyboard.append(
            [
                _btn("◀️ 上一頁", f"{prefix}:{persona_id}:{granularity}:{max(0, safe_page - 1)}"),
                _btn(f"{safe_page + 1}/{total_pages}", f"{prefix}:{persona_id}:{granularity}:{safe_page}"),
                _btn("下一頁 ▶️", f"{prefix}:{persona_id}:{granularity}:{min(total_pages - 1, safe_page + 1)}"),
            ]
        )
    keyboard.extend(
        _rows(
            [_btn("➕ 新增熱點/輿情", "genpost_hot_manual")],
            [_btn("✍️ 手動輸入記憶", "genpost_memory_manual"), _btn("⏭ 不使用記憶", "genpost_memory_skip")],
            [_btn("◀️ 返回記憶選擇", f"genpost:{persona_id}")],
        )
    )
    return _response(_message("\n".join(lines), keyboard), state={"flow": "genpost_memory", "draft": {"persona_id": persona_id, "name": name, "memory": "", "memory_granularity": granularity}})


def _genpost_use_memory(action: str, state: dict[str, Any]) -> dict[str, Any]:
    memory_id = action.split(":", 1)[1] if ":" in action else ""
    memory = PostMemoryRepo.get(memory_id)
    if not memory:
        return _response(_message("找不到這筆記憶，請重新選擇。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    persona = PersonaRepo.get(memory.persona_id)
    name = _local_persona_display_name(persona) if persona else memory.persona_id
    draft = {
        "persona_id": memory.persona_id,
        "name": name,
        "memory": memory.content,
        "memory_id": memory.id,
        "memory_granularity": memory.granularity,
    }
    return _response(_genpost_count_prompt(memory.persona_id, name, memory.content), state={"flow": "genpost_count", "draft": draft})


def _persona_hot_context(persona_id: str, row: dict[str, Any] | None = None, *, global_context: bool = False) -> str:
    overview = build_overview()
    if not row:
        row = find_persona(overview, persona_id)
    lines: list[str] = []
    if row and not global_context:
        name = _persona_row_name(row)
        hot = row.get("hot") if isinstance(row.get("hot"), dict) else {}
        lines.extend(
            [
                f"人設熱點：{name}",
                f"熱度 {_compact(hot.get('hot_score'))}；逐帖瀏覽 {_compact(hot.get('post_views'))}；主頁瀏覽 {_compact(hot.get('recent_views'))}",
                f"互動：讚 {_compact(hot.get('likes'))}／評 {_compact(hot.get('comments'))}／分享 {_compact(hot.get('shares'))}／轉發 {_compact(hot.get('reposts'))}",
            ]
        )
        posts = row.get("post_metrics") if isinstance(row.get("post_metrics"), list) else []
        ranked = sorted(
            [post for post in posts if isinstance(post, dict)],
            key=lambda post: _num(post.get("view_count")) + _num(post.get("like_count")) + _num(post.get("comment_count")) * 3 + _num(post.get("repost_count")) * 2,
            reverse=True,
        )[:5]
        if ranked:
            lines.append("可延伸的熱門單帖：")
            for index, post in enumerate(ranked, start=1):
                content = _remote_post_preview(post, 120)
                lines.append(f"{index}. {content}｜讚 {_compact(post.get('like_count'))}｜評 {_compact(post.get('comment_count'))}｜瀏覽 {_compact(post.get('view_count'))}")
    if global_context or not lines:
        rows = overview.get("personas") if isinstance(overview.get("personas"), list) else []
        ranked_rows = sorted(
            [item for item in rows if isinstance(item, dict)],
            key=lambda item: _num((item.get("hot") or {}).get("hot_score")) if isinstance(item.get("hot"), dict) else 0,
            reverse=True,
        )[:6]
        lines.append("全局輿情熱點：")
        for index, item in enumerate(ranked_rows, start=1):
            hot = item.get("hot") if isinstance(item.get("hot"), dict) else {}
            engagement = _num(hot.get("likes")) + _num(hot.get("comments")) + _num(hot.get("shares")) + _num(hot.get("reposts"))
            lines.append(f"{index}. {_persona_row_name(item)}｜熱度 {_compact(hot.get('hot_score'))}｜瀏覽 {_compact(hot.get('post_views'))}｜互動 {_compact(engagement)}")
    return to_traditional("\n".join(lines).strip())


def _genpost_use_hot(action: str, state: dict[str, Any], *, global_context: bool = False) -> dict[str, Any]:
    parts = action.split(":")
    persona_id = parts[1] if len(parts) > 1 else str((state.get("draft") or {}).get("persona_id") or "")
    granularity = parts[2] if len(parts) > 2 else "hot"
    local, row = _resolve_persona_for_action(persona_id)
    if local:
        persona_id = local.id
    name = _local_persona_display_name(local) if local else (_persona_row_name(row or {}) if row else persona_id)
    context = _persona_hot_context(persona_id, row, global_context=global_context)
    memory = _record_post_memory(
        persona_id,
        context,
        granularity=granularity or "hot",
        source_type="global_hot_opinion" if global_context else "persona_hot_opinion",
        source_ref=persona_id,
        title="全局輿情熱點" if global_context else "人設熱點輿情",
    )
    draft = {
        "persona_id": persona_id,
        "name": name,
        "memory": context,
        "memory_id": memory.id if memory else "",
        "memory_granularity": granularity or "hot",
        "hot_context": context,
        "content_branch": "全局輿情熱點" if global_context else "熱點推文",
        "content_time_slot": "即時熱點",
    }
    return _response(_genpost_count_prompt(persona_id, name, context), state={"flow": "genpost_count", "draft": draft})


def _genpost_hot_manual_prompt(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    persona_id = str(draft.get("persona_id") or "")
    draft.update(
        {
            "memory_granularity": "hot",
            "content_branch": str(draft.get("content_branch") or "手動熱點推文"),
            "content_time_slot": str(draft.get("content_time_slot") or "即時熱點"),
        }
    )
    return _response(
        _message(
            "\n".join(
                [
                    "➕ 新增熱點/輿情",
                    "",
                    "請貼上這次要用來生成推文的熱點資料、原帖內容、數據或觀察。",
                    "送出後會保存成「熱點輿情」記憶，並可直接輸入篇數生成推文。",
                    "",
                    "例如：",
                    "平台 Threads；熱度 30萬；主題是青年貸款與理財焦慮，語氣要像真人觀察。",
                ]
            ),
            _rows([_btn("◀️ 返回記憶選擇", f"genpost:{persona_id}")]),
        ),
        state={"flow": "genpost_hot_manual", "draft": draft},
    )


def _toggle_memory_favorite(action: str, state: dict[str, Any]) -> dict[str, Any]:
    parts = action.split(":")
    memory_id = parts[1] if len(parts) > 1 else ""
    persona_id = parts[2] if len(parts) > 2 else str((state.get("draft") or {}).get("persona_id") or "")
    granularity = parts[3] if len(parts) > 3 else "all"
    page = parts[4] if len(parts) > 4 else "0"
    memory = PostMemoryRepo.get(memory_id)
    if memory:
        PostMemoryRepo.set_favorite(memory_id, not bool(memory.favorite))
    return _genpost_memory_list(f"genpost_memlist:{persona_id}:{granularity}:{page}", state, favorite_only=False)


def _favorite_current_draft(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    memory_id = str(draft.get("generated_memory_id") or draft.get("memory_id") or "")
    if memory_id:
        PostMemoryRepo.set_favorite(memory_id, True)
        return _response(
            [_message("⭐ 已收藏這次推文記憶，之後可從「收藏記憶」繼承。", kind="status"), _post_select_message(draft)],
            state={"flow": "post_select", "draft": draft},
        )
    persona_id = str(draft.get("persona_id") or "")
    posts = [str(item) for item in draft.get("posts", []) if str(item or "").strip()]
    if not persona_id or not posts:
        return _response(_message("目前沒有可收藏的推文記憶。", [[_btn("◀️ 返回", "post_select_back")]]), state=state)
    memory = _record_post_memory(
        persona_id,
        "\n\n".join(posts),
        granularity=str(draft.get("memory_granularity") or "daily"),
        source_type="generated_posts",
        title="生成推文記憶",
        favorite=True,
        payload={"posts": posts},
    )
    if memory:
        draft["generated_memory_id"] = memory.id
    return _response(
        [_message("⭐ 已收藏這次推文記憶，之後可從「收藏記憶」繼承。", kind="status"), _post_select_message(draft)],
        state={"flow": "post_select", "draft": draft},
    )


def _sentiment_action_key(persona_id: str, scope: str, draft: dict[str, Any]) -> str:
    existing = re.sub(r"[^a-f0-9]", "", str(draft.get("sentiment_action_key") or ""))[:8]
    if existing:
        return existing
    return hashlib.md5(f"{persona_id}:{scope}:{time.time()}".encode()).hexdigest()[:8]


def _sentiment_hot_expired(draft: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(draft.get("persona_id") or "")
    return _response(
        _message("热点候选已过期，请重新刷新抓取。", [[_btn("返回新建推文", f"genpost_branch_{persona_id}")]]),
        state={"flow": ""},
    )


def _sentiment_hot_key_matches(draft: dict[str, Any], key: str) -> bool:
    return bool(key and key == str(draft.get("sentiment_action_key") or ""))


def _sentiment_hot_candidate_content(draft: dict[str, Any], candidate: dict[str, Any]) -> str:
    edited = draft.get("hot_edited_contents") if isinstance(draft.get("hot_edited_contents"), dict) else {}
    candidate_id = str(candidate.get("id") or "")
    return str(edited.get(candidate_id) or candidate.get("content") or "").strip()


def _sentiment_hot_media(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (candidate.get("media") if isinstance(candidate.get("media"), list) else []) if isinstance(item, dict)]


def _sentiment_hot_input_media(payload: Any) -> list[dict[str, str]]:
    items = payload if isinstance(payload, list) else []
    result: list[dict[str, str]] = []
    for item in items[:1]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        media_type = str(item.get("type") or "").strip().lower()
        if not url or media_type not in {"image", "video"}:
            continue
        result.append({"url": url, "type": media_type, "name": str(item.get("name") or "").strip()[:160]})
    return result


def _sentiment_hot_media_cards(candidate: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    cards: list[dict[str, Any]] = []
    preview = ""
    for index, item in enumerate(_sentiment_hot_media(candidate), start=1):
        url = _safe_web_media_url(item.get("url") or item.get("localPath"))
        if not url:
            continue
        is_image = str(item.get("type") or "").lower() != "video" and _is_web_image_url(url)
        if is_image and not preview:
            preview = url
        cards.append({
            "title": f"媒体 {index}",
            "subtitle": "视频" if str(item.get("type") or "").lower() == "video" else "图片",
            **({"image": url} if is_image else {"url": url}),
        })
    return preview, cards


def _sentiment_hot_memory_summaries(row: dict[str, Any] | None) -> list[str]:
    entries = row.get("memory_entries") if isinstance(row, dict) and isinstance(row.get("memory_entries"), list) else []
    result: list[str] = []
    for item in sorted(
        [entry for entry in entries if isinstance(entry, dict)],
        key=lambda entry: str(entry.get("date") or ""),
        reverse=True,
    ):
        summary = str(item.get("summary") or "").strip()
        if summary and not _is_auto_imported_hot_memory(summary):
            result.append(summary)
        if len(result) >= 8:
            break
    return result


def _sentiment_hot_submit(task_type: str, label: str, params: dict[str, Any], loading_text: str) -> dict[str, Any]:
    stored_params = params
    if task_type == "persona_sentiment_hot" and isinstance(params.get("edits"), list):
        stored_params = json.loads(json.dumps(params, ensure_ascii=False))
        for edit in stored_params.get("edits", []):
            replacement = edit.get("replacementMedia") if isinstance(edit, dict) and isinstance(edit.get("replacementMedia"), dict) else None
            if replacement and str(replacement.get("url") or "").startswith("data:"):
                replacement["url"] = "[uploaded media]"
    job = SourceWorkflowJobRepo.create(task_type, label, stored_params, status="submitting")
    try:
        base, data = _source_submit_task(task_type, params)
        source_task_id = str(data.get("id") or "")
        SourceWorkflowJobRepo.update(
            job.id,
            status="submitted",
            result=data,
            source_task_id=source_task_id,
            source_base_url=base,
        )
    except Exception as exc:
        SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
        return _response(_message(f"❌ {label}提交失敗\n\n{exc}", [[_btn("返回新建推文", f"genpost_branch_{params.get('uiPersonaId') or params.get('archiveId')}")]]), state={"flow": ""})
    response = _response(
        _message(loading_text, [[_btn("刷新本次任務", f"source_task_detail:{source_task_id}")]]),
        state={"flow": "sentiment_hot_wait", "draft": {"source_task_id": source_task_id}},
    )
    response["poll"] = {"action": f"source_task_poll:{source_task_id}", "interval_ms": 2000}
    return response


def _sentiment_hot_fetch_start(persona_id: str, content_branch: str = "", *, refresh: bool = True) -> dict[str, Any]:
    persona_id, local, row, name = _genpost_context(persona_id)
    archive_id = _tool_r18_archive_id(persona_id, local, row)
    if not archive_id:
        return _response(_message("这个人设尚未连接 Tool R18 人设归档，不能抓取热点。", [[_btn("返回新建推文", f"genpost_branch_{persona_id}")]]), state={"flow": ""})
    params = {
        "action": "fetch",
        "archiveId": archive_id,
        "contentBranch": content_branch if content_branch in {"nonr18", "r18"} else "",
        "limit": 10,
        "refresh": refresh,
        "memorySummaries": _sentiment_hot_memory_summaries(row),
        "uiPersonaId": persona_id,
        "uiPersonaName": name,
    }
    return _sentiment_hot_submit(
        "persona_sentiment_hot",
        f"抓取人设热点：{name}",
        params,
        "正在抓取 Threads / Instagram 热点，请稍候...",
    )


def _genpost_hot_menu(action: str, state: dict[str, Any], *, global_context: bool = False) -> dict[str, Any]:
    parts = action.split(":")
    incoming_draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    persona_id = parts[1] if len(parts) > 1 else str(incoming_draft.get("persona_id") or "")
    name = str(incoming_draft.get("name") or persona_id or "人設")
    candidates = [item for item in incoming_draft.get("hot_candidates", []) if isinstance(item, dict)]
    action_key = _sentiment_action_key(persona_id, "source", incoming_draft)
    selected = {
        int(item)
        for item in incoming_draft.get("selected_hot_indexes", [])
        if str(item).isdigit() and 0 <= int(item) < len(candidates)
    }
    lines = [
        "🔥 热点抓取",
        "",
        f"人设: {name}",
        "来源: Threads + Instagram",
        f"关键词: {' / '.join(str(item) for item in incoming_draft.get('hot_keywords', []) if str(item).strip()) or '自动分析'}",
        "",
        "Cookie 状态:",
        *[str(item) for item in incoming_draft.get("hot_cookie_lines", []) if str(item).strip()],
        *(["", "提示:", *[f"- {item}" for item in incoming_draft.get("hot_warnings", []) if str(item).strip()]] if incoming_draft.get("hot_warnings") else []),
        "",
        f"已选: {len(selected)}/{len(candidates)} 篇，可多选后一次保存。" if candidates else "暂时没有抓到可用热点，请刷新或检查 Cookie。",
        "",
        "候选热点:" if candidates else "",
    ]
    for index, candidate in enumerate(candidates):
        marker = "☑️" if index in selected else "⬜️"
        list_text = str(candidate.get("listText") or "").strip()
        if list_text:
            lines.extend(list_text.replace(f"{index + 1}. ", f"{index + 1}. {marker} ", 1).splitlines())
        else:
            lines.extend(["────────────", f"{index + 1}. {marker} {_sentiment_hot_candidate_content(incoming_draft, candidate)[:72]}"])
    keyboard: list[list[dict[str, str]]] = []
    for index, _candidate in enumerate(candidates):
        keyboard.append(
            [
                _btn(("☑️ " if index in selected else "⬜️ ") + str(index + 1), f"shsel_{action_key}_{index}"),
                _btn(f"查看第 {index + 1} 篇", f"shdet_{action_key}_{index}"),
                _btn(f"使用第 {index + 1} 篇", f"shuse_{action_key}_{index}"),
            ]
        )
    if candidates:
        keyboard.extend(
            _rows(
                [_btn("全选", f"shselall_{action_key}"), _btn("清空选择", f"shselclear_{action_key}")],
                [_btn(f"保存已选 {len(selected)} 篇" if selected else "保存已选（先勾选）", f"shsave_{action_key}", "primary")],
            )
        )
    keyboard.extend(_rows([_btn("刷新抓取", f"shrf_{action_key}")], [_btn("返回新建推文", f"genpost_branch_{persona_id}")]))
    draft = {
        **incoming_draft,
        "sentiment_action_key": action_key,
        "persona_id": persona_id,
        "name": name,
        "hot_candidates": candidates,
        "selected_hot_indexes": sorted(selected),
        "content_branch": incoming_draft.get("content_branch") or "",
    }
    return _response(_message("\n".join([line for line in lines if line != ""]), keyboard), state={"flow": "sentiment_hot_select", "draft": draft})


def _sentiment_hot_state(state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], set[int]]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    candidates = [item for item in draft.get("hot_candidates", []) if isinstance(item, dict)]
    selected = {int(item) for item in draft.get("selected_hot_indexes", []) if str(item).isdigit() and 0 <= int(item) < len(candidates)}
    return draft, candidates, selected


def _sentiment_hot_list_from_state(state: dict[str, Any]) -> dict[str, Any]:
    draft, _candidates, _selected = _sentiment_hot_state(state)
    return _genpost_hot_menu("genpost_hot", {"draft": draft})


def _sentiment_hot_select_action(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft, candidates, selected = _sentiment_hot_state(state)
    key_match = re.match(r"^shsel(?:all|clear)?_([a-f0-9]+)(?:_\d+)?$", action)
    if not key_match or not _sentiment_hot_key_matches(draft, key_match.group(1)):
        return _sentiment_hot_expired(draft)
    if action.startswith("shselall_"):
        draft["selected_hot_indexes"] = list(range(len(candidates)))
        return _sentiment_hot_list_from_state({"draft": draft})
    if action.startswith("shselclear_"):
        draft["selected_hot_indexes"] = []
        return _sentiment_hot_list_from_state({"draft": draft})
    match = re.match(r"^shsel_([a-f0-9]+)_(\d+)$", action)
    if match:
        index = int(match.group(2))
        if index in selected:
            selected.remove(index)
        elif 0 <= index < len(candidates):
            selected.add(index)
        draft["selected_hot_indexes"] = sorted(selected)
    return _sentiment_hot_list_from_state({"draft": draft})


def _sentiment_hot_detail(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft, candidates, selected = _sentiment_hot_state(state)
    match = re.match(r"^shdet_([a-f0-9]+)_(\d+)$", action)
    if not match or not _sentiment_hot_key_matches(draft, match.group(1)):
        return _sentiment_hot_expired(draft)
    index = int(match.group(2))
    if not (0 <= index < len(candidates)):
        return _response(_message("热点候选参数无效，请重新刷新抓取。", [[_btn("返回新建推文", f"genpost_branch_{draft.get('persona_id')}")]]), state=state)
    candidate = candidates[index]
    lines = [str(candidate.get("detailText") or "热点候选详情")]
    edited_content = _sentiment_hot_candidate_content(draft, candidate)
    if edited_content and edited_content != str(candidate.get("content") or "").strip():
        lines.extend(["", "编辑后正文:", edited_content])
    key = str(draft.get("sentiment_action_key") or "00000000")
    keyboard = _rows(
        [_btn("☑️ 已加入多选" if index in selected else "⬜️ 加入多选", f"shsel_{key}_{index}")],
        [_btn(f"✅ 使用第 {index + 1} 篇", f"shuse_{key}_{index}")],
        [_btn("✏️ 编辑后使用", f"shedit_{key}_{index}")],
        [_btn("全选", f"shselall_{key}"), _btn("清空选择", f"shselclear_{key}")],
        [_btn(f"保存已选 {len(selected)} 篇" if selected else "保存已选（先勾选）", f"shsave_{key}")],
        [_btn("返回候选列表", f"shlist_{key}")],
        [_btn("刷新抓取", f"shrf_{key}")],
        [_btn("返回新建推文", f"genpost_branch_{draft.get('persona_id')}")],
    )
    preview, cards = _sentiment_hot_media_cards(candidate)
    return _response(_message("\n".join(lines), keyboard, image=preview, cards=cards), state={"flow": "sentiment_hot_select", "draft": draft})


def _candidate_media_items(candidate: dict[str, Any]) -> list[Any]:
    for key in ("media", "mediaItems", "media_items", "images", "videos"):
        value = candidate.get(key)
        if isinstance(value, list):
            return list(value)
    return []


def _set_candidate_media_items(candidate: dict[str, Any], items: list[Any]) -> dict[str, Any]:
    next_candidate = dict(candidate)
    changed = False
    for key in ("media", "mediaItems", "media_items", "images", "videos"):
        if isinstance(next_candidate.get(key), list):
            next_candidate[key] = list(items)
            changed = True
    if not changed:
        next_candidate["media"] = list(items)
    return next_candidate


def _sentiment_hot_media_edit(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft, candidates, _selected = _sentiment_hot_state(state)
    edit_match = re.match(r"^shedit_([a-f0-9]+)_(\d+)$", action)
    if edit_match:
        key, index_text = edit_match.groups()
        if not _sentiment_hot_key_matches(draft, key):
            return _sentiment_hot_expired(draft)
        index = int(index_text)
        if not (0 <= index < len(candidates)):
            return _sentiment_hot_expired(draft)
        draft["hot_edit_index"] = index
        draft["hot_edit_action_key"] = key
        candidate_id = str(candidates[index].get("id") or index)
        deleted_map = dict(draft.get("hot_deleted_media_indexes") if isinstance(draft.get("hot_deleted_media_indexes"), dict) else {})
        deleted_map[candidate_id] = []
        draft["hot_deleted_media_indexes"] = deleted_map
        return _sentiment_hot_media_edit("shmedia_start", {"draft": draft})
    index = _num(draft.get("hot_edit_index"))
    key = str(draft.get("hot_edit_action_key") or "")
    if not _sentiment_hot_key_matches(draft, key) or not (0 <= index < len(candidates)):
        return _sentiment_hot_expired(draft)
    media_index_text = ""
    op = "start"
    toggle_match = re.match(r"^shmedia_toggle_(\d+)$", action)
    if toggle_match:
        op, media_index_text = "toggle", toggle_match.group(1)
    elif action == "shmedia_select_all":
        op = "select_all"
    elif action == "shmedia_clear":
        op = "clear"
    elif action == "shmedia_save":
        op = "save"
    elif action != "shmedia_start":
        return _sentiment_hot_expired(draft)
    if not (0 <= index < len(candidates)):
        return _sentiment_hot_list_from_state(state)
    candidate = candidates[index]
    media_items = _candidate_media_items(candidate)
    candidate_id = str(candidate.get("id") or index)
    deleted_map = dict(draft.get("hot_deleted_media_indexes") if isinstance(draft.get("hot_deleted_media_indexes"), dict) else {})
    selected = {int(item) for item in deleted_map.get(candidate_id, []) if str(item).isdigit() and 0 <= int(item) < len(media_items)}
    if op == "toggle" and media_index_text:
        media_index = int(media_index_text)
        if media_index in selected:
            selected.remove(media_index)
        elif 0 <= media_index < len(media_items):
            selected.add(media_index)
    elif op == "select_all":
        selected = set(range(len(media_items)))
    elif op == "clear":
        selected = set()
    elif op == "save":
        return _sentiment_hot_import(f"shuse_{key}_{index}", {"draft": draft})
    deleted_map[candidate_id] = sorted(selected)
    draft["hot_deleted_media_indexes"] = deleted_map
    kept_count = max(0, len(media_items) - len(selected))
    lines = [
        "✏️ 编辑热点推文",
        "",
        f"人设: {draft.get('name') or draft.get('persona_id')}",
        f"数据: {candidate.get('metricLine') or '-'}",
        f"媒体: {len(media_items)} 个，已选删除 {len(selected)} 个，保存后保留 {kept_count} 个",
        "",
        "图片已按媒体编号排版；下方按钮可多选删除，红色标记表示保存时会删除。",
        "可以直接发送新文案；也可以发送图片/视频+文案来整体替换媒体。",
    ]
    if not media_items:
        lines.extend(["", "这篇候选没有可管理的媒体，仍可直接使用文字内容。"])
    else:
        for media_index, item in enumerate(media_items):
            mark = "☑️" if media_index in selected else "⬜️"
            preview = item if isinstance(item, str) else str(item.get("url") or item.get("type") or "媒体")[:80]
            lines.append(f"{media_index + 1}. {mark} {preview}")
    keyboard: list[list[dict[str, str]]] = []
    for media_index, _item in enumerate(media_items):
        media_type = "视频" if isinstance(_item, dict) and str(_item.get("type") or "") == "video" else "图片"
        button = _btn(f"{'☑️' if media_index in selected else '⬜️'} {media_index + 1}.{media_type}", f"shmedia_toggle_{media_index}")
        if media_index % 2 == 0:
            keyboard.append([button])
        else:
            keyboard[-1].append(button)
    keyboard.extend(
        _rows(
            [_btn("全选删除", "shmedia_select_all"), _btn("清空选择", "shmedia_clear")],
            [_btn(f"保存并使用（删除 {len(selected)} 个媒体）", "shmedia_save")],
            [_btn("返回候选详情", f"shdet_{key}_{index}")],
        )
    )
    preview, cards = _sentiment_hot_media_cards(candidate)
    return _response(_message("\n".join(lines), keyboard, image=preview, cards=cards), state={"flow": "sentiment_hot_edit_input", "draft": draft})


def _sentiment_candidate_to_post(candidate: dict[str, Any]) -> str:
    return to_traditional(str(candidate.get("content") or "").strip())


def _sentiment_hot_import(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft, candidates, selected = _sentiment_hot_state(state)
    match = re.match(r"^shuse_([a-f0-9]+)_(\d+)$", action)
    if match:
        if not _sentiment_hot_key_matches(draft, match.group(1)):
            return _sentiment_hot_expired(draft)
        selected = {int(match.group(2))}
    else:
        save_match = re.match(r"^shsave_([a-f0-9]+)$", action)
        if not save_match or not _sentiment_hot_key_matches(draft, save_match.group(1)):
            return _sentiment_hot_expired(draft)
    if not selected:
        return _response(_message("请先勾选要保存的热点推文。", [[_btn("返回候选列表", f"shlist_{draft.get('sentiment_action_key') or '00000000'}")]]), state=state)
    imported = [candidates[index] for index in sorted(selected) if 0 <= index < len(candidates)]
    persona_id = str(draft.get("persona_id") or "")
    name = str(draft.get("name") or persona_id or "人設")
    fetch_task_id = str(draft.get("fetch_task_id") or "")
    if not fetch_task_id:
        return _sentiment_hot_expired(draft)
    edited_contents = draft.get("hot_edited_contents") if isinstance(draft.get("hot_edited_contents"), dict) else {}
    deleted_map = draft.get("hot_deleted_media_indexes") if isinstance(draft.get("hot_deleted_media_indexes"), dict) else {}
    replacement_map = draft.get("hot_replacement_media") if isinstance(draft.get("hot_replacement_media"), dict) else {}
    edits: list[dict[str, Any]] = []
    for source_index in sorted(selected):
        if not (0 <= source_index < len(candidates)):
            continue
        candidate = candidates[source_index]
        candidate_id = str(candidate.get("id") or "")
        edit: dict[str, Any] = {"candidateId": candidate_id}
        if str(edited_contents.get(candidate_id) or "").strip():
            edit["content"] = str(edited_contents[candidate_id]).strip()
        if candidate_id in deleted_map:
            deleted = {int(item) for item in deleted_map.get(candidate_id, []) if str(item).isdigit()}
            edit["keptMediaIndexes"] = [index for index in range(len(_sentiment_hot_media(candidate))) if index not in deleted]
        replacement = replacement_map.get(candidate_id)
        if isinstance(replacement, dict) and str(replacement.get("url") or "").strip():
            edit["replacementMedia"] = {
                "url": str(replacement.get("url") or "").strip(),
                "type": str(replacement.get("type") or "unknown").strip().lower(),
                "name": str(replacement.get("name") or "").strip()[:160],
            }
            edit.pop("keptMediaIndexes", None)
        if len(edit) > 1:
            edits.append(edit)
    params = {
        "action": "import",
        "archiveId": str(draft.get("source_archive_id") or ""),
        "fetchTaskId": fetch_task_id,
        "candidateIds": [str(item.get("id") or "") for item in imported if str(item.get("id") or "")],
        "edits": edits,
        "contentBranch": str(draft.get("content_branch") or ""),
        "uiPersonaId": persona_id,
        "uiPersonaName": name,
    }
    return _sentiment_hot_submit(
        "persona_sentiment_hot",
        f"导入人设热点：{name}",
        params,
        f"正在导入 {len(imported)} 篇热点推文并下载媒体，请稍候...",
    )


def _dt(ts: Any) -> str:
    try:
        if not ts:
            return "-"
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except Exception:
        return "-"


def _btn(label: str, action: str, style: str = "tg") -> dict[str, str]:
    return {"label": str(label), "action": action, "style": style}


def _rows(*rows: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    return [row for row in rows if row]


def _chunk_buttons(buttons: list[dict[str, str]], size: int = 2) -> list[list[dict[str, str]]]:
    return [buttons[index : index + size] for index in range(0, len(buttons), size)]


def _message(
    text: str,
    keyboard: list[list[dict[str, str]]] | None = None,
    *,
    cards: list[dict[str, Any]] | None = None,
    image: str = "",
    kind: str = "normal",
) -> dict[str, Any]:
    keyboard = keyboard or []
    actions = [button for row in keyboard for button in row]
    payload: dict[str, Any] = {
        "role": "bot",
        "text": str(text),
        "keyboard": keyboard,
        "actions": actions,
        "cards": _traditionalize_cards(cards or []),
        "kind": kind,
    }
    if image:
        payload["image"] = image
    return payload


def _traditionalize_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def convert(value: Any) -> Any:
        if isinstance(value, str):
            return to_traditional(value)
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        return value

    return [convert(card) for card in cards]


def _response(
    messages: list[dict[str, Any]] | dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
    open_url: str = "",
) -> dict[str, Any]:
    if isinstance(messages, dict):
        messages = [messages]
    out = {"messages": messages, "state": state or {"flow": ""}}
    if open_url:
        out["open"] = open_url
    return out


def _open(path: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    return _response(
        _message("正在打开工作台页面...", [[_btn("返回主选单", "menu")]], kind="status"),
        state=state or {"flow": ""},
        open_url=path,
    )


def _main_keyboard() -> list[list[dict[str, str]]]:
    return _rows(
        [_btn("👤 我的人設", "list_personas"), _btn("📊 排程狀態", "menu_status")],
        [_btn("⏰ 定時任務", "schedule_publish"), _btn("📱 智能體手機管理", "pad_mgmt")],
        [_btn("🛑 強制中止目前任務", "force_stop_current_task", "danger")],
    )


def _main_menu() -> dict[str, Any]:
    devices = _active_devices()
    default_pad_code = devices[0].pad_code if devices else "未設定"
    text = "\n".join(
        [
            "🤖 自動化推文營運控制台",
            "",
            f"預設智能體手機：{default_pad_code}",
            "預設平台：Threads",
            "",
            "你可以直接發送自然語言指令，也可以點擊按鈕操作：",
        ]
    )
    return _response(_message(text, _main_keyboard()), state={"flow": ""})


def _persona_row_name(row: dict[str, Any]) -> str:
    raw_name = str(row.get("name") or row.get("id") or "未命名人设")
    pad_name, pad_code = _persona_bound_info(row)
    name = _clean_persona_name(raw_name, pad_code)
    if name in _PLACEHOLDER_PERSONA_NAMES and pad_code:
        return _fallback_persona_name(pad_name, pad_code)
    return name


_PAD_CODE_RE = re.compile(r"\b[A-Z]{2,4}\d[A-Z0-9]{6,}\b")
_PLACEHOLDER_PERSONA_NAMES = {"人设", "人設", "未命名人设", "未命名人設", "imported persona"}
_BAD_PERSONA_NAME_TOKENS = ("??", "callback")


def _clean_persona_name(name: str, pad_code: str = "") -> str:
    text = str(name or "").strip()
    if pad_code:
        text = text.replace(pad_code, "")
    text = _PAD_CODE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" ·-_/|:：→")
    return text or "人设"


def _is_placeholder_persona_name(name: str, pad_code: str = "") -> bool:
    clean = _clean_persona_name(name, pad_code)
    lowered = clean.lower()
    return clean in _PLACEHOLDER_PERSONA_NAMES or any(token in lowered for token in _BAD_PERSONA_NAME_TOKENS)


def _persona_name_is_device_alias(name: str, pad_code: str = "") -> bool:
    clean = _clean_persona_name(name, pad_code).lower()
    if not clean:
        return True
    aliases = {
        _clean_persona_name(device.alias, device.pad_code).lower()
        for device in DeviceRepo.list_all()
        if device.alias
    }
    aliases.discard("")
    return clean.startswith("op-test") or clean in aliases


def _persona_name_is_workflow_fallback(name: str, pad_code: str = "") -> bool:
    clean = _clean_persona_name(name, pad_code).lower()
    return clean.endswith("工作流人设") or clean.endswith("工作流人設")


def _persona_has_real_name(persona: Persona | None) -> bool:
    if not persona:
        return False
    return (
        not _is_placeholder_persona_name(persona.name, persona.pad_code)
        and not _persona_name_is_device_alias(persona.name, persona.pad_code)
        and not _persona_name_is_workflow_fallback(persona.name, persona.pad_code)
    )


def _fallback_persona_name(pad_name: str = "", pad_code: str = "") -> str:
    clean_pad_name = _clean_persona_name(pad_name, pad_code) if pad_name else ""
    if clean_pad_name and clean_pad_name not in _PLACEHOLDER_PERSONA_NAMES:
        return f"{clean_pad_name} 工作流人设"
    if pad_code:
        return f"{pad_code} 工作流人设"
    return "未命名工作流人设"


def _usable_row_persona_name(row: dict[str, Any] | None, pad_name: str = "", pad_code: str = "") -> str:
    row = row or {}
    name = _clean_persona_name(str(row.get("name") or ""), pad_code)
    if name and not _is_placeholder_persona_name(name, pad_code) and not _persona_name_is_device_alias(name, pad_code):
        return name
    return _fallback_persona_name(pad_name or str(row.get("bound_pad_name") or ""), pad_code)


def _local_persona_display_name(persona: Persona) -> str:
    name = _clean_persona_name(persona.name, persona.pad_code)
    if name in _PLACEHOLDER_PERSONA_NAMES and persona.pad_code:
        device = DeviceRepo.get(persona.pad_code)
        pad_name = (device.alias if device else "") or ""
        return _fallback_persona_name(pad_name, persona.pad_code)
    return name


def _local_persona_row(persona: Persona) -> dict[str, Any]:
    device = DeviceRepo.get(persona.pad_code) if persona.pad_code else None
    return {
        "id": persona.id,
        "name": _local_persona_display_name(persona),
        "description": persona.description,
        "content": persona.description,
        "style_prompt": persona.style_prompt,
        "bound_pad_code": persona.pad_code,
        "bound_pad_name": (device.alias if device else ""),
        "account_username": persona.account_username,
        "counts": {"posts": 0, "published": 0},
    }


def _local_persona_rows() -> list[dict[str, Any]]:
    return [_local_persona_row(persona) for persona in PersonaRepo.list_all(limit=500)]


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _cached_remote_persona_rows() -> list[dict[str, Any]]:
    for attr in ("REMOTE_CACHE", "REMOTE_SAMPLE"):
        path = getattr(persona_dashboard_module, attr, None)
        if not isinstance(path, Path) or not path.exists():
            continue
        data = _read_json_file(path)
        rows = data.get("personas") if isinstance(data.get("personas"), list) else []
        clean_rows = [row for row in rows if isinstance(row, dict)]
        if data.get("ok") and clean_rows:
            return clean_rows
    return []


def _merge_source_and_local_rows(source_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(source_rows)
    seen = {str(row.get("id") or "").strip() for row in rows if str(row.get("id") or "").strip()}
    for row in _local_persona_rows():
        rid = str(row.get("id") or "").strip()
        if rid and rid not in seen:
            rows.append(row)
            seen.add(rid)
    return rows


def _refresh_persona_overview_cache(*, force_remote: bool = False) -> None:
    if not _PERSONA_OVERVIEW_REFRESH_LOCK.acquire(blocking=False):
        return
    try:
        build_overview(force_remote=force_remote)
        source_rows = _cached_remote_persona_rows()
        rows = _merge_source_and_local_rows(source_rows) if source_rows else _local_persona_rows()
        if rows:
            _PERSONA_MENU_CACHE.update({"at": time.time(), "rows": rows})
    except Exception:
        pass
    finally:
        _PERSONA_OVERVIEW_REFRESH_LOCK.release()


def _schedule_persona_overview_refresh() -> None:
    if _PERSONA_OVERVIEW_REFRESH_LOCK.locked():
        return
    thread = threading.Thread(target=_refresh_persona_overview_cache, name="persona-overview-refresh", daemon=True)
    thread.start()


def _persona_menu_rows() -> list[dict[str, Any]]:
    now = time.time()
    cached_rows = _PERSONA_MENU_CACHE.get("rows")
    if isinstance(cached_rows, list) and cached_rows and now - float(_PERSONA_MENU_CACHE.get("at") or 0) < PERSONA_MENU_CACHE_TTL_SECONDS:
        return cached_rows
    source_rows = _cached_remote_persona_rows()
    source_cache_has_pending_schema = bool(source_rows) and any("pending_posts" in row for row in source_rows)
    if not source_rows or not source_cache_has_pending_schema:
        try:
            overview = build_overview(force_remote=True)
            source_rows = [row for row in overview.get("personas", []) if isinstance(row, dict)]
        except Exception:
            source_rows = []
    rows = _merge_source_and_local_rows(source_rows) if source_rows else _local_persona_rows()
    _PERSONA_MENU_CACHE.update({"at": now, "rows": rows})
    _schedule_persona_overview_refresh()
    return rows


def _source_row_local_id(row: dict[str, Any], pad_code: str = "") -> str:
    row_id = str(row.get("id") or "").strip()
    if row_id:
        return row_id
    if pad_code:
        suffix = "".join(ch.lower() for ch in pad_code if ch.isalnum())[-8:] or hashlib.sha1(pad_code.encode()).hexdigest()[:8]
        return f"persona_{suffix}"
    return _stable_id("persona", row.get("name"), row.get("content"), row.get("account_username"))


def _account_pad_for_persona(persona: Persona | None) -> str:
    if not persona or not persona.account_username:
        return ""
    account = AccountRepo.get(persona.account_username)
    return str(account.pad_code or "").strip() if account else ""


def _align_persona_to_account_pad(persona: Persona | None) -> Persona | None:
    if not persona:
        return None
    account_pad = _account_pad_for_persona(persona)
    if account_pad and account_pad != persona.pad_code:
        PersonaRepo.upsert(_persona_payload(persona, pad_code=account_pad))
        persona = PersonaRepo.get(persona.id) or persona
    if persona.account_username and _persona_has_real_name(persona):
        AccountRepo.upsert_many([(persona.account_username, persona.name, persona.name)])
        if account_pad or persona.pad_code:
            AccountRepo.assign_pad(persona.account_username, account_pad or persona.pad_code)
    return persona


def _find_related_real_persona(row: dict[str, Any] | None, local: Persona | None = None) -> Persona | None:
    row = row or {}
    row_account = str(row.get("account_username") or "").strip()
    pad_name, pad_code = _persona_bound_info(row, local)
    candidates: list[Persona] = []
    for candidate in PersonaRepo.list_all(limit=10000):
        if local and candidate.id == local.id:
            continue
        candidate_account_pad = _account_pad_for_persona(candidate)
        same_account = bool(row_account and candidate.account_username == row_account)
        same_pad = bool(pad_code and (candidate.pad_code == pad_code or candidate_account_pad == pad_code))
        if not (same_account or same_pad):
            continue
        if not _persona_has_real_name(candidate):
            continue
        candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            0 if str(item.source_archive_id or "").startswith("device:") else 1,
            item.updated_at or 0,
        ),
        reverse=True,
    )
    return _align_persona_to_account_pad(candidates[0])


def _ensure_local_persona_from_row(row: dict[str, Any] | None, local: Persona | None = None) -> Persona | None:
    if not row:
        return _align_persona_to_account_pad(local)
    local = _align_persona_to_account_pad(local)
    pad_name, pad_code = _persona_bound_info(row, local)
    row_name = _usable_row_persona_name(row, pad_name, pad_code)
    row_content = str(row.get("content") or "").strip()
    row_account = str(row.get("account_username") or "").strip()
    persona_id = local.id if local else _source_row_local_id(row, pad_code)

    if local:
        name = _clean_persona_name(local.name, pad_code)
        should_use_row_name = (
            _is_placeholder_persona_name(local.name, pad_code)
            or _persona_name_is_device_alias(local.name, pad_code)
            or str(local.source_archive_id or "").startswith("device:")
        )
        payload = _persona_payload(
            local,
            name=row_name if should_use_row_name else name,
            description=row_content or local.description,
            pad_code=pad_code or local.pad_code,
            account_username=row_account or local.account_username,
            source_archive_id=local.source_archive_id or f"source:{row.get('id') or pad_code}",
        )
    else:
        description = row_content or (f"来源人设同步生成，PAD_CODE：{pad_code}" if pad_code else "来源人设同步生成")
        payload = {
            "id": persona_id,
            "name": row_name,
            "description": description,
            "style_prompt": "",
            "avatar_path": "",
            "account_username": row_account,
            "pad_code": pad_code,
            "source_archive_id": f"source:{row.get('id') or pad_code or row_name}",
        }
    PersonaRepo.upsert(payload)
    updated = PersonaRepo.get(persona_id)
    if updated and updated.account_username and _persona_has_real_name(updated):
        AccountRepo.upsert_many([(updated.account_username, updated.name, updated.name)])
        if updated.pad_code:
            AccountRepo.assign_pad(updated.account_username, updated.pad_code)
    return updated


def _persona_bound_info(row: dict[str, Any] | None, local: Persona | None = None) -> tuple[str, str]:
    row = row or {}
    account_pad = _account_pad_for_persona(local)
    pad_code = (
        account_pad
        or (local.pad_code if local else "")
        or str(row.get("bound_pad_code") or row.get("pad_code") or "")
    ).strip()
    if not pad_code:
        return "", ""
    pad_name = str(row.get("bound_pad_name") or "").strip()
    device = DeviceRepo.get(pad_code)
    if not pad_name or pad_name == pad_code:
        pad_name = (device.alias if device else "") or pad_code
    return pad_name, pad_code


def _persona_list_button_label(row: dict[str, Any]) -> str:
    name = _persona_row_name(row)
    pad_name, pad_code = _persona_bound_info(row)
    if pad_code:
        return f"✅ {name} · {pad_name} / {pad_code}"
    return f"⚠️ 未绑定云机 / {name}"


def _persona_action_label(row: dict[str, Any]) -> str:
    name = _persona_row_name(row)
    counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
    post_count = _num(counts.get("posts") or row.get("postCount") or row.get("post_count"))
    marker = "⭐ " if row.get("imageWorkflow") or row.get("image_workflow") or row.get("workflow") else ""
    suffix = " · 工作流人設" if marker else ""
    return f"{marker}{name} ({post_count}篇){suffix}"


def _persona_hot_score(row: dict[str, Any]) -> float:
    hot = row.get("hot") if isinstance(row.get("hot"), dict) else {}
    try:
        return float(hot.get("hot_score") or 0)
    except Exception:
        return 0.0


def _persona_sort_key(row: dict[str, Any]) -> tuple[float, str]:
    return (-_persona_hot_score(row), _persona_row_name(row).lower())


def _persona_dashboard_url(persona_id: str) -> str:
    return "/persona-dashboard?persona=" + urllib.parse.quote(str(persona_id or ""), safe="")


def _persona_hot_text(row: dict[str, Any]) -> str:
    hot = row.get("hot") if isinstance(row.get("hot"), dict) else {}
    return "\n".join(
        [
            f"{_persona_row_name(row)} 的热点数据",
            f"热度：{_compact(hot.get('hot_score'))}",
            f"逐帖浏览：{_compact(hot.get('post_views'))}；主页浏览：{_compact(hot.get('recent_views'))}",
            (
                "互动：赞 "
                f"{_compact(hot.get('likes'))} / 评 {_compact(hot.get('comments'))} / "
                f"分享 {_compact(hot.get('shares'))} / 转发 {_compact(hot.get('reposts'))}"
            ),
            "公式：热度 = 逐帖浏览 + 点赞 + 评论 + 分享 + 转发；不包含账号主页浏览。",
        ]
    )


def _personas_menu(page: int = 0, bind_filter: str = "all") -> dict[str, Any]:
    personas = _persona_menu_rows()
    page_action = "list_personas"
    page_size = 8
    total_pages = max(1, (len(personas) + page_size - 1) // page_size)
    safe_page = max(0, min(page, total_pages - 1))
    visible = personas[safe_page * page_size : (safe_page + 1) * page_size]
    has_workflow = any(row.get("imageWorkflow") or row.get("image_workflow") or row.get("workflow") for row in personas)
    lines = ["📋 我的人設"]
    if has_workflow:
        lines.extend(["", "⭐ 工作流人設"])
    if total_pages > 1:
        lines.extend(["", f"第 {safe_page + 1}/{total_pages} 頁"])
    if not personas:
        lines = ["暫無人設。", "", "發送類似「建立一个理财专家人設」來建立。"]

    keyboard: list[list[dict[str, str]]] = []
    keyboard.append([_btn("🚀 矩陣發布", "matrix_start")])
    keyboard.append([_btn("➕ 新建人設", "create_persona_entry")])
    for row in visible:
        persona_id = str(row.get("id") or row.get("name") or "").strip()
        if not persona_id:
            continue
        keyboard.append([_btn(_persona_action_label(row), f"pd_{persona_id}")])
    if total_pages > 1:
        if safe_page > 0:
            keyboard.append(
                [
                    _btn("⏮ 首頁", page_action),
                    _btn("◀️ 上一頁", f"{page_action}_p{safe_page - 1}" if safe_page - 1 > 0 else page_action),
                ]
            )
        keyboard.append([_btn(f"{safe_page + 1}/{total_pages}", f"{page_action}_p{safe_page}" if safe_page > 0 else page_action)])
        if safe_page < total_pages - 1:
            keyboard.append(
                [
                    _btn("下一頁 ▶️", f"{page_action}_p{safe_page + 1}"),
                    _btn("尾頁 ⏭", f"{page_action}_p{total_pages - 1}"),
                ]
            )
    if not personas:
        keyboard = [[_btn("➕ 新建人設", "create_persona_entry")]]
    return _response(_message("\n".join(lines), keyboard), state={"flow": ""})


def _find_persona_any(persona_id: str) -> tuple[Persona | None, dict[str, Any] | None]:
    local = PersonaRepo.get(persona_id)
    for row in _persona_menu_rows():
        row_id = str(row.get("id") or "").strip()
        if row_id == persona_id:
            return local, row
    if local:
        return local, _local_persona_row(local)
    overview = build_overview()
    row = find_persona(overview, persona_id)
    return local, row


def _resolve_persona_for_action(persona_id: str) -> tuple[Persona | None, dict[str, Any] | None]:
    local, row = _find_persona_any(persona_id)
    local = _align_persona_to_account_pad(local)
    if row:
        related = _find_related_real_persona(row, local)
        if related and (not _persona_has_real_name(local) or str(local.source_archive_id or "").startswith("device:")):
            return related, row
        local = _ensure_local_persona_from_row(row, local)
    return local, row


def _persona_detail(persona_id: str) -> dict[str, Any]:
    local, row = _resolve_persona_for_action(persona_id)
    if not local and not row:
        return _response(
            _message("沒有找到這個人設。", [[_btn("◀️ 返回人設列表", "list_personas")]]),
            state={"flow": ""},
        )
    persona_id = _tool_r18_archive_id(persona_id, local, row) or (local.id if local else persona_id)
    pad_name, pad_code = _persona_bound_info(row, local)
    if local:
        name = _clean_persona_name(local.name, pad_code)
        if name in _PLACEHOLDER_PERSONA_NAMES:
            name = _persona_row_name(row) if row else _fallback_persona_name(pad_name, pad_code)
        content = str(local.description or "").strip()
        style = str(local.style_prompt or "").strip()
    else:
        name = _persona_row_name(row or {})
        content = str((row or {}).get("description") or (row or {}).get("content") or "").strip()
        style = str((row or {}).get("style_prompt") or "").strip()
    counts = (row or {}).get("counts") if isinstance((row or {}).get("counts"), dict) else {}
    pending_count = _num(counts.get("posts") or (row or {}).get("postCount") or (row or {}).get("post_count"))
    published_count = _num(counts.get("published") or (row or {}).get("publishedCount") or (row or {}).get("published_count"))
    setup = (row or {}).get("setup") if isinstance((row or {}).get("setup"), dict) else {}
    genres = setup.get("genres") if isinstance(setup.get("genres"), list) else []
    persona_type = ", ".join(str(item) for item in genres if str(item).strip()) or "-"
    personality = str(setup.get("personaPersonality") or setup.get("personality") or style or "-").strip() or "-"
    interests = setup.get("interests") if isinstance(setup.get("interests"), list) else []
    interest_line = ""
    if interests:
        interest_line = "興趣標籤: " + "、".join(str(item) for item in interests if str(item).strip()) + "\n"
    bound = pad_code or "未綁定"
    if pad_code and pad_name:
        bound = f"{pad_code} ({pad_name})"
    intro = content[:200] if content else "-"
    lines = [
        f"👤 {name}",
        "",
        f"类型: {persona_type}",
        f"性格: {personality}",
        f"待发布推文: {pending_count} 篇",
        f"已发布: {published_count} 篇",
        f"绑定智能體手機: {bound}",
        "",
        f"{interest_line}{intro}",
    ]
    workflow_persona = _is_workflow_persona_row(row, persona_id)
    keyboard = _rows(
        [
            _btn("📝 查看推文", f"posts_branch_{persona_id}" if workflow_persona else f"posts_{persona_id}_p0"),
            _btn("🕘 发布历史", f"history_branch_{persona_id}" if workflow_persona else f"history_{persona_id}"),
        ],
        [_btn("✍️ 新建推文", f"genpost_branch_{persona_id}"), _btn("⚙️ 人设设置", f"settings_{persona_id}")],
        [_btn("💬 自動回覆", f"persona_autoreply_{persona_id}"), _btn("🌱 養號", f"persona_warmup_{persona_id}")],
        [_btn("🚀 发布推文", f"pub_branch_{persona_id}" if workflow_persona else f"pub_{persona_id}")],
        [_btn("◀️ 返回", "list_personas")],
    )
    return _response(
        _message("\n".join(lines), keyboard),
        state={"flow": "persona_detail", "draft": {"persona_id": persona_id, "name": name}},
    )


def _persona_settings(persona_id: str) -> dict[str, Any]:
    local, row = _resolve_persona_for_action(persona_id)
    if not local and not row:
        return _response(
            _message("没有找到这个人设。", [[_btn("◀️ 返回人设列表", "list_personas")]]),
            state={"flow": ""},
        )
    if local:
        persona_id = local.id
    if not ((local and _avatar_exists(local)) or _persona_reference_image_url(row)):
        row = _fresh_persona_row(persona_id, local, row)
    pad_name, pad_code = _persona_bound_info(row, local)
    if local:
        name = _clean_persona_name(local.name, pad_code)
        if name in _PLACEHOLDER_PERSONA_NAMES:
            name = _persona_row_name(row) if row else _fallback_persona_name(pad_name, pad_code)
    else:
        name = _persona_row_name(row or {})
    bound_name = pad_name or "未绑定"
    account = (
        (local.account_username if local else "")
        or str((row or {}).get("account_username") or "")
        or "未设置"
    )
    counts = (row or {}).get("counts") if isinstance((row or {}).get("counts"), dict) else {}
    lines = [
        "⚙️ 人設設定",
        "",
        f"人设：{name}",
        f"绑定智能体手机：{bound_name}",
        f"TG 通用群：{(local.tg_free_group_name or local.tg_free_group_id) if local else '未绑定'}" if local else "TG 通用群：未绑定",
        f"账号管理：Threads：{account}；Telegram：未设置",
        f"Threads：{_persona_hot_status(row)}",
        f"待发布推文：{_num(counts.get('posts'))} 篇",
        f"已发布：{_num(counts.get('published'))} 篇",
    ]
    if pad_code:
        lines[3] = f"云机名称：{bound_name}"
        lines.insert(4, f"PAD_CODE：{pad_code}")
    else:
        lines[3] = "绑定状态：未绑定云机"
    is_workflow = bool(row and (str((row or {}).get("id") or "").startswith("workflow-persona-") or (row or {}).get("imageWorkflow") or (row or {}).get("workflow")))
    buttons = [
        _btn("✏️ 改名稱", f"editname_{persona_id}"),
        _btn("🧾 推文風格", f"tweetstyle_{persona_id}"),
        _btn("🧾 人設簡介", f"editcontent_{persona_id}"),
        _btn("🔗 链接设置", f"linksettings_{persona_id}"),
        _btn("📱 綁定智能體手機", f"bindpad_{persona_id}"),
        _btn("🔐 帳號管理", f"acctmgmt_{persona_id}"),
        _btn("🔥 人設熱點數據", f"shs_{persona_id}"),
    ]
    if is_workflow:
        buttons.extend([_btn("TG免費群", f"bindtg_free_{persona_id}"), _btn("TG付費群", f"bindtg_paid_{persona_id}")])
    else:
        buttons.append(_btn("TG通用群", f"bindtg_free_{persona_id}"))
        if (local and _avatar_exists(local)) or _persona_reference_image_url(row):
            buttons.extend([_btn("👁 查看人设图", f"viewimg_{persona_id}"), _btn("🔄 重新生成人设图", f"regenimg_{persona_id}")])
        else:
            buttons.append(_btn("🎨 生成人设图", f"genimg_{persona_id}"))
        buttons.append(_btn("🗑 删除人设", f"del_{persona_id}", "danger"))
    buttons.append(_btn("◀️ 返回人設詳情", f"pd_{persona_id}"))
    keyboard = _chunk_buttons(buttons, 2)
    return _response(
        _message("\n".join(lines), keyboard),
        state={"flow": "persona_detail", "draft": {"persona_id": persona_id, "name": name}},
    )


def _persona_hot_status(row: dict[str, Any] | None) -> str:
    hot = row.get("hot") if isinstance(row, dict) and isinstance(row.get("hot"), dict) else {}
    if not hot:
        return "尚未刷新"
    if _num(hot.get("hot_score")) or _num(hot.get("post_views")):
        return f"已刷新，热度 {_compact(hot.get('hot_score'))}"
    return "尚未刷新"


def _setting_cooldown_key(flow: str) -> str:
    if flow == "edit_persona_name":
        return "name"
    if flow in {"edit_persona_desc", "edit_persona_desc_regen"}:
        return "description"
    return ""


def _format_cooldown_remaining(seconds: float) -> str:
    remaining = max(0, int(seconds))
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    if days:
        return f"{days} 天 {hours} 小時"
    minutes = max(1, (remaining % 3600) // 60)
    return f"{hours} 小時 {minutes} 分鐘" if hours else f"{minutes} 分鐘"


def _persona_setting_cooldown_remaining(persona_id: str, key: str) -> tuple[float, PostMemory | None]:
    if not persona_id or not key:
        return 0, None
    for memory in PostMemoryRepo.list_for_persona(persona_id, limit=200):
        if memory.source_type != "persona_setting_update":
            continue
        if str(memory.payload.get("setting_key") or "") != key:
            continue
        elapsed = time.time() - float(memory.created_at or memory.updated_at or 0)
        remaining = PERSONA_SETTING_COOLDOWN_SECONDS - elapsed
        return max(0, remaining), memory
    return 0, None


def _record_persona_setting_update(persona_id: str, key: str, content: str) -> None:
    if not persona_id or not key:
        return
    PostMemoryRepo.create(
        persona_id,
        content[:1600],
        granularity="persona",
        source_type="persona_setting_update",
        title=f"人設設定更新：{key}",
        payload={"setting_key": key, "cooldown_days": PERSONA_SETTING_COOLDOWN_DAYS},
    )


def _persona_settings_flow(persona_id: str, flow: str, prompt: str) -> dict[str, Any]:
    local = PersonaRepo.get(persona_id)
    if not local:
        return _response(_message("只能编辑本地人设，请先同步或新建。", [[_btn("◀️ 返回", "list_personas")]]))
    key = _setting_cooldown_key(flow)
    remaining, last = _persona_setting_cooldown_remaining(persona_id, key)
    if key and remaining > 0:
        last_time = datetime.fromtimestamp(float(last.created_at if last else time.time())).strftime("%Y-%m-%d %H:%M")
        return _response(
            _message(
                "\n".join(
                    [
                        "⏳ 這個欄位仍在 60 天冷卻期內。",
                        "",
                        f"人設：{local.name}",
                        f"欄位：{'名稱' if key == 'name' else '人設簡介'}",
                        f"上次修改：{last_time}",
                        f"剩餘：約 {_format_cooldown_remaining(remaining)}",
                    ]
                ),
                [[_btn("◀️ 返回人設設定", f"settings_{persona_id}")]],
            ),
            state={"flow": ""},
        )
    return _response(
        _message(prompt, [[_btn("❌ 取消", f"settings_{persona_id}")]]),
        state={"flow": flow, "draft": {"persona_id": persona_id}},
    )


def _persona_payload(persona: Persona, **updates: Any) -> dict[str, Any]:
    data = {
        "id": persona.id,
        "name": persona.name,
        "description": persona.description,
        "style_prompt": persona.style_prompt,
        "avatar_path": persona.avatar_path,
        "account_username": persona.account_username,
        "pad_code": persona.pad_code,
        "tg_free_group_id": persona.tg_free_group_id,
        "tg_free_group_name": persona.tg_free_group_name,
        "tg_paid_group_id": persona.tg_paid_group_id,
        "tg_paid_group_name": persona.tg_paid_group_name,
        "status": persona.status,
        "source_archive_id": persona.source_archive_id,
        "created_at": persona.created_at,
    }
    data.update(updates)
    return data


def _persona_content_edit_menu(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("只能编辑本地人设，请先同步或新建。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    return _response(
        _message(
            "\n".join(
                [
                    "🧾 生成/修改个人简介",
                    "",
                    f"人設：{persona.name}",
                    "",
                    "请选择要执行的操作：",
                ]
            ),
            _rows(
                [_btn("✏️ 直接替换简介", f"editcontent_patch_{persona_id}")],
                [_btn("🔄 重新生成", f"editcontent_regen_{persona_id}")],
                [_btn("❌ 取消", f"settings_{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _generate_persona_bio_response(persona_id: str, direction: str = "") -> dict[str, Any]:
    persona, row = _resolve_persona_for_action(persona_id)
    if not persona:
        return _response(_message("只能编辑本地人设，请先同步或新建。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    source_archive_id = _tool_r18_archive_id(persona_id, persona, row)
    if not source_archive_id:
        return _response(
            _message(
                "這個 Web 本地人設尚未同步到 Tool R18 人設庫，不能使用 TG Bot 的 AI 簡介重寫流程。",
                [[_btn("◀️ 返回設定", f"settings_{persona.id}")]],
            ),
            state={"flow": ""},
        )
    params = {"archiveId": source_archive_id, "direction": str(direction or "").strip(), "mode": "replace"}
    job = SourceWorkflowJobRepo.create("persona_rewrite_intro", f"重新生成人設簡介：{persona.name}", params, status="submitting")
    try:
        base, data = _source_submit_task("persona_rewrite_intro", params)
        SourceWorkflowJobRepo.update(
            job.id,
            status="submitted",
            result=data,
            source_task_id=str(data.get("id") or ""),
            source_base_url=base,
        )
    except Exception as exc:
        SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
        return _response(
            _message(f"❌ AI 人設簡介任務提交失敗\n\n{exc}", [[_btn("◀️ 返回設定", f"settings_{persona.id}")]]),
            state={"flow": ""},
        )
    source_task_id = str(data.get("id") or "")
    return _response(
        _message(
            "\n".join(
                [
                    "🧠 正在重新生成人設簡介...",
                    "",
                    f"人設：{persona.name}",
                    f"來源任務 ID：{source_task_id or '-'}",
                    "",
                    "完成後會直接更新 Tool R18 中同一個人設的 content 與 setup。",
                ]
            ),
            _rows(
                [_btn("📊 查看本次任務", f"source_task_detail:{source_task_id}") if source_task_id else _btn("📊 查看任務列表", "source_tasks")],
                [_btn("◀️ 返回設定", f"settings_{persona.id}")],
            ),
        ),
        state={"flow": ""},
    )


def _start_create_persona() -> dict[str, Any]:
    return _response(
        _message(
            "\n".join(
                [
                    "⭐ 新建人设",
                    "",
                    "步骤 1/3：请先输入角色名称。",
                    "",
                    "例如：林一",
                ]
            ),
            [[_btn("◀️ 返回", "list_personas")]],
        ),
        state={"flow": "create_persona_name", "draft": {}},
    )


def _derive_keywords(name: str, prompt: str) -> list[str]:
    prompt_lower = prompt.lower()
    candidates: list[str] = []
    if any(token in prompt for token in ("股", "金融", "理财", "看盘", "ETF")):
        candidates.extend(["穿紧身包臀裙的股民", "穿 Cos 服看盘的辣妹", "满房手办的温柔姐姐"])
    if any(token in prompt for token in ("游戏", "电竞", "二次元", "宅", "动漫")):
        candidates.extend(["戴猫耳耳机的电竞娘", "满房手办的温柔姐姐", "穿 Cos 服看盘的辣妹"])
    if any(token in prompt for token in ("温柔", "软", "可爱", "甜", "小姐姐")):
        candidates.extend(["穿蓬松大毛衣的软妹", "个性温柔的日常系姐姐", "午后咖啡厅里的邻家感"])
    if any(token in prompt_lower for token in ("fitness", "sport", "gym")) or any(token in prompt for token in ("健身", "运动", "瑜伽")):
        candidates.extend(["运动背心的健身教练", "清晨跑步的活力女孩", "瑜伽课后的自然笑容"])
    candidates.extend(
        [
            f"{name} 的标志性穿搭",
            "有镜头感的生活方式博主",
            "自然真实的 Threads 口吻",
            "日常感强的手机随拍风格",
            "高互动话题型表达",
        ]
    )
    out: list[str] = []
    for item in candidates:
        if item and item not in out:
            out.append(item)
        if len(out) >= 5:
            break
    return out


def _create_keyword_text(name: str, prompt: str, options: list[str], selected: list[str]) -> str:
    selected_text = "、".join(selected) if selected else "尚未选择"
    return "\n".join(
        [
            "✍️ 新建人设",
            "",
            f"人设：{name}",
            "",
            "请先选择本次人设走向的核心关键词。",
            f"最多可选 {CREATE_PERSONA_MAX_SELECTED_KEYWORDS} 个；选好后再生成完整人设。",
            "",
            f"目前已选：{selected_text}",
            "",
            f"原始提示：{prompt}",
        ]
    )


def _create_keyword_keyboard(options: list[str], selected: list[str]) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    for index in range(0, len(options), 2):
        row: list[dict[str, str]] = []
        for offset, keyword in enumerate(options[index : index + 2]):
            marker = "✅ " if keyword in selected else "☑️ "
            row.append(_btn(f"{marker}{keyword}", f"cpk_t_{index + offset}"))
        rows.append(row)
    rows.extend(
        _rows(
            [_btn("✅ 确认并生成人设", "cpk_done")],
            [_btn("🧹 清空选择", "cpk_clear"), _btn("◀️ 返回修改提示词", "cpk_back")],
        )
    )
    return rows


def _continue_create_persona(message: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    text = str(message or "").strip()
    if not text:
        return _response(_message("请直接输入内容。", [[_btn("◀️ 返回", "list_personas")]]), state=state)

    if state.get("flow") == "create_persona_name":
        if len(text) < 2:
            return _response(
                _message("角色名称太短，请重新输入 2 个字以上的名称。", [[_btn("◀️ 返回人设列表", "list_personas")]]),
                state=state,
            )
        name = text[:40]
        return _response(
            _message(
                "\n".join(
                    [
                        "⭐ 新建人设",
                        "",
                        f"角色名称：{name}",
                        "",
                        "步骤 2/3：请输入人设提示词。",
                        "我会沿用原来正常的人设生成流程，根据你的提示词生成人设卡片与后续推文设置。",
                        "",
                        "可以描述身份、性格、内容方向、语气、受众、图片风格等。",
                    ]
                ),
                [[_btn("◀️ 返回重新输入名称", "create_persona_entry")]],
            ),
            state={"flow": "create_persona_prompt", "draft": {"name": name}},
        )

    if state.get("flow") == "create_persona_prompt":
        name = str(draft.get("name") or "新人设")
        options = _derive_keywords(name, text)
        next_state = {
            "flow": "create_persona_keywords",
            "draft": {"name": name, "prompt": text, "options": options, "selected": []},
        }
        return _response(
            [
                _message("🧠 正在提炼人设核心关键词...", kind="status"),
                _message(_create_keyword_text(name, text, options, []), _create_keyword_keyboard(options, [])),
            ],
            state=next_state,
        )

    if state.get("flow") == "create_persona_keywords":
        return _response(
            _message(
                "请先点击上方按钮选择核心关键词；最多选 2 个，选好后点「确认并生成人设」。",
                [[_btn("◀️ 返回修改提示词", "cpk_back")]],
            ),
            state=state,
        )

    return _main_menu()


def _create_persona_keyword_action(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    name = str(draft.get("name") or "新人设")
    prompt = str(draft.get("prompt") or "")
    options = [str(item) for item in draft.get("options", []) if str(item)]
    selected = [str(item) for item in draft.get("selected", []) if str(item) in options]
    if not options:
        return _start_create_persona()
    if action == "cpk_back":
        return _response(
            _message(
                "\n".join(["✍️ 新建人设", "", f"角色名称：{name}", "", "请重新输入人设提示词。"]),
                [[_btn("◀️ 返回重新输入名称", "create_persona_entry")]],
            ),
            state={"flow": "create_persona_prompt", "draft": {"name": name}},
        )
    if action == "cpk_clear":
        selected = []
    elif action.startswith("cpk_t_") or action.startswith("cpk_t:"):
        try:
            keyword = options[int(action.rsplit("_", 1)[1] if action.startswith("cpk_t_") else action.split(":", 1)[1])]
        except Exception:
            keyword = ""
        if keyword:
            if keyword in selected:
                selected = [item for item in selected if item != keyword]
            elif len(selected) < CREATE_PERSONA_MAX_SELECTED_KEYWORDS:
                selected = [*selected, keyword]
    elif action == "cpk_done":
        return _finish_create_persona(name, prompt, selected)
    next_state = {"flow": "create_persona_keywords", "draft": {**draft, "selected": selected}}
    return _response(
        _message(_create_keyword_text(name, prompt, options, selected), _create_keyword_keyboard(options, selected)),
        state=next_state,
    )


def _finish_create_persona(name: str, prompt: str, selected: list[str]) -> dict[str, Any]:
    devices = _active_devices()
    pad_code = devices[0].pad_code if devices else ""
    account = next((item for item in _active_accounts() if item.pad_code == pad_code), None) if pad_code else None
    description = "\n".join(
        [
            prompt,
            "",
            f"核心关键词：{'、'.join(selected) if selected else '沿用原始提示'}",
        ]
    ).strip()
    persona_id, _ = PersonaRepo.upsert(
        {
            "name": name,
            "description": description,
            "style_prompt": "；".join(selected),
            "pad_code": pad_code,
            "account_username": account.username if account else "",
            "source_archive_id": "web-bot:telegram-create-persona",
        }
    )
    messages = [
        _message(f"🧠 正在根据「{'、'.join(selected) or '原始提示'}」生成人设...", kind="status"),
        _message(f"✅ 已新建人设：{name}\n\n{description}", kind="status"),
        _message(f"🎨 正在为人设「{name}」直接生成参考图...", kind="status"),
        _message("正在抓取 Threads / Instagram 热点，请稍候...", kind="status"),
    ]
    persona = PersonaRepo.get(persona_id)
    if persona:
        try:
            path = _generate_persona_reference_image(persona)
            updated = PersonaRepo.get(persona_id) or persona
            messages.append(_persona_image_message(updated, path))
        except Exception as exc:
            messages.append(
                _message(
                    f"人设已建立，但人设图生成失败：{exc}",
                    _rows([_btn("🔄 重新生成人设图", f"regenimg:{persona_id}")], [_btn("✍️ 新建推文", f"genpost:{persona_id}")]),
                )
            )
    return _response(messages, state={"flow": ""})


def _finish_create_persona(name: str, prompt: str, selected: list[str]) -> dict[str, Any]:
    devices = _active_devices()
    pad_code = devices[0].pad_code if devices else ""
    params = {
        "name": name,
        "prompt": prompt,
        "selectedKeywords": selected,
        "ownerBotName": "web-console",
        "chatId": SOURCE_WEB_BOT_CHAT_ID,
        "defaultPadCode": pad_code,
    }
    job = SourceWorkflowJobRepo.create("persona_create", f"新建人設：{name}", params, status="submitting")
    try:
        base, data = _source_submit_task("persona_create", params)
        SourceWorkflowJobRepo.update(
            job.id,
            status="submitted",
            result=data,
            source_task_id=str(data.get("id") or ""),
            source_base_url=base,
        )
    except Exception as exc:
        SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
        return _response(
            _message(
                f"❌ 新建人設任務提交失敗\n\n{exc}",
                _rows([_btn("➕ 重新新建人設", "create_persona_entry")], [_btn("◀️ 返回人設列表", "list_personas")]),
            ),
            state={"flow": ""},
        )
    source_task_id = str(data.get("id") or "")
    return _response(
        _message(
            "\n".join(
                [
                    f"🧠 正在根據「{'、'.join(selected) or '原始提示'}」生成人設...",
                    "",
                    f"角色名稱：{name}",
                    f"來源任務 ID：{source_task_id or '-'}",
                    "",
                    "已交給 TG Bot 使用的 AI 人設生成與 Tool R18 人設存儲流程。完成後請查看任務結果，再生成人設圖。",
                ]
            ),
            _rows(
                [_btn("📊 查看本次任務", f"source_task_detail:{source_task_id}") if source_task_id else _btn("📊 查看任務列表", "source_tasks")],
                [_btn("◀️ 返回人設列表", "list_personas")],
            ),
        ),
        state={"flow": ""},
    )


def _sync_personas() -> dict[str, Any]:
    inserted = updated = 0
    accounts_by_pad = {account.pad_code: account for account in _active_accounts() if account.pad_code}
    for index, device in enumerate(reversed(_active_devices()), start=1):
        suffix = "".join(ch.lower() for ch in device.pad_code if ch.isalnum())[-8:] or str(index)
        account = accounts_by_pad.get(device.pad_code)
        _, is_new = PersonaRepo.upsert(
            {
                "id": f"persona_{suffix}",
                "name": device.alias or f"云机 {device.pad_code}",
                "description": f"由智能体手机同步生成，PAD_CODE：{device.pad_code}",
                "account_username": account.username if account else "",
                "pad_code": device.pad_code,
                "source_archive_id": f"device:{device.pad_code}",
            }
        )
        inserted += int(is_new)
        updated += int(not is_new)
    return _response(
        _message(
            f"✅ 同步完成：新增 {inserted} 个，更新 {updated} 个。",
            [[_btn("👤 我的人设", "list_personas"), _btn("📱 智能体手机管理", "pad_mgmt")]],
        ),
        state={"flow": ""},
    )


def _account_name_from_pad(pad_code: str, index: int) -> str:
    suffix = "".join(ch.lower() for ch in str(pad_code or "") if ch.isalnum())[-8:]
    return f"cloud_{suffix or index}"


def _account_device_label(account: Any, devices_by_pad: dict[str, Device] | None = None) -> tuple[str, str]:
    pad_code = str(getattr(account, "pad_code", "") or "")
    device = (devices_by_pad or {}).get(pad_code) if pad_code else None
    name = str((device.alias if device else "") or pad_code or "")
    return name, pad_code


def _tg_accounts_text() -> str:
    accounts = _active_accounts()[: int(os.getenv("TG_BOT_MAX_LIST", "10") or 10)]
    if not accounts:
        return "我的账号\n\n暂无账号。\n\n可发送：/add_account USERNAME [ALIAS]"
    lines = ["我的账号", ""]
    lines.extend(f"{a.username} | 云机:{a.pad_code or '-'} | {a.alias or a.persona or '-'}" for a in accounts)
    lines.extend(["", "新增账号：/add_account USERNAME [ALIAS]", "绑定云机：/assign USERNAME PAD_CODE"])
    return "\n".join(lines)


def _tg_devices_text() -> str:
    devices = _active_devices()[: int(os.getenv("TG_BOT_MAX_LIST", "10") or 10)]
    if not devices:
        return "云机列表\n\n暂无云机。\n\n可按“导入 VMOS”或发送：/add_device PAD_CODE [ALIAS]"
    lines = ["云机列表", ""]
    lines.extend(f"{d.pad_code} | {d.alias or '-'} | last:{_dt(d.last_seen)}" for d in devices)
    lines.extend(["", "手动添加：/add_device PAD_CODE [ALIAS]"])
    return "\n".join(lines)


def _import_vmos_devices() -> tuple[int, int]:
    pads = vmos_client.list_devices_all_accounts()
    added = skipped = 0
    for item in pads:
        pad_code = str(item.get("padCode") or "").strip()
        if not pad_code:
            continue
        alias = str(item.get("padName") or "").strip()
        inserted, updated = DeviceRepo.upsert(
            pad_code,
            alias,
            str(item.get("_vmos_account") or "").strip(),
        )
        added += int(inserted)
        skipped += int(updated)
    return added, skipped


def _import_vmos_action() -> dict[str, Any]:
    try:
        added, skipped = _import_vmos_devices()
    except Exception as exc:
        logger.exception("VMOS import failed from web bot: %s", exc)
        return _response(
            _message(f"操作失败：{exc}", _tg_back_keyboard("devices_console")),
            state={"flow": ""},
        )
    return _response(
        _message(
            f"VMOS import complete. added={added}, existing={skipped}",
            _tg_back_keyboard("devices_console"),
        ),
        state={"flow": ""},
    )


def _tg_back_keyboard(refresh_action: str | None = None) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    if refresh_action:
        rows.append([_btn("刷新", refresh_action)])
    rows.append([_btn("返回主菜单", "menu")])
    return rows


def _account_command_response(message: str) -> dict[str, Any] | None:
    raw = str(message or "").strip()
    if not raw:
        return None
    command, _, args = raw.partition(" ")
    command = command.lower()
    args = args.strip()

    if command == "/accounts":
        return _accounts_console_menu()
    if command == "/devices":
        return _response(_message(_tg_devices_text(), _tg_back_keyboard("devices_console")), state={"flow": ""})
    if command == "/add_account":
        username, _, alias = args.partition(" ")
        username = username.strip()
        alias = alias.strip()
        if not username:
            return _response(_message("Usage: /add_account USERNAME [ALIAS]", _tg_back_keyboard("accounts_console")), state={"flow": ""})
        inserted, updated = AccountRepo.upsert_many([(username, "", alias)])
        return _response(
            _message(f"Account saved. inserted={inserted}, updated={updated}", _tg_back_keyboard("accounts_console")),
            state={"flow": ""},
        )
    if command == "/add_device":
        pad_code, _, alias = args.partition(" ")
        pad_code = pad_code.strip()
        alias = alias.strip()
        if not pad_code:
            return _response(_message("Usage: /add_device PAD_CODE [ALIAS]", _tg_back_keyboard("devices_console")), state={"flow": ""})
        created = DeviceRepo.create(pad_code, alias)
        return _response(
            _message("Device added." if created else "Device already exists.", _tg_back_keyboard("devices_console")),
            state={"flow": ""},
        )
    if command == "/assign":
        parts = args.split()
        if len(parts) < 2:
            return _response(_message("Usage: /assign USERNAME PAD_CODE", _tg_back_keyboard("accounts_console")), state={"flow": ""})
        username, pad_code = parts[0], parts[1]
        if not AccountRepo.get(username):
            return _response(_message(f"Account not found: {username}", _tg_back_keyboard("accounts_console")), state={"flow": ""})
        if pad_code.lower() not in {"none", "null", "-"} and not DeviceRepo.exists(pad_code):
            return _response(_message(f"Device not found: {pad_code}", _tg_back_keyboard("accounts_console")), state={"flow": ""})
        AccountRepo.assign_pad(username, None if pad_code.lower() in {"none", "null", "-"} else pad_code)
        return _response(
            _message(f"Assigned {username} -> {pad_code}", _tg_back_keyboard("accounts_console")),
            state={"flow": ""},
        )
    if command == "/import_vmos":
        return _import_vmos_action()
    return None


def _accounts_console_menu(page: int = 0) -> dict[str, Any]:
    accounts = _active_accounts()
    page_size = 8
    total_pages = max(1, (len(accounts) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    visible = accounts[start : start + page_size]
    account_buttons = [
        _btn((account.alias or account.username or "未命名账号")[:24], f"acctdetail:{account.username}")
        for account in visible
        if account.username
    ]
    keyboard = _rows(*_chunk_buttons(account_buttons, 2))
    if total_pages > 1:
        keyboard.append(
            [
                _btn("上一页", f"accounts_page:{max(0, page - 1)}"),
                _btn(f"{page + 1}/{total_pages}", "accounts_console"),
                _btn("下一页", f"accounts_page:{min(total_pages - 1, page + 1)}"),
            ]
        )
    keyboard.extend(
        _rows(
            [_btn("➕ 新建账号", "acct_create_start"), _btn("📥 从 VMOS 生成", "acct_from_devices")],
            [_btn("📲 导入 VMOS 云机", "import_vmos"), _btn("📱 智能体手机管理", "pad_mgmt")],
            [_btn("打开 /accounts", "open:/accounts"), _btn("◀️ 返回主菜单", "menu")],
        )
    )
    return _response(_message(_tg_accounts_text(), keyboard), state={"flow": ""})


def _devices_console_menu() -> dict[str, Any]:
    return _response(_message(_tg_devices_text(), _tg_back_keyboard("devices_console")), state={"flow": ""})


def _account_detail(username: str) -> dict[str, Any]:
    account = AccountRepo.get(username)
    if not account:
        return _response(_message("没有找到这个账号。", [[_btn("◀️ 返回账号列表", "accounts_console")]]), state={"flow": ""})
    device = DeviceRepo.get(account.pad_code) if account.pad_code else None
    name, pad_code = _account_device_label(account, {device.pad_code: device} if device else {})
    lines = [
        "🔐 云机账号详情",
        "",
        f"云机名称：{name}" if pad_code else "未绑定云机",
        f"PAD_CODE：{pad_code}" if pad_code else "",
        f"创建时间：{_dt(account.created_at)}",
        "",
        "可以直接在这里改绑定手机，和 Telegram Bot 的账号管理流程一致。",
    ]
    lines = [line for line in lines if line]
    devices = _active_devices()[:8]
    bind_rows = _chunk_buttons(
        [
            _btn((device.alias or device.pad_code)[:22], f"acctassign:{device.pad_code}:{account.username}")
            for device in devices
            if device.pad_code != account.pad_code
        ],
        2,
    )
    keyboard = _rows(*bind_rows)
    keyboard.extend(
        _rows(
            [_btn("解除绑定", f"acctassign:_none:{account.username}"), _btn("📱 手机管理", "pad_mgmt")],
            [_btn("🗑 删除账号", f"acctdelete_confirm:{account.username}", "danger")],
            [_btn("◀️ 返回账号列表", "accounts_console"), _btn("打开 /accounts", "open:/accounts")],
        )
    )
    return _response(_message("\n".join(lines), keyboard), state={"flow": ""})


def _account_assign(action: str) -> dict[str, Any]:
    try:
        _, pad_code, username = action.split(":", 2)
    except ValueError:
        return _accounts_console_menu()
    account = AccountRepo.get(username)
    if not account:
        return _response(_message("账号不存在。", [[_btn("◀️ 返回账号列表", "accounts_console")]]), state={"flow": ""})
    pad = "" if pad_code == "_none" else pad_code
    if pad and not DeviceRepo.exists(pad):
        return _response(_message("这台智能体手机不存在，请先导入设备。", [[_btn("◀️ 返回账号详情", f"acctdetail:{username}")]]), state={"flow": ""})
    AccountRepo.assign_pad(username, pad or None)
    device = DeviceRepo.get(pad) if pad else None
    label = f"{device.alias or pad} / {pad}" if device else "未绑定"
    return _response(
        _message(f"✅ 已更新绑定：{label}", [[_btn("查看账号详情", f"acctdetail:{username}"), _btn("返回账号列表", "accounts_console")]]),
        state={"flow": ""},
    )


def _account_create_start() -> dict[str, Any]:
    return _response(
        _message("➕ 新建账号\n\n步骤 1/3：请输入 username。", [[_btn("❌ 取消", "accounts_console")]]),
        state={"flow": "account_create_username", "draft": {}},
    )


def _continue_account_create(text: str, state: dict[str, Any]) -> dict[str, Any]:
    flow = str(state.get("flow") or "")
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    value = str(text or "").strip()
    if flow == "account_create_username":
        if not value:
            return _response(_message("username 不能为空，请重新输入。", [[_btn("❌ 取消", "accounts_console")]]), state=state)
        if "/" in value or "\\" in value:
            return _response(_message("username 不能包含 / 或 \\，请重新输入。", [[_btn("❌ 取消", "accounts_console")]]), state=state)
        draft["username"] = value
        return _response(
            _message("步骤 2/3：请输入别名。\n可以发送 - 跳过。", [[_btn("❌ 取消", "accounts_console")]]),
            state={"flow": "account_create_alias", "draft": draft},
        )
    if flow == "account_create_alias":
        draft["alias"] = "" if value in {"-", "skip", "跳过", "跳過"} else value
        return _response(
            _message("步骤 3/3：请输入人设备注。\n可以发送 - 跳过。", [[_btn("❌ 取消", "accounts_console")]]),
            state={"flow": "account_create_persona", "draft": draft},
        )
    if flow == "account_create_persona":
        username = str(draft.get("username") or "")
        alias = str(draft.get("alias") or "")
        persona = "" if value in {"-", "skip", "跳过", "跳過"} else value
        if not username:
            return _account_create_start()
        AccountRepo.upsert_many([(username, persona, alias)])
        return _response(
            [
                _message(f"✅ 已保存账号：{username}", kind="status"),
                _account_detail(username)["messages"][0],
            ],
            state={"flow": ""},
        )
    return _accounts_console_menu()


def _accounts_create_from_devices() -> dict[str, Any]:
    devices = _active_devices()
    if not devices:
        return _response(_message("还没有设备，请先到智能体手机管理导入 VMOS 云机。", [[_btn("📱 智能体手机管理", "pad_mgmt")]]), state={"flow": ""})
    inserted = updated = assigned = 0
    for index, device in enumerate(reversed(devices), start=1):
        username = _account_name_from_pad(device.pad_code, index)
        alias = device.alias or device.pad_code
        ins, upd = AccountRepo.upsert_many([(username, "VMOS 云机账号", alias)])
        inserted += ins
        updated += upd
        if AccountRepo.assign_pad(username, device.pad_code):
            assigned += 1
    return _response(
        _message(
            f"✅ 已按云机生成账号：新增 {inserted}，更新 {updated}，绑定 {assigned} 台。",
            [[_btn("查看账号列表", "accounts_console"), _btn("智能体手机管理", "pad_mgmt")]],
        ),
        state={"flow": ""},
    )


def _account_delete_confirm(username: str) -> dict[str, Any]:
    return _response(
        _message(
            "确认删除这个账号？已创建的发帖任务不会自动删除。",
            [[_btn("确认删除", f"acctdelete:{username}", "danger")], [_btn("取消", f"acctdetail:{username}")]],
        ),
        state={"flow": ""},
    )


def _account_delete(username: str) -> dict[str, Any]:
    from db import get_conn

    with get_conn() as conn:
        cur = conn.execute("DELETE FROM accounts WHERE username = ?", (username,))
    if cur.rowcount <= 0:
        return _response(_message("账号不存在或已被删除。", [[_btn("返回账号列表", "accounts_console")]]), state={"flow": ""})
    return _response(_message("🗑 已删除账号。", [[_btn("返回账号列表", "accounts_console")]]), state={"flow": ""})


def _hot_metrics_summary(persona_id: str, *, force: bool = False) -> dict[str, Any]:
    overview = build_overview(force_remote=force)
    row = find_persona(overview, persona_id)
    if not row:
        return _response(
            _message("尚未读取到这个人设的热点数据。", [[_btn("◀️ 返回人设设置", f"pd:{persona_id}")]]),
            state={"flow": ""},
        )
    posts = row.get("post_metrics") if isinstance(row.get("post_metrics"), list) else []
    keyboard = _rows(
        [_btn("🔄 刷新数据", f"shr:{persona_id}")],
        [_btn(f"📋 查看推文数据（{len(posts)} 篇）", f"shp:0:{persona_id}")] if posts else [],
        [_btn("📊 指定人設看板", f"open:{_persona_dashboard_url(persona_id)}")],
        [_btn("📊 打开此人设数据", f"open:/personas/{persona_id}/data")],
        [_btn("📊 打开总看板", "open:/persona-dashboard"), _btn("◀️ 返回人设设置", f"pd:{persona_id}")],
    )
    text = "\n".join(
        [
            "🔥 人设热点数据",
            "",
            f"人设：{_persona_row_name(row)}",
            "平台：Threads",
            "状态：已刷新" if _num((row.get("hot") or {}).get("hot_score")) else "状态：尚未刷新",
            "",
            _persona_hot_text(row),
            "",
            f"推文数据：{'已读取 ' + str(len(posts)) + ' 篇，点击下方按钮查看' if posts else '尚未读取到单帖资料'}",
        ]
    )
    return _response(_message(text, keyboard), state={"flow": ""})


def _hot_metrics_posts(action: str) -> dict[str, Any]:
    parts = action.split(":", 2)
    page = int(parts[1] or 0) if len(parts) > 1 and parts[1].isdigit() else 0
    persona_id = parts[2] if len(parts) > 2 else ""
    overview = build_overview()
    row = find_persona(overview, persona_id)
    if not row:
        return _hot_metrics_summary(persona_id)
    posts = row.get("post_metrics") if isinstance(row.get("post_metrics"), list) else []
    total_pages = max(1, (len(posts) + STORED_POSTS_PAGE_SIZE - 1) // STORED_POSTS_PAGE_SIZE)
    safe_page = max(0, min(page, total_pages - 1))
    visible = posts[safe_page * STORED_POSTS_PAGE_SIZE : (safe_page + 1) * STORED_POSTS_PAGE_SIZE]
    lines = ["🔥 人设热点数据", "", f"人设：{_persona_row_name(row)}", "平台：Threads", "", _persona_hot_text(row), ""]
    lines.append(f"单帖数据：第 {safe_page * STORED_POSTS_PAGE_SIZE + 1}-{safe_page * STORED_POSTS_PAGE_SIZE + len(visible)} / {len(posts)} 篇")
    for index, post in enumerate(visible, start=safe_page * STORED_POSTS_PAGE_SIZE + 1):
        content = str(post.get("content") or post.get("source_url") or "未读取到文案").replace("\n", " ")
        metrics = [
            f"赞 {_compact(post.get('like_count'))}",
            f"评 {_compact(post.get('comment_count'))}",
            f"转发 {_compact(post.get('repost_count'))}",
            f"分享 {_compact(post.get('share_count'))}",
            f"浏览 {_compact(post.get('view_count'))}" if post.get("view_count") is not None else "浏览 平台未公开",
        ]
        lines.extend(["", f"{index}. {content[:80]}{'...' if len(content) > 80 else ''}", "数据：" + " · ".join(metrics)])
        if post.get("source_url"):
            lines.append(f"原帖：{post.get('source_url')}")
    keyboard = _rows(
        [_btn("🔄 刷新数据", f"shr:{persona_id}")],
        [
            _btn("◀️ 上一页", f"shp:{max(0, safe_page - 1)}:{persona_id}"),
            _btn(f"{safe_page + 1}/{total_pages}", f"shp:{safe_page}:{persona_id}"),
            _btn("下一页 ▶️", f"shp:{min(total_pages - 1, safe_page + 1)}:{persona_id}"),
        ]
        if total_pages > 1
        else [],
        [_btn("📊 打开此人设数据", f"open:/personas/{persona_id}/data")],
        [_btn("◀️ 返回人设设置", f"pd:{persona_id}")],
    )
    return _response(_message("\n".join(lines), keyboard), state={"flow": ""})


def _pad_status_label(value: Any) -> str:
    labels = {
        "0": "未知",
        "1": "创建中",
        "2": "已关机",
        "3": "故障",
        "4": "重启中",
        "5": "恢复中",
        "10": "运行中",
    }
    if value is None or value == "":
        return "本地记录"
    return labels.get(str(value), str(value))


def _device_display(device: Device, status: dict[str, Any] | None = None) -> str:
    name = (status or {}).get("pad_name") or (status or {}).get("padName") or device.alias or device.pad_code
    status_value = (status or {}).get("status") or (status or {}).get("padStatus") or ""
    return f"{name} · {_pad_status_label(status_value)}"


def _devices_menu(*, force: bool = False) -> dict[str, Any]:
    devices = _active_devices()
    refresh_error = ""
    status_by_pad: dict[str, dict[str, Any]] = {}
    if force:
        try:
            raw = vmos_client.list_devices_all_accounts()
            for item in raw:
                pad = str(item.get("padCode") or item.get("pad_code") or "").strip()
                if not pad:
                    continue
                name = str(item.get("padName") or item.get("padType") or pad).strip()
                account = str(item.get("_vmos_account") or "").strip()
                DeviceRepo.upsert(pad, name, account)
                status_by_pad[pad] = item
            devices = _active_devices()
        except Exception as exc:
            refresh_error = str(exc)
    running_count = 0
    lines = ["📱 智能体手机管理", ""]
    if refresh_error:
        lines.extend(["VMOS 刷新失败：", refresh_error, ""])
    if not devices:
        lines.append("目前还没有智能体手机，请先导入 VMOS 云机。")
    else:
        for device in devices[:12]:
            status = status_by_pad.get(device.pad_code, {})
            if str(status.get("padStatus") or status.get("status") or "") == "10":
                running_count += 1
            indicator = "🟢" if str(status.get("padStatus") or status.get("status") or "") == "10" else "🟢"
            lines.append(f"{indicator} {_device_display(device, status)}")
        lines.extend(["", f"请选择要管理的智能体手机："])
    buttons = [_btn((device.alias or device.pad_code)[:20], f"pad_detail:{device.pad_code}") for device in devices[:12]]
    keyboard = _chunk_buttons(buttons, 2)
    keyboard.extend(
        _rows(
            [_btn("🔄 刷新列表", "pad_mgmt_refresh")],
            [_btn("📋 打开设备管理", "open:/devices"), _btn("返回主选单", "menu")],
        )
    )
    return _response(_message("\n".join(lines), keyboard), state={"flow": ""})


def _device_status_payload(pad_code: str) -> dict[str, Any]:
    device = DeviceRepo.get(pad_code)
    if not device:
        return {"ok": False, "err": "device not found"}
    try:
        raw = vmos_client.get_device_info(pad_code)
        merged = {**raw}
        try:
            merged.update(vmos_client.get_pad_info(pad_code))
        except Exception:
            pass
        if merged.get("padName"):
            DeviceRepo.upsert(pad_code, str(merged.get("padName") or ""), str(merged.get("_vmos_account") or device.vmos_account or ""))
        return {"ok": True, **merged}
    except Exception as exc:
        return {"ok": False, "err": str(exc)}


def _device_detail(pad_code: str) -> dict[str, Any]:
    device = DeviceRepo.get(pad_code)
    if not device:
        return _response(_message("没有找到这台智能体手机。", [[_btn("◀️ 返回智能体手机列表", "pad_mgmt")]]))
    status = _device_status_payload(pad_code)
    name = str(status.get("padName") or device.alias or pad_code)
    status_text = _pad_status_label(status.get("padStatus") or status.get("status"))
    lines = [
        "📱 智能体手机",
        "",
        f"名称：{name}",
        f"padCode：{pad_code}",
        f"状态：{status_text}",
        f"ADB：{status.get('adbOpenStatus', '-')}",
        f"型号：{status.get('padType', '-')}",
        f"VMOS 账号：{device.vmos_account or status.get('_vmos_account') or '-'}",
        f"最后预览：{_dt(device.last_seen)}",
    ]
    if not status.get("ok") and status.get("err"):
        lines.extend(["", f"状态读取失败：{status.get('err')}"])
    keyboard = _rows(
        [_btn("🖼 预览画面", f"device_preview:{pad_code}")],
        [_btn("◀️ 返回智能体手机列表", "pad_mgmt")],
    )
    return _response(_message("\n".join(lines), keyboard), state={"flow": ""})


def _device_preview(pad_code: str) -> dict[str, Any]:
    device = DeviceRepo.get(pad_code)
    if not device:
        return _response(_message("没有找到这台智能体手机。", [[_btn("◀️ 返回列表", "pad_mgmt")]]))
    try:
        shot = vmos_client.screenshot(pad_code)
        image_url = str(
            shot.get("accessUrl")
            or shot.get("url")
            or shot.get("screenUrl")
            or shot.get("screenshotUrl")
            or shot.get("imageUrl")
            or ""
        ).strip()
        if not image_url:
            raise RuntimeError("VMOS screenshot did not return an image URL")
        DeviceRepo.touch(pad_code)
        return _response(
            _message(
                f"🖼 智能体手机画面预览\n\n{device.alias or pad_code}",
                _rows([_btn("🔄 重新预览", f"device_preview:{pad_code}"), _btn("◀️ 返回手机详情", f"pad_detail:{pad_code}")]),
                image=image_url,
            ),
            state={"flow": ""},
        )
    except Exception as exc:
        return _response(
            _message(
                f"预览失败：{exc}",
                _rows([_btn("🔄 重试", f"device_preview:{pad_code}"), _btn("◀️ 返回手机详情", f"pad_detail:{pad_code}")]),
            ),
            state={"flow": ""},
        )


def _status_menu() -> dict[str, Any]:
    tasks = _task_counts(_visible_tasks(limit=10000))
    lines = [
        "📊 排程状态",
        "",
        f"待发布：{tasks.get('pending', 0)}",
        f"发布中：{tasks.get('publishing', 0)}",
        f"完成：{tasks.get('done', 0) + tasks.get('success', 0)}",
        f"失败：{tasks.get('failed', 0)}",
        f"取消：{tasks.get('cancelled', 0)}",
        "",
        "目前 Web Bot 与本地排程器联动，发布任务会进入发帖任务列表。",
    ]
    return _response(
        _message(
            "\n".join(lines),
            _rows([_btn("📋 查看发帖任务", "open:/tasks"), _btn("🔄 刷新状态", "status")], [_btn("返回主选单", "menu")]),
        ),
        state={"flow": ""},
    )


def _dashboard_menu(force: bool = False) -> dict[str, Any]:
    overview = build_overview(force_remote=force)
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    source = (overview.get("data_sources") or {}).get("remote_persona_dashboard", {})
    lines = [
        "🔥 热点数据看板",
        "",
        f"人设：{summary.get('persona_count', 0)}",
        f"总热度：{_compact(summary.get('hot_score'))}",
        f"逐帖浏览：{_compact(summary.get('post_views'))}",
        f"主页浏览：{_compact(summary.get('recent_views'))}",
        f"互动合计：{_compact(summary.get('total_interactions'))}",
        "",
        f"数据来源：{source.get('source') or 'local'}",
    ]
    return _response(
        _message(
            "\n".join(lines),
            _rows(
                [_btn("🔄 刷新数据", "dashboard_refresh"), _btn("📊 打开数据看板", "open:/persona-dashboard")],
                [_btn("👤 我的人设", "list_personas"), _btn("返回主选单", "menu")],
            ),
        ),
        state={"flow": ""},
    )


def _source_workflow_key(action_key: str) -> str:
    text = str(action_key or "").strip()
    return SOURCE_ACTION_ALIASES.get(text, text)


def _source_step_summary(params: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in params.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            shown = "；".join(str(item) for item in value[:4])
        else:
            shown = str(value)
        if len(shown) > 120:
            shown = shown[:117] + "..."
        lines.append(f"{key}: {shown}")
    return "\n".join(lines) if lines else "尚未填写参数"


SOURCE_STATUS_LABELS = {
    "queued": "排队中",
    "running": "生成中",
    "success": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}


def _source_clean_workflow_id(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    if text.startswith("__converted__/"):
        text = text[len("__converted__/") :]
    lowered = text.lower()
    if lowered.endswith(".api.json"):
        text = text[:-9]
    elif lowered.endswith(".json"):
        text = text[:-5]
    return text.strip("/")


def _source_workflow_line(item: dict[str, Any]) -> str:
    workflow_name = str(item.get("current_workflow_name") or item.get("workflow_name") or "").strip()
    raw_ids = item.get("current_workflow_ids")
    if not isinstance(raw_ids, list):
        raw_ids = item.get("workflow_ids")
    workflow_ids: list[str] = []
    if isinstance(raw_ids, list):
        workflow_ids = [_source_clean_workflow_id(value) for value in raw_ids]
    if not workflow_ids:
        workflow_id_value = item.get("current_workflow_id") or item.get("workflow_id")
        workflow_ids = [_source_clean_workflow_id(value) for value in str(workflow_id_value or "").split(",")]
    workflow_ids = [value for value in workflow_ids if value]
    workflow_chain = " > ".join(workflow_ids)
    if workflow_name and workflow_chain:
        return f"工作流：{workflow_name} / {workflow_chain}"
    if workflow_name:
        return f"工作流：{workflow_name}"
    if workflow_chain:
        return f"工作流：{workflow_chain}"
    return ""


def _source_event_line(item: dict[str, Any]) -> str:
    event = item.get("latest_event") if isinstance(item.get("latest_event"), dict) else {}
    message = str(event.get("message") or "").strip()
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    parts: list[str] = []
    if isinstance(data, dict):
        if data.get("queue_position"):
            parts.append(f"位置{data.get('queue_position')}")
        if data.get("waiting") is not None:
            parts.append(f"等待{data.get('waiting')}")
        if data.get("running") is not None:
            parts.append(f"执行{data.get('running')}")
        if data.get("max_concurrency") is not None:
            parts.append(f"上限{data.get('max_concurrency')}")
    if message and parts:
        return f"{message}（{'，'.join(parts)}）"
    if message:
        return message
    if parts:
        return "队列：" + "，".join(parts)
    return ""


def _source_task_label(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip().lower()
    return str(item.get("status_label") or SOURCE_STATUS_LABELS.get(status, status or "unknown"))


def _source_current_step(draft: dict[str, Any]) -> dict[str, Any] | None:
    key = _source_workflow_key(str(draft.get("key") or ""))
    flow = SOURCE_WORKFLOW_CATALOG.get(key) or {}
    steps = flow.get("steps") if isinstance(flow.get("steps"), list) else []
    index = _num(draft.get("step_index"))
    if index < 0 or index >= len(steps):
        return None
    step = steps[index]
    return step if isinstance(step, dict) else None


def _source_parse_step_value(step: dict[str, Any], text: str) -> tuple[bool, Any, str]:
    typ = str(step.get("type") or "text")
    label = str(step.get("label") or step.get("key") or "参数")
    raw = str(text or "").strip()
    optional = typ.startswith("optional")
    if optional and raw.lower() in {"", "-", "skip", "略过", "跳过", "不需要"}:
        return True, "", ""
    if typ in {"path", "optional_path"}:
        return _validate_source_path(raw, label)
    if typ == "path_list":
        items = _split_path_list(raw)
        if not items:
            return False, [], f"{label} 至少需要一个文件路径"
        checked: list[str] = []
        for item in items:
            ok, value, error = _validate_source_path(item, label)
            if not ok:
                return False, [], error
            checked.append(value)
        return True, checked, ""
    if typ == "number":
        if not raw:
            return False, 0, f"{label} 不能为空"
        try:
            value = int(float(raw))
        except Exception:
            return False, 0, f"{label} 必须是数字"
        if step.get("min") is not None:
            value = max(int(step.get("min") or value), value)
        if step.get("max") is not None:
            value = min(int(step.get("max") or value), value)
        return True, value, ""
    if typ == "choice":
        choices = [str(item) for item in (step.get("choices") or [])]
        if raw not in choices:
            return False, "", f"{label} 请从按钮选择：{' / '.join(choices)}"
        return True, raw, ""
    if typ == "optional_text":
        return True, raw, ""
    if not raw:
        return False, "", f"{label} 不能为空"
    return True, raw, ""


def _source_prompt_for_step(draft: dict[str, Any]) -> dict[str, Any]:
    key = _source_workflow_key(str(draft.get("key") or ""))
    flow = SOURCE_WORKFLOW_CATALOG.get(key)
    if not flow:
        return _response(_message("来源功能不存在，请重新选择。", [[_btn("返回主选单", "menu")]]), state={"flow": ""})
    step = _source_current_step(draft)
    if not step:
        return _source_workflow_ready(draft)
    steps = flow.get("steps") if isinstance(flow.get("steps"), list) else []
    index = _num(draft.get("step_index")) + 1
    typ = str(step.get("type") or "text")
    keyboard: list[list[dict[str, str]]] = []
    if typ == "choice":
        keyboard.extend(_chunk_buttons([_btn(str(choice), f"source_choice:{urllib.parse.quote(str(choice), safe='')}") for choice in step.get("choices", [])], 2))
    if typ.startswith("optional"):
        keyboard.append([_btn("略过", "source_step_skip")])
    keyboard.append([_btn("取消", "source_cancel"), _btn("返回主选单", "menu")])
    text = "\n".join(
        [
            f"{flow.get('label')} 参数填写",
            f"步骤 {index}/{len(steps)}：{step.get('label')}",
            "",
            str(step.get("prompt") or "请输入参数。"),
        ]
    )
    return _response(_message(text, keyboard), state={"flow": "source_workflow_collect", "draft": draft})


def _source_workflow_ready(draft: dict[str, Any]) -> dict[str, Any]:
    key = _source_workflow_key(str(draft.get("key") or ""))
    flow = SOURCE_WORKFLOW_CATALOG.get(key) or {}
    params = draft.get("params") if isinstance(draft.get("params"), dict) else {}
    text = "\n".join(
        [
            f"✅ {flow.get('label', key)} 参数已齐",
            "",
            f"来源任务类型：{flow.get('task_type')}",
            "",
            _source_step_summary(params),
            "",
            "确认后会提交到 D:\\workflow_delivery_package_source 的 internal TG 工作流。",
        ]
    )
    return _response(
        _message(
            text,
            _rows(
                [_btn("提交来源任务", "source_workflow_confirm", "primary")],
                [_btn("重新填写", f"source_task_start:{key}"), _btn("返回主选单", "menu")],
            ),
        ),
        state={"flow": "source_workflow_ready", "draft": draft},
    )


def _source_workflow_start(action_key: str, preset_params: dict[str, Any] | None = None) -> dict[str, Any]:
    key = _source_workflow_key(action_key)
    flow = SOURCE_WORKFLOW_CATALOG.get(key)
    if not flow:
        return _response(_message("找不到这个来源工作流，请从图像/视频菜单重新选择。", [[_btn("返回主选单", "menu")]]), state={"flow": ""})
    params = dict(flow.get("defaults") or {})
    params.update(preset_params or {})
    draft = {"key": key, "step_index": 0, "params": params}
    intro = str(flow.get("intro") or "").strip()
    first = _source_prompt_for_step(draft)
    messages = [_message(f"{flow.get('label')}\n\n{intro}\n\n来源：{SOURCE_ROOT}", kind="status")]
    messages.extend(first["messages"])
    return _response(messages, state=first.get("state") or {"flow": ""})


def _continue_source_workflow(text: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    step = _source_current_step(draft)
    if not step:
        return _source_workflow_ready(draft)
    ok, value, error = _source_parse_step_value(step, text)
    if not ok:
        return _response(
            _message(f"⚠️ {error}\n\n请重新输入。", [[_btn("取消", "source_cancel"), _btn("返回主选单", "menu")]]),
            state=state,
        )
    params = draft.get("params") if isinstance(draft.get("params"), dict) else {}
    if value not in ("", None, [], {}):
        params[str(step.get("key"))] = value
    draft["params"] = params
    draft["step_index"] = _num(draft.get("step_index")) + 1
    return _source_prompt_for_step(draft)


def _source_choice(action: str, state: dict[str, Any]) -> dict[str, Any]:
    value = urllib.parse.unquote(action.split(":", 1)[1]) if ":" in action else ""
    return _continue_source_workflow(value, state)


def _source_skip_step(state: dict[str, Any]) -> dict[str, Any]:
    return _continue_source_workflow("-", state)


def _source_submit_from_draft(draft: dict[str, Any]) -> dict[str, Any]:
    key = _source_workflow_key(str(draft.get("key") or ""))
    flow = SOURCE_WORKFLOW_CATALOG.get(key)
    if not flow:
        return _response(_message("来源工作流已失效，请重新选择。", [[_btn("返回主选单", "menu")]]), state={"flow": ""})
    params = dict(flow.get("defaults") or {})
    params.update(draft.get("params") if isinstance(draft.get("params"), dict) else {})
    if flow.get("task_type") == "create_video" and not params.get("product_image_local_path"):
        params["product_image_local_path"] = params.get("model_image_local_path") or params.get("image_local_path") or ""
    label = str(flow.get("label") or key)
    task_type = str(flow.get("task_type") or key)
    job = SourceWorkflowJobRepo.create(task_type, label, params, status="submitting")
    try:
        base, data = _source_submit_task(task_type, params)
        SourceWorkflowJobRepo.update(
            job.id,
            status="submitted",
            result=data,
            source_task_id=str(data.get("id") or ""),
            source_base_url=base,
        )
    except Exception as exc:
        SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
        return _response(
            _message(
                f"❌ 来源任务提交失败\n\n{exc}",
                _rows([_btn("重新填写", f"source_task_start:{key}")], [_btn("查看来源状态", "source_status"), _btn("返回主选单", "menu")]),
            ),
            state={"flow": ""},
        )
    text = "\n".join(
        [
            f"✅ 已提交：{label}",
            "",
            f"来源 API：{base}",
            f"来源任务 ID：{data.get('id') or '-'}",
            f"任务类型：{data.get('task_type') or task_type}",
            "",
            str(data.get("prompt_preview") or "").strip()[:600],
        ]
    ).strip()
    return _response(
        _message(
            text,
            _rows(
                [_btn("查看来源状态", "source_status"), _btn("查看来源任务列表", "source_tasks")],
                [_btn("重跑最近任务", "source_rerun_latest"), _btn("返回主选单", "menu")],
            ),
        ),
        state={"flow": ""},
    )


def _source_status_menu() -> dict[str, Any]:
    try:
        base, data = _source_status_data()
    except Exception as exc:
        return _response(_message(f"❌ 读取来源状态失败\n\n{exc}", [[_btn("返回主选单", "menu")]]), state={"flow": ""})
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    latest = data.get("latest_task") if isinstance(data.get("latest_task"), dict) else {}
    active = data.get("active_task") if isinstance(data.get("active_task"), dict) else {}
    lines = [
        "来源工作流状态",
        "",
        f"API：{base}",
        f"chat_id：{data.get('chat_id') or SOURCE_WEB_BOT_CHAT_ID}",
        f"排队：{counts.get('queued', 0)}",
        f"运行中：{counts.get('running', 0)}",
        f"成功：{counts.get('success', 0)}",
        f"失败：{counts.get('failed', 0)}",
        f"取消：{counts.get('cancelled', 0)}",
    ]
    if active:
        lines.extend(["", f"当前任务：{active.get('type') or '-'} / {active.get('id') or '-'} / {_source_task_label(active)}"])
        workflow_line = _source_workflow_line(active)
        event_line = _source_event_line(active)
        if workflow_line:
            lines.append(workflow_line)
        if event_line:
            lines.append(f"当前进度：{event_line}")
    else:
        lines.extend(["", "当前任务：无，来源工作台可立即使用"])
    if latest:
        lines.extend(["", f"最近任务：{latest.get('type') or '-'} / {latest.get('id') or '-'} / {_source_task_label(latest)}"])
        workflow_line = _source_workflow_line(latest)
        if workflow_line:
            lines.append(workflow_line)
    return _response(
        _message(
            "\n".join(lines),
            _rows(
                [_btn("刷新状态", "source_status"), _btn("任务列表", "source_tasks")],
                [_btn("停止最近任务", "source_cancel_latest", "danger"), _btn("工作流配置", "source_runtime_config")],
                [_btn("返回主选单", "menu")],
            ),
        ),
        state={"flow": ""},
    )


def _source_tasks_menu() -> dict[str, Any]:
    try:
        base, tasks = _source_tasks(limit=10)
    except Exception as exc:
        return _response(_message(f"❌ 读取来源任务失败\n\n{exc}", [[_btn("返回主选单", "menu")]]), state={"flow": ""})
    lines = ["来源任务列表", "", f"API：{base}", ""]
    keyboard: list[list[dict[str, str]]] = []
    if not tasks:
        lines.append("暂无这个 Web 操作台提交的来源任务。")
    for index, task in enumerate(tasks, start=1):
        tid = str(task.get("id") or "")
        lines.append(f"{index}. {task.get('type') or '-'}：{_source_task_label(task)}（{tid or '-'}）")
        workflow_line = _source_workflow_line(task)
        event_line = _source_event_line(task)
        if workflow_line:
            lines.append(f"   {workflow_line}")
        if event_line and str(task.get("status") or "").lower() in {"queued", "running"}:
            lines.append(f"   进度：{event_line}")
        error = str(task.get("error") or "").strip()
        if error and str(task.get("status") or "").lower() == "failed":
            lines.append(f"   错误：{error[:120]}")
        if tid:
            keyboard.append([_btn(f"查看 {index}", f"source_task_detail:{tid}")])
    keyboard.extend(_rows([_btn("刷新", "source_tasks"), _btn("来源状态", "source_status")], [_btn("返回主选单", "menu")]))
    return _response(_message("\n".join(lines), keyboard), state={"flow": ""})


def _sentiment_hot_draft_from_fetch(task_id: str, task_input: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(task_input.get("uiPersonaId") or result.get("archiveId") or task_input.get("archiveId") or "")
    return {
        "fetch_task_id": task_id,
        "sentiment_action_key": hashlib.md5(task_id.encode()).hexdigest()[:8],
        "persona_id": persona_id,
        "source_archive_id": str(result.get("archiveId") or task_input.get("archiveId") or ""),
        "name": str(result.get("archiveName") or task_input.get("uiPersonaName") or persona_id or "人設"),
        "content_branch": str(task_input.get("contentBranch") or ""),
        "hot_candidates": [item for item in (result.get("candidates") if isinstance(result.get("candidates"), list) else []) if isinstance(item, dict)],
        "selected_hot_indexes": [],
        "hot_keywords": [str(item) for item in (result.get("keywords") if isinstance(result.get("keywords"), list) else []) if str(item).strip()],
        "hot_cookie_statuses": [item for item in (result.get("cookieStatuses") if isinstance(result.get("cookieStatuses"), list) else []) if isinstance(item, dict)],
        "hot_cookie_lines": [str(item) for item in (result.get("cookieLines") if isinstance(result.get("cookieLines"), list) else []) if str(item).strip()],
        "hot_warnings": [str(item) for item in (result.get("warnings") if isinstance(result.get("warnings"), list) else []) if str(item).strip()],
    }


def _sentiment_hot_restore_import_draft(task_input: dict[str, Any], imported_ids: set[str] | None = None) -> dict[str, Any]:
    fetch_task_id = str(task_input.get("fetchTaskId") or "")
    if not fetch_task_id:
        return {}
    _base, data = _source_task_detail_data(fetch_task_id)
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    draft = _sentiment_hot_draft_from_fetch(fetch_task_id, task.get("input") if isinstance(task.get("input"), dict) else {}, result)
    draft["hot_candidates"] = [item for item in draft.get("hot_candidates", []) if str(item.get("id") or "") not in (imported_ids or set())]
    by_id = {str(item.get("id") or ""): (index, item) for index, item in enumerate(draft["hot_candidates"])}
    edited: dict[str, str] = {}
    deleted: dict[str, list[int]] = {}
    selected: list[int] = []
    for item in (task_input.get("items") if isinstance(task_input.get("items"), list) else []):
        candidate = item.get("candidate") if isinstance(item, dict) and isinstance(item.get("candidate"), dict) else {}
        candidate_id = str(candidate.get("id") or "")
        if candidate_id not in by_id:
            continue
        index, source_candidate = by_id[candidate_id]
        selected.append(index)
        if str(item.get("content") or "").strip():
            edited[candidate_id] = str(item["content"]).strip()
        if isinstance(item.get("media"), list):
            kept_urls = {str(media.get("url") or "") for media in item["media"] if isinstance(media, dict)}
            deleted[candidate_id] = [i for i, media in enumerate(_sentiment_hot_media(source_candidate)) if str(media.get("url") or "") not in kept_urls]
    draft.update({"selected_hot_indexes": selected, "hot_edited_contents": edited, "hot_deleted_media_indexes": deleted})
    return draft


def _sentiment_hot_source_task_response(task_id: str, task: dict[str, Any], task_input: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    status = str(task.get("status") or "").lower()
    action = str(result.get("action") or task_input.get("action") or "fetch").lower()
    persona_id = str(task_input.get("uiPersonaId") or result.get("archiveId") or task_input.get("archiveId") or "")
    archive_id = str(result.get("archiveId") or task_input.get("archiveId") or "")
    name = str(result.get("archiveName") or task_input.get("uiPersonaName") or persona_id or "人設")
    if status in {"queued", "running"}:
        text = "正在抓取 Threads / Instagram 热点，请稍候..." if action == "fetch" else "正在导入热点推文并下载媒体，请稍候..."
        response = _response(
            _message(text, _rows([_btn("刷新本次任務", f"source_task_detail:{task_id}")], [_btn("返回新建推文", f"genpost_branch_{persona_id}")])),
            state={"flow": "sentiment_hot_wait", "draft": {"source_task_id": task_id}},
        )
        response["poll"] = {"action": f"source_task_poll:{task_id}", "interval_ms": 2000}
        return response
    if status != "success":
        error = str(task.get("error") or "热点任务执行失败")
        if action == "import":
            try:
                draft = _sentiment_hot_restore_import_draft(task_input)
                key = str(draft.get("sentiment_action_key") or "")
                if key:
                    return _response(_message(f"❌ 热点导入失败\n\n{error}", [[_btn("返回候选列表", f"shlist_{key}")]]), state={"flow": "sentiment_hot_select", "draft": draft})
            except Exception:
                pass
        return _response(
            _message(f"❌ 热点{'抓取' if action == 'fetch' else '导入'}失败\n\n{error}", _rows([_btn("重新抓取", f"gph_{_genpost_branch_token(str(task_input.get('contentBranch') or ''))}_{persona_id}")], [_btn("返回新建推文", f"genpost_branch_{persona_id}")])),
            state={"flow": ""},
        )
    if action == "fetch":
        draft = _sentiment_hot_draft_from_fetch(task_id, task_input, result)
        return _genpost_hot_menu("genpost_hot", {"draft": draft})

    imported_posts = [item for item in (result.get("posts") if isinstance(result.get("posts"), list) else []) if isinstance(item, dict)]
    failures = [item for item in (result.get("failures") if isinstance(result.get("failures"), list) else []) if isinstance(item, dict)]
    imported_ids = {str(item.get("candidateId") or "") for item in imported_posts if str(item.get("candidateId") or "")}
    try:
        draft = _sentiment_hot_restore_import_draft(task_input, imported_ids)
    except Exception:
        draft = {"persona_id": persona_id, "source_archive_id": archive_id, "name": name, "hot_candidates": [], "selected_hot_indexes": []}
    requested_items = [item for item in (task_input.get("items") if isinstance(task_input.get("items"), list) else []) if isinstance(item, dict)]
    if len(requested_items) == 1 and imported_posts:
        first = imported_posts[0]
        lines = [
            "✅ 已导入编辑后的热点推文" if first.get("edited") else "✅ 已导入热点推文",
            f"人设: {name}",
            f"来源: {first.get('platform') or '-'}",
            f"数据: {first.get('metricLine') or '-'}",
            f"媒体: {first.get('mediaType') or 'unknown'}" if first.get("mediaUrl") else "媒体: 无",
            "已加入待发布推文，发布成功后才会写入人设记忆。",
        ]
    else:
        lines = [
            f"✅ 已批量导入 {len(imported_posts)} 篇热点推文" if imported_posts else "⚠️ 本次没有成功导入热点推文",
            f"人设: {name}",
            "已加入待发布推文，发布成功后才会写入人设记忆。",
        ]
    if failures:
        lines.extend(["", "失败:", *[f"- 第 {_num(item.get('index')) + 1} 篇：{item.get('error') or '保存失败'}" for item in failures[:5]]])
    key = str(draft.get("sentiment_action_key") or "")
    rows = [[_btn("查看推文列表", f"posts_{archive_id}_p0")]]
    if draft.get("hot_candidates") and key:
        rows.append([_btn(f"返回候选列表（剩余 {len(draft['hot_candidates'])} 篇）", f"shlist_{key}")])
    if key:
        rows.append([_btn("继续刷新抓取", f"shrf_{key}")])
    rows.append([_btn("返回人设详情", f"pd_{archive_id}")])
    return _response(_message("\n".join(lines), rows), state={"flow": "sentiment_hot_select", "draft": draft})


def _source_task_detail(task_id: str) -> dict[str, Any]:
    try:
        base, data = _source_task_detail_data(task_id)
    except Exception as exc:
        return _response(_message(f"❌ 读取来源任务详情失败\n\n{exc}", [[_btn("任务列表", "source_tasks")]]), state={"flow": ""})
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    lines = [
        "来源任务详情",
        "",
        f"API：{base}",
        f"ID：{task.get('id') or task_id}",
        f"类型：{task.get('type') or '-'}",
        f"状态：{_source_task_label(task)}",
        f"RunningHub：{task.get('runninghub_task_id') or '-'}",
        f"下载：{task.get('download_path') or '-'}",
    ]
    workflow_line = _source_workflow_line(task)
    event_line = _source_event_line(task)
    if workflow_line:
        lines.append(workflow_line)
    if event_line:
        lines.append(f"进度：{event_line}")
    if task.get("error"):
        lines.extend(["", f"错误：{task.get('error')}"])
    if task.get("batch_summary"):
        lines.extend(["", f"批次：{json.dumps(task.get('batch_summary'), ensure_ascii=False)[:500]}"])
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
    archive_id = str(result.get("archiveId") or result.get("archive_id") or task_input.get("archiveId") or task_input.get("archive_id") or "").strip()
    post_id = str(result.get("postId") or result.get("post_id") or task_input.get("postId") or task_input.get("post_id") or "").strip()
    task_type = str(task.get("type") or "").strip()
    status = str(task.get("status") or "").lower()
    if task_type == "persona_sentiment_hot":
        return _sentiment_hot_source_task_response(task_id, task, task_input, result)
    generated_image = _safe_web_media_url(result.get("imageUrl") or result.get("image_url"))
    publish_screenshot = _safe_web_media_url(result.get("screenshotUrl") or result.get("screenshot_url"))
    preview_image = publish_screenshot if task_type == "persona_publish_post" and publish_screenshot else generated_image
    published_url = _safe_web_media_url(result.get("publishedUrl") or result.get("published_url"))
    candidate_images = [url for value in (result.get("imageUrls") if isinstance(result.get("imageUrls"), list) else []) if (url := _safe_web_media_url(value))]
    generated_posts = [post for post in (result.get("posts") if isinstance(result.get("posts"), list) else []) if isinstance(post, dict)]
    if not preview_image and candidate_images:
        preview_image = candidate_images[0]
    if result.get("generatedCount") is not None:
        lines.extend(["", f"已生成：{_num(result.get('generatedCount'))} 篇"])
    if archive_id:
        lines.append(f"人設 ID：{archive_id}")
    if preview_image:
        lines.append("媒體：已返回並寫入人設歸檔")
    if published_url:
        lines.append(f"發布連結：{published_url}")
    result_rows: list[list[dict[str, str]]] = []
    task_source = "favorites" if str(task_input.get("postSource") or task_input.get("source") or "posts") == "favorites" else "posts"
    task_content_type = str(task_input.get("uiContentType") or "")
    task_page = max(0, _num(task_input.get("uiPage")))
    if status == "success" and archive_id:
        if task_type.startswith("persona_"):
            _PERSONA_MENU_CACHE.update({"at": 0.0, "rows": []})
            _refresh_persona_overview_cache(force_remote=True)
        if task_type == "persona_create":
            result_rows.extend(_rows([_btn("🎨 生成人設圖", f"genimg_{archive_id}")], [_btn("🧾 查看人設詳情", f"pd_{archive_id}")]))
        elif task_type == "persona_rewrite_intro":
            result_rows.extend(_rows([_btn("⚙️ 返回人設設定", f"settings_{archive_id}")]))
        elif task_type == "persona_generate_posts":
            generated_count = _num(result.get("generatedCount")) or len(generated_posts)
            lines = [f"✅ 推文生成完成：{generated_count} 篇"]
            used = len(lines[0])
            for index, post in enumerate(generated_posts, start=1):
                content = str(post.get("content") or "").strip()
                if not content:
                    continue
                block = f"\n\n【第{index}篇】\n{content}"
                if used + len(block) > 12000:
                    lines.extend(["", f"其餘 {len(generated_posts) - index + 1} 篇可在推文列表查看。"])
                    break
                lines.append(block)
                used += len(block)
            result_rows.extend(_rows([_btn("📝 查看推文列表", f"posts_{archive_id}_p0")], [_btn("🧾 返回人設詳情", f"pd_{archive_id}")]))
        elif task_type == "persona_generate_image":
            local, row = _resolve_persona_for_action(archive_id)
            name = local.name if local else _persona_row_name(row or {})
            lines = [f"✅ 已为人设「{name}」生成参考图"]
            if result.get("mode"):
                lines.append(f"模式：{result.get('mode')}")
            result_rows.extend(_rows([_btn("◀️ 返回人設詳情", f"pd_{archive_id}")], [_btn("◀️ 返回設定", f"settings_{archive_id}")]))
        elif task_type == "persona_generate_post_image" and post_id:
            generated_post_ids = [
                str(value or "").strip()
                for value in (task_input.get("uiGeneratedPostIds") if isinstance(task_input.get("uiGeneratedPostIds"), list) else [])
                if str(value or "").strip()
            ]
            generated_post_index = max(0, _num(task_input.get("uiPostIndex")))
            if candidate_images:
                title = (
                    f"🖼 第 {generated_post_index + 1}/{len(generated_post_ids)} 篇候選配圖（共 {len(candidate_images)} 張）"
                    if generated_post_ids
                    else "✅ 已生成推文候選配圖"
                )
                lines = [title, "", "請選擇要寫入推文的一張。"]
                result_rows.extend([
                    [_btn(f"✅ 選擇第 {index + 1} 張", f"pimgpick:{task_id}:{index}")]
                    for index in range(len(candidate_images))
                ])
                result_rows.extend(_rows([_btn("🔄 重新生成候選圖", _source_post_image_retry_callback(archive_id, post_id, source=task_source, content_type=task_content_type, page=task_page, post_index=_num(task_input.get("uiPostIndex"))))], [_btn("📋 返回推文列表", _source_posts_callback(archive_id, source=task_source, content_type=task_content_type, page=task_page))]))
            elif str(task_input.get("action") or "") == "select_candidate" and generated_post_ids:
                next_index = generated_post_index + 1
                if next_index >= len(generated_post_ids):
                    lines = [f"✅ 配圖選擇完成：{len(generated_post_ids)}/{len(generated_post_ids)} 篇"]
                    result_rows.extend(_rows(
                        [_btn("📝 查看推文列表", _source_posts_callback(archive_id, source=task_source, content_type=task_content_type, page=task_page))],
                        [_btn("◀️ 返回人設詳情", f"pd_{archive_id}")],
                    ))
                else:
                    lines = [
                        f"✅ 已選完第 {next_index}/{len(generated_post_ids)} 篇的配圖。",
                        "",
                        f"點擊下方按鈕生成第 {next_index + 1}/{len(generated_post_ids)} 組圖片。",
                    ]
                    result_rows.extend(_rows(
                        [_btn(f"🖼 生成第 {next_index + 1} 組圖片", f"source_genpost_image_next:{task_id}")],
                        [_btn("📝 查看推文列表", _source_posts_callback(archive_id, source=task_source, content_type=task_content_type, page=task_page))],
                    ))
            else:
                lines = ["✅ 已寫入推文配圖", "", "圖片已保存到同一篇 Tool R18 推文。"]
                result_rows.extend(_rows([_btn("📝 查看這篇推文", _source_post_detail_callback(archive_id, post_id, source=task_source, content_type=task_content_type, page=task_page))], [_btn("📋 返回推文列表", _source_posts_callback(archive_id, source=task_source, content_type=task_content_type, page=task_page))]))
        elif task_type == "persona_publish_post":
            lines = ["✅ 推文發布完成"] + (["", f"發布連結：{published_url}"] if published_url else [])
            if _num(result.get("publishedCount")) > 1:
                lines.extend(["", f"完成發布：{_num(result.get('publishedCount'))} 個推文/智能體手機組合"])
            result_rows.extend(_rows([_btn("🕘 查看發布歷史", f"history_{archive_id}")], [_btn("🧾 返回人設詳情", f"pd_{archive_id}")]))
        elif task_type == "persona_post_action":
            action_name = str(result.get("action") or task_input.get("action") or "")
            labels = {
                "regenerate_content": "✅ 推文已重新生成",
                "favorite": "⭐ 已收藏",
                "delete": "✅ 推文已刪除",
                "delete_many": "✅ 已刪除所選推文",
                "update_content": "✅ 自訂文案已保存",
                "refresh_metrics": "✅ 推文熱度已刷新",
                "delete_media": "✅ 已刪除所選媒體",
                "replace_media": "✅ 已替換所選媒體",
            }
            lines = [labels.get(action_name, "✅ 推文操作已完成")]
            if action_name == "favorite":
                lines.extend(["", f"當前收藏：{_num(result.get('favoriteCount'))} 篇"])
            if action_name in {"delete", "delete_many"}:
                lines.extend(["", f"剩餘：{_num(result.get('remaining'))} 篇"])
                result_rows.extend(_rows([_btn("◀️ 返回收藏推文" if task_source == "favorites" else "◀️ 返回推文列表", _source_posts_callback(archive_id, source=task_source, content_type=task_content_type, page=task_page))]))
            elif post_id:
                result_rows.extend(_rows(
                    [_btn("👁 返回查看推文", _source_post_detail_callback(archive_id, post_id, source=task_source, content_type=task_content_type, page=task_page))],
                    *([[_btn("⭐ 查看收藏推文", _source_posts_callback(archive_id, source="favorites"))]] if action_name == "favorite" else []),
                ))
    if status in {"queued", "running"}:
        result_rows.extend(_rows([_btn("🔄 刷新本次任務", f"source_task_detail:{task_id}")]))
        if archive_id:
            if post_id:
                result_rows.extend(_rows([_btn("◀️ 返回查看推文", _source_post_detail_callback(archive_id, post_id, source=task_source, content_type=task_content_type, page=task_page))]))
            else:
                result_rows.extend(_rows([_btn("◀️ 返回設定", f"settings_{archive_id}")]))
    if not (status == "success" and task_type.startswith("persona_")):
        result_rows.extend(_rows([_btn("任務列表", "source_tasks"), _btn("返回主選單", "back_main")]))
    response = _response(
        _message(
            "\n".join(lines),
            result_rows,
            image=preview_image,
            cards=[{"title": f"候選圖 {index + 1}", "image": url} for index, url in enumerate(candidate_images)],
        ),
        state={"flow": ""},
    )
    if status in {"queued", "running"}:
        response["poll"] = {"action": f"source_task_poll:{task_id}", "interval_ms": 2000}
    elif status == "success" and task_type == "persona_generate_posts" and task_input.get("uiTextOnly") is False and generated_posts:
        response["followup"] = {"action": f"source_genpost_image_start:{task_id}", "delay_ms": 700}
    return response


def _source_task_poll(task_id: str) -> dict[str, Any]:
    result = _source_task_detail(task_id)
    poll = result.get("poll") if isinstance(result.get("poll"), dict) else None
    if poll:
        return {"messages": [], "state": {"flow": ""}, "poll": poll}
    return result


def _source_generated_post_image_start(task_id: str) -> dict[str, Any]:
    try:
        _base, data = _source_task_detail_data(task_id)
    except Exception as exc:
        return _response(_message(f"讀取已生成推文失敗：{exc}", [[_btn("🔄 重新查看任務", f"source_task_detail:{task_id}")]]), state={"flow": ""})
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
    posts = [post for post in (result.get("posts") if isinstance(result.get("posts"), list) else []) if isinstance(post, dict)]
    archive_id = str(result.get("archiveId") or task_input.get("archiveId") or "").strip()
    post_id = str((posts[0] if posts else {}).get("id") or "").strip()
    if str(task.get("status") or "").lower() != "success" or not archive_id or not post_id:
        return _response(
            _message("推文已生成，但找不到可開始配圖的推文。", [[_btn("📝 查看推文列表", f"posts_{archive_id}_p0")]]),
            state={"flow": ""},
        )
    params = {
        "archiveId": archive_id,
        "postId": post_id,
        "action": "generate_candidates",
        "chatId": SOURCE_WEB_BOT_CHAT_ID,
        "imageAspectRatio": str(task_input.get("uiImageAspectRatio") or ""),
        "imageWidth": _num(task_input.get("uiImageWidth")),
        "imageHeight": _num(task_input.get("uiImageHeight")),
        "imageRatioLabel": str(task_input.get("uiImageRatioLabel") or ""),
        "postSource": "posts",
        "uiPage": 0,
        "uiPostIndex": 0,
        "uiGeneratedPostIds": [str(post.get("id") or "") for post in posts if str(post.get("id") or "").strip()],
    }
    return _submit_source_post_task("persona_generate_post_image", archive_id, post_id, params, "推文配圖任務")


def _source_generated_post_image_next(task_id: str) -> dict[str, Any]:
    try:
        _base, data = _source_task_detail_data(task_id)
    except Exception as exc:
        return _response(_message(f"讀取配圖進度失敗：{exc}", [[_btn("🔄 重新查看任務", f"source_task_detail:{task_id}")]]), state={"flow": ""})
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    archive_id = str(result.get("archiveId") or task_input.get("archiveId") or "").strip()
    generated_post_ids = [
        str(value or "").strip()
        for value in (task_input.get("uiGeneratedPostIds") if isinstance(task_input.get("uiGeneratedPostIds"), list) else [])
        if str(value or "").strip()
    ]
    next_index = max(0, _num(task_input.get("uiPostIndex"))) + 1
    if str(task.get("status") or "").lower() != "success" or not archive_id or next_index >= len(generated_post_ids):
        return _response(
            _message(
                f"✅ 配圖選擇完成：{len(generated_post_ids)}/{len(generated_post_ids)} 篇" if generated_post_ids else "配圖分組狀態已失效，請重新生成推文。",
                _rows([_btn("📝 查看推文列表", _source_posts_callback(archive_id))], [_btn("◀️ 返回人設詳情", f"pd_{archive_id}")]),
            ),
            state={"flow": ""},
        )
    post_id = generated_post_ids[next_index]
    params = {
        "archiveId": archive_id,
        "postId": post_id,
        "action": "generate_candidates",
        "chatId": SOURCE_WEB_BOT_CHAT_ID,
        "imageAspectRatio": str(task_input.get("imageAspectRatio") or task_input.get("uiImageAspectRatio") or ""),
        "imageWidth": _num(task_input.get("imageWidth") or task_input.get("uiImageWidth")),
        "imageHeight": _num(task_input.get("imageHeight") or task_input.get("uiImageHeight")),
        "imageRatioLabel": str(task_input.get("imageRatioLabel") or task_input.get("uiImageRatioLabel") or ""),
        "postSource": "posts",
        "uiPage": 0,
        "uiPostIndex": next_index,
        "uiGeneratedPostIds": generated_post_ids,
    }
    return _submit_source_post_task("persona_generate_post_image", archive_id, post_id, params, "推文配圖任務")


def _source_cancel_latest() -> dict[str, Any]:
    try:
        _base, data = _source_cancel_latest_data()
    except Exception as exc:
        return _response(_message(f"❌ 停止来源任务失败\n\n{exc}", [[_btn("来源状态", "source_status")]]), state={"flow": ""})
    if data.get("cancelled") is True:
        task_id = str(data.get("id") or data.get("task_id") or "").strip()
        typ = str(data.get("type") or data.get("task_type") or "来源任务").strip()
        text = "\n".join(
            [
                "🛑 已强制停止来源后台任务。",
                "",
                f"工作流：{typ}",
                f"任务编号：{task_id or '-'}",
                "如果远端已经开始推理，远端可能仍会跑完，但本地不会再把结果当作完成任务推送。",
            ]
        )
    else:
        text = str(data.get("message") or "").strip()
        if not text:
            latest = data.get("latest") if isinstance(data.get("latest"), dict) else {}
            text = f"目前没有可强制停止的来源后台任务。最近任务：{latest.get('id') or '-'} / {_source_task_label(latest)}"
    return _response(
        _message(
            text,
            _rows([_btn("来源状态", "source_status"), _btn("任务列表", "source_tasks")], [_btn("返回主选单", "menu")]),
        ),
        state={"flow": ""},
    )


def _source_runtime_config_menu() -> dict[str, Any]:
    try:
        base, runtime = _source_runtime_config_data()
    except Exception as exc:
        return _response(_message(f"❌ 读取来源工作流配置失败\n\n{exc}", [[_btn("返回主选单", "menu")]]), state={"flow": ""})
    interesting = [
        "create_video_app_id",
        "commerce_video_app_id",
        "video_i2v_app_id",
        "image_generate_app_id",
        "single_image_edit_app_id",
        "get_nano_banana_app_id",
        "face_swap_app_id",
        "replace_model_original_app_id",
        "replace_product_app_id",
        "replace_productANDmodel_app_id",
        "text_to_image_auto_qa_enabled",
    ]
    lines = ["来源后台工作流配置", "", f"API：{base}", ""]
    source = str(runtime.get("comfy_workflow_source") or "remote").strip().lower()
    mappings_key = "local_comfy_workflow_mappings" if source == "local" else "remote_comfy_workflow_mappings"
    mappings = runtime.get(mappings_key) if isinstance(runtime.get(mappings_key), dict) else {}
    if mappings:
        lines.append(f"工作流来源：{source}")
        mapping_labels = [
            ("text_to_image", "文生图"),
            ("image_generate", "图像生成"),
            ("single_image_edit", "单图编辑"),
            ("get_nano_banana", "图片编辑"),
            ("face_swap", "人物换脸"),
            ("video_i2v", "图生视频"),
            ("create_video", "数字人视频"),
            ("replace_model", "视频模特替换"),
            ("replace_product", "视频商品替换"),
            ("replace_productANDmodel", "联合替换"),
        ]
        for key, label in mapping_labels:
            value = mappings.get(key)
            if value is None and key == "single_image_edit":
                value = mappings.get("get_nano_banana")
            if isinstance(value, dict):
                value = value.get("workflow") or value.get("path") or value.get("value")
            if isinstance(value, list):
                cleaned = [_source_clean_workflow_id(item) for item in value]
                shown = " > ".join(item for item in cleaned if item)
            else:
                shown = _source_clean_workflow_id(value)
            if shown:
                lines.append(f"{label}: {shown}")
        lines.append("")
    chain_labels = [
        ("oral_digital_human_workflow_ids", "口播数字人链"),
        ("image_generate_workflow_ids", "图像生成链"),
        ("replace_model_original_workflow_ids", "模特替换链"),
        ("replace_product_workflow_ids", "商品替换链"),
    ]
    for key, label in chain_labels:
        value = runtime.get(key)
        if isinstance(value, list) and value:
            lines.append(f"{label}: {' > '.join(_source_clean_workflow_id(item) for item in value if _source_clean_workflow_id(item))}")
    for key in interesting:
        value = runtime.get(key)
        if value not in (None, "", [], {}):
            lines.append(f"{key}: {value}")
    if len(lines) <= 4:
        lines.append("已连接，但来源 runtime_config 没有返回常用工作流键。")
    return _response(
        _message(
            "\n".join(lines[:32]),
            _rows([_btn("来源状态", "source_status"), _btn("任务列表", "source_tasks")], [_btn("返回主选单", "menu")]),
        ),
        state={"flow": ""},
    )


def _source_workbench_open() -> dict[str, Any]:
    try:
        base, _runtime = _source_runtime_config_data()
    except Exception:
        base = _source_api_candidates()[0]
    return _response(
        _message(
            f"已打开来源工作台：{base}",
            _rows([_btn("来源状态", "source_status"), _btn("返回主选单", "menu")]),
        ),
        state={"flow": ""},
        open_url=base,
    )


def _source_submit_agent_action(label: str, message_text: str) -> dict[str, Any]:
    job = SourceWorkflowJobRepo.create("agent_submit", label, {"message": message_text}, status="submitting")
    try:
        base, data = _source_agent_submit(message_text)
        SourceWorkflowJobRepo.update(
            job.id,
            status="submitted" if data.get("submitted", True) else "chat",
            result=data,
            source_task_id=str(data.get("id") or ""),
            source_base_url=base,
        )
    except Exception as exc:
        SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
        return _response(_message(f"❌ {label} 失败\n\n{exc}", [[_btn("来源状态", "source_status"), _btn("返回主选单", "menu")]]), state={"flow": ""})
    body = str(data.get("reply") or data.get("summary") or data.get("prompt_preview") or "").strip()
    text = "\n".join(
        [
            f"✅ {label} 已送到来源智能体",
            "",
            f"API：{base}",
            f"任务类型：{data.get('task_type') or '-'}",
            f"任务 ID：{data.get('id') or '-'}",
            "",
            body[:700],
        ]
    ).strip()
    return _response(
        _message(text, _rows([_btn("来源状态", "source_status"), _btn("任务列表", "source_tasks")], [_btn("返回主选单", "menu")])),
        state={"flow": ""},
    )


def _source_latest_task_id(task_types: set[str] | None = None) -> str:
    _base, tasks = _source_tasks(limit=20)
    for task in tasks:
        typ = str(task.get("type") or "").strip()
        if not task_types or typ in task_types:
            return str(task.get("id") or "").strip()
    return ""


def _source_rerun_task(task_id: str, *, label: str = "重跑来源任务", transform: str = "") -> dict[str, Any]:
    try:
        _base, detail = _source_task_detail_data(task_id)
        task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
        task_type = str(task.get("type") or "").strip()
        params = task.get("input") if isinstance(task.get("input"), dict) else {}
        params = dict(params)
        params.pop("source", None)
        params.pop("tg_chat_id", None)
        if transform == "continue_text_to_image":
            prompt = str(params.get("prompt") or params.get("prompt_text") or params.get("message") or "").strip()
            params["prompt"] = f"{prompt}\n继续生成同一主题的下一组图片，保持风格但避免完全重复。".strip()
            params["prompt_text"] = params["prompt"]
            seed = _num(params.get("seed") or params.get("random_seed"))
            if seed > 0:
                params["seed"] = seed + 1
        job = SourceWorkflowJobRepo.create(task_type, label, params, status="submitting")
        base, data = _source_submit_task(task_type, params)
        SourceWorkflowJobRepo.update(job.id, status="submitted", result=data, source_task_id=str(data.get("id") or ""), source_base_url=base)
    except Exception as exc:
        return _response(_message(f"❌ {label} 失败\n\n{exc}", [[_btn("任务列表", "source_tasks"), _btn("返回主选单", "menu")]]), state={"flow": ""})
    return _response(
        _message(
            f"✅ {label} 已提交\n\n来源任务 ID：{data.get('id')}\n任务类型：{data.get('task_type') or task_type}",
            _rows([_btn("来源状态", "source_status"), _btn("任务列表", "source_tasks")], [_btn("返回主选单", "menu")]),
        ),
        state={"flow": ""},
    )


def _source_rerun_latest(task_types: set[str] | None = None, *, label: str = "重跑最近任务", transform: str = "") -> dict[str, Any]:
    try:
        task_id = _source_latest_task_id(task_types)
    except Exception as exc:
        return _response(_message(f"❌ 读取最近来源任务失败\n\n{exc}", [[_btn("来源状态", "source_status")]]), state={"flow": ""})
    if not task_id:
        return _response(_message("还没有可重跑的来源任务。", [[_btn("任务列表", "source_tasks"), _btn("返回主选单", "menu")]]), state={"flow": ""})
    return _source_rerun_task(task_id, label=label, transform=transform)


def _image_menu() -> dict[str, Any]:
    text = "\n".join(
        [
            "🖼 AI 图像工作流",
            "",
            "已按来源 Telegram Bot 搬入以下入口：文生图、单图编辑、多图编辑、人物换脸、图片替换、继续/重跑最近任务。",
            "",
            "请选择要建立的任务。",
        ]
    )
    keyboard = _rows(
        [_btn("文生图", "text_to_image"), _btn("单图编辑", "single_image_edit")],
        [_btn("图像编辑", "image_edit"), _btn("人物换脸", "face_swap")],
        [_btn("多图合成", "multi_image"), _btn("图片替换", "image_replace")],
        [_btn("继续生成图片", "text_to_image_continue"), _btn("重跑最近任务", "rerun_latest")],
        [_btn("查看后台工作流配置", "workflow_config"), _btn("返回主选单", "menu")],
    )
    return _response(_message(text, keyboard), state={"flow": ""})


def _text_to_image_start() -> dict[str, Any]:
    text = "\n".join(
        [
            "文生图设置",
            "当前步骤：1/4 请选择图像比例",
            "",
            "请选择画面比例；下一步会选择最终分辨率、人设 LoRA 与提示词方式。",
        ]
    )
    keyboard = _rows(
        [_btn("2:3 基礎豎圖", "t2i_ratio:2:3"), _btn("3:4 穩定豎圖", "t2i_ratio:3:4")],
        [_btn("9:16 手機全屏長圖", "t2i_ratio:9:16"), _btn("1:1 正方形配圖", "t2i_ratio:1:1")],
        [_btn("3:2 橫向基準圖", "t2i_ratio:3:2"), _btn("4:3 平衡橫圖", "t2i_ratio:4:3")],
        [_btn("16:9 寬屏視頻比例圖", "t2i_ratio:16:9")],
        [_btn("☑️ QA 審查：關閉", "t2i_qa_toggle"), _btn("返回主選單", "menu")],
    )
    return _response(_message(text, keyboard), state={"flow": "t2i_ratio", "draft": {"qa": False}})


def _text_to_image_action(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    if action.startswith("t2i_ratio:"):
        ratio = action.split(":", 1)[1]
        draft["ratio"] = ratio
        text = "\n".join(
            [
                "文生图设置",
                "当前步骤：2/4 请选择最终分辨率",
                f"画面比例：{ratio}",
                "",
                "请选择使用基础分辨率或开启最终分辨率。",
            ]
        )
        return _response(
            _message(
                text,
                _rows(
                    [_btn("使用基础分辨率", "t2i_final:off")],
                    [_btn("开启最终分辨率", "t2i_final:on")],
                    [_btn("上一步", "text_to_image"), _btn("返回主选单", "menu")],
                ),
            ),
            state={"flow": "t2i_final", "draft": draft},
        )
    if action.startswith("t2i_final:"):
        draft["final_resolution"] = action.endswith(":on")
        personas = PersonaRepo.list_all(limit=8)
        buttons = [_btn(persona.name[:20], f"t2i_persona:{persona.id}") for persona in personas]
        keyboard = _chunk_buttons(buttons, 1)
        keyboard.extend(_rows([_btn("不使用人设", "t2i_persona:off")], [_btn("上一步", "text_to_image"), _btn("返回主选单", "menu")]))
        return _response(
            _message("文生图设置\n当前步骤：3/4 请选择人设 LoRA", keyboard),
            state={"flow": "t2i_persona", "draft": draft},
        )
    if action.startswith("t2i_persona:"):
        draft["persona_id"] = "" if action.endswith(":off") else action.split(":", 1)[1]
        return _response(
            _message(
                "文生图设置\n当前步骤：4/4 请选择提示词方式\n\n请选择让 Grok 根据你的需求生成提示词，或直接输入自定义最终提示词。",
                _rows(
                    [_btn("让 Grok 生成提示词", "t2i_prompt_mode:grok")],
                    [_btn("输入自定义提示词", "t2i_prompt_mode:custom")],
                    [_btn("上一步", "text_to_image"), _btn("返回主选单", "menu")],
                ),
            ),
            state={"flow": "t2i_prompt_mode", "draft": draft},
        )
    if action.startswith("t2i_prompt_mode:"):
        draft["prompt_mode"] = action.split(":", 1)[1]
        return _response(
            _message(
                "文生图设置\n当前步骤：请输入图片需求或上传参考图\n\n请直接输入图片需求。Web 操作台会保留本次参数，并生成与 Telegram Bot 一样的确认页。",
                _rows([_btn("上一步", "text_to_image"), _btn("返回主选单", "menu")]),
            ),
            state={"flow": "t2i_prompt", "draft": draft},
        )
    return _text_to_image_start()


def _video_menu() -> dict[str, Any]:
    text = "\n".join(
        [
            "🎬 视频/数字人",
            "",
            "请选择要建立的任务。",
            "",
            "来源 Telegram 功能包含：图生视频、数字人视频、视频编辑、模特替换、商品替换、联合替换。",
        ]
    )
    keyboard = _rows(
        [_btn("图生视频", "video_i2v"), _btn("数字人视频生成", "digital_human")],
        [_btn("视频编辑", "video_edit"), _btn("视频模特替换", "replace_model")],
        [_btn("视频商品替换", "replace_product"), _btn("联合替换工作流", "replace_union")],
        [_btn("多智能体数字人", "multi_agent_digital_human"), _btn("返回主选单", "menu")],
    )
    return _response(_message(text, keyboard), state={"flow": ""})


def _video_i2v_start() -> dict[str, Any]:
    return _response(
        _message(
            "视频生成设置\n当前步骤：1/5 选择分辨率",
            _rows([_btn("720p（最小资源）", "v2v_resolution:720p"), _btn("1080p", "v2v_resolution:1080p")], [_btn("返回主菜单", "menu")]),
        ),
        state={"flow": "video_resolution", "draft": {}},
    )


def _video_action(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    if action.startswith("v2v_resolution:"):
        draft["resolution"] = action.split(":", 1)[1]
        return _response(
            _message(
                "视频生成设置\n当前步骤：2/5 输入视频时长\n\n请直接输入视频时长，范围 2 到 15 秒，例如：5。",
                [[_btn("上一步", "video_i2v"), _btn("返回主菜单", "menu")]],
            ),
            state={"flow": "video_duration", "draft": draft},
        )
    return _video_i2v_start()


def _feature_shell(title: str, body: str, *, back: str = "menu") -> dict[str, Any]:
    text = "\n".join([title, "", body, "", f"来源：{SOURCE_ROOT}"])
    return _response(
        _message(
            text,
            _rows(
                [_btn("开始填写参数", f"source_workflow_start:{title}")],
                [_btn("查看后台工作流配置", "workflow_config"), _btn("返回", back)],
            ),
        ),
        state={"flow": ""},
    )


TG_CAPABILITY_GROUPS: list[tuple[str, list[str]]] = [
    ("一、人設管理", ["新建人設", "查看人設圖", "AI 重新生成人設圖", "刪除人設", "矩陣多機分發", "修改名稱（60 天冷卻提示）", "修改人設簡介", "推文風格配置", "連結模板設定", "綁定智能體手機", "R18 既有人設免費/付費通道"]),
    ("二、推文內容管理", ["待發布推文", "收藏推文/收藏記憶", "查看推文", "查看/收藏推文", "發布歷史", "重新回庫", "純文字推文", "推文+配圖", "自訂圖文/影片素材", "熱點抓取推文", "生成記憶", "自訂記憶", "不指定記憶"]),
    ("三、多媒體素材生成", ["文生圖：2:3、3:4、9:16、3:2、4:3、16:9、1:1", "單圖編輯", "圖片通用編輯", "人物換臉", "圖生視頻 720p", "圖生視頻 1080p"]),
    ("四、帳號管理", ["綁定/更換雲機", "切換 VMOS 登入帳號提示", "Threads 資料維護", "Telegram 登入憑證", "清除 TG 本地憑證", "TG 通用群組綁定", "子帳號/操作員權限"]),
    ("五、自動化運營", ["自動回覆評論", "自動回覆熱點推文", "固定文案回覆", "AI 依人設回覆", "養號：滑動瀏覽", "養號：滑動+點讚", "養號：滑動+留言", "養號：全套操作"]),
    ("六、矩陣發布", ["多台雲機批量發布", "單條推文一鍵發布", "R18 免費/付費素材分流"]),
]


def _capabilities_menu() -> dict[str, Any]:
    jobs = SourceWorkflowJobRepo.list_all(limit=5)
    job_lines = ["最近來源/腳本任務："]
    if jobs:
        for job in jobs:
            job_lines.append(f"#{job.id[:6]}｜{job.status}｜{job.label}")
    else:
        job_lines.append("尚無來源任務。")
    lines = [
        "🧭 TG 全功能操作台",
        "",
        "以下入口對照 D:\\workflow_delivery_package_source 的 Telegram Bot 操作，已集中到智能體小控制台。",
        "",
    ]
    for group, items in TG_CAPABILITY_GROUPS:
        lines.append(group)
        lines.append("、".join(items))
        lines.append("")
    lines.extend(job_lines)
    keyboard = _rows(
        [_btn("👤 人設管理", "list_personas"), _btn("🚀 矩陣發布", "matrix_start")],
        [_btn("📚 推文素材庫", "post_library_global"), _btn("🤖 自動化運營", "automation_global")],
        [_btn("🖼 多媒體生成", "image_menu"), _btn("🎬 影片/數字人", "video_menu")],
        [_btn("🔐 帳號/TG 設定", "accounts_console"), _btn("📱 智能體手機", "pad_mgmt")],
        [_btn("📊 腳本/來源任務", "local_jobs"), _btn("⚙️ 後台工作流", "workflow_config")],
        [_btn("返回主選單", "menu")],
    )
    return _response(_message("\n".join(lines), keyboard), state={"flow": ""})


def _workflow_config() -> dict[str, Any]:
    text = "\n".join(
        [
            "查看后台工作流配置",
            "",
            "已移入的 Telegram 操作入口：",
            "1. 人設管理 / 新建 / 查看人設圖 / 重新生成 / 刪除 / R18 分流",
            "2. 推文素材庫 / 待發布 / 收藏 / 發布歷史 / 重新回庫",
            "3. 新建推文 / 記憶顆粒 / 熱點輿情 / 逐篇配圖 / 矩陣發布",
            "4. 文生圖 / 單圖編輯 / 圖片通用編輯 / 人物換臉 / 圖生視頻 720p/1080p",
            "5. 帳號管理 / Threads 資料 / Telegram 憑證 / TG 通用群 / 子帳號",
            "6. 自動回覆 / 熱點回覆 / 養號滑動點讚留言",
            "",
            f"来源目录：{SOURCE_ROOT}",
        ]
    )
    return _response(_message(text, [[_btn("🧭 TG 全功能", "capabilities"), _btn("返回主選單", "menu")]]), state={"flow": ""})


def _image_menu() -> dict[str, Any]:
    text = "\n".join(
        [
            "AI 图像工作流",
            "",
            "这些入口对照 D:\\workflow_delivery_package_source 的 Telegram 功能，参数填写完会直接提交来源 internal TG 工作流。",
            "",
            "请选择要执行的功能。",
        ]
    )
    keyboard = _rows(
        [_btn("文生图", "text_to_image"), _btn("单图编辑", "single_image_edit")],
        [_btn("图片编辑", "image_edit"), _btn("人物换脸", "face_swap")],
        [_btn("多图生成", "multi_image"), _btn("图片替换", "image_replace")],
        [_btn("继续生成图片", "text_to_image_continue"), _btn("重新生成图片", "text_to_image_rerun")],
        [_btn("继续编辑结果图", "image_edit_continue"), _btn("重新生成图片编辑", "image_edit_rerun")],
        [_btn("增加解析度 2 倍", "face_swap_upscale"), _btn("重新生成人物换脸", "face_swap_rerun")],
        [_btn("来源任务状态", "source_status"), _btn("后台工作流配置", "source_runtime_config")],
        [_btn("来源工作台网址", "source_workbench")],
        [_btn("返回主选单", "menu")],
    )
    return _response(_message(text, keyboard), state={"flow": ""})


def _video_menu() -> dict[str, Any]:
    text = "\n".join(
        [
            "视频 / 数字人工作流",
            "",
            "这些入口对照来源 Telegram 的视频、数字人和替换流程。",
            "",
            "请选择要执行的功能。",
        ]
    )
    keyboard = _rows(
        [_btn("图生视频", "video_i2v"), _btn("视频生成", "video_edit")],
        [_btn("数字人视频生成", "digital_human"), _btn("写实带货视频", "digital_human_realistic")],
        [_btn("直播口播视频", "digital_human_live"), _btn("产品展示视频", "digital_human_product")],
        [_btn("自定义数字人要求", "digital_human_custom"), _btn("生成口播音频", "create_audio")],
        [_btn("视频模特替换", "replace_model"), _btn("视频商品替换", "replace_product")],
        [_btn("联合替换工作流", "replace_union"), _btn("智能体生产入口", "multi_agent_digital_human")],
        [_btn("来源任务状态", "source_status"), _btn("后台工作流配置", "source_runtime_config")],
        [_btn("来源工作台网址", "source_workbench")],
        [_btn("返回主选单", "menu")],
    )
    return _response(_message(text, keyboard), state={"flow": ""})


def _start_generate_posts(persona_id: str) -> dict[str, Any]:
    local, row = _resolve_persona_for_action(persona_id)
    if not local:
        return _response(_message("没有找到这个人设，不能生成推文。", [[_btn("◀️ 返回人设列表", "list_personas")]]), state={"flow": ""})
    persona_id = local.id
    if local:
        name = _persona_row_name(row) if row and _is_placeholder_persona_name(local.name, local.pad_code) else _local_persona_display_name(local)
    else:
        name = _persona_row_name(row or {})
    messages: list[dict[str, Any]] = []
    if local and not _avatar_exists(local):
        messages.append(
            _message(
                f"🎨 「{name}」還沒有人設圖。你可以先繼續生成文字推文，或返回人設設定單獨生成人設圖。",
                [[_btn("🎨 生成人設圖", f"genimg_{persona_id}")], [_btn("◀️ 返回人設詳情", f"pd_{persona_id}")]],
                kind="status",
            )
        )
    messages.append(_genpost_memory_prompt(persona_id, name))
    return _response(messages, state={"flow": "genpost_memory", "draft": {"persona_id": persona_id, "name": name, "memory": ""}})
    if local and not _avatar_exists(local):
        messages.append(_message(f"🎨 「{name}」还没有人设图，正在先生成参考图...", kind="status"))
        try:
            path = _generate_persona_reference_image(local)
            local = PersonaRepo.get(persona_id) or local
            messages.append(_persona_image_message(local, path))
        except Exception as exc:
            messages.append(
                _message(
                    f"人设图生成失败，仍可先继续生成纯文字推文：{exc}",
                    [[_btn("🔄 重新生成人设图", f"regenimg:{persona_id}")]],
                )
            )
    messages.append(_genpost_memory_prompt(persona_id, name))
    return _response(messages, state={"flow": "genpost_memory", "draft": {"persona_id": persona_id, "name": name, "memory": ""}})


def _is_workflow_persona_row(row: dict[str, Any] | None, persona_id: str = "") -> bool:
    if str(persona_id or "").startswith("workflow-persona-"):
        return True
    if not isinstance(row, dict):
        return False
    for key in ("source", "kind", "type", "runtime_type"):
        if "workflow" in str(row.get(key) or "").lower():
            return True
    return bool(row.get("archive_id") or row.get("workflow_id") or row.get("source_path"))


def _genpost_branch_label(branch: str) -> str:
    if branch == "nonr18":
        return "免費群內容"
    if branch == "r18":
        return "付費群內容"
    return "未指定"


def _genpost_branch_token(branch: str) -> str:
    return "r" if branch == "r18" else "n" if branch == "nonr18" else "x"


def _genpost_branch_from_token(token: str) -> str:
    return "r18" if token == "r" else "nonr18" if token == "n" else ""


def _genpost_context(persona_id: str) -> tuple[str, Persona | None, dict[str, Any] | None, str]:
    local, row = _resolve_persona_for_action(persona_id)
    if local:
        persona_id = local.id
        name = _persona_row_name(row) if row and _is_placeholder_persona_name(local.name, local.pad_code) else _local_persona_display_name(local)
    elif row:
        name = _persona_row_name(row)
    else:
        name = str(persona_id or "人設")
    return persona_id, local, row, name


def _tool_r18_archive_id(persona_id: str, local: Persona | None, row: dict[str, Any] | None) -> str:
    row_id = str((row or {}).get("id") or "").strip()
    row_source_id = str(row.get("source_archive_id") or "").strip() if row else ""
    if row_source_id:
        return row_source_id[len("source:") :].strip() if row_source_id.startswith("source:") else row_source_id
    source_id = str(local.source_archive_id or "").strip() if local else ""
    if source_id.startswith("source:") and (not row_id or (local and row_id == local.id)):
        return source_id[len("source:") :].strip()
    if row_id:
        return row_id
    if str(persona_id or "").startswith("workflow-persona-"):
        return str(persona_id).strip()
    return ""


def _genpost_content_counts(row: dict[str, Any] | None) -> tuple[int, int]:
    counts = row.get("counts") if isinstance(row, dict) and isinstance(row.get("counts"), dict) else {}
    free = _num(counts.get("free") or counts.get("nonr18") or counts.get("free_posts") or counts.get("freePostCount"))
    paid = _num(counts.get("paid") or counts.get("r18") or counts.get("paid_posts") or counts.get("paidPostCount"))
    if not free and not paid:
        total = _source_count(row, "posts")
        free = total
    return free, paid


def _genpost_branch_picker(persona_id: str) -> dict[str, Any]:
    persona_id, local, row, name = _genpost_context(persona_id)
    if not local and not row:
        return _response(_message("找不到這個人設，無法新建推文。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    free, paid = _genpost_content_counts(row)
    text = "\n".join(["✍️ 新建推文", "", f"人設：{name}", "請先選擇本次要生成的內容類型："])
    return _response(
        _message(
            text,
            _rows(
                [_btn(f"免費內容（{free}）", f"genpost_nonr18_{persona_id}"), _btn(f"付費內容（{paid}）", f"genpost_r18_{persona_id}")],
                [_btn("◀️ 返回人設詳情", f"pd_{persona_id}")],
            ),
        ),
        state={"flow": "", "draft": {"persona_id": persona_id, "name": name}},
    )


def _genpost_mode_picker(persona_id: str, content_branch: str = "") -> dict[str, Any]:
    persona_id, local, row, name = _genpost_context(persona_id)
    if not local and not row:
        return _response(_message("找不到這個人設，無法新建推文。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    free, paid = _genpost_content_counts(row)
    pending_count = paid if content_branch == "r18" else free if content_branch == "nonr18" else _source_count(row, "posts")
    lines = ["✍️ 新建推文", "", f"人設：{name}"]
    if content_branch:
        lines.append(f"內容類型：{_genpost_branch_label(content_branch)}")
    lines.extend([f"目前待發布：{pending_count} 篇", "", "請選擇生成模式："])
    token = _genpost_branch_token(content_branch)
    back_action = f"genpost_branch_{persona_id}" if content_branch else f"pd_{persona_id}"
    back_label = "◀️ 返回內容類型" if content_branch else "◀️ 返回人設詳情"
    return _response(
        _message(
            "\n".join(lines),
            _rows(
                [_btn("📝 只生成推文（不配圖）", f"gpm_{persona_id}_t_{token}")],
                [_btn("🖼 生成推文+配圖/視頻", f"gpm_{persona_id}_i_{token}")],
                [_btn("🧩 自訂新建（文字/圖片/視頻）", f"genpost_custom_{persona_id}_ct_{content_branch or 'default'}")],
                [_btn("🔥 热点抓取", f"gph_{token}_{persona_id}")],
                [_btn(back_label, back_action)],
            ),
        ),
        state={"flow": "", "draft": {"persona_id": persona_id, "name": name, "content_branch": content_branch}},
    )


def _is_auto_imported_hot_memory(summary: Any) -> bool:
    text = re.sub(r"\s+", " ", str(summary or "")).strip().lower()
    return bool(
        re.search(r"(?:舆情热点素材|輿情熱點素材|热点素材|熱點素材)\s*\|\s*平台[:：]?\s*(?:threads|instagram)", text, re.I)
        or re.search(r"平台[:：]?\s*(?:threads|instagram)\s*\|\s*(?:数据|數據)[:：]?", text, re.I)
    )


def _genpost_memory_options(persona_id: str) -> list[dict[str, Any]]:
    local, row = _resolve_persona_for_action(persona_id)
    row = _fresh_persona_row(persona_id, local, row)
    source_entries = row.get("memory_entries") if isinstance(row, dict) and isinstance(row.get("memory_entries"), list) else []
    options = [
        {
            "id": str(item.get("id") or ""),
            "title": str(item.get("summary") or ""),
            "content": str(item.get("content") or item.get("summary") or ""),
            "granularity": str(item.get("kind") or "post"),
            "memory_date": str(item.get("date") or ""),
            "source": "tool_r18",
        }
        for item in sorted(source_entries, key=lambda value: str(value.get("date") or "") if isinstance(value, dict) else "", reverse=True)
        if (
            isinstance(item, dict)
            and str(item.get("id") or "").strip()
            and str(item.get("summary") or "").strip()
            and not _is_auto_imported_hot_memory(item.get("summary"))
        )
    ]
    if options:
        return options[:100]
    return [
        {
            "id": memory.id,
            "title": memory.title,
            "content": memory.content,
            "granularity": memory.granularity,
            "memory_date": memory.memory_date,
            "source": "web",
        }
        for memory in PostMemoryRepo.list_for_persona(persona_id, limit=80)
        if memory.source_type != LINK_ENDING_SOURCE_TYPE
    ]


def _genpost_memory_selection(draft: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(draft.get("persona_id") or "")
    name = str(draft.get("name") or persona_id or "人設")
    content_branch = str(draft.get("content_branch") or "")
    page = max(0, _num(draft.get("memory_page")))
    options = draft.get("memory_options")
    if not isinstance(options, list):
        options = _genpost_memory_options(persona_id)
        draft["memory_options"] = options
    selected = {str(item) for item in draft.get("selected_memory_entry_ids", [])}
    page_size = 10
    total_pages = max(1, (len(options) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    draft["memory_page"] = page
    visible = options[page * page_size : (page + 1) * page_size]
    mode_label = "只生成推文（不配圖）" if draft.get("text_only") else "生成推文+配圖/視頻"
    lines = [
        "🧠 選擇本次參考的人設記憶",
        "",
        f"人設：{name}",
        f"模式：{mode_label}",
        f"可選記憶：{len(options)} 條",
        f"已選：{len(selected)} 條",
        "",
        "勾選後，本輪生成的推文會圍繞這些記憶自然延展；也可以跳過。",
    ]
    keyboard: list[list[dict[str, str]]] = []
    for absolute_index, item in enumerate(visible, start=page * page_size):
        mark = "✅" if str(item.get("id") or absolute_index) in selected else "☐"
        date = str(item.get("memory_date") or "")[:10]
        summary = _memory_excerpt(item.get("content") or item.get("title"), 44)
        keyboard.append([_btn(f"{mark} {absolute_index + 1}. {date} {summary}".strip(), f"genmem_toggle_{absolute_index}")])
    if total_pages > 1:
        keyboard.append(
            [
                _btn("◀️ 上一頁", f"genmem_page_{max(0, page - 1)}"),
                _btn(f"{page + 1}/{total_pages}", f"genmem_page_{page}"),
                _btn("下一頁 ▶️", f"genmem_page_{min(total_pages - 1, page + 1)}"),
            ]
        )
    if options:
        all_ids = {str(item.get("id") or index) for index, item in enumerate(options)}
        keyboard.append([_btn("☐ 取消全選" if selected == all_ids else "✅ 全選記憶", "genmem_select_all")])
    if selected:
        keyboard.append([_btn("🗑 刪除已選記憶", "genmem_delete_selected")])
    keyboard.extend(
        _rows(
            [_btn("➕ 添加自定義記憶", "genmem_add_custom")],
            [_btn("✅ 使用已選記憶", "genmem_done")],
            [_btn("⏭ 不指定記憶", "genmem_skip")],
            [_btn("◀️ 返回生成模式", f"genpost_{'r18' if content_branch == 'r18' else 'nonr18' if content_branch == 'nonr18' else 'branch'}_{persona_id}")],
        )
    )
    return _response(_message("\n".join(lines), keyboard), state={"flow": "genpost_tg_memory", "draft": draft})


def _genpost_time_slot_picker(draft: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(draft.get("persona_id") or "")
    name = str(draft.get("name") or persona_id or "人設")
    return _response(
        _message(
            "\n".join(["✍️ 新建推文", "", f"人設：{name}", "內容類型：免費群內容", "", "請選擇本次要生成早上文案還是晚上文案。"]),
            _rows(
                [_btn("早上文案", "genmem_time_morning"), _btn("晚上文案", "genmem_time_night")],
                [_btn("◀️ 返回群內容類型", "genmem_time_back")],
            ),
        ),
        state={"flow": "genpost_time_slot", "draft": draft},
    )


def _genpost_paid_entry_picker(draft: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(draft.get("persona_id") or "")
    name = str(draft.get("name") or persona_id or "人設")
    return _response(
        _message(
            "\n".join(["✍️ 新建推文", "", f"人設：{name}", "內容類型：付費群內容", "", "請選擇付費內容生成入口："]),
            _rows(
                [_btn("圖片內容", "paidr18_group_image"), _btn("圖生視頻", "paidr18_group_video")],
                [_btn("◀️ 返回記憶選擇", "paidr18_back_memory")],
            ),
        ),
        state={"flow": "genpost_paid_entry", "draft": draft},
    )


def _genpost_tg_count_prompt(draft: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(draft.get("persona_id") or "")
    name = str(draft.get("name") or persona_id or "人設")
    selected = draft.get("selected_memory_entry_ids") if isinstance(draft.get("selected_memory_entry_ids"), list) else []
    lines = ["✍️ 新建推文", "", f"人設：{name}"]
    if draft.get("content_branch"):
        lines.append(f"內容類型：{_genpost_branch_label(str(draft.get('content_branch') or ''))}")
    if draft.get("content_time_slot"):
        lines.append(f"文案時段：{draft.get('content_time_slot')}")
    mode_label = "只生成推文（不配圖）" if draft.get("text_only") else "生成推文+配圖/視頻"
    lines.extend(
        [
            f"模式：{mode_label}",
            f"指定記憶：{f'{len(selected)} 條' if selected else '不指定'}",
            "",
            "⭐ 請輸入生成數量 ⭐",
            "　　只需要發送數字即可。",
            "",
            "例如：3",
        ]
    )
    return _response(_message("\n".join(lines), [[_btn("◀️ 返回", "genpost_count_back")]]), state={"flow": "genpost_count", "draft": draft})


def _genpost_tg_prompt_input(draft: dict[str, Any]) -> dict[str, Any]:
    name = str(draft.get("name") or draft.get("persona_id") or "人設")
    mode_label = "只生成推文（不配圖）" if draft.get("text_only") else "生成推文+配圖/視頻"
    ratio_line = f"\n畫面比例：{draft.get('imageAspectRatio')}（{draft.get('imageRatioLabel')}）" if draft.get("imageAspectRatio") else ""
    text = (
        f"✍️ 新建推文\n\n人設：{name}\n模式：{mode_label}\n數量：{draft.get('count') or 0} 篇{ratio_line}"
        "\n\n⭐ 請發送本次生成的提示詞 ⭐\n　　也可以跳過提示詞，讓 AI 根據人設自由發展。"
        "\n\n例如：圍繞教師生活，寫得像群內早安日常。"
    )
    return _response(
        _message(text, _rows([_btn("⏭ 跳過提示詞，讓 AI 自由發展", "genpost_prompt_skip")], [_btn("◀️ 返回生成數量", "genpost_count_back")])),
        state={"flow": "genpost_prompt", "draft": draft},
    )


GENPOST_RATIO_OPTIONS = [
    {"id": "2x3", "ratio": "2:3", "label": "2:3 豎圖", "width": 1024, "height": 1536},
    {"id": "3x4", "ratio": "3:4", "label": "3:4 穩定豎圖", "width": 1024, "height": 1365},
    {"id": "9x16", "ratio": "9:16", "label": "9:16 手機豎屏長圖", "width": 1024, "height": 1820},
    {"id": "3x2", "ratio": "3:2", "label": "3:2 橫圖基準", "width": 1536, "height": 1024},
    {"id": "4x3", "ratio": "4:3", "label": "4:3 平衡橫圖", "width": 1365, "height": 1024},
    {"id": "16x9", "ratio": "16:9", "label": "16:9 寬屏", "width": 1820, "height": 1024},
    {"id": "1x1", "ratio": "1:1", "label": "1:1 正方形", "width": 1024, "height": 1024},
]


def _genpost_ratio_picker(draft: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(draft.get("persona_id") or "")
    name = str(draft.get("name") or persona_id or "人設")
    lines = [
        "✍️ 新建推文",
        "",
        f"人設：{name}",
        f"模式：{_genpost_branch_label(str(draft.get('content_branch') or '')) + ' + 配圖 / 視頻' if draft.get('content_branch') else '生成推文+配圖/視頻'}",
        f"數量：{draft.get('count') or 0} 篇",
        "",
        "請選擇免費群配圖畫面比例：",
    ]
    keyboard: list[list[dict[str, str]]] = []
    for i in range(0, len(GENPOST_RATIO_OPTIONS), 2):
        keyboard.append([_btn(item["label"], "genpost_ratio_" + item["id"]) for item in GENPOST_RATIO_OPTIONS[i : i + 2]])
    keyboard.append([_btn("◀️ 返回生成數量", "genpost_count_back")])
    return _response(_message("\n".join(lines), keyboard), state={"flow": "genpost_ratio", "draft": draft})


def _genpost_apply_ratio(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    if action == "genpost_ratio_back":
        return _genpost_ratio_picker(draft)
    ratio_id = action[len("genpost_ratio_") :]
    option = next((item for item in GENPOST_RATIO_OPTIONS if item["id"] == ratio_id), None)
    if not option:
        return _genpost_ratio_picker(draft)
    draft.update(
        {
            "imageRatioId": option["id"],
            "imageAspectRatio": option["ratio"],
            "imageRatioLabel": option["label"],
            "imageWidth": option["width"],
            "imageHeight": option["height"],
        }
    )
    return _genpost_tg_prompt_input(draft)


def _genpost_tg_words_prompt(draft: dict[str, Any], received_prompt: str | None = None) -> dict[str, Any]:
    prefix = f"✅ 已收到提示詞：{received_prompt}" if received_prompt is not None else "✅ 已選擇讓 AI 自動生成提示詞。"
    return _response(
        _message(
            prefix + "\n\n⭐ 請輸入每篇推文的目標字數 ⭐\n只需要發送數字即可。\n\n例如：120",
        ),
        state={"flow": "genpost_words", "draft": draft},
    )


def _genpost_collect_memory_text(draft: dict[str, Any]) -> str:
    chunks: list[str] = []
    selected = {str(item) for item in draft.get("selected_memory_entry_ids", [])}
    for item in draft.get("memory_options", []) if isinstance(draft.get("memory_options"), list) else []:
        if str(item.get("id") or "") in selected and item.get("content"):
            chunks.append(str(item.get("content")))
    custom = str(draft.get("custom_memory") or "").strip()
    if custom:
        chunks.append(custom)
    return "\n\n".join(chunks)[:2400]


def _genmem_action(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    selected = {str(item) for item in draft.get("selected_memory_entry_ids", [])}
    options = draft.get("memory_options") if isinstance(draft.get("memory_options"), list) else []
    if action.startswith("genmem_page_"):
        draft["memory_page"] = _num(action[len("genmem_page_") :])
        return _genpost_memory_selection(draft)
    if action.startswith("genmem_toggle_"):
        index = _num(action[len("genmem_toggle_") :])
        if 0 <= index < len(options):
            key = str(options[index].get("id") or index)
            if key in selected:
                selected.remove(key)
            else:
                selected.add(key)
            draft["selected_memory_entry_ids"] = list(selected)
        return _genpost_memory_selection(draft)
    if action == "genmem_select_all":
        all_ids = {str(item.get("id") or idx) for idx, item in enumerate(options)}
        draft["selected_memory_entry_ids"] = [] if selected and selected == all_ids else list(all_ids)
        return _genpost_memory_selection(draft)
    if action == "genmem_delete_selected":
        chosen = [item for item in options if str(item.get("id") or "") in selected]
        if not chosen:
            return _response(_message("請先勾選要刪除的人設記憶。", [[_btn("◀️ 返回記憶選擇", "genmem_page_0")]]), state={"flow": "genpost_tg_memory", "draft": draft})
        lines = ["🗑 刪除已選記憶", "", f"即將刪除 {len(chosen)} 條人設記憶："]
        for index, item in enumerate(chosen[:8], start=1):
            lines.append(f"{index}. {_memory_excerpt(item.get('title') or item.get('content'), 48)}")
        return _response(
            _message("\n".join(lines), [[_btn("✅ 確認刪除", "genmem_delete_confirm")], [_btn("◀️ 返回記憶選擇", "genmem_delete_cancel")]]),
            state={"flow": "genpost_tg_memory", "draft": draft},
        )
    if action == "genmem_delete_cancel":
        return _genpost_memory_selection(draft)
    if action == "genmem_delete_confirm":
        deleted = 0
        source_ids = {
            str(item.get("id") or "")
            for item in options
            if str(item.get("id") or "") in selected and item.get("source") == "tool_r18"
        }
        if source_ids:
            archive_id = str(draft.get("source_archive_id") or draft.get("persona_id") or "").strip()
            try:
                _base, result = _source_http_request(
                    "POST",
                    f"/api/internal/tg/personas/{urllib.parse.quote(archive_id, safe='')}/memories/delete",
                    payload={"tg_chat_id": SOURCE_WEB_BOT_CHAT_ID, "entry_ids": sorted(source_ids)},
                    timeout=30,
                )
                deleted += _num(result.get("deleted"))
            except Exception as exc:
                return _response(
                    _message(f"刪除人設記憶失敗：{exc}", [[_btn("◀️ 返回記憶選擇", "genmem_delete_cancel")]]),
                    state={"flow": "genpost_tg_memory", "draft": draft},
                )
        for memory_id in selected - source_ids:
            if PostMemoryRepo.delete(memory_id):
                deleted += 1
        draft["selected_memory_entry_ids"] = []
        draft.pop("memory_options", None)
        result = _genpost_memory_selection(draft)
        messages = result.get("messages") if isinstance(result.get("messages"), list) else []
        return {**result, "messages": [_message(f"✅ 已刪除 {deleted} 條人設記憶。", kind="status"), *messages]}
    if action == "genmem_add_custom":
        return _response(
            _message("請直接發送要新增的人設記憶。", [[_btn("◀️ 返回記憶選擇", "genmem_custom_back")]]),
            state={"flow": "genpost_custom_memory", "draft": draft},
        )
    if action == "genmem_custom_back":
        return _genpost_memory_selection(draft)
    if action in {"genmem_done", "genmem_skip"}:
        if action == "genmem_skip":
            draft["selected_memory_entry_ids"] = []
            draft["memory"] = ""
        else:
            draft["memory"] = _genpost_collect_memory_text(draft)
        if draft.get("content_branch") == "nonr18":
            return _genpost_time_slot_picker(draft)
        if draft.get("content_branch") == "r18":
            return _genpost_paid_entry_picker(draft)
        return _genpost_tg_count_prompt(draft)
    if action == "genmem_time_back":
        return _genpost_memory_selection(draft)
    if action in {"genmem_time_morning", "genmem_time_night"}:
        draft["content_time_slot"] = "早上文案" if action == "genmem_time_morning" else "晚上文案"
        return _genpost_tg_count_prompt(draft)
    return _genpost_memory_selection(draft)


def _start_generate_posts(persona_id: str) -> dict[str, Any]:
    persona_id, local, row, name = _genpost_context(persona_id)
    if not local and not row:
        return _response(_message("找不到這個人設，無法新建推文。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    if _is_workflow_persona_row(row, persona_id):
        return _genpost_branch_picker(persona_id)
    return _genpost_mode_picker(persona_id)


def _generate_draft_posts(name: str, description: str, count: int, words: int, memory: str = "") -> list[str]:
    name = to_traditional(name)
    memory = to_traditional(str(memory or "").strip())
    description = to_traditional(str(description or "").strip())
    topic_source = memory or description or name
    topic = re.sub(r"\s+", " ", topic_source).strip()[:90] or name
    memory_note = f"這次我想特別記住的是：{memory[:120]}。 " if memory else ""
    templates = [
        f"{memory_note}今天用 {name} 的角度看「{topic}」，最有感的是：內容不一定要很用力，重點是把一個真實細節講清楚，讓看到的人覺得這像生活裡真的會發生的瞬間。",
        f"剛剛整理 {name} 的內容方向時想到，「{topic}」其實很適合做成日常分享。不要只丟結論，可以先寫場景，再寫感受，最後留一個讓人想回覆的小問題。",
        f"如果今天要圍繞「{topic}」發 Threads，我會讓 {name} 用比較自然的語氣說：我不是想把自己包裝得很完美，只是想把這段經驗留下來，也許剛好有人需要。",
        f"{name} 今天的觀察：真正容易被記住的內容，不是標籤堆滿，而是每次出現都能讓人感覺到同一種個性。像「{topic}」這種素材，就適合慢慢鋪情緒。",
        f"有時候一篇推文不用寫得太滿。把「{topic}」拆成一個畫面、一句感受、一個小轉折，就會比硬寫大道理更像真人，也更容易被互動。",
    ]
    out = []
    for index in range(count):
        text = templates[index % len(templates)]
        if words and words > 0:
            out.append(to_traditional(text[: max(40, min(len(text), words * 2))]))
        else:
            out.append(to_traditional(text))
    return out


def _continue_generate_posts(message: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    persona_id = str(draft.get("persona_id") or "")
    name = str(draft.get("name") or "人设")
    flow = str(state.get("flow") or "")
    if flow == "genpost_memory":
        text = str(message or "").strip()
        compact = re.sub(r"\s+", "", text.lower())
        if re.fullmatch(r"\d+", text):
            flow = "genpost_count"
        else:
            if compact in {"略过", "略過", "跳过", "跳過", "skip", "无", "無", "不用", "不需要"}:
                draft["memory"] = ""
            else:
                draft["memory"] = to_traditional(text[:1200])
            return _response(
                _genpost_count_prompt(persona_id, name, str(draft.get("memory") or "")),
                state={"flow": "genpost_count", "draft": draft},
            )
    if flow == "genpost_count":
        count = _num(message)
        if count <= 0:
            return _response(_message("请只发送生成数量，例如：3", [[_btn("◀️ 返回人设详情", f"pd:{persona_id}")]]), state=state)
        capped = min(count, GENPOST_MAX_COUNT)
        draft["count"] = capped
        if not bool(draft.get("text_only")) and str(draft.get("content_branch") or "") != "r18":
            return _genpost_ratio_picker(draft)
        return _genpost_tg_prompt_input(draft)
        limit_note = f"\n\n你输入的是 {count}，为避免操作台断线，本次先生成 {capped} 篇。需要更多可以分批再生成。" if count > capped else ""
        memory_line = "指定记忆：已加入" if str(draft.get("memory") or "").strip() else "指定记忆：未指定"
        return _response(
            _message(
                "✅ 已选择让 AI 自动生成提示词。" + limit_note + f"\n\n{memory_line}\n\n⭐ 请输入每篇推文的目标字数 ⭐\n只需要发送数字即可。\n\n例如：120",
                [[_btn("◀️ 返回人设详情", f"pd:{persona_id}")]],
            ),
            state={"flow": "genpost_words", "draft": draft},
        )
    if flow == "genpost_words":
        words = max(20, min(_num(message), 500))
        local = PersonaRepo.get(persona_id)
        description = "\n".join([part for part in [local.description, local.style_prompt] if part]) if local else ""
        count = min(int(draft.get("count") or 3), GENPOST_MAX_COUNT)
        memory = str(draft.get("memory") or "").strip()
        posts = _apply_link_ending_to_posts(persona_id, _generate_draft_posts(name, description, count, words, memory))
        draft.update(
            {
                "words": words,
                "posts": posts,
                "selected": list(range(min(2, len(posts)))),
                "image_group": 1,
                "memory": memory,
                "post_image_paths": ["" for _ in posts],
                "post_image_candidates": {},
                "content_branch": "免費群內容",
                "content_time_slot": "早上文案",
            }
        )
        memory_line = "指定记忆：已加入" if memory else "指定记忆：未指定"
        messages = [
            _message(
                f"⏳ 正在为人设「{name}」生成 {len(posts)} 篇推文 + 配图，请稍候...\n\n{memory_line}\n目标字数：约 {words} 字/篇",
                kind="status",
            )
        ]
        if posts:
            messages.append(
                _message(
                    "✅ 已生成推文草稿，接下来会按推文顺序逐篇生成配图。\n\n每篇生成 4 张候选图，请先选择其中 1 张，再生成下一篇。",
                    kind="status",
                )
            )
        if local:
            try:
                if not _avatar_exists(local):
                    messages.append(_message(f"🎨 正在补齐「{name}」的人设图...", kind="status"))
                    _generate_persona_reference_image(local)
                    local = PersonaRepo.get(persona_id) or local
                if posts:
                    messages.append(
                        _message(
                            f"⏳ 正在生成第 1/{len(posts)} 篇配图候选，每组 {GENPOST_IMAGE_CANDIDATE_COUNT} 张...",
                            kind="status",
                        )
                    )
                    _generate_post_image_candidates_for_index(local, posts, 0, draft)
                    messages.append(_post_candidate_message(draft, 0))
            except Exception as exc:
                messages.append(_message(f"配图候选生成失败，已保留文字草稿，可稍后在推文列表重试：{exc}", kind="status"))
        messages.append(_post_select_message(draft))
        return _response(
            messages,
            state={"flow": "post_select", "draft": draft},
        )
    return _main_menu()


def _hot_originals_from_context(hot_context: str) -> list[dict[str, str]]:
    seeds: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in str(hot_context or "").splitlines():
        line = to_traditional(re.sub(r"\s+", " ", raw_line).strip())
        if not line:
            continue
        if line.startswith("候選熱點"):
            if current.get("original"):
                seeds.append(current)
            current = {"title": line, "metrics": "", "original": "", "source": ""}
            continue
        if line.startswith("數據："):
            current["metrics"] = line.removeprefix("數據：").strip()
            continue
        if line.startswith("原帖："):
            original = line.removeprefix("原帖：").strip()
            if original and original not in {"未讀取到文案", "未读取到文案", "-"}:
                current["original"] = original
            continue
        if line.startswith("來源："):
            current["source"] = line.removeprefix("來源：").strip()
            continue
    if current.get("original"):
        seeds.append(current)
    return seeds


def _compose_hot_post_from_seed(name: str, description: str, seed: dict[str, str], index: int, target: int) -> str:
    original = to_traditional(str(seed.get("original") or "").strip())
    metrics = to_traditional(str(seed.get("metrics") or "").strip())
    source = str(seed.get("source") or "").strip()
    persona_hint = to_traditional(re.sub(r"\s+", " ", str(description or "")).strip())[:90]
    openers = [
        "剛看到這則討論，我覺得真正值得看的不是表面數字，而是背後那個情緒。",
        "這篇會被推起來其實不意外，因為它講到很多人心裡一直卡住的點。",
        "如果把這則熱門拆開看，重點不是跟風，而是找到可以延伸成自己觀點的角度。",
        "我會用比較白話的方式看這個話題：先看人為什麼停下來，再看我們能補什麼觀點。",
    ]
    angles = [
        "所以這篇我會抓住一個重點：把原本很散的焦慮，整理成可以被理解的一句話。",
        "與其照抄原帖，不如順著它的情緒補一層判斷，讓內容更像真人在回應現場。",
        "這種熱點最怕寫成新聞摘要，真正有用的是把它變成受眾能代入的日常場景。",
        "接這類話題時，我會保留原本的真實感，但把結論收斂得更清楚。",
    ]
    opener = openers[index % len(openers)]
    angle = angles[index % len(angles)]
    lines = [
        f"{name}｜熱點觀察",
        "",
        opener,
        "",
        f"原帖重點：{original}",
        "",
        angle,
    ]
    if persona_hint:
        lines.append(f"依照人設語氣：{persona_hint}")
    if metrics:
        lines.append(f"互動訊號：{metrics}")
    if source:
        lines.append(f"來源參考：{source}")
    text = "\n".join(lines)
    if len(text) > target:
        keep_original = max(80, target - 260)
        trimmed_original = original[:keep_original].rstrip("，。；、 \n") + ("..." if len(original) > keep_original else "")
        lines = [
            f"{name}｜熱點觀察",
            "",
            opener,
            "",
            f"原帖重點：{trimmed_original}",
            "",
            angle,
        ]
        if metrics and target >= 180:
            lines.append(f"互動訊號：{metrics}")
        text = "\n".join(lines)
    if len(text) > target:
        text = text[:target].rstrip("，。；、 \n") + "。"
    return to_traditional(text)


def _generate_draft_posts(name: str, description: str, count: int, words: int, memory: str = "", hot_context: str = "") -> list[str]:
    name = to_traditional(name or "人設")
    description = to_traditional(str(description or "").strip())
    memory = to_traditional(str(memory or "").strip())
    hot_context = to_traditional(str(hot_context or "").strip())
    source = memory or hot_context or description or name
    source = re.sub(r"\s+", " ", source).strip()
    topic = source[:120] or name
    angles = [
        "把最近的觀察整理成一個清楚提醒",
        "用生活化語氣切入受眾正在遇到的問題",
        "從熱門討論延伸出一個可保存的觀點",
        "用第一人稱分享一個具體經驗",
        "把焦慮轉成可以立刻行動的小步驟",
        "用反差開頭帶出人設的專業判斷",
    ]
    endings = [
        "先把節奏穩住，接下來每一步都會更清楚。",
        "真正有用的內容，不是喊口號，而是讓人知道下一步怎麼做。",
        "今天先記住這件事：方向對了，速度才有意義。",
        "如果你也卡在這裡，可以先從最小的一個動作開始。",
    ]
    hot_items = _hot_originals_from_context(hot_context)
    out: list[str] = []
    target = max(60, min(int(words or 120) * 2, 900))
    for index in range(max(1, int(count or 1))):
        angle = angles[index % len(angles)]
        ending = endings[index % len(endings)]
        memory_line = f"延續今天的記憶：{topic}" if memory else f"今天的主題：{topic}"
        if hot_items:
            hot_seed = hot_items[index % len(hot_items)]
            text = _compose_hot_post_from_seed(name, description, hot_seed, index, target)
        elif hot_context:
            text = (
                "這次熱點資料沒有讀到可改寫的原帖文案，請先回到熱點清單刷新資料，或手動貼上原帖內容後再生成。"
            )
        else:
            text = (
                f"{name}｜{angle}\n\n"
                f"{memory_line}\n\n"
                "我會先看人群真正被什麼吸引，再把內容拆成可以被理解、被共鳴、被收藏的角度。"
                "不要急著把所有資訊一次塞滿，一篇推文只要把一個重點講透，就能讓人願意停下來。"
                f"\n\n{ending}"
            )
        if len(text) > target:
            text = text[:target].rstrip("，。；、 \n") + "。"
        out.append(to_traditional(text))
    return out


def _tg_post_action_key(index: int) -> str:
    return f"cur_{max(0, int(index or 0))}"


def _tg_post_action_index(value: str) -> int:
    value = str(value or "").strip()
    if value.startswith("cur_"):
        return _num(value[len("cur_") :])
    if value.startswith("post_"):
        return _num(value[len("post_") :])
    return _num(value)


def _parse_tg_post_action(action: str) -> tuple[str, str, str]:
    if not action.startswith("pa_"):
        return "", "", ""
    payload = action[len("pa_") :]
    for kind in ("dopm", "dop", "pp", "pub", "imgai", "imgup", "img", "media", "edit", "fav", "del", "rai", "ras", "rap", "rc", "rf", "msa", "mcl", "mrs", "mru", "mra", "mt", "md", "v", "mm", "mp", "ed", "rg"):
        prefix = kind + "_"
        if payload.startswith(prefix):
            rest = payload[len(prefix) :]
            if kind in {"pp", "dop", "dopm"}:
                key, _, value = rest.rpartition("_")
                return kind, key or rest, value
            return kind, rest, ""
    return "", "", ""


def _post_list_callback_for_draft(draft: dict[str, Any]) -> str:
    persona_id = str(draft.get("persona_id") or "")
    branch = str(draft.get("content_branch") or "")
    page = max(0, _num(draft.get("post_page")))
    suffix = "_ct_paid" if branch == "r18" else "_ct_free" if branch == "nonr18" else ""
    return f"posts_{persona_id}{suffix}_p{page}"


def _no_persona_reference_generate_response(draft: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(draft.get("persona_id") or "")
    source_archive_id = str(draft.get("source_archive_id") or "").strip()
    callback_persona_id = source_archive_id or persona_id
    name = str(draft.get("name") or persona_id or "人設")
    lines = [
        "⚠️ 此人設尚未生成人設圖。",
        "推文配圖必須先使用人設圖鎖定人物長相；請先點擊下方按鈕生成人設圖。",
        "人設圖生成完成後，可以直接繼續剛才的推文配圖流程。",
    ]
    return _response(
        _message(
            "\n".join(lines),
            _rows([_btn("🎨 生成人設圖", f"genimg_{callback_persona_id}")], [_btn("◀️ 返回人設詳情", f"pd_{callback_persona_id}")]),
        ),
        state={"flow": "genpost_no_reference", "draft": {**draft, "name": name}},
    )


def _generation_persona_reference(
    persona_id: str,
    draft: dict[str, Any] | None = None,
) -> tuple[str, Persona | None, dict[str, Any] | None, bool, bool]:
    draft = draft if isinstance(draft, dict) else {}
    lookup_id = str(draft.get("source_archive_id") or persona_id or "").strip()
    local, row = _resolve_persona_for_action(lookup_id)
    source_archive_id = _tool_r18_archive_id(lookup_id, local, row) or lookup_id
    has_reference = bool((local and _avatar_exists(local)) or _persona_reference_image_url(row))
    if not has_reference:
        row = _fresh_persona_row(source_archive_id or lookup_id, local, row)
        source_archive_id = _tool_r18_archive_id(source_archive_id or lookup_id, local, row) or source_archive_id
        has_reference = bool((local and _avatar_exists(local)) or _persona_reference_image_url(row))
    is_workflow = _is_workflow_persona_row(row, source_archive_id or lookup_id)
    return source_archive_id, local, row, has_reference, is_workflow


def _continue_no_reference_generate(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    persona_id = str(draft.get("persona_id") or "")
    source_archive_id, _local, _row, has_reference, is_workflow = _generation_persona_reference(persona_id, draft)
    if source_archive_id:
        draft["source_archive_id"] = source_archive_id
    if not has_reference and not is_workflow:
        return _no_persona_reference_generate_response(draft)
    words = str(draft.get("words") or draft.get("target_words") or "80")
    return _continue_generate_posts(words, {"flow": "genpost_words", "draft": draft})


def _continue_generate_posts(message: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    persona_id = str(draft.get("persona_id") or "")
    name = str(draft.get("name") or "人設")
    flow = str(state.get("flow") or "")
    text = str(message or "").strip()

    if flow == "genpost_custom_memory":
        if not text:
            return _response(_message("請直接發送要新增的人設記憶。", [[_btn("◀️ 返回記憶選擇", "genmem_custom_back")]]), state=state)
        custom = to_traditional(text[:1600])
        draft["custom_memory"] = custom
        archive_id = str(draft.get("source_archive_id") or persona_id).strip()
        try:
            _base, result = _source_http_request(
                "POST",
                f"/api/internal/tg/personas/{urllib.parse.quote(archive_id, safe='')}/memories/add",
                payload={"tg_chat_id": SOURCE_WEB_BOT_CHAT_ID, "summary": custom},
                timeout=30,
            )
        except Exception as exc:
            return _response(
                _message(f"新增人設記憶失敗：{exc}", [[_btn("◀️ 返回記憶選擇", "genmem_custom_back")]]),
                state=state,
            )
        entry = result.get("entry") if isinstance(result.get("entry"), dict) else {}
        entry_id = str(entry.get("id") or "").strip()
        if entry_id:
            options = draft.get("memory_options") if isinstance(draft.get("memory_options"), list) else []
            options.insert(0, {"id": entry_id, "title": custom, "content": custom, "granularity": "post", "memory_date": str(entry.get("date") or ""), "source": "tool_r18"})
            draft["memory_options"] = options
            selected = list(draft.get("selected_memory_entry_ids") if isinstance(draft.get("selected_memory_entry_ids"), list) else [])
            selected.append(entry_id)
            draft["selected_memory_entry_ids"] = selected
        return _genpost_memory_selection(draft)

    if flow == "genpost_hot_manual":
        if not text:
            return _response(
                _message("請貼上熱點/輿情內容，或返回記憶選擇。", _rows([_btn("◀️ 返回記憶選擇", f"genpost:{persona_id}")])) ,
                state=state,
            )
        context = to_traditional(text[:2400])
        saved = _record_post_memory(
            persona_id,
            context,
            granularity="hot",
            source_type="manual_hot_opinion",
            title="手動新增熱點輿情",
        )
        draft.update(
            {
                "memory": context,
                "hot_context": context,
                "memory_id": saved.id if saved else "",
                "memory_granularity": "hot",
                "content_branch": str(draft.get("content_branch") or "手動熱點推文"),
                "content_time_slot": str(draft.get("content_time_slot") or "即時熱點"),
            }
        )
        return _response(
            _genpost_count_prompt(persona_id, name, context),
            state={"flow": "genpost_count", "draft": draft},
        )

    if flow == "genpost_edit_post":
        posts = [str(item) for item in draft.get("posts", [])]
        index = _num(draft.get("edit_index"))
        if not (0 <= index < len(posts)):
            return _response(_message("找不到要修改的推文，請回到推文列表重新選擇。", [[_btn("📋 查看推文列表", "post_select_back")]]), state={"flow": "post_select", "draft": draft})
        posts[index] = to_traditional(text[:2200])
        draft["posts"] = posts
        _set_post_image_path(draft, index, "")
        candidates = dict(draft.get("post_image_candidates") if isinstance(draft.get("post_image_candidates"), dict) else {})
        candidates.pop(str(index), None)
        candidates.pop(index, None)
        draft["post_image_candidates"] = candidates
        draft.pop("edit_index", None)
        _persist_generated_post_draft(draft)
        return _response(
            [_message(f"✅ 已更新第 {index + 1} 篇文案，原配圖候選已清空，避免圖文不一致。", kind="status"), _post_detail_message(draft, index)],
            state={"flow": "post_select", "draft": draft},
        )

    if flow in {"genpost_memory", "genpost_memory_manual"}:
        if flow == "genpost_memory" and re.fullmatch(r"\d+", text):
            draft["memory"] = ""
            flow = "genpost_count"
        else:
            compact = re.sub(r"\s+", "", text.lower())
            if compact in {"skip", "略過", "略过", "不用", "不使用", "無", "无"}:
                draft["memory"] = ""
            else:
                memory = to_traditional(text[:1600])
                draft["memory"] = memory
                saved = _record_post_memory(
                    persona_id,
                    memory,
                    granularity=str(draft.get("memory_granularity") or "daily"),
                    source_type="manual",
                    title="手動輸入記憶",
                )
                if saved:
                    draft["memory_id"] = saved.id
            return _response(
                _genpost_count_prompt(persona_id, name, str(draft.get("memory") or "")),
                state={"flow": "genpost_count", "draft": draft},
            )

    if flow == "genpost_count":
        count = _num(text)
        if count < 1 or count > GENPOST_MAX_COUNT:
            return _response(
                _message("❌ 數量格式不正確。\n\n⭐ 請發送 1-20 之間的數字 ⭐\n　　　只需要發送數字即可。\n\n例如：3"),
                state=state,
            )
        draft["count"] = count
        if not bool(draft.get("text_only")) and str(draft.get("content_branch") or "") != "r18":
            return _genpost_ratio_picker(draft)
        return _genpost_tg_prompt_input(draft)
        note = f"\n\n你輸入 {count} 篇，系統上限為 {GENPOST_MAX_COUNT} 篇，已改為 {capped} 篇。" if count > capped else ""
        return _response(
            _message(
                "✅ 已設定推文數量。\n\n請輸入每篇推文的目標字數。\n例如：120" + note,
                _rows([_btn("◀️ 返回記憶選擇", f"genpost:{persona_id}")]),
            ),
            state={"flow": "genpost_words", "draft": draft},
        )

    if flow == "genpost_prompt":
        draft["prompt"] = to_traditional(text[:1200])
        return _genpost_tg_words_prompt(draft, draft["prompt"])

    if flow == "genpost_words":
        words = _num(text)
        if words < 10 or words > 2000:
            return _response(
                _message(
                    "❌ 字數格式不正確。\n\n⭐ 請發送 10-2000 之間的數字 ⭐\n　　　只需要發送數字即可。\n\n例如：120",
                    [[_btn("◀️ 返回人設詳情", f"pd_{persona_id}")]],
                ),
                state=state,
            )
        source_archive_id, local, row, has_reference, is_workflow = _generation_persona_reference(persona_id, draft)
        if source_archive_id:
            draft["source_archive_id"] = source_archive_id
        if not bool(draft.get("text_only")) and not has_reference and not is_workflow:
            draft["words"] = words
            draft["target_words"] = words
            return _no_persona_reference_generate_response(draft)
        count = min(int(draft.get("count") or 3), GENPOST_MAX_COUNT)
        memory = str(draft.get("memory") or "").strip()
        hot_context = str(draft.get("hot_context") or "").strip()
        instruction_parts = [
            str(draft.get("prompt") or "").strip(),
            f"每篇目標約 {words} 字。",
            f"文案時段：{draft.get('content_time_slot')}" if draft.get("content_time_slot") else "",
            hot_context,
        ]
        selected_ids = [str(item) for item in draft.get("selected_memory_entry_ids", []) if str(item).strip()]
        if not source_archive_id:
            return _response(
                _message(
                    "這個 Web 本地人設尚未同步到 Tool R18 人設庫，暫時不能生成真實推文。",
                    _rows([_btn("🔄 同步設備生成人設", "sync_personas")], [_btn("◀️ 返回人設詳情", f"pd_{persona_id}")]),
                ),
                state={"flow": ""},
            )
        params = {
            "archiveId": source_archive_id,
            "count": count,
            "customInstruction": "\n\n".join(part for part in instruction_parts if part),
            "selectedMemoryEntryIds": selected_ids,
            "selectedMemorySummaries": [memory] if memory and not selected_ids else [],
            "textModelBranch": "paid" if str(draft.get("content_branch") or "") == "r18" else "free",
            "uiTextOnly": bool(draft.get("text_only")),
            "uiPersonaId": persona_id,
            "uiImageAspectRatio": str(draft.get("imageAspectRatio") or ""),
            "uiImageWidth": _num(draft.get("imageWidth")),
            "uiImageHeight": _num(draft.get("imageHeight")),
            "uiImageRatioLabel": str(draft.get("imageRatioLabel") or ""),
        }
        job = SourceWorkflowJobRepo.create(
            "persona_generate_posts",
            f"生成推文：{name} / {count} 篇",
            params,
            status="submitting",
        )
        try:
            base, data = _source_submit_task("persona_generate_posts", params)
            SourceWorkflowJobRepo.update(
                job.id,
                status="submitted",
                result=data,
                source_task_id=str(data.get("id") or ""),
                source_base_url=base,
            )
        except Exception as exc:
            SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
            return _response(
                _message(
                    f"❌ 推文生成任務提交失敗\n\n{exc}",
                    _rows([_btn("◀️ 返回字數設定", "genpost_prompt_back")], [_btn("◀️ 返回人設詳情", f"pd_{persona_id}")]),
                ),
                state={"flow": "genpost_words", "draft": draft},
            )
        source_task_id = str(data.get("id") or "").strip()
        response = _response(
            _message(
                "\n".join(
                    [
                        f"⏳ 正在為人設「{name}」生成 {count} 篇推文{' + 配圖' if not bool(draft.get('text_only')) else ''}，請稍候...",
                        "",
                        f"指定記憶：{f'{len(selected_ids)} 條' if selected_ids else '不指定'}",
                        f"目標字數：約 {words} 字/篇",
                    ]
                ),
                [[_btn("◀️ 返回人設詳情", f"pd_{persona_id}")]],
            ),
            state={"flow": ""},
        )
        if source_task_id:
            response["poll"] = {"action": f"source_task_poll:{source_task_id}", "interval_ms": 2000}
        return response

    return _main_menu()


def _post_image_candidates(draft: dict[str, Any], index: int) -> list[str]:
    raw = draft.get("post_image_candidates")
    if not isinstance(raw, dict):
        return []
    value = raw.get(str(index), raw.get(index))
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _post_candidate_count(draft: dict[str, Any], index: int) -> int:
    return sum(1 for path in _post_image_candidates(draft, index) if Path(path).exists())


def _set_post_image_path(draft: dict[str, Any], index: int, path: str) -> None:
    merged_paths = [str(item) for item in draft.get("post_image_paths", [])]
    while len(merged_paths) <= index:
        merged_paths.append("")
    merged_paths[index] = str(path or "")
    draft["post_image_paths"] = merged_paths


def _generate_post_image_candidates_for_index(persona: Persona, posts: list[str], index: int, draft: dict[str, Any]) -> list[str]:
    if not (0 <= index < len(posts)):
        raise RuntimeError("找不到这篇推文，无法生成候选图。")
    errors: list[str] = []
    paths: list[str] = []
    base_text = str(posts[index] or "").strip()
    for candidate_index in range(GENPOST_IMAGE_CANDIDATE_COUNT):
        variation = (
            f"{base_text}\n\n"
            f"Candidate image {candidate_index + 1}/{GENPOST_IMAGE_CANDIDATE_COUNT}: "
            "create a distinct lifestyle composition, different pose, scene, outfit color, and camera angle. "
            "Keep the same persona identity from the reference image. No text in the image."
        )
        try:
            paths.append(_generate_post_image(persona, variation, index * GENPOST_IMAGE_CANDIDATE_COUNT + candidate_index))
        except Exception as exc:
            errors.append(f"候选 {candidate_index + 1}: {exc}")
            if not paths:
                break
    if not paths:
        raise RuntimeError("; ".join(errors[:2]) or "候选图生成失败")
    candidates = dict(draft.get("post_image_candidates") if isinstance(draft.get("post_image_candidates"), dict) else {})
    candidates[str(index)] = paths
    draft["post_image_candidates"] = candidates
    _set_post_image_path(draft, index, "")
    draft["image_group"] = index + 1
    _persist_generated_post_draft(draft)
    return paths


def _post_candidate_cards(candidates: list[str], selected_path: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for index, path in enumerate(candidates, start=1):
        if not path or not Path(path).exists():
            continue
        cards.append(
            {
                "title": f"候选图 {index}",
                "subtitle": "已选中" if selected_path and Path(path) == Path(selected_path) else "点击下方按钮选择这张",
                "image": _image_data_url(path),
            }
        )
    return cards


def _post_candidate_message(draft: dict[str, Any], index: int) -> dict[str, Any]:
    posts = [str(item) for item in draft.get("posts", [])]
    candidates = _post_image_candidates(draft, index)
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    selected_path = post_image_paths[index] if 0 <= index < len(post_image_paths) else ""
    existing = [path for path in candidates if path and Path(path).exists()]
    total = len(posts)
    text = posts[index] if 0 <= index < len(posts) else ""
    lines = [
        f"🖼 第 {index + 1}/{max(total, 1)} 篇推文候选图",
        "",
        f"候选图：{len(existing)} / {GENPOST_IMAGE_CANDIDATE_COUNT} 张",
        f"选择状态：{'已选中 1 张' if selected_path and Path(selected_path).exists() else '尚未选择'}",
        "",
        "请从候选图中选 1 张；选中后这篇推文会标记为有图。",
        "",
        f"推文：{text[:180]}{'...' if len(text) > 180 else ''}",
    ]
    choose_buttons = [
        _btn(f"✅ 选第 {candidate_index + 1} 张", f"select_post_image:{index}:{candidate_index}", "primary")
        for candidate_index, path in enumerate(candidates)
        if path and Path(path).exists()
    ]
    keyboard = _chunk_buttons(choose_buttons, 2)
    next_index = index + 1
    nav_row = [_btn("🔄 重生本篇候选图", f"pa_img_{_tg_post_action_key(index)}")]
    if next_index < len(posts):
        nav_row.append(_btn(f"🖼 生成第 {next_index + 1} 篇候选图", "next_post_image_group"))
    keyboard.extend(
        _rows(
            nav_row,
            [_btn("📋 查看推文列表", "pa_back")],
            [_btn("◀️ 返回人设详情", f"pd:{draft.get('persona_id')}")],
        )
    )
    preview_path = selected_path if selected_path and Path(selected_path).exists() else (existing[0] if existing else "")
    preview = _image_data_url(preview_path) if preview_path else ""
    return _message("\n".join(lines), keyboard, cards=_post_candidate_cards(candidates, selected_path), image=preview)


def _select_post_image(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    parts = action.split(":")
    if len(parts) != 3:
        return _response(_message("候选图选择入口无效，请重新打开推文列表。", [[_btn("📋 查看推文列表", "post_select_back")]]), state=state)
    post_index = _num(parts[1])
    candidate_index = _num(parts[2])
    candidates = _post_image_candidates(draft, post_index)
    if not (0 <= candidate_index < len(candidates)) or not Path(candidates[candidate_index]).exists():
        return _response(
            _message("这张候选图不存在或已经遗失，请重新生成候选图。", [[_btn("🔄 重生候选图", f"genpost_image:{post_index}")]]),
            state={"flow": "post_select", "draft": draft},
        )
    _set_post_image_path(draft, post_index, candidates[candidate_index])
    draft["image_group"] = post_index + 1
    _persist_generated_post_draft(draft)
    messages = [
        _message(f"✅ 已为第 {post_index + 1} 篇选择第 {candidate_index + 1} 张配图。", kind="status"),
        _post_select_message(draft),
    ]
    return _response(messages, state={"flow": "post_select", "draft": draft})


def _post_select_message(draft: dict[str, Any]) -> dict[str, Any]:
    posts = [str(item) for item in draft.get("posts", [])]
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    existing_images = [path for path in post_image_paths if path and Path(path).exists()]
    selected = {int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()}
    image_status = f"已生成 {len(existing_images)} 张" if existing_images else "未生成"
    total_with_image, total_without_image = _post_image_counts(posts, post_image_paths)
    selected_with_image, selected_without_image = _post_image_counts(posts, post_image_paths, selected)
    current_group = max(1, min(_num(draft.get("image_group")) or 1, max(1, len(posts))))
    next_group = current_group + 1 if current_group < len(posts) else 1
    total_candidates = sum(_post_candidate_count(draft, index) for index in range(len(posts)))
    lines = [
        "📝 待发布推文列表",
        "",
        f"配图：{image_status}；候选图 {total_candidates} 张（目前第 {current_group}/{max(1, len(posts))} 篇）",
        f"全部：有图 {total_with_image} 篇；暂无图 {total_without_image} 篇",
        f"已选：有图 {selected_with_image} 篇；暂无图 {selected_without_image} 篇",
        "",
        "请勾选要发布的推文。",
    ]
    for index, text in enumerate(posts[:STORED_POSTS_PAGE_SIZE], start=1):
        mark = "☑️" if index - 1 in selected else "☐"
        candidate_label = _post_candidate_count(draft, index - 1)
        candidate_text = f"；候选 {candidate_label} 张" if candidate_label else "；未生成候选"
        lines.extend(["", f"{mark}【{index}】{_post_image_status_label(post_image_paths, index - 1)}{candidate_text}", f"{text[:150]}{'...' if len(text) > 150 else ''}"])
    keyboard: list[list[dict[str, str]]] = []
    for index in range(min(len(posts), STORED_POSTS_PAGE_SIZE)):
        keyboard.append([_btn(f"{'☑️' if index in selected else '☐'} 第{index + 1}篇・{_post_image_short_label(post_image_paths, index)}", f"btog:{index}")])
        candidate_count = _post_candidate_count(draft, index)
        image_action = "🔄 重生候选" if candidate_count else "🖼 生成候选"
        row = [_btn(f"{image_action}第 {index + 1} 篇", f"genpost_image:{index}")]
        if candidate_count:
            row.append(_btn(f"👁 查看候选图（{candidate_count}）", f"view_post_candidates:{index}"))
        keyboard.append(row)
    keyboard.extend(
        _rows(
            [_btn(f"🔄 重生第 {current_group} 篇候选图", "regen_post_images"), _btn(f"🖼 生成第 {next_group} 篇候选图", "next_post_image_group")],
            [_btn("📋 查看推文列表", "post_select_back")],
            [_btn("☑️ 全选本页", "bsel_page"), _btn("⬜ 清空本页", "bclear_page")],
            [_btn(f"✅ 确认发布选择（已选{len(selected)}篇）", "bconfirm")],
            [_btn("◀️ 返回推文列表", f"pd:{draft.get('persona_id')}")],
        )
    )
    preview = _image_data_url(existing_images[0]) if existing_images else ""
    return _message("\n".join(lines), keyboard, image=preview)


def _post_select_page_info(draft: dict[str, Any], total: int) -> tuple[int, int, list[int]]:
    page_size = max(1, STORED_POSTS_PAGE_SIZE)
    total_pages = max(1, (max(0, total) + page_size - 1) // page_size)
    page = max(0, min(_num(draft.get("post_page")), total_pages - 1))
    start = page * page_size
    return page, total_pages, list(range(start, min(total, start + page_size)))


def _post_source_label(draft: dict[str, Any]) -> str:
    if draft.get("hot_context"):
        return "熱點輿情"
    if draft.get("memory"):
        return "手動/既有記憶"
    return "無"


def _post_select_message(draft: dict[str, Any]) -> dict[str, Any]:
    posts = [to_traditional(str(item)) for item in draft.get("posts", [])]
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    existing_images = [path for path in post_image_paths if path and Path(path).exists()]
    selected = {int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()}
    total_with_image, total_without_image = _post_image_counts(posts, post_image_paths)
    selected_with_image, selected_without_image = _post_image_counts(posts, post_image_paths, selected)
    current_group = max(1, min(_num(draft.get("image_group")) or 1, max(1, len(posts))))
    next_group = current_group + 1 if current_group < len(posts) else 1
    total_candidates = sum(_post_candidate_count(draft, index) for index in range(len(posts)))
    generated_memory_id = str(draft.get("generated_memory_id") or "")
    generated_memory = PostMemoryRepo.get(generated_memory_id) if generated_memory_id else None
    memory_saved = "已保存" if generated_memory else ("已引用" if draft.get("memory_id") or draft.get("memory") or draft.get("hot_context") else "未使用")
    favorite_label = "已收藏" if generated_memory and int(generated_memory.favorite or 0) else "未收藏"
    memory_source = _post_source_label(draft)
    page, total_pages, visible_indexes = _post_select_page_info(draft, len(posts))
    draft["post_page"] = page

    lines = [
        "🧾 推文列表與圖片狀態",
        "",
        f"人設：{draft.get('name') or draft.get('persona_id')}",
        f"第 {page + 1}/{total_pages} 頁，共 {len(posts)} 篇",
        f"記憶：{memory_saved}｜來源：{memory_source}｜收藏：{favorite_label}",
        f"圖片：有圖 {total_with_image} 篇／無圖 {total_without_image} 篇｜候選圖 {total_candidates} 張",
        f"已選發布：有圖 {selected_with_image} 篇／無圖 {selected_without_image} 篇",
        "",
        "可逐篇查看、收藏、生成圖片，也可勾選後發布。",
    ]
    if not posts:
        lines.extend(["", "目前沒有推文草稿，請重新生成推文。"])
    for idx in visible_indexes:
        text = posts[idx]
        mark = "✅" if idx in selected else "⬜"
        has_image = _post_image_exists(post_image_paths, idx)
        candidate_label = _post_candidate_count(draft, idx)
        image_label = "有圖" if has_image else "無圖"
        lines.extend(
            [
                "",
                f"{mark} 第 {idx + 1} 篇｜{image_label}｜候選圖 {candidate_label} 張",
                _memory_excerpt(text, 180),
            ]
        )

    keyboard: list[list[dict[str, str]]] = []
    for idx in visible_indexes:
        has_image = _post_image_exists(post_image_paths, idx)
        candidate_count = _post_candidate_count(draft, idx)
        keyboard.append(
            [
                _btn(f"{'✅' if idx in selected else '⬜'} 第 {idx + 1} 篇｜{'有圖' if has_image else '無圖'}", f"btog:{idx}"),
                _btn(f"👁 查看第 {idx + 1} 篇", f"vp_{idx}"),
            ]
        )
        row = [_btn(f"{'🔄 重生' if candidate_count else '🖼 生成'}第 {idx + 1} 篇圖片", f"pa_img_{_tg_post_action_key(idx)}")]
        if candidate_count:
            row.append(_btn(f"查看候選圖（{candidate_count}）", f"pa_media_{_tg_post_action_key(idx)}"))
        keyboard.append(row)
    if total_pages > 1:
        keyboard.append(
            [
                _btn("⏮ 首頁", "post_page:0"),
                _btn("◀️ 上一頁", f"post_page:{max(0, page - 1)}"),
                _btn("下一頁 ▶️", f"post_page:{min(total_pages - 1, page + 1)}"),
                _btn("尾頁 ⏭", f"post_page:{total_pages - 1}"),
            ]
        )
    keyboard.extend(
        _rows(
            [_btn(f"🔄 重生第 {current_group} 篇圖片", f"pa_img_{_tg_post_action_key(current_group - 1)}"), _btn(f"🖼 生成第 {next_group} 篇圖片", "next_post_image_group")],
            [_btn("⭐ 收藏本次記憶", "draft_memory_favorite"), _btn("📚 記憶庫", f"genpost_memlist:{draft.get('persona_id')}:all:0")],
            [_btn("✅ 全選本頁", "bsel_page"), _btn("⬜ 清空本頁", "bclear_page")],
            [_btn(f"✅ 確認發布選擇（已選 {len(selected)} 篇）", "bconfirm")],
            [_btn("◀️ 返回人設詳情", f"pd:{draft.get('persona_id')}")],
        )
    )
    preview = _image_data_url(existing_images[0]) if existing_images else ""
    return _message("\n".join(lines), keyboard, image=preview)


def _post_select_message(draft: dict[str, Any]) -> dict[str, Any]:
    posts = [to_traditional(str(item)) for item in draft.get("posts", [])]
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    selected = {int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()}
    page, total_pages, visible_indexes = _post_select_page_info(draft, len(posts))
    draft["post_page"] = page
    persona_id = str(draft.get("persona_id") or "")
    name = str(draft.get("name") or persona_id or "人設")
    branch = str(draft.get("content_branch") or "")
    lines = [
        "📝 推文列表",
        "",
        f"人設：{name}",
        f"第 {page + 1}/{total_pages} 頁，共 {len(posts)} 篇",
    ]
    if branch:
        lines.append(f"內容類型：{_genpost_branch_label(branch)}")
    lines.append("")
    if not posts:
        lines.append("目前沒有推文草稿。")
    for idx in visible_indexes:
        media = "有配圖/視頻" if _post_image_exists(post_image_paths, idx) else "無配圖/視頻"
        mark = "✅" if idx in selected else "☐"
        lines.extend(["", f"{mark} 第 {idx + 1} 篇（{media}）", _memory_excerpt(posts[idx], 180)])
    keyboard: list[list[dict[str, str]]] = []
    for idx in visible_indexes:
        keyboard.append([_btn(f"查看第{idx + 1}篇", f"vp_{idx}")])
    if total_pages > 1:
        keyboard.append(
            [
                _btn("◀️ 上一頁", f"post_page:{max(0, page - 1)}"),
                _btn(f"{page + 1}/{total_pages}", f"post_page:{page}"),
                _btn("下一頁 ▶️", f"post_page:{min(total_pages - 1, page + 1)}"),
            ]
        )
    suffix = "_ct_paid" if branch == "r18" else "_ct_free" if branch == "nonr18" else ""
    footer_rows = []
    if posts:
        footer_rows.extend([[_btn("🚀 發布推文", f"bulkpub_{persona_id}{suffix}_p{page}")], [_btn("🗑 刪除推文", f"bulkdel_{persona_id}{suffix}_p{page}")]])
    footer_rows.extend([[_btn("⭐ 收藏入口", f"favs_{persona_id}_p0")], [_btn("◀️ 返回人設詳情", f"pd_{persona_id}")]])
    keyboard.extend(footer_rows)
    return _message("\n".join(lines), keyboard)


def _bulk_delete_selected_posts(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    posts = [str(item) for item in draft.get("posts", [])]
    selected = {int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()}
    if not posts:
        return _response(_post_select_message(draft), state={"flow": "post_select", "draft": draft})
    keep = [text for idx, text in enumerate(posts) if idx not in selected]
    draft["posts"] = keep
    draft["selected"] = list(range(len(keep)))
    paths = [str(item) for item in draft.get("post_image_paths", [])]
    draft["post_image_paths"] = [path for idx, path in enumerate(paths) if idx not in selected]
    draft["post_image_candidates"] = {}
    _persist_generated_post_draft(draft)
    return _response([_message(f"已刪除 {len(selected)} 篇已選推文。", kind="status"), _post_select_message(draft)], state={"flow": "post_select", "draft": draft})


def _post_detail_message(draft: dict[str, Any], index: int) -> dict[str, Any]:
    posts = [to_traditional(str(item)) for item in draft.get("posts", [])]
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    if not (0 <= index < len(posts)):
        return _message("找不到這篇推文，請返回推文列表重新選擇。", [[_btn("📋 查看推文列表", "post_select_back")]])
    selected = {int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()}
    has_image = _post_image_exists(post_image_paths, index)
    candidates = _post_image_candidates(draft, index)
    existing_candidates = [path for path in candidates if path and Path(path).exists()]
    source_lines: list[str] = []
    if draft.get("hot_context"):
        source_lines.append("熱點來源：")
        source_lines.extend(_memory_excerpt(line, 120) for line in str(draft.get("hot_context") or "").splitlines()[:6] if str(line).strip())
    elif draft.get("memory"):
        source_lines.append("繼承記憶：")
        source_lines.append(_memory_excerpt(draft.get("memory"), 260))
    lines = [
        f"📝 第 {index + 1} 篇推文",
        "",
        f"人設：{draft.get('name') or draft.get('persona_id')}",
        f"狀態：{'已勾選發布' if index in selected else '未勾選'}｜圖片：{'有圖' if has_image else '暫無圖'}｜候選圖 {len(existing_candidates)} 張",
    ]
    if source_lines:
        lines.extend(["", *source_lines])
    lines.extend(["", posts[index]])
    if has_image:
        lines.extend(["", "🖼 這篇推文已有配圖，可直接發布或重新生成。"])
    else:
        lines.extend(["", "暫無配圖。可以單獨生成圖片，或選擇「根據文字內容生成圖片再發布」。"])

    image_row = (
        [_btn("🖼 查看候選圖/配圖", f"view_post_candidates:{index}"), _btn("🔄 重新生成圖片", f"genpost_image:{index}")]
        if existing_candidates
        else [_btn("🖼 單獨生成圖片", f"genpost_image:{index}")]
    )
    keyboard = _rows(
        [_btn("🚀 發布這篇", f"post_publish_one:{index}", "primary")],
        [_btn("刷新熱度", f"post_refresh_hot:{index}")] if draft.get("hot_context") else [],
        image_row,
        [_btn("✏️ 文案管理", f"post_edit:{index}"), _btn("⭐ 收藏這篇", f"post_favorite:{index}")],
        [_btn("🗑 刪除這篇", f"post_delete:{index}")],
        [_btn("◀️ 返回推文列表", "post_select_back")],
    )
    preview_path = post_image_paths[index] if has_image else (existing_candidates[0] if existing_candidates else "")
    return _message("\n".join(lines), keyboard, image=_image_data_url(preview_path) if preview_path else "")


def _post_detail_message(draft: dict[str, Any], index: int) -> dict[str, Any]:
    posts = [to_traditional(str(item)) for item in draft.get("posts", [])]
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    if not (0 <= index < len(posts)):
        return _message("找不到這篇推文，請返回推文列表重新選擇。", [[_btn("📝 返回推文列表", "post_select_back")]])
    selected = {int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()}
    has_image = _post_image_exists(post_image_paths, index)
    candidates = _post_image_candidates(draft, index)
    existing_candidates = [path for path in candidates if path and Path(path).exists()]
    lines = [
        f"📝 第 {index + 1} 篇推文",
        "",
        f"人設：{draft.get('name') or draft.get('persona_id')}",
        f"狀態：{'已勾選發布' if index in selected else '未勾選'}｜媒體：{'已有' if has_image else '暫無'}｜候選 {len(existing_candidates)} 張",
        "",
        posts[index],
    ]
    action_key = _tg_post_action_key(index)
    media_row = (
        [_btn("查看配圖/視頻", f"pa_media_{action_key}"), _btn("重新生成圖片", f"pa_img_{action_key}")]
        if existing_candidates or has_image
        else [_btn("單獨生成圖片", f"pa_img_{action_key}")]
    )
    keyboard = _rows(
        [_btn("🚀 發布這篇", f"pa_pub_{action_key}", "primary")],
        media_row,
        [_btn("重新生成推文", f"pa_edit_{action_key}"), _btn("收藏", f"pa_fav_{action_key}")],
        [_btn("刪除", f"pa_del_{action_key}")],
        [_btn("◀️ 返回列表", "pa_back")],
    )
    preview_path = post_image_paths[index] if has_image else (existing_candidates[0] if existing_candidates else "")
    return _message("\n".join(lines), keyboard, image=_image_data_url(preview_path) if preview_path else "")


def _post_image_exists(post_image_paths: list[str], index: int) -> bool:
    return 0 <= index < len(post_image_paths) and bool(post_image_paths[index]) and Path(post_image_paths[index]).exists()


def _post_image_status_label(post_image_paths: list[str], index: int) -> str:
    if _post_image_exists(post_image_paths, index):
        return "🖼 有圖"
    if 0 <= index < len(post_image_paths) and post_image_paths[index]:
        return "⚠️ 圖檔遺失"
    return "⬜ 暫無圖（帶圖發布會自動補圖）"


def _post_image_short_label(post_image_paths: list[str], index: int) -> str:
    if _post_image_exists(post_image_paths, index):
        return "有圖"
    if 0 <= index < len(post_image_paths) and post_image_paths[index]:
        return "圖遺失"
    return "暫無圖"


def _post_image_counts(posts: list[str], post_image_paths: list[str], indexes: set[int] | list[int] | None = None) -> tuple[int, int]:
    if indexes is None:
        selected_indexes = range(len(posts))
    else:
        selected_indexes = [int(item) for item in indexes if 0 <= int(item) < len(posts)]
    with_image = sum(1 for index in selected_indexes if _post_image_exists(post_image_paths, index))
    total = len(list(range(len(posts))) if indexes is None else selected_indexes)
    return with_image, max(0, total - with_image)


def _post_select_action(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    posts = [str(item) for item in draft.get("posts", [])]
    selected = {int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()}
    page, _total_pages, visible_indexes = _post_select_page_info(draft, len(posts))
    if action.startswith("btog:"):
        idx = _num(action.split(":", 1)[1])
        if idx in selected:
            selected.remove(idx)
        elif 0 <= idx < len(posts):
            selected.add(idx)
    elif action == "bsel_page":
        selected.update(visible_indexes)
    elif action == "bclear_page":
        selected.difference_update(visible_indexes)
    elif action == "bconfirm":
        draft["selected"] = sorted(selected)
        _persist_generated_post_draft(draft)
        return _publish_confirm_from_posts(draft)
    draft["selected"] = sorted(selected)
    draft["post_page"] = page
    _persist_generated_post_draft(draft)
    return _response(_post_select_message(draft), state={"flow": "post_select", "draft": draft})


def _post_page_action(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    page = _num(action.split(":", 1)[1]) if ":" in action else 0
    draft["post_page"] = page
    _persist_generated_post_draft(draft)
    return _response(_post_select_message(draft), state={"flow": "post_select", "draft": draft})


def _post_view_action(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    index = _num(action.split(":", 1)[1]) if ":" in action else -1
    draft["post_page"] = max(0, index // max(1, STORED_POSTS_PAGE_SIZE))
    return _response(_post_detail_message(draft, index), state={"flow": "post_select", "draft": draft})


def _post_publish_one(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    index = _num(action.split(":", 1)[1]) if ":" in action else -1
    posts = [str(item) for item in draft.get("posts", [])]
    if not (0 <= index < len(posts)):
        return _response(_message("找不到這篇推文，請返回推文列表重新選擇。", [[_btn("📋 查看推文列表", "post_select_back")]]), state={"flow": "post_select", "draft": draft})
    draft["selected"] = [index]
    draft["post_page"] = max(0, index // max(1, STORED_POSTS_PAGE_SIZE))
    return _publish_confirm_from_posts(draft)


def _post_favorite(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    index = _num(action.split(":", 1)[1]) if ":" in action else -1
    posts = [str(item) for item in draft.get("posts", [])]
    persona_id = str(draft.get("persona_id") or "")
    if not persona_id or not (0 <= index < len(posts)):
        return _response(_message("目前無法收藏這篇推文。", [[_btn("📋 查看推文列表", "post_select_back")]]), state={"flow": "post_select", "draft": draft})
    memory = _record_post_memory(
        persona_id,
        posts[index],
        granularity=str(draft.get("memory_granularity") or "daily"),
        source_type="single_post_favorite",
        source_ref=str(draft.get("generated_memory_id") or draft.get("memory_id") or ""),
        title=f"收藏第 {index + 1} 篇推文",
        favorite=True,
        payload={"post_index": index, "hot_context": str(draft.get("hot_context") or "")[:1200]},
    )
    return _response(
        [_message(f"⭐ 已收藏第 {index + 1} 篇推文。", kind="status"), _post_detail_message(draft, index)],
        state={"flow": "post_select", "draft": draft},
    )


def _post_delete(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    index = _num(action.split(":", 1)[1]) if ":" in action else -1
    posts = [str(item) for item in draft.get("posts", [])]
    if not (0 <= index < len(posts)):
        return _response(_message("找不到這篇推文，請返回推文列表重新選擇。", [[_btn("📋 查看推文列表", "post_select_back")]]), state={"flow": "post_select", "draft": draft})
    posts.pop(index)
    draft["posts"] = posts
    paths = [str(item) for item in draft.get("post_image_paths", [])]
    if 0 <= index < len(paths):
        paths.pop(index)
    draft["post_image_paths"] = paths
    selected = {int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()}
    draft["selected"] = sorted((item if item < index else item - 1) for item in selected if item != index and item < len(posts) + 1)
    old_candidates = dict(draft.get("post_image_candidates") if isinstance(draft.get("post_image_candidates"), dict) else {})
    new_candidates: dict[str, Any] = {}
    for key, value in old_candidates.items():
        try:
            old_index = int(key)
        except Exception:
            continue
        if old_index == index:
            continue
        new_index = old_index if old_index < index else old_index - 1
        if new_index >= 0:
            new_candidates[str(new_index)] = value
    draft["post_image_candidates"] = new_candidates
    page, total_pages, _visible = _post_select_page_info(draft, len(posts))
    draft["post_page"] = min(page, total_pages - 1)
    _persist_generated_post_draft(draft)
    return _response(
        [_message(f"🗑 已刪除第 {index + 1} 篇推文。", kind="status"), _post_select_message(draft)],
        state={"flow": "post_select", "draft": draft},
    )


def _post_edit_prompt(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    index = _num(action.split(":", 1)[1]) if ":" in action else -1
    posts = [str(item) for item in draft.get("posts", [])]
    if not (0 <= index < len(posts)):
        return _response(_message("找不到這篇推文，請返回推文列表重新選擇。", [[_btn("📋 查看推文列表", "post_select_back")]]), state={"flow": "post_select", "draft": draft})
    draft["edit_index"] = index
    return _response(
        _message(
            f"✏️ 文案管理\n\n目前正在修改第 {index + 1} 篇推文。請直接送出新的完整文案。\n\n原文：\n{_memory_excerpt(posts[index], 360)}",
            _rows([_btn("◀️ 返回單篇詳情", f"post_view:{index}")]),
        ),
        state={"flow": "genpost_edit_post", "draft": draft},
    )


def _post_refresh_hot(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    index = _num(action.split(":", 1)[1]) if ":" in action else 0
    persona_id = str(draft.get("persona_id") or "")
    local, row = _resolve_persona_for_action(persona_id)
    if local:
        persona_id = local.id
    context = _persona_hot_context(persona_id, row, global_context=False)
    if context:
        memory = _record_post_memory(
            persona_id,
            context,
            granularity="hot",
            source_type="persona_hot_opinion_refresh",
            source_ref=persona_id,
            title="刷新熱點輿情",
        )
        draft["hot_context"] = context
        draft["memory"] = context
        draft["memory_id"] = memory.id if memory else str(draft.get("memory_id") or "")
        draft["memory_granularity"] = "hot"
    return _response(
        [_message("✅ 已刷新熱點資料並保存為熱點記憶。", kind="status"), _post_detail_message(draft, index)],
        state={"flow": "post_select", "draft": draft},
    )


def _generate_single_post_image(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    persona_id = str(draft.get("persona_id") or "")
    persona = PersonaRepo.get(persona_id)
    posts = [str(item) for item in draft.get("posts", [])]
    index = _num(action.split(":", 1)[1]) if ":" in action else -1
    if not persona:
        return _response(
            _message("这个人设还没有本地资料，无法生成配图。", [[_btn("◀️ 返回推文选择", "post_select_back")]]),
            state={"flow": "post_select", "draft": draft},
        )
    if not (0 <= index < len(posts)):
        return _response(
            _message("找不到这篇推文，请回到推文列表重新选择。", [[_btn("◀️ 返回推文选择", "post_select_back")]]),
            state={"flow": "post_select", "draft": draft},
        )
    if not _avatar_exists(persona):
        draft["image_group"] = index + 1
        return _response(
            _message(
                "⚠️ 此人設尚未生成人設圖。\n\n推文配圖必須先使用人設圖鎖定人物長相；請先點擊下方按鈕生成人設圖。",
                [[_btn("🎨 生成人設圖", f"genimg_{persona_id}")], [_btn("◀️ 返回推文列表", "post_select_back")]],
            ),
            state={"flow": "genpost_no_reference", "draft": draft},
        )
    try:
        _generate_post_image_candidates_for_index(persona, posts, index, draft)
    except Exception as exc:
        return _response(
            _message(
                f"第 {index + 1} 篇候选图生成失败：{exc}",
                [[_btn("🔄 再试一次", f"pa_img_{_tg_post_action_key(index)}")], [_btn("◀️ 返回推文选择", "post_select_back")]],
            ),
            state={"flow": "post_select", "draft": draft},
        )
    candidate_count = _post_candidate_count(draft, index)
    return _response(
        [_message(f"✅ 已生成第 {index + 1} 篇候选图，共 {candidate_count} 张。请选择其中 1 张。", kind="status"), _post_candidate_message(draft, index)],
        state={"flow": "post_select", "draft": draft},
    )


def _regenerate_post_images(state: dict[str, Any], *, next_group: bool = False) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    persona_id = str(draft.get("persona_id") or "")
    persona = PersonaRepo.get(persona_id)
    posts = [str(item) for item in draft.get("posts", [])]
    if not persona:
        return _response(
            _message("这个人设还没有本地资料，无法生成配图。", [[_btn("◀️ 返回推文选择", "post_select_back")]]),
            state={"flow": "post_select", "draft": draft},
        )
    if not posts:
        return _response(
            _message("目前没有可配图的推文，请先重新生成推文。", [[_btn("✍️ 重新生成推文", f"genpost:{persona_id}")]]),
            state={"flow": "post_select", "draft": draft},
        )
    current_group = max(1, _num(draft.get("image_group")) or 1)
    target_index = current_group if next_group else current_group - 1
    if target_index >= len(posts):
        target_index = 0
    if target_index < 0:
        target_index = 0
    if not _avatar_exists(persona):
        draft["image_group"] = target_index + 1
        return _response(
            _message(
                "⚠️ 此人設尚未生成人設圖。\n\n推文配圖必須先使用人設圖鎖定人物長相；請先點擊下方按鈕生成人設圖。",
                [[_btn("🎨 生成人設圖", f"genimg_{persona_id}")], [_btn("◀️ 返回推文列表", "post_select_back")]],
            ),
            state={"flow": "genpost_no_reference", "draft": draft},
        )
    try:
        _generate_post_image_candidates_for_index(persona, posts, target_index, draft)
    except Exception as exc:
        return _response(
            _message(f"配图生成失败：{exc}", [[_btn("🔄 再试一次", "regen_post_images"), _btn("◀️ 返回推文选择", "post_select_back")]]),
            state={"flow": "post_select", "draft": draft},
        )
    candidate_count = _post_candidate_count(draft, target_index)
    return _response(
        [_message(f"✅ 已生成第 {target_index + 1}/{len(posts)} 篇候选图，共 {candidate_count} 张。请选择其中 1 张。", kind="status"), _post_candidate_message(draft, target_index)],
        state={"flow": "post_select", "draft": draft},
    )


def _publish_context(persona_id: str) -> tuple[str, Persona | None, dict[str, Any] | None, str]:
    local, row = _resolve_persona_for_action(persona_id)
    source_persona_id = _tool_r18_archive_id(persona_id, local, row) or (local.id if local else persona_id)
    if local:
        name = _local_persona_display_name(local)
        if row and _is_placeholder_persona_name(name, local.pad_code):
            name = _persona_row_name(row)
    elif row:
        name = _persona_row_name(row)
    else:
        name = "人设"
    return source_persona_id, local, row, name


def _publish_username_candidates(persona: Persona | None, row: dict[str, Any] | None = None) -> set[str]:
    candidates: set[str] = set()
    if persona:
        if persona.account_username:
            candidates.add(persona.account_username)
        candidates.add(f"persona_{persona.id}")
        for account in AccountRepo.list_all():
            if persona.pad_code and account.pad_code == persona.pad_code:
                candidates.add(account.username)
            if persona.name and account.persona == persona.name:
                candidates.add(account.username)
            if persona.name and account.alias == persona.name:
                candidates.add(account.username)
    if row:
        value = str(row.get("account_username") or "").strip()
        if value:
            candidates.add(value)
        threads = row.get("threads_account") if isinstance(row.get("threads_account"), dict) else {}
        handle = str(threads.get("handle") or "").strip()
        if handle:
            candidates.add(handle)
    return {item for item in candidates if item}


def _publish_tasks_for_persona(persona: Persona | None, row: dict[str, Any] | None = None, *, limit: int = 10000) -> list:
    usernames = _publish_username_candidates(persona, row)
    if not usernames:
        return []
    return [task for task in TaskRepo.list_all(limit=limit) if task.username in usernames]


def _task_status_label(status: str) -> str:
    labels = {
        "pending": "待發布",
        "publishing": "發布中",
        "done": "完成",
        "success": "完成",
        "failed": "失敗",
        "cancelled": "取消",
    }
    return labels.get(str(status or ""), str(status or "-"))


def _task_time(value: Any) -> str:
    try:
        ts = float(value or 0)
    except Exception:
        ts = 0.0
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _task_media_label(task: Any) -> str:
    media = getattr(task, "media_list", []) or []
    if not media:
        return "無圖"
    return f"{len(media)} 個媒體"


def _task_preview(text: Any, size: int = 96) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact[:size] + ("..." if len(compact) > size else "")


def _remote_post_preview(post: dict[str, Any], size: int = 96) -> str:
    return _task_preview(post.get("content") or post.get("text") or post.get("caption") or post.get("source_url") or "未讀取到文案", size)


def _remote_history_preview(item: dict[str, Any], size: int = 96) -> str:
    return _task_preview(
        item.get("text")
        or item.get("content")
        or item.get("caption")
        or item.get("post_text")
        or item.get("source_url")
        or item.get("url")
        or "來源歷史記錄",
        size,
    )


def _remote_history_time(item: dict[str, Any]) -> str:
    for key in ("published_at", "created_at", "scheduled_at", "time", "updated_at"):
        value = item.get(key)
        if not value:
            continue
        if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
            return _task_time(value)
        return str(value)[:19].replace("T", " ")
    return "-"


def _source_count(row: dict[str, Any] | None, key: str) -> int:
    if not isinstance(row, dict):
        return 0
    counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
    value = counts.get(key)
    if value is None and key == "posts":
        value = row.get("postCount") or row.get("post_count")
    if value is None and key == "published":
        value = row.get("publishedCount") or row.get("published_count")
    return _num(value)


def _source_pending_posts(row: dict[str, Any] | None, content_type: str = "") -> list[dict[str, Any]]:
    posts = row.get("pending_posts") if isinstance(row, dict) and isinstance(row.get("pending_posts"), list) else []
    result = [item for item in posts if isinstance(item, dict) and str(item.get("id") or "").strip()]
    if content_type in {"free", "paid"}:
        result = [
            item
            for item in result
            if str(item.get("telegramGroupContentType") or item.get("telegram_group_content_type") or "free").strip().lower()
            == content_type
        ]
    return result


def _source_favorite_posts(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    posts = row.get("favorite_posts") if isinstance(row, dict) and isinstance(row.get("favorite_posts"), list) else []
    return [item for item in posts if isinstance(item, dict) and str(item.get("id") or "").strip()]


def _source_post_collection(
    row: dict[str, Any] | None,
    source: str = "posts",
    content_type: str = "",
) -> list[dict[str, Any]]:
    return _source_favorite_posts(row) if source == "favorites" else _source_pending_posts(row, content_type)


def _source_posts_callback(
    archive_id: str,
    *,
    source: str = "posts",
    content_type: str = "",
    page: int = 0,
) -> str:
    if source == "favorites":
        return f"favs_{archive_id}_p{max(0, page)}"
    suffix = f"_ct_{content_type}" if content_type in {"free", "paid"} else ""
    return f"posts_{archive_id}{suffix}_p{max(0, page)}"


def _source_post_detail_callback(
    archive_id: str,
    post_id: str,
    *,
    source: str = "posts",
    content_type: str = "",
    page: int = 0,
) -> str:
    return ":".join([
        "source_post",
        urllib.parse.quote(str(archive_id or ""), safe=""),
        urllib.parse.quote(str(post_id or ""), safe=""),
        "favorites" if source == "favorites" else "posts",
        content_type if content_type in {"free", "paid"} else "all",
        str(max(0, page)),
    ])


def _source_post_image_retry_callback(
    archive_id: str,
    post_id: str,
    *,
    source: str = "posts",
    content_type: str = "",
    page: int = 0,
    post_index: int = 0,
) -> str:
    return ":".join([
        "source_post_image_retry",
        urllib.parse.quote(str(archive_id or ""), safe=""),
        urllib.parse.quote(str(post_id or ""), safe=""),
        "favorites" if source == "favorites" else "posts",
        content_type if content_type in {"free", "paid"} else "all",
        str(max(0, page)),
        str(max(0, post_index)),
    ])


def _source_post_type_label(post: dict[str, Any]) -> str:
    urls = _source_post_media_urls(post)
    if not urls:
        return "純文字"
    has_video = any(re.search(r"^data:video/|\.(?:mp4|mov|m4v|webm)(?:[?#].*)?$", url, re.I) for url in urls)
    has_image = any(not re.search(r"^data:video/|\.(?:mp4|mov|m4v|webm)(?:[?#].*)?$", url, re.I) for url in urls)
    if has_video and has_image:
        return "圖片+視頻"
    return "視頻" if has_video else "圖片"


def _source_post_media_urls(post: dict[str, Any]) -> list[str]:
    values: list[str] = []
    def add_items(raw: Any) -> None:
        if not isinstance(raw, list):
            return
        for item in raw:
            value = str(item.get("url") if isinstance(item, dict) else item or "").strip()
            if value:
                values.append(value)

    add_items(post.get("mediaItems"))
    source_meta = post.get("sourceMeta") if isinstance(post.get("sourceMeta"), dict) else {}
    add_items(source_meta.get("mediaItems"))
    for key in ("imageUrl", "image_url", "mediaUrl", "media_url"):
        value = str(post.get(key) or "").strip()
        if value:
            values.append(value)
    history = post.get("imageHistory") if isinstance(post.get("imageHistory"), list) else []
    if history:
        value = str((history[-1] if isinstance(history[-1], dict) else {}).get("imageUrl") or "").strip()
        if value:
            values.append(value)
    if not values:
        raw = post.get("mediaUrls") if isinstance(post.get("mediaUrls"), list) else post.get("media_urls")
        if isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    return list(dict.fromkeys(values))


def _persona_content_type_picker(persona_id: str, target: str) -> dict[str, Any]:
    local, row = _resolve_persona_for_action(persona_id)
    if not local and not row:
        return _response(_message("找不到這個人設。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    if local:
        persona_id = local.id
    if not _is_workflow_persona_row(row, persona_id):
        if target == "history":
            return _publish_history(f"pub_history:0:{persona_id}")
        if target == "publish":
            return _publish_center(persona_id)
        return _publish_posts_list(f"pub_posts:0:{persona_id}")
    if target == "history":
        items = row.get("publish_history") if isinstance((row or {}).get("publish_history"), list) else []
    else:
        items = _source_pending_posts(row)
    counts = {"free": 0, "paid": 0}
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("telegramGroupContentType") or item.get("telegram_group_content_type") or "free").strip().lower()
        counts["paid" if kind == "paid" else "free"] += 1
    title = "發布歷史" if target == "history" else "發布推文" if target == "publish" else "待發布推文"
    prefix = "history" if target == "history" else "pub" if target == "publish" else "posts"
    return _response(
        _message(
            f"請選擇要查看的{title}內容類型：",
            _rows(
                [
                    _btn(f"免費內容（{counts['free']}）", f"{prefix}_{persona_id}_ct_free"),
                    _btn(f"付費內容（{counts['paid']}）", f"{prefix}_{persona_id}_ct_paid"),
                ],
                [_btn("◀️ 返回人設詳情", f"pd_{persona_id}")],
            ),
        ),
        state={"flow": "", "draft": {"persona_id": persona_id}},
    )


def _publish_center(persona_id: str, content_type: str = "") -> dict[str, Any]:
    persona_id, persona, row, name = _publish_context(persona_id)
    if not persona and not row:
        return _response(_message("没有找到本地人设，不能创建发布任务。", [[_btn("◀️ 返回人设列表", "list_personas")]]))

    tasks = _publish_tasks_for_persona(persona, row)
    open_tasks = [task for task in tasks if task.status in {"pending", "publishing"}]
    history_tasks = [task for task in tasks if task.status not in {"pending", "publishing"}]
    source_posts = _source_pending_posts(row, content_type)
    source_history = row.get("publish_history") if isinstance((row or {}).get("publish_history"), list) else []
    source_post_count = max(len(source_posts), _source_count(row, "posts"))
    source_history_count = max(len(source_history), _source_count(row, "published"))
    issue = _publish_device_issue(persona) if persona else ""
    device_line = "綁定狀態：未綁定雲機"
    if persona and persona.pad_code:
        device = DeviceRepo.get(persona.pad_code)
        device_line = f"雲機名稱：{(device.alias if device else '') or persona.pad_code}\nPAD_CODE：{persona.pad_code}"

    lines = [
        "🚀 發布推文",
        "",
        f"人設：{name}",
        device_line,
        "",
        f"待發布/發布中：{len(open_tasks)} 篇",
        f"本機發布歷史：{len(history_tasks)} 筆",
        f"來源推文資料：{source_post_count} 篇",
        f"來源發布歷史：{source_history_count} 筆",
    ]
    if issue:
        lines.extend(["", "⚠️ " + issue])
    if source_post_count and not source_posts:
        lines.extend(["", f"來源目前只返回 {source_post_count} 篇待發布數量，未返回可直接列出的單帖明細。"])
    if not open_tasks and not source_post_count:
        lines.extend(["", "目前沒有可選的推文列表。可以先「生成推文」，或用「直接發新內容」建立一篇。"])

    return _response(
        _message(
            "\n".join(lines),
            _rows(
                [_btn("📋 查看推文列表", f"posts_{persona_id}{f'_ct_{content_type}' if content_type else ''}_p0"), _btn("🕘 发布历史", f"history_{persona_id}{f'_ct_{content_type}' if content_type else ''}")],
                [_btn("✍️ 生成推文", f"genpost:{persona_id}"), _btn("✏️ 直接发新内容", f"pub_direct:{persona_id}")],
                [_btn("📋 打开发帖任务", "open:/tasks"), _btn("📊 人设数据", f"open:/personas/{persona_id}/data")],
                [_btn("◀️ 返回", f"pub_branch_{persona_id}" if content_type else f"pd:{persona_id}")],
            ),
        ),
        state={"flow": "publish_center", "draft": {"persona_id": persona_id, "name": name}},
    )


def _parse_publish_page_action(action: str) -> tuple[int, str]:
    parts = action.split(":", 2)
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    persona_id = parts[2] if len(parts) > 2 else ""
    return max(0, page), persona_id


def _publish_posts_list(action: str, content_type: str = "", source: str = "posts") -> dict[str, Any]:
    page, persona_id = _parse_publish_page_action(action)
    persona_id, persona, row, name = _publish_context(persona_id)
    if not persona and not row:
        return _response(_message("没有找到这个人设。", [[_btn("◀️ 返回人设列表", "list_personas")]]), state={"flow": ""})

    archive_id = _tool_r18_archive_id(persona_id, persona, row) or persona_id
    posts = _source_post_collection(row, source, content_type)
    total_pages = max(1, (len(posts) + STORED_POSTS_PAGE_SIZE - 1) // STORED_POSTS_PAGE_SIZE)
    safe_page = min(page, total_pages - 1)
    start = safe_page * STORED_POSTS_PAGE_SIZE
    visible = posts[start : start + STORED_POSTS_PAGE_SIZE]
    title = "⭐ 收藏推文" if source == "favorites" else "📝 待發布推文列表"
    branch = " - 付費內容" if content_type == "paid" else " - 免費內容" if content_type == "free" else ""
    lines = [f"{title}{branch}（共 {len(posts)} 篇，第 {safe_page + 1}/{total_pages} 頁）"]
    if not visible:
        lines.extend(["", "当前没有收藏推文。" if source == "favorites" else "当前没有待发布推文。"])
    for offset, post in enumerate(visible, start=start + 1):
        metric = post.get("sourceMeta") if isinstance(post.get("sourceMeta"), dict) else {}
        metric_line = ""
        if metric.get("source") == "sentiment_hot_import":
            score = metric.get("hotScore")
            metric_line = f"\n數據：熱度 {score}" if score not in (None, "") else ""
        lines.extend([
            "",
            f"【{offset}】類型: {_source_post_type_label(post)}{metric_line}",
            _remote_post_preview(post, 120),
        ])

    keyboard = [
        [_btn(f"👁 查看第{start + index + 1}篇（{_source_post_type_label(post)}）", f"vp_{start + index}")]
        for index, post in enumerate(visible)
    ]
    if total_pages > 1:
        keyboard.extend(_rows(
            [_btn("⏮ 首頁", _source_posts_callback(archive_id, source=source, content_type=content_type, page=0)), _btn("◀️ 上一頁", _source_posts_callback(archive_id, source=source, content_type=content_type, page=max(0, safe_page - 1)))],
            [_btn(f"{safe_page + 1}/{total_pages}", _source_posts_callback(archive_id, source=source, content_type=content_type, page=safe_page))],
            [_btn("下一頁 ▶️", _source_posts_callback(archive_id, source=source, content_type=content_type, page=min(total_pages - 1, safe_page + 1))), _btn("尾頁 ⏭", _source_posts_callback(archive_id, source=source, content_type=content_type, page=total_pages - 1))],
        ))
    if source != "favorites":
        keyboard.append([_btn(f"⭐ 收藏推文（{len(_source_favorite_posts(row))}）", _source_posts_callback(archive_id, source="favorites"))])
        if posts:
            suffix = f"_ct_{content_type}" if content_type else ""
            keyboard.append([_btn("🚀 發布推文", f"bulkpub_{archive_id}{suffix}_p{safe_page}"), _btn("🗑 刪除推文", f"bulkdel_{archive_id}{suffix}_p{safe_page}")])
    back_action = f"posts_branch_{archive_id}" if content_type else f"pd_{archive_id}"
    keyboard.append([_btn("◀️ 返回", back_action)])
    return _response(
        _message("\n".join(lines), keyboard),
        state={
            "flow": "source_posts_list",
            "draft": {
                "persona_id": archive_id,
                "archive_id": archive_id,
                "name": name,
                "source": source,
                "group_content_type": content_type,
                "post_page": safe_page,
                "source_post_ids": [str(post.get("id") or "") for post in posts],
            },
        },
    )


def _source_archive_post(
    archive_id: str,
    post_id: str,
    source: str = "posts",
    content_type: str = "",
) -> tuple[Persona | None, dict[str, Any] | None, dict[str, Any] | None]:
    local, row = _resolve_persona_for_action(archive_id)
    for post in _source_post_collection(row, source, content_type):
        if str(post.get("id") or "").strip() == str(post_id or "").strip():
            return local, row, post
    return local, row, None


def _source_post_view_from_state(index: int, state: dict[str, Any]) -> dict[str, Any] | None:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    post_ids = [str(item) for item in draft.get("source_post_ids", []) if str(item).strip()]
    if not post_ids or not (0 <= index < len(post_ids)):
        return None
    archive_id = str(draft.get("archive_id") or draft.get("persona_id") or "").strip()
    if not archive_id:
        return None
    return _source_post_detail(
        f"source_post:{archive_id}:{post_ids[index]}",
        context=draft,
    )


def _web_post_action_key(archive_id: str, post_id: str) -> str:
    return hashlib.sha1(f"{archive_id}|{post_id}".encode("utf-8", errors="ignore")).hexdigest()[:10]


def _web_post_action_from_state(action: str, state: dict[str, Any]) -> tuple[str, str, str] | None:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    archive_id = str(draft.get("archive_id") or draft.get("persona_id") or "").strip()
    post_id = str(draft.get("post_id") or "").strip()
    parts = str(action or "").split("_")
    key = parts[2] if len(parts) > 2 and parts[0] == "pa" else ""
    if not archive_id or not post_id or key != _web_post_action_key(archive_id, post_id):
        return None
    return archive_id, post_id, key


def _is_source_post_action_state(state: dict[str, Any]) -> bool:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    return bool(str(draft.get("archive_id") or "").strip() and str(draft.get("post_id") or "").strip())


def _expired_source_post_action() -> dict[str, Any]:
    return _response(_message("推文操作已過期，請重新打開推文。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})


def _source_post_detail(action: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = dict(context or {})
    parts = str(action or "").split(":")
    if len(parts) < 3:
        return _response(_message("推文入口無效，請返回人設重新選擇。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    archive_id = urllib.parse.unquote(parts[1])
    post_id = urllib.parse.unquote(parts[2])
    source_token = parts[3] if len(parts) > 3 else str(context.get("source") or "posts")
    content_token = parts[4] if len(parts) > 4 else str(context.get("group_content_type") or "")
    page_token = parts[5] if len(parts) > 5 else context.get("post_page")
    source = "favorites" if source_token == "favorites" else "posts"
    content_type = content_token if content_token in {"free", "paid"} else ""
    page = max(0, _num(page_token))
    local, row, post = _source_archive_post(archive_id, post_id, source, content_type)
    if not post:
        return _response(_message("找不到這篇待發布推文，資料可能已更新。", [[_btn("📝 返回推文列表", _source_posts_callback(archive_id, source=source, content_type=content_type, page=page))]]), state={"flow": ""})
    archive_id = _tool_r18_archive_id(archive_id, local, row) or archive_id
    content = str(post.get("content") or post.get("text") or "").strip()
    group_type = str(post.get("telegramGroupContentType") or post.get("telegram_group_content_type") or "free").strip().lower()
    media_urls = _source_post_media_urls(post)
    safe_media_urls = [url for item in media_urls if (url := _safe_web_media_url(item))]
    preview_image = next((url for url in safe_media_urls if _is_web_image_url(url)), "")
    post_rows = _source_post_collection(row, source, content_type)
    post_index = next((index for index, item in enumerate(post_rows) if str(item.get("id") or "") == post_id), 0)
    action_key = _web_post_action_key(archive_id, post_id)
    source_meta = post.get("sourceMeta") if isinstance(post.get("sourceMeta"), dict) else {}
    is_sentiment = str(source_meta.get("source") or "") == "sentiment_hot_import"
    favorites = _source_favorite_posts(row)
    favorite_added = any(
        str(item.get("id") or "") == post_id
        or str((item.get("sourceMeta") if isinstance(item.get("sourceMeta"), dict) else {}).get("favoriteSourcePostId") or "") == post_id
        for item in favorites
    )
    media_cards = [
        {
            "title": f"媒體 {index}",
            "subtitle": "圖片" if _is_web_image_url(url) else "視頻 / 媒體文件",
            **({"image": url} if _is_web_image_url(url) and url != preview_image else {"url": url}),
        }
        for index, url in enumerate(safe_media_urls, start=1)
        if url != preview_image
    ]
    lines = [
        f"📝 查看第 {post_index + 1} 篇推文",
        "",
        f"人設：{_persona_row_name(row or {})}",
        f"內容類型：{'付費內容' if group_type == 'paid' else '免費內容'}",
        f"媒體：{len(media_urls)} 個" if media_urls else "媒體：暫無配圖/視頻",
        "",
        content or "（空內容）",
    ]
    rows: list[list[dict[str, str]]] = [[_btn("🚀 發布這篇", f"pa_pub_{action_key}")]]
    if is_sentiment and source != "favorites":
        rows.append([_btn("刷新熱度", f"pa_rf_{action_key}")])
    if is_sentiment or source == "favorites":
        rows.append([_btn("🧩 媒體管理", f"pa_mm_{action_key}")]) if media_urls else rows.append([_btn("🖼 單獨生成配圖", f"post_img_regen_{archive_id}_{post_index}")])
        rows.append([_btn("✏️ 文案管理", f"pa_ed_{action_key}")])
    else:
        if media_urls:
            rows.append([_btn("🖼 查看配圖/視頻", f"pa_mp_{action_key}")])
        rows.append([_btn("🔄 重新生成推文", f"pa_rg_{action_key}")])
        rows.append([_btn("🖼 重新生成圖片" if media_urls else "🖼 單獨生成配圖", f"post_img_regen_{archive_id}_{post_index}")])
    if source != "favorites":
        rows.append([_btn("⭐ 已收藏" if favorite_added else "⭐ 收藏這篇", f"pa_v_{action_key}" if favorite_added else f"pa_fav_{action_key}")])
    rows.extend([
        [_btn("🗑 刪除這篇", f"pa_del_{action_key}")],
        [_btn("◀️ 返回收藏推文" if source == "favorites" else "◀️ 返回推文列表", _source_posts_callback(archive_id, source=source, content_type=content_type, page=page))],
    ])
    next_context = {
        **context,
        "persona_id": archive_id,
        "archive_id": archive_id,
        "post_id": post_id,
        "source": source,
        "group_content_type": content_type,
        "post_page": page,
        "post_index": post_index,
        "post_action_key": action_key,
        "source_post_ids": [str(item.get("id") or "") for item in post_rows],
        "is_sentiment_post": is_sentiment,
        "media_urls": media_urls,
    }
    return _response(
        _message(
            "\n".join(lines),
            rows,
            image=preview_image,
            cards=media_cards,
        ),
        state={"flow": "source_post_detail", "draft": next_context},
    )


def _source_post_action_context(state: dict[str, Any]) -> tuple[dict[str, Any], str, str, str, str, int]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    archive_id = str(draft.get("archive_id") or draft.get("persona_id") or "").strip()
    post_id = str(draft.get("post_id") or "").strip()
    source = "favorites" if str(draft.get("source") or "posts") == "favorites" else "posts"
    content_type = str(draft.get("group_content_type") or "")
    page = max(0, _num(draft.get("post_page")))
    content_type = str(draft.get("group_content_type") or "").strip().lower()
    if content_type not in {"free", "paid"}:
        content_type = ""
    return draft, archive_id, post_id, source, content_type, max(0, _num(draft.get("post_page")))


def _source_post_detail_from_state(state: dict[str, Any]) -> dict[str, Any]:
    draft, archive_id, post_id, source, content_type, page = _source_post_action_context(state)
    if not archive_id or not post_id:
        return _response(_message("推文操作已過期，請重新打開推文。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    return _source_post_detail(
        _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page),
        context=draft,
    )


def _source_post_media_preview(state: dict[str, Any]) -> dict[str, Any]:
    draft, archive_id, post_id, source, content_type, page = _source_post_action_context(state)
    _local, _row, post = _source_archive_post(archive_id, post_id, source, content_type)
    if not post:
        return _response(_message("沒有找到這篇推文。", [[_btn("◀️ 返回推文列表", _source_posts_callback(archive_id, source=source, content_type=content_type, page=page))]]), state={"flow": ""})
    urls = [url for value in _source_post_media_urls(post) if (url := _safe_web_media_url(value))]
    if not urls:
        return _response(_message("這篇推文沒有可預覽的配圖或視頻。", [[_btn("👁 查看這篇", _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page))]]), state=state)
    cards = [
        {
            "title": f"媒體 {index}",
            "subtitle": "圖片" if _is_web_image_url(url) else "視頻 / 媒體文件",
            **({"image": url} if _is_web_image_url(url) else {"url": url}),
        }
        for index, url in enumerate(urls, start=1)
    ]
    return _response(
        _message(
            f"🖼 推文媒體預覽\n\n共 {len(urls)} 個媒體。",
            _rows(
                [_btn("◀️ 返回查看推文", _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page))],
                [_btn("◀️ 返回推文列表", _source_posts_callback(archive_id, source=source, content_type=content_type, page=page))],
            ),
            image=next((url for url in urls if _is_web_image_url(url)), ""),
            cards=cards,
        ),
        state=state,
    )


def _source_post_regenerate_menu(state: dict[str, Any], *, edit_mode: bool = False) -> dict[str, Any]:
    draft, archive_id, post_id, source, content_type, page = _source_post_action_context(state)
    key = str(draft.get("post_action_key") or _web_post_action_key(archive_id, post_id))
    is_sentiment = bool(draft.get("is_sentiment_post"))
    if edit_mode:
        rows = _rows(
            [_btn("🤖 AI 重寫推文", f"pa_rai_{key}")],
            [_btn("✍️ 自訂文案", f"pa_rc_{key}")],
            [_btn("◀️ 返回查看推文", _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page))],
        )
        return _response(_message("✏️ 文案管理\n\n請選擇要執行的操作。", rows), state={"flow": "source_post_edit_menu", "draft": draft})
    rows = _rows(
        [_btn("🤖 AI 重新生成", f"pa_rai_{key}")],
        [_btn("✍️ 自訂發送文字", f"pa_rc_{key}")],
        [_btn("◀️ 返回查看推文", _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page))],
    )
    if is_sentiment:
        rows = _rows(
            [_btn("🧬 按原帖結構樣式生成", f"pa_ras_{key}")],
            [_btn("👤 按當前人設風格生成", f"pa_rap_{key}")],
            [_btn("◀️ 返回文案管理", f"pa_ed_{key}")],
        )
    return _response(_message("🔄 重新生成推文\n\n請選擇生成方式。", rows), state={"flow": "source_post_regenerate", "draft": draft})


def _source_post_action_submit(
    state: dict[str, Any],
    action_name: str,
    *,
    label: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    draft, archive_id, post_id, source, content_type, page = _source_post_action_context(state)
    if not archive_id or (not post_id and action_name != "delete_many"):
        return _response(_message("推文操作已過期，請重新打開推文。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    params: dict[str, Any] = {
        "archiveId": archive_id,
        "postId": post_id,
        "action": action_name,
        "source": source,
        "uiContentType": content_type,
        "uiPage": page,
        **(extra or {}),
    }
    response = _submit_source_post_task("persona_post_action", archive_id, post_id, params, label)
    response["state"] = {"flow": "source_post_task", "draft": draft}
    return response


def _source_post_delete_confirm(state: dict[str, Any]) -> dict[str, Any]:
    draft, archive_id, post_id, source, content_type, page = _source_post_action_context(state)
    key = str(draft.get("post_action_key") or _web_post_action_key(archive_id, post_id))
    label = "收藏推文" if source == "favorites" else "推文"
    return _response(
        _message(
            f"🗑 確認刪除{label}\n\n刪除後無法撤回。",
            _rows(
                [_btn(f"✅ 確認刪除這篇{label}", f"pa_del_{key}_confirm")],
                [_btn("取消，返回查看推文", _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page))],
                [_btn("◀️ 返回收藏推文" if source == "favorites" else "◀️ 返回推文列表", _source_posts_callback(archive_id, source=source, content_type=content_type, page=page))],
            ),
        ),
        state={"flow": "source_post_delete_confirm", "draft": draft},
    )


def _source_post_media_manager(state: dict[str, Any]) -> dict[str, Any]:
    draft, archive_id, post_id, source, content_type, page = _source_post_action_context(state)
    _local, _row, post = _source_archive_post(archive_id, post_id, source, content_type)
    if not post:
        return _response(_message("沒有找到這篇推文。", [[_btn("◀️ 返回推文列表", _source_posts_callback(archive_id, source=source, content_type=content_type, page=page))]]), state={"flow": ""})
    urls = _source_post_media_urls(post)
    selected = {int(item) for item in draft.get("selected_media_indexes", []) if str(item).isdigit() and 0 <= int(item) < len(urls)}
    key = str(draft.get("post_action_key") or _web_post_action_key(archive_id, post_id))
    rows: list[list[dict[str, str]]] = []
    for start in range(0, len(urls), 2):
        row_buttons = []
        for index in range(start, min(start + 2, len(urls))):
            kind = "視頻" if re.search(r"^data:video/|\.(?:mp4|mov|m4v|webm)(?:[?#].*)?$", urls[index], re.I) else "圖片"
            row_buttons.append(_btn(f"{'☑️' if index in selected else '⬜️'} {index + 1}.{kind}", f"pa_mt_{key}_{index}"))
        rows.append(row_buttons)
    rows.extend(_rows(
        [_btn("✅ 全選", f"pa_msa_{key}"), _btn("🧹 清空", f"pa_mcl_{key}")],
        [_btn(f"🗑 刪除選中 {len(selected)}", f"pa_md_{key}"), _btn(f"🔁 替換選中 {len(selected)}", f"pa_mrs_{key}")],
        [_btn(f"🤖 AI 生成圖片替換選中 {len(selected)}", f"pa_mra_{key}")],
        [_btn("◀️ 返回查看推文", _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page))],
    ))
    cards = [{"title": f"媒體 {index + 1}", **({"image": url} if _is_web_image_url(url) else {"url": url})} for index, url in enumerate(urls)]
    draft["selected_media_indexes"] = sorted(selected)
    return _response(
        _message(f"媒體管理\n\n媒體：{len(urls)} 個\n已選：{len(selected)} 個\n\n點擊下方編號可單選/多選。", rows, cards=cards),
        state={"flow": "source_post_media_manage", "draft": draft},
    )


def _source_post_media_replace_menu(state: dict[str, Any]) -> dict[str, Any]:
    draft, archive_id, post_id, _source, _content_type, _page = _source_post_action_context(state)
    selected = [int(item) for item in draft.get("selected_media_indexes", []) if str(item).isdigit()]
    if not selected:
        return _source_post_media_manager(state)
    key = str(draft.get("post_action_key") or _web_post_action_key(archive_id, post_id))
    return _response(
        _message(
            f"🔁 替換選中媒體\n\n已選：{len(selected)} 個\n請選擇替換方式。",
            _rows(
                [_btn("📤 手動上傳替換", f"pa_mru_{key}")],
                [_btn("🤖 AI 生成圖片替換", f"pa_mra_{key}")],
                [_btn("◀️ 返回媒體管理", f"pa_mm_{key}")],
            ),
        ),
        state={"flow": "source_post_media_replace", "draft": draft},
    )


def _source_bulk_start(action: str, mode: str) -> dict[str, Any]:
    prefix = "bulkpub_" if mode == "publish" else "bulkdel_"
    rest = action[len(prefix) :]
    page = 0
    if "_p" in rest:
        rest, page_text = rest.rsplit("_p", 1)
        page = max(0, _num(page_text))
    content_type = ""
    if "_ct_" in rest:
        archive_id, content_type = rest.split("_ct_", 1)
    else:
        archive_id = rest
    local, row = _resolve_persona_for_action(archive_id)
    archive_id = _tool_r18_archive_id(archive_id, local, row) or archive_id
    posts = _source_pending_posts(row, content_type)
    draft = {
        "archive_id": archive_id,
        "persona_id": archive_id,
        "source": "posts",
        "group_content_type": content_type,
        "post_page": page,
        "bulk_mode": mode,
        "source_post_ids": [str(post.get("id") or "") for post in posts],
        "selected_post_ids": [],
    }
    return _source_bulk_render(draft)


def _source_bulk_render(draft: dict[str, Any], note: str = "") -> dict[str, Any]:
    archive_id = str(draft.get("archive_id") or "")
    content_type = str(draft.get("group_content_type") or "")
    local, row = _resolve_persona_for_action(archive_id)
    posts = _source_pending_posts(row, content_type)
    post_ids = [str(post.get("id") or "") for post in posts]
    selected = {str(item) for item in draft.get("selected_post_ids", []) if str(item) in post_ids}
    page = max(0, _num(draft.get("post_page")))
    total_pages = max(1, (len(posts) + STORED_POSTS_PAGE_SIZE - 1) // STORED_POSTS_PAGE_SIZE)
    page = min(page, total_pages - 1)
    start = page * STORED_POSTS_PAGE_SIZE
    visible = posts[start : start + STORED_POSTS_PAGE_SIZE]
    mode = str(draft.get("bulk_mode") or "publish")
    action_text = "發布" if mode == "publish" else "刪除"
    lines = [f"請選擇要{action_text}的推文：", f"已選：{len(selected)} 篇"]
    if note:
        lines.extend(["", note])
    for index, post in enumerate(visible, start=start + 1):
        post_id = str(post.get("id") or "")
        lines.extend(["", f"{'☑️' if post_id in selected else '⬜'} 第 {index} 篇｜{_source_post_type_label(post)}", _remote_post_preview(post, 100)])
    rows = [
        [_btn(f"{'☑️' if str(post.get('id') or '') in selected else '⬜'} 第 {start + index + 1} 篇", f"sbtog_{start + index}")]
        for index, post in enumerate(visible)
    ]
    rows.extend(_rows(
        [_btn("☑️ 全選本頁", "sbsel_page"), _btn("⬜ 清空本頁", "sbclear_page")],
        [_btn(f"✅ 確認{action_text}（已選 {len(selected)} 篇）", "sbconfirm")],
        [_btn("◀️ 返回推文列表", _source_posts_callback(archive_id, content_type=content_type, page=page))],
    ))
    draft.update({"post_page": page, "source_post_ids": post_ids, "selected_post_ids": sorted(selected)})
    return _response(_message("\n".join(lines), rows), state={"flow": "source_post_bulk", "draft": draft})


def _source_bulk_toggle(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    post_ids = [str(item) for item in draft.get("source_post_ids", []) if str(item)]
    selected = {str(item) for item in draft.get("selected_post_ids", []) if str(item) in post_ids}
    page = max(0, _num(draft.get("post_page")))
    start = page * STORED_POSTS_PAGE_SIZE
    visible_ids = post_ids[start : start + STORED_POSTS_PAGE_SIZE]
    if action.startswith("sbtog_"):
        index = _num(action[len("sbtog_") :])
        if 0 <= index < len(post_ids):
            post_id = post_ids[index]
            selected.remove(post_id) if post_id in selected else selected.add(post_id)
    elif action == "sbsel_page":
        selected.update(visible_ids)
    elif action == "sbclear_page":
        selected.difference_update(visible_ids)
    draft["selected_post_ids"] = sorted(selected)
    return _source_bulk_render(draft)


def _source_bulk_confirm(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    selected = [str(item) for item in draft.get("selected_post_ids", []) if str(item)]
    if not selected:
        return _source_bulk_render(draft, "請至少選擇一篇推文。")
    archive_id = str(draft.get("archive_id") or "")
    content_type = str(draft.get("group_content_type") or "")
    page = max(0, _num(draft.get("post_page")))
    if draft.get("bulk_mode") == "delete":
        return _response(
            _message(
                f"🗑 確認刪除推文\n\n已選擇：{len(selected)} 篇\n刪除後無法撤回。",
                _rows(
                    [_btn("✅ 確認刪除", "sbdelete_confirm")],
                    [_btn("◀️ 返回選擇", "sbback")],
                    [_btn("◀️ 返回推文列表", _source_posts_callback(archive_id, content_type=content_type, page=page))],
                ),
            ),
            state={"flow": "source_post_bulk_delete_confirm", "draft": draft},
        )
    return _response(
        _message(
            f"🚀 批量發布推文\n\n已選擇：{len(selected)} 篇\n\n請選擇發布平台：",
            _rows(
                [_btn("🧵 Threads", "sbplatform_threads"), _btn("📣 Telegram 群組", "sbplatform_telegram")],
                [_btn("◀️ 返回選擇", "sbback")],
            ),
        ),
        state={"flow": "source_post_bulk_platform", "draft": draft},
    )


def _source_bulk_delete_execute(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    archive_id = str(draft.get("archive_id") or "")
    selected = [str(item) for item in draft.get("selected_post_ids", []) if str(item)]
    response = _submit_source_post_task(
        "persona_post_action",
        archive_id,
        "",
        {"archiveId": archive_id, "action": "delete_many", "source": "posts", "postIds": selected, "uiContentType": str(draft.get("group_content_type") or ""), "uiPage": _num(draft.get("post_page"))},
        "批量刪除推文",
    )
    response["state"] = {"flow": "source_post_task", "draft": draft}
    return response


def _source_bulk_publish_platform(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    platform = action[len("sbplatform_") :]
    archive_id = str(draft.get("archive_id") or "")
    local, row = _resolve_persona_for_action(archive_id)
    pad_code = str((local.pad_code if local else "") or (row or {}).get("bound_pad_code") or "").strip()
    if not pad_code:
        return _response(_message("這個人設尚未綁定智能體手機，請先綁定後再發布。", _rows([_btn("📱 綁定智能體手機", f"bindpad_{archive_id}")], [_btn("◀️ 返回選擇平台", "sbconfirm")])), state=state)
    draft.update({"platform": platform, "pad_code": pad_code})
    selected = [str(item) for item in draft.get("selected_post_ids", []) if str(item)]
    label = "Threads" if platform == "threads" else "Telegram 群組"
    return _response(
        _message(
            f"🚀 確認發布推文\n\n平台：{label}\n智能體手機：{pad_code}\n推文：{len(selected)} 篇",
            _rows(
                [_btn("✅ 確認發布到綁定智能體手機", "sbpublish_confirm")],
                [_btn("◀️ 返回選擇平台", "sbconfirm")],
            ),
        ),
        state={"flow": "source_post_bulk_publish_confirm", "draft": draft},
    )


def _source_bulk_publish_execute(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    archive_id = str(draft.get("archive_id") or "")
    platform = str(draft.get("platform") or "threads")
    pad_code = str(draft.get("pad_code") or "")
    selected = [str(item) for item in draft.get("selected_post_ids", []) if str(item)]
    if not archive_id or not selected or not pad_code:
        return _source_bulk_render(draft, "發布狀態已失效，請重新選擇推文和智能體手機。")
    response = _submit_source_post_task(
        "persona_publish_post",
        archive_id,
        selected[0],
        {"archiveId": archive_id, "postId": selected[0], "postIds": selected, "padCode": pad_code, "platform": platform, "postSource": "posts", "uiContentType": str(draft.get("group_content_type") or ""), "uiPage": _num(draft.get("post_page")), "dryRun": False},
        "批量真實發布任務",
    )
    response["state"] = {"flow": "source_post_task", "draft": draft}
    return response


def _source_post_pad_menu(state: dict[str, Any]) -> dict[str, Any]:
    draft, archive_id, post_id, source, content_type, page = _source_post_action_context(state)
    devices = _active_devices()
    selected = {str(item) for item in draft.get("selected_pad_codes", []) if str(item)}
    rows = [
        [_btn(f"{'☑️' if device.pad_code in selected else '⬜'} {device.alias or device.pad_code}", f"sppad:{device.pad_code}")]
        for device in devices[:20]
    ]
    rows.extend(_rows(
        [_btn("☑️ 全選本頁", "sppad_all"), _btn("⬜ 清空本頁", "sppad_clear")],
        [_btn(f"✅ 確認發布智能體手機（{len(selected)}）", "sppad_confirm")],
        [_btn("◀️ 返回發布確認", f"pa_pp_{draft.get('post_action_key')}_{draft.get('platform') or 'threads'}")],
        [_btn("◀️ 返回查看推文", _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page))],
    ))
    draft["selected_pad_codes"] = sorted(selected)
    return _response(_message(f"📱 選擇多智能體手機發布\n\n已選：{len(selected)} 台", rows), state={"flow": "source_post_publish_pads", "draft": draft})


def _source_post_pad_action(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    selected = {str(item) for item in draft.get("selected_pad_codes", []) if str(item)}
    devices = _active_devices()[:20]
    if action.startswith("sppad:"):
        pad_code = action.split(":", 1)[1]
        selected.remove(pad_code) if pad_code in selected else selected.add(pad_code)
    elif action == "sppad_all":
        selected.update(device.pad_code for device in devices)
    elif action == "sppad_clear":
        selected.clear()
    draft["selected_pad_codes"] = sorted(selected)
    return _source_post_pad_menu({"flow": "source_post_publish_pads", "draft": draft})


def _source_post_multi_publish_execute(state: dict[str, Any]) -> dict[str, Any]:
    draft, archive_id, post_id, source, content_type, page = _source_post_action_context(state)
    pad_codes = [str(item) for item in draft.get("selected_pad_codes", []) if str(item)]
    if not pad_codes:
        return _source_post_pad_menu({"flow": "source_post_publish_pads", "draft": {**draft, "pad_notice": "請至少選擇一台智能體手機。"}})
    platform = str(draft.get("platform") or "threads")
    response = _submit_source_post_task(
        "persona_publish_post",
        archive_id,
        post_id,
        {"archiveId": archive_id, "postId": post_id, "padCode": pad_codes[0], "padCodes": pad_codes, "platform": platform, "postSource": source, "uiContentType": content_type, "uiPage": page, "dryRun": False},
        "多智能體手機真實發布任務",
    )
    response["state"] = {"flow": "source_post_task", "draft": draft}
    return response


def _source_post_image_ratio_picker(archive_id: str, post_id: str, post_index: int, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = dict(context or {})
    source = "favorites" if str(context.get("source") or "posts") == "favorites" else "posts"
    content_type = str(context.get("group_content_type") or "")
    page = max(0, _num(context.get("post_page")))
    local, row, post = _source_archive_post(archive_id, post_id, source, content_type)
    if not post:
        return _response(_message("沒有找到這篇推文。", [[_btn("◀️ 返回推文列表", f"posts_{archive_id}_p0")]]), state={"flow": ""})
    name = local.name if local else _persona_row_name(row or {})
    keyboard = [
        [_btn(item["label"], f"post_img_ratio_{item['id']}") for item in GENPOST_RATIO_OPTIONS[index : index + 2]]
        for index in range(0, len(GENPOST_RATIO_OPTIONS), 2)
    ]
    keyboard.append([_btn("◀️ 返回查看推文", _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page))])
    return _response(
        _message(
            "\n".join(["🖼 單篇推文配圖", "", f"人設：{name}", f"推文：第 {post_index + 1} 篇", "", "請選擇配圖畫面比例："]),
            keyboard,
        ),
        state={"flow": "source_post_image_ratio", "draft": {**context, "archive_id": archive_id, "persona_id": archive_id, "post_id": post_id, "post_index": post_index, "source": source, "group_content_type": content_type, "post_page": page}},
    )


def _source_post_image_regen_entry(action: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    context = dict((state or {}).get("draft") if isinstance((state or {}).get("draft"), dict) else {})
    payload = action[len("post_img_regen_") :]
    archive_id, separator, raw_index = payload.rpartition("_")
    if not separator or not archive_id:
        return _response(_message("配圖入口已失效。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    source = "favorites" if str(context.get("source") or "posts") == "favorites" else "posts"
    content_type = str(context.get("group_content_type") or "")
    posts = _source_post_collection(_resolve_persona_for_action(archive_id)[1], source, content_type)
    index = max(0, _num(raw_index))
    if index >= len(posts):
        return _response(_message("沒有找到這篇推文。", [[_btn("◀️ 返回推文列表", f"posts_{archive_id}_p0")]]), state={"flow": ""})
    post_id = str(posts[index].get("id") or "")
    return _source_post_image_ratio_picker(archive_id, post_id, index, context)


def _source_post_image_retry(action: str) -> dict[str, Any]:
    parts = str(action or "").split(":")
    if len(parts) < 7:
        return _response(_message("配圖入口已失效。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    archive_id = urllib.parse.unquote(parts[1])
    post_id = urllib.parse.unquote(parts[2])
    source = "favorites" if parts[3] == "favorites" else "posts"
    content_type = parts[4] if parts[4] in {"free", "paid"} else ""
    page = max(0, _num(parts[5]))
    post_index = max(0, _num(parts[6]))
    context = {"archive_id": archive_id, "persona_id": archive_id, "post_id": post_id, "source": source, "group_content_type": content_type, "post_page": page, "post_index": post_index}
    return _source_post_image_ratio_picker(archive_id, post_id, post_index, context)


def _source_post_image_ratio_submit(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    archive_id = str(draft.get("archive_id") or "")
    post_id = str(draft.get("post_id") or "")
    ratio_id = action[len("post_img_ratio_") :]
    option = next((item for item in GENPOST_RATIO_OPTIONS if item["id"] == ratio_id), None)
    if not archive_id or not post_id or not option:
        return _response(_message("配圖比例選擇已過期，請從推文列表重新打開。", [[_btn("📝 查看推文列表", f"posts_{archive_id}_p0")]]), state={"flow": ""})
    source = "favorites" if str(draft.get("source") or "posts") == "favorites" else "posts"
    content_type = str(draft.get("group_content_type") or "")
    page = max(0, _num(draft.get("post_page")))
    return _submit_source_post_task(
        "persona_generate_post_image",
        archive_id,
        post_id,
        {"archiveId": archive_id, "postId": post_id, "action": "generate_candidates", "chatId": SOURCE_WEB_BOT_CHAT_ID, "imageAspectRatio": option["ratio"], "imageWidth": option["width"], "imageHeight": option["height"], "imageRatioLabel": option["label"], "postSource": source, "uiContentType": content_type, "uiPage": page, "uiPostIndex": _num(draft.get("post_index")), "uiSelectedIndexes": [int(item) for item in draft.get("selected_media_indexes", []) if str(item).isdigit()]},
        "推文配圖任務",
    )


def _submit_source_post_task(task_type: str, archive_id: str, post_id: str, params: dict[str, Any], label: str) -> dict[str, Any]:
    task_source = "favorites" if str(params.get("postSource") or params.get("source") or "posts") == "favorites" else "posts"
    task_content_type = str(params.get("uiContentType") or "")
    task_page = max(0, _num(params.get("uiPage")))
    back_action = _source_post_detail_callback(archive_id, post_id, source=task_source, content_type=task_content_type, page=task_page) if post_id else f"pd_{archive_id}"
    back_label = "◀️ 返回推文" if post_id else "◀️ 返回人設詳情"
    job = SourceWorkflowJobRepo.create(task_type, label, params, status="submitting")
    try:
        base, data = _source_submit_task(task_type, params)
        SourceWorkflowJobRepo.update(job.id, status="submitted", result=data, source_task_id=str(data.get("id") or ""), source_base_url=base)
    except Exception as exc:
        SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
        return _response(
            _message(f"❌ {label}提交失敗\n\n{exc}", [[_btn(back_label, back_action)]]),
            state={"flow": "source_post_task", "draft": {"archive_id": archive_id, "persona_id": archive_id, "post_id": post_id, "source": task_source, "group_content_type": task_content_type, "post_page": task_page}},
        )
    source_task_id = str(data.get("id") or "")
    if task_type == "persona_generate_image":
        pending_text = f"🎨 正在為人設生成图片...\n\n人設 ID：{archive_id}"
        pending_rows = _rows([_btn("◀️ 返回", f"settings_{archive_id}")])
    elif task_type == "persona_generate_post_image":
        pending_text = "⏳ 正在生成推文配圖，完成後會直接寫回同一篇推文。"
        pending_rows = _rows([_btn(back_label, back_action)])
    elif task_type == "persona_publish_post":
        pending_text = "🚀 推文發布中，請稍候..."
        pending_rows = _rows([_btn(back_label, back_action)])
    elif task_type == "persona_post_action":
        pending_text = f"⏳ {label}執行中，完成後會直接寫回同一篇 Tool R18 推文。"
        pending_rows = _rows([_btn("📊 查看本次任務", f"source_task_detail:{source_task_id}") if source_task_id else _btn("📊 查看任務列表", "source_tasks")], [_btn(back_label, back_action)])
    else:
        pending_text = f"⏳ {label}已提交\n\n來源任務 ID：{source_task_id or '-'}\n完成後會直接寫回同一個 Tool R18 人設歸檔。"
        pending_rows = _rows(
            [_btn("📊 查看本次任務", f"source_task_detail:{source_task_id}") if source_task_id else _btn("📊 查看任務列表", "source_tasks")],
            [_btn(back_label, back_action)],
        )
    response = _response(
        _message(
            pending_text,
            pending_rows,
        ),
        state={"flow": "source_post_task", "draft": {"archive_id": archive_id, "persona_id": archive_id, "post_id": post_id, "source": task_source, "group_content_type": task_content_type, "post_page": task_page}},
    )
    if source_task_id and task_type in {"persona_generate_image", "persona_generate_post_image", "persona_publish_post", "persona_post_action"}:
        response["poll"] = {"action": f"source_task_poll:{source_task_id}", "interval_ms": 2000}
    return response


def _source_post_generate_image(action: str) -> dict[str, Any]:
    try:
        archive_id, post_id = action.split(":", 2)[1:]
    except ValueError:
        return _response(_message("推文配圖入口無效。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    _local, _row, post = _source_archive_post(archive_id, post_id)
    if not post:
        return _response(_message("找不到這篇待發布推文。", [[_btn("📝 返回推文列表", f"posts_{archive_id}_p0")]]), state={"flow": ""})
    return _submit_source_post_task(
        "persona_generate_post_image",
        archive_id,
        post_id,
        {"archiveId": archive_id, "postId": post_id, "chatId": SOURCE_WEB_BOT_CHAT_ID},
        "推文配圖任務",
    )


def _source_post_pick_candidate(action: str) -> dict[str, Any]:
    try:
        _prefix, task_id, raw_index = action.split(":", 2)
        index = int(raw_index)
    except (TypeError, ValueError):
        return _response(_message("候選圖片選擇已失效。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    try:
        _base, data = _source_task_detail_data(task_id)
    except Exception as exc:
        return _response(_message(f"讀取候選圖片失敗：{exc}", [[_btn("🔄 重新查看任務", f"source_task_detail:{task_id}")]]), state={"flow": ""})
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
    images = [url for value in (result.get("imageUrls") if isinstance(result.get("imageUrls"), list) else []) if (url := _safe_web_media_url(value))]
    if not (0 <= index < len(images)):
        return _response(_message("找不到這張候選圖片。", [[_btn("🔄 返回候選圖片", f"source_task_detail:{task_id}")]]), state={"flow": ""})
    archive_id = str(result.get("archiveId") or task_input.get("archiveId") or "")
    post_id = str(result.get("postId") or task_input.get("postId") or "")
    source = "favorites" if str(task_input.get("postSource") or "posts") == "favorites" else "posts"
    content_type = str(task_input.get("uiContentType") or "")
    page = max(0, _num(task_input.get("uiPage")))
    return _submit_source_post_task(
        "persona_generate_post_image",
        archive_id,
        post_id,
        {
            "archiveId": archive_id,
            "postId": post_id,
            "action": "select_candidate",
            "imageUrl": images[index],
            "postSource": source,
            "selectedIndexes": [int(item) for item in task_input.get("uiSelectedIndexes", []) if isinstance(item, int) and item >= 0],
            "uiContentType": content_type,
            "uiPage": page,
            "uiPostIndex": max(0, _num(task_input.get("uiPostIndex"))),
            "uiGeneratedPostIds": [
                str(value or "").strip()
                for value in (task_input.get("uiGeneratedPostIds") if isinstance(task_input.get("uiGeneratedPostIds"), list) else [])
                if str(value or "").strip()
            ],
            "uiImageAspectRatio": str(task_input.get("imageAspectRatio") or task_input.get("uiImageAspectRatio") or ""),
            "uiImageWidth": _num(task_input.get("imageWidth") or task_input.get("uiImageWidth")),
            "uiImageHeight": _num(task_input.get("imageHeight") or task_input.get("uiImageHeight")),
            "uiImageRatioLabel": str(task_input.get("imageRatioLabel") or task_input.get("uiImageRatioLabel") or ""),
        },
        "寫入推文候選配圖",
    )


def _source_post_publish_start(action: str, action_key: str = "", context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = dict(context or {})
    try:
        archive_id, post_id = action.split(":", 2)[1:]
    except ValueError:
        return _response(_message("發布入口無效。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    source = "favorites" if str(context.get("source") or "posts") == "favorites" else "posts"
    content_type = str(context.get("group_content_type") or "")
    page = max(0, _num(context.get("post_page")))
    _local, _row, post = _source_archive_post(archive_id, post_id, source, content_type)
    if not post:
        return _response(_message("找不到這篇待發布推文。", [[_btn("📝 返回推文列表", f"posts_{archive_id}_p0")]]), state={"flow": ""})
    key = action_key or _web_post_action_key(archive_id, post_id)
    return _response(
        _message(
            "🚀 發布這篇\n\n請選擇發布平台：",
            _rows([_btn("🧵 Threads", f"pa_pp_{key}_threads"), _btn("📣 Telegram 群组", f"pa_pp_{key}_telegram")], [_btn("◀️ 返回查看推文", _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page))]),
        ),
        state={"flow": "source_post_publish_platform", "draft": {**context, "archive_id": archive_id, "persona_id": archive_id, "post_id": post_id, "post_action_key": key, "source": source, "group_content_type": content_type, "post_page": page}},
    )


def _source_post_publish_platform(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    platform = action.split(":", 1)[1] if ":" in action else "threads"
    archive_id = str(draft.get("archive_id") or "")
    post_id = str(draft.get("post_id") or "")
    source = "favorites" if str(draft.get("source") or "posts") == "favorites" else "posts"
    content_type = str(draft.get("group_content_type") or "")
    page = max(0, _num(draft.get("post_page")))
    local, row, post = _source_archive_post(archive_id, post_id, source, content_type)
    if not post:
        return _response(_message("發布狀態已失效，請重新選擇推文。", [[_btn("📝 返回推文列表", f"posts_{archive_id}_p0")]]), state={"flow": ""})
    pad_code = str((local.pad_code if local else "") or (row or {}).get("bound_pad_code") or "").strip()
    if not pad_code:
        return _response(
            _message("這個人設尚未綁定智能體手機，請先綁定後再發布。", _rows([_btn("📱 綁定智能體手機", f"bindpad_{archive_id}")], [_btn("◀️ 返回查看推文", f"source_post:{archive_id}:{post_id}")])),
            state={"flow": ""},
        )
    platform_label = "Threads" if platform == "threads" else "Telegram 群組"
    action_key = str(draft.get("post_action_key") or _web_post_action_key(archive_id, post_id))
    return _response(
        _message(
            f"🚀 確認發布推文\n\n平台：{platform_label}\nPAD_CODE：{pad_code}\n\n{_remote_post_preview(post, 220)}",
            _rows([_btn(f"✅ 确认发布到绑定智能體手機 {platform_label}", f"pa_dop_{action_key}_{platform}")], [_btn("📱 選擇多智能體手機發布", f"pa_dopm_{action_key}_{platform}")], [_btn("◀️ 返回选择平台", f"pa_pp_{action_key}_clear")], [_btn("◀️ 返回查看推文", _source_post_detail_callback(archive_id, post_id, source=source, content_type=content_type, page=page))]),
        ),
        state={"flow": "source_post_publish_confirm", "draft": {**draft, "platform": platform, "pad_code": pad_code}},
    )


def _source_post_publish_execute(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    archive_id = str(draft.get("archive_id") or "")
    post_id = str(draft.get("post_id") or "")
    platform = str(draft.get("platform") or "threads")
    pad_code = str(draft.get("pad_code") or "")
    source = "favorites" if str(draft.get("source") or "posts") == "favorites" else "posts"
    content_type = str(draft.get("group_content_type") or "")
    page = max(0, _num(draft.get("post_page")))
    if not archive_id or not post_id or not pad_code:
        return _response(_message("發布狀態已失效，請重新選擇推文。", [[_btn("👤 返回人設", f"pd_{archive_id}")]]), state={"flow": ""})
    return _submit_source_post_task(
        "persona_publish_post",
        archive_id,
        post_id,
        {"archiveId": archive_id, "postId": post_id, "padCode": pad_code, "platform": platform, "postSource": source, "uiContentType": content_type, "uiPage": page, "dryRun": False},
        "真實發布任務",
    )


def _publish_history(action: str, content_type: str = "") -> dict[str, Any]:
    page, persona_id = _parse_publish_page_action(action)
    persona_id, persona, row, name = _publish_context(persona_id)
    if not persona and not row:
        return _response(_message("没有找到这个人设。", [[_btn("◀️ 返回人设列表", "list_personas")]]), state={"flow": ""})

    tasks = [task for task in _publish_tasks_for_persona(persona, row) if task.status not in {"pending", "publishing"}]
    source_history = row.get("publish_history") if isinstance((row or {}).get("publish_history"), list) else []
    if content_type in {"free", "paid"}:
        source_history = [
            item
            for item in source_history
            if isinstance(item, dict)
            and str(item.get("telegramGroupContentType") or item.get("telegram_group_content_type") or "free").strip().lower() == content_type
        ]
    items: list[tuple[str, Any]] = [("task", task) for task in tasks] + [("source", item) for item in source_history if isinstance(item, dict)]
    total_pages = max(1, (len(items) + STORED_POSTS_PAGE_SIZE - 1) // STORED_POSTS_PAGE_SIZE)
    safe_page = min(page, total_pages - 1)
    visible = items[safe_page * STORED_POSTS_PAGE_SIZE : (safe_page + 1) * STORED_POSTS_PAGE_SIZE]

    lines = ["🕘 发布历史", "", f"人設：{name}", f"第 {safe_page + 1}/{total_pages} 頁，共 {len(items)} 筆"]
    if not visible:
        lines.extend(["", "目前尚無發布歷史。送出推文任務並由排程器完成後，會出現在這裡。"])
    for offset, (kind, item) in enumerate(visible, start=safe_page * STORED_POSTS_PAGE_SIZE + 1):
        if kind == "task":
            task = item
            lines.extend(
                [
                    "",
                    f"{offset}. 本機任務 #{task.id}｜{_task_status_label(task.status)}",
                    f"建立：{_task_time(task.created_at)}｜更新：{_task_time(task.status_at)}｜媒體：{_task_media_label(task)}",
                    f"內容：{_task_preview(task.text)}",
                ]
            )
            if task.result:
                lines.append(f"結果：{_task_preview(task.result, 120)}")
        else:
            item = item
            platform = str(item.get("platform") or item.get("target") or "來源").strip()
            status = str(item.get("status") or item.get("result") or "-").strip()
            lines.extend(
                [
                    "",
                    f"{offset}. 來源歷史｜{platform}｜{status}",
                    f"時間：{_remote_history_time(item)}",
                    f"內容：{_remote_history_preview(item)}",
                ]
            )
            url = item.get("url") or item.get("source_url") or item.get("post_url")
            if url:
                lines.append(f"連結：{url}")

    keyboard = _rows(
        [
            _btn("◀️ 上一頁", f"pub_history:{max(0, safe_page - 1)}:{persona_id}"),
            _btn(f"{safe_page + 1}/{total_pages}", f"pub_history:{safe_page}:{persona_id}"),
            _btn("下一頁 ▶️", f"pub_history:{min(total_pages - 1, safe_page + 1)}:{persona_id}"),
        ]
        if total_pages > 1
        else [],
        [_btn("📋 查看推文列表", f"pub_posts:0:{persona_id}"), _btn("📋 打开发帖任务", "open:/tasks")],
        [_btn("◀️ 返回", f"history_branch_{persona_id}" if content_type else f"pd:{persona_id}")],
    )
    return _response(_message("\n".join(lines), keyboard), state={"flow": "publish_center", "draft": {"persona_id": persona_id, "name": name}})


def _post_library(persona_id: str = "") -> dict[str, Any]:
    if not persona_id:
        personas = PersonaRepo.list_all(limit=12)
        lines = ["📚 推文素材庫", "", "請先選擇人設，進入該人設的待發布、收藏、歷史與回庫操作。"]
        if not personas:
            lines.append("目前沒有本地人設。")
        keyboard = _chunk_buttons([_btn(_local_persona_display_name(persona)[:22], f"post_library:{persona.id}") for persona in personas], 2)
        keyboard.extend(_rows([_btn("👤 人設管理", "list_personas"), _btn("返回主選單", "menu")]))
        return _response(_message("\n".join(lines), keyboard), state={"flow": ""})

    persona_id, persona, row, name = _publish_context(persona_id)
    if not persona and not row:
        return _response(_message("找不到這個人設，不能查看素材庫。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    tasks = _publish_tasks_for_persona(persona, row)
    pending = [task for task in tasks if task.status in {"pending", "publishing"}]
    history = [task for task in tasks if task.status not in {"pending", "publishing"}]
    favorites = PostMemoryRepo.list_for_persona(persona_id, limit=30, favorite_only=True) if persona else []
    source_posts = row.get("post_metrics") if isinstance((row or {}).get("post_metrics"), list) else []
    source_history = row.get("publish_history") if isinstance((row or {}).get("publish_history"), list) else []
    lines = [
        "📚 推文素材庫",
        "",
        f"人設：{name}",
        f"待發布/發布中：{len(pending)} 篇",
        f"收藏推文/記憶：{len(favorites)} 筆",
        f"本機發布歷史：{len(history)} 筆",
        f"來源推文資料：{len(source_posts)} 篇",
        f"來源發布歷史：{len(source_history)} 筆",
        "",
        "可從這裡查看待發布、收藏、歷史，或把已發布內容重新回庫成待發布草稿。",
    ]
    return _response(
        _message(
            "\n".join(lines),
            _rows(
                [_btn("📋 待發布推文", f"pub_posts:0:{persona_id}"), _btn("⭐ 收藏推文", f"genpost_favorites:{persona_id}:all:0")],
                [_btn("🕘 發布歷史", f"pub_history:0:{persona_id}"), _btn("♻️ 重新回庫", f"restore_history:0:{persona_id}")],
                [_btn("✍️ 新建推文", f"genpost:{persona_id}")],
                [_btn("🚀 發布推文", f"pub:{persona_id}"), _btn("◀️ 返回人設", f"pd:{persona_id}")],
            ),
        ),
        state={"flow": "publish_center", "draft": {"persona_id": persona_id, "name": name}},
    )


def _restore_history_menu(action: str) -> dict[str, Any]:
    parts = action.split(":")
    page = _num(parts[1]) if len(parts) > 1 else 0
    persona_id = parts[2] if len(parts) > 2 else ""
    persona_id, persona, row, name = _publish_context(persona_id)
    if not persona and not row:
        return _response(_message("找不到這個人設，不能重新回庫。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    tasks = [task for task in _publish_tasks_for_persona(persona, row) if task.status not in {"pending", "publishing"}]
    page_size = 5
    total_pages = max(1, (len(tasks) + page_size - 1) // page_size)
    safe_page = max(0, min(page, total_pages - 1))
    visible = tasks[safe_page * page_size : (safe_page + 1) * page_size]
    lines = ["♻️ 重新回庫", "", f"人設：{name}", f"第 {safe_page + 1}/{total_pages} 頁，共 {len(tasks)} 筆", ""]
    if not visible:
        lines.append("目前沒有可回庫的本機發布歷史。")
    for index, task in enumerate(visible, start=safe_page * page_size + 1):
        lines.extend([f"{index}. #{task.id}｜{_task_status_label(task.status)}｜{_task_media_label(task)}", _task_preview(task.text, 140), ""])
    keyboard = [[_btn(f"回庫 #{task.id}", f"restore_task:{task.id}:{persona_id}", "primary")] for task in visible]
    if total_pages > 1:
        keyboard.append(
            [
                _btn("◀️ 上一頁", f"restore_history:{max(0, safe_page - 1)}:{persona_id}"),
                _btn(f"{safe_page + 1}/{total_pages}", f"restore_history:{safe_page}:{persona_id}"),
                _btn("下一頁 ▶️", f"restore_history:{min(total_pages - 1, safe_page + 1)}:{persona_id}"),
            ]
        )
    keyboard.extend(_rows([_btn("📚 返回素材庫", f"post_library:{persona_id}"), _btn("◀️ 返回發布中心", f"pub:{persona_id}")]))
    return _response(_message("\n".join(lines), keyboard), state={"flow": ""})


def _restore_task(action: str) -> dict[str, Any]:
    parts = action.split(":")
    task_id = parts[1] if len(parts) > 1 else ""
    persona_id = parts[2] if len(parts) > 2 else ""
    task = TaskRepo.get(task_id)
    if not task:
        return _response(_message("找不到這筆發布歷史，無法回庫。", [[_btn("◀️ 返回素材庫", f"post_library:{persona_id}")]]), state={"flow": ""})
    TaskRepo.add_many(
        traditionalize_task_entries(
            [
                {
                    "username": task.username,
                    "text": task.text,
                    "media_paths": task.media_paths,
                    "batch_dir": getattr(task, "batch_dir", ""),
                    "scheduled_at": 0,
                }
            ]
        )
    )
    if persona_id:
        _record_post_memory(
            persona_id,
            task.text,
            granularity="daily",
            source_type="restore_to_draft",
            source_ref=task.id,
            title=f"重新回庫 #{task.id}",
            payload={"media_paths": task.media_paths},
        )
    return _response(
        _message(
            f"✅ 已將 #{task.id} 重新回庫為待發布推文。",
            _rows([_btn("📋 待發布推文", f"pub_posts:0:{persona_id}")], [_btn("📚 返回素材庫", f"post_library:{persona_id}")]),
        ),
        state={"flow": ""},
    )


def _r18_menu(persona_id: str) -> dict[str, Any]:
    persona, row = _resolve_persona_for_action(persona_id)
    if not persona and not row:
        return _response(_message("找不到這個既有人設。R18 通道只允許使用既有工作流人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    if persona:
        persona_id = persona.id
    name = _local_persona_display_name(persona) if persona else _persona_row_name(row or {})
    lines = [
        "🔞 R18 專屬人設通道",
        "",
        f"人設：{name}",
        "規則：R18 不從這裡新建人設，只使用既有工作流人設。",
        "內容需分為免費內容與付費內容，發布時會帶入對應分流記憶。",
    ]
    return _response(
        _message(
            "\n".join(lines),
            _rows(
                [_btn("免費內容：生成推文", f"r18_gen:free:{persona_id}"), _btn("付費內容：生成推文", f"r18_gen:paid:{persona_id}")],
                [_btn("免費/付費發布中心", f"pub:{persona_id}"), _btn("矩陣 R18 發布", "matrix_start")],
                [_btn("◀️ 返回人設詳情", f"pd:{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _r18_generate(action: str) -> dict[str, Any]:
    parts = action.split(":")
    branch = parts[1] if len(parts) > 1 else "free"
    persona_id = parts[2] if len(parts) > 2 else ""
    persona, row = _resolve_persona_for_action(persona_id)
    if not persona:
        return _response(_message("R18 推文只能使用本地既有人設。", [[_btn("◀️ 返回", f"r18:{persona_id}")]]), state={"flow": ""})
    branch_label = "付費內容" if branch == "paid" else "免費內容"
    name = _local_persona_display_name(persona)
    draft = {
        "persona_id": persona.id,
        "name": name,
        "memory": f"R18 {branch_label}分流；請符合平台邊界與既有人設設定。",
        "memory_granularity": "persona",
        "content_branch": branch_label,
        "text_model_branch": branch,
    }
    return _response(
        _genpost_count_prompt(persona.id, name, draft["memory"]),
        state={"flow": "genpost_count", "draft": draft},
    )


def _tg_credentials_file() -> Path:
    return DATA_DIR / "tg_credentials.local.json"


def _read_tg_credentials() -> dict[str, Any]:
    path = _tg_credentials_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_tg_credentials(data: dict[str, Any]) -> None:
    path = _tg_credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _tg_credentials_prompt(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", f"pd:{persona_id}")]]), state={"flow": ""})
    return _response(
        _message(
            "🔐 設定 Telegram 登入憑證\n\n請貼上登入資料備註，例如：手機號、2FA 提醒、登入用信箱。資料只保存在本機 JSON，清除鍵可刪除。",
            [[_btn("❌ 取消", f"acctplatform_telegram_{persona_id}")]],
        ),
        state={"flow": "tg_credentials", "draft": {"persona_id": persona_id}},
    )


def _save_tg_credentials(text: str, persona_id: str) -> dict[str, Any]:
    data = _read_tg_credentials()
    data[str(persona_id)] = {"note": text[:1200], "updated_at": time.time()}
    _write_tg_credentials(data)
    return _response(_message("✅ 已保存 Telegram 登入資料備註。", [[_btn("◀️ 返回 Telegram 設定", f"acctplatform_telegram_{persona_id}")]]), state={"flow": ""})


def _clear_tg_credentials(persona_id: str) -> dict[str, Any]:
    data = _read_tg_credentials()
    data.pop(str(persona_id), None)
    _write_tg_credentials(data)
    return _response(_message("✅ 已清除這個人設的 Telegram 本地登入資料。", [[_btn("◀️ 返回 Telegram 設定", f"acctplatform_telegram_{persona_id}")]]), state={"flow": ""})


def _telegram_login_check(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    pad_code = persona.pad_code if persona else ""
    if not pad_code:
        return _response(_message("請先綁定智能體手機，再檢測 TG 登入狀態。", [[_btn("📱 綁定智能體手機", f"bindpad_{persona_id}")]]), state={"flow": ""})
    return _source_submit_agent_action(
        "檢測 Telegram 登入狀態",
        f"請檢測 PAD_CODE {pad_code} 的 Telegram 是否已登入，並回報目前登入帳號與可用狀態。",
    )


def _automation_menu(persona_id: str = "") -> dict[str, Any]:
    if not persona_id:
        personas = PersonaRepo.list_all(limit=12)
        lines = ["🤖 自動化運營", "", "請先選擇要執行自動回覆或養號的人設。"]
        keyboard = _chunk_buttons([_btn(_local_persona_display_name(persona)[:22], f"automation:{persona.id}") for persona in personas], 2)
        keyboard.extend(_rows([_btn("👤 人設管理", "list_personas"), _btn("返回主選單", "menu")]))
        return _response(_message("\n".join(lines), keyboard), state={"flow": ""})
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設，不能建立自動化任務。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    device = DeviceRepo.get(persona.pad_code) if persona.pad_code else None
    lines = [
        "🤖 自動化運營",
        "",
        f"人設：{_local_persona_display_name(persona)}",
        f"雲機名稱：{(device.alias if device else '') or persona.pad_code or '未綁定'}",
        f"PAD_CODE：{persona.pad_code or '未綁定'}",
        "",
        "可執行：自動回覆評論、熱點推文回覆、固定文案/AI 回覆、養號滑動/點讚/留言。",
    ]
    return _response(
        _message(
            "\n".join(lines),
            _rows(
                [_btn("AI 回覆評論", f"automation_run:auto_reply_comments:ai:{persona_id}"), _btn("AI 回覆熱點", f"automation_run:auto_reply_hot_posts:ai:{persona_id}")],
                [_btn("固定文案回覆評論", f"automation_fixed:auto_reply_comments:{persona_id}"), _btn("固定文案回覆熱點", f"automation_fixed:auto_reply_hot_posts:{persona_id}")],
                [_btn("養號：滑動", f"automation_run:warm:browse:{persona_id}"), _btn("養號：滑動+點讚", f"automation_run:warm:like:{persona_id}")],
                [_btn("養號：滑動+留言", f"automation_run:warm:comment:{persona_id}"), _btn("養號：全套", f"automation_run:warm:both:{persona_id}")],
                [_btn("📊 腳本任務狀態", "local_jobs"), _btn("◀️ 返回人設詳情", f"pd:{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _persona_autoreply_menu(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    return _response(
        _message(
            "\n".join(["💬 自動回覆", "", f"人設：{persona.name}", "", "請先選擇平台，再進入對應功能設定。"]),
            _rows([_btn("Threads", f"acctautoreply_{persona_id}")], [_btn("◀️ 返回人設詳情", f"pd_{persona_id}")]),
        ),
        state={"flow": ""},
    )


def _persona_autoreply_mode_menu(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    return _response(
        _message(
            "\n".join(
                [
                    "💬 自動回覆",
                    "",
                    f"人設：{persona.name}",
                    "",
                    "請先選擇自動回覆方式。",
                    "自動回覆評論：沿用原本路線，掃描自己推文下方留言並自然回覆。",
                    "自動回覆熱點推文：只在自己已發布、符合瀏覽量和天數條件、且未回覆過的 Threads 推文內，使用你自訂的內容回覆。",
                ]
            ),
            _rows(
                [_btn("💬 自動回覆評論", f"persona_autoreply_original_{persona_id}")],
                [_btn("🔥 自動回覆熱點推文", f"persona_autoreply_hot_{persona_id}")],
                [_btn("◀️ 返回人設詳情", f"pd_{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _own_reply_mode_menu(persona_id: str) -> dict[str, Any]:
    persona, row = _resolve_persona_for_action(persona_id)
    if not persona and not row:
        return _response(_message("沒有找到這個人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    if persona:
        persona_id = persona.id
    name = persona.name if persona else _persona_row_name(row or {})
    history = row.get("publish_history") if isinstance((row or {}).get("publish_history"), list) else []
    return _response(
        _message(
            "\n".join(
                [
                    "🔥 自動回覆熱點推文",
                    "",
                    f"人設：{name}",
                    f"可回覆推文：{len(history)} 篇",
                    "",
                    "請選擇回覆分支：",
                    "1. 自定義內容回覆：找到符合條件的自己主推文後，直接發送你輸入的內容。",
                    "2. AI 自動回覆：根據目前人設和推文內容自動生成自然回覆。",
                ]
            ),
            _rows(
                [_btn("✍️ 使用自定義內容回覆", f"ownreply_mode_manual_{persona_id}")],
                [_btn("🤖 AI 根據人設自動回覆", f"ownreply_mode_ai_{persona_id}")],
                [_btn("◀️ 返回自動回覆", f"persona_autoreply_{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _own_reply_mode_start(action: str) -> dict[str, Any]:
    manual = action.startswith("ownreply_mode_manual_")
    prefix = "ownreply_mode_manual_" if manual else "ownreply_mode_ai_"
    persona_id = action[len(prefix) :]
    persona, _row = _resolve_persona_for_action(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    draft = {"persona_id": persona.id, "reply_mode": "manual" if manual else "ai"}
    if manual:
        return _response(
            _message(
                f"🔥 自動回覆熱點推文\n\n人設：{persona.name}\n回覆模式：使用自定義內容\n\n請直接輸入要回覆到自己已發布主推文內的內容。",
                [[_btn("◀️ 返回分支選擇", f"persona_autoreply_hot_{persona.id}")]],
            ),
            state={"flow": "ownreply_reply_text", "draft": draft},
        )
    return _own_reply_views_prompt(draft)


def _own_reply_views_prompt(draft: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(draft.get("persona_id") or "")
    persona = PersonaRepo.get(persona_id)
    mode = "使用自定義內容" if draft.get("reply_mode") == "manual" else "AI 根據人設和推文自動回覆"
    lines = ["🔥 自動回覆熱點推文", "", f"人設：{persona.name if persona else persona_id}", f"回覆模式：{mode}"]
    if draft.get("reply_text"):
        lines.append(f"回覆內容：{draft.get('reply_text')}")
    lines.extend(["", "請輸入瀏覽量門檻。", "只有已發布推文瀏覽量大於等於這個值時才會自動評論。", "例如：10000、1萬、2.5萬。輸入 0 表示不限制瀏覽量。"])
    rows = []
    if draft.get("reply_mode") == "manual":
        rows.append([_btn("✏️ 重新編輯文案", f"ownreply_text_{persona_id}")])
    rows.append([_btn("◀️ 返回自動回覆", f"persona_autoreply_{persona_id}")])
    return _response(_message("\n".join(lines), rows), state={"flow": "ownreply_views", "draft": draft})


def _parse_own_reply_views(text: str) -> int | None:
    raw = str(text or "").strip().lower().replace(",", "")
    multiplier = 10000 if raw.endswith(("萬", "万")) else 1
    if multiplier != 1:
        raw = raw[:-1].strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return max(0, int(value * multiplier))


def _own_reply_days_prompt(draft: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(draft.get("persona_id") or "")
    persona = PersonaRepo.get(persona_id)
    mode = "使用自定義內容" if draft.get("reply_mode") == "manual" else "AI 根據人設和推文自動回覆"
    return _response(
        _message(
            "\n".join(
                [
                    "🔥 自動回覆熱點推文",
                    "",
                    f"人設：{persona.name if persona else persona_id}",
                    f"回覆模式：{mode}",
                    f"瀏覽量條件：大於等於 {_compact(draft.get('min_views'))}",
                    "",
                    "請輸入查看天數，規則和自動回覆評論一致。",
                    "可輸入：1-7，例如：2。",
                ]
            ),
            _rows([_btn("👁 重新設定瀏覽量", f"ownreply_views_{persona_id}")], [_btn("◀️ 返回自動回覆", f"persona_autoreply_{persona_id}")]),
        ),
        state={"flow": "ownreply_days", "draft": draft},
    )


def _own_reply_confirmation(draft: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(draft.get("persona_id") or "")
    persona = PersonaRepo.get(persona_id)
    return _response(
        _message(
            "\n".join(
                [
                    "🔥 自動回覆熱點推文確認",
                    "",
                    f"人設：{persona.name if persona else persona_id}",
                    "平台：Threads",
                    f"回覆模式：{'使用自定義內容' if draft.get('reply_mode') == 'manual' else 'AI 根據人設和推文自動回覆'}",
                    f"瀏覽量條件：大於等於 {_compact(draft.get('min_views'))}",
                    f"查看天數：{draft.get('max_age_days')} 天",
                    "",
                    "確認後才會建立真實回覆任務。",
                ]
            ),
            _rows([_btn("✅ 開始自動回覆", "ownreply_run")], [_btn("◀️ 返回設定天數", f"ownreply_days_{persona_id}")]),
        ),
        state={"flow": "ownreply_confirm", "draft": draft},
    )


def _own_reply_submit(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    persona_id = str(draft.get("persona_id") or "")
    persona, row = _resolve_persona_for_action(persona_id)
    archive_id = _tool_r18_archive_id(persona_id, persona, row)
    if not persona or not archive_id or not persona.pad_code:
        return _response(_message("人設、來源歸檔或綁定智能體手機已失效，請重新設定。", [[_btn("◀️ 返回自動回覆", f"persona_autoreply_{persona_id}")]]), state={"flow": ""})
    params = {
        "archiveId": archive_id,
        "padCode": persona.pad_code,
        "replyMode": str(draft.get("reply_mode") or "ai"),
        "replyText": str(draft.get("reply_text") or ""),
        "minViews": max(0, _num(draft.get("min_views"))),
        "maxAgeDays": max(1, min(_num(draft.get("max_age_days")), 7)),
        "dryRun": False,
    }
    job = SourceWorkflowJobRepo.create("threads_own_post_reply", f"熱點推文自動回覆：{persona.name}", params, status="submitting")
    _submit_source_task_job_async(job.id, "threads_own_post_reply", params)
    return _response(
        _message("✅ 已提交真實熱點推文回覆任務。", _rows([_btn("📊 查看來源任務", "source_tasks")], [_btn("◀️ 返回自動回覆", f"persona_autoreply_{persona_id}")])),
        state={"flow": ""},
    )


def _persona_warmup_platform_menu(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    return _response(
        _message(
            "\n".join(["🌱 養號", "", f"人設：{persona.name}", "", "請先選擇平台，再進入對應功能設定。"]),
            _rows([_btn("Threads", f"acctwarmup_threads_{persona_id}")], [_btn("◀️ 返回人設詳情", f"pd_{persona_id}")]),
        ),
        state={"flow": ""},
    )


def _threads_warmup_menu(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    if not persona.pad_code:
        return _response(
            _message(
                f"❌ 這個人設還沒有可用的綁定智能體手機。\n\n人設：{persona.name}\n平台：Threads\n\n請先綁定智能體手機，再執行 Threads 養號。",
                _rows([_btn("📱 綁定智能體手機", f"bindpad_{persona_id}")], [_btn("◀️ 返回養號", f"persona_warmup_{persona_id}")]),
            ),
            state={"flow": ""},
        )
    device = DeviceRepo.get(persona.pad_code)
    pad_name = (device.alias if device else "") or persona.pad_code
    return _response(
        _message(
            "\n".join(
                [
                    "🌱 養號設定",
                    "",
                    f"人設：{persona.name}",
                    f"智能體手機：{pad_name}",
                    "平台：Threads",
                    "",
                    "執行規則：",
                    "- 每次滑動瀏覽 7-10 分鐘，每天最多 2 次",
                    "- 每滑過約 2-3 篇，隨機挑選 1 篇點讚或留言",
                    "- 不會每篇都互動；遇到風險閾值會停止並提示人工介入",
                    "",
                    "請選擇互動策略：",
                ]
            ),
            _rows(
                [_btn("只滑動", f"warmrun_browse_{persona_id}")],
                [_btn("滑動 + 隨機點讚", f"warmrun_like_{persona_id}")],
                [_btn("滑動 + 隨機留言", f"warmrun_comment_{persona_id}")],
                [_btn("滑動 + 點讚 + 留言", f"warmrun_both_{persona_id}")],
                [_btn("◀️ 返回養號", f"persona_warmup_{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _persona_by_pad_code(pad_code: str) -> Persona | None:
    target = str(pad_code or "").strip()
    if not target:
        return None
    for persona in PersonaRepo.list_all():
        if str(persona.pad_code or "").strip() == target:
            return persona
    return None


def _threads_warmup_menu(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    if not persona.pad_code:
        return _response(
            _message(
                f"❌ 這個人設還沒有可用的綁定智能體手機。\n\n人設：{persona.name}\n平台：Threads\n\n請先綁定智能體手機，再執行 Threads 養號。",
                _rows([_btn("📱 綁定智能體手機", f"bindpad_{persona_id}")], [_btn("◀️ 返回養號", f"persona_warmup_{persona_id}")]),
            ),
            state={"flow": ""},
        )
    device = DeviceRepo.get(persona.pad_code)
    pad_name = (device.alias if device else "") or persona.pad_code
    return _response(
        _message(
            "\n".join(
                [
                    "🌱 養號設定",
                    "",
                    f"人設：{persona.name}",
                    f"智能體手機：{pad_name}",
                    "平台：Threads",
                    "",
                    "執行規則：",
                    "- 每次滑動瀏覽 7-10 分鐘，每天最多 2 次",
                    "- 每滑過約 2-3 篇，隨機挑選 1 篇點讚或留言",
                    "- 不會每篇都互動；遇到風險閾值會停止並提示人工介入",
                    "",
                    "請選擇互動策略：",
                ]
            ),
            _rows(
                [_btn("只滑動", f"warmup_engage_threads_{persona.pad_code}_browse")],
                [_btn("滑動 + 隨機點讚", f"warmup_engage_threads_{persona.pad_code}_like")],
                [_btn("滑動 + 隨機留言", f"warmup_engage_threads_{persona.pad_code}_comment")],
                [_btn("滑動 + 隨機點讚/留言", f"warmup_engage_threads_{persona.pad_code}_both")],
                [_btn("◀️ 返回養號", f"persona_warmup_{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _warmup_engage_threads(action: str) -> dict[str, Any]:
    rest = action[len("warmup_engage_threads_") :]
    pad_code, _, mode = rest.rpartition("_")
    mode = mode or "browse"
    persona = _persona_by_pad_code(pad_code)
    if not persona:
        return _response(_message("沒有找到綁定這台智能體手機的人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    mode_labels = {
        "browse": "只滑動",
        "like": "滑動 + 隨機點讚",
        "comment": "滑動 + 隨機留言",
        "both": "滑動 + 隨機點讚/留言",
    }
    label = mode_labels.get(mode, "只滑動")
    return _response(
        _message(
            "\n".join(
                [
                    "🌱 確認養號任務",
                    "",
                    f"人設：{persona.name}",
                    f"平台：Threads",
                    f"智能體手機：{persona.pad_code}",
                    f"互動策略：{label}",
                    "",
                    "確認後會建立後台養號任務。",
                ]
            ),
            _rows(
                [_btn("✅ 開始養號", f"warmup_run_threads_{pad_code}")],
                [_btn("◀️ 返回改策略", f"acctwarmup_threads_{persona.id}")],
            ),
        ),
        state={"flow": "warmup_confirm", "draft": {"persona_id": persona.id, "mode": mode, "pad_code": pad_code}},
    )


def _warmup_run_threads(action: str, state: dict[str, Any]) -> dict[str, Any]:
    pad_code = action[len("warmup_run_threads_") :]
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    persona_id = str(draft.get("persona_id") or "")
    mode = str(draft.get("mode") or "browse")
    persona = PersonaRepo.get(persona_id) if persona_id else _persona_by_pad_code(pad_code)
    if not persona:
        return _response(_message("沒有找到綁定這台智能體手機的人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    return _automation_run(f"automation_run:warm:{mode}:{persona.id}")


def _warmup_run_threads(action: str, state: dict[str, Any]) -> dict[str, Any]:
    pad_code = action[len("warmup_run_threads_") :]
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    if state.get("flow") != "warmup_confirm" or not draft:
        persona = _persona_by_pad_code(pad_code)
        back = f"acctwarmup_threads_{persona.id}" if persona else "list_personas"
        return _response(
            _message("養號確認狀態已失效，請重新選擇互動策略後再開始。", [[_btn("◀️ 返回養號設定", back)]]),
            state={"flow": ""},
        )
    persona_id = str(draft.get("persona_id") or "")
    mode = str(draft.get("mode") or "")
    if mode not in {"browse", "like", "comment", "both"}:
        persona = PersonaRepo.get(persona_id) if persona_id else _persona_by_pad_code(pad_code)
        back = f"acctwarmup_threads_{persona.id}" if persona else "list_personas"
        return _response(_message("養號互動策略已失效，請重新選擇。", [[_btn("◀️ 返回養號設定", back)]]), state={"flow": ""})
    persona = PersonaRepo.get(persona_id) if persona_id else _persona_by_pad_code(pad_code)
    if not persona:
        return _response(_message("沒有找到綁定這台智能體手機的人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    return _automation_run(f"automation_run:warm:{mode}:{persona.id}")


def _automation_fixed_prompt(action: str) -> dict[str, Any]:
    parts = action.split(":")
    kind = parts[1] if len(parts) > 1 else "auto_reply_comments"
    persona_id = parts[2] if len(parts) > 2 else ""
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    return _response(
        _message(
            "💬 固定文案回覆\n\n請輸入要固定回覆的文案。送出後會建立自動回覆任務。",
            [[_btn("❌ 取消", f"automation:{persona_id}")]],
        ),
        state={"flow": "automation_fixed_reply", "draft": {"persona_id": persona_id, "kind": kind}},
    )


def _automation_run(action: str, *, custom_content: str = "") -> dict[str, Any]:
    parts = action.split(":")
    group = parts[1] if len(parts) > 1 else ""
    mode = parts[2] if len(parts) > 2 else ""
    persona_id = parts[3] if len(parts) > 3 else ""
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設，不能建立自動化任務。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    if not persona.pad_code:
        back_action = f"persona_warmup_{persona_id}" if group == "warm" else f"acctautoreply_{persona_id}"
        return _response(
            _message(
                "請先綁定智能體手機，才能執行自動化。",
                _rows([_btn("📱 綁定智能體手機", f"bindpad_{persona_id}")], [_btn("◀️ 返回自動化", back_action)]),
            ),
            state={"flow": ""},
        )
    if group == "warm":
        mode_map = {"browse": "browse", "like": "like", "comment": "comment", "both": "both"}
        warm_mode = mode_map.get(mode, "browse")
        payload = {
            "padCode": persona.pad_code,
            "dryRun": False,
            "mode": warm_mode,
            "browseCount": 80,
            "minSessionMinutes": 7,
            "maxSessionMinutes": 10,
            "interactionEveryMinPosts": 2,
            "interactionEveryMaxPosts": 3,
            "searchChance": 16,
            "riskManaged": False,
            "stopOnRiskLimit": True,
            "allowEngagement": warm_mode in {"like", "comment", "both"},
            "commentPersona": {"name": persona.name, "profile": persona.description[:1200], "replyMode": "ai_persona"},
        }
        label = f"養號自動化｜{persona.name}｜{warm_mode}"
        job = SourceWorkflowJobRepo.create("threads_warmup", label, payload, status="submitting")
        source_task_id = ""
        try:
            base, data = _source_submit_task("threads_warmup", payload)
            source_task_id = str(data.get("id") or "")
            SourceWorkflowJobRepo.update(
                job.id,
                status="submitted",
                result=data,
                source_task_id=source_task_id,
                source_base_url=base,
            )
        except Exception as exc:
            SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
            return _response(
                _message(
                    f"❌ 養號任務提交失敗\n\n{exc}",
                    _rows([_btn("來源 API 狀態", "source_status"), _btn("📊 腳本任務狀態", "local_jobs")], [_btn("◀️ 返回養號", f"persona_warmup_{persona_id}")]),
                ),
                state={"flow": ""},
            )
    else:
        reply_mode = "fixed" if custom_content else "ai_persona"
        payload = {
            "padCode": persona.pad_code,
            "dryRun": False,
            "maxAgeDays": 2 if group == "auto_reply_hot_posts" else 7,
            "maxPosts": 6 if group == "auto_reply_hot_posts" else 3,
            "maxReplies": 3,
            "commentPersona": {
                "name": persona.name,
                "profile": persona.description[:1200],
                "replyMode": reply_mode,
                "customContent": custom_content,
            },
        }
        label = f"自動回覆｜{persona.name}｜{'固定文案' if custom_content else 'AI 人設'}"
        job = SourceWorkflowJobRepo.create("threads_auto_reply", label, payload, status="submitting")
        source_task_id = ""
        try:
            base, data = _source_submit_task("threads_auto_reply", payload)
            source_task_id = str(data.get("id") or "")
            SourceWorkflowJobRepo.update(
                job.id,
                status="submitted",
                result=data,
                source_task_id=source_task_id,
                source_base_url=base,
            )
        except Exception as exc:
            SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
            return _response(
                _message(
                    f"❌ 自動回覆任務提交失敗\n\n{exc}",
                    _rows([_btn("來源 API 狀態", "source_status"), _btn("📊 腳本任務狀態", "local_jobs")], [_btn("◀️ 返回自動回覆", f"acctautoreply_{persona_id}")]),
                ),
                state={"flow": ""},
            )
    return _response(
        _message(
            "\n".join(
                [
                    "✅ 已加入背景自動化任務",
                    "",
                    f"任務：{job.label}",
                    f"Job ID：{job.id}",
                    f"來源任務：{source_task_id or '-'}",
                    f"人設：{persona.name}",
                    f"PAD_CODE：{persona.pad_code}",
                    "",
                    "已提交到來源執行器，可在本次任務詳情查看執行中、完成或失敗。",
                ]
            ),
            _rows(
                [_btn("查看本次执行结果", f"source_task_detail:{source_task_id}") if source_task_id else _btn("来源任务状态", "source_tasks")],
                [_btn("📊 腳本任務狀態", "local_jobs"), _btn("◀️ 返回自動化", f"persona_warmup_{persona_id}" if group == "warm" else f"acctautoreply_{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _automation_run(action: str, *, custom_content: str = "") -> dict[str, Any]:
    parts = action.split(":")
    group = parts[1] if len(parts) > 1 else ""
    mode = parts[2] if len(parts) > 2 else ""
    persona_id = parts[3] if len(parts) > 3 else ""
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設，不能建立自動化任務。", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})
    if not persona.pad_code:
        back_action = f"persona_warmup_{persona_id}" if group == "warm" else f"acctautoreply_{persona_id}"
        return _response(
            _message(
                "請先綁定智能體手機，才能執行自動化。",
                _rows(
                    [_btn("📱 綁定智能體手機", f"bindpad_{persona_id}")],
                    [_btn("◀️ 返回自動化", back_action)],
                ),
            ),
            state={"flow": ""},
        )

    if group == "warm":
        mode_map = {"browse": "browse", "like": "like", "comment": "comment", "both": "both"}
        warm_mode = mode_map.get(mode, "browse")
        payload = {
            "padCode": persona.pad_code,
            "dryRun": False,
            "mode": warm_mode,
            "browseCount": 80,
            "minSessionMinutes": 7,
            "maxSessionMinutes": 10,
            "interactionEveryMinPosts": 2,
            "interactionEveryMaxPosts": 3,
            "searchChance": 16,
            "riskManaged": False,
            "stopOnRiskLimit": True,
            "allowEngagement": warm_mode in {"like", "comment", "both"},
            "commentPersona": {"name": persona.name, "profile": persona.description[:1200], "replyMode": "ai_persona"},
        }
        label = f"養號自動化：{persona.name} / {warm_mode}"
        task_type = "threads_warmup"
        back_action = f"persona_warmup_{persona_id}"
    else:
        reply_mode = "fixed" if custom_content else "ai_persona"
        payload = {
            "padCode": persona.pad_code,
            "dryRun": False,
            "maxAgeDays": 2 if group == "auto_reply_hot_posts" else 7,
            "maxPosts": 6 if group == "auto_reply_hot_posts" else 3,
            "maxReplies": 3,
            "commentPersona": {
                "name": persona.name,
                "profile": persona.description[:1200],
                "replyMode": reply_mode,
                "customContent": custom_content,
            },
        }
        label = f"自動回覆：{persona.name} / {'固定文案' if custom_content else 'AI 人設'}"
        task_type = "threads_auto_reply"
        back_action = f"acctautoreply_{persona_id}"

    job = SourceWorkflowJobRepo.create(task_type, label, payload, status="submitting")
    _submit_source_task_job_async(job.id, task_type, payload)
    return _response(
        _message(
            "\n".join(
                [
                    "✅ 已加入背景自動化任務",
                    "",
                    f"任務：{job.label}",
                    f"Job ID：{job.id}",
                    "來源任務：提交中",
                    f"人設：{persona.name}",
                    f"PAD_CODE：{persona.pad_code}",
                    "",
                    "已送入 Web 後台提交器；來源任務 ID 生成後會寫回本地任務狀態。",
                ]
            ),
            _rows(
                [_btn("來源任務狀態", "source_tasks")],
                [_btn("📊 腳本任務狀態", "local_jobs"), _btn("◀️ 返回自動化", back_action)],
            ),
        ),
        state={"flow": ""},
    )


def _threads_profile_prompt(persona_id: str, kind: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", f"pd:{persona_id}")]]), state={"flow": ""})
    if not persona.pad_code:
        return _response(
            _message(
                f"❌ 這個人設還沒有可用的綁定智能體手機。\n\n人設：{persona.name}\n平台：Threads\n\n請先綁定智能體手機，再執行 Threads 資料修改。",
                _rows([_btn("📱 綁定智能體手機", f"bindpad_{persona_id}")], [_btn("◀️ 返回 Threads 帳號", f"acctplatform_threads_{persona_id}")]),
            ),
            state={"flow": ""},
        )
    titles = {
        "link": "🔗 Threads 簡介新增連結",
        "bio": "📝 修改 Threads 簡介",
        "name": "🏷 修改 Threads 名稱",
        "avatar": "🖼 修改 Threads 頭像",
    }
    hints = {
        "link": "請直接發送要添加到 Threads 個人簡介裡的完整連結。\n例如：https://example.com",
        "bio": "請直接發送新的 Threads 個人簡介，建議 150 字以內。",
        "name": "請直接發送新的 Threads 名稱，建議 30 字以內。",
        "avatar": "請直接發送一張圖片的本機路徑或可下載圖片 URL，作為新的 Threads 頭像。",
    }
    title = titles.get(kind, "Threads 資料修改")
    hint = hints.get(kind, "請直接發送要更新的內容。")
    return _response(
        _message(
            "\n".join([title, "", f"人設：{persona.name}", f"智能體手機：{persona.pad_code}", "", hint]),
            [[_btn("✖️ 取消", f"acctplatform_threads_{persona_id}")]],
        ),
        state={"flow": "threads_profile_update", "draft": {"persona_id": persona_id, "kind": kind}},
    )


def _threads_profile_submit(persona_id: str, kind: str, value: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona or not persona.pad_code:
        return _response(_message("請先綁定智能體手機，再執行 Threads 資料修改。", [[_btn("📱 綁定智能體手機", f"bindpad_{persona_id}"), _btn("◀️ 返回 Threads 帳號", f"acctplatform_threads_{persona_id}")]]), state={"flow": ""})
    kind = kind if kind in {"link", "bio", "name", "avatar"} else ""
    value = str(value or "").strip()
    if not kind or not value:
        return _response(_message("內容不能為空，請重新輸入。", [[_btn("◀️ 返回 Threads 帳號", f"acctplatform_threads_{persona_id}")]]), state={"flow": ""})
    if kind == "link" and not re.match(r"^https?://", value, flags=re.I):
        return _response(_message("❌ 連結格式不對。\n\n請發送完整連結，例如：https://example.com", [[_btn("✖️ 取消", f"acctplatform_threads_{persona_id}")]]), state={"flow": "threads_profile_update", "draft": {"persona_id": persona_id, "kind": kind}})
    labels = {
        "link": "Threads 簡介連結",
        "bio": "Threads 簡介",
        "name": "Threads 名稱",
        "avatar": "Threads 頭像",
    }
    payload = {
        "padCode": persona.pad_code,
        "kind": kind,
        "value": value,
        "timeout_seconds": 900,
    }
    label = f"{labels[kind]}修改｜{persona.name}"
    job = SourceWorkflowJobRepo.create("threads_profile_update", label, payload, status="submitting")
    source_task_id = ""
    try:
        base, data = _source_submit_task("threads_profile_update", payload)
        source_task_id = str(data.get("id") or "")
        SourceWorkflowJobRepo.update(
            job.id,
            status="submitted",
            result=data,
            source_task_id=source_task_id,
            source_base_url=base,
        )
    except Exception as exc:
        SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
        return _response(
            _message(
                f"❌ {labels[kind]}提交失敗\n\n{exc}",
                _rows([_btn("來源 API 狀態", "source_status"), _btn("📊 腳本任務狀態", "local_jobs")], [_btn("◀️ 返回 Threads 帳號", f"acctplatform_threads_{persona_id}")]),
            ),
            state={"flow": ""},
        )
    return _response(
        _message(
            "\n".join(
                [
                    f"✅ 已提交 {labels[kind]} 修改任務",
                    "",
                    f"任務：{job.label}",
                    f"Job ID：{job.id}",
                    f"來源任務：{source_task_id or '-'}",
                    f"人設：{persona.name}",
                    f"PAD_CODE：{persona.pad_code}",
                    "",
                    "已提交到來源執行器，可在本次任務詳情查看執行中、完成或失敗。",
                ]
            ),
            _rows(
                [_btn("查看本次执行结果", f"source_task_detail:{source_task_id}") if source_task_id else _btn("来源任务状态", "source_tasks")],
                [_btn("📊 腳本任務狀態", "local_jobs"), _btn("◀️ 返回 Threads 帳號", f"acctplatform_threads_{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _threads_login_start(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", f"pd:{persona_id}")]]), state={"flow": ""})
    if not persona.pad_code:
        return _response(
            _message(
                "請先綁定智能體手機，再切換 Threads 登入帳號。",
                _rows([_btn("📱 綁定智能體手機", f"bindpad_{persona_id}")], [_btn("◀️ 返回 Threads 帳號", f"acctplatform_threads_{persona_id}")]),
            ),
            state={"flow": ""},
        )
    return _response(
        _message(
            "\n".join(["🔄 切換登入帳號", "", f"人設：{persona.name}", f"智能體手機：{persona.pad_code}", "", "步驟 1/2：請發送 Threads / Instagram 登入帳號。"]),
            [[_btn("✖️ 取消", f"acctplatform_threads_{persona_id}")]],
        ),
        state={"flow": "threads_login_username", "draft": {"persona_id": persona_id}},
    )


def _threads_login_submit(persona_id: str, username: str, password: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona or not persona.pad_code:
        return _response(_message("請先綁定智能體手機，再切換 Threads 登入帳號。", [[_btn("📱 綁定智能體手機", f"bindpad_{persona_id}"), _btn("◀️ 返回 Threads 帳號", f"acctplatform_threads_{persona_id}")]]), state={"flow": ""})
    username = str(username or "").strip()
    password = str(password or "").strip()
    if not username or not password:
        return _threads_login_start(persona_id)
    payload = {
        "padCode": persona.pad_code,
        "username": username,
        "password": password,
        "timeout_seconds": 900,
    }
    label = f"Threads 登入｜{persona.name}｜{username}"
    job = SourceWorkflowJobRepo.create("threads_login", label, {**payload, "password": "***"}, status="submitting")
    try:
        base, data = _source_submit_task("threads_login", payload)
        SourceWorkflowJobRepo.update(
            job.id,
            status="submitted",
            result={k: v for k, v in data.items() if k != "password"} if isinstance(data, dict) else data,
            source_task_id=str(data.get("id") or ""),
            source_base_url=base,
        )
        handle = username.replace("@", "").strip()
        PersonaRepo.upsert(_persona_payload(persona, account_username=handle))
        AccountRepo.upsert_many([(handle, persona.name, persona.name)])
        AccountRepo.assign_pad(handle, persona.pad_code)
    except Exception as exc:
        SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
        return _response(
            _message(
                f"❌ Threads 登入任務提交失敗\n\n{exc}",
                _rows([_btn("來源 API 狀態", "source_status"), _btn("📊 腳本任務狀態", "local_jobs")], [_btn("◀️ 返回 Threads 帳號", f"acctplatform_threads_{persona_id}")]),
            ),
            state={"flow": ""},
        )
    return _response(
        _message(
            "\n".join(
                [
                    "✅ 已提交 Threads 登入任務",
                    "",
                    f"任務：{job.label}",
                    f"Job ID：{job.id}",
                    f"人設：{persona.name}",
                    f"智能體手機：{persona.pad_code}",
                    f"帳號：{username}",
                    "",
                    "遇到驗證碼或安全驗證時，請按智能體手機畫面人工處理，再回來查看任務狀態。",
                ]
            ),
            _rows([_btn("📊 腳本任務狀態", "local_jobs"), _btn("◀️ 返回 Threads 帳號", f"acctplatform_threads_{persona_id}")]),
        ),
        state={"flow": ""},
    )


def _threads_account_query_submit(*, persona_id: str = "", pad_code: str = "", back: str = "menu") -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id) if persona_id else None
    resolved_pad = (pad_code or (persona.pad_code if persona else "") or "").strip()
    if not resolved_pad:
        target = f"bindpad:{persona_id}" if persona_id else "pad_mgmt"
        return _response(_message("請先綁定智能體手機，再查詢 Threads 帳號狀態。", [[_btn("📱 綁定智能體手機", target), _btn("◀️ 返回", back)]]), state={"flow": ""})
    payload = {"padCode": resolved_pad, "timeout_seconds": 120}
    label_name = persona.name if persona else resolved_pad
    label = f"Threads 帳號查詢｜{label_name}"
    job = SourceWorkflowJobRepo.create("threads_account_query", label, payload, status="submitting")
    try:
        base, data = _source_submit_task("threads_account_query", payload)
        SourceWorkflowJobRepo.update(job.id, status="submitted", result=data, source_task_id=str(data.get("id") or ""), source_base_url=base)
    except Exception as exc:
        SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
        return _response(
            _message(
                f"❌ Threads 帳號查詢提交失敗\n\n{exc}",
                _rows([_btn("來源 API 狀態", "source_status"), _btn("📊 腳本任務狀態", "local_jobs")], [_btn("◀️ 返回", back)]),
            ),
            state={"flow": ""},
        )
    return _response(
        _message(
            "\n".join(["✅ 已提交 Threads 帳號查詢任務", "", f"任務：{job.label}", f"Job ID：{job.id}", f"PAD_CODE：{resolved_pad}", "", "可在「腳本任務狀態」查看執行結果。"]),
            _rows([_btn("📊 腳本任務狀態", "local_jobs"), _btn("◀️ 返回", back)]),
        ),
        state={"flow": ""},
    )


def _local_jobs_menu() -> dict[str, Any]:
    jobs = SourceWorkflowJobRepo.list_all(limit=20)
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
    lines = [
        "📊 腳本/來源任務狀態",
        "",
        f"背景排隊 {counts.get('queued_external', 0)}｜執行中 {counts.get('running', 0)}｜完成 {counts.get('success', 0) + counts.get('submitted', 0)}｜失敗 {counts.get('failed', 0)}",
        "",
    ]
    if not jobs:
        lines.append("尚無本機來源任務。")
    for job in jobs:
        error = f"｜錯誤：{_task_preview(job.error, 80)}" if job.error else ""
        lines.append(f"#{job.id[:8]}｜{job.status}｜{job.label}{error}")
    return _response(
        _message(
            "\n".join(lines),
            _rows([_btn("刷新", "local_jobs"), _btn("來源 API 狀態", "source_status")], [_btn("🧭 TG 全功能", "capabilities"), _btn("返回主選單", "menu")]),
        ),
        state={"flow": ""},
    )


def _operator_console_menu() -> dict[str, Any]:
    operators = OperatorRepo.list_all()
    accounts = vmos_client.configured_accounts()
    active = vmos_client.active_account_name()
    lines = [
        "👥 子帳號 / VMOS 帳號",
        "",
        f"目前 VMOS 帳號：{active or '-'}",
        f"已配置 VMOS 帳號：{len(accounts)} 組",
        f"操作員：{len(operators)} 個",
        "",
        "子帳號建立與權限分配已在 /operators 頁面，可指定允許操作的 VMOS 帳號。",
    ]
    if operators:
        lines.append("操作員列表：")
        for operator in operators[:10]:
            allowed = "、".join(operator.allowed_accounts) if operator.allowed_accounts else "全部"
            lines.append(f"- {operator.username}｜{operator.role}｜VMOS：{allowed}")
    return _response(
        _message(
            "\n".join(lines),
            _rows(
                [_btn("打開子帳號管理", "open:/operators"), _btn("打開帳號管理", "open:/accounts")],
                [_btn("刷新 VMOS 雲機", "pad_mgmt_refresh"), _btn("返回主選單", "menu")],
            ),
        ),
        state={"flow": ""},
    )


def _publish_direct_start(persona_id: str) -> dict[str, Any]:
    persona, _row = _resolve_persona_for_action(persona_id)
    if not persona:
        return _response(_message("没有找到本地人设，不能创建发布任务。", [[_btn("◀️ 返回", f"pd:{persona_id}")]]))
    persona_id = persona.id
    return _response(
        _message(
            "✅ 已确认直发模式\n\n目前步骤：1/2 发送内容\n\n请发送其中一种：\n1) 纯文字推文\n2) 图片/视频 + caption\n3) 先发图片/视频，下一步再补文字\n4) 先发文字，下一步再补图片/视频",
            _rows([_btn("📋 查看推文列表", f"pub_posts:0:{persona_id}")], [_btn("🕘 发布历史", f"pub_history:0:{persona_id}")], [_btn("◀️ 返回发布中心", f"pub:{persona_id}")]),
        ),
        state={"flow": "custom_publish_content", "draft": {"persona_id": persona_id}},
    )


def _publish_confirm_from_posts(draft: dict[str, Any]) -> dict[str, Any]:
    selected = [int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()]
    if not selected:
        return _response(_message("请先至少选择一篇推文。", [[_btn("◀️ 返回推文选择", "post_select_back")]]), state={"flow": "post_select", "draft": draft})
    posts = [str(item) for item in draft.get("posts", [])]
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    selected_with_image, selected_without_image = _post_image_counts(posts, post_image_paths, selected)
    text = "\n".join(
        [
            "🚀 发布推文",
            "",
            f"人设：{draft.get('name') or draft.get('persona_id')}",
            f"已选：{len(selected)} 篇",
            f"图片：有图 {selected_with_image} 篇；暂无图 {selected_without_image} 篇",
            "",
            "请选择发布平台：",
        ]
    )
    return _response(
        _message(
            text,
            _rows(
                [_btn("Threads", "pa_pp_cur_threads"), _btn("Telegram", "pa_pp_cur_telegram")],
                [],
                [_btn("◀️ 返回推文選擇", "pa_back")],
            ),
        ),
        state={"flow": "publish_platform", "draft": draft},
    )


def _publish_platform(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    platform = action.split(":", 1)[1] if ":" in action else "threads"
    persona_id = str(draft.get("persona_id") or "")
    local, _row = _resolve_persona_for_action(persona_id)
    if local:
        persona_id = local.id
        draft["persona_id"] = persona_id
    pad = local.pad_code if local else ""
    issue = _publish_device_issue(local)
    if issue:
        return _response(
            _message(issue, _rows([_btn("📱 绑定智能体手机", f"bindpad:{persona_id}")], [_btn("◀️ 返回推文选择", "post_select_back")])),
            state={"flow": "post_select", "draft": draft},
        )
    posts = [str(item) for item in draft.get("posts", [])]
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    selected = [int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()]
    selected_with_image, selected_without_image = _post_image_counts(posts, post_image_paths, selected)
    device = DeviceRepo.get(pad) if pad else None
    device_line = (
        f"雲機名稱：{(device.alias if device else '') or pad}\nPAD_CODE：{pad}"
        if pad
        else "雲機：未綁定"
    )
    publish_status_lines = _publish_status_lines_for_pad(pad, limit=3) if pad else []
    text = "\n".join(
        [
            f"🛰 已选择平台：{platform}",
            "",
            "請確認發布智能體手機：",
            device_line,
            f"图片：有图 {selected_with_image} 篇；暂无图 {selected_without_image} 篇",
            "",
            *publish_status_lines,
        ]
    )
    return _response(
        _message(
            text,
            _rows(
                [_btn("綁定手機發布", f"pa_dop_cur_{platform}")],
                [_btn("根據文字內容生成圖片再發布", f"pa_dopimg_cur_{platform}")],
                [_btn("多智能體手機發布", f"pa_dopm_cur_{platform}")],
                [_btn("📱 修改智能体手机", f"bindpad:{persona_id}")],
                [_btn("◀️ 返回选平台", "bconfirm")],
            ),
        ),
        state={"flow": "publish_confirm", "draft": {**draft, "platform": platform}},
    )


def _publish_platform(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    platform = action.split(":", 1)[1] if ":" in action else action.rsplit("_", 1)[-1]
    if platform not in {"threads", "telegram"}:
        platform = "threads"
    persona_id = str(draft.get("persona_id") or "")
    local, _row = _resolve_persona_for_action(persona_id)
    if local:
        persona_id = local.id
        draft["persona_id"] = persona_id
    pad = local.pad_code if local else ""
    issue = _publish_device_issue(local)
    if issue:
        return _response(
            _message(issue, _rows([_btn("📱 綁定智能體手機", f"bindpad:{persona_id}")], [_btn("◀️ 返回推文選擇", "pa_back")])),
            state={"flow": "post_select", "draft": draft},
        )
    posts = [str(item) for item in draft.get("posts", [])]
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    selected = [int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()]
    selected_with_image, selected_without_image = _post_image_counts(posts, post_image_paths, selected)
    device = DeviceRepo.get(pad) if pad else None
    platform_label = "Threads" if platform == "threads" else "Telegram 群組"
    device_line = f"雲機名稱：{(device.alias if device else '') or pad}\nPAD_CODE：{pad}" if pad else "雲機：未綁定"
    publish_status_lines = _publish_status_lines_for_pad(pad, limit=3) if pad else []
    text = "\n".join(
        [
            f"🚀 已選擇平台：{platform_label}",
            "",
            "請確認發布智能體手機：",
            device_line,
            f"圖片：有圖 {selected_with_image} 篇；暫無圖 {selected_without_image} 篇",
            "",
            *publish_status_lines,
        ]
    )
    return _response(
        _message(
            text,
            _rows(
                [_btn(f"✅ 確認發布到綁定智能體手機 {platform_label}", f"pa_dop_cur_{platform}")],
                [_btn(f"📱 選擇多智能體手機發布 {platform_label}", f"pa_dopm_cur_{platform}")],
                [_btn("◀️ 返回選擇平台", "bconfirm")],
                [_btn("◀️ 返回推文列表", "pa_back")],
            ),
        ),
        state={"flow": "publish_confirm", "draft": {**draft, "platform": platform}},
    )


def _publish_device_issue(persona: Persona | None) -> str:
    if not persona:
        return "没有找到本地人设，不能创建发布任务。"
    if not persona.pad_code:
        return f"人设「{_local_persona_display_name(persona)}」还没有绑定云机，请先绑定智能体手机。"
    device = DeviceRepo.get(persona.pad_code)
    if not device:
        return f"人设绑定的云机不存在：{persona.pad_code}。请重新绑定可用云机。"
    if not vmos_client.is_active_account_name(device.vmos_account):
        return (
            f"这台云机不属于当前启用的 VMOS 账号，不能发布。\n\n"
            f"云机名称：{device.alias or persona.pad_code}\nPAD_CODE：{persona.pad_code}\nVMOS 账号：{device.vmos_account or '-'}"
        )
    return ""


def _ensure_publish_account(persona: Persona | None) -> str:
    if not persona:
        username = "web_bot_publish"
        AccountRepo.upsert_many([(username, "Web Bot", "Web Bot")])
        return username
    username = persona.account_username or f"persona_{persona.id}"
    AccountRepo.upsert_many([(username, persona.name, persona.name)])
    if persona.pad_code:
        AccountRepo.assign_pad(username, persona.pad_code)
    if not persona.account_username:
        PersonaRepo.upsert(_persona_payload(persona, account_username=username))
    return username


def _enqueue_selected_posts(state: dict[str, Any], with_image: bool = False) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    persona_id = str(draft.get("persona_id") or "")
    persona, _row = _resolve_persona_for_action(persona_id)
    if persona:
        persona_id = persona.id
        draft["persona_id"] = persona_id
    issue = _publish_device_issue(persona)
    if issue:
        return _response(
            _message(issue, _rows([_btn("📱 绑定智能体手机", f"bindpad:{persona_id}")], [_btn("◀️ 返回选平台", "bconfirm")])),
            state={"flow": "publish_confirm", "draft": draft},
        )
    posts = [str(item) for item in draft.get("posts", [])]
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    selected = [int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()]
    username = _ensure_publish_account(persona)
    entries = []
    media_count = 0
    image_errors: list[str] = []
    publish_dir = DATA_DIR / "batches" / f"webbot_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_safe_filename(persona_id)}"
    for idx in selected:
        if 0 <= idx < len(posts):
            text = to_traditional(_apply_link_ending_to_text(posts[idx], _active_link_ending_preset(persona_id)))
            media_names: list[str] = []
            source_path = ""
            if idx < len(post_image_paths) and Path(post_image_paths[idx]).exists():
                source_path = post_image_paths[idx]
            elif with_image and persona:
                try:
                    if not _avatar_exists(persona):
                        _generate_persona_reference_image(persona)
                        persona = PersonaRepo.get(persona_id) or persona
                    source_path = _generate_post_image(persona, text, idx)
                except Exception as exc:
                    image_errors.append(f"第 {idx + 1} 篇：{exc}")
                    source_path = ""
            if with_image and not source_path:
                continue
            if source_path and Path(source_path).exists():
                publish_dir.mkdir(parents=True, exist_ok=True)
                suffix = Path(source_path).suffix.lower() or ".png"
                dest_name = f"post_{idx + 1}{suffix}"
                shutil.copy2(source_path, publish_dir / dest_name)
                media_names.append(dest_name)
                media_count += 1
            entries.append(
                {
                    "username": username,
                    "text": text,
                    "scheduled_at": 0,
                    "media_paths": ";".join(media_names),
                    "batch_dir": str(publish_dir) if media_names else "",
                }
            )
    if not entries:
        if image_errors:
            return _response(
                _message(
                    "配图生成失败，未建立发布任务。\n\n" + "\n".join(image_errors[:3]),
                    _rows([_btn("🔄 再生成配图", "regen_post_images"), _btn("◀️ 返回选平台", "bconfirm")]),
                ),
                state={"flow": "publish_confirm", "draft": draft},
            )
        return _response(_message("没有可发布的推文。", [[_btn("◀️ 返回", "post_select_back")]]), state=state)
    added = TaskRepo.add_many(traditionalize_task_entries(entries))
    if persona and entries:
        _record_post_memory(
            persona_id,
            "\n\n".join(str(entry.get("text") or "") for entry in entries),
            granularity=str(draft.get("memory_granularity") or "daily"),
            source_type="publish_queue",
            source_ref=str(draft.get("generated_memory_id") or draft.get("memory_id") or ""),
            title=f"已加入發布佇列 {added} 篇",
            payload={"entries": entries, "added": added, "with_image": with_image},
        )
    media_line = f"配图：{media_count} 张" if media_count else "配图：未附加"
    device = DeviceRepo.get(persona.pad_code) if persona and persona.pad_code else None
    device_line = (
        f"云机名称：{device.alias or persona.pad_code}\nPAD_CODE：{persona.pad_code}"
        if persona and persona.pad_code
        else "云机：未绑定"
    )
    status_lines = _publish_status_lines_for_pad(persona.pad_code, limit=6) if persona and persona.pad_code else []
    return _response(
        _message(
            "\n".join(
                [
                    f"✅ 已提交 {added} 篇推文，待排程器發布。",
                    "",
                    f"人設：{_local_persona_display_name(persona) if persona else persona_id}",
                    device_line,
                    media_line,
                    "",
                    *status_lines,
                ]
            ),
            _rows(
                [_btn("📋 查看推文列表", f"pub_posts:0:{persona_id}"), _btn("🕘 發布歷史", f"pub_history:0:{persona_id}")],
                [_btn("📱 查看雲機畫面", f"device_preview:{persona.pad_code}" if persona and persona.pad_code else "pad_mgmt"), _btn("📋 發帖任務", "open:/tasks")],
                [_btn("◀️ 返回發布中心", f"pub:{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )



MATRIX_PAD_PAGE_SIZE = 10


def _matrix_pads_menu(state: dict[str, Any], *, page: int = 0, action_note: str = "") -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    devices = _active_devices()
    selected = {str(item) for item in draft.get("selected_pads", []) if str(item).strip()}
    total_pages = max(1, (len(devices) + MATRIX_PAD_PAGE_SIZE - 1) // MATRIX_PAD_PAGE_SIZE)
    safe_page = max(0, min(page, total_pages - 1))
    visible = devices[safe_page * MATRIX_PAD_PAGE_SIZE : (safe_page + 1) * MATRIX_PAD_PAGE_SIZE]
    draft["matrix_pad_page"] = safe_page
    draft["selected_pads"] = sorted(selected)
    lines = [
        "🚀 矩阵发布：选择智能体手机",
        "",
        "请选择到时间后要发布的智能体手机：",
        f"已选择：{len(selected)} / {len(devices)} 台",
    ]
    if action_note:
        lines.extend(["", action_note])
    if not devices:
        lines.extend(["", "目前还没有云机，请先到智能体手机管理导入。"])
    for index, device in enumerate(visible, start=safe_page * MATRIX_PAD_PAGE_SIZE + 1):
        mark = "☑️" if device.pad_code in selected else "☐"
        lines.append(f"{index}. {mark} {device.alias or device.pad_code}")
    buttons = [
        _btn(f"{'☑️' if device.pad_code in selected else '☐'} {device.alias or device.pad_code}"[:24], f"pubpad_toggle:{device.pad_code}")
        for device in visible
    ]
    keyboard = _chunk_buttons(buttons, 1)
    keyboard.extend(
        _rows(
            [_btn("☑️ 全选本页", "pubpad_select_page"), _btn("⬜ 清空本页", "pubpad_clear_page")],
            [_btn("☑️ 全选全部", "pubpad_select_all"), _btn("⬜ 清空全部", "pubpad_clear_all")],
        )
    )
    if total_pages > 1:
        keyboard.append(
            [
                _btn("◀️ 上一页", f"pubpad_page:{max(0, safe_page - 1)}"),
                _btn(f"{safe_page + 1}/{total_pages}", f"pubpad_page:{safe_page}"),
                _btn("下一页 ▶️", f"pubpad_page:{min(total_pages - 1, safe_page + 1)}"),
            ]
        )
    keyboard.extend(_rows([_btn(f"✅ 确认发布到 {len(selected)} 台", "pubpad_confirm", "primary")], [_btn("◀️ 返回", "matrix_start")]))
    return _response(_message("\n".join(lines), keyboard), state={"flow": "matrix_pads", "draft": draft})


def _matrix_update_pads(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    devices = _active_devices()
    device_codes = [device.pad_code for device in devices]
    selected = {str(item) for item in draft.get("selected_pads", []) if str(item).strip()}
    page = max(0, _num(draft.get("matrix_pad_page")))
    visible = devices[page * MATRIX_PAD_PAGE_SIZE : (page + 1) * MATRIX_PAD_PAGE_SIZE]
    if action.startswith("pubpad_toggle:"):
        pad_code = action.split(":", 1)[1]
        if pad_code in selected:
            selected.remove(pad_code)
        elif pad_code in device_codes:
            selected.add(pad_code)
    elif action.startswith("pubpad_page:"):
        page = _num(action.split(":", 1)[1])
    elif action == "pubpad_select_page":
        selected.update(device.pad_code for device in visible)
    elif action == "pubpad_clear_page":
        selected.difference_update(device.pad_code for device in visible)
    elif action == "pubpad_select_all":
        selected.update(device_codes)
    elif action == "pubpad_clear_all":
        selected.clear()
    draft["selected_pads"] = sorted(selected)
    draft["matrix_pad_page"] = page
    return _matrix_pads_menu({"draft": draft}, page=page)


def _matrix_publish_account_for_device(device: Device, index: int) -> str:
    username = _account_name_from_pad(device.pad_code, index)
    AccountRepo.upsert_many([(username, "VMOS 云机账号", device.alias or device.pad_code)])
    AccountRepo.assign_pad(username, device.pad_code)
    return username


def _enqueue_matrix_posts(state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    selected_pads = [str(item) for item in draft.get("selected_pads", []) if str(item).strip()]
    if not selected_pads:
        return _matrix_pads_menu(state, action_note="请先至少选择一台智能体手机。")
    posts = [str(item) for item in draft.get("posts", [])]
    selected_posts = [int(item) for item in draft.get("selected", []) if isinstance(item, int) or str(item).isdigit()]
    if not posts or not selected_posts:
        return _response(
            _message(
                "已记录云机选择，但还没有可发布的推文内容。\n\n请先从人设进入「生成推文」或选择待发布推文。",
                _rows([_btn("👤 返回人设管理", "list_personas"), _btn("◀️ 返回选择云机", "custom_publish_multi_now")]),
            ),
            state=state,
        )
    devices_by_pad = {device.pad_code: device for device in _active_devices()}
    entries: list[dict[str, Any]] = []
    post_image_paths = [str(item) for item in draft.get("post_image_paths", [])]
    publish_dir = DATA_DIR / "batches" / f"matrix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    copied_media: dict[int, str] = {}
    for post_index in selected_posts:
        if 0 <= post_index < len(post_image_paths) and Path(post_image_paths[post_index]).exists():
            publish_dir.mkdir(parents=True, exist_ok=True)
            source_path = Path(post_image_paths[post_index])
            dest_name = f"post_{post_index + 1}{source_path.suffix.lower() or '.png'}"
            if post_index not in copied_media:
                shutil.copy2(source_path, publish_dir / dest_name)
                copied_media[post_index] = dest_name
    for dev_index, pad_code in enumerate(selected_pads, start=1):
        device = devices_by_pad.get(pad_code)
        if not device:
            continue
        username = _matrix_publish_account_for_device(device, dev_index)
        for post_index in selected_posts:
            if 0 <= post_index < len(posts):
                media = copied_media.get(post_index, "")
                entries.append(
                    {
                        "username": username,
                        "text": to_traditional(posts[post_index]),
                        "scheduled_at": 0,
                        "media_paths": media,
                        "batch_dir": str(publish_dir) if media else "",
                    }
                )
    if not entries:
        return _matrix_pads_menu(state, action_note="选择的云机没有可建立的发布账号，请刷新设备后再试。")
    added = TaskRepo.add_many(traditionalize_task_entries(entries))
    for persona_id in [str(item) for item in draft.get("selected_personas", []) if str(item).strip()]:
        selected_texts = [
            to_traditional(posts[post_index])
            for post_index in selected_posts
            if 0 <= post_index < len(posts)
        ]
        if selected_texts:
            _record_post_memory(
                persona_id,
                "\n\n".join(selected_texts),
                granularity=str(draft.get("memory_granularity") or "daily"),
                source_type="matrix_publish_queue",
                source_ref=str(draft.get("generated_memory_id") or draft.get("memory_id") or ""),
                title=f"矩陣發布佇列 {added} 則任務",
                payload={"selected_pads": selected_pads, "selected_posts": selected_posts, "added": added},
            )
    status_blocks: list[str] = []
    for pad_code in selected_pads[:8]:
        device = devices_by_pad.get(pad_code) or DeviceRepo.get(pad_code)
        pad_name = (device.alias if device and device.alias else "") or pad_code
        status_blocks.extend(["", f"📱 {pad_name}", f"PAD_CODE：{pad_code}"])
        status_blocks.extend(_publish_status_lines_for_pad(pad_code, limit=2))
    if len(selected_pads) > 8:
        status_blocks.append(f"...另有 {len(selected_pads) - 8} 台，請到發帖任務查看。")
    return _response(
        _message(
            "\n".join(
                [
                    f"✅ 已提交矩陣發布任務：{added} 則",
                    "",
                    f"智能體手機：{len(selected_pads)} 台",
                    f"推文：{len(selected_posts)} 篇",
                    *status_blocks,
                ]
            ),
            _rows([_btn("📋 發帖任務", "open:/tasks"), _btn("📱 手機管理", "pad_mgmt")], [_btn("返回主選單", "menu")]),
        ),
        state={"flow": ""},
    )


def _account_management(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("这个人设只能在来源数据中查看，不能编辑账号。", [[_btn("◀️ 返回", f"pd:{persona_id}")]]))
    text = "\n".join(
        [
            "🔐 账号管理",
            "",
            f"人设：{persona.name}",
            "",
            f"Threads：{persona.account_username or '未设置'}",
            f"Telegram：{persona.tg_free_group_name or persona.tg_free_group_id or '未设置'}",
            "",
            "请选择要设置的平台。",
        ]
    )
    return _response(
        _message(
            text,
            _rows(
                [_btn("Threads", f"acctplatform_threads_{persona_id}"), _btn("Telegram", f"acctplatform_telegram_{persona_id}")],
                [_btn("◀️ 返回人設設定", f"settings_{persona_id}")],
            ),
        ) ,
        state={"flow": ""},
    )


def _threads_account_panel(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("没有找到本地人设。", [[_btn("◀️ 返回", f"pd:{persona_id}")]]))
    text = "\n".join(
        [
            "🔐 Threads 账号设置",
            "",
            f"人设：{persona.name}",
            f"绑定智能体手机：{persona.pad_code or '未绑定'}",
            f"目前账号：{persona.account_username or '未设定'}",
            "密码：未设定",
            "",
            "必填资料：Threads 使用者名/信箱/手机号 + 密码。",
            "可跳过资料：验证码、安全验证、设备确认。登录遇到时会返回截图，请人工处理后继续。",
        ]
    )
    keyboard = _rows(
        [_btn("🪪 人設帳號綁定", f"acctbindmenu_threads_{persona_id}")],
        [_btn("📱 更換綁定智能體手機", f"bindpad_{persona_id}")],
        [_btn("🔄 切換登入帳號", f"acctlogin_threads_{persona_id}")],
        [_btn("🌱 養號", f"persona_warmup_{persona_id}")],
        [_btn("🔗 Threads 簡介新增連結", f"threads_profile_link_{persona_id}"), _btn("📝 修改 Threads 簡介", f"threads_profile_bio_{persona_id}")],
        [_btn("🏷 修改 Threads 名稱", f"threads_profile_name_{persona_id}"), _btn("🖼 修改 Threads 頭像", f"threads_profile_avatar_{persona_id}")],
        [_btn("◀️ 返回帳號管理", f"acctmgmt_{persona_id}")],
    )
    return _response(_message(text, keyboard), state={"flow": ""})


def _threads_account_binding_menu(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", f"pd:{persona_id}")]]), state={"flow": ""})
    current = f"@{persona.account_username}" if persona.account_username else "未設定"
    rows = [[_btn("✏️ 更換綁定帳號" if persona.account_username else "➕ 綁定人設帳號", f"acctbind_threads_{persona_id}")]]
    if persona.account_username:
        rows.append([_btn("🧹 清除當前帳號數據", f"acctclear_threads_{persona_id}", "danger")])
    rows.append([_btn("◀️ 返回 Threads 帳號", f"acctplatform_threads_{persona_id}")])
    return _response(
        _message(
            "\n".join(
                [
                    "🪪 人設帳號綁定",
                    "",
                    f"人設：{persona.name}",
                    f"目前帳號：{current}",
                    "",
                    "你可以在這裡綁定、替換，或清除目前保存的人設帳號資料。",
                ]
            ),
            rows,
        ),
        state={"flow": ""},
    )


def _threads_account_clear(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("沒有找到本地人設。", [[_btn("◀️ 返回", f"pd:{persona_id}")]]), state={"flow": ""})
    PersonaRepo.upsert(_persona_payload(persona, account_username=""))
    return _response(
        _message("✅ 已清除 Threads 已保存帳號資料。\n\n這只會清除本地保存的人設帳號，不會登出智能體手機 App。", [[_btn("◀️ 返回 Threads 帳號", f"acctplatform_threads:{persona_id}")]]),
        state={"flow": ""},
    )


def _telegram_account_panel(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    if not persona:
        return _response(_message("没有找到本地人设。", [[_btn("◀️ 返回", f"pd:{persona_id}")]]))
    text = "\n".join(
        [
            "🔐 Telegram 账号设置",
            "",
            f"人设：{persona.name}",
            f"TG 通用群：{persona.tg_free_group_name or persona.tg_free_group_id or '未设置'}",
            "",
            "请设置 Telegram 群组或登入资料。",
        ]
    )
    return _response(
        _message(
            text,
            _rows(
                [_btn("TG 通用群", f"bindtg_free_{persona_id}")],
                [_btn("设置 Telegram 手机", f"acctbind_telegram_{persona_id}")],
                [_btn("設定 TG 登入憑證", f"tg_credentials_set_{persona_id}")],
                [_btn("清除本地登入資料", f"tg_credentials_clear_{persona_id}", "danger")],
                [_btn("◀️ 返回帳號管理", f"acctmgmt_{persona_id}")],
            ),
        ),
        state={"flow": ""},
    )


def _bind_pad(persona_id: str) -> dict[str, Any]:
    persona = PersonaRepo.get(persona_id)
    devices = _active_devices()
    if not persona:
        return _response(_message("没有找到本地人设。", [[_btn("◀️ 返回", "list_personas")]]))
    if not devices:
        return _response(
            _message(
                "未能获取智能体手机列表。\n\n⭐ 请手动输入 padCode ⭐",
                [[_btn("❌ 取消", f"pd:{persona_id}")]],
            ),
            state={"flow": "bind_pad_manual", "draft": {"persona_id": persona_id}},
        )
    rows = _chunk_buttons(
        [
            _btn(f"{'✅ ' if device.pad_code == persona.pad_code else ''}{device.alias or device.pad_code}"[:22], f"selectpad:{persona_id}:{device.pad_code}")
            for device in devices[:12]
        ],
        2,
    )
    rows.extend(_rows([_btn("✍️ 手动输入 padCode", f"bindpad_manual:{persona_id}")], [_btn("❌ 取消", f"pd:{persona_id}")]))
    text = "\n".join(
        [
            "📱 选择要绑定的智能体手机",
            "",
            f"当前绑定：{persona.pad_code or '未绑定'}",
            f"共 {len(devices)} 台",
            "",
            "请选择智能体手机，或手动输入 padCode：",
        ]
    )
    return _response(_message(text, rows), state={"flow": ""})


def _select_pad(action: str) -> dict[str, Any]:
    _, persona_id, pad_code = action.split(":", 2)
    persona = PersonaRepo.get(persona_id)
    device = DeviceRepo.get(pad_code)
    if not persona or not device:
        return _response(_message("绑定失败：人设或智能体手机不存在。", [[_btn("◀️ 返回", f"pd:{persona_id}")]]))
    PersonaRepo.upsert(_persona_payload(persona, pad_code=pad_code))
    return _response(
        _message(f"✅ 已绑定智能体手机：{device.alias or pad_code}", [[_btn("◀️ 返回设置", f"settings_{persona_id}"), _btn("📱 手机详情", f"pad_detail:{pad_code}")]]),
        state={"flow": ""},
    )


def _continue_persona_context_text(message: str, state: dict[str, Any]) -> dict[str, Any] | None:
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    persona_id = str(draft.get("persona_id") or "").strip()
    if not persona_id:
        return None
    compact = re.sub(r"\s+", "", str(message or "").strip().lower())
    if any(key in compact for key in ("用此人设图新建推文", "用此人設圖新建推文", "生成推文", "新建推文", "產生推文")):
        return _start_generate_posts(persona_id)
    if any(key in compact for key in ("发布推文", "發布推文", "发布", "發布")):
        return handle({"action": f"pub:{persona_id}", "state": state})
    if any(key in compact for key in ("查看人设图", "查看人設圖", "人设图", "人設圖")):
        return _view_persona_image(persona_id)
    if any(key in compact for key in ("重新生成人设图", "重新生成人設圖", "重新生成图片", "重新生成圖片")):
        return _generate_persona_image_response(persona_id, regenerate=True)
    if any(key in compact for key in ("返回人设详情", "返回人設詳情", "查看人设详情", "查看人設詳情", "人设详情", "人設詳情")):
        return _persona_detail(persona_id)
    return None


def _continue_sentiment_hot_edit(message: str, media: list[dict[str, str]], state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    candidates = [item for item in draft.get("hot_candidates", []) if isinstance(item, dict)]
    index = _num(draft.get("hot_edit_index"))
    if not (0 <= index < len(candidates)) or (not message and not media):
        return _sentiment_hot_expired(draft)
    candidate_id = str(candidates[index].get("id") or "")
    if message:
        edited = dict(draft.get("hot_edited_contents") if isinstance(draft.get("hot_edited_contents"), dict) else {})
        edited[candidate_id] = message
        draft["hot_edited_contents"] = edited
    if media:
        replacements = dict(draft.get("hot_replacement_media") if isinstance(draft.get("hot_replacement_media"), dict) else {})
        replacements[candidate_id] = media[0]
        draft["hot_replacement_media"] = replacements
    key = str(draft.get("hot_edit_action_key") or draft.get("sentiment_action_key") or "")
    return _sentiment_hot_import(f"shuse_{key}_{index}", {"draft": draft})


def _continue_state_text(message: str, state: dict[str, Any]) -> dict[str, Any]:
    flow = str(state.get("flow") or "")
    draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
    text = str(message or "").strip()
    persona_id = str(draft.get("persona_id") or "")
    persona = PersonaRepo.get(persona_id) if persona_id else None

    if flow in {"persona_detail", "persona_image"}:
        response = _continue_persona_context_text(text, state)
        if response:
            return response
        return _response(
            _message("请使用下方按钮，或输入：生成推文、发布推文、查看人设图、重新生成人设图。", [[_btn("◀️ 返回人设详情", f"pd:{persona_id}")]]),
            state=state,
        )

    if flow.startswith("create_persona_"):
        return _continue_create_persona(text, state)
    if flow.startswith("account_create_"):
        return _continue_account_create(text, state)
    if flow.startswith("link_ending_"):
        return _save_link_ending_input(text, state)
    if flow == "source_post_custom_content":
        if not text:
            return _response(_message("請直接輸入要保存的新文案。", [[_btn("◀️ 返回文案管理", f"pa_ed_{draft.get('post_action_key')}")]]), state=state)
        return _source_post_action_submit(state, "update_content", label="保存自訂推文文案", extra={"content": text})
    if flow == "source_post_replace_media_url":
        media_url = _safe_web_media_url(text)
        indexes = [int(item) for item in draft.get("selected_media_indexes", []) if str(item).isdigit()]
        if not media_url:
            return _response(_message("請輸入可直接訪問的圖片或視頻 URL。", [[_btn("◀️ 返回替換方式", f"pa_mrs_{draft.get('post_action_key')}")]]), state=state)
        return _source_post_action_submit(state, "replace_media", label="替換推文媒體", extra={"selectedIndexes": indexes, "mediaUrl": media_url})
    if flow == "sentiment_hot_edit_input":
        return _continue_sentiment_hot_edit(text, [], state)
    if flow.startswith("genpost_"):
        return _continue_generate_posts(text, state)
    if flow == "tg_credentials" and persona:
        return _save_tg_credentials(text, persona_id)
    if flow == "automation_fixed_reply" and persona:
        kind = str(draft.get("kind") or "auto_reply_comments")
        return _automation_run(f"automation_run:{kind}:fixed:{persona_id}", custom_content=text)
    if flow == "ownreply_reply_text" and persona:
        if not text or len(text) > 220:
            return _response(_message("❌ 回覆內容格式不正確，請輸入 1-220 字的回覆內容。", [[_btn("◀️ 返回分支選擇", f"persona_autoreply_hot_{persona_id}")]]), state=state)
        draft["reply_text"] = text
        draft["reply_mode"] = "manual"
        return _own_reply_views_prompt(draft)
    if flow == "ownreply_views" and persona:
        min_views = _parse_own_reply_views(text)
        if min_views is None:
            return _response(_message("❌ 瀏覽量格式不正確，請輸入數字，例如：10000、1萬、2.5萬。", [[_btn("◀️ 返回自動回覆", f"persona_autoreply_{persona_id}")]]), state=state)
        draft["min_views"] = min_views
        return _own_reply_days_prompt(draft)
    if flow == "ownreply_days" and persona:
        days = _num(text)
        if not 1 <= days <= 7:
            return _response(_message("❌ 查看天數格式不正確。\n\n請輸入 1-7 之間的數字，例如：2。", [[_btn("◀️ 返回自動回覆", f"persona_autoreply_{persona_id}")]]), state=state)
        draft["max_age_days"] = days
        return _own_reply_confirmation(draft)
    if flow == "threads_profile_update" and persona:
        return _threads_profile_submit(persona_id, str(draft.get("kind") or ""), text)
    if flow == "threads_login_username" and persona:
        username = text.replace("@", "").strip()
        if not username:
            return _response(_message("帳號不能為空，請重新發送 Threads / Instagram 登入帳號。", [[_btn("✖️ 取消", f"acctplatform_threads:{persona_id}")]]), state=state)
        draft["username"] = username
        return _response(
            _message(
                "\n".join(["🔄 切換登入帳號", "", f"人設：{persona.name}", f"帳號：{username}", "", "步驟 2/2：請發送 Threads / Instagram 登入密碼。"]),
                [[_btn("✖️ 取消", f"acctplatform_threads:{persona_id}")]],
            ),
            state={"flow": "threads_login_password", "draft": draft},
        )
    if flow == "threads_login_password" and persona:
        username = str(draft.get("username") or "").strip()
        return _threads_login_submit(persona_id, username, text)
    if flow == "schedule_time":
        return _schedule_submit_at(_parse_schedule_time_input(text), state)
    if flow == "source_workflow_collect":
        return _continue_source_workflow(text, state)
    if flow == "replace_persona_image":
        return _replace_persona_image_from_text(text, state)
    if flow == "edit_persona_name" and persona:
        PersonaRepo.upsert(_persona_payload(persona, name=text[:80]))
        _record_persona_setting_update(persona_id, "name", text[:80])
        return _response(_message(f"✅ 人设名称已更新为：{text[:80]}", [[_btn("◀️ 返回设置", f"settings_{persona_id}")]]), state={"flow": ""})
    if flow == "edit_persona_desc" and persona:
        _local, row = _resolve_persona_for_action(persona_id)
        source_archive_id = _tool_r18_archive_id(persona_id, persona, row)
        if not source_archive_id:
            return _response(_message("這個 Web 本地人設尚未同步到 Tool R18 人設庫，不能直接替換來源人設簡介。", [[_btn("◀️ 返回設定", f"settings_{persona_id}")]]), state={"flow": ""})
        params = {"archiveId": source_archive_id, "direction": text, "mode": "direct"}
        job = SourceWorkflowJobRepo.create("persona_rewrite_intro", f"替換人設簡介：{persona.name}", params, status="submitting")
        try:
            base, data = _source_submit_task("persona_rewrite_intro", params)
            SourceWorkflowJobRepo.update(job.id, status="submitted", result=data, source_task_id=str(data.get("id") or ""), source_base_url=base)
        except Exception as exc:
            SourceWorkflowJobRepo.update(job.id, status="failed", error=str(exc))
            return _response(_message(f"❌ 人設簡介任務提交失敗\n\n{exc}", [[_btn("◀️ 返回設定", f"settings_{persona_id}")]]), state={"flow": ""})
        source_task_id = str(data.get("id") or "")
        return _response(
            _message(
                f"🧠 正在替換人設簡介...\n\n人設：{persona.name}\n來源任務 ID：{source_task_id or '-'}\n\n完成後會同步更新 Tool R18 人設庫。",
                _rows([_btn("📊 查看本次任務", f"source_task_detail:{source_task_id}")], [_btn("◀️ 返回設定", f"settings_{persona_id}")]),
            ),
            state={"flow": ""},
        )
    if flow == "edit_persona_desc_regen" and persona:
        return _generate_persona_bio_response(persona_id, text)
    if flow == "edit_persona_style" and persona:
        PersonaRepo.upsert(_persona_payload(persona, style_prompt=to_traditional(text[:2400])))
        _record_post_memory(
            persona_id,
            text[:2400],
            granularity="persona",
            source_type="tweet_style_reference",
            title="推文風格案例",
            favorite=True,
        )
        return _response(_message("✅ 推文風格已保存，後續生成會優先參考這個語氣、格式與行文邏輯。", [[_btn("◀️ 返回設定", f"settings_{persona_id}"), _btn("✍️ 生成推文", f"genpost_branch_{persona_id}")]]), state={"flow": ""})
    if flow == "bind_tg_group" and persona:
        PersonaRepo.upsert(_persona_payload(persona, tg_free_group_name=text))
        return _response(_message(f"✅ 已保存 TG 通用群：{text}", [[_btn("◀️ 返回设置", f"settings_{persona_id}")]]), state={"flow": ""})
    if flow == "bindtg_paid" and persona:
        PersonaRepo.upsert(_persona_payload(persona, tg_paid_group_name=text))
        return _response(_message(f"✅ 已保存 TG 付費群：{text}", [[_btn("◀️ 返回設定", f"settings_{persona_id}")]]), state={"flow": ""})
    if flow == "acct_threads_handle" and persona:
        handle = text.replace("@", "").strip()
        AccountRepo.upsert_many([(handle, persona.name, persona.name)])
        if persona.pad_code:
            AccountRepo.assign_pad(handle, persona.pad_code)
        PersonaRepo.upsert(_persona_payload(persona, account_username=handle))
        return _response(_message(f"✅ 已保存 Threads 帳號：@{handle}", [[_btn("◀️ 返回人設帳號綁定", f"acctbindmenu_threads:{persona_id}"), _btn("◀️ 返回 Threads 帳號", f"acctplatform_threads:{persona_id}")]]), state={"flow": ""})
    if flow == "bind_pad_manual" and persona:
        pad_code = text
        if not DeviceRepo.exists(pad_code):
            return _response(_message("这台智能体手机不在当前列表中，请先导入设备或重新输入。", [[_btn("◀️ 返回设置", f"settings_{persona_id}")]]), state=state)
        PersonaRepo.upsert(_persona_payload(persona, pad_code=pad_code))
        return _response(_message(f"✅ 已绑定智能体手机：{pad_code}", [[_btn("◀️ 返回设置", f"settings_{persona_id}")]]), state={"flow": ""})
    if flow == "custom_publish_content" and persona:
        username = _ensure_publish_account(persona)
        text = _apply_link_ending_to_text(text, _active_link_ending_preset(persona_id))
        TaskRepo.add_many(traditionalize_task_entries([{"username": username, "text": text, "scheduled_at": 0, "media_paths": "", "batch_dir": ""}]))
        _record_post_memory(
            persona.id,
            text,
            granularity="daily",
            source_type="custom_publish",
            title="直接發布推文",
            payload={"username": username},
        )
        return _response(
            _message(
                "✅ 已提交 1 篇自定义推文，待排程器发布。",
                _rows(
                    [_btn("📋 查看推文列表", f"pub_posts:0:{persona_id}"), _btn("🕘 发布历史", f"pub_history:0:{persona_id}")],
                    [_btn("📋 打开发帖任务", "open:/tasks"), _btn("◀️ 返回发布中心", f"pub:{persona_id}")],
                ),
            ),
            state={"flow": ""},
        )
    if flow == "t2i_prompt":
        draft["prompt"] = text
        return _response(
            _message(
                "\n".join(
                    [
                        "✅ 已收到提示词",
                        "",
                        f"画面比例：{draft.get('ratio') or '-'}",
                        f"最终分辨率：{'开启' if draft.get('final_resolution') else '关闭'}",
                        f"人设：{draft.get('persona_id') or '不使用'}",
                        "",
                        f"提示词：{text}",
                    ]
                ),
                _rows(
                    [_btn("使用这个提示词生成", "source_t2i_confirm")],
                    [_btn("继续让 Grok 调整", "t2i_prompt_mode:grok"), _btn("重新生成提示词", "t2i_prompt_mode:grok")],
                    [_btn("返回参数设定", "text_to_image"), _btn("返回主选单", "menu")],
                ),
            ),
            state={"flow": "t2i_ready", "draft": draft},
        )
    if flow == "video_duration":
        seconds = max(2, min(_num(text), 15))
        draft["duration"] = seconds
        return _response(
            _message(
                "\n".join(["视频生成设置", "当前步骤：3/5 上传参考图", f"分辨率：{draft.get('resolution')}", f"时长：{seconds}秒", "", "请上传一张参考图片；Web 版可先继续选择提示词方式。"]),
                _rows([_btn("跳过上传，继续", "video_prompt_mode"), _btn("返回主菜单", "menu")]),
            ),
            state={"flow": "video_image", "draft": draft},
        )
    return _main_menu()


def _update_post_memory_payload(memory_id: str, payload: dict[str, Any]) -> bool:
    memory_id = str(memory_id or "").strip()
    if not memory_id:
        return False
    try:
        from db import get_conn

        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE post_memories SET payload_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(payload or {}, ensure_ascii=False), time.time(), memory_id),
            )
        return cur.rowcount > 0
    except Exception:
        return False


def _generated_post_memory_for_persona(persona_id: str) -> PostMemory | None:
    for memory in PostMemoryRepo.list_for_persona(persona_id, limit=80):
        if memory.source_type != "generated_posts":
            continue
        payload = memory.payload
        posts = payload.get("posts") if isinstance(payload, dict) else None
        if isinstance(posts, list) and posts:
            return memory
    return None


def _draft_from_generated_post_memory(persona_id: str, memory: PostMemory, *, page: int = 0) -> dict[str, Any]:
    persona_id, persona, row, name = _genpost_context(persona_id)
    payload = memory.payload if isinstance(memory.payload, dict) else {}
    posts = [to_traditional(str(item)) for item in (payload.get("posts") if isinstance(payload.get("posts"), list) else [])]
    image_paths = [str(item) for item in (payload.get("post_image_paths") if isinstance(payload.get("post_image_paths"), list) else [])]
    while len(image_paths) < len(posts):
        image_paths.append("")
    selected = payload.get("selected") if isinstance(payload.get("selected"), list) else list(range(min(2, len(posts))))
    candidates = payload.get("post_image_candidates") if isinstance(payload.get("post_image_candidates"), dict) else {}
    draft = {
        "persona_id": persona_id,
        "name": name,
        "posts": posts,
        "selected": [int(item) for item in selected if str(item).isdigit() and 0 <= int(item) < len(posts)],
        "post_page": max(0, int(page or 0)),
        "image_group": max(1, _num(payload.get("image_group")) or 1),
        "generated_memory_id": memory.id,
        "memory_id": str(memory.source_ref or ""),
        "memory": str(payload.get("input_memory") or ""),
        "hot_context": str(payload.get("hot_context") or ""),
        "memory_granularity": str(payload.get("memory_granularity") or memory.granularity or "daily"),
        "content_branch": str(payload.get("content_branch") or ""),
        "content_time_slot": str(payload.get("content_time_slot") or ""),
        "words": _num(payload.get("words")),
        "post_image_paths": image_paths[: len(posts)],
        "post_image_candidates": candidates,
    }
    return draft


def _persist_generated_post_draft(draft: dict[str, Any]) -> bool:
    memory_id = str(draft.get("generated_memory_id") or "").strip()
    memory = PostMemoryRepo.get(memory_id) if memory_id else None
    if not memory or memory.source_type != "generated_posts":
        return False
    posts = [to_traditional(str(item)) for item in draft.get("posts", [])]
    payload = memory.payload if isinstance(memory.payload, dict) else {}
    payload.update(
        {
            "posts": posts,
            "words": _num(draft.get("words")),
            "input_memory": str(draft.get("memory") or ""),
            "hot_context": str(draft.get("hot_context") or ""),
            "memory_granularity": str(draft.get("memory_granularity") or "daily"),
            "post_image_paths": [str(item) for item in draft.get("post_image_paths", [])],
            "post_image_candidates": draft.get("post_image_candidates") if isinstance(draft.get("post_image_candidates"), dict) else {},
            "selected": [int(item) for item in draft.get("selected", []) if str(item).isdigit()],
            "image_group": max(1, _num(draft.get("image_group")) or 1),
            "content_branch": str(draft.get("content_branch") or ""),
            "content_time_slot": str(draft.get("content_time_slot") or ""),
        }
    )
    return _update_post_memory_payload(memory.id, payload)


def _stored_generated_posts_response(persona_id: str, page: int = 0, state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    state = state if isinstance(state, dict) else {}
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    if draft.get("posts") and str(draft.get("persona_id") or "") == str(persona_id or ""):
        draft["post_page"] = max(0, page)
        _persist_generated_post_draft(draft)
        return _response(_post_select_message(draft), state={"flow": "post_select", "draft": draft})
    memory = _generated_post_memory_for_persona(persona_id)
    if not memory:
        return None
    draft = _draft_from_generated_post_memory(persona_id, memory, page=page)
    return _response(_post_select_message(draft), state={"flow": "post_select", "draft": draft})


def _generated_posts_for_persona(persona_id: str, *, favorite_only: bool = False) -> list[str]:
    persona_id = str(persona_id or "").strip()
    if not persona_id:
        return []
    memories = PostMemoryRepo.list_for_persona(persona_id, limit=80, favorite_only=favorite_only)
    for memory in memories:
        payload = memory.payload if isinstance(memory.payload, dict) else {}
        posts = payload.get("posts") if isinstance(payload.get("posts"), list) else None
        if posts:
            return [to_traditional(str(item)).strip() for item in posts if str(item or "").strip()]
        if favorite_only and str(memory.content or "").strip():
            return [to_traditional(str(memory.content)).strip()]
    return []


def _matrix_selected_personas(draft: dict[str, Any]) -> list[Persona]:
    ids = [str(item) for item in draft.get("selected_personas", []) if str(item).strip()]
    personas: list[Persona] = []
    seen: set[str] = set()
    for persona_id in ids:
        if persona_id in seen:
            continue
        persona = PersonaRepo.get(persona_id)
        if persona:
            personas.append(persona)
            seen.add(persona_id)
    return personas


def _matrix_real_posts(persona_id: str) -> tuple[str, Persona | None, dict[str, Any] | None, list[dict[str, Any]]]:
    local, row = _resolve_persona_for_action(persona_id)
    archive_id = _tool_r18_archive_id(persona_id, local, row)
    return archive_id, local, row, _source_pending_posts(row) if archive_id else []


def _matrix_persona_rows() -> list[tuple[Persona, dict[str, Any]]]:
    result: list[tuple[Persona, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in _persona_menu_rows():
        persona_id = str(row.get("id") or "").strip()
        if not persona_id or persona_id in seen:
            continue
        persona = _ensure_local_persona_from_row(row, PersonaRepo.get(persona_id))
        if not persona:
            continue
        result.append((persona, row))
        seen.add(persona.id)
    return result


def _matrix_start() -> dict[str, Any]:
    rows = _matrix_persona_rows()
    if not rows:
        return _response(_message("目前沒有可矩陣發布的人設，請先建立新的人設。", [[_btn("➕ 建立人設", "create_persona_entry")]]))
    return _matrix_persona_selection({"selected_personas": [], "matrix_page": 0})


def _matrix_persona_selection(draft: dict[str, Any], note: str = "") -> dict[str, Any]:
    rows = _matrix_persona_rows()
    selected = {str(item) for item in draft.get("selected_personas", []) if str(item).strip()}
    page_size = 8
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(_num(draft.get("matrix_page")), total_pages - 1))
    visible = rows[page * page_size : (page + 1) * page_size]
    draft["matrix_page"] = page
    lines = ["🚀 矩陣發布", ""]
    if total_pages > 1:
        lines.extend([f"第 {page + 1}/{total_pages} 頁", ""])
    lines.extend([f"已選人設：{len(selected)} 個", "請選擇要一起發布的人設。"])
    if note:
        lines.extend(["", note])
    keyboard = [
        [_btn(f"{'✅' if persona.id in selected else '⬜'} {_persona_action_label(row)}", f"mxpt_{persona.id}")]
        for persona, row in visible
    ]
    if total_pages > 1:
        if page > 0:
            keyboard.append([_btn("⏮ 首頁", "mxpg_0"), _btn("◀️ 上一頁", f"mxpg_{page - 1}")])
        keyboard.append([_btn(f"{page + 1}/{total_pages}", f"mxpg_{page}")])
        if page < total_pages - 1:
            keyboard.append([_btn("下一頁 ▶️", f"mxpg_{page + 1}"), _btn("尾頁 ⏭", f"mxpg_{total_pages - 1}")])
    keyboard.extend(
        _rows(
            [_btn("✅ 選本頁", "mxpsel"), _btn("🧹 清本頁", "mxpclr")],
            [_btn(f"下一步：選擇來源（{len(selected)}）", "mxpc")],
            [_btn("◀️ 返回人設列表", "list_personas")],
        )
    )
    draft["selected_personas"] = sorted(selected)
    return _response(_message("\n".join(lines), keyboard), state={"flow": "matrix_select", "draft": draft})


def _matrix_toggle_persona(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    selected = {str(item) for item in draft.get("selected_personas", []) if str(item).strip()}
    rows = _matrix_persona_rows()
    valid_ids = {persona.id for persona, _ in rows}
    page_size = 8
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(_num(draft.get("matrix_page")), total_pages - 1))
    visible_ids = {persona.id for persona, _ in rows[page * page_size : (page + 1) * page_size]}
    if action.startswith("mxpg_"):
        draft["matrix_page"] = max(0, min(_num(action[len("mxpg_") :]), total_pages - 1))
    elif action.startswith("mxpt_"):
        persona_id = action[len("mxpt_") :]
        if persona_id in selected:
            selected.remove(persona_id)
        elif persona_id in valid_ids:
            selected.add(persona_id)
    elif action == "mxpsel":
        selected.update(visible_ids)
    elif action == "mxpclr":
        selected.difference_update(visible_ids)
    draft["selected_personas"] = sorted(selected)
    return _matrix_persona_selection(draft)


def _matrix_source_menu(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    personas = _matrix_selected_personas(draft)
    if not personas:
        return _matrix_persona_selection(draft, "請先至少選擇一個人設。")
    text = "\n".join(["🚀 矩陣發布", "", f"已選人設：{len(personas)} 個", "", "請選擇推文來源："])
    return _response(
        _message(text, _rows([_btn("📝 待發布推文", "mxsrc_posts")], [_btn("⭐ 收藏推文", "mxsrc_favorites")], [_btn("◀️ 返回選人設", "matrix_start")])),
        state={"flow": "matrix_source", "draft": draft},
    )


def _matrix_platform_menu(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    source = action.split("_", 1)[1] if action.startswith("mxsrc_") else str(draft.get("matrix_source") or "")
    if source not in {"posts", "favorites"}:
        return _matrix_source_menu({"draft": draft})
    draft["matrix_source"] = source
    source_label = "待發布推文" if source == "posts" else "收藏推文"
    text = "\n".join(["🚀 矩陣發布", "", f"人設：{len(_matrix_selected_personas(draft))} 個", f"來源：{source_label}", "", "請選擇發布平台："])
    return _response(
        _message(text, _rows([_btn("Threads", "mxplat_threads")], [_btn("◀️ 返回來源", "mxb_source")])),
        state={"flow": "matrix_platform", "draft": draft},
    )


def _matrix_count_menu(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    platform = action.split("_", 1)[1] if action.startswith("mxplat_") else str(draft.get("matrix_platform") or "threads")
    draft["matrix_platform"] = platform
    source = str(draft.get("matrix_source") or "posts")
    personas = _matrix_selected_personas(draft)
    if source == "favorites":
        return _response(
            _message("收藏推文必须先按 TG Bot 流程回存为待发布推文，再执行矩阵发布。", [[_btn("◀️ 返回来源", "mxb_source")]]),
            state={"flow": "matrix_source", "draft": draft},
        )
    counts = [_matrix_real_posts(persona.id)[3] for persona in personas]
    common_limit = min((len(items) for items in counts), default=0)
    lines = ["🚀 矩陣發布", "", f"人設：{len(personas)} 個", f"平台：{platform}", f"每個人設共同可發布上限：{common_limit} 篇", "", "請選擇每個人設要發布幾篇："]
    rows = [[_btn("1 篇", "mxc_1"), _btn("2 篇", "mxc_2")], [_btn("3 篇", "mxc_3"), _btn("每人全部", "mxc_all")], [_btn("◀️ 返回平台", "mxback_platform")]]
    if common_limit <= 0:
        lines.extend(["", "❌ 目前沒有可發布的推文。請先進入人設生成推文，或收藏要發布的推文。"])
    return _response(_message("\n".join(lines), rows), state={"flow": "matrix_count", "draft": draft})


def _matrix_confirm(action: str, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    token = action.split("_", 1)[1] if action.startswith("mxc_") else str(draft.get("matrix_count") or "1")
    source = str(draft.get("matrix_source") or "posts")
    personas = _matrix_selected_personas(draft)
    common_limit = min((len(_matrix_real_posts(persona.id)[3]) for persona in personas), default=0)
    count: int | str = "all" if token == "all" else _num(token)
    if common_limit <= 0 or (count != "all" and (count <= 0 or count > common_limit)):
        return _matrix_count_menu("mxplat_" + str(draft.get("matrix_platform") or "threads"), {"draft": draft})
    draft["matrix_count"] = count
    display_count = "每人全部" if count == "all" else f"每人 {count} 篇"
    source_label = "待發布推文" if source == "posts" else "收藏推文"
    total = sum(len(_matrix_real_posts(persona.id)[3]) if count == "all" else int(count) for persona in personas)
    text = "\n".join(["🚀 矩陣發布確認", "", f"人設：{len(personas)} 個", f"來源：{source_label}", f"平台：{draft.get('matrix_platform') or 'threads'}", f"篇數：{display_count}", f"預計任務：{total} 條", "", "確認後會加入發布隊列。"])
    return _response(_message(text, _rows([_btn("🚀 開始矩陣發布", "mxrun", "primary")], [_btn("◀️ 返回篇數", "mxb_confirm")], [_btn("👤 重新選人設", "matrix_start")])), state={"flow": "matrix_confirm", "draft": draft})


def _matrix_run(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    personas = _matrix_selected_personas(draft)
    source = str(draft.get("matrix_source") or "posts")
    if source != "posts":
        return _response(_message("目前只能从真实待发布推文执行矩阵发布。", [[_btn("◀️ 返回来源", "mxb_source")]]), state={"flow": "matrix_source", "draft": draft})
    count_value = draft.get("matrix_count") or 1
    submitted = 0
    missing: list[str] = []
    for persona in personas:
        archive_id, local, _row, posts = _matrix_real_posts(persona.id)
        pad_code = str(local.pad_code or "").strip() if local else ""
        if not archive_id or not posts or not pad_code:
            missing.append(_local_persona_display_name(persona))
            continue
        limit = len(posts) if count_value == "all" else min(int(count_value), len(posts))
        for post in posts[:limit]:
            post_id = str(post.get("id") or "").strip()
            if not post_id:
                continue
            params = {
                "archiveId": archive_id,
                "postId": post_id,
                "padCode": pad_code,
                "platform": str(draft.get("matrix_platform") or "threads"),
                "dryRun": False,
            }
            job = SourceWorkflowJobRepo.create("persona_publish_post", f"矩陣發布：{persona.name}", params, status="submitting")
            _submit_source_task_job_async(job.id, "persona_publish_post", params)
            submitted += 1
    if not submitted:
        return _response(_message("❌ 沒有可建立的矩陣發布任務，請先生成或收藏推文。", [[_btn("◀️ 返回來源", "mxb_source")]]), state={"flow": "matrix_source", "draft": draft})
    lines = ["✅ 已提交矩陣發布任務", "", f"來源真實發布任務：{submitted}", f"人設：{len(personas)} 個"]
    if missing:
        lines.extend(["", "以下人設沒有可發布推文：", *[f"• {name}" for name in missing[:6]]])
    return _response(_message("\n".join(lines), _rows([_btn("📊 來源任務", "source_tasks"), _btn("📊 排程狀態", "menu_status")], [_btn("返回主選單", "menu")])), state={"flow": ""})


def _schedule_publish_start() -> dict[str, Any]:
    rows = [(persona, row) for persona, row in _matrix_persona_rows() if _matrix_real_posts(persona.id)[3]]
    if not rows:
        return _response(_message("目前沒有可定時發布的人設，請先建立並生成推文。", [[_btn("➕ 建立人設", "create_persona_entry")], [_btn("◀️ 返回主選單", "menu")]]))
    lines = ["⏰ 定時發布", "", "請選擇要定時發布的人設："]
    keyboard = _chunk_buttons([_btn(_persona_action_label(row)[:18], f"sched_persona_{persona.id}") for persona, row in rows], 1)
    keyboard.append([_btn("◀️ 返回主選單", "menu")])
    return _response(_message("\n".join(lines), keyboard), state={"flow": "schedule_select_persona", "draft": {}})


def _schedule_persona_posts(action: str) -> dict[str, Any]:
    persona_id = action[len("sched_persona_") :]
    _archive_id, persona, _row, posts = _matrix_real_posts(persona_id)
    if not persona or not posts:
        return _response(_message("這個人設目前沒有待發布推文，請先生成推文。", [[_btn("◀️ 返回", "schedule_publish")]]))
    lines = ["⏰ 定時發布", "", f"已選擇人設：{_local_persona_display_name(persona)}", "", "請選擇要定時發布的推文："]
    keyboard = []
    for index, post in enumerate(posts[:8]):
        lines.extend(["", f"{index + 1}. {_memory_excerpt(post.get('content'), 80)}"])
        keyboard.append([_btn(f"第 {index + 1} 篇", f"sched_post_{persona_id}_{index}")])
    keyboard.append([_btn("◀️ 返回", "schedule_publish")])
    return _response(_message("\n".join(lines), keyboard), state={"flow": "schedule_select_post", "draft": {"persona_id": persona_id}})


def _schedule_platform(action: str) -> dict[str, Any]:
    payload = action[len("sched_post_") :] if action.startswith("sched_post_") else ""
    if "_" not in payload:
        return _schedule_publish_start()
    persona_id, index_text = payload.rsplit("_", 1)
    if not index_text.isdigit():
        return _schedule_publish_start()
    archive_id, persona, _row, posts = _matrix_real_posts(persona_id)
    index = int(index_text)
    if not persona or not (0 <= index < len(posts)):
        return _response(_message("沒有找到要定時發布的推文。", [[_btn("◀️ 返回", "schedule_publish")]]))
    post = posts[index]
    draft = {"persona_id": persona_id, "archive_id": archive_id, "post_id": str(post.get("id") or ""), "post_index": index, "post_text": str(post.get("content") or ""), "pad_code": persona.pad_code, "platform": "threads"}
    text = "\n".join(["⏰ 定時發布", "", f"人設：{_local_persona_display_name(persona)}", f"推文：{_memory_excerpt(post.get('content'), 100)}", "", "請選擇發布平台："])
    return _response(_message(text, _rows([_btn("Threads", "sched_platform_threads")], [_btn("◀️ 返回", f"sched_persona_{persona_id}")])) , state={"flow": "schedule_platform", "draft": draft})


def _schedule_time_picker(state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    now = datetime.now()
    options = [
        ("今天 21:00", now.replace(hour=21, minute=0, second=0, microsecond=0)),
        ("明天 09:00", (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)),
        ("明天 21:00", (now + timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)),
    ]
    rows = [[_btn(label, f"schedpick_ts_{int(value.timestamp())}")] for label, value in options if value.timestamp() > time.time() + 60]
    rows.append([_btn("◀️ 返回平台", "sched_back_platform")])
    text = "\n".join(["⏰ 定時發布", "", "請選擇發布時間，或直接輸入：", "例如：明天 09:00 / 2026-05-18 21:30"])
    return _response(_message(text, rows), state={"flow": "schedule_time", "draft": draft})


def _parse_schedule_time_input(text: str) -> float:
    raw = str(text or "").strip()
    now = datetime.now()
    if not raw:
        return 0
    if raw.startswith("今天"):
        date = now.date()
        hm = raw.replace("今天", "", 1).strip()
        raw = f"{date.isoformat()} {hm}"
    elif raw.startswith("明天"):
        date = (now + timedelta(days=1)).date()
        hm = raw.replace("明天", "", 1).strip()
        raw = f"{date.isoformat()} {hm}"
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%m-%d %H:%M", "%H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt == "%m-%d %H:%M":
                parsed = parsed.replace(year=now.year)
            elif fmt == "%H:%M":
                parsed = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
                if parsed.timestamp() <= time.time() + 60:
                    parsed += timedelta(days=1)
            return parsed.timestamp()
        except ValueError:
            continue
    return 0


def _schedule_submit_at(timestamp: float, state: dict[str, Any]) -> dict[str, Any]:
    draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    persona_id = str(draft.get("persona_id") or "")
    persona = PersonaRepo.get(persona_id)
    text = str(draft.get("post_text") or "").strip()
    if not persona or not text:
        return _response(_message("❌ 定時發布狀態已失效，請重新選擇。", [[_btn("◀️ 返回", "schedule_publish")]]), state={"flow": ""})
    if timestamp <= time.time() + 60:
        return _response(_message("❌ 沒識別到有效發布時間，請用例如「明天 09:00」或「2026-05-18 21:30」。", [[_btn("◀️ 返回", "schedule_publish")]]), state=state)
    archive_id = str(draft.get("archive_id") or "")
    post_id = str(draft.get("post_id") or "")
    pad_code = str(draft.get("pad_code") or persona.pad_code or "")
    if not archive_id or not post_id or not pad_code:
        return _response(_message("❌ 定時發布來源資料已失效，請重新選擇。", [[_btn("◀️ 返回", "schedule_publish")]]), state={"flow": ""})
    when = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    params = {
        "archiveId": archive_id,
        "postIds": [post_id],
        "padCode": pad_code,
        "platform": str(draft.get("platform") or "threads"),
        "scheduledAt": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
    }
    job = SourceWorkflowJobRepo.create("persona_enqueue_posts", f"定時發布：{persona.name} / {when}", params, status="submitting")
    _submit_source_task_job_async(job.id, "persona_enqueue_posts", params)
    return _response(_message(f"✅ 已提交 Tool R18 定時發布佇列\n\n人設：{_local_persona_display_name(persona)}\n平台：{draft.get('platform') or 'threads'}\n時間：{when}\n來源任務：提交中", _rows([_btn("📊 來源任務", "source_tasks"), _btn("📊 排程狀態", "menu_status")], [_btn("◀️ 返回主選單", "menu")])) , state={"flow": ""})


def _route_text(message: str) -> str:
    raw = message.strip()
    text = raw.lower()
    direct = {
        "/start": "menu",
        "start": "menu",
        "menu": "menu",
        "/status": "source_status",
        "status": "source_status",
        "查看工作台状态": "source_status",
        "查看工作臺狀態": "source_status",
        "来源任务状态": "source_status",
        "來源任務狀態": "source_status",
        "/workflow": "source_runtime_config",
        "workflow": "source_runtime_config",
        "查看后台工作流配置": "source_runtime_config",
        "查看後台工作流配置": "source_runtime_config",
        "查看後臺工作流配置": "source_runtime_config",
        "/workbench": "source_workbench",
        "workbench": "source_workbench",
        "工作台网址": "source_workbench",
        "工作臺網址": "source_workbench",
        "工作台網址": "source_workbench",
        "/stop": "stop",
        "强制停止当前任务": "stop",
        "強制停止目前任務": "stop",
        "强制停止目前任务": "stop",
        "重跑最近任务": "source_rerun_latest",
        "重跑最近任務": "source_rerun_latest",
        "多智能体数字人": "multi_agent_digital_human",
        "多智能體數字人": "multi_agent_digital_human",
        "生成口播音频": "create_audio",
        "生成口播音頻": "create_audio",
    }
    if raw in direct:
        return direct[raw]
    source_action = SOURCE_ACTION_ALIASES.get(raw)
    if source_action:
        return source_action
    mapping = {
        "我的人设": "list_personas",
        "我的人設": "list_personas",
        "人设管理": "list_personas",
        "人設管理": "list_personas",
        "账号管理": "accounts_console",
        "帳號管理": "accounts_console",
        "帐号管理": "accounts_console",
        "賬號管理": "accounts_console",
        "手机管理": "pad_mgmt",
        "手機管理": "pad_mgmt",
        "云机管理": "pad_mgmt",
        "雲機管理": "pad_mgmt",
        "智能体手机管理": "pad_mgmt",
        "智能體手機管理": "pad_mgmt",
        "热点数据": "dashboard",
        "熱點數據": "dashboard",
        "排程状态": "status",
        "排程狀態": "status",
        "定时任务": "schedule",
        "定時任務": "schedule",
        "矩阵发布": "matrix_start",
        "矩陣發布": "matrix_start",
        "图像编辑": "image_menu",
        "圖像編輯": "image_menu",
        "图像生成": "image_menu",
        "圖像生成": "image_menu",
        "图片生成": "image_menu",
        "圖片生成": "image_menu",
        "视频": "video_menu",
        "視頻": "video_menu",
    }
    for key, action in mapping.items():
        if key.lower() in text:
            return action
    if "persona" in text:
        return "list_personas"
    if "device" in text or "pad" in text:
        return "pad_mgmt"
    if "dashboard" in text:
        return "dashboard"
    return ""


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip()
    message = str(payload.get("message") or "").strip()
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    media = _sentiment_hot_input_media(payload.get("media"))

    if action.startswith("open:"):
        return _open(action.split(":", 1)[1], state)
    if not action and state.get("flow") == "sentiment_hot_edit_input" and (message or media):
        return _continue_sentiment_hot_edit(message, media, state)
    if not action and state.get("flow") and message:
        return _continue_state_text(message, state)
    if not action and message:
        account_command = _account_command_response(message)
        if account_command is not None:
            return account_command
        action = _route_text(message)
        if not action:
            return _source_submit_agent_action("智能体对话", message)
    if not action or action in {"menu", "back_main"}:
        return _main_menu()

    if action == "capabilities":
        return _capabilities_menu()
    if action == "post_library_global":
        return _post_library("")
    if action.startswith("post_library:"):
        return _post_library(action.split(":", 1)[1])
    if action.startswith("restore_history:"):
        return _restore_history_menu(action)
    if action.startswith("restore_task:"):
        return _restore_task(action)
    if action.startswith("r18:"):
        return _r18_menu(action.split(":", 1)[1])
    if action.startswith("r18_gen:"):
        return _r18_generate(action)
    if action in {"automation", "automation_global"}:
        return _automation_menu("")
    if action.startswith("automation:"):
        return _automation_menu(action.split(":", 1)[1])
    if action.startswith("automation_fixed:"):
        return _automation_fixed_prompt(action)
    if action.startswith("automation_run:"):
        return _automation_run(action)
    if action == "local_jobs":
        return _local_jobs_menu()
    if action == "operator_console":
        return _operator_console_menu()
    if action.startswith("tg_credentials_set_") or action.startswith("tg_credentials_set:"):
        return _tg_credentials_prompt(action[len("tg_credentials_set_") :] if action.startswith("tg_credentials_set_") else action.split(":", 1)[1])
    if action.startswith("tg_credentials_clear_") or action.startswith("tg_credentials_clear:"):
        return _clear_tg_credentials(action[len("tg_credentials_clear_") :] if action.startswith("tg_credentials_clear_") else action.split(":", 1)[1])
    if action.startswith("tg_login_check:"):
        return _telegram_login_check(action.split(":", 1)[1])

    if action in {"list_personas", "personas"}:
        return _personas_menu()
    if action.startswith("list_personas_p"):
        return _personas_menu(_num(action[len("list_personas_p") :]))
    if action.startswith("list_personas:"):
        return _personas_menu(_num(action.split(":", 1)[1]))
    if action.startswith("personas_page:"):
        return _personas_menu(_num(action.split(":", 1)[1]))
    if action.startswith("personas_bound:"):
        return _personas_menu(_num(action.split(":", 1)[1]))
    if action.startswith("personas_unbound:"):
        return _personas_menu(_num(action.split(":", 1)[1]))
    if action.startswith("pd_"):
        return _persona_detail(action.split("_", 1)[1])
    if action.startswith("pd:"):
        return _persona_detail(action.split(":", 1)[1])
    if action.startswith("settings_"):
        return _persona_settings(action.split("_", 1)[1])
    if action.startswith("settings:"):
        return _persona_settings(action.split(":", 1)[1])
    if action.startswith("posts_branch_"):
        return _persona_content_type_picker(action[len("posts_branch_") :], "posts")
    if action.startswith("posts_"):
        rest = action[len("posts_") :]
        page = 0
        content_type = ""
        if "_p" in rest:
            pid, page_text = rest.rsplit("_p", 1)
            page = _num(page_text)
        else:
            pid = rest
        if "_ct_" in pid:
            pid, content_type = pid.split("_ct_", 1)
        _local, source_row = _resolve_persona_for_action(pid)
        if not _source_pending_posts(source_row, content_type) and not _is_workflow_persona_row(source_row, pid):
            restored = _stored_generated_posts_response(pid, page, state)
            if restored is not None:
                return restored
        return _publish_posts_list(f"pub_posts:{page}:{pid}", content_type)
    if action.startswith("history_branch_"):
        return _persona_content_type_picker(action[len("history_branch_") :], "history")
    if action.startswith("history_"):
        rest = action[len("history_") :]
        page = 0
        if "_p" in rest:
            pid, page_text = rest.rsplit("_p", 1)
            page = _num(page_text)
        else:
            pid = rest
        content_type = ""
        if "_ct_" in pid:
            pid, content_type = pid.split("_ct_", 1)
        return _publish_history(f"pub_history:{page}:{pid}", content_type)
    if action.startswith("pub_branch_"):
        return _persona_content_type_picker(action[len("pub_branch_") :], "publish")
    if action.startswith("pub_direct:"):
        return _publish_direct_start(action.split(":", 1)[1])
    if action.startswith("pub_posts:"):
        return _publish_posts_list(action)
    if action.startswith("pub_history:"):
        return _publish_history(action)
    if action.startswith("pub_"):
        pid = action[len("pub_") :]
        content_type = ""
        if "_ct_" in pid:
            pid, content_type = pid.split("_ct_", 1)
        return _publish_center(pid, content_type)
    if action.startswith("genpost_branch_"):
        return _start_generate_posts(action[len("genpost_branch_") :])
    if action.startswith("genpost_nonr18_"):
        return _genpost_mode_picker(action[len("genpost_nonr18_") :], "nonr18")
    if action.startswith("genpost_r18_"):
        return _genpost_mode_picker(action[len("genpost_r18_") :], "r18")
    if action.startswith("gpm_"):
        rest = action[len("gpm_") :]
        try:
            persona_id, mode_token, branch_token = rest.rsplit("_", 2)
        except ValueError:
            return _response(_message("新建推文模式入口無效，請返回重新選擇。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
        persona_id, local, row, name = _genpost_context(persona_id)
        source_archive_id = _tool_r18_archive_id(persona_id, local, row)
        draft = {
            "persona_id": persona_id,
            "source_archive_id": source_archive_id,
            "name": name,
            "content_branch": _genpost_branch_from_token(branch_token),
            "text_only": mode_token == "t",
            "memory": "",
        }
        return _genpost_memory_selection(draft)
    if action.startswith("genpost_custom_"):
        rest = action[len("genpost_custom_") :]
        persona_id = rest.split("_ct_", 1)[0] if "_ct_" in rest else rest
        return _publish_direct_start(persona_id)
    if action.startswith("gph_"):
        rest = action[len("gph_") :]
        try:
            branch_token, persona_id = rest.split("_", 1)
        except ValueError:
            branch_token, persona_id = "x", rest
        content_branch = _genpost_branch_from_token(branch_token)
        return _sentiment_hot_fetch_start(persona_id, content_branch, refresh=True)
    if action.startswith("genmem_"):
        return _genmem_action(action, state)
    if action == "genpost_ratio_back" or action.startswith("genpost_ratio_"):
        return _genpost_apply_ratio(action, state)
    if action.startswith("shsel_") or action.startswith("shselall_") or action.startswith("shselclear_"):
        return _sentiment_hot_select_action(action, state)
    if action.startswith("shdet_"):
        return _sentiment_hot_detail(action, state)
    if action.startswith("shuse_") or action.startswith("shsave_"):
        return _sentiment_hot_import(action, state)
    if action.startswith("shlist_"):
        key = action[len("shlist_") :]
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        if not _sentiment_hot_key_matches(draft, key):
            return _sentiment_hot_expired(draft)
        return _sentiment_hot_list_from_state(state)
    if action.startswith("shrf_"):
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        key = action[len("shrf_") :]
        if not _sentiment_hot_key_matches(draft, key):
            return _sentiment_hot_expired(draft)
        persona_id = str(draft.get("persona_id") or "")
        return _sentiment_hot_fetch_start(persona_id, str(draft.get("content_branch") or ""), refresh=True)
    if action.startswith("shedit_"):
        return _sentiment_hot_media_edit(action, state)
    if action.startswith("shmedia_"):
        return _sentiment_hot_media_edit(action, state)
    if action == "paidr18_back_memory":
        return _genpost_memory_selection(dict(state.get("draft") if isinstance(state.get("draft"), dict) else {}))
    if action in {"paidr18_group_image", "paidr18_group_video"}:
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        draft["content_time_slot"] = "付費圖片內容" if action == "paidr18_group_image" else "付費圖生視頻"
        return _genpost_tg_count_prompt(draft)
    if action == "genpost_count_back":
        return _genpost_memory_selection(dict(state.get("draft") if isinstance(state.get("draft"), dict) else {}))
    if action == "genpost_prompt_skip":
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        if not str(draft.get("persona_id") or "").strip():
            return _response(
                _message("新建推文状态已失效，请返回人设重新开始。", [[_btn("◀️ 返回人设列表", "list_personas")]]),
                state={"flow": ""},
            )
        draft["prompt"] = ""
        return _genpost_tg_words_prompt(draft)
    if action == "genpost_prompt_back":
        return _genpost_tg_prompt_input(dict(state.get("draft") if isinstance(state.get("draft"), dict) else {}))
    if action.startswith("genpost_words_"):
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        if not str(draft.get("persona_id") or "").strip():
            return _response(
                _message("新建推文状态已失效，请返回人设重新开始。", [[_btn("◀️ 返回人设列表", "list_personas")]]),
                state={"flow": ""},
            )
        return _continue_generate_posts(action[len("genpost_words_") :], {"flow": "genpost_words", "draft": draft})
    if action == "gpnoref_continue":
        return _continue_no_reference_generate(state)
    if action.startswith("vp_"):
        source_view = _source_post_view_from_state(_num(action[len("vp_") :]), state)
        if source_view is not None:
            return source_view
        return _post_view_action(f"post_view:{_num(action[len('vp_'):])}", state)
    if action == "pa_back":
        return _response(_post_select_message(state.get("draft", {})), state={"flow": "post_select", "draft": state.get("draft", {})})
    if action.startswith("pa_v_") and _web_post_action_from_state(action, state):
        return _source_post_detail_from_state(state)
    if action.startswith("pa_mp_") and _web_post_action_from_state(action, state):
        return _source_post_media_preview(state)
    if action.startswith("pa_mm_") and _web_post_action_from_state(action, state):
        return _source_post_media_manager(state)
    if action.startswith("pa_ed_") and _web_post_action_from_state(action, state):
        return _source_post_regenerate_menu(state, edit_mode=True)
    if action.startswith("pa_rg_") and _web_post_action_from_state(action, state):
        return _source_post_regenerate_menu(state)
    if action.startswith("pa_rai_") and _web_post_action_from_state(action, state):
        draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
        if draft.get("is_sentiment_post"):
            return _source_post_regenerate_menu(state)
        return _source_post_action_submit(state, "regenerate_content", label="AI 重新生成推文", extra={"rewriteMode": "persona_style"})
    if action.startswith("pa_ras_") and _web_post_action_from_state(action, state):
        return _source_post_action_submit(state, "regenerate_content", label="按原帖結構重寫推文", extra={"rewriteMode": "source_structure"})
    if action.startswith("pa_rap_") and _web_post_action_from_state(action, state):
        return _source_post_action_submit(state, "regenerate_content", label="按當前人設重寫推文", extra={"rewriteMode": "persona_style"})
    if action.startswith("pa_rc_") and _web_post_action_from_state(action, state):
        draft, archive_id, post_id, source, content_type, page = _source_post_action_context(state)
        return _response(
            _message("✍️ 自訂文案\n\n請直接輸入要保存到這篇推文的新文案。", [[_btn("◀️ 返回文案管理", f"pa_ed_{draft.get('post_action_key')}")]]),
            state={"flow": "source_post_custom_content", "draft": draft},
        )
    if action.startswith("pa_rf_") and _web_post_action_from_state(action, state):
        return _source_post_action_submit(state, "refresh_metrics", label="刷新推文熱度")
    if action.startswith("pa_fav_") and _web_post_action_from_state(action, state):
        return _source_post_action_submit(state, "favorite", label="收藏推文")
    if action.startswith("pa_del_") and _web_post_action_from_state(action, state):
        if action.endswith("_confirm"):
            return _source_post_action_submit(state, "delete", label="刪除推文")
        return _source_post_delete_confirm(state)
    if action.startswith("pa_mt_") and _web_post_action_from_state(action, state):
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        index = _num(action.rsplit("_", 1)[1])
        selected = {int(item) for item in draft.get("selected_media_indexes", []) if str(item).isdigit()}
        selected.remove(index) if index in selected else selected.add(index)
        draft["selected_media_indexes"] = sorted(selected)
        return _source_post_media_manager({"flow": "source_post_media_manage", "draft": draft})
    if action.startswith("pa_msa_") and _web_post_action_from_state(action, state):
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        draft["selected_media_indexes"] = list(range(len(draft.get("media_urls", []))))
        return _source_post_media_manager({"flow": "source_post_media_manage", "draft": draft})
    if action.startswith("pa_mcl_") and _web_post_action_from_state(action, state):
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        draft["selected_media_indexes"] = []
        return _source_post_media_manager({"flow": "source_post_media_manage", "draft": draft})
    if action.startswith("pa_md_") and _web_post_action_from_state(action, state):
        draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
        indexes = [int(item) for item in draft.get("selected_media_indexes", []) if str(item).isdigit()]
        if not indexes:
            return _response([_message("請先選擇要刪除的媒體。", kind="status"), *_source_post_media_manager(state).get("messages", [])], state=state)
        return _source_post_action_submit(state, "delete_media", label="刪除推文媒體", extra={"selectedIndexes": indexes})
    if action.startswith("pa_mrs_") and _web_post_action_from_state(action, state):
        return _source_post_media_replace_menu(state)
    if action.startswith("pa_mru_") and _web_post_action_from_state(action, state):
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        return _response(_message("📤 手動上傳替換\n\n請貼上要用來替換所選媒體的圖片或視頻 URL。", [[_btn("◀️ 返回替換方式", f"pa_mrs_{draft.get('post_action_key')}")]]), state={"flow": "source_post_replace_media_url", "draft": draft})
    if action.startswith("pa_mra_") and _web_post_action_from_state(action, state):
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        return _source_post_image_ratio_picker(str(draft.get("archive_id") or ""), str(draft.get("post_id") or ""), _num(draft.get("post_index")), draft)
    if action.startswith("pa_pp_"):
        resolved = _web_post_action_from_state(action, state)
        if resolved:
            platform = action.rsplit("_", 1)[1]
            if platform == "clear":
                return _source_post_publish_start(f"source_post_publish:{resolved[0]}:{resolved[1]}", resolved[2], state.get("draft") if isinstance(state.get("draft"), dict) else {})
            return _source_post_publish_platform(f"source_post_platform:{platform}", state)
        if _is_source_post_action_state(state):
            return _expired_source_post_action()
        _kind, _action_key, platform = _parse_tg_post_action(action)
        return _publish_platform(f"publish_platform:{platform}", state)
    if action.startswith("pa_dopimg_"):
        if _is_source_post_action_state(state):
            return _expired_source_post_action()
        return _enqueue_selected_posts(state, with_image=True)
    if action.startswith("pa_dopm_"):
        if _web_post_action_from_state(action, state):
            return _source_post_pad_menu(state)
        if _is_source_post_action_state(state):
            return _expired_source_post_action()
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        if draft.get("persona_id") and not draft.get("selected_personas"):
            draft["selected_personas"] = [str(draft.get("persona_id"))]
        return _matrix_pads_menu({"draft": draft})
    if action.startswith("pa_dop_"):
        if _web_post_action_from_state(action, state):
            return _source_post_publish_execute(state)
        if _is_source_post_action_state(state):
            return _expired_source_post_action()
        return _enqueue_selected_posts(state, with_image=False)
    if action.startswith("pa_pub_"):
        resolved = _web_post_action_from_state(action, state)
        if resolved:
            return _source_post_publish_start(f"source_post_publish:{resolved[0]}:{resolved[1]}", resolved[2], state.get("draft") if isinstance(state.get("draft"), dict) else {})
        if _is_source_post_action_state(state):
            return _expired_source_post_action()
        _kind, action_key, _value = _parse_tg_post_action(action)
        return _post_publish_one(f"post_publish_one:{_tg_post_action_index(action_key)}", state)
    if action.startswith("pa_") and _is_source_post_action_state(state):
        return _expired_source_post_action()
    if action.startswith("pa_img_"):
        _kind, action_key, _value = _parse_tg_post_action(action)
        return _generate_single_post_image(f"genpost_image:{_tg_post_action_index(action_key)}", state)
    if action.startswith("pa_media_"):
        _kind, action_key, _value = _parse_tg_post_action(action)
        draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
        return _response(_post_candidate_message(draft, _tg_post_action_index(action_key)), state={"flow": "post_select", "draft": draft})
    if action.startswith("pa_edit_"):
        _kind, action_key, _value = _parse_tg_post_action(action)
        return _post_edit_prompt(f"post_edit:{_tg_post_action_index(action_key)}", state)
    if action.startswith("pa_fav_"):
        _kind, action_key, _value = _parse_tg_post_action(action)
        return _post_favorite(f"post_favorite:{_tg_post_action_index(action_key)}", state)
    if action.startswith("pa_del_"):
        _kind, action_key, _value = _parse_tg_post_action(action)
        return _post_delete(f"post_delete:{_tg_post_action_index(action_key)}", state)
    if action.startswith("sbtog_") or action in {"sbsel_page", "sbclear_page"}:
        return _source_bulk_toggle(action, state)
    if action == "sbconfirm":
        return _source_bulk_confirm(state)
    if action == "sbback":
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        return _source_bulk_render(draft)
    if action == "sbdelete_confirm":
        return _source_bulk_delete_execute(state)
    if action.startswith("sbplatform_"):
        return _source_bulk_publish_platform(action, state)
    if action == "sbpublish_confirm":
        return _source_bulk_publish_execute(state)
    if action.startswith("sppad:") or action in {"sppad_all", "sppad_clear"}:
        return _source_post_pad_action(action, state)
    if action == "sppad_confirm":
        return _source_post_multi_publish_execute(state)
    if action.startswith("bulkpub_"):
        return _source_bulk_start(action, "publish")
    if action.startswith("bulkdel_"):
        return _source_bulk_start(action, "delete")
    if action.startswith("favs_"):
        rest = action[len("favs_") :]
        if "_p" in rest:
            persona_id, page_text = rest.rsplit("_p", 1)
            page = _num(page_text)
        else:
            persona_id, page = rest, 0
        return _publish_posts_list(f"pub_posts:{page}:{persona_id}", source="favorites")
    if action.startswith("persona_autoreply_original_"):
        return _automation_run(f"automation_run:auto_reply_comments:ai:{action[len('persona_autoreply_original_'):]}")
    if action.startswith("persona_autoreply_hot_"):
        return _own_reply_mode_menu(action[len("persona_autoreply_hot_") :])
    if action.startswith("ownreply_mode_manual_") or action.startswith("ownreply_mode_ai_"):
        return _own_reply_mode_start(action)
    if action.startswith("ownreply_text_"):
        pid = action[len("ownreply_text_") :]
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        draft.update({"persona_id": pid, "reply_mode": "manual"})
        return _response(_message("請重新輸入要回覆的內容。", [[_btn("◀️ 返回自動回覆", f"persona_autoreply_{pid}")]]), state={"flow": "ownreply_reply_text", "draft": draft})
    if action.startswith("ownreply_views_"):
        pid = action[len("ownreply_views_") :]
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        draft["persona_id"] = pid
        return _own_reply_views_prompt(draft)
    if action.startswith("ownreply_days_"):
        pid = action[len("ownreply_days_") :]
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        draft["persona_id"] = pid
        return _own_reply_days_prompt(draft)
    if action == "ownreply_run":
        return _own_reply_submit(state)
    if action.startswith("acctautoreply_"):
        return _persona_autoreply_mode_menu(action[len("acctautoreply_") :])
    if action.startswith("persona_autoreply_"):
        return _persona_autoreply_menu(action[len("persona_autoreply_") :])
    if action.startswith("warmup_engage_threads_"):
        return _warmup_engage_threads(action)
    if action.startswith("warmup_run_threads_"):
        return _warmup_run_threads(action, state)
    if action.startswith("persona_warmup_"):
        return _persona_warmup_platform_menu(action[len("persona_warmup_") :])
    if action in {"create_persona_entry", "persona_create_start"}:
        return _start_create_persona()
    if action.startswith("cpk_"):
        return _create_persona_keyword_action(action, state)
    if action == "sync_personas":
        return _sync_personas()
    if action == "accounts_console":
        return _accounts_console_menu()
    if action.startswith("accounts_page:"):
        return _accounts_console_menu(_num(action.split(":", 1)[1]))
    if action == "devices_console":
        return _devices_console_menu()
    if action.startswith("acctdetail:"):
        return _account_detail(action.split(":", 1)[1])
    if action.startswith("acctassign:"):
        return _account_assign(action)
    if action in {"acct_create_start", "account_create_start"}:
        return _account_create_start()
    if action == "acct_from_devices":
        return _accounts_create_from_devices()
    if action == "import_vmos":
        return _import_vmos_action()
    if action.startswith("acctdelete_confirm:"):
        return _account_delete_confirm(action.split(":", 1)[1])
    if action.startswith("acctdelete:"):
        return _account_delete(action.split(":", 1)[1])
    if action.startswith("editname_") or action.startswith("editname:"):
        pid = action.split("_", 1)[1] if action.startswith("editname_") else action.split(":", 1)[1]
        return _persona_settings_flow(pid, "edit_persona_name", "⭐ 请输入新的人设名称 ⭐")
    if action.startswith("editcontent_patch_") or action.startswith("editcontent_patch:"):
        pid = action.split("_", 2)[2] if action.startswith("editcontent_patch_") else action.split(":", 1)[1]
        return _persona_settings_flow(pid, "edit_persona_desc", "⭐ 请输入新的人设简介 ⭐\n　　你发送什么文字，我就直接覆盖保存，不会在原简介基础上改写。")
    if action.startswith("editcontent_regen_") or action.startswith("editcontent_regen:"):
        pid = action.split("_", 2)[2] if action.startswith("editcontent_regen_") else action.split(":", 1)[1]
        return _persona_settings_flow(pid, "edit_persona_desc_regen", "⭐ 请输入重新生成简介的方向 ⭐\n　　我会重新生成完整人设简介。")
    if action.startswith("editcontent_") or action.startswith("editcontent:"):
        pid = action.split("_", 1)[1] if action.startswith("editcontent_") else action.split(":", 1)[1]
        return _persona_content_edit_menu(pid)
    if action.startswith("tweetstyle_") or action.startswith("tweetstyle:"):
        pid = action.split("_", 1)[1] if action.startswith("tweetstyle_") else action.split(":", 1)[1]
        return _persona_settings_flow(pid, "edit_persona_style", "🧾 推文風格\n\n請直接發送一篇案例推文正文，我會保存為這個人設後續生成推文時的語氣、格式與行文邏輯。")
    if action.startswith("linksettings_") or action.startswith("linksettings:"):
        pid = action.split("_", 1)[1] if action.startswith("linksettings_") else action.split(":", 1)[1]
        return _link_ending_menu(pid)
    if action.startswith("linkpreset_add_") or action.startswith("linkpreset_add:"):
        return _link_ending_add_prompt(action.split("_", 2)[2] if action.startswith("linkpreset_add_") else action.split(":", 1)[1])
    if action.startswith("linkpreset_off_") or action.startswith("linkpreset_off:"):
        pid = action.split("_", 2)[2] if action.startswith("linkpreset_off_") else action.split(":", 1)[1]
        settings = _link_ending_settings(pid)
        presets = [{**preset, "enabled": False} for preset in settings.get("linkEndingPresets", [])]
        _save_link_ending_settings(pid, {"linkEndingPresets": presets, "activeLinkEndingPresetId": ""}, "链接设置停用")
        return _link_ending_menu(pid)
    if action.startswith(("lpe_", "lpn_", "lpc_", "lpu_", "lpd_", "lpe:", "lpn:", "lpc:", "lpu:", "lpd:")):
        if ":" in action:
            parts = action.split(":")
            prefix = parts[0]
            pid = parts[1] if len(parts) > 1 else ""
            index = _num(parts[2]) if len(parts) > 2 else -1
        else:
            prefix, rest = action.split("_", 1)
            split_index = rest.rfind("_")
            pid = rest[:split_index] if split_index > 0 else ""
            index = _num(rest[split_index + 1 :]) if split_index > 0 else -1
        settings = _link_ending_settings(pid)
        presets = list(settings.get("linkEndingPresets", []))
        if index < 0 or index >= len(presets):
            return _link_ending_menu(pid)
        if prefix == "lpe":
            return _link_ending_edit_menu(pid, index)
        if prefix == "lpn":
            return _link_ending_input_prompt(pid, index, "name")
        if prefix == "lpc":
            return _link_ending_input_prompt(pid, index, "content")
        if prefix == "lpu":
            presets = [{**preset, "enabled": i == index} for i, preset in enumerate(presets)]
            _save_link_ending_settings(pid, {"linkEndingPresets": presets, "activeLinkEndingPresetId": presets[index].get("id") or ""}, "链接设置启用")
            return _link_ending_menu(pid)
        if prefix == "lpd":
            target_id = presets[index].get("id")
            active_id = "" if settings.get("activeLinkEndingPresetId") == target_id else str(settings.get("activeLinkEndingPresetId") or "")
            presets = [preset for i, preset in enumerate(presets) if i != index]
            _save_link_ending_settings(pid, {"linkEndingPresets": presets, "activeLinkEndingPresetId": active_id}, "链接设置删除")
            return _link_ending_menu(pid)
    if action.startswith("bindtg_free_") or action.startswith("bindtg_free:"):
        pid = action.split("_", 2)[2] if action.startswith("bindtg_free_") else action.split(":", 1)[1]
        local = PersonaRepo.get(pid)
        if not local:
            return _response(_message("只能为本地人设绑定 TG 群。", [[_btn("◀️ 返回", f"settings_{pid}")]]))
        return _response(
            _message(f"请输入「{local.name}」的 TG 通用群组名称。", [[_btn("❌ 取消", f"settings_{pid}")]]),
            state={"flow": "bind_tg_group", "draft": {"persona_id": pid}},
        )
    if action.startswith("bindtg_paid_") or action.startswith("bindtg_paid:"):
        pid = action.split("_", 2)[2] if action.startswith("bindtg_paid_") else action.split(":", 1)[1]
        return _persona_settings_flow(pid, "bindtg_paid", "請輸入 TG 付費群名稱或群 ID。")
    if action.startswith("acctmgmt_") or action.startswith("acctmgmt:"):
        return _account_management(action.split("_", 1)[1] if action.startswith("acctmgmt_") else action.split(":", 1)[1])
    if action.startswith("persona_autoreply:"):
        return _persona_autoreply_menu(action.split(":", 1)[1])
    if action.startswith("persona_warmup:"):
        return _persona_warmup_platform_menu(action.split(":", 1)[1])
    if action.startswith("acctwarmup_threads_") or action.startswith("acctwarmup_threads:"):
        pid = action[len("acctwarmup_threads_") :] if action.startswith("acctwarmup_threads_") else action.split(":", 1)[1]
        return _threads_warmup_menu(pid)
    if action.startswith("warmrun_"):
        rest = action[len("warmrun_") :]
        if "_" in rest:
            mode, pid = rest.split("_", 1)
            return _automation_run(f"automation_run:warm:{mode}:{pid}")
    if action.startswith("acctplatform_threads_") or action.startswith("acctplatform_threads:"):
        pid = action[len("acctplatform_threads_") :] if action.startswith("acctplatform_threads_") else action.split(":", 1)[1]
        return _threads_account_panel(pid)
    if action.startswith("acctplatform_telegram_") or action.startswith("acctplatform_telegram:"):
        pid = action[len("acctplatform_telegram_") :] if action.startswith("acctplatform_telegram_") else action.split(":", 1)[1]
        return _telegram_account_panel(pid)
    if action.startswith("acctbind_telegram_") or action.startswith("acctbind_telegram:"):
        pid = action[len("acctbind_telegram_") :] if action.startswith("acctbind_telegram_") else action.split(":", 1)[1]
        return _tg_credentials_prompt(pid)
    if action.startswith("acctquery_threads_") or action.startswith("acctquery_threads:"):
        pid = action[len("acctquery_threads_") :] if action.startswith("acctquery_threads_") else action.split(":", 1)[1]
        return _threads_account_query_submit(persona_id=pid, back=f"acctplatform_threads_{pid}")
    if action.startswith("acctbindmenu_threads_") or action.startswith("acctbindmenu_threads:"):
        pid = action[len("acctbindmenu_threads_") :] if action.startswith("acctbindmenu_threads_") else action.split(":", 1)[1]
        return _threads_account_binding_menu(pid)
    if action.startswith("acctbind_") and not action.startswith(("acctbind_threads_", "acctbind_telegram_")):
        return _threads_account_binding_menu(action[len("acctbind_") :])
    if action.startswith("acctbind_threads_") or action.startswith("acctbind_threads:"):
        pid = action[len("acctbind_threads_") :] if action.startswith("acctbind_threads_") else action.split(":", 1)[1]
        return _response(
            _message("請直接發送這個人設要綁定的 Threads 使用者名稱。", [[_btn("✖️ 取消", f"acctbindmenu_threads_{pid}")]]),
            state={"flow": "acct_threads_handle", "draft": {"persona_id": pid}},
        )
    if action.startswith("acctclear_threads_") or action.startswith("acctclear_threads:"):
        pid = action[len("acctclear_threads_") :] if action.startswith("acctclear_threads_") else action.split(":", 1)[1]
        return _threads_account_clear(pid)
    if action.startswith("acctlogin_threads_") or action.startswith("acctlogin_threads:"):
        pid = action[len("acctlogin_threads_") :] if action.startswith("acctlogin_threads_") else action.split(":", 1)[1]
        return _threads_login_start(pid)
    if action.startswith("threads_profile_"):
        for operation in ("link", "bio", "name", "avatar"):
            prefix = f"threads_profile_{operation}_"
            if action.startswith(prefix):
                return _threads_profile_prompt(action[len(prefix) :], operation)
        operation = action.split(":", 1)[0].replace("threads_profile_", "")
        pid = action.split(":", 1)[1] if ":" in action else ""
        return _threads_profile_prompt(pid, operation)
    if action.startswith("bindpad_manual:"):
        pid = action.split(":", 1)[1]
        return _response(_message("⭐ 请手动输入 padCode ⭐", [[_btn("❌ 取消", f"pd:{pid}")]]), state={"flow": "bind_pad_manual", "draft": {"persona_id": pid}})
    if action.startswith("bindpad_") or action.startswith("bindpad:"):
        return _bind_pad(action.split("_", 1)[1] if action.startswith("bindpad_") else action.split(":", 1)[1])
    if action.startswith("selectpad:"):
        return _select_pad(action)
    if action.startswith("queryaccounts:"):
        pid = action.split(":", 1)[1]
        return _threads_account_query_submit(persona_id=pid, back=f"bindpad:{pid}")
    if action.startswith("shs_") or action.startswith("shs:"):
        return _hot_metrics_summary(action.split("_", 1)[1] if action.startswith("shs_") else action.split(":", 1)[1])
    if action.startswith("shr_") or action.startswith("shr:"):
        return _hot_metrics_summary(action.split("_", 1)[1] if action.startswith("shr_") else action.split(":", 1)[1], force=True)
    if action.startswith("shp:"):
        return _hot_metrics_posts(action)
    if action.startswith("viewimg_") or action.startswith("viewimg:"):
        return _view_persona_image(action.split("_", 1)[1] if action.startswith("viewimg_") else action.split(":", 1)[1])
    if action.startswith("genimg_") or action.startswith("genimg:"):
        pid = action.split("_", 1)[1] if action.startswith("genimg_") else action.split(":", 1)[1]
        result = _generate_persona_image_response(pid)
        if str(state.get("flow") or "") == "genpost_no_reference":
            messages = list(result.get("messages") or [])
            messages.append(
                _message(
                    "人設圖任務已建立；完成後請點擊下方按鈕繼續生成推文配圖。",
                    [[_btn("▶️ 繼續生成推文配圖", "gpnoref_continue")], [_btn("◀️ 返回人設詳情", f"pd_{pid}")]],
                    kind="status",
                )
            )
            return {**result, "messages": messages, "state": state}
        return result
    if action.startswith("regenimg_") or action.startswith("regenimg:"):
        return _generate_persona_image_response(action.split("_", 1)[1] if action.startswith("regenimg_") else action.split(":", 1)[1], regenerate=True)
    if action.startswith("uploadimg:"):
        return _replace_persona_image_start(action.split(":", 1)[1])
    if action.startswith("del_") or action.startswith("delete_persona_confirm:"):
        pid = action.split("_", 1)[1] if action.startswith("del_") else action.split(":", 1)[1]
        return _response(_message("确认删除这个人设？", [[_btn("确认删除", f"delete_persona:{pid}", "danger")], [_btn("取消", f"settings_{pid}")]]))
    if action.startswith("delete_persona:"):
        pid = action.split(":", 1)[1]
        PersonaRepo.delete(pid)
        return _response(_message("🗑 已删除人设", [[_btn("◀️ 返回", "list_personas")]]), state={"flow": ""})

    if action in {"pad_mgmt", "devices"}:
        return _devices_menu()
    if action == "pad_mgmt_refresh":
        return _devices_menu(force=True)
    if action.startswith("pad_detail:"):
        return _device_detail(action.split(":", 1)[1])
    if action.startswith("device_preview:"):
        return _device_preview(action.split(":", 1)[1])
    if action.startswith("pad_query_account:"):
        pad = action.split(":", 1)[1]
        return _threads_account_query_submit(pad_code=pad, back=f"pad_detail:{pad}")
    if action.startswith("pad_login_threads:") or action.startswith("pad_login_telegram:") or action.startswith("pad_threads_"):
        pad = action.split(":", 1)[1] if ":" in action else ""
        return _source_submit_agent_action(
            "智能体手机操作",
            f"请按来源 Telegram 流程处理 PAD_CODE {pad} 的账号登录、名称、简介或头像素材操作，并回报执行结果。",
        )

    if action in {"status", "menu_status"}:
        return _status_menu()
    if action in {"schedule", "schedule_publish"}:
        return _schedule_publish_start()
    if action.startswith("sched_persona_"):
        return _schedule_persona_posts(action)
    if action.startswith("sched_post_"):
        return _schedule_platform(action)
    if action == "sched_platform_threads":
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        draft["platform"] = "threads"
        return _schedule_time_picker({"draft": draft})
    if action == "sched_back_platform":
        draft = dict(state.get("draft") if isinstance(state.get("draft"), dict) else {})
        persona_id = str(draft.get("persona_id") or "")
        index = _num(draft.get("post_index"))
        return _schedule_platform(f"sched_post_{persona_id}_{index}") if persona_id else _schedule_publish_start()
    if action.startswith("schedpick_ts_"):
        return _schedule_submit_at(float(action[len("schedpick_ts_") :] or 0), state)
    if action == "dashboard":
        return _dashboard_menu()
    if action == "dashboard_refresh":
        return _dashboard_menu(force=True)
    if action in {"stop", "force_stop_current_task"}:
        return _source_cancel_latest()

    if action == "image_menu":
        return _image_menu()
    if action == "text_to_image":
        return _text_to_image_start()
    if action.startswith("t2i_"):
        return _text_to_image_action(action, state)
    if action == "source_t2i_confirm":
        draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
        prompt = str(draft.get("prompt") or "").strip()
        if not prompt:
            return _text_to_image_start()
        params = {
            "prompt": prompt,
            "prompt_text": prompt,
            "message": prompt,
            "tg_use_llm_prompt": True,
            "tg_user_instruction": f"User text-to-image request: {prompt}",
            "ratio": str(draft.get("ratio") or ""),
            "final_resolution": bool(draft.get("final_resolution")),
            "persona_id": str(draft.get("persona_id") or ""),
        }
        return _source_submit_from_draft({"key": "text_to_image", "params": params})
    if action in {"single_image_edit", "image_edit", "face_swap", "multi_image", "image_replace"}:
        return _source_workflow_start(action)
    if action in {"text_to_image_continue", "text_to_image_rerun"}:
        return _source_rerun_latest(
            {"text_to_image"},
            label="继续生成图片" if action == "text_to_image_continue" else "重新生成图片",
            transform="continue_text_to_image" if action == "text_to_image_continue" else "",
        )
    if action == "image_edit_continue":
        return _source_submit_agent_action("继续编辑结果图", "继续编辑结果图")
    if action == "image_edit_rerun":
        return _source_rerun_latest({"single_image_edit", "get_nano_banana"}, label="重新生成图片编辑")
    if action == "face_swap_upscale":
        return _source_submit_agent_action("增加解析度 2 倍", "增加解析度 2 倍")
    if action == "face_swap_rerun":
        return _source_rerun_latest({"face_swap"}, label="重新生成人物换脸")
    if action == "source_status":
        return _source_status_menu()
    if action == "source_tasks":
        return _source_tasks_menu()
    if action == "source_cancel_latest":
        return _source_cancel_latest()
    if action == "source_runtime_config":
        return _source_runtime_config_menu()
    if action == "source_workbench":
        return _source_workbench_open()
    if action == "source_step_skip":
        return _source_skip_step(state)
    if action.startswith("source_choice:"):
        return _source_choice(action, state)
    if action == "source_cancel":
        return _response(_message("已取消来源工作流填写。", [[_btn("返回图像菜单", "image_menu"), _btn("返回主选单", "menu")]]), state={"flow": ""})
    if action == "source_workflow_confirm":
        return _source_submit_from_draft(state.get("draft") if isinstance(state.get("draft"), dict) else {})
    if action.startswith("source_workflow_start:"):
        return _source_workflow_start(action.split(":", 1)[1])
    if action.startswith("source_task_start:"):
        return _source_workflow_start(action.split(":", 1)[1])
    if action.startswith("source_post:"):
        return _source_post_detail(action, context=state.get("draft") if isinstance(state.get("draft"), dict) else {})
    if action.startswith("pa_mp_"):
        resolved = _web_post_action_from_state(action, state)
        return _source_post_detail(f"source_post:{resolved[0]}:{resolved[1]}") if resolved else _response(_message("推文操作已過期，請重新打開推文。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    if action.startswith("pa_pub_"):
        resolved = _web_post_action_from_state(action, state)
        return _source_post_publish_start(f"source_post_publish:{resolved[0]}:{resolved[1]}", resolved[2]) if resolved else _response(_message("推文操作已過期，請重新打開推文。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    if action.startswith("pa_pp_"):
        resolved = _web_post_action_from_state(action, state)
        if not resolved:
            return _response(_message("發布操作已過期，請重新打開推文。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
        platform = action.rsplit("_", 1)[1]
        if platform == "clear":
            return _source_post_publish_start(f"source_post_publish:{resolved[0]}:{resolved[1]}", resolved[2])
        return _source_post_publish_platform(f"source_post_platform:{platform}", state)
    if action.startswith("pa_dop_"):
        resolved = _web_post_action_from_state(action, state)
        return _source_post_publish_execute(state) if resolved else _response(_message("發布操作已過期，請重新打開推文。", [[_btn("◀️ 返回人設列表", "list_personas")]]), state={"flow": ""})
    if action.startswith("post_img_regen_"):
        return _source_post_image_regen_entry(action, state)
    if action.startswith("source_post_image_retry:"):
        return _source_post_image_retry(action)
    if action.startswith("post_img_ratio_"):
        return _source_post_image_ratio_submit(action, state)
    if action.startswith("pimgpick:"):
        return _source_post_pick_candidate(action)
    if action.startswith("source_post_image:"):
        return _source_post_generate_image(action)
    if action.startswith("source_post_publish:"):
        return _source_post_publish_start(action, context=state.get("draft") if isinstance(state.get("draft"), dict) else {})
    if action.startswith("source_post_platform:"):
        return _source_post_publish_platform(action, state)
    if action == "source_post_execute":
        return _source_post_publish_execute(state)
    if action.startswith("source_task_detail:"):
        return _source_task_detail(action.split(":", 1)[1])
    if action.startswith("source_task_poll:"):
        return _source_task_poll(action.split(":", 1)[1])
    if action.startswith("source_genpost_image_start:"):
        return _source_generated_post_image_start(action.split(":", 1)[1])
    if action.startswith("source_genpost_image_next:"):
        return _source_generated_post_image_next(action.split(":", 1)[1])
    if action.startswith("source_rerun_task:"):
        return _source_rerun_task(action.split(":", 1)[1])
    if action == "source_rerun_latest":
        return _source_rerun_latest()
    if action == "rerun_latest":
        return _source_rerun_latest()
    if action == "video_i2v":
        return _source_workflow_start("video_i2v")
    if action == "video_edit":
        return _source_workflow_start("video_i2v")
    if action in {
        "digital_human",
        "digital_human_realistic",
        "digital_human_live",
        "digital_human_product",
        "digital_human_custom",
        "replace_model",
        "replace_product",
        "replace_union",
        "create_audio",
    }:
        return _source_workflow_start(action)
    if action == "multi_agent_digital_human":
        return _source_submit_agent_action("智能体生产入口", "请按智能体生产入口判断任务类型。")
    if action == "workflow_config":
        return _workflow_config()
    if action == "video_menu":
        return _video_menu()
    if action == "video_i2v":
        return _video_i2v_start()
    if action.startswith("v2v_") or action == "video_prompt_mode":
        return _video_action(action, state)
    if action.startswith("source_workflow_start:"):
        title = action.split(":", 1)[1]
        return _response(_message(f"✅ 已进入「{title}」参数填写。\n\n请继续在输入框发送需求或素材说明。", [[_btn("返回主选单", "menu")]]), state={"flow": "source_workflow", "draft": {"title": title}})

    if action.startswith("genpost:"):
        return _start_generate_posts(action.split(":", 1)[1])
    if action.startswith("genpost_memlist:"):
        return _genpost_memory_list(action, state)
    if action.startswith("genpost_favorites:"):
        return _genpost_memory_list(action, state, favorite_only=True)
    if action.startswith("genpost_usemem:"):
        return _genpost_use_memory(action, state)
    if action.startswith("genpost_hot:"):
        parts = action.split(":")
        return _genpost_branch_picker(parts[1] if len(parts) > 1 else "")
    if action.startswith("genpost_trending:"):
        parts = action.split(":")
        return _genpost_branch_picker(parts[1] if len(parts) > 1 else "")
    if action == "genpost_hot_manual":
        return _genpost_hot_manual_prompt(state)
    if action == "genpost_memory_manual":
        return _genpost_manual_prompt(state)
    if action.startswith("memfav:"):
        return _toggle_memory_favorite(action, state)
    if action == "draft_memory_favorite":
        return _favorite_current_draft(state)
    if action == "genpost_memory_skip":
        draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
        persona_id = str(draft.get("persona_id") or "")
        name = str(draft.get("name") or "人设")
        draft["memory"] = ""
        return _response(_genpost_count_prompt(persona_id, name, ""), state={"flow": "genpost_count", "draft": draft})
    if action.startswith("genpost_image:"):
        return _generate_single_post_image(action, state)
    if action.startswith("view_post_candidates:"):
        draft = state.get("draft") if isinstance(state.get("draft"), dict) else {}
        index = _num(action.split(":", 1)[1])
        return _response(_post_candidate_message(draft, index), state={"flow": "post_select", "draft": draft})
    if action.startswith("select_post_image:"):
        return _select_post_image(action, state)
    if action.startswith("post_page:"):
        return _post_page_action(action, state)
    if action.startswith("post_view:"):
        return _post_view_action(action, state)
    if action.startswith("post_publish_one:"):
        return _post_publish_one(action, state)
    if action.startswith("post_favorite:"):
        return _post_favorite(action, state)
    if action.startswith("post_delete:"):
        return _post_delete(action, state)
    if action.startswith("post_edit:"):
        return _post_edit_prompt(action, state)
    if action.startswith("post_refresh_hot:"):
        return _post_refresh_hot(action, state)
    if action.startswith("btog:") or action in {"bsel_page", "bclear_page", "bconfirm"}:
        return _post_select_action(action, state)
    if action == "regen_post_images":
        return _regenerate_post_images(state, next_group=False)
    if action == "next_post_image_group":
        return _regenerate_post_images(state, next_group=True)
    if action == "post_select_back":
        return _response(_post_select_message(state.get("draft", {})), state={"flow": "post_select", "draft": state.get("draft", {})})
    if action.startswith("publish_platform:"):
        return _publish_platform(action, state)
    if action in {"publish_now", "custom_publish_publish_now"}:
        return _enqueue_selected_posts(state, with_image=False)
    if action in {"publish_with_image", "custom_publish_confirm_pad_with_image"}:
        return _enqueue_selected_posts(state, with_image=True)
    if action == "custom_publish_multi_now":
        return _matrix_pads_menu(state)
    if action.startswith("pub:"):
        return _publish_center(action.split(":", 1)[1])
    if action == "matrix_start":
        return _matrix_start()
    if action.startswith("mxpg_") or action.startswith("mxpt_") or action in {"mxpsel", "mxpclr"}:
        return _matrix_toggle_persona(action, state)
    if action in {"mxpc", "mxb_source"}:
        return _matrix_source_menu(state)
    if action in {"mxsrc_posts", "mxsrc_favorites", "mxback_platform"}:
        return _matrix_platform_menu(action, state)
    if action.startswith("mxplat_"):
        return _matrix_count_menu(action, state)
    if action.startswith("mxc_") or action == "mxb_confirm":
        return _matrix_confirm(action, state)
    if action == "mxrun":
        return _matrix_run(state)
    if action in {"pubpad_select_page", "pubpad_clear_page", "pubpad_select_all", "pubpad_clear_all"} or action.startswith(("pubpad_toggle:", "pubpad_page:")):
        return _matrix_update_pads(action, state)
    if action == "pubpad_confirm":
        return _enqueue_matrix_posts(state)

    return _response(_message("这个操作已收到，请选择下一步。", _main_keyboard()), state={"flow": ""})
