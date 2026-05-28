from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import requests
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image

import create_audio
import create_video
import commerce_video_generator
import get_gemini
import image_model_api
import asset_uploader
import replace_model
import replace_product
import replace_productANDmodel
import runninghub_common
from .auth import SESSION_COOKIE, create_session, delete_session, get_current_user, hash_password, require_admin, verify_password
from .billing import compute_cost_cents
from .db import db, get_admin_config, init_db, set_admin_config


ROOT_DIR = Path(__file__).resolve().parent.parent
WEBAPP_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEBAPP_DIR / "static"
DATA_DIR = Path(os.getenv("WEBAPP_DATA_DIR", str(ROOT_DIR / "webapp_data"))).resolve()
UPLOAD_ROOT = DATA_DIR / "uploads"
OUTPUT_ROOT = DATA_DIR / "outputs"
RUNTIME_CONFIG_PATH = Path(os.getenv("APP_RUNTIME_CONFIG_PATH", str(DATA_DIR / "runtime_config.json"))).resolve()
TG_WORKBENCH_DB_PATH = Path(os.getenv("TG_WORKBENCH_DB_PATH", str(ROOT_DIR / "data" / "workbench.db"))).resolve()
CLOSED_IMAGE_WORKFLOW_STAGE_PREFIX = "closed_image_model:"
CLOSED_LLM_WORKFLOW_STAGE_PREFIX = "closed_llm_model:"

SECRET_KEY_HINTS = {
    "api_key",
    "token",
    "password",
    "secret",
    "authorization",
    "session",
}

DEFAULT_PRICING: dict[str, Any] = {
    "rh_coins_per_10rmb": 2500,
    "usd_to_rmb": 7.2,
    "gemini_input_usd_per_1m": 4.0,
    "gemini_output_usd_per_1m": 18.0,
    "nano_usd_per_image": 0.134,
    "allow_negative_balance": False,
}

DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "remote_comfy_gateway_url": "",
    "remote_comfy_gateway_token": "",
    "remote_comfy_workflow_mappings": {},
    "upload_server_ip": "",
    "upload_file_api_key": "",
    "image_generate_mode_default": "closed_model_api",
    "image_model_provider_base_url": "http://202.90.21.53:3008",
    "image_model_provider_api_key_gemini": "",
    "image_model_provider_api_key_gpt": "",
    "image_model_default_model": "gemini-3-pro-image-preview",
    "image_model_default_model_gemini": "gemini-3-pro-image-preview",
    "image_model_default_model_gpt": "gpt-image-1",
    "image_model_priority_order": "gemini-3-pro-image-preview, gpt-image-1",
    "llm_base_url": "http://202.90.21.53:3008",
    "llm_api_key": "",
    "llm_api_key_gemini": "",
    "llm_api_key_gpt": "",
    "llm_default_model": "gemini-3.1-pro-preview",
    "llm_default_model_gemini": "gemini-3.1-pro-preview",
    "llm_default_model_gpt": "gpt-4.1",
    "llm_model_priority_order": "gemini-3.1-pro-preview, gpt-4.1",
    "mulerouter_api_name": "",
    "mulerouter_api_key": "",
    "mulerouter_base_url": "https://api.mulerouter.ai",
    "mulerouter_wan_i2v_endpoint": "/vendors/carrothub/v1/wan2.7-i2v-spicy/generation",
    "mulerouter_wan_i2v_resolution": "720p",
    "mulerouter_wan_i2v_duration": 2,
    "mulerouter_wan_i2v_prompt_extend": False,
    "mulerouter_wan_i2v_negative_prompt": "low quality, blurry, distorted, watermark, text, logo",
    "oral_digital_human_workflow_ids": [],
    "digital_human_workflow_ids": [],
    "image_generate_workflow_ids": [],
    "replace_model_original_workflow_ids": [],
    "replace_product_workflow_ids": [],
    "replace_union_model_workflow_ids": [],
    "replace_union_product_workflow_ids": [],
    "create_video_app_id": "",
    "create_audio_app_id": "",
    "video_app_id": "",
    "replace_model_app_id": "",
    "replace_model_original_app_id": "",
    "replace_product_app_id": "",
    "cleanup_enabled": True,
    "cleanup_time": "03:30",
    "cleanup_retention_days": 7,
}

BUILTIN_IMAGE_RUNNINGHUB_WORKFLOW_ID = ""
BUILTIN_IMAGE_MODEL_PROVIDER_BASE_URL = os.getenv("IMAGE_MODEL_PROVIDER_BASE_URL", "")
BUILTIN_IMAGE_MODEL_PROVIDER_API_KEY_GEMINI = os.getenv("IMAGE_MODEL_PROVIDER_API_KEY_GEMINI", "")
BUILTIN_IMAGE_MODEL_PROVIDER_API_KEY_GPT = os.getenv("IMAGE_MODEL_PROVIDER_API_KEY_GPT", "")
BUILTIN_IMAGE_MODEL_DEFAULT = "gemini-3-pro-image-preview"
BUILTIN_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
BUILTIN_LLM_API_KEY = os.getenv("LLM_API_KEY", "")
BUILTIN_LLM_API_KEY_GEMINI = BUILTIN_LLM_API_KEY
BUILTIN_LLM_API_KEY_GPT = ""
BUILTIN_LLM_DEFAULT_MODEL = "gemini-3.1-pro-preview"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
UPLOAD_CHUNK_SIZE = 1024 * 1024
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_ZIP_MEMBERS = 5000
MAX_ZIP_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default)) or "").strip() or str(default))
    except Exception:
        return int(default)


RH_MAX_CONCURRENCY = max(_env_int("RH_MAX_CONCURRENCY", 20), 1)
TASK_QUEUE_MAXSIZE = max(_env_int("TASK_QUEUE_MAXSIZE", 0), 0)
_TASK_QUEUE: queue.Queue[tuple[str, int, str, dict[str, Any]]] = queue.Queue(maxsize=int(TASK_QUEUE_MAXSIZE or 0))
_WORKERS: list[threading.Thread] = []
_WORKERS_LOCK = threading.Lock()
_RUNTIME_CONFIG_LOCK = threading.RLock()


class RuntimeConfigFileError(RuntimeError):
    pass


def _now_ts() -> int:
    return int(time.time())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _is_admin(user: dict[str, Any]) -> bool:
    try:
        return int(user.get("is_admin") or 0) == 1
    except Exception:
        return False


def _public_register_enabled() -> bool:
    value = str(os.getenv("ALLOW_PUBLIC_REGISTER", "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _require_positive_balance(user: dict[str, Any]) -> None:
    if _is_admin(user):
        return
    try:
        bal = int(user.get("balance_cents") or 0)
    except Exception:
        bal = 0
    if bal <= 0:
        raise HTTPException(status_code=403, detail="额度为0，无法提交生成，请联系运营管理员分配额度")


def _task_queue_worker(worker_id: int) -> None:
    while True:
        item = _TASK_QUEUE.get()
        try:
            task_id, user_id, task_type, payload = item
            try:
                _task_worker(task_id, int(user_id), str(task_type), payload if isinstance(payload, dict) else {})
            except Exception:
                with db() as conn:
                    conn.execute(
                        "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                        ("failed", "任务执行线程异常退出", _now_ts(), str(task_id)),
                    )
                    _insert_task_event(
                        conn,
                        task_id=str(task_id),
                        user_id=int(user_id),
                        kind="done",
                        message="任务失败",
                        data={"status": "failed", "error": "任务执行线程异常退出", "cost_cents": 0},
                    )
        finally:
            try:
                _TASK_QUEUE.task_done()
            except Exception:
                pass


def _parse_hhmm(text: str, default_h: int = 3, default_m: int = 30) -> tuple[int, int]:
    s = str(text or "").strip()
    m = re.fullmatch(r"(\d{1,2})\s*:\s*(\d{1,2})", s)
    if not m:
        return int(default_h), int(default_m)
    h = _to_int(m.group(1), default_h)
    mi = _to_int(m.group(2), default_m)
    if h < 0 or h > 23:
        h = int(default_h)
    if mi < 0 or mi > 59:
        mi = int(default_m)
    return int(h), int(mi)


def _seconds_until_next_local_time(hour: int, minute: int) -> float:
    now = time.time()
    lt = time.localtime(now)
    target = time.struct_time((lt.tm_year, lt.tm_mon, lt.tm_mday, int(hour), int(minute), 0, lt.tm_wday, lt.tm_yday, lt.tm_isdst))
    target_ts = time.mktime(target)
    if target_ts <= now + 1:
        tomorrow = time.localtime(now + 86400)
        target = time.struct_time((tomorrow.tm_year, tomorrow.tm_mon, tomorrow.tm_mday, int(hour), int(minute), 0, tomorrow.tm_wday, tomorrow.tm_yday, tomorrow.tm_isdst))
        target_ts = time.mktime(target)
    return max(float(target_ts - now), 1.0)


def parse_model_list(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"\s*[,，\n]+\s*", text)
    out: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _safe_rmtree(path: Path) -> None:
    try:
        p = path.resolve()
    except Exception:
        p = path
    if DATA_DIR != p and DATA_DIR not in p.parents:
        return
    if p.exists():
        shutil.rmtree(str(p), ignore_errors=True)


def _cleanup_files_once(*, retention_days: int) -> dict[str, Any]:
    cutoff = time.time() - float(max(int(retention_days), 1) * 86400)
    with db() as conn:
        rows = conn.execute("SELECT id FROM tasks WHERE status IN ('queued','running')").fetchall()
    active = {str(r["id"]) for r in rows if r and str(r["id"] or "").strip()}

    deleted: list[str] = []
    scanned = 0

    def walk_root(root: Path):
        nonlocal scanned
        if not root.exists():
            return
        for user_dir in root.iterdir():
            if not user_dir.is_dir():
                continue
            for item_dir in user_dir.iterdir():
                if not item_dir.is_dir():
                    continue
                scanned += 1
                tid = str(item_dir.name or "").strip()
                if tid and tid in active:
                    continue
                try:
                    mtime = float(item_dir.stat().st_mtime)
                except Exception:
                    mtime = 0.0
                if mtime > cutoff:
                    continue
                _safe_rmtree(item_dir)
                deleted.append(str(item_dir))

    walk_root(UPLOAD_ROOT)
    walk_root(OUTPUT_ROOT)
    return {"scanned": int(scanned), "deleted": int(len(deleted)), "deleted_paths": deleted[:50]}


def _cleanup_worker() -> None:
    while True:
        try:
            with db() as conn:
                cfg = _get_runtime_config(conn)
            if not _to_bool(cfg.get("cleanup_enabled"), True):
                time.sleep(30.0)
                continue
            h, m = _parse_hhmm(str(cfg.get("cleanup_time") or ""))
            wait = _seconds_until_next_local_time(h, m)
            time.sleep(wait)
            with db() as conn:
                cfg2 = _get_runtime_config(conn)
            if not _to_bool(cfg2.get("cleanup_enabled"), True):
                continue
            retention = max(_to_int(cfg2.get("cleanup_retention_days"), 7), 1)
            _cleanup_files_once(retention_days=retention)
        except Exception:
            time.sleep(10.0)


def _start_task_workers() -> None:
    with _WORKERS_LOCK:
        if _WORKERS:
            return
        for i in range(int(RH_MAX_CONCURRENCY)):
            t = threading.Thread(target=_task_queue_worker, args=(i + 1,), daemon=True)
            _WORKERS.append(t)
            t.start()


_CLEANUP_THREAD: threading.Thread | None = None
_CLEANUP_LOCK = threading.Lock()


def _start_cleanup_worker() -> None:
    global _CLEANUP_THREAD
    with _CLEANUP_LOCK:
        if _CLEANUP_THREAD is not None:
            return
        t = threading.Thread(target=_cleanup_worker, args=(), daemon=True)
        _CLEANUP_THREAD = t
        t.start()


def _resume_pending_tasks() -> None:
    rows = []
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, type, status, input_json, created_at
            FROM tasks
            WHERE status IN ('queued', 'running')
            ORDER BY created_at ASC
            """,
        ).fetchall()

    if not rows:
        return

    with db() as conn:
        for r in rows:
            tid = str(r["id"] or "").strip()
            if not tid:
                continue
            user_id = int(r["user_id"] or 0)
            task_type = str(r["type"] or "").strip()
            status = str(r["status"] or "").strip().lower()
            payload = _json_loads(r["input_json"], {})
            if not isinstance(payload, dict) or not task_type or user_id <= 0:
                continue
            payload = _apply_runtime_defaults(task_type, payload)
            if status == "running":
                conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", ("queued", _now_ts(), tid))
                _insert_task_event(conn, task_id=tid, user_id=user_id, kind="queued", message="服务重启，任务重新排队", data={})
            try:
                _TASK_QUEUE.put((tid, user_id, task_type, payload), block=False)
            except Exception:
                conn.execute(
                    "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                    ("failed", "任务队列已满，无法入队", _now_ts(), tid),
                )
                _insert_task_event(
                    conn,
                    task_id=tid,
                    user_id=user_id,
                    kind="done",
                    message="任务失败",
                    data={"status": "failed", "error": "任务队列已满，无法入队", "cost_cents": 0},
                )

def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(text: Any, default: Any) -> Any:
    try:
        return json.loads(str(text or ""))
    except Exception:
        return default


def _is_secret_key(key: str) -> bool:
    low = str(key or "").strip().lower()
    return any(hint in low for hint in SECRET_KEY_HINTS)


def _mask_secret(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}***{text[-4:]}"


def _read_dotenv_values(path: Path | None = None) -> dict[str, str]:
    env_path = path or (ROOT_DIR / ".env")
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _parse_id_list(value: Any) -> list[int]:
    items: list[int] = []
    for part in str(value or "").replace(";", ",").split(","):
        raw = part.strip()
        if not raw:
            continue
        try:
            items.append(int(raw))
        except Exception:
            continue
    return list(dict.fromkeys(items))


def _tg_env_values() -> dict[str, str]:
    values = _read_dotenv_values()
    for key in ("TG_BOT_TOKEN", "TG_ALLOWED_CHAT_IDS", "TG_CHAT_ID", "PUBLIC_BASE_URL"):
        raw = str(os.getenv(key) or "").strip()
        if raw:
            values[key] = raw
    return values


def _tg_seed_chat_ids(env_values: dict[str, str]) -> list[int]:
    allowed = _parse_id_list(env_values.get("TG_ALLOWED_CHAT_IDS"))
    return allowed or _parse_id_list(env_values.get("TG_CHAT_ID"))


def _ensure_tg_workbench_schema(conn: sqlite3.Connection, env_values: dict[str, str] | None = None) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_members (
            chat_id            INTEGER PRIMARY KEY,
            label              TEXT NOT NULL DEFAULT '',
            enabled            INTEGER NOT NULL DEFAULT 1,
            notify_busy        INTEGER NOT NULL DEFAULT 1,
            notify_available   INTEGER NOT NULL DEFAULT 1,
            created_at         REAL NOT NULL,
            updated_at         REAL NOT NULL
        )
        """
    )
    now = time.time()
    for chat_id in _tg_seed_chat_ids(env_values or {}):
        conn.execute(
            """
            INSERT INTO workspace_members
            (chat_id, label, enabled, notify_busy, notify_available, created_at, updated_at)
            VALUES (?, ?, 1, 1, 1, ?, ?)
            ON CONFLICT(chat_id) DO NOTHING
            """,
            (int(chat_id), f"TG-{chat_id}", now, now),
        )
    conn.commit()


def _connect_tg_workbench_db() -> sqlite3.Connection:
    TG_WORKBENCH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(TG_WORKBENCH_DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_tg_workbench_schema(conn, _tg_env_values())
    return conn


def _tg_member_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "chat_id": int(row["chat_id"]),
        "label": str(row["label"] or ""),
        "enabled": bool(int(row["enabled"] or 0)),
        "notify_busy": bool(int(row["notify_busy"] or 0)),
        "notify_available": bool(int(row["notify_available"] or 0)),
        "created_at": float(row["created_at"] or 0),
        "updated_at": float(row["updated_at"] or 0),
    }


def _load_tg_settings_payload() -> dict[str, Any]:
    env_values = _tg_env_values()
    token = str(env_values.get("TG_BOT_TOKEN") or "").strip()
    members: list[dict[str, Any]] = []
    conn = _connect_tg_workbench_db()
    try:
        rows = conn.execute(
            """
            SELECT chat_id, label, enabled, notify_busy, notify_available, created_at, updated_at
            FROM workspace_members
            ORDER BY enabled DESC, chat_id ASC
            """
        ).fetchall()
        members = [_tg_member_payload(row) for row in rows]
    finally:
        conn.close()
    return {
        "db_path": str(TG_WORKBENCH_DB_PATH),
        "db_exists": TG_WORKBENCH_DB_PATH.exists(),
        "bot_token_configured": bool(token),
        "bot_token_masked": _mask_secret(token) if token else "",
        "allowed_chat_ids_env": _tg_seed_chat_ids(env_values),
        "trusted_users": members,
    }


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _is_secret_key(k):
                out[str(k)] = _mask_secret(v)
            else:
                out[str(k)] = _sanitize_payload(v)
        return out
    if isinstance(value, list):
        return [_sanitize_payload(v) for v in value]
    return value


def _error_analysis_available(runtime: dict[str, Any]) -> bool:
    return bool(
        str(runtime.get("llm_api_key_gemini") or runtime.get("llm_api_key_gpt") or runtime.get("llm_api_key") or BUILTIN_LLM_API_KEY_GEMINI or BUILTIN_LLM_API_KEY_GPT or "").strip()
        and str(runtime.get("llm_base_url") or BUILTIN_LLM_BASE_URL or "").strip()
    )


def _resolve_llm_settings(source: dict[str, Any] | None, *, allow_builtin: bool = True) -> tuple[str, str, str]:
    base_url, candidates = _resolve_llm_fallback_candidates(source, allow_builtin=allow_builtin)
    if candidates:
        first = candidates[0]
        return base_url, str(first.get("api_key") or "").strip(), str(first.get("model") or "").strip()
    return base_url, "", ""


def _resolve_openai_models_url(base_url: str) -> str:
    cleaned = str(base_url or "").strip().strip("`'\"")
    if not cleaned:
        raise ValueError("缺少文字模型 API Base URL")
    if "://" not in cleaned:
        cleaned = "https://" + cleaned.lstrip("/")
    parsed = urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("文字模型 API Base URL 无效")
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/models"):
        final_path = path
    elif path.endswith("/v1"):
        final_path = f"{path}/models"
    elif path.endswith("/chat/completions"):
        final_path = path[: -len("/chat/completions")] + "/models"
    elif not path:
        final_path = "/v1/models"
    else:
        final_path = f"{path}/v1/models"
    return urlunsplit((parsed.scheme, parsed.netloc, final_path, parsed.query, parsed.fragment))


def _fetch_openai_compatible_model_ids(*, base_url: str, api_key: str) -> list[str]:
    models_url = _resolve_openai_models_url(base_url)
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("缺少文字模型 API Key")
    try:
        resp = requests.get(
            models_url,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"查询可用模型失败: {exc}") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"查询可用模型失败: HTTP {resp.status_code}; {resp.text[:300]}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError("查询可用模型失败: 响应不是有效 JSON") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in rows:
        model_id = str(item.get("id") if isinstance(item, dict) else item or "").strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            out.append(model_id)
    return out


def _detect_llm_provider(model: str) -> str:
    text = str(model or "").strip().lower()
    if "grok" in text:
        return "gpt"
    if text.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4")):
        return "gpt"
    if "gpt" in text:
        return "gpt"
    return "gemini"


def _detect_image_model_provider(model: str) -> str:
    text = str(model or "").strip().lower()
    if text.startswith("gpt-") or "gpt-image" in text or text.startswith("chatgpt"):
        return "gpt"
    return "gemini"


def _select_llm_api_key(provider: str, *, gemini_api_key: str, gpt_api_key: str, legacy_api_key: str) -> str:
    if provider == "gpt":
        return str(gpt_api_key or legacy_api_key or gemini_api_key or "").strip()
    return str(gemini_api_key or legacy_api_key or gpt_api_key or "").strip()


def _select_image_api_key(provider: str, *, gemini_api_key: str, gpt_api_key: str) -> str:
    if provider == "gpt":
        return str(gpt_api_key or gemini_api_key or "").strip()
    return str(gemini_api_key or gpt_api_key or "").strip()


def _build_model_priority(
    *,
    explicit_models: list[str],
    priority_models: list[str],
    gemini_models: list[str],
    gpt_models: list[str],
    legacy_models: list[str],
    builtin_model: str,
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    def add_all(items: list[str]) -> None:
        for raw in items:
            model = str(raw or "").strip()
            if not model or model in seen:
                continue
            seen.add(model)
            merged.append(model)

    add_all(explicit_models)
    add_all(priority_models)
    add_all(gemini_models)
    add_all(gpt_models)
    add_all(legacy_models)
    if not merged:
        add_all([builtin_model])
    return merged


def _resolve_llm_fallback_candidates(source: dict[str, Any] | None, *, allow_builtin: bool = True) -> tuple[str, list[dict[str, str]]]:
    payload = source if isinstance(source, dict) else {}
    base_url = str(payload.get("llm_base_url") or "").strip()
    gemini_api_key = str(payload.get("llm_api_key_gemini") or "").strip()
    gpt_api_key = str(payload.get("llm_api_key_gpt") or "").strip()
    legacy_api_key = str(payload.get("llm_api_key") or "").strip()
    if allow_builtin:
        base_url = base_url or str(BUILTIN_LLM_BASE_URL).strip()
        gemini_api_key = gemini_api_key or str(BUILTIN_LLM_API_KEY_GEMINI).strip()
        gpt_api_key = gpt_api_key or str(BUILTIN_LLM_API_KEY_GPT).strip()
        legacy_api_key = legacy_api_key or str(BUILTIN_LLM_API_KEY).strip()

    explicit_models = parse_model_list(payload.get("llm_model"))
    priority_models = parse_model_list(payload.get("llm_model_priority_order"))
    gemini_models = parse_model_list(payload.get("llm_default_model_gemini"))
    gpt_models = parse_model_list(payload.get("llm_default_model_gpt"))
    legacy_models = parse_model_list(payload.get("llm_default_model"))
    model_priority = _build_model_priority(
        explicit_models=explicit_models,
        priority_models=priority_models,
        gemini_models=gemini_models,
        gpt_models=gpt_models,
        legacy_models=legacy_models,
        builtin_model=str(BUILTIN_LLM_DEFAULT_MODEL).strip(),
    )

    candidates: list[dict[str, str]] = []
    for model in model_priority:
        provider = _detect_llm_provider(model)
        api_key = _select_llm_api_key(
            provider,
            gemini_api_key=gemini_api_key,
            gpt_api_key=gpt_api_key,
            legacy_api_key=legacy_api_key,
        )
        if not api_key:
            continue
        candidates.append(
            {
                "model": model,
                "provider": provider,
                "api_key": api_key,
            }
        )
    return base_url, candidates


def _resolve_closed_image_model_settings(source: dict[str, Any] | None, *, allow_builtin: bool = True) -> tuple[str, str, str, str]:
    base_url, gemini_api_key, gpt_api_key, candidates = _resolve_closed_image_model_fallback_candidates(
        source,
        allow_builtin=allow_builtin,
    )
    model = str(candidates[0].get("model") or "").strip() if candidates else ""
    return base_url, gemini_api_key, gpt_api_key, model


def _resolve_closed_image_model_fallback_candidates(
    source: dict[str, Any] | None,
    *,
    allow_builtin: bool = True,
) -> tuple[str, str, str, list[dict[str, str]]]:
    payload = source if isinstance(source, dict) else {}
    base_url = str(payload.get("image_model_provider_base_url") or "").strip()
    gemini_api_key = str(payload.get("image_model_provider_api_key_gemini") or "").strip()
    gpt_api_key = str(payload.get("image_model_provider_api_key_gpt") or "").strip()
    if allow_builtin:
        base_url = base_url or str(BUILTIN_IMAGE_MODEL_PROVIDER_BASE_URL).strip()
        gemini_api_key = gemini_api_key or str(BUILTIN_IMAGE_MODEL_PROVIDER_API_KEY_GEMINI).strip()
        gpt_api_key = gpt_api_key or str(BUILTIN_IMAGE_MODEL_PROVIDER_API_KEY_GPT).strip()

    explicit_models = parse_model_list(payload.get("image_generate_model"))
    priority_models = parse_model_list(payload.get("image_model_priority_order"))
    gemini_models = parse_model_list(payload.get("image_model_default_model_gemini"))
    gpt_models = parse_model_list(payload.get("image_model_default_model_gpt"))
    legacy_models = parse_model_list(payload.get("image_model_default_model"))
    model_priority = _build_model_priority(
        explicit_models=explicit_models,
        priority_models=priority_models,
        gemini_models=gemini_models,
        gpt_models=gpt_models,
        legacy_models=legacy_models,
        builtin_model=str(BUILTIN_IMAGE_MODEL_DEFAULT).strip(),
    )

    candidates: list[dict[str, str]] = []
    for model in model_priority:
        provider = _detect_image_model_provider(model)
        api_key = _select_image_api_key(
            provider,
            gemini_api_key=gemini_api_key,
            gpt_api_key=gpt_api_key,
        )
        if not api_key:
            continue
        candidates.append(
            {
                "model": model,
                "provider": provider,
                "api_key": api_key,
            }
        )
    return base_url, gemini_api_key, gpt_api_key, candidates


def _request_llm_json_with_fallback(
    *,
    source: dict[str, Any] | None,
    user_input: str,
    system_prompt: str,
    port: int | str | None = None,
    parameters: dict | None | str = "",
    image_paths: list[str] | str | None = None,
    video_paths: list[str] | str | None = None,
    allow_builtin: bool = True,
    logger=None,
    request_label: str = "文字模型请求",
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    base_url, candidates = _resolve_llm_fallback_candidates(source, allow_builtin=allow_builtin)
    if not base_url:
        raise RuntimeError("缺少文字模型 API Base URL")
    if not candidates:
        raise RuntimeError("缺少文字模型 API Key 或候选模型")
    attempts: list[dict[str, Any]] = []
    errors: list[str] = []
    last_result: dict[str, Any] | None = None
    for idx, candidate in enumerate(candidates, start=1):
        model = str(candidate.get("model") or "").strip()
        provider = str(candidate.get("provider") or "").strip()
        api_key = str(candidate.get("api_key") or "").strip()
        if logger:
            logger(f"{request_label}尝试 {idx}/{len(candidates)}：{provider} · {model}")
        result = get_gemini.request_gemini3_pro_json(
            user_input=user_input,
            host=base_url,
            api_key=api_key,
            system_prompt=system_prompt,
            port=port,
            parameters=parameters,
            image_paths=image_paths,
            video_paths=video_paths,
            logger=logger,
            model=model,
        )
        last_result = result if isinstance(result, dict) else {"ok": False, "error": str(result)}
        ok = isinstance(last_result, dict) and last_result.get("ok") is True
        attempts.append(
            {
                "attempt": idx,
                "provider": provider,
                "model": model,
                "ok": bool(ok),
                "error": "" if ok else str(last_result.get("error") or "请求失败"),
            }
        )
        if ok:
            return last_result, candidate, attempts
        errors.append(f"{provider}:{model} -> {str(last_result.get('error') or '请求失败')}")
    error_text = "; ".join(errors) if errors else "未知错误"
    raise RuntimeError(f"{request_label}全部候选模型调用失败: {error_text}")


def _generate_closed_image_with_fallback(
    *,
    source: dict[str, Any] | None,
    prompt: str,
    output_image_path: str,
    input_image_path: str | None = None,
    allow_builtin: bool = True,
    logger=None,
    request_label: str = "闭源图片模型请求",
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    base_url, gemini_api_key, gpt_api_key, candidates = _resolve_closed_image_model_fallback_candidates(
        source,
        allow_builtin=allow_builtin,
    )
    if not str(base_url or "").strip():
        raise RuntimeError("缺少闭源图像模型 Base URL")
    if not candidates:
        raise RuntimeError("缺少闭源图像模型 API Key 或候选模型")
    attempts: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, candidate in enumerate(candidates, start=1):
        model = str(candidate.get("model") or "").strip()
        provider = str(candidate.get("provider") or "").strip()
        if logger:
            logger(f"{request_label}尝试 {idx}/{len(candidates)}：{provider} · {model}")
        try:
            result = image_model_api.generate_image(
                base_url=base_url,
                model=model,
                prompt=prompt,
                output_image_path=output_image_path,
                gemini_api_key=gemini_api_key,
                gpt_api_key=gpt_api_key,
                input_image_path=input_image_path,
                logger=logger,
            )
            attempts.append(
                {
                    "attempt": idx,
                    "provider": provider,
                    "model": model,
                    "ok": True,
                    "error": "",
                }
            )
            return (result if isinstance(result, dict) else {"raw_result": result}), candidate, attempts
        except Exception as exc:
            err = str(exc)
            attempts.append(
                {
                    "attempt": idx,
                    "provider": provider,
                    "model": model,
                    "ok": False,
                    "error": err,
                }
            )
            errors.append(f"{provider}:{model} -> {err}")
            continue
    error_text = "; ".join(errors) if errors else "未知错误"
    raise RuntimeError(f"{request_label}全部候选模型调用失败: {error_text}")


def _extract_download_path(output_data: dict[str, Any]) -> str:
    candidates = [
        output_data.get("download_path"),
        output_data.get("video_path"),
        output_data.get("audio_path"),
        output_data.get("image_path"),
        output_data.get("result_zip"),
        output_data.get("result_path"),
        output_data.get("output_path"),
    ]
    for cand in candidates:
        text = str(cand or "").strip()
        if text and Path(text).exists():
            return text
    return ""


def _task_has_download_file(output_data: dict[str, Any]) -> bool:
    return bool(_extract_download_path(output_data))


def _get_tg_chat_id_from_payload(payload: dict[str, Any]) -> int | None:
    try:
        chat_id = int(payload.get("tg_chat_id") or 0)
    except Exception:
        return None
    return chat_id if chat_id > 0 else None


def _telegram_file_method(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTS:
        return "sendPhoto"
    if suffix in VIDEO_EXTS:
        return "sendVideo"
    return "sendDocument"


def _tg_bot_token() -> str:
    return str(os.getenv("TG_BOT_TOKEN") or _read_dotenv_values().get("TG_BOT_TOKEN") or "").strip()


def _send_telegram_message(chat_id: int, text: str) -> bool:
    token = _tg_bot_token()
    if not token or int(chat_id or 0) <= 0:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": int(chat_id), "text": str(text or "")[:3900]},
            timeout=30,
        )
        return resp.status_code < 400
    except Exception:
        return False


def _send_telegram_file(chat_id: int, file_path: str, *, caption: str) -> bool:
    token = _tg_bot_token()
    path = Path(str(file_path or "")).expanduser()
    if not token or int(chat_id or 0) <= 0 or not path.exists() or not path.is_file():
        return False
    method = _telegram_file_method(path)
    field = {"sendPhoto": "photo", "sendVideo": "video"}.get(method, "document")
    try:
        with path.open("rb") as fh:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/{method}",
                data={"chat_id": int(chat_id), "caption": str(caption or "")[:1000]},
                files={field: (path.name, fh)},
                timeout=120,
            )
        if resp.status_code < 400:
            return True
    except Exception:
        pass
    if method != "sendDocument":
        try:
            with path.open("rb") as fh:
                resp = requests.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": int(chat_id), "caption": str(caption or "")[:1000]},
                    files={"document": (path.name, fh)},
                    timeout=120,
                )
            return resp.status_code < 400
        except Exception:
            return False
    return False


def _notify_tg_task_finished(
    *,
    task_id: str,
    task_type: str,
    payload: dict[str, Any],
    status: str,
    error: str,
    output_data: dict[str, Any],
) -> None:
    chat_id = _get_tg_chat_id_from_payload(payload)
    if chat_id is None:
        return
    download_path = _extract_download_path(output_data if isinstance(output_data, dict) else {})
    public_base = str(os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    task_url = f"{public_base}/index.html#app-tasks" if public_base else ""
    if str(status or "").strip().lower() == "success":
        caption = "\n".join(
            part
            for part in [
                "后台生成任务已完成。",
                f"工作流: {task_type}",
                f"任务编号: {task_id}",
            ]
            if part
        )
        if download_path and _send_telegram_file(chat_id, download_path, caption=caption):
            return
        parts = [caption]
        if task_url:
            parts.append(f"工作台: {task_url}")
        if download_path:
            parts.append(f"结果文件: {download_path}")
        _send_telegram_message(chat_id, "\n".join(parts))
        return

    message = "\n".join(
        part
        for part in [
            "后台生成任务失败。",
            f"工作流: {task_type}",
            f"任务编号: {task_id}",
            f"原因: {_format_user_visible_task_error(str(error or output_data.get('error') or output_data.get('message') or '未知错误').strip())}",
            f"工作台: {task_url}" if task_url else "",
        ]
        if part
    )
    _send_telegram_message(chat_id, message)


def _format_user_visible_task_error(error: str) -> str:
    text = str(error or "").strip()
    if "MuleRouter" in text and ("External service request failed" in text or '"code": 3002' in text):
        return "MuleRouter 下游生成失败（3002）。常见原因是提示词或参考图触发供应商限制、图文不匹配，或供应商服务临时异常；请换成更清晰、合规的动作描述后重试。"
    return text or "未知错误"


def _truncate_text(value: Any, max_len: int = 1200) -> str:
    text = str(value or "")
    if len(text) <= int(max_len):
        return text
    return f"{text[:int(max_len)]}...(已截断，共{len(text)}字符)"


def _truncate_payload(
    value: Any,
    *,
    max_string: int = 1200,
    max_list_items: int = 20,
    max_dict_items: int = 40,
    depth: int = 0,
    max_depth: int = 6,
) -> Any:
    if depth >= max_depth:
        return "[已截断: 嵌套过深]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:max_dict_items]:
            out[str(key)] = _truncate_payload(
                item,
                max_string=max_string,
                max_list_items=max_list_items,
                max_dict_items=max_dict_items,
                depth=depth + 1,
                max_depth=max_depth,
            )
        if len(items) > max_dict_items:
            out["__truncated_keys__"] = int(len(items) - max_dict_items)
        return out
    if isinstance(value, list):
        out_list = [
            _truncate_payload(
                item,
                max_string=max_string,
                max_list_items=max_list_items,
                max_dict_items=max_dict_items,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for item in value[:max_list_items]
        ]
        if len(value) > max_list_items:
            out_list.append(f"...(其余 {len(value) - max_list_items} 项已截断)")
        return out_list
    if isinstance(value, str):
        return _truncate_text(value, max_len=max_string)
    return value


def _sanitize_log_payload(value: Any) -> Any:
    return _truncate_payload(_sanitize_payload(value))


def _default_event_stage(kind: str) -> str:
    low = str(kind or "").strip().lower()
    mapping = {
        "queued": "queue",
        "running": "running",
        "progress": "progress",
        "log": "log",
        "done": "finish",
        "analysis": "error_analysis",
    }
    return mapping.get(low, "log")


def _default_event_status(kind: str, data: dict[str, Any]) -> str:
    low = str(kind or "").strip().lower()
    if str(data.get("status") or "").strip():
        return str(data.get("status") or "").strip()
    if str(data.get("state") or "").strip():
        return str(data.get("state") or "").strip()
    if low == "queued":
        return "queued"
    if low == "running":
        return "running"
    if low == "done":
        return "failed" if str(data.get("error") or "").strip() else "success"
    if low == "analysis":
        return "success"
    return "info"


def _normalize_task_event_data(kind: str, message: str, data: Any) -> dict[str, Any]:
    merged = dict(data) if isinstance(data, dict) else ({"raw": data} if data not in {None, ""} else {})
    if not str(merged.get("message") or "").strip():
        merged["message"] = str(message or "")
    if not str(merged.get("stage") or "").strip():
        merged["stage"] = _default_event_stage(kind)
    if not str(merged.get("status") or "").strip():
        merged["status"] = _default_event_status(kind, merged)
    if not str(merged.get("level") or "").strip():
        status_text = str(merged.get("status") or "").strip().lower()
        merged["level"] = "error" if status_text == "failed" else ("warn" if status_text == "warn" else "info")
    if not str(merged.get("source") or "").strip():
        merged["source"] = "webapp"
    if "user_visible" not in merged:
        merged["user_visible"] = bool(str(kind or "").strip().lower() in {"queued", "running", "progress", "done", "analysis"})
    return _sanitize_log_payload(merged)


def _count_batch_success(item: Any) -> bool:
    if isinstance(item, dict):
        if isinstance(item.get("ok"), bool):
            return bool(item.get("ok"))
        status = str(item.get("status") or "").strip().lower()
        if status in {"success", "ok", "done"}:
            return True
    return False


def _extract_batch_summary(output_data: Any) -> dict[str, Any]:
    output = output_data if isinstance(output_data, dict) else {}
    items = output.get("items") if isinstance(output.get("items"), list) else []
    total_count = max(_to_int(output.get("total"), 0), len(items))
    success_count = max(_to_int(output.get("success"), 0), 0)
    if items:
        success_count = sum(1 for item in items if _count_batch_success(item))
    failed_count = max(int(total_count - success_count), 0)
    first_error = str(output.get("error") or "").strip()
    if not first_error and failed_count > 0 and not _to_bool(output.get("ok"), False):
        first_error = str(output.get("message") or "").strip()
    if items and not first_error:
        for item in items:
            text = str((item or {}).get("error") or (item or {}).get("message") or "").strip() if isinstance(item, dict) else ""
            if text:
                first_error = text
                break
    return {
        "total_count": int(total_count),
        "success_count": int(success_count),
        "failed_count": int(failed_count),
        "first_error": first_error,
    }


def _build_final_output_snapshot(output_data: Any) -> dict[str, Any]:
    output = output_data if isinstance(output_data, dict) else {}
    download_path = _extract_download_path(output) if isinstance(output, dict) else ""
    summary = _extract_batch_summary(output)
    snapshot: dict[str, Any] = {
        "download_path": download_path,
        "has_download": bool(download_path),
        "runninghub_task_id": str(output.get("runninghub_task_id") or "").strip(),
        "runninghub_task_ids": list(output.get("runninghub_task_ids") or []) if isinstance(output.get("runninghub_task_ids"), list) else [],
        "message": str(output.get("message") or output.get("error") or "").strip(),
        "summary": summary,
    }
    for key in ("video_path", "audio_path", "image_path", "result_zip", "result_path", "output_path"):
        value = str(output.get(key) or "").strip()
        if value:
            snapshot[key] = value
    if isinstance(output.get("items"), list):
        compact_items: list[dict[str, Any]] = []
        for idx, item in enumerate(output.get("items") or [], start=1):
            if not isinstance(item, dict):
                continue
            compact_items.append(
                {
                    "item_index": idx,
                    "item_id": str(item.get("id") or item.get("item_id") or f"item_{idx}"),
                    "status": str(item.get("status") or ("success" if _count_batch_success(item) else "failed")),
                    "error": str(item.get("error") or item.get("message") or "").strip(),
                    "video_path": str(item.get("video_path") or "").strip(),
                    "download_path": str(item.get("download_path") or "").strip(),
                    "runninghub_task_id": str(item.get("runninghub_task_id") or "").strip(),
                    "cost_cents": int(_to_int(item.get("cost_cents"), 0)),
                }
            )
        if compact_items:
            snapshot["items"] = _sanitize_log_payload(compact_items)
    return _sanitize_log_payload(snapshot)


def _read_jsonl_records(path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            text = str(line or "").strip()
            if not text:
                continue
            parsed = _json_loads(text, {})
            if isinstance(parsed, dict):
                rows.append(parsed)
    except Exception:
        return []
    if limit > 0 and len(rows) > limit:
        return rows[:limit]
    return rows


def _extract_execution_step(item: Any, *, fallback_step: int = 0) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    nested = None
    for key in ("result", "done", "query", "final"):
        value = item.get(key)
        if isinstance(value, dict):
            nested = value
            break
    step_index = max(_to_int(item.get("step"), fallback_step), 0)
    workflow_id = str(item.get("workflow_id") or item.get("app_id") or "").strip()
    runninghub_task_id = str(
        item.get("runninghub_task_id")
        or item.get("task_id")
        or _extract_runninghub_task_id(nested)
        or ""
    ).strip()
    status = str(item.get("status") or (nested.get("status") if isinstance(nested, dict) else "") or "").strip()
    message = str(
        item.get("message")
        or item.get("error")
        or (nested.get("message") if isinstance(nested, dict) else "")
        or ""
    ).strip()
    output_path = str(item.get("output_path") or item.get("video_path") or item.get("image_path") or "").strip()
    input_ref = str(
        item.get("camera_video_url")
        or item.get("input_video_url")
        or item.get("input_temp_video_url")
        or item.get("video_path")
        or ""
    ).strip()
    uploaded_ref = str(item.get("uploaded_video_url") or item.get("uploaded_image_url") or "").strip()
    payload = {
        "step": int(step_index),
        "workflow_id": workflow_id,
        "runninghub_task_id": runninghub_task_id,
        "status": status,
        "message": message,
        "output_path": output_path,
        "input_ref": input_ref,
        "uploaded_ref": uploaded_ref,
    }
    return _sanitize_log_payload({k: v for k, v in payload.items() if v not in {"", None}})


def _build_trace_group(*, title: str, steps: list[dict[str, Any]], status: str = "", message: str = "", final_output_path: str = "") -> dict[str, Any]:
    normalized_steps = [step for step in (_extract_execution_step(step, fallback_step=index) for index, step in enumerate(steps, start=1)) if step]
    payload = {
        "title": str(title or "").strip(),
        "status": str(status or "").strip(),
        "message": str(message or "").strip(),
        "final_output_path": str(final_output_path or "").strip(),
        "steps": normalized_steps,
    }
    return _sanitize_log_payload(
        {
            k: v
            for k, v in payload.items()
            if v is not None and v != "" and v != []
        }
    )


def _extract_execution_trace_from_step_results(raw_result: dict[str, Any], *, title: str, status: str = "", message: str = "", final_output_path: str = "") -> list[dict[str, Any]]:
    steps = raw_result.get("steps") if isinstance(raw_result.get("steps"), list) else []
    if not steps:
        return []
    return [_build_trace_group(title=title, steps=steps, status=status, message=message, final_output_path=final_output_path)]


def _extract_execution_trace_from_union_logs(output_dir: Path) -> list[dict[str, Any]]:
    records = _read_jsonl_records(output_dir / "logs.jsonl", limit=20)
    groups: list[dict[str, Any]] = []
    for record in records:
        job_no = max(_to_int(record.get("job"), 0), 0)
        stage_model = record.get("stage_model") if isinstance(record.get("stage_model"), dict) else {}
        for part in stage_model.get("parts") if isinstance(stage_model.get("parts"), list) else []:
            if not isinstance(part, dict):
                continue
            part_no = max(_to_int(part.get("part"), 0), 0)
            steps = [dict(step) for step in (part.get("steps") if isinstance(part.get("steps"), list) else []) if isinstance(step, dict)]
            if steps and str(part.get("input_video_url") or "").strip():
                steps[0]["input_video_url"] = str(part.get("input_video_url") or "").strip()
            groups.append(
                _build_trace_group(
                    title=f"联合替换·模特链 Job {job_no} / Part {part_no}",
                    steps=steps,
                    status=str(record.get("status") or "").strip(),
                    message=str(record.get("error") or "").strip(),
                    final_output_path=str(record.get("final") or "").strip(),
                )
            )
        stage_product = record.get("stage_product") if isinstance(record.get("stage_product"), dict) else {}
        for part in stage_product.get("parts") if isinstance(stage_product.get("parts"), list) else []:
            if not isinstance(part, dict):
                continue
            part_no = max(_to_int(part.get("part"), 0), 0)
            steps = [dict(step) for step in (part.get("steps") if isinstance(part.get("steps"), list) else []) if isinstance(step, dict)]
            if steps and str(part.get("input_temp_video_url") or "").strip():
                steps[0]["input_temp_video_url"] = str(part.get("input_temp_video_url") or "").strip()
            groups.append(
                _build_trace_group(
                    title=f"联合替换·商品链 Job {job_no} / Part {part_no}",
                    steps=steps,
                    status=str(record.get("status") or "").strip(),
                    message=str(record.get("error") or "").strip(),
                    final_output_path=str(record.get("final") or "").strip(),
                )
            )
    return [group for group in groups if isinstance(group, dict) and group.get("steps")]


def _extract_execution_trace_from_video_logs(output_dir: Path) -> list[dict[str, Any]]:
    records = _read_jsonl_records(output_dir / "logs.jsonl", limit=20)
    groups: list[dict[str, Any]] = []
    for record in records:
        video_chain = record.get("video_chain") if isinstance(record.get("video_chain"), dict) else {}
        steps = video_chain.get("steps") if isinstance(video_chain.get("steps"), list) else []
        if not steps:
            continue
        job_no = max(_to_int(record.get("job"), 0), 0)
        groups.append(
            _build_trace_group(
                title=f"口播视频链 Job {job_no}",
                steps=steps,
                status=str(record.get("status") or "").strip(),
                message=str(record.get("error") or "").strip(),
                final_output_path=str(record.get("video") or "").strip(),
            )
        )
    return [group for group in groups if isinstance(group, dict) and group.get("steps")]


def _build_task_execution_trace(*, task_type: str, output_data: Any) -> list[dict[str, Any]]:
    output = output_data if isinstance(output_data, dict) else {}
    raw_result = output.get("raw_result") if isinstance(output.get("raw_result"), dict) else {}
    final_output_path = str(
        output.get("download_path")
        or output.get("video_path")
        or output.get("audio_path")
        or output.get("image_path")
        or ""
    ).strip()
    status = "success" if _to_bool(output.get("ok"), False) else str(output.get("status") or "").strip()
    message = str(output.get("message") or output.get("error") or "").strip()
    if task_type == "replace_model":
        return _extract_execution_trace_from_step_results(raw_result, title="视频模特替换链", status=status, message=message, final_output_path=final_output_path)
    if task_type == "replace_product":
        return _extract_execution_trace_from_step_results(raw_result, title="视频商品替换链", status=status, message=message, final_output_path=final_output_path)
    if task_type == "image_generate":
        return _extract_execution_trace_from_step_results(raw_result, title="图片生成链", status=status, message=message, final_output_path=final_output_path)
    if task_type in {"create_video", "commerce_video", "batch_create_video"}:
        direct_trace = _extract_execution_trace_from_step_results(raw_result, title="数字人工作流", status=status, message=message, final_output_path=final_output_path)
        if direct_trace:
            return direct_trace

    output_dir_candidates = [
        output.get("output_dir"),
        raw_result.get("output_dir"),
    ]
    output_dir = None
    for candidate in output_dir_candidates:
        text = str(candidate or "").strip()
        if text:
            output_dir = Path(text).resolve()
            break
    if output_dir is None:
        return []
    if task_type in {"create_video", "commerce_video", "batch_create_video"}:
        return _extract_execution_trace_from_video_logs(output_dir)
    if task_type == "replace_productANDmodel":
        return _extract_execution_trace_from_union_logs(output_dir)
    return []


def _emit_batch_item_output_event(
    payload: dict[str, Any],
    *,
    item_index: int,
    item_id: str,
    result: dict[str, Any],
) -> None:
    event_data = {
        "stage": "batch_item_output",
        "status": "success" if _count_batch_success(result) else "failed",
        "source": "webapp",
        "user_visible": True,
        "item_index": int(item_index),
        "item_id": str(item_id or f"item_{item_index}"),
        "output_snapshot": _sanitize_log_payload(result),
    }
    _emit_task_event(
        task_id=str(payload.get("_task_id") or ""),
        user_id=int(_to_int(payload.get("_user_id"), 0)),
        kind="log",
        message="批量子项输出",
        data=event_data,
    )


def _iter_usage_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if any(k in value for k in ("consumeCoins", "consumeMoney", "thirdPartyConsumeMoney")):
            yield value
        for v in value.values():
            yield from _iter_usage_dicts(v)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_usage_dicts(item)


def _sum_usage(usages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    total = {
        "consumeCoins": 0.0,
        "consumeMoney": 0.0,
        "thirdPartyConsumeMoney": 0.0,
    }
    found = False
    for usage in usages:
        found = True
        total["consumeCoins"] += _to_float(usage.get("consumeCoins"), 0.0)
        total["consumeMoney"] += _to_float(usage.get("consumeMoney"), 0.0)
        total["thirdPartyConsumeMoney"] += _to_float(usage.get("thirdPartyConsumeMoney"), 0.0)
    if not found:
        return {}
    return {
        "consumeCoins": round(total["consumeCoins"], 6),
        "consumeMoney": round(total["consumeMoney"], 6),
        "thirdPartyConsumeMoney": round(total["thirdPartyConsumeMoney"], 6),
    }


def _merge_usage_values(*values: Any) -> dict[str, Any]:
    usage_items: list[dict[str, Any]] = []
    for value in values:
        usage_items.extend(_iter_usage_dicts(value))
    return _sum_usage(usage_items)


def _extract_runninghub_task_id(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    return str(
        result.get("runninghub_task_id")
        or result.get("task_id")
        or result.get("task id")
        or ""
    ).strip()


def _extract_runninghub_task_ids(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    return _normalize_workflow_ids(
        [
            _extract_runninghub_task_id(result),
            *(
                result.get("runninghub_task_ids")
                if isinstance(result.get("runninghub_task_ids"), list)
                else []
            ),
        ]
    )


def _prefixed_logger(logger: Any, prefix: str):
    if not callable(logger):
        return logger
    prefix_text = str(prefix or "").strip()
    if not prefix_text:
        return logger

    def _wrapped(message: Any) -> None:
        logger(f"{prefix_text}{message}")

    return _wrapped


def _build_task_workdir(task_id: str, fallback_username: str | None = None) -> Path:
    user_dir = str(fallback_username or "").strip() or "unknown"
    with db() as conn:
        row = conn.execute(
            """
            SELECT u.username AS username
            FROM tasks t
            JOIN users u ON u.id = t.user_id
            WHERE t.id = ?
            """,
            (str(task_id),),
        ).fetchone()
    if row is not None:
        user_dir = str(row["username"] or "").strip() or "unknown"
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", user_dir).strip("._-") or "user"
    workdir = OUTPUT_ROOT / safe / task_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _copytree_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists() or not src.is_dir():
        return False
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    return True


def _get_pricing_config(conn) -> dict[str, Any]:
    raw = get_admin_config(conn, "pricing", DEFAULT_PRICING)
    merged = dict(DEFAULT_PRICING)
    if isinstance(raw, dict):
        for k in list(merged.keys()):
            if k in raw:
                merged[k] = raw.get(k)
    merged["rh_coins_per_10rmb"] = max(_to_int(merged.get("rh_coins_per_10rmb"), 2500), 1)
    merged["usd_to_rmb"] = max(_to_float(merged.get("usd_to_rmb"), 7.2), 0.01)
    merged["gemini_input_usd_per_1m"] = max(_to_float(merged.get("gemini_input_usd_per_1m"), 4.0), 0.0)
    merged["gemini_output_usd_per_1m"] = max(_to_float(merged.get("gemini_output_usd_per_1m"), 18.0), 0.0)
    merged["nano_usd_per_image"] = max(_to_float(merged.get("nano_usd_per_image"), 0.134), 0.0)
    merged["allow_negative_balance"] = False
    return merged


def _get_runtime_config(conn) -> dict[str, Any]:
    return _load_runtime_config_file(conn)


def _load_legacy_runtime_config(conn) -> dict[str, Any] | None:
    if conn is None:
        return None
    current = get_admin_config(conn, "runtime_config", None)
    if isinstance(current, dict):
        return current
    return None


def _write_runtime_config_file(config: dict[str, Any]) -> None:
    payload = _normalize_runtime_config(config)
    tmp_path = RUNTIME_CONFIG_PATH.with_name(f"{RUNTIME_CONFIG_PATH.name}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, RUNTIME_CONFIG_PATH)
    except Exception as exc:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise RuntimeConfigFileError(f"写入运行配置文件失败: {exc}") from exc


def _read_runtime_config_file_dict() -> dict[str, Any]:
    try:
        text = RUNTIME_CONFIG_PATH.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise RuntimeConfigFileError(f"读取运行配置文件失败: {exc}") from exc
    try:
        data = json.loads(text)
    except Exception as exc:
        raise RuntimeConfigFileError(f"运行配置文件 JSON 损坏: {RUNTIME_CONFIG_PATH}") from exc
    if not isinstance(data, dict):
        raise RuntimeConfigFileError(f"运行配置文件必须是 JSON 对象: {RUNTIME_CONFIG_PATH}")
    return data


def _load_runtime_config_file(conn) -> dict[str, Any]:
    with _RUNTIME_CONFIG_LOCK:
        try:
            raw = _read_runtime_config_file_dict()
        except FileNotFoundError:
            raw = _load_legacy_runtime_config(conn) or dict(DEFAULT_RUNTIME_CONFIG)
            merged = _normalize_runtime_config(raw)
            _write_runtime_config_file(merged)
            return merged
        merged = _normalize_runtime_config(raw)
        if merged != raw:
            _write_runtime_config_file(merged)
        return merged


def _normalize_runtime_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    current = raw if isinstance(raw, dict) else {}
    merged = dict(DEFAULT_RUNTIME_CONFIG)
    for k in list(merged.keys()):
        if k in current:
            merged[k] = current.get(k)
    merged["create_video_app_id"] = str(merged.get("create_video_app_id") or merged.get("video_app_id") or "").strip()
    merged["video_app_id"] = str(merged.get("video_app_id") or merged.get("create_video_app_id") or "").strip()
    merged["create_audio_app_id"] = str(merged.get("create_audio_app_id") or "").strip()
    legacy_replace_model_app_id = str(current.get("replace_model_app_id") or "").strip()
    explicit_original_app_id = str(current.get("replace_model_original_app_id") or "").strip()
    merged["replace_model_original_app_id"] = str(explicit_original_app_id or legacy_replace_model_app_id or "").strip()
    merged["replace_model_app_id"] = merged["replace_model_original_app_id"]
    for legacy_workflow_key in (
        "create_video_app_id",
        "create_audio_app_id",
        "video_app_id",
        "replace_model_app_id",
        "replace_model_original_app_id",
        "replace_product_app_id",
    ):
        merged[legacy_workflow_key] = ""
    merged["remote_comfy_gateway_url"] = str(merged.get("remote_comfy_gateway_url") or "").strip().rstrip("/")
    merged["remote_comfy_gateway_token"] = str(merged.get("remote_comfy_gateway_token") or "").strip()
    raw_remote_mappings = current.get("remote_comfy_workflow_mappings")
    remote_mappings: dict[str, str] = {}
    if isinstance(raw_remote_mappings, dict):
        for key, value in raw_remote_mappings.items():
            task_key = str(key or "").strip()
            workflow_path = str(value or "").strip()
            if task_key and workflow_path:
                remote_mappings[task_key] = workflow_path
    merged["remote_comfy_workflow_mappings"] = remote_mappings
    merged["image_generate_mode_default"] = str(merged.get("image_generate_mode_default") or "closed_model_api").strip() or "closed_model_api"
    if merged["image_generate_mode_default"] not in {"closed_model_api"}:
        merged["image_generate_mode_default"] = "closed_model_api"
    merged["image_model_provider_base_url"] = str(merged.get("image_model_provider_base_url") or BUILTIN_IMAGE_MODEL_PROVIDER_BASE_URL).strip() or BUILTIN_IMAGE_MODEL_PROVIDER_BASE_URL
    merged["image_model_provider_api_key_gemini"] = str(merged.get("image_model_provider_api_key_gemini") or BUILTIN_IMAGE_MODEL_PROVIDER_API_KEY_GEMINI).strip()
    merged["image_model_provider_api_key_gpt"] = str(merged.get("image_model_provider_api_key_gpt") or BUILTIN_IMAGE_MODEL_PROVIDER_API_KEY_GPT).strip()
    merged["image_model_default_model"] = str(merged.get("image_model_default_model") or "gemini-3-pro-image-preview").strip() or "gemini-3-pro-image-preview"
    image_model_default_model_gemini = current.get("image_model_default_model_gemini") if "image_model_default_model_gemini" in current else None
    image_model_default_model_gpt = current.get("image_model_default_model_gpt") if "image_model_default_model_gpt" in current else None
    if image_model_default_model_gemini is None and image_model_default_model_gpt is None:
        image_model_default_model_gemini = merged.get("image_model_default_model")
        image_model_default_model_gpt = ""
    merged["image_model_default_model_gemini"] = str(image_model_default_model_gemini or "").strip()
    merged["image_model_default_model_gpt"] = str(image_model_default_model_gpt or "").strip()
    merged["llm_base_url"] = str(merged.get("llm_base_url") or BUILTIN_LLM_BASE_URL).strip() or BUILTIN_LLM_BASE_URL
    llm_api_key_gemini = current.get("llm_api_key_gemini") if "llm_api_key_gemini" in current else None
    llm_api_key_gpt = current.get("llm_api_key_gpt") if "llm_api_key_gpt" in current else None
    llm_api_key_legacy = str(merged.get("llm_api_key") or "").strip()
    if llm_api_key_gemini is None and llm_api_key_gpt is None:
        llm_api_key_gemini = llm_api_key_legacy or BUILTIN_LLM_API_KEY_GEMINI
        llm_api_key_gpt = BUILTIN_LLM_API_KEY_GPT
    merged["llm_api_key_gemini"] = str(llm_api_key_gemini or "").strip()
    merged["llm_api_key_gpt"] = str(llm_api_key_gpt or "").strip()
    merged["llm_api_key"] = str(merged["llm_api_key_gemini"] or merged["llm_api_key_gpt"] or llm_api_key_legacy or BUILTIN_LLM_API_KEY).strip()
    merged["llm_default_model"] = str(merged.get("llm_default_model") or "gemini-3.1-pro-preview").strip() or "gemini-3.1-pro-preview"
    llm_default_model_gemini = current.get("llm_default_model_gemini") if "llm_default_model_gemini" in current else None
    llm_default_model_gpt = current.get("llm_default_model_gpt") if "llm_default_model_gpt" in current else None
    if llm_default_model_gemini is None and llm_default_model_gpt is None:
        llm_default_model_gemini = merged.get("llm_default_model")
        llm_default_model_gpt = ""
    merged["llm_default_model_gemini"] = str(llm_default_model_gemini or "").strip()
    merged["llm_default_model_gpt"] = str(llm_default_model_gpt or "").strip()
    llm_model_priority_order = current.get("llm_model_priority_order") if "llm_model_priority_order" in current else None
    llm_gemini_models = parse_model_list(merged.get("llm_default_model_gemini"))
    llm_gpt_models = parse_model_list(merged.get("llm_default_model_gpt"))
    if not llm_gemini_models and not llm_gpt_models:
        llm_gemini_models = ["gemini-3.1-pro-preview"]
    merged["llm_default_model_gemini"] = ", ".join(llm_gemini_models)
    merged["llm_default_model_gpt"] = ", ".join(llm_gpt_models)
    merged["llm_default_model"] = ", ".join(llm_gemini_models or llm_gpt_models or ["gemini-3.1-pro-preview"])
    llm_priority_candidates = parse_model_list(llm_model_priority_order)
    llm_priority_models = _build_model_priority(
        explicit_models=[],
        priority_models=llm_priority_candidates,
        gemini_models=llm_gemini_models,
        gpt_models=llm_gpt_models,
        legacy_models=parse_model_list(merged.get("llm_default_model")),
        builtin_model="gemini-3.1-pro-preview",
    )
    merged["llm_model_priority_order"] = ", ".join(llm_priority_models)

    merged["mulerouter_api_name"] = str(merged.get("mulerouter_api_name") or "").strip()
    merged["mulerouter_api_key"] = str(merged.get("mulerouter_api_key") or "").strip()
    merged["mulerouter_base_url"] = str(merged.get("mulerouter_base_url") or "https://api.mulerouter.ai").strip().rstrip("/") or "https://api.mulerouter.ai"
    endpoint = str(merged.get("mulerouter_wan_i2v_endpoint") or "/vendors/carrothub/v1/wan2.7-i2v-spicy/generation").strip()
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    merged["mulerouter_wan_i2v_endpoint"] = endpoint
    resolution = str(merged.get("mulerouter_wan_i2v_resolution") or "720p").strip()
    merged["mulerouter_wan_i2v_resolution"] = resolution if resolution in {"720p", "1080p"} else "720p"
    merged["mulerouter_wan_i2v_duration"] = min(max(_to_int(merged.get("mulerouter_wan_i2v_duration"), 2), 2), 15)
    merged["mulerouter_wan_i2v_prompt_extend"] = _to_bool(merged.get("mulerouter_wan_i2v_prompt_extend"), False)
    merged["mulerouter_wan_i2v_negative_prompt"] = str(merged.get("mulerouter_wan_i2v_negative_prompt") or "").strip()

    image_model_priority_order = current.get("image_model_priority_order") if "image_model_priority_order" in current else None
    image_gemini_models = parse_model_list(merged.get("image_model_default_model_gemini"))
    image_gpt_models = parse_model_list(merged.get("image_model_default_model_gpt"))
    if not image_gemini_models and not image_gpt_models:
        image_gemini_models = ["gemini-3-pro-image-preview"]
    merged["image_model_default_model_gemini"] = ", ".join(image_gemini_models)
    merged["image_model_default_model_gpt"] = ", ".join(image_gpt_models)
    merged["image_model_default_model"] = ", ".join(image_gemini_models or image_gpt_models or ["gemini-3-pro-image-preview"])
    image_priority_candidates = parse_model_list(image_model_priority_order)
    image_priority_models = _build_model_priority(
        explicit_models=[],
        priority_models=image_priority_candidates,
        gemini_models=image_gemini_models,
        gpt_models=image_gpt_models,
        legacy_models=parse_model_list(merged.get("image_model_default_model")),
        builtin_model="gemini-3-pro-image-preview",
    )
    merged["image_model_priority_order"] = ", ".join(image_priority_models)
    oral_chain = _normalize_runtime_workflow_chain(current.get("oral_digital_human_workflow_ids"))
    oral_chain = [value for value in oral_chain if not _workflow_stage_runninghub_id(value)]
    merged["oral_digital_human_workflow_ids"] = oral_chain
    if oral_chain:
        runninghub_oral_chain = [value for value in oral_chain if _workflow_stage_runninghub_id(value)]
        if runninghub_oral_chain:
            merged["create_audio_app_id"] = runninghub_oral_chain[0]
            merged["create_video_app_id"] = runninghub_oral_chain[-1]
            merged["video_app_id"] = runninghub_oral_chain[-1]

    digital_human_chain = _normalize_runtime_workflow_chain(current.get("digital_human_workflow_ids"))
    merged["digital_human_workflow_ids"] = digital_human_chain

    image_chain = _normalize_runtime_workflow_chain(current.get("image_generate_workflow_ids"))
    image_chain = [value for value in image_chain if not _workflow_stage_runninghub_id(value)]
    merged["image_generate_workflow_ids"] = image_chain

    replace_model_original_chain = _normalize_runtime_workflow_chain(current.get("replace_model_original_workflow_ids"))
    replace_model_original_chain = [value for value in replace_model_original_chain if not _workflow_stage_runninghub_id(value)]
    merged["replace_model_original_workflow_ids"] = replace_model_original_chain

    replace_product_chain = _normalize_runtime_workflow_chain(current.get("replace_product_workflow_ids"))
    replace_product_chain = [value for value in replace_product_chain if not _workflow_stage_runninghub_id(value)]
    merged["replace_product_workflow_ids"] = replace_product_chain

    replace_union_model_chain = _normalize_runtime_workflow_chain(current.get("replace_union_model_workflow_ids"))
    replace_union_model_chain = [value for value in replace_union_model_chain if not _workflow_stage_runninghub_id(value)]
    merged["replace_union_model_workflow_ids"] = replace_union_model_chain

    replace_union_product_chain = _normalize_runtime_workflow_chain(current.get("replace_union_product_workflow_ids"))
    replace_union_product_chain = [value for value in replace_union_product_chain if not _workflow_stage_runninghub_id(value)]
    merged["replace_union_product_workflow_ids"] = replace_union_product_chain

    merged["cleanup_enabled"] = _to_bool(merged.get("cleanup_enabled"), True)
    merged["cleanup_time"] = str(merged.get("cleanup_time") or "03:30").strip() or "03:30"
    merged["cleanup_retention_days"] = max(_to_int(merged.get("cleanup_retention_days"), 7), 1)
    return merged


def _normalize_runtime_workflow_chain(value: Any) -> list[str]:
    if isinstance(value, list):
        normalized_items: list[str] = []
        for item in value:
            if isinstance(item, dict):
                stage_type = str(item.get("type") or item.get("provider") or "").strip()
                stage_value = str(item.get("value") or item.get("model") or item.get("workflow_id") or item.get("id") or "").strip()
                if stage_type in {"closed_image_model", "closed_model_api", "closed_model", "image_model"}:
                    closed_stage = _make_closed_image_workflow_stage(stage_value)
                    if closed_stage:
                        normalized_items.append(closed_stage)
                    continue
                if stage_type in {"closed_llm_model", "closed_text_model", "llm_model", "text_model"}:
                    closed_stage = _make_closed_llm_workflow_stage(stage_value)
                    if closed_stage:
                        normalized_items.append(closed_stage)
                    continue
                if stage_value:
                    normalized_items.append(stage_value)
                continue
            normalized_items.append(str(item or "").strip())
        return _normalize_workflow_ids(normalized_items)
    if isinstance(value, tuple):
        return _normalize_workflow_ids(list(value))
    if isinstance(value, str):
        normalized = (
            value.replace("->", ",")
            .replace(">", ",")
            .replace("，", ",")
            .replace("\r", ",")
            .replace("\n", ",")
        )
        return _normalize_workflow_ids(normalized.split(","))
    return []


def _backup_runtime_config_file() -> Path | None:
    if not RUNTIME_CONFIG_PATH.exists():
        return None
    backup = RUNTIME_CONFIG_PATH.with_name(f"{RUNTIME_CONFIG_PATH.stem}.broken-{_now_ts()}{RUNTIME_CONFIG_PATH.suffix}")
    os.replace(RUNTIME_CONFIG_PATH, backup)
    return backup


def _asset_version(*relative_parts: str) -> str:
    path = STATIC_DIR.joinpath(*relative_parts)
    try:
        stat_result = path.stat()
    except FileNotFoundError:
        return "missing"
    return f"{int(stat_result.st_mtime)}-{stat_result.st_size}"


def _html_response_with_versions(filename: str, replacements: dict[str, str] | None = None) -> HTMLResponse:
    html = (STATIC_DIR / filename).read_text(encoding="utf-8")
    for key, value in (replacements or {}).items():
        html = html.replace(key, value)
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _ensure_default_pricing() -> None:
    with db() as conn:
        current = get_admin_config(conn, "pricing", None)
        if not isinstance(current, dict):
            set_admin_config(conn, "pricing", DEFAULT_PRICING, _now_ts())


def _ensure_default_runtime_config() -> None:
    with db() as conn:
        with _RUNTIME_CONFIG_LOCK:
            try:
                _load_runtime_config_file(conn)
            except RuntimeConfigFileError:
                _backup_runtime_config_file()
                raw = _load_legacy_runtime_config(conn) or dict(DEFAULT_RUNTIME_CONFIG)
                _write_runtime_config_file(_normalize_runtime_config(raw))


def _ensure_admin_seed() -> None:
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        if row and int(row["c"]) > 0:
            return
        now = _now_ts()
        conn.execute(
            """
            INSERT INTO users(username, password_hash, is_admin, is_disabled, balance_cents, created_at, updated_at)
            VALUES (?, ?, 1, 0, 0, ?, ?)
            """,
            ("admin", hash_password("admin123"), now, now),
        )


def _ensure_user_can_access_task(user: dict[str, Any], task_row: dict[str, Any]) -> None:
    if int(user.get("is_admin") or 0) == 1:
        return
    if int(task_row.get("user_id") or 0) != int(user.get("id") or 0):
        raise HTTPException(status_code=404, detail="任务不存在")


def _task_type_label(task_type: Any) -> str:
    mapping = {
        "create_video": "创建视频",
        "commerce_video": "商业视频生成",
        "create_audio": "创建音频",
        "replace_model": "视频模特替换",
        "replace_product": "视频商品替换",
        "replace_productANDmodel": "联合替换商品和模特",
        "get_nano_banana": "闭源图片模型",
        "get_gemini": "Gemini 分析",
        "batch_create_video": "批量创建视频",
        "batch_replace_model": "批量视频模特替换",
        "batch_replace_product": "批量视频商品替换",
    }
    key = str(task_type or "").strip()
    return mapping.get(key, key or "未知工作流")


def _replace_model_mode_label(mode: Any) -> str:
    normalized = replace_model.normalize_mode(str(mode or ""))
    mapping = {
        replace_model.MODE_ORIGINAL: "原版工作流",
        replace_model.MODE_PRIMARY: "主要工作流",
        replace_model.MODE_SLICE: "切片工作流",
        replace_model.MODE_MOTION_TRANSFER: "动作迁移工作流",
    }
    return mapping.get(normalized, "原版工作流")


def _normalize_replace_model_mode(payload: dict[str, Any] | None) -> str:
    source = payload if isinstance(payload, dict) else {}
    return replace_model.normalize_mode(source.get("mode"))


def _replace_model_runtime_app_id(runtime: dict[str, Any], mode: Any) -> str:
    normalized = replace_model.normalize_mode(str(mode or ""))
    if normalized == replace_model.MODE_PRIMARY:
        return str(runtime.get("replace_model_primary_app_id") or replace_model.PRIMARY_APP_ID).strip() or replace_model.PRIMARY_APP_ID
    if normalized == replace_model.MODE_SLICE:
        return str(runtime.get("replace_model_slice_app_id") or replace_model.SLICE_APP_ID).strip() or replace_model.SLICE_APP_ID
    if normalized == replace_model.MODE_MOTION_TRANSFER:
        return str(runtime.get("replace_model_motion_transfer_app_id") or replace_model.MOTION_TRANSFER_APP_ID).strip() or replace_model.MOTION_TRANSFER_APP_ID
    return str(
        runtime.get("replace_model_original_app_id")
        or runtime.get("replace_model_app_id")
        or replace_model.LEGACY_DEFAULT_APP_ID
    ).strip() or replace_model.LEGACY_DEFAULT_APP_ID


def _normalize_replace_model_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(payload or {})
    mode = _normalize_replace_model_mode(merged)
    merged["mode"] = mode
    if mode == replace_model.MODE_PRIMARY:
        merged.pop("prompt", None)
        merged.pop("frame", None)
        merged.pop("duration_seconds", None)
        merged.pop("start_seconds", None)
    elif mode == replace_model.MODE_SLICE:
        merged.pop("width", None)
        merged.pop("height", None)
        merged.pop("frame", None)
        merged["start_seconds"] = max(_to_int(merged.get("start_seconds"), 0), 0)
        merged["duration_seconds"] = max(_to_int(merged.get("duration_seconds"), 5), 1)
    elif mode == replace_model.MODE_MOTION_TRANSFER:
        merged.pop("prompt", None)
        merged.pop("frame", None)
        merged.pop("duration_seconds", None)
        merged.pop("start_seconds", None)
    else:
        merged.pop("start_seconds", None)
        merged["duration_seconds"] = max(_to_int(merged.get("duration_seconds"), 10), 1)
        merged["frame"] = max(_to_int(merged.get("frame"), 30), 1)
        merged["width"] = max(_to_int(merged.get("width"), 576), 1)
        merged["height"] = max(_to_int(merged.get("height"), 1024), 1)
    if mode in {replace_model.MODE_PRIMARY, replace_model.MODE_MOTION_TRANSFER}:
        merged["width"] = max(_to_int(merged.get("width"), 1280), 1)
        merged["height"] = max(_to_int(merged.get("height"), 720), 1)
    return merged


def _normalize_workflow_ids(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _is_closed_image_workflow_stage(value: Any) -> bool:
    return str(value or "").strip().startswith(CLOSED_IMAGE_WORKFLOW_STAGE_PREFIX)


def _is_closed_llm_workflow_stage(value: Any) -> bool:
    return str(value or "").strip().startswith(CLOSED_LLM_WORKFLOW_STAGE_PREFIX)


def _closed_image_workflow_stage_model(value: Any) -> str:
    text = str(value or "").strip()
    if not text.startswith(CLOSED_IMAGE_WORKFLOW_STAGE_PREFIX):
        return ""
    return text[len(CLOSED_IMAGE_WORKFLOW_STAGE_PREFIX) :].strip()


def _closed_llm_workflow_stage_model(value: Any) -> str:
    text = str(value or "").strip()
    if not text.startswith(CLOSED_LLM_WORKFLOW_STAGE_PREFIX):
        return ""
    return text[len(CLOSED_LLM_WORKFLOW_STAGE_PREFIX) :].strip()


def _make_closed_image_workflow_stage(model: Any) -> str:
    text = str(model or "").strip()
    return f"{CLOSED_IMAGE_WORKFLOW_STAGE_PREFIX}{text}" if text else ""


def _make_closed_llm_workflow_stage(model: Any) -> str:
    text = str(model or "").strip()
    return f"{CLOSED_LLM_WORKFLOW_STAGE_PREFIX}{text}" if text else ""


def _workflow_stage_display_id(value: Any) -> str:
    text = str(value or "").strip()
    if _is_closed_image_workflow_stage(text):
        model = _closed_image_workflow_stage_model(text)
        return f"闭源图片模型:{model}" if model else "闭源图片模型"
    if _is_closed_llm_workflow_stage(text):
        model = _closed_llm_workflow_stage_model(text)
        return f"闭源文字模型:{model}" if model else "闭源文字模型"
    return text


def _workflow_stage_runninghub_id(value: Any) -> str:
    text = str(value or "").strip()
    if _is_closed_image_workflow_stage(text) or _is_closed_llm_workflow_stage(text):
        return ""
    return text


def _last_runninghub_workflow_id(values: Iterable[Any]) -> str:
    for value in reversed(list(values or [])):
        workflow_id = _workflow_stage_runninghub_id(value)
        if workflow_id:
            return workflow_id
    return ""


def _replace_model_chain_key(mode: Any) -> str:
    normalized = replace_model.normalize_mode(str(mode or ""))
    if normalized == replace_model.MODE_PRIMARY:
        return "replace_model_primary_workflow_ids"
    if normalized == replace_model.MODE_SLICE:
        return "replace_model_slice_workflow_ids"
    if normalized == replace_model.MODE_MOTION_TRANSFER:
        return "replace_model_motion_transfer_workflow_ids"
    return "replace_model_original_workflow_ids"


def _workflow_chain_from_payload(payload: dict[str, Any] | None, key: str, fallback_values: Iterable[Any] = ()) -> list[str]:
    source = payload if isinstance(payload, dict) else {}
    chain = _normalize_runtime_workflow_chain(source.get(key))
    if chain:
        return chain
    return _normalize_workflow_ids(fallback_values)


def _build_workflow_chain_summary(*, task_type: str, payload: dict[str, Any], workflow_ids: list[str]) -> tuple[str, int]:
    ids = _normalize_workflow_ids(workflow_ids)
    total_steps = len(ids)
    if task_type in {"create_video", "commerce_video", "batch_create_video", "create_audio"}:
        digital_chain = _workflow_chain_from_payload(payload, "digital_human_workflow_ids", [])
        oral_chain = _workflow_chain_from_payload(
            payload,
            "oral_digital_human_workflow_ids",
            [payload.get("create_audio_app_id"), payload.get("video_app_id"), payload.get("create_video_app_id")],
        )
        if not oral_chain:
            return ("", 0)
        runninghub_steps = sum(1 for value in oral_chain if _workflow_stage_runninghub_id(value))
        llm_steps = sum(1 for value in oral_chain if _is_closed_llm_workflow_stage(value))
        image_steps = sum(1 for value in oral_chain if _is_closed_image_workflow_stage(value))
        audio_steps = 1 if runninghub_steps else 0
        video_steps = max(runninghub_steps - 1, 0)
        if task_type == "create_audio":
            return (f"口播音频链 {audio_steps} 步", audio_steps)
        if llm_steps or image_steps:
            parts = []
            if llm_steps:
                parts.append(f"闭源文字 {llm_steps}")
            if image_steps:
                parts.append(f"闭源图片 {image_steps}")
            if runninghub_steps:
                parts.append(f"RunningHub {runninghub_steps}")
            return (f"口播链 {len(oral_chain)} 步（{' + '.join(parts)}）", len(oral_chain))
        return (f"口播链 {len(oral_chain)} 步（音频 {audio_steps} + 视频 {video_steps}）", len(oral_chain))
    if task_type in {"replace_model", "batch_replace_model"}:
        closed_steps = sum(1 for value in ids if _is_closed_image_workflow_stage(value))
        if closed_steps and total_steps:
            return (f"视频模特替换链 {total_steps} 步（闭源图片 {closed_steps} + RunningHub {total_steps - closed_steps}）", total_steps)
        return (f"视频模特替换链 {total_steps} 步", total_steps) if total_steps else ("", 0)
    if task_type in {"replace_product", "batch_replace_product"}:
        closed_steps = sum(1 for value in ids if _is_closed_image_workflow_stage(value))
        if closed_steps and total_steps:
            return (f"视频商品替换链 {total_steps} 步（闭源图片 {closed_steps} + RunningHub {total_steps - closed_steps}）", total_steps)
        return (f"视频商品替换链 {total_steps} 步", total_steps) if total_steps else ("", 0)
    if task_type == "replace_productANDmodel":
        model_chain = _workflow_chain_from_payload(payload, "model_workflow_chain_ids", [payload.get("model_app_id")])
        product_chain = _workflow_chain_from_payload(payload, "product_workflow_chain_ids", [payload.get("product_app_id")])
        model_steps = len(model_chain)
        product_steps = len(product_chain)
        total = model_steps + product_steps
        if total <= 0:
            return ("", 0)
        closed_steps = sum(1 for value in [*model_chain, *product_chain] if _is_closed_image_workflow_stage(value))
        suffix = f"（闭源图片 {closed_steps}）" if closed_steps else ""
        return (f"联合链 模特 {model_steps} 步 + 商品 {product_steps} 步{suffix}", total)
    if task_type == "image_generate":
        provider = str(payload.get("image_generate_provider") or payload.get("image_generate_mode_default") or "closed_model_api").strip() or "closed_model_api"
        if provider == "closed_model_api":
            return ("闭源图像编辑模型", 0)
        closed_steps = sum(1 for value in ids if _is_closed_image_workflow_stage(value))
        if closed_steps and total_steps:
            return (f"图像编辑链 {total_steps} 步（闭源模型 {closed_steps} + RunningHub {total_steps - closed_steps}）", total_steps)
        return (f"图像编辑链 {total_steps} 步", total_steps) if total_steps else ("", 0)
    return (f"{total_steps} 步" if total_steps > 0 else "", total_steps)


def _build_workflow_meta(*, task_id: str, task_type: str, input_payload: Any, output_payload: Any, runninghub_task_id: Any) -> dict[str, Any]:
    payload = input_payload if isinstance(input_payload, dict) else {}
    output = output_payload if isinstance(output_payload, dict) else {}
    batch_defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
    replace_model_payload = payload
    if task_type == "batch_replace_model" and batch_defaults:
        replace_model_payload = batch_defaults

    workflow_name = _task_type_label(task_type)
    workflow_ids: list[str] = []
    model_app_id = str(payload.get("model_app_id") or "").strip()
    product_app_id = str(payload.get("product_app_id") or "").strip()
    workflow_mode = ""
    workflow_mode_label = ""

    if task_type in {"create_video", "commerce_video", "batch_create_video"}:
        digital_chain = _workflow_chain_from_payload(payload, "digital_human_workflow_ids", [])
        workflow_ids = _workflow_chain_from_payload(
            payload,
            "oral_digital_human_workflow_ids",
            [payload.get("create_audio_app_id"), payload.get("video_app_id"), payload.get("create_video_app_id")],
        )
    elif task_type == "create_audio":
        workflow_ids = _normalize_workflow_ids(
            [
                *(_workflow_chain_from_payload(payload, "oral_digital_human_workflow_ids", [payload.get("create_audio_app_id")])[:1]),
                payload.get("create_audio_app_id"),
            ]
        )
    elif task_type in {"replace_model", "batch_replace_model"}:
        workflow_mode = _normalize_replace_model_mode(replace_model_payload)
        workflow_mode_label = _replace_model_mode_label(workflow_mode)
        workflow_name = f"{workflow_name}（{workflow_mode_label}）"
        workflow_ids = _workflow_chain_from_payload(
            replace_model_payload,
            _replace_model_chain_key(workflow_mode),
            [replace_model_payload.get("workflow_chain_ids"), replace_model_payload.get("app_id"), replace_model_payload.get("model_app_id")],
        )
    elif task_type in {"replace_product", "batch_replace_product"}:
        workflow_ids = _workflow_chain_from_payload(
            payload,
            "replace_product_workflow_ids",
            [payload.get("workflow_chain_ids"), payload.get("app_id"), payload.get("product_app_id")],
        )
    elif task_type == "replace_productANDmodel":
        workflow_ids = _normalize_workflow_ids(
            [
                *_workflow_chain_from_payload(payload, "model_workflow_chain_ids", [model_app_id]),
                *_workflow_chain_from_payload(payload, "product_workflow_chain_ids", [product_app_id]),
            ]
        )
        if workflow_ids:
            workflow_name = "联合替换商品和模特"
    elif task_type == "get_nano_banana":
        workflow_ids = _normalize_workflow_ids(["gemini-3-pro-image-preview"])
    elif task_type == "image_generate":
        provider = str(payload.get("image_generate_provider") or payload.get("image_generate_mode_default") or "closed_model_api").strip() or "closed_model_api"
        model_name = str(payload.get("image_generate_model") or payload.get("image_model_default_model") or "").strip()
        workflow_ids = _normalize_workflow_ids([model_name])

    runninghub_ids = _normalize_workflow_ids(
        [
            runninghub_task_id,
            output.get("runninghub_task_id"),
            *(
                output.get("runninghub_task_ids")
                if isinstance(output.get("runninghub_task_ids"), list)
                else []
            ),
        ]
    )

    workflow_id = ", ".join(workflow_ids)
    workflow_chain_summary, workflow_step_count = _build_workflow_chain_summary(
        task_type=str(task_type or "").strip(),
        payload=payload,
        workflow_ids=workflow_ids,
    )
    workflow_ids = [_workflow_stage_display_id(value) for value in workflow_ids]
    workflow_id = ", ".join(workflow_ids)
    return {
        "task_id": str(task_id or "").strip(),
        "task_type": str(task_type or "").strip(),
        "workflow_name": workflow_name,
        "workflow_id": workflow_id,
        "workflow_ids": workflow_ids,
        "workflow_chain_summary": workflow_chain_summary,
        "workflow_step_count": int(workflow_step_count),
        "workflow_mode": workflow_mode,
        "workflow_mode_label": workflow_mode_label,
        "runninghub_task_id": runninghub_ids[0] if runninghub_ids else "",
        "runninghub_task_ids": runninghub_ids,
    }


def _attach_workflow_meta_to_payload(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload or {})
    meta = _build_workflow_meta(
        task_id="",
        task_type=str(task_type or "").strip(),
        input_payload=merged,
        output_payload={},
        runninghub_task_id="",
    )
    merged["workflow_name"] = meta.get("workflow_name") or ""
    merged["workflow_id"] = meta.get("workflow_id") or ""
    merged["workflow_ids"] = list(meta.get("workflow_ids") or [])
    merged["workflow_chain_summary"] = meta.get("workflow_chain_summary") or ""
    merged["workflow_step_count"] = int(_to_int(meta.get("workflow_step_count"), 0))
    merged["workflow_mode"] = meta.get("workflow_mode") or ""
    merged["workflow_mode_label"] = meta.get("workflow_mode_label") or ""
    return merged


def _get_task_log_context(conn, task_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, user_id, type, input_json, output_json, runninghub_task_id
        FROM tasks
        WHERE id = ?
        """,
        (str(task_id or "").strip(),),
    ).fetchone()
    if row is None:
        return {}
    task = dict(row)
    return _build_workflow_meta(
        task_id=str(task.get("id") or ""),
        task_type=str(task.get("type") or ""),
        input_payload=_json_loads(task.get("input_json"), {}),
        output_payload=_json_loads(task.get("output_json"), {}),
        runninghub_task_id=task.get("runninghub_task_id"),
    )


def _merge_task_log_meta(base: Any, extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base) if isinstance(base, dict) else {}
    for key, value in (extra or {}).items():
        if key in {"workflow_ids", "runninghub_task_ids"}:
            current = merged.get(key)
            if isinstance(current, list) and current:
                continue
            merged[key] = list(value) if isinstance(value, list) else []
            continue
        if str(merged.get(key) or "").strip():
            continue
        merged[key] = value
    return merged


def _serialize_task_event_record(*, task: dict[str, Any], event_row: Any) -> dict[str, Any]:
    event = dict(event_row) if not isinstance(event_row, dict) else dict(event_row)
    data = _json_loads(event.get("data_json"), {})
    meta = _build_workflow_meta(
        task_id=str(task.get("id") or ""),
        task_type=str(task.get("type") or ""),
        input_payload=_json_loads(task.get("input_json"), {}),
        output_payload=_json_loads(task.get("output_json"), {}),
        runninghub_task_id=task.get("runninghub_task_id"),
    )
    merged_data = _merge_task_log_meta(_normalize_task_event_data(str(event.get("kind") or ""), str(event.get("message") or ""), data), meta)
    return {
        "id": int(event.get("id") or 0),
        "kind": str(event.get("kind") or ""),
        "message": str(event.get("message") or ""),
        "data": merged_data,
        "created_at": int(event.get("created_at") or 0),
    }


def _extract_latest_analysis_summary(events: list[dict[str, Any]]) -> str:
    for payload in reversed(list(events or [])):
        if str(payload.get("kind") or "").strip().lower() != "analysis":
            continue
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        text = str(data.get("summary") or data.get("analysis_summary") or payload.get("message") or "").strip()
        if text:
            return text
    return ""


def _build_task_logs_export_lines(*, task_detail: dict[str, Any], username: str, events: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for payload in events:
        line = dict(payload)
        line["task"] = {
            "id": task_detail["id"],
            "user_id": task_detail["user_id"],
            "username": str(username or ""),
            "type": task_detail["type"],
            "status": task_detail["status"],
            "workflow_name": task_detail.get("workflow_name"),
            "workflow_id": task_detail.get("workflow_id"),
            "workflow_ids": task_detail.get("workflow_ids"),
            "runninghub_task_id": task_detail.get("runninghub_task_id"),
            "runninghub_task_ids": task_detail.get("runninghub_task_ids"),
            "created_at": task_detail.get("created_at"),
            "updated_at": task_detail.get("updated_at"),
            "cost_cents": task_detail.get("cost_cents"),
            "has_download": bool(task_detail.get("has_download")),
            "total_count": int(task_detail.get("total_count") or 0),
            "success_count": int(task_detail.get("success_count") or 0),
            "failed_count": int(task_detail.get("failed_count") or 0),
            "first_error": str(task_detail.get("first_error") or ""),
            "analysis_summary": str(task_detail.get("analysis_summary") or ""),
        }
        lines.append(json.dumps(line, ensure_ascii=False))
    return lines


def _load_task_events(conn, *, task: dict[str, Any], limit: int = 1000) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, kind, message, data_json, created_at
        FROM task_events
        WHERE task_id = ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (str(task.get("id") or ""), min(max(int(limit or 1000), 1), 5000)),
    ).fetchall()
    return [_serialize_task_event_record(task=task, event_row=row) for row in rows]


def _build_task_detail_payload(*, task: dict[str, Any], include_logs: bool = True, log_limit: int = 1000) -> dict[str, Any]:
    workflow_meta = _build_workflow_meta(
        task_id=str(task.get("id") or ""),
        task_type=str(task.get("type") or ""),
        input_payload=_json_loads(task.get("input_json"), {}),
        output_payload=_json_loads(task.get("output_json"), {}),
        runninghub_task_id=task.get("runninghub_task_id"),
    )
    raw_input = _json_loads(task.get("input_json"), {})
    raw_output = _json_loads(task.get("output_json"), {})
    safe_input = _sanitize_payload(raw_input)
    safe_output = _sanitize_payload(raw_output)
    execution_trace = _build_task_execution_trace(task_type=str(task.get("type") or ""), output_data=raw_output)
    logs: list[dict[str, Any]] = []
    runtime: dict[str, Any] = {}
    with db() as conn:
        runtime = _get_runtime_config(conn)
        if include_logs:
            logs = _load_task_events(conn, task=task, limit=log_limit)
    batch_summary = _extract_batch_summary(safe_output)
    has_download = _task_has_download_file(_json_loads(task.get("output_json"), {}))
    return {
        "id": task["id"],
        "user_id": int(task["user_id"]),
        "type": task["type"],
        "status": task["status"],
        "error": task["error"],
        "runninghub_task_id": task["runninghub_task_id"],
        "cost_cents": int(task["cost_cents"] or 0),
        "input": safe_input,
        "output": safe_output,
        "usage": _json_loads(task.get("usage_json"), {}),
        "created_at": int(task["created_at"]),
        "updated_at": int(task["updated_at"]),
        "workflow_name": workflow_meta.get("workflow_name"),
        "workflow_id": workflow_meta.get("workflow_id"),
        "workflow_ids": workflow_meta.get("workflow_ids"),
        "workflow_chain_summary": workflow_meta.get("workflow_chain_summary"),
        "workflow_step_count": int(_to_int(workflow_meta.get("workflow_step_count"), 0)),
        "runninghub_task_ids": workflow_meta.get("runninghub_task_ids"),
        "execution_trace": execution_trace,
        "has_download": bool(has_download),
        "error_analysis_available": _error_analysis_available(runtime),
        "logs": logs,
        "analysis_summary": _extract_latest_analysis_summary(logs),
        **batch_summary,
    }


def _insert_ledger(conn, *, user_id: int, typ: str, amount_cents: int, ref_task_id: str, meta: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO ledger(id, user_id, type, amount_cents, ref_task_id, meta_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _new_id("ledger"),
            int(user_id),
            str(typ),
            int(amount_cents),
            str(ref_task_id or ""),
            _json_dumps(meta),
            _now_ts(),
        ),
    )


def _apply_runtime_defaults(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload or {})
    with db() as conn:
        runtime = _get_runtime_config(conn)

    def _looks_like_runninghub_workflow_id(value: Any) -> bool:
        text = str(value or "").strip()
        return bool(re.fullmatch(r"\d{10,}", text))

    secret_keys = {
        "upload_file_api_key",
        "image_model_provider_api_key_gemini",
        "image_model_provider_api_key_gpt",
        "llm_api_key",
        "llm_api_key_gemini",
        "llm_api_key_gpt",
        "remote_comfy_gateway_token",
        "mulerouter_api_key",
    }
    runtime_fill_keys = [
        "remote_comfy_gateway_url",
        "remote_comfy_gateway_token",
        "upload_server_ip",
        "upload_file_api_key",
        "image_generate_mode_default",
        "image_model_provider_base_url",
        "image_model_provider_api_key_gemini",
        "image_model_provider_api_key_gpt",
        "image_model_default_model",
        "image_model_default_model_gemini",
        "image_model_default_model_gpt",
        "image_model_priority_order",
        "llm_base_url",
        "llm_api_key",
        "llm_api_key_gemini",
        "llm_api_key_gpt",
        "llm_default_model",
        "llm_default_model_gemini",
        "llm_default_model_gpt",
        "llm_model_priority_order",
        "mulerouter_api_name",
        "mulerouter_api_key",
        "mulerouter_base_url",
        "mulerouter_wan_i2v_endpoint",
        "mulerouter_wan_i2v_resolution",
        "mulerouter_wan_i2v_duration",
        "mulerouter_wan_i2v_prompt_extend",
        "mulerouter_wan_i2v_negative_prompt",
    ]
    for key in runtime_fill_keys:
        current_value = str(merged.get(key) or "").strip()
        if key in secret_keys and "***" in current_value:
            merged[key] = runtime.get(key)
            continue
        if not current_value:
            merged[key] = runtime.get(key)
    if not isinstance(merged.get("remote_comfy_workflow_mappings"), dict):
        merged["remote_comfy_workflow_mappings"] = runtime.get("remote_comfy_workflow_mappings") if isinstance(runtime.get("remote_comfy_workflow_mappings"), dict) else {}

    if task_type in {"create_video", "commerce_video", "batch_create_video", "create_audio"}:
        oral_chain = _workflow_chain_from_payload(
            merged,
            "oral_digital_human_workflow_ids",
            [
                merged.get("create_audio_app_id"),
                merged.get("video_app_id"),
                merged.get("create_video_app_id"),
                runtime.get("create_audio_app_id"),
                runtime.get("create_video_app_id"),
                runtime.get("video_app_id"),
            ],
        )
        if oral_chain:
            merged["oral_digital_human_workflow_ids"] = oral_chain
        runninghub_oral_chain = [value for value in oral_chain if _workflow_stage_runninghub_id(value)]
        if task_type == "create_audio":
            if runninghub_oral_chain and (not str(merged.get("create_audio_app_id") or "").strip()):
                merged["create_audio_app_id"] = runninghub_oral_chain[0]
        else:
            if runninghub_oral_chain and (not str(merged.get("create_audio_app_id") or "").strip()):
                merged["create_audio_app_id"] = runninghub_oral_chain[0]
            if not str(merged.get("video_app_id") or "").strip():
                if runninghub_oral_chain:
                    merged["video_app_id"] = runninghub_oral_chain[-1]
                else:
                    merged["video_app_id"] = runtime.get("create_video_app_id") or runtime.get("video_app_id")
            if not str(merged.get("create_video_app_id") or "").strip():
                merged["create_video_app_id"] = merged.get("video_app_id") or runtime.get("create_video_app_id") or runtime.get("video_app_id")
        digital_chain = _workflow_chain_from_payload(
            merged,
            "digital_human_workflow_ids",
            runtime.get("digital_human_workflow_ids") or [],
        )
        if digital_chain:
            merged["digital_human_workflow_ids"] = digital_chain
    if task_type == "image_generate":
        mode = str(merged.get("mode") or "product_only").strip() or "product_only"
        if mode not in {"product_only", "model_product"}:
            merged["mode"] = "product_only"
        if str(merged.get("image_generate_provider") or "").strip() == "runninghub_workflow":
            merged["image_generate_provider"] = "closed_model_api"
        merged["image_generate_workflow_ids"] = []
        if not str(merged.get("create_audio_app_id") or "").strip():
            merged["create_audio_app_id"] = runtime.get("create_audio_app_id")
    if task_type == "replace_model":
        merged = _normalize_replace_model_payload(merged)
        chain_key = _replace_model_chain_key(merged.get("mode"))
        workflow_chain = _workflow_chain_from_payload(
            merged,
            chain_key,
            [merged.get("workflow_chain_ids"), merged.get("app_id"), _replace_model_runtime_app_id(runtime, merged.get("mode"))],
        )
        if workflow_chain:
            merged[chain_key] = workflow_chain
            merged["workflow_chain_ids"] = workflow_chain
        app_id = str(merged.get("app_id") or "").strip()
        if (not app_id) or (not _looks_like_runninghub_workflow_id(app_id)):
            merged["app_id"] = _last_runninghub_workflow_id(workflow_chain) or _replace_model_runtime_app_id(runtime, merged.get("mode"))
    if task_type == "replace_product":
        workflow_chain = _workflow_chain_from_payload(
            merged,
            "replace_product_workflow_ids",
            [merged.get("workflow_chain_ids"), merged.get("app_id"), runtime.get("replace_product_app_id")],
        )
        if workflow_chain:
            merged["replace_product_workflow_ids"] = workflow_chain
            merged["workflow_chain_ids"] = workflow_chain
        app_id = str(merged.get("app_id") or "").strip()
        if (not app_id) or (not _looks_like_runninghub_workflow_id(app_id)):
            merged["app_id"] = _last_runninghub_workflow_id(workflow_chain) or runtime.get("replace_product_app_id")
    if task_type == "replace_productANDmodel":
        runtime_union_model_chain = _normalize_runtime_workflow_chain(runtime.get("replace_union_model_workflow_ids"))
        runtime_union_product_chain = _normalize_runtime_workflow_chain(runtime.get("replace_union_product_workflow_ids"))
        model_chain = _workflow_chain_from_payload(
            merged,
            "model_workflow_chain_ids",
            runtime_union_model_chain or [merged.get("model_app_id"), runtime.get("replace_model_original_app_id")],
        )
        product_chain = _workflow_chain_from_payload(
            merged,
            "product_workflow_chain_ids",
            runtime_union_product_chain or [merged.get("product_app_id"), runtime.get("replace_product_app_id")],
        )
        if model_chain:
            merged["model_workflow_chain_ids"] = model_chain
        if product_chain:
            merged["product_workflow_chain_ids"] = product_chain
        model_app_id = str(merged.get("model_app_id") or "").strip()
        product_app_id = str(merged.get("product_app_id") or "").strip()
        if (not model_app_id) or (not _looks_like_runninghub_workflow_id(model_app_id)):
            merged["model_app_id"] = _last_runninghub_workflow_id(model_chain) or _replace_model_runtime_app_id(runtime, replace_model.MODE_ORIGINAL)
        if (not product_app_id) or (not _looks_like_runninghub_workflow_id(product_app_id)):
            merged["product_app_id"] = _last_runninghub_workflow_id(product_chain) or runtime.get("replace_product_app_id")
    return _attach_workflow_meta_to_payload(task_type, merged)


def _upload_binary_to_runninghub(*, api_key: str, file_path: Path, media_kind: str) -> str:
    url = f"{str(runninghub_common.BASE_URL).rstrip('/')}/openapi/v2/media/upload/binary"
    headers = {"Authorization": f"Bearer {api_key}"}
    with file_path.open("rb") as f:
        response = requests.post(url, headers=headers, files={"file": f}, timeout=120)
    payload = response.json()
    if not isinstance(payload, dict) or int(payload.get("code", -1)) != 0:
        raise RuntimeError(f"上传媒体失败: {runninghub_common._safe_json_preview(payload)}")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"上传媒体失败: {runninghub_common._safe_json_preview(payload)}")
    suffix = file_path.suffix.lower()
    file_name = str(data.get("fileName") or "").strip()
    download_url = str(data.get("download_url") or "").strip()
    kind = str(media_kind or "").strip().lower()
    if suffix in IMAGE_EXTS and download_url:
        if download_url.startswith("http"):
            return download_url
        return f"{str(runninghub_common.BASE_URL).rstrip('/')}/{download_url.lstrip('/')}"
    if kind in {"video", "audio", "camera_video"} and file_name:
        return file_name
    if not download_url and file_name:
        return file_name
    if not download_url:
        raise RuntimeError(f"上传媒体失败: {runninghub_common._safe_json_preview(payload)}")
    if download_url.startswith("http"):
        return download_url
    return f"{str(runninghub_common.BASE_URL).rstrip('/')}/{download_url.lstrip('/')}"


def _parse_upload_port(port_value: Any) -> int:
    port = _to_int(port_value, 0)
    if port <= 0 or port > 65535:
        raise RuntimeError(f"upload_server_port 不合法: {port_value}")
    return port


def _build_upload_remote_path(*, task_id: str, media_kind: str, local_file_path: Path) -> str:
    suffix = local_file_path.suffix.lower() or ".bin"
    kind = re.sub(r"[^a-z0-9_-]+", "_", str(media_kind or "asset").lower())
    kind = kind.strip("_") or "asset"
    return f"scene/{task_id}/{kind}_{uuid.uuid4().hex[:12]}{suffix}"


def _upload_file_to_public_server(
    *,
    task_id: str,
    media_kind: str,
    local_file_path: Path,
    server_ip: str,
    server_port: int,
) -> str:
    last_result: Any = None
    last_exc: Exception | None = None
    for attempt in range(1, 6):
        try:
            result = asset_uploader.upload_file(
                server_ip=server_ip,
                server_port=server_port,
                local_path=str(local_file_path),
                remote_path=_build_upload_remote_path(task_id=task_id, media_kind=media_kind, local_file_path=local_file_path),
            )
            last_result = result
            status = str(result.get("statu") or "").strip().lower() if isinstance(result, dict) else ""
            url = str(result.get("path") or "").strip() if isinstance(result, dict) else ""
            if status == "success" and url:
                return url
        except Exception as exc:
            last_exc = exc
        if attempt < 5:
            time.sleep(min(2 ** (attempt - 1), 8))
    if last_exc is not None:
        raise RuntimeError(f"上传素材到公网服务失败（已重试 5 次）: {last_exc}") from last_exc
    detail = runninghub_common._safe_json_preview(last_result)
    raise RuntimeError(f"上传素材到公网服务失败（已重试 5 次）: {detail}")


def _resolve_media_url(
    *,
    task_id: str,
    media_kind: str,
    api_key: str,
    local_path: str | None,
    remote_url: str | None,
    upload_server_ip: str | None = None,
    upload_server_port: str | int | None = None,
    upload_file_api_key: str | None = None,
) -> str:
    remote = str(remote_url or "").strip()
    if remote:
        return remote
    local = str(local_path or "").strip()
    if not local:
        raise RuntimeError("缺少本地文件或 URL")
    path = Path(local).resolve()
    if not path.exists():
        raise FileNotFoundError(f"本地文件不存在: {path}")
    upload_api_key = str(upload_file_api_key or "").strip() or str(api_key or "").strip()
    if path.suffix.lower() in IMAGE_EXTS:
        return _upload_binary_to_runninghub(api_key=upload_api_key, file_path=path, media_kind=media_kind)
    server_ip = str(upload_server_ip or "").strip()
    server_port_text = str(upload_server_port or "").strip()
    if server_ip and server_port_text:
        return _upload_file_to_public_server(
            task_id=task_id,
            media_kind=media_kind,
            local_file_path=path,
            server_ip=server_ip,
            server_port=_parse_upload_port(server_port_text),
        )
    return _upload_binary_to_runninghub(api_key=upload_api_key, file_path=path, media_kind=media_kind)


def _download_to_file(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=180) as resp:
        resp.raise_for_status()
        with output_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)


def _mulerouter_url(base_url: str, endpoint: str) -> str:
    base = str(base_url or "").strip().rstrip("/") or "https://api.mulerouter.ai"
    path = str(endpoint or "").strip() or "/vendors/carrothub/v1/wan2.7-i2v-spicy/generation"
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def _image_file_to_mulerouter_base64(image_path: str, workdir: Path) -> tuple[str, Path]:
    src = Path(str(image_path or "")).expanduser()
    if not src.exists() or not src.is_file():
        raise RuntimeError(f"图生视频参考图不存在: {src}")
    target = workdir / "mulerouter_input.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(src) as img:
            rgb = img.convert("RGB")
            max_side = max(rgb.size or (0, 0))
            if max_side > 1600:
                rgb.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            rgb.save(target, format="JPEG", quality=90, optimize=True)
    except Exception as exc:
        raise RuntimeError(f"图生视频参考图处理失败: {exc}") from exc
    if target.stat().st_size > 20 * 1024 * 1024:
        raise RuntimeError("图生视频参考图超过 MuleRouter 20MB 限制")
    return base64.b64encode(target.read_bytes()).decode("ascii"), target


def _run_mulerouter_wan_i2v(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    api_key = str(payload.get("mulerouter_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("MuleRouter 图生视频需要配置 API Key")
    base_url = str(payload.get("mulerouter_base_url") or "https://api.mulerouter.ai").strip().rstrip("/")
    endpoint = str(payload.get("mulerouter_wan_i2v_endpoint") or "/vendors/carrothub/v1/wan2.7-i2v-spicy/generation").strip()
    create_url = _mulerouter_url(base_url, endpoint)
    prompt = str(payload.get("prompt_text") or payload.get("prompt") or payload.get("message") or "").strip()
    if not prompt:
        raise RuntimeError("MuleRouter 图生视频需要 prompt")
    workdir = _build_task_workdir(task_id, fallback_username="telegram")
    image_b64, normalized_image = _image_file_to_mulerouter_base64(str(payload.get("image_local_path") or payload.get("input_image_local_path") or ""), workdir)
    resolution = str(payload.get("mulerouter_wan_i2v_resolution") or payload.get("resolution") or "720p").strip()
    if resolution not in {"720p", "1080p"}:
        resolution = "720p"
    duration = min(max(_to_int(payload.get("mulerouter_wan_i2v_duration") or payload.get("duration_seconds"), 2), 2), 15)
    prompt_extend = _to_bool(payload.get("mulerouter_wan_i2v_prompt_extend", payload.get("prompt_extend")), False)
    negative_prompt = str(payload.get("mulerouter_wan_i2v_negative_prompt") or payload.get("negative_prompt") or "").strip()
    seed_raw = payload.get("seed")
    seed = None if str(seed_raw or "").strip() in {"", "auto", "None", "null"} else min(max(_to_int(seed_raw, 0), 0), 2147483647)
    request_body: dict[str, Any] = {
        "prompt": prompt,
        "image": image_b64,
        "negative_prompt": negative_prompt,
        "resolution": resolution,
        "duration": duration,
        "prompt_extend": prompt_extend,
        "seed": seed,
    }
    audio_url = str(payload.get("audio_url") or "").strip()
    if audio_url:
        request_body["audio_url"] = audio_url
    request_log = dict(request_body)
    request_log["image"] = f"base64:{normalized_image.name}:{normalized_image.stat().st_size} bytes"
    provider_meta = {
        "provider": "mulerouter",
        "api_name": str(payload.get("mulerouter_api_name") or "").strip(),
        "base_url": base_url,
        "endpoint": endpoint,
        "create_url": create_url,
        "api_key_masked": _mask_secret(api_key),
        "request": request_log,
    }
    _emit_stage(payload, stage="mulerouter_request", status="running", message="正在提交 MuleRouter 图生视频请求", data=provider_meta)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(create_url, headers=headers, json=request_body, timeout=120)
        response_json = resp.json() if resp.content else {}
    except Exception as exc:
        raise RuntimeError(f"MuleRouter 图生视频提交失败: {exc}") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"MuleRouter 图生视频提交失败 HTTP {resp.status_code}: {json.dumps(_sanitize_payload(response_json), ensure_ascii=False)[:800]}")
    task_info = response_json.get("task_info") if isinstance(response_json, dict) else {}
    mule_task_id = str((task_info or {}).get("id") or response_json.get("id") or "").strip()
    if not mule_task_id:
        raise RuntimeError(f"MuleRouter 图生视频未返回 task_id: {json.dumps(_sanitize_payload(response_json), ensure_ascii=False)[:800]}")
    _emit_stage(payload, stage="mulerouter_task", status="running", message=f"MuleRouter 任务已创建: {mule_task_id}", data={"mulerouter_task_id": mule_task_id, "response": _sanitize_payload(response_json), **provider_meta})
    poll_url = create_url.rstrip("/") + f"/{mule_task_id}"
    final_json: dict[str, Any] = {}
    status = ""
    deadline = time.time() + max(_to_int(payload.get("timeout_seconds"), 1800), 60)
    while time.time() < deadline:
        time.sleep(max(_to_float(payload.get("poll_interval_seconds"), 8.0), 2.0))
        resp = requests.get(poll_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
        try:
            final_json = resp.json() if resp.content else {}
        except Exception:
            final_json = {"raw": resp.text[:800]}
        if resp.status_code >= 400:
            raise RuntimeError(f"MuleRouter 图生视频查询失败 HTTP {resp.status_code}: {json.dumps(_sanitize_payload(final_json), ensure_ascii=False)[:800]}")
        task_info = final_json.get("task_info") if isinstance(final_json, dict) else {}
        status = str((task_info or {}).get("status") or final_json.get("status") or "").strip().lower()
        _emit_stage(payload, stage="mulerouter_poll", status="running", message=f"MuleRouter 状态: {status or 'unknown'}", data={"mulerouter_task_id": mule_task_id, "status": status})
        if status == "completed":
            break
        if status == "failed":
            error_detail = (task_info or {}).get("error") if isinstance(task_info, dict) else None
            raise RuntimeError(f"MuleRouter 图生视频失败: {json.dumps(error_detail or final_json, ensure_ascii=False)[:800]}")
    if status != "completed":
        raise RuntimeError(f"MuleRouter 图生视频超时，最后状态: {status or 'unknown'}")
    videos = final_json.get("videos") if isinstance(final_json, dict) else []
    video_url = str((videos or [""])[0] or "").strip() if isinstance(videos, list) else ""
    if not video_url:
        raise RuntimeError(f"MuleRouter 图生视频完成但未返回视频 URL: {json.dumps(final_json, ensure_ascii=False)[:800]}")
    suffix = Path(urlsplit(video_url).path).suffix or ".mp4"
    output_path = workdir / f"mulerouter_wan_i2v{suffix}"
    _emit_stage(payload, stage="download", status="running", message="正在下载 MuleRouter 视频结果", data={"mulerouter_task_id": mule_task_id, "video_url": video_url})
    _download_to_file(video_url, output_path)
    return {
        "ok": True,
        "message": "MuleRouter 图生视频完成",
        "download_path": str(output_path),
        "video_path": str(output_path),
        "mulerouter_task_id": mule_task_id,
        "mulerouter": _sanitize_payload({**provider_meta, "poll_url": poll_url, "response": final_json}),
        "skip_billing": True,
        "billing": {"mode": "external_mulerouter", "cost_cents": 0},
    }


def _json_object_from_text(text: Any, *, label: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"{label} 必须是 JSON 对象") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{label} 必须是 JSON 对象")
    return parsed


def _local_file_to_data_uri(path: Path) -> str:
    import base64
    import mimetypes

    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('utf-8')}"


def _extract_request_id(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("request_id", "requestId", "id", "job_id", "jobId", "task_id", "taskId"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        for key in ("data", "result"):
            nested = data.get(key)
            value = _extract_request_id(nested)
            if value:
                return value
    return ""


def _extract_nested_url(data: Any, suffixes: set[str]) -> str:
    if isinstance(data, dict):
        for key in ("url", "uri", "download_url", "downloadUrl", "file_url", "fileUrl", "image_url", "imageUrl", "video_url", "videoUrl"):
            value = str(data.get(key) or "").strip()
            if value and (not suffixes or any(value.lower().split("?", 1)[0].endswith(suffix) for suffix in suffixes)):
                return value
        for value in data.values():
            found = _extract_nested_url(value, suffixes)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _extract_nested_url(item, suffixes)
            if found:
                return found
    elif isinstance(data, str):
        value = data.strip()
        if value.startswith("http") and (not suffixes or any(value.lower().split("?", 1)[0].endswith(suffix) for suffix in suffixes)):
            return value
    return ""


def _extract_status_url(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("status_url", "statusUrl"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        for key in ("urls", "links", "data"):
            nested = data.get(key)
            value = _extract_status_url(nested)
            if value:
                return value
    return ""


def _extract_result_url(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("result_url", "resultUrl", "output_url", "outputUrl"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        for key in ("urls", "links", "data"):
            nested = data.get(key)
            value = _extract_result_url(nested)
            if value:
                return value
    return ""


def _normalize_remote_comfy_gateway_url(gateway_url: str) -> str:
    cleaned = str(gateway_url or "").strip().rstrip("/")
    if not cleaned:
        raise ValueError("远程 ComfyUI 网关地址不能为空")
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("远程 ComfyUI 网关地址必须是 http 或 https URL")
    return cleaned


def _remote_comfy_gateway_headers(token: str) -> dict[str, str]:
    cleaned = str(token or "").strip()
    if not cleaned:
        raise ValueError("远程 ComfyUI 网关 Token 不能为空")
    return {
        "Authorization": f"Bearer {cleaned}",
        "Accept": "application/json",
    }


def _remote_comfy_gateway_health(*, gateway_url: str, token: str) -> dict[str, Any]:
    root = _normalize_remote_comfy_gateway_url(gateway_url)
    headers = _remote_comfy_gateway_headers(token)
    try:
        response = requests.get(f"{root}/api/health", headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"远程 ComfyUI 网关检测失败: {exc}") from exc
    except Exception as exc:
        raise RuntimeError("远程 ComfyUI 网关返回的不是有效 JSON") from exc
    return data if isinstance(data, dict) else {"raw": data}


def _remote_comfy_gateway_json(
    *,
    gateway_url: str,
    token: str,
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    root = _normalize_remote_comfy_gateway_url(gateway_url)
    headers = _remote_comfy_gateway_headers(token)
    endpoint = f"{root}/{str(path or '').lstrip('/')}"
    try:
        response = requests.request(
            str(method or "GET").upper(),
            endpoint,
            headers=headers,
            json=json_body,
            timeout=max(int(timeout or 60), 1),
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"远程 ComfyUI 网关请求失败: {exc}") from exc
    except Exception as exc:
        raise RuntimeError("远程 ComfyUI 网关返回的不是有效 JSON") from exc
    return data if isinstance(data, dict) else {"raw": data}


def _remote_comfy_gateway_download_output(
    *,
    gateway_url: str,
    token: str,
    file_item: dict[str, Any],
    output_dir: Path,
) -> Path:
    filename = Path(str(file_item.get("filename") or "output.bin")).name
    if not filename:
        filename = "output.bin"
    params = {
        "filename": filename,
        "subfolder": str(file_item.get("subfolder") or ""),
        "type": str(file_item.get("type") or "output"),
    }
    root = _normalize_remote_comfy_gateway_url(gateway_url)
    headers = _remote_comfy_gateway_headers(token)
    response = requests.get(f"{root}/api/view", headers=headers, params=params, timeout=120)
    response.raise_for_status()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / filename
    if target.exists():
        target = output_dir / f"{target.stem}_{uuid.uuid4().hex[:8]}{target.suffix}"
    target.write_bytes(response.content)
    return target


def _run_remote_comfy_gateway_test(
    *,
    gateway_url: str,
    token: str,
    workflow_path: str,
    prompt_text: str,
    negative_prompt: str = "",
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    batch_size: int | None = None,
    node_inputs: dict[str, Any] | None = None,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "path": str(workflow_path or "").strip(),
        "prompt_text": str(prompt_text or "").strip() or "a simple red apple on a wooden table, studio lighting",
        "negative_prompt": str(negative_prompt or "").strip(),
    }
    for key, value in {
        "width": width,
        "height": height,
        "steps": steps,
        "batch_size": batch_size,
    }.items():
        if value is not None:
            body[key] = int(value)
    if isinstance(node_inputs, dict) and node_inputs:
        body["node_inputs"] = node_inputs
    submitted = _remote_comfy_gateway_json(
        gateway_url=gateway_url,
        token=token,
        method="POST",
        path="/api/workflows/run",
        json_body=body,
        timeout=90,
    )
    prompt_id = str(submitted.get("prompt_id") or "").strip()
    if not prompt_id:
        raise RuntimeError(f"远程 ComfyUI 未返回 prompt_id: {submitted}")
    deadline = time.time() + max(int(timeout_seconds or 900), 30)
    last_job: dict[str, Any] = {}
    while time.time() < deadline:
        last_job = _remote_comfy_gateway_json(
            gateway_url=gateway_url,
            token=token,
            method="GET",
            path=f"/api/jobs?prompt_id={prompt_id}",
            timeout=60,
        )
        if _to_bool(last_job.get("done"), False):
            outputs = last_job.get("outputs") if isinstance(last_job.get("outputs"), list) else []
            local_outputs: list[dict[str, Any]] = []
            output_dir = OUTPUT_ROOT / "remote_comfy_tests" / prompt_id
            for item in outputs:
                if not isinstance(item, dict):
                    continue
                try:
                    local_path = _remote_comfy_gateway_download_output(
                        gateway_url=gateway_url,
                        token=token,
                        file_item=item,
                        output_dir=output_dir,
                    )
                    local_outputs.append({**item, "local_path": str(local_path)})
                except Exception as exc:
                    local_outputs.append({**item, "download_error": str(exc)})
            return {
                "ok": True,
                "prompt_id": prompt_id,
                "outputs": outputs,
                "local_outputs": local_outputs,
                "raw_submit": submitted,
                "raw_job": last_job,
            }
        time.sleep(5)
    return {"ok": False, "prompt_id": prompt_id, "message": "远程 ComfyUI 测试超时", "raw_job": last_job}


REMOTE_COMFY_TASK_LABELS = {
    "text_to_image": "文字生成图片",
    "image_generate": "图片生成",
    "replace_model": "替换模特",
    "replace_product": "替换商品",
    "replace_productANDmodel": "联合替换",
    "create_audio": "生成音频",
    "create_video": "生成视频",
    "commerce_video": "带货视频",
    "get_nano_banana": "图片编辑",
}


def _remote_comfy_workflow_mapping(payload: dict[str, Any], task_type: str) -> str:
    mappings = payload.get("remote_comfy_workflow_mappings")
    if not isinstance(mappings, dict):
        mappings = {}
    candidates = [
        payload.get("remote_comfy_workflow_path"),
        mappings.get(task_type),
        mappings.get("default"),
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _remote_comfy_prompt_from_payload(task_type: str, payload: dict[str, Any]) -> str:
    candidates = [
        payload.get("prompt_text"),
        payload.get("prompt"),
        payload.get("message"),
        payload.get("user_input"),
        payload.get("style_hint"),
        payload.get("product_name"),
        payload.get("speech_text"),
    ]
    if isinstance(payload.get("model_params"), dict):
        candidates.append(payload["model_params"].get("image_prompt"))
    if isinstance(payload.get("product_params"), dict):
        candidates.append(payload["product_params"].get("image_prompt"))
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return f"{REMOTE_COMFY_TASK_LABELS.get(task_type, task_type)} test generation, high quality"


def _remote_comfy_node_inputs_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("remote_comfy_node_inputs")
    if isinstance(raw, dict):
        return raw
    raw_json = str(payload.get("remote_comfy_node_inputs_json") or "").strip()
    if not raw_json:
        return {}
    parsed = _json_loads(raw_json, {})
    return parsed if isinstance(parsed, dict) else {}


def _first_remote_comfy_output_path(result: dict[str, Any]) -> str:
    outputs = result.get("local_outputs") if isinstance(result.get("local_outputs"), list) else []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        local_path = str(item.get("local_path") or "").strip()
        if local_path and Path(local_path).exists():
            return local_path
    return ""


def _run_remote_comfy_mapped_task(task_id: str, payload: dict[str, Any], task_type: str) -> dict[str, Any]:
    gateway_url = str(payload.get("remote_comfy_gateway_url") or "").strip()
    token = str(payload.get("remote_comfy_gateway_token") or "").strip()
    workflow_path = _remote_comfy_workflow_mapping(payload, task_type)
    if not gateway_url or not token:
        raise RuntimeError("远程 ComfyUI 网关未配置，请先在后台保存网关地址和 Token")
    if not workflow_path:
        raise RuntimeError(f"{REMOTE_COMFY_TASK_LABELS.get(task_type, task_type)} 未映射远程 ComfyUI 工作流")

    prompt_text = _remote_comfy_prompt_from_payload(task_type, payload)
    negative_prompt = str(payload.get("negative_prompt") or payload.get("negative") or "low quality, blurry, distorted").strip()
    steps = _to_int(payload.get("steps"), 6)
    width = _to_int(payload.get("width"), 512)
    height = _to_int(payload.get("height"), 512)
    batch_size = _to_int(payload.get("batch_size"), 1)
    _emit_stage(payload, stage="remote_comfy", status="running", message=f"提交远程 ComfyUI 工作流: {workflow_path}")
    result = _run_remote_comfy_gateway_test(
        gateway_url=gateway_url,
        token=token,
        workflow_path=workflow_path,
        prompt_text=prompt_text,
        negative_prompt=negative_prompt,
        width=width if width > 0 else None,
        height=height if height > 0 else None,
        steps=steps if steps > 0 else None,
        batch_size=batch_size if batch_size > 0 else None,
        node_inputs=_remote_comfy_node_inputs_from_payload(payload),
        timeout_seconds=max(_to_int(payload.get("remote_comfy_timeout_seconds"), 900), 30),
    )
    if not _to_bool(result.get("ok"), False):
        raise RuntimeError(str(result.get("message") or "远程 ComfyUI 工作流执行失败"))
    output_path = _first_remote_comfy_output_path(result)
    output_key = "download_path"
    suffix = Path(output_path).suffix.lower() if output_path else ""
    if suffix in IMAGE_EXTS:
        output_key = "image_path"
    elif suffix in VIDEO_EXTS:
        output_key = "video_path"
    elif suffix in AUDIO_EXTS:
        output_key = "audio_path"
    output: dict[str, Any] = {
        "ok": True,
        "message": "远程 ComfyUI 工作流完成",
        "remote_comfy_prompt_id": str(result.get("prompt_id") or "").strip(),
        "remote_comfy_workflow_path": workflow_path,
        "runninghub_task_id": str(result.get("prompt_id") or "").strip(),
        "runninghub_usage": {},
        "download_path": output_path,
        "raw_result": result,
    }
    if output_path:
        output[output_key] = output_path
    return output


def _resolve_digital_human_reference_image(task_id: str, payload: dict[str, Any], workdir: Path) -> Path:
    generated = str(payload.get("generated_scene_image_local_path") or "").strip()
    if generated:
        path = Path(generated).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"数字人场景图不存在: {path}")
        return path

    model_local = str(payload.get("model_image_local_path") or "").strip()
    product_local = str(payload.get("product_image_local_path") or "").strip()
    if model_local and product_local:
        model_path = Path(model_local).resolve()
        product_path = Path(product_local).resolve()
        if not model_path.exists() or not product_path.exists():
            raise FileNotFoundError("数字人工作流输入图片不存在")
        return _compose_reference_image(
            model_image=model_path,
            product_image=product_path,
            output_path=workdir / "digital_human_reference.png",
        )
    if product_local:
        product_path = Path(product_local).resolve()
        if not product_path.exists():
            raise FileNotFoundError(f"商品图不存在: {product_path}")
        return product_path
    if model_local:
        model_path = Path(model_local).resolve()
        if not model_path.exists():
            raise FileNotFoundError(f"模特图不存在: {model_path}")
        return model_path
    raise RuntimeError("数字人工作流需要上传模特图、商品图或提供 generated_scene_image_local_path")


def _classify_runninghub_image_generate_failure(query_result: Any) -> str:
    data = query_result if isinstance(query_result, dict) else {}
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    status_text = str(data.get("status") or raw.get("status") or "").strip().lower()
    error_code = str(data.get("errorCode") or raw.get("errorCode") or data.get("error_code") or raw.get("error_code") or "").strip()
    error_message = str(data.get("errorMessage") or raw.get("errorMessage") or data.get("message") or raw.get("message") or "").strip()
    failed_reason = data.get("failedReason") if isinstance(data.get("failedReason"), dict) else raw.get("failedReason") if isinstance(raw.get("failedReason"), dict) else {}
    exception_type = str(failed_reason.get("exception_type") or "").strip()
    audit_msg = str(failed_reason.get("msg") or "").strip()
    node_name = str(failed_reason.get("node_name") or "").strip()
    raw_preview = json.dumps(query_result, ensure_ascii=False)[:500]
    if status_text == "failed" and (
        exception_type == "audit.RHAuditException"
        or audit_msg.lower() == "porn"
        or error_code == "805"
    ):
        detail_parts = [part for part in [node_name, audit_msg or error_message] if part]
        detail_text = f"（{' / '.join(detail_parts)}）" if detail_parts else ""
        return f"图片疑似触发平台审核{detail_text}，请更换素材或弱化提示词后重试"
    if error_message:
        return f"RunningHub 图片生成失败：{error_message}"
    return f"RunningHub 图像编辑查询失败: {raw_preview}"




def _run_image_generate_via_runninghub_workflow(task_id: str, payload: dict[str, Any], *, ref_input: Path, prompt_text: str, mode: str) -> dict[str, Any]:
    runninghub_api_key = str(payload.get("runninghub_api_key") or "").strip()
    workflow_ids = _workflow_chain_from_payload(
        payload,
        "image_generate_workflow_ids",
        [payload.get("image_runninghub_workflow_id")],
    )
    needs_runninghub = any(not _is_closed_image_workflow_stage(item) for item in workflow_ids)
    if needs_runninghub and not runninghub_api_key:
        raise RuntimeError("RunningHub 图像编辑工作流需要 runninghub_api_key")
    if not workflow_ids:
        raise RuntimeError("RunningHub 图像编辑工作流需要 image_runninghub_workflow_id")

    product_name = str(payload.get("product_name") or "商品").strip() or "商品"
    replace_target = str(payload.get("replace_target_name") or product_name).strip() or product_name
    output_height = max(_to_int(payload.get("output_height_limit"), 1980), 256)
    extra_prompt = str(payload.get("style_hint") or prompt_text).strip()

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {runninghub_api_key}"}
    workdir = _build_task_workdir(task_id)
    final_path = workdir / "image_generate_output.png"
    step_results: list[dict[str, Any]] = []
    runninghub_task_ids: list[str] = []
    current_output_path = final_path
    current_input_path = Path(ref_input).resolve()
    query_result = None
    submit_result = None
    for idx, workflow_id in enumerate(workflow_ids, start=1):
        current_output_path = final_path if idx == len(workflow_ids) else workdir / f"image_generate_step_{idx:02d}.png"
        if _is_closed_image_workflow_stage(workflow_id):
            model_name = _closed_image_workflow_stage_model(workflow_id)
            current_output_path, closed_result, model = _run_closed_image_model_transform(
                input_path=current_input_path,
                output_path=current_output_path,
                payload=payload,
                model_name=model_name,
                prompt_text=prompt_text,
                logger=_prefixed_logger(payload.get("_event_logger"), f"[图像编辑链 {idx}/{len(workflow_ids)} 闭源模型] "),
            )
            step_results.append(
                {
                    "step": idx,
                    "provider": "closed_image_model",
                    "workflow_id": _workflow_stage_display_id(workflow_id),
                    "model": model,
                    "result": closed_result,
                    "output_path": str(current_output_path),
                }
            )
            current_input_path = current_output_path
            continue

        image_url = _upload_binary_to_runninghub(
            api_key=runninghub_api_key,
            file_path=current_input_path,
            media_kind="image_generate_input" if idx == 1 else f"image_generate_chain_step_{idx - 1}",
        )
        submit_payload = {
            "nodeInfoList": [
                {"nodeId": "16", "fieldName": "image", "fieldValue": image_url, "description": "产品图片"},
                {"nodeId": "142", "fieldName": "string", "fieldValue": product_name, "description": "目标描述（可以是单个或者多个）"},
                {"nodeId": "12", "fieldName": "image", "fieldValue": image_url, "description": "背景或模特图"},
                {"nodeId": "141", "fieldName": "string", "fieldValue": replace_target, "description": "被替换区域描述（可以单个可以多个）"},
                {"nodeId": "143", "fieldName": "value", "fieldValue": str(output_height), "description": "输出高度限制"},
                {"nodeId": "215", "fieldName": "string", "fieldValue": extra_prompt, "description": "提示词（可不填，可以增加被替换后的约束）"},
            ],
            "instanceType": "default",
            "usePersonalQueue": False,
        }
        response = requests.post(
            f"{str(runninghub_common.BASE_URL).rstrip('/')}/openapi/v2/run/ai-app/{workflow_id}",
            headers=headers,
            data=json.dumps(submit_payload),
            timeout=120,
        )
        response.raise_for_status()
        submit_result = response.json()
        normalized = runninghub_common._normalize_submit_result(submit_result)
        task_id_text = str(normalized.get("task_id") or normalized.get("task id") or "").strip()
        if not task_id_text:
            raise RuntimeError(f"RunningHub 图像编辑提交失败: {json.dumps(submit_result, ensure_ascii=False)[:500]}")
        runninghub_task_ids.append(task_id_text)

        query_result = None
        for _ in range(120):
            current = runninghub_common.query_task(task_id=task_id_text, api_key=runninghub_api_key, video_output_path=str(current_output_path))
            status_text = str(current.get("status") or "").strip().lower()
            query_result = current
            if status_text == "success":
                break
            if status_text == "failed":
                break
            time.sleep(3)
        if not isinstance(query_result, dict) or str(query_result.get("status") or "").lower() != "success":
            raise RuntimeError(_classify_runninghub_image_generate_failure(query_result))

        possible_url = ""
        results = query_result.get("results") if isinstance(query_result.get("results"), list) else []
        for item in results:
            if not isinstance(item, dict):
                continue
            value = str(item.get("url") or "").strip()
            if value:
                possible_url = value
                break
        if not possible_url:
            raw = query_result.get("raw") if isinstance(query_result.get("raw"), dict) else {}
            if isinstance(raw, dict):
                raw_results = raw.get("results") if isinstance(raw.get("results"), list) else []
                for item in raw_results:
                    if not isinstance(item, dict):
                        continue
                    value = str(item.get("url") or item.get("imageUrl") or item.get("image_url") or "").strip()
                    if value:
                        possible_url = value
                        break
        if not possible_url:
            raise RuntimeError(f"RunningHub 图像编辑成功但未返回图片 URL: {json.dumps(query_result, ensure_ascii=False)[:500]}")

        _download_to_file(possible_url, current_output_path)
        current_output_path = current_output_path.resolve()
        step_results.append(
            {
                "step": idx,
                "provider": "runninghub_workflow",
                "workflow_id": str(workflow_id),
                "runninghub_task_id": task_id_text,
                "submit": submit_result,
                "query": query_result,
                "output_path": str(current_output_path),
            }
        )
        current_input_path = current_output_path

    final_path = current_output_path.resolve()
    return {
        "ok": True,
        "message": "图片生成完成",
        "runninghub_task_id": runninghub_task_ids[-1] if runninghub_task_ids else "",
        "runninghub_task_ids": _normalize_workflow_ids(runninghub_task_ids),
        "runninghub_usage": _merge_usage_values(step_results),
        "nano_images": 1,
        "image_path": str(final_path),
        "scene_image_path": str(final_path),
        "download_path": str(final_path),
        "mode": mode,
        "raw_result": {"steps": step_results, "final_submit": submit_result, "final_query": query_result},
    }



def _run_image_generate_via_closed_model_api(task_id: str, payload: dict[str, Any], *, ref_input: Path, prompt_text: str, mode: str) -> dict[str, Any]:
    workdir = _build_task_workdir(task_id)
    output_path = workdir / "image_generate_output.png"
    result, selected, attempts = _generate_closed_image_with_fallback(
        source=payload,
        prompt=prompt_text,
        output_image_path=str(output_path),
        input_image_path=str(ref_input),
        logger=payload.get("_event_logger"),
        request_label="图像编辑闭源模型",
    )
    model = str(selected.get("model") or "").strip()
    image_path = str(result.get("image_path") or output_path)
    final_path = Path(image_path).resolve()
    if not final_path.exists():
        raise RuntimeError("闭源模型图像编辑成功但未找到输出图片")
    return {
        "ok": True,
        "message": "图片生成完成",
        "runninghub_task_id": "",
        "runninghub_usage": {},
        "nano_images": 1,
        "image_path": str(final_path),
        "scene_image_path": str(final_path),
        "download_path": str(final_path),
        "mode": mode,
        "image_model_used": model,
        "image_model_attempts": attempts,
        "raw_result": result,
    }



def _run_image_generate_via_legacy_nano(task_id: str, payload: dict[str, Any], *, ref_input: Path, prompt_text: str, mode: str) -> dict[str, Any]:
    return _run_image_generate_via_closed_model_api(task_id, payload, ref_input=ref_input, prompt_text=prompt_text, mode=mode)



def _run_image_generate(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if _remote_comfy_workflow_mapping(payload, "image_generate"):
        return _run_remote_comfy_mapped_task(task_id, payload, "image_generate")
    mode = str(payload.get("mode") or "product_only").strip() or "product_only"
    provider = str(payload.get("image_generate_provider") or payload.get("image_generate_mode_default") or "closed_model_api").strip() or "closed_model_api"
    product_local = str(payload.get("product_image_local_path") or "").strip()
    model_local = str(payload.get("model_image_local_path") or "").strip()
    prompt_text = str(payload.get("prompt") or payload.get("prompt_text") or payload.get("message") or "").strip()
    if not prompt_text:
        raise RuntimeError("图片生成需要填写提示词")
    if not product_local:
        raise RuntimeError("图片生成缺少商品图")

    workdir = _build_task_workdir(task_id)
    product_src = Path(product_local).resolve()
    if not product_src.exists():
        raise FileNotFoundError(f"商品图不存在: {product_src}")

    if mode == "model_product":
        if not model_local:
            raise RuntimeError("图片生成（模特+商品）需要上传模特图和商品图")
        model_src = Path(model_local).resolve()
        if not model_src.exists():
            raise FileNotFoundError(f"模特图不存在: {model_src}")
        ref_input = _compose_reference_image(
            model_image=model_src,
            product_image=product_src,
            output_path=workdir / "image_generate_ref.png",
        )
    else:
        ref_input = workdir / f"product_input{product_src.suffix.lower() or '.png'}"
        shutil.copy2(product_src, ref_input)

    if provider == "runninghub_workflow":
        provider = "closed_model_api"
    return _run_image_generate_via_closed_model_api(task_id, payload, ref_input=ref_input, prompt_text=prompt_text, mode=mode)


def _run_get_gemini(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    host, api_key, model = _resolve_llm_settings(payload)
    if not api_key:
        raise RuntimeError("缺少文字模型 API Key")
    if not host:
        raise RuntimeError("缺少文字模型 API Base URL")
    image_paths = payload.get("image_paths")
    video_paths = payload.get("video_paths")
    result = get_gemini.request_gemini3_pro(
        user_input=str(payload.get("user_input") or "").strip(),
        host=host,
        api_key=api_key,
        parameters=payload.get("parameters") or "",
        image_paths=image_paths if isinstance(image_paths, list) else None,
        port=None,
        video_paths=video_paths if isinstance(video_paths, list) else None,
        system_prompt=str(payload.get("system_prompt") or ""),
        logger=payload.get("_event_logger"),
        model=model,
    )
    if isinstance(result, str):
        failure_prefixes = ("请求失败:", "响应解析失败:", "未找到有效的响应内容:", "未识别的响应格式:")
        if result.startswith(failure_prefixes):
            raise RuntimeError(result)

    workdir = _build_task_workdir(task_id)
    out_path = workdir / "gemini_result.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return {
        "ok": True,
        "message": "Gemini 请求完成",
        "runninghub_task_id": "",
        "runninghub_usage": {},
        "gemini_input_tokens": max(_to_int(payload.get("gemini_input_tokens"), 0), 0),
        "gemini_output_tokens": max(_to_int(payload.get("gemini_output_tokens"), 0), 0),
        "result_path": str(out_path),
        "download_path": str(out_path),
        "raw_result": result,
    }


def _extract_image_label_mapping(parsed: Any) -> dict[str, str]:
    if isinstance(parsed, dict):
        for key in ("image lable", "image_label", "image_lable", "image labels", "labels", "image_labels"):
            val = parsed.get(key)
            if isinstance(val, dict):
                return {str(k): str(v) for k, v in val.items()}
        if all(isinstance(k, str) for k in parsed.keys()):
            if all(isinstance(v, (str, int, float, bool)) or v is None for v in parsed.values()):
                return {str(k): str(v) for k, v in parsed.items()}
    return {}


def _label_images_with_gemini(
    *,
    image_paths: list[str],
    gemini_host: str,
    gemini_api_key: str,
    gemini_port: str | None,
    task_id: str,
    llm_model: str = "",
) -> dict[str, Any]:
    system_prompt = (
        "你是一个视频标注助手，专门对图片进行标注。\n"
        "分为以下类型：1.商品图，2.模特图。\n"
        "你会收到多张图片与图片路径列表，你需要判断每张图片属于“商品图”还是“模特图”，不确定则标注为“不属于模特图或商品图”。\n"
        "只输出严格 JSON，不要代码块，不要解释文字。\n"
        "输出格式：\n"
        "{\n"
        '  \"tool\": \"image_path_lable\",\n'
        '  \"image lable\": {\n'
        '    \"/abs/path/a.png\": \"模特图|商品图|不属于模特图或商品图\"\n'
        "  }\n"
        "}\n"
        "注意：image lable 的 key 必须使用我给你的路径字符串原样返回。"
    )
    user_input = "请对下列图片逐一标注（路径列表）：\n" + "\n".join(image_paths)
    resp = get_gemini.request_gemini3_pro_json(
        user_input=user_input,
        host=gemini_host,
        api_key=gemini_api_key,
        system_prompt=system_prompt,
        port=gemini_port,
        parameters="",
        image_paths=image_paths,
        model=llm_model,
    )
    if not (isinstance(resp, dict) and resp.get("ok") is True):
        err = str(resp.get("error") if isinstance(resp, dict) else "Gemini 请求失败")
        raw_text = str(resp.get("raw_text") or "") if isinstance(resp, dict) else ""
        raise RuntimeError(f"Gemini 标注失败: {err}; task_id={task_id}; raw={raw_text[:300]}")
    parsed = resp.get("parsed")
    mapping = _extract_image_label_mapping(parsed)
    if not mapping:
        raw_text = str(resp.get("raw_text") or "")
        raise RuntimeError(f"Gemini 未返回可用标注结果; task_id={task_id}; raw={raw_text[:300]}")
    return {"mapping": mapping, "raw_text": str(resp.get("raw_text") or ""), "raw": resp.get("raw")}


def _copy_inputs_to_dir(src_paths: list[str], dest_dir: Path) -> list[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for idx, p in enumerate(src_paths or [], start=1):
        src = Path(str(p)).resolve()
        if not src.exists() or not src.is_file():
            continue
        suffix = src.suffix.lower()
        name = src.name
        target = dest_dir / name
        if target.exists():
            target = dest_dir / f"{idx:04d}{suffix or src.suffix}"
        shutil.copy2(src, target)
        copied.append(str(target))
    return copied


def _auto_split_model_product_inputs(
    *,
    task_id: str,
    mixed_image_paths: list[str],
    gemini_host: str,
    gemini_api_key: str,
    gemini_port: str | None,
) -> tuple[str, str]:
    workdir = _build_task_workdir(task_id)
    input_dir = workdir / "batch_input"
    model_dir = input_dir / "model"
    product_dir = input_dir / "product"
    unknown_dir = input_dir / "unknown"

    abs_paths = [str(Path(p).resolve()) for p in mixed_image_paths if str(p).strip()]
    if len(abs_paths) < 2:
        raise RuntimeError("联合替换自动分拣需要至少 2 张图片（模特+商品）")

    merged_mapping: dict[str, str] = {}
    raw_texts: list[str] = []
    batch_size = 8
    for i in range(0, len(abs_paths), batch_size):
        chunk = abs_paths[i : i + batch_size]
        labeled = _label_images_with_gemini(
            image_paths=chunk,
            gemini_host=gemini_host,
            gemini_api_key=gemini_api_key,
            gemini_port=gemini_port,
            task_id=task_id,
        )
        mapping = labeled.get("mapping") if isinstance(labeled, dict) else {}
        if isinstance(mapping, dict):
            merged_mapping.update({str(k): str(v) for k, v in mapping.items()})
        raw_texts.append(str(labeled.get("raw_text") or ""))

    key_by_name: dict[str, str] = {}
    for k, v in merged_mapping.items():
        key_by_name[Path(str(k)).name] = str(v)

    model_src: list[str] = []
    product_src: list[str] = []
    unknown_src: list[str] = []
    for p in abs_paths:
        v = merged_mapping.get(p) or merged_mapping.get(str(Path(p).resolve())) or key_by_name.get(Path(p).name) or ""
        low = str(v).strip()
        if "模特" in low or "人物" in low or "人像" in low:
            model_src.append(p)
        elif "商品" in low or "产品" in low:
            product_src.append(p)
        else:
            unknown_src.append(p)

    _copy_inputs_to_dir(model_src, model_dir)
    _copy_inputs_to_dir(product_src, product_dir)
    _copy_inputs_to_dir(unknown_src, unknown_dir)

    label_path = workdir / "image_labels.json"
    with label_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "tool": "image_path_lable",
                "image lable": merged_mapping,
                "stats": {"model": len(model_src), "product": len(product_src), "unknown": len(unknown_src)},
                "raw_texts": raw_texts,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    if not model_src or not product_src:
        raise RuntimeError(f"自动分拣失败：未识别到足够的模特图或商品图（已写入 {label_path}）")
    return str(model_dir), str(product_dir)


def _run_replace_product_and_model(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if _remote_comfy_workflow_mapping(payload, "replace_productANDmodel"):
        return _run_remote_comfy_mapped_task(task_id, payload, "replace_productANDmodel")
    raise RuntimeError("旧联合替换工作流已停用：下一步需要接入远程 ComfyUI 工作流后再提交")
    api_key = str(payload.get("runninghub_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("缺少 RunningHub API Key")
    upload_server_ip = str(payload.get("upload_server_ip") or "").strip()
    upload_server_port = str(payload.get("upload_server_port") or "").strip()
    upload_file_api_key = str(payload.get("upload_file_api_key") or "").strip()
    _emit_stage(payload, stage="parsing", status="running", message="解析文件中")

    def _resolve_for_union(*, media_kind: str, file_path: Path) -> str:
        _emit_stage(payload, stage="uploading", status="running", message="上传文件中", data={"media_kind": str(media_kind), "name": file_path.name})
        try:
            url = _resolve_media_url(
                task_id=task_id,
                media_kind=str(media_kind),
                api_key=api_key,
                local_path=str(file_path),
                remote_url="",
                upload_server_ip=upload_server_ip,
                upload_server_port=upload_server_port,
                upload_file_api_key=upload_file_api_key,
            )
            _emit_stage(payload, stage="upload_result", status="success", message="上传成功", data={"media_kind": str(media_kind), "urls": [str(url)]})
            return str(url)
        except Exception as exc:
            _emit_stage(payload, stage="upload_result", status="failed", message="上传失败", data={"media_kind": str(media_kind), "error": str(exc)})
            raise

    def zip_has_entries(path_text: str) -> bool:
        text = str(path_text or "").strip()
        if not text:
            return False
        p = Path(text)
        if not p.exists():
            return False
        try:
            with zipfile.ZipFile(str(p), "r") as zf:
                return bool(zf.namelist())
        except Exception:
            return False

    model_zip = str(payload.get("model_zip_path") or "").strip()
    product_zip = str(payload.get("product_zip_path") or "").strip()
    video_zip = str(payload.get("video_zip_path") or "").strip()
    model_dir = str(payload.get("model_dir_path") or "").strip()
    product_dir = str(payload.get("product_dir_path") or "").strip()
    video_dir = str(payload.get("video_dir_path") or "").strip()

    mixed_image_paths = payload.get("mixed_image_paths") if isinstance(payload.get("mixed_image_paths"), list) else []
    mixed_image_paths = [str(p) for p in mixed_image_paths if str(p).strip()]

    video_paths = payload.get("video_paths") if isinstance(payload.get("video_paths"), list) else []
    video_paths = [str(p) for p in video_paths if str(p).strip()]

    if (not model_zip) and (not model_dir) and (not product_zip) and (not product_dir):
        with db() as conn:
            runtime = _get_runtime_config(conn)
        gemini_host, gemini_key, _ = _resolve_llm_settings(runtime)
        gemini_port = None
        if not gemini_key or not gemini_host:
            raise RuntimeError("自动分拣模特/商品图片需要管理员配置文字模型（API Base URL / API Key）")
        model_dir, product_dir = _auto_split_model_product_inputs(
            task_id=task_id,
            mixed_image_paths=mixed_image_paths,
            gemini_host=gemini_host,
            gemini_api_key=gemini_key,
            gemini_port=gemini_port,
        )
        _emit_stage(payload, stage="parse_result", status="success", message="解析结果", data={"auto_split": True, "model_dir": str(model_dir), "product_dir": str(product_dir)})

    if (not video_zip) and (not video_dir) and video_paths:
        workdir = _build_task_workdir(task_id)
        video_dir_path = workdir / "batch_input" / "video"
        _copy_inputs_to_dir(video_paths, video_dir_path)
        video_dir = str(video_dir_path)
    _emit_stage(
        payload,
        stage="parse_result",
        status="success",
        message="解析结果",
        data={
            "model_input": "zip" if model_zip else "dir",
            "product_input": "zip" if product_zip else "dir",
            "video_input": "zip" if video_zip else "dir",
        },
    )

    if bool(model_zip) == bool(model_dir):
        raise RuntimeError("联合替换的模特输入必须且只能提供 zip 或 dir 其中一个")
    if bool(product_zip) == bool(product_dir):
        raise RuntimeError("联合替换的商品输入必须且只能提供 zip 或 dir 其中一个")
    if bool(video_zip) == bool(video_dir):
        raise RuntimeError("联合替换的原视频输入必须且只能提供 zip 或 dir 其中一个")

    out_dir = _build_task_workdir(task_id) / "batch_output"
    _emit_stage(payload, stage="uploading", status="running", message="上传文件中")
    _emit_stage(payload, stage="processing", status="running", message="正在执行视频模特替换/视频商品替换")
    model_chain_ids = _workflow_chain_from_payload(
        payload,
        "model_workflow_chain_ids",
        [payload.get("model_app_id"), payload.get("replace_model_original_app_id")],
    )
    product_chain_ids = _workflow_chain_from_payload(
        payload,
        "product_workflow_chain_ids",
        [payload.get("product_app_id"), payload.get("replace_product_app_id")],
    )
    model_closed_stages, model_runninghub_chain = _split_workflow_chain_stages(model_chain_ids)
    product_closed_stages, product_runninghub_chain = _split_workflow_chain_stages(product_chain_ids)
    if not model_runninghub_chain:
        raise RuntimeError("联合替换的模特链至少需要一个 RunningHub 视频工作流，闭源图片模型只负责处理模特图")
    if not product_runninghub_chain:
        raise RuntimeError("联合替换的商品链至少需要一个 RunningHub 视频工作流，闭源图片模型只负责处理商品图")

    closed_stage_results: list[dict[str, Any]] = []
    closed_workdir = _build_task_workdir(task_id) / "closed_image_preprocess"
    if model_closed_stages:
        model_source_dir = Path(model_dir).resolve() if model_dir else closed_workdir / "model_zip_input"
        if model_zip:
            _extract_zip_to_dir(Path(model_zip).resolve(), model_source_dir)
            model_zip = ""
        processed_model_dir, model_closed_results = _apply_closed_image_stages_to_dir(
            task_id=task_id,
            payload=payload,
            input_dir=model_source_dir,
            closed_stages=model_closed_stages,
            workdir=closed_workdir,
            label="model",
            prompt_text=str(
                (
                    (payload.get("model_params") or {}).get("image_prompt")
                    if isinstance(payload.get("model_params"), dict)
                    else ""
                )
                or "优化人物参考图，保持人物身份、面部和服饰自然清晰，背景简洁，不添加文字或水印。"
            ),
        )
        model_dir = str(processed_model_dir)
        closed_stage_results.extend(model_closed_results)
    if product_closed_stages:
        product_source_dir = Path(product_dir).resolve() if product_dir else closed_workdir / "product_zip_input"
        if product_zip:
            _extract_zip_to_dir(Path(product_zip).resolve(), product_source_dir)
            product_zip = ""
        processed_product_dir, product_closed_results = _apply_closed_image_stages_to_dir(
            task_id=task_id,
            payload=payload,
            input_dir=product_source_dir,
            closed_stages=product_closed_stages,
            workdir=closed_workdir,
            label="product",
            prompt_text=str(
                (
                    (payload.get("product_params") or {}).get("image_prompt")
                    if isinstance(payload.get("product_params"), dict)
                    else ""
                )
                or "优化商品参考图，保持商品外观、材质和颜色准确，背景简洁，不添加文字或水印。"
            ),
        )
        product_dir = str(processed_product_dir)
        closed_stage_results.extend(product_closed_results)

    result = replace_productANDmodel.run_product_and_model_replace(
        rh_api_key=api_key,
        model_zip=model_zip or None,
        model_dir=model_dir or None,
        product_zip=product_zip or None,
        product_dir=product_dir or None,
        video_zip=video_zip or None,
        video_dir=video_dir or None,
        output_dir=str(out_dir),
        model_app_id=replace_model.normalize_app_id(_last_runninghub_workflow_id(model_runninghub_chain) or payload.get("model_app_id")),
        product_app_id=str(_last_runninghub_workflow_id(product_runninghub_chain) or payload.get("product_app_id") or replace_productANDmodel.DEFAULT_PRODUCT_APP_ID).strip() or replace_productANDmodel.DEFAULT_PRODUCT_APP_ID,
        model_app_ids=model_runninghub_chain,
        product_app_ids=product_runninghub_chain,
        match_mode=str(payload.get("match_mode") or "cycle").strip() or "cycle",
        fixed_index=max(_to_int(payload.get("fixed_index"), 1), 1),
        auto_rename=_to_bool(payload.get("auto_rename"), True),
        model_params=payload.get("model_params") if isinstance(payload.get("model_params"), dict) else {},
        product_params=payload.get("product_params") if isinstance(payload.get("product_params"), dict) else {},
        batch_params=payload.get("batch_params") if isinstance(payload.get("batch_params"), list) else [],
        common_params=payload.get("common_params") if isinstance(payload.get("common_params"), list) else [],
        cycle_params_on_shortage=_to_bool(payload.get("cycle_params_on_shortage"), True),
        product_mapping=payload.get("product_mapping") if isinstance(payload.get("product_mapping"), list) else None,
        upload_result=False,
        media_url_resolver=_resolve_for_union,
    )

    output_dir = Path(str(result.get("output_dir") or out_dir)).resolve()
    usage = _collect_batch_usage(output_dir)
    result_zip = str(result.get("result_zip") or "").strip()
    rh_task_ids = result.get("runninghub_task_ids") if isinstance(result.get("runninghub_task_ids"), list) else []
    rh_task_ids = [str(x).strip() for x in rh_task_ids if str(x).strip()]
    rh_task_id = rh_task_ids[-1] if rh_task_ids else ""
    if closed_stage_results:
        result["closed_image_steps"] = closed_stage_results

    success = _to_int(result.get("success"), 0)
    ok = success > 0
    message = "批量替换完成" if ok else "批量替换失败：未生成任何视频"
    if not ok:
        results_path = output_dir / "results.json"
        reason = ""
        if results_path.exists():
            try:
                parsed = _json_loads(results_path.read_text(encoding="utf-8", errors="ignore"), {})
                items = []
                if isinstance(parsed, dict):
                    items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
                elif isinstance(parsed, list):
                    items = parsed
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    st = str(it.get("status") or "").strip().lower()
                    if st == "failed":
                        reason = str(it.get("error") or "").strip()
                        break
                if not reason:
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        reason = str(it.get("error") or "").strip()
                        if reason:
                            break
            except Exception:
                reason = ""
        if reason:
            message = f"批量替换失败：{reason}"
            _emit_stage(payload, stage="processing", status="failed", message="生成失败", data={"error": reason})
    else:
        _emit_stage(payload, stage="processing", status="success", message="生成成功")

    return {
        "ok": ok,
        "message": message,
        "runninghub_task_id": rh_task_id,
        "runninghub_task_ids": rh_task_ids,
        "runninghub_usage": usage,
        "result_zip": result_zip,
        "download_path": result_zip if zip_has_entries(result_zip) else "",
        "raw_result": result,
    }


def _run_create_video_with_doubao(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    task_type = str(payload.get("_task_type") or "").strip()
    mapped_type = "commerce_video" if task_type == "commerce_video" else "create_video"
    if _remote_comfy_workflow_mapping(payload, mapped_type):
        return _run_remote_comfy_mapped_task(task_id, payload, mapped_type)
    raise RuntimeError("旧视频生成工作流已停用：下一步需要接入远程 ComfyUI 工作流后再提交")

    runninghub_api_key = str(payload.get("runninghub_api_key") or "").strip()
    if not runninghub_api_key:
        raise RuntimeError("缺少 RunningHub API Key")

    model_local = str(payload.get("model_image_local_path") or "").strip()
    product_local = str(payload.get("product_image_local_path") or "").strip()
    if not model_local or not product_local:
        raise RuntimeError("视频生成必须上传模特图和商品图")

    model_path = Path(model_local).resolve()
    product_path = Path(product_local).resolve()
    if not model_path.exists() or not product_path.exists():
        raise RuntimeError("上传图片不存在")
    _emit_stage(payload, stage="parsing", status="running", message="解析文件中")

    fallback_username = str(payload.get("_username") or "").strip() or None
    workdir = _build_task_workdir(task_id, fallback_username=fallback_username)
    resume_from_task_id = str(payload.get("resume_from_task_id") or "").strip()
    resume_enabled = False
    if resume_from_task_id:
        source_workdir = _build_task_workdir(resume_from_task_id, fallback_username=fallback_username)
        copied_any = False
        copied_any = _copytree_if_exists(source_workdir / "commerce_input", workdir / "commerce_input") or copied_any
        copied_any = _copytree_if_exists(source_workdir / "commerce_out", workdir / "commerce_out") or copied_any
        if not copied_any:
            raise RuntimeError(f"源任务缺少可续跑产物: {resume_from_task_id}")
        resume_enabled = True

    input_dir = workdir / "commerce_input"
    product_dir = input_dir / "products"
    model_dir = input_dir / "models"
    product_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(product_path, product_dir / f"1{product_path.suffix.lower()}")
    shutil.copy2(model_path, model_dir / f"1{model_path.suffix.lower()}")

    use_ai_copy = _to_bool(payload.get("use_ai_copy"), _to_bool(payload.get("use_doubao"), True))
    speech_text = str(payload.get("speech_text") or "").strip()
    prompt_text = str(payload.get("prompt_text") or "").strip()
    uploaded_audio_local = str(payload.get("audio_local_path") or "").strip()
    generated_scene_local = str(payload.get("generated_scene_image_local_path") or "").strip()
    ai_meta: dict[str, Any] = {}
    ai_warning = ""
    fallback_text = (
        str(payload.get("message") or "").strip()
        or str(payload.get("style_hint") or "").strip()
        or str(payload.get("product_name") or "").strip()
    )

    product_name = str(payload.get("product_name") or "该商品").strip() or "该商品"
    style_hint = str(payload.get("style_hint") or "自然口播，真实电商场景").strip()
    duration_mode = str(payload.get("duration_mode") or "manual").strip().lower() or "manual"
    duration_seconds = max(_to_int(payload.get("duration_seconds"), 15), 1)
    oral_chain = _workflow_chain_from_payload(
        payload,
        "oral_digital_human_workflow_ids",
        [payload.get("create_audio_app_id"), payload.get("video_app_id"), payload.get("create_video_app_id")],
    )
    runninghub_oral_chain = [stage for stage in oral_chain if _workflow_stage_runninghub_id(stage)]
    closed_llm_models = [_closed_llm_workflow_stage_model(stage) for stage in oral_chain if _is_closed_llm_workflow_stage(stage)]
    closed_image_models = [_closed_image_workflow_stage_model(stage) for stage in oral_chain if _is_closed_image_workflow_stage(stage)]
    oral_llm_model = next((model for model in reversed(closed_llm_models) if model), "")
    oral_image_model = next((model for model in reversed(closed_image_models) if model), "")
    audio_app_id = (
        runninghub_oral_chain[0]
        if runninghub_oral_chain
        else str(payload.get("create_audio_app_id") or "").strip() or create_audio.DEFAULT_APP_ID
    )
    video_chain_ids = (
        runninghub_oral_chain[1:]
        if len(runninghub_oral_chain) > 1
        else _normalize_workflow_ids([payload.get("video_app_id"), payload.get("create_video_app_id"), create_video.DEFAULT_APP_ID])
    )

    if use_ai_copy and ((not prompt_text) or ((not speech_text) and (not uploaded_audio_local))):
        _emit_stage(payload, stage="processing", status="running", message="正在生成文案")
        llm_source = dict(payload)
        if oral_llm_model:
            llm_source["llm_model"] = oral_llm_model
        llm_base_url, llm_candidates = _resolve_llm_fallback_candidates(llm_source, allow_builtin=True)
        llm_port = None
        if not llm_base_url or not llm_candidates:
            if uploaded_audio_local:
                raise RuntimeError("启用文字模型生成提示词需先在后台配置文字模型 API，或手动填写 prompt_text")
            raise RuntimeError("启用文字模型生成口播/提示词需先在后台配置文字模型 API，或手动填写 speech_text/prompt_text")
        gemini_prompt = "\n".join(
            [
                "你是电商短视频创作助手。请输出严格 JSON（不要代码块、不要多余文字）。",
                "字段必须是 prompt_text。" if uploaded_audio_local else "字段必须是 speech_text 和 prompt_text。",
                f"商品名称：{product_name}。",
                f"风格：{style_hint}。",
                f"目标时长：{duration_seconds} 秒。",
                "" if uploaded_audio_local else "speech_text：中文口播文案，适合直接配音。",
                "prompt_text：用于视频生成的镜头/场景提示词。",
            ]
        ).strip()
        try:
            gemini_result, llm_selected, llm_attempts = _request_llm_json_with_fallback(
                source=llm_source,
                user_input=fallback_text or style_hint or product_name,
                system_prompt=gemini_prompt,
                port=llm_port,
                parameters="",
                logger=payload.get("_event_logger"),
                allow_builtin=True,
                request_label="口播文案生成",
            )
            ai_meta = gemini_result if isinstance(gemini_result, dict) else {"raw": gemini_result}
            ai_meta["llm_selected"] = llm_selected
            ai_meta["llm_attempts"] = llm_attempts
            parsed = gemini_result.get("parsed") if isinstance(gemini_result, dict) else None
            if isinstance(parsed, dict):
                if not uploaded_audio_local:
                    speech_text = speech_text or str(parsed.get("speech_text") or "").strip()
                prompt_text = prompt_text or str(parsed.get("prompt_text") or "").strip()
        except Exception as exc:
            ai_meta = {"error": str(exc)}
            ai_warning = f"文字模型自动文案失败: {exc}"

    if (not speech_text) and (not uploaded_audio_local):
        speech_text = fallback_text
    if not prompt_text:
        prompt_text = fallback_text
    if uploaded_audio_local:
        if not prompt_text:
            raise RuntimeError("缺少 prompt_text，请手动填写或启用 Gemini 自动生成")
    else:
        if not speech_text or not prompt_text:
            raise RuntimeError("缺少 speech_text/prompt_text，请手动填写或启用 Gemini 自动生成")

    image_source = dict(payload)
    if oral_image_model:
        image_source["image_generate_model"] = oral_image_model
    image_base_url, image_gemini_api_key, image_gpt_api_key, image_candidates = _resolve_closed_image_model_fallback_candidates(
        image_source,
        allow_builtin=True,
    )
    image_model = str(image_candidates[0].get("model") or "").strip() if image_candidates else ""
    requires_generated_scene = not bool(generated_scene_local)
    if requires_generated_scene and (not image_base_url or not image_candidates):
        raise RuntimeError("视频生成需要先配置闭源图片模型（Base URL / API Key / 候选模型）")

    camera_video_url = _resolve_media_url(
        task_id=task_id,
        media_kind="camera_video",
        api_key=runninghub_api_key,
        local_path=payload.get("camera_video_local_path"),
        remote_url=payload.get("camera_video_url"),
        upload_server_ip=payload.get("upload_server_ip"),
        upload_server_port=payload.get("upload_server_port"),
        upload_file_api_key=payload.get("upload_file_api_key"),
    ) if str(payload.get("camera_video_local_path") or "").strip() or str(payload.get("camera_video_url") or "").strip() else ""

    out_dir = workdir / "commerce_out"
    audio_path_value = Path(uploaded_audio_local).resolve() if uploaded_audio_local else None
    if audio_path_value is not None and not audio_path_value.exists():
        raise FileNotFoundError("上传音频不存在")
    _emit_stage(payload, stage="uploading", status="running", message="上传文件中")
    _emit_stage(payload, stage="processing", status="running", message="正在生成音频/视频")
    result = commerce_video_generator.generate_commerce_videos(
        runninghub_api_key=runninghub_api_key,
        upload_api_key=str(payload.get("upload_file_api_key") or "").strip() or runninghub_api_key,
        product_dir=str(product_dir),
        model_dir=str(model_dir),
        output_dir=str(out_dir),
        batch=commerce_video_generator.BatchSettings(
            output_dir=str(out_dir),
            match_mode="cycle",
            fixed_index=1,
            auto_rename=True,
            upload_result_zip=False,
            resume=resume_enabled,
        ),
        audio_settings=commerce_video_generator.AudioSettings(
            emotion=str(payload.get("emotion") or "happy"),
            language=str(payload.get("language") or "Chinese"),
            model_choice=str(payload.get("model_choice") or "1.7B"),
            speaker=str(payload.get("speaker") or "Ryan"),
            app_id=audio_app_id,
        ),
        nano_settings=commerce_video_generator.NanoSettings(
            base_url=image_base_url,
            model=image_model,
            gemini_api_key=image_gemini_api_key,
            gpt_api_key=image_gpt_api_key,
            prompt_template=str(payload.get("nano_prompt") or "电商口播视频场景截图风格：真实人物在室内/直播间展示商品，手持商品或放在手掌上讲解；写实摄影、柔和补光、干净背景；9:16；画面不要文字/水印/海报排版。"),
        ),
        video_workflow=commerce_video_generator.VideoWorkflowSettings(
            app_id=video_chain_ids[-1] if video_chain_ids else str(payload.get("video_app_id") or create_video.DEFAULT_APP_ID).strip() or create_video.DEFAULT_APP_ID,
            app_ids=video_chain_ids,
            duration_mode=duration_mode,
            duration_seconds=duration_seconds,
            camera_video_url=camera_video_url or None,
            instance_type=str(payload.get("instance_type") or "default").strip() or "default",
            use_personal_queue=_to_bool(payload.get("use_personal_queue"), False),
        ),
        speech_text_provider=(lambda _i, _m, _p: speech_text) if not uploaded_audio_local else None,
        prompt_provider=lambda _i, _m, _p: prompt_text,
        image_path_provider=(lambda _i, _m, _p: generated_scene_local) if generated_scene_local else None,
        logger=payload.get("_event_logger"),
        progress_callback=payload.get("_event_progress"),
    )
    output_dir = Path(str(result.get("output_dir") or out_dir)).resolve()
    runninghub_usage = _collect_batch_usage(output_dir)
    if _to_int(result.get("success"), 0) <= 0:
        logs_path = output_dir / "logs.jsonl"
        if logs_path.exists():
            last_line = ""
            for line in logs_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip():
                    last_line = line.strip()
            record = _json_loads(last_line, {}) if last_line else {}
            err_text = str(record.get("error") or "").strip()
            raise RuntimeError(err_text or "视频生成失败（logs.jsonl 未提供 error 字段）")
        raise RuntimeError("视频生成失败（无 logs.jsonl）")
    video_path = output_dir / "videos" / "1.mp4"
    if not video_path.exists():
        for cand in sorted((output_dir / "videos").glob("*.mp4")):
            video_path = cand
            break
    if not video_path.exists():
        logs_path = output_dir / "logs.jsonl"
        if logs_path.exists():
            last_line = ""
            for line in logs_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip():
                    last_line = line.strip()
            record = _json_loads(last_line, {}) if last_line else {}
            err_text = str(record.get("error") or "").strip()
            raise RuntimeError(err_text or "视频生成失败（未找到输出视频文件）")
        raise RuntimeError("视频生成失败（未找到输出视频文件）")

    return {
        "ok": True,
        "message": "视频流程完成",
        "runninghub_task_id": str((result.get("runninghub_task_ids") or [""])[-1] or "").strip(),
        "runninghub_task_ids": result.get("runninghub_task_ids") if isinstance(result.get("runninghub_task_ids"), list) else [],
        "runninghub_usage": runninghub_usage,
        "nano_images": 1,
        "speech_text": speech_text,
        "prompt_text": prompt_text,
        "video_path": str(video_path),
        "download_path": str(video_path),
        "ai_copy": ai_meta,
        "warnings": [ai_warning] if ai_warning else [],
        "raw_result": result,
    }


def _extract_zip_to_dir(zip_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        members = zf.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise RuntimeError("zip 文件内容过多，拒绝处理")
        out_base = out_dir.resolve()
        total_bytes = 0
        for m in members:
            name = str(m.filename or "")
            if not name or name.endswith("/"):
                continue
            norm = name.replace("\\", "/")
            if norm.startswith("/") or re.match(r"^[a-zA-Z]:", norm):
                raise RuntimeError("zip 文件路径不安全")
            posix = PurePosixPath(norm)
            if ".." in posix.parts:
                raise RuntimeError("zip 文件路径不安全")
            if _is_macos_junk_posix(posix):
                continue
            mode = (int(getattr(m, "external_attr", 0)) >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise RuntimeError("zip 文件路径不安全")
            size = int(getattr(m, "file_size", 0) or 0)
            if size > MAX_ZIP_MEMBER_BYTES:
                raise RuntimeError("zip 单文件过大，拒绝处理")
            total_bytes += size
            if total_bytes > MAX_ZIP_TOTAL_BYTES:
                raise RuntimeError("zip 解压后总大小过大，拒绝处理")
            rel = Path(*posix.parts)
            target = (out_dir / rel).resolve()
            if target != out_base and out_base not in target.parents:
                raise RuntimeError("zip 文件路径不安全")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=UPLOAD_CHUNK_SIZE)


def _normalize_batch_media_rel_path(value: Any, *, field_name: str, required: bool = False) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        if required:
            raise RuntimeError(f"{field_name} 不能为空")
        return ""
    if text.startswith("/") or re.match(r"^[a-zA-Z]:", text):
        raise RuntimeError(f"{field_name} 路径不安全")
    posix = PurePosixPath(text)
    if posix.is_absolute() or ".." in posix.parts or _is_macos_junk_posix(posix):
        raise RuntimeError(f"{field_name} 路径不安全")
    return str(PurePosixPath(*posix.parts))


def _resolve_batch_media_path(src_dir: Path, value: Any, *, field_name: str, required: bool = False) -> Path | None:
    rel = _normalize_batch_media_rel_path(value, field_name=field_name, required=required)
    if not rel:
        return None
    src_base = src_dir.resolve()
    target = (src_base / Path(*PurePosixPath(rel).parts)).resolve()
    if target != src_base and src_base not in target.parents:
        raise RuntimeError(f"{field_name} 路径不安全")
    return target


BATCH_MODEL_KEYWORDS = {"model", "person", "human", "actor", "talent", "模特", "人物", "人像", "modelimg"}
BATCH_PRODUCT_KEYWORDS = {"product", "goods", "item", "sku", "商品", "产品", "货品"}
BATCH_AUDIO_KEYWORDS = {"audio", "voice", "speech", "tts", "音频", "配音"}
BATCH_VIDEO_KEYWORDS = {"video", "camera", "motion", "运镜", "镜头", "视频"}


def _batch_match_sort_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(entry.get("path_depth") or 0),
        str(entry.get("normalized_path") or ""),
        str(entry.get("basename") or ""),
        str(entry.get("rel") or ""),
    )


def _normalize_batch_match_keys(stem: str) -> dict[str, str]:
    low = str(stem or "").strip().lower()
    low = re.sub(r"\s+", "_", low)
    exact = low
    numeric = ""
    if low.isdigit():
        try:
            numeric = str(int(low))
        except Exception:
            numeric = low
    parts = re.findall(r"[a-z]+|\d+", low)
    normalized_parts: list[str] = []
    for part in parts:
        if part.isdigit():
            try:
                normalized_parts.append(str(int(part)))
            except Exception:
                normalized_parts.append(part)
        else:
            normalized_parts.append(part)
    normalized = "_".join([p for p in normalized_parts if p])
    return {
        "exact_stem": exact,
        "numeric_stem": numeric,
        "normalized_stem": normalized,
    }


def _detect_batch_media_role(*, rel: str, kind: str, role_hint: str = "") -> str:
    hint = str(role_hint or "").strip().lower()
    if hint in {"model", "product", "audio", "video"}:
        return hint
    if kind == "audio":
        return "audio"
    if kind == "video":
        return "video"
    low = str(rel or "").lower()
    if _has_any(low, BATCH_PRODUCT_KEYWORDS):
        return "product"
    if _has_any(low, BATCH_MODEL_KEYWORDS):
        return "model"
    return ""


def _make_batch_media_entry(rel: str, *, kind: str, role_hint: str = "") -> dict[str, Any]:
    norm = str(rel or "").replace("\\", "/").strip("/")
    posix = PurePosixPath(norm)
    folder = str(PurePosixPath(*posix.parts[:-1])) if len(posix.parts) > 1 else ""
    basename = str(posix.name or "")
    stem = str(Path(basename).stem or "")
    key_info = _normalize_batch_match_keys(stem)
    return {
        "rel": norm,
        "folder": folder,
        "kind": kind,
        "role": _detect_batch_media_role(rel=norm, kind=kind, role_hint=role_hint),
        "basename": basename,
        "stem": stem,
        "path_depth": len(posix.parts),
        "normalized_path": norm.lower(),
        **key_info,
    }


def _collect_batch_media_entries_from_dir(root_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(root_dir.rglob("*")):
        if not p.is_file():
            continue
        if _is_macos_junk_path(p):
            continue
        suf = p.suffix.lower()
        rel = str(p.relative_to(root_dir)).replace("\\", "/")
        if suf in IMAGE_EXTS:
            out.append(_make_batch_media_entry(rel, kind="image"))
        elif suf in VIDEO_EXTS:
            out.append(_make_batch_media_entry(rel, kind="video"))
        elif suf in AUDIO_EXTS:
            out.append(_make_batch_media_entry(rel, kind="audio"))
    return sorted(out, key=_batch_match_sort_key)


def _legacy_scan_batch_items(root_dir: Path) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for entry in _collect_batch_media_entries_from_dir(root_dir):
        folder = str(entry.get("folder") or "")
        g = grouped.setdefault(folder, {"id": folder or "root", "folder": folder, "images": [], "videos": []})
        if entry.get("kind") == "image":
            g["images"].append(str(entry.get("rel") or ""))
        elif entry.get("kind") == "video":
            g["videos"].append(str(entry.get("rel") or ""))

    out: list[dict[str, Any]] = []
    for folder, g in sorted(grouped.items(), key=lambda kv: kv[0]):
        images = sorted(set(g.get("images") or []))
        if not images:
            continue
        videos = sorted(set(g.get("videos") or []))
        out.append(
            {
                "id": str(g.get("id") or folder or "root"),
                "folder": str(g.get("folder") or ""),
                "model_image": str(images[0]),
                "product_image": str(images[1]) if len(images) > 1 else str(images[0]),
                "camera_video": str(videos[0]) if videos else "",
                "audio": "",
                "match_key": str(folder or images[0]),
                "match_mode": "legacy_folder",
                "audio_match_state": "missing",
                "source_folder": str(g.get("folder") or ""),
            }
        )
    return out


def _build_strict_batch_item_from_pair(
    *,
    idx: int,
    model_entry: dict[str, Any],
    product_entry: dict[str, Any],
    match_key: str,
    match_mode: str,
    audio_entry: dict[str, Any] | None,
    audio_state: str,
    video_entry: dict[str, Any] | None,
    model_candidate_count: int,
    product_candidate_count: int,
) -> dict[str, Any]:
    source_parts = [str(model_entry.get("folder") or ""), str(product_entry.get("folder") or "")]
    source_parts = [p for p in source_parts if p]
    return {
        "id": f"item_{idx}",
        "folder": str(model_entry.get("folder") or product_entry.get("folder") or ""),
        "model_image": str(model_entry.get("rel") or ""),
        "product_image": str(product_entry.get("rel") or ""),
        "camera_video": str((video_entry or {}).get("rel") or ""),
        "audio": str((audio_entry or {}).get("rel") or ""),
        "match_key": str(match_key or ""),
        "match_mode": str(match_mode or ""),
        "audio_match_state": str(audio_state or "missing"),
        "source_folder": "|".join(dict.fromkeys(source_parts)),
        "model_candidates": int(model_candidate_count),
        "product_candidates": int(product_candidate_count),
    }


def _pick_optional_support_entry(
    entries: list[dict[str, Any]],
    *,
    pair_info: dict[str, Any],
    used_rels: set[str],
) -> tuple[dict[str, Any] | None, str]:
    if not entries:
        return None, "missing"
    preferred_fields = ["exact_stem", "numeric_stem", "normalized_stem"]
    match_mode = str(pair_info.get("match_mode") or "")
    if match_mode in preferred_fields:
        preferred_fields = [match_mode] + [f for f in preferred_fields if f != match_mode]
    for field in preferred_fields:
        key = str(pair_info.get(field) or "")
        if not key:
            continue
        candidates = [e for e in entries if str(e.get(field) or "") == key and str(e.get("rel") or "") not in used_rels]
        candidates = sorted(candidates, key=_batch_match_sort_key)
        if len(candidates) == 1:
            picked = candidates[0]
            used_rels.add(str(picked.get("rel") or ""))
            return picked, "matched"
        if len(candidates) > 1:
            return None, "ambiguous"
    return None, "missing"


def _format_batch_pairing_error(
    *,
    ambiguous: list[str],
    missing_models: list[dict[str, Any]],
    missing_products: list[dict[str, Any]],
) -> str:
    parts: list[str] = []
    if ambiguous:
        parts.append("存在歧义配对: " + "; ".join(sorted(dict.fromkeys(ambiguous))))
    if missing_products:
        details = ", ".join(str(e.get("rel") or "") for e in sorted(missing_products, key=_batch_match_sort_key)[:5])
        parts.append(f"以下模特图缺少对应商品图: {details}")
    if missing_models:
        details = ", ".join(str(e.get("rel") or "") for e in sorted(missing_models, key=_batch_match_sort_key)[:5])
        parts.append(f"以下商品图缺少对应模特图: {details}")
    return "；".join(parts) if parts else "未识别到稳定的图像配对规则"


def _build_strict_batch_items(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    image_entries = [e for e in entries if e.get("kind") == "image"]
    model_entries = sorted([e for e in image_entries if e.get("role") == "model"], key=_batch_match_sort_key)
    product_entries = sorted([e for e in image_entries if e.get("role") == "product"], key=_batch_match_sort_key)
    if not model_entries or not product_entries:
        return []

    fields = ["exact_stem", "numeric_stem", "normalized_stem"]
    used_models: set[str] = set()
    used_products: set[str] = set()
    pairs: list[dict[str, Any]] = []
    ambiguous: list[str] = []

    for field in fields:
        model_map: dict[str, list[dict[str, Any]]] = {}
        product_map: dict[str, list[dict[str, Any]]] = {}
        for entry in model_entries:
            if str(entry.get("rel") or "") in used_models:
                continue
            key = str(entry.get(field) or "")
            if key:
                model_map.setdefault(key, []).append(entry)
        for entry in product_entries:
            if str(entry.get("rel") or "") in used_products:
                continue
            key = str(entry.get(field) or "")
            if key:
                product_map.setdefault(key, []).append(entry)
        for key in sorted(set(model_map.keys()) & set(product_map.keys())):
            models = sorted(model_map.get(key) or [], key=_batch_match_sort_key)
            products = sorted(product_map.get(key) or [], key=_batch_match_sort_key)
            if len(models) != 1 or len(products) != 1:
                ambiguous.append(f"{field}:{key}")
                continue
            model_entry = models[0]
            product_entry = products[0]
            used_models.add(str(model_entry.get("rel") or ""))
            used_products.add(str(product_entry.get("rel") or ""))
            pairs.append(
                {
                    "match_key": key,
                    "match_mode": field,
                    "model": model_entry,
                    "product": product_entry,
                    "exact_stem": str(model_entry.get("exact_stem") or product_entry.get("exact_stem") or ""),
                    "numeric_stem": str(model_entry.get("numeric_stem") or product_entry.get("numeric_stem") or ""),
                    "normalized_stem": str(model_entry.get("normalized_stem") or product_entry.get("normalized_stem") or ""),
                    "model_candidate_count": len(models),
                    "product_candidate_count": len(products),
                }
            )

    missing_models = [e for e in product_entries if str(e.get("rel") or "") not in used_products]
    missing_products = [e for e in model_entries if str(e.get("rel") or "") not in used_models]
    if ambiguous or missing_models or missing_products:
        raise RuntimeError(
            _format_batch_pairing_error(
                ambiguous=ambiguous,
                missing_models=missing_models,
                missing_products=missing_products,
            )
        )
    if not pairs:
        return []

    audio_entries = sorted([e for e in entries if e.get("kind") == "audio"], key=_batch_match_sort_key)
    video_entries = sorted([e for e in entries if e.get("kind") == "video"], key=_batch_match_sort_key)
    used_audio: set[str] = set()
    used_video: set[str] = set()
    out: list[dict[str, Any]] = []
    sorted_pairs = sorted(
        pairs,
        key=lambda item: (
            str(item.get("match_key") or ""),
            str(item.get("match_mode") or ""),
            _batch_match_sort_key(item.get("model") or {}),
        ),
    )
    for idx, pair in enumerate(sorted_pairs, start=1):
        audio_entry, audio_state = _pick_optional_support_entry(audio_entries, pair_info=pair, used_rels=used_audio)
        video_entry, _ = _pick_optional_support_entry(video_entries, pair_info=pair, used_rels=used_video)
        out.append(
            _build_strict_batch_item_from_pair(
                idx=idx,
                model_entry=pair["model"],
                product_entry=pair["product"],
                match_key=str(pair.get("match_key") or ""),
                match_mode=str(pair.get("match_mode") or ""),
                audio_entry=audio_entry,
                audio_state=audio_state,
                video_entry=video_entry,
                model_candidate_count=int(pair.get("model_candidate_count") or 0),
                product_candidate_count=int(pair.get("product_candidate_count") or 0),
            )
        )
    return out


def _should_try_strict_batch_matching(entries: list[dict[str, Any]]) -> bool:
    if not entries:
        return False
    image_entries = [e for e in entries if e.get("kind") == "image"]
    if not image_entries:
        return False
    roles = {str(e.get("role") or "") for e in image_entries if str(e.get("role") or "")}
    role_folders = {str(e.get("folder") or "") for e in image_entries if str(e.get("role") or "") in {"model", "product"}}
    if any(e.get("kind") == "audio" for e in entries):
        return True
    if "model" in roles and "product" in roles and len(role_folders) > 1:
        return True
    folder_image_counts: dict[str, int] = {}
    for entry in image_entries:
        folder = str(entry.get("folder") or "")
        folder_image_counts[folder] = folder_image_counts.get(folder, 0) + 1
    if any(v > 2 for v in folder_image_counts.values()):
        return True
    seen: dict[tuple[str, str], int] = {}
    for entry in image_entries:
        for field in ("exact_stem", "numeric_stem", "normalized_stem"):
            key = str(entry.get(field) or "")
            if not key:
                continue
            seen_key = (field, key)
            seen[seen_key] = seen.get(seen_key, 0) + 1
            if seen[seen_key] > 1:
                return True
    return False


def _scan_batch_items(root_dir: Path) -> list[dict[str, Any]]:
    entries = _collect_batch_media_entries_from_dir(root_dir)
    legacy_items = _legacy_scan_batch_items(root_dir)
    if _should_try_strict_batch_matching(entries):
        strict_items = _build_strict_batch_items(entries)
        if strict_items:
            return strict_items
    return legacy_items


def _strip_batch_meta_params(params: dict[str, Any]) -> dict[str, Any]:
    out = dict(params or {})
    for k in [
        "batch_mode",
        "batch_groups_estimated",
        "batch_params",
        "common_params",
        "cycle_params_on_shortage",
        "use_common_params_on_shortage",
    ]:
        out.pop(k, None)
    return out


def _has_any(text: str, keywords: set[str]) -> bool:
    low = str(text or "").lower()
    return any(k in low for k in (keywords or set()) if k)


def _is_macos_junk_posix(posix: PurePosixPath) -> bool:
    parts = tuple(getattr(posix, "parts", ()) or ())
    if "__MACOSX" in parts:
        return True
    name = str(parts[-1] if parts else "")
    if not name:
        return False
    if name.startswith("._"):
        return True
    if name in {".DS_Store", "Thumbs.db"}:
        return True
    return False


def _is_macos_junk_path(path: Path) -> bool:
    try:
        if "__MACOSX" in path.parts:
            return True
        name = str(path.name or "")
        if name.startswith("._"):
            return True
        if name in {".DS_Store", "Thumbs.db"}:
            return True
    except Exception:
        return False
    return False


def _zip_category(zip_path: Path) -> str:
    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            img = 0
            vid = 0
            aud = 0
            other = 0
            for m in zf.infolist():
                name = str(getattr(m, "filename", "") or "")
                if not name or name.endswith("/"):
                    continue
                norm = name.replace("\\", "/")
                posix = PurePosixPath(norm)
                if posix.is_absolute() or ".." in posix.parts:
                    continue
                if _is_macos_junk_posix(posix):
                    continue
                suf = Path(norm).suffix.lower()
                if suf in IMAGE_EXTS:
                    img += 1
                elif suf in VIDEO_EXTS:
                    vid += 1
                elif suf in AUDIO_EXTS:
                    aud += 1
                else:
                    other += 1
            total = img + vid + aud + other
            if total <= 0:
                return "empty"
            def ratio(x: int) -> float:
                return float(x) / float(total)
            if img > 0 and ratio(img) >= 0.8:
                return "images"
            if vid > 0 and ratio(vid) >= 0.8:
                return "videos"
            if aud > 0 and ratio(aud) >= 0.8:
                return "audios"
            return "mixed"
    except Exception:
        return "mixed"


def _scan_zip_media(zip_path: Path) -> dict[str, dict[str, list[str]]]:
    grouped: dict[str, dict[str, list[str]]] = {}
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for m in zf.infolist():
            name = str(getattr(m, "filename", "") or "")
            if not name or name.endswith("/"):
                continue
            norm = name.replace("\\", "/")
            posix = PurePosixPath(norm)
            if posix.is_absolute() or ".." in posix.parts:
                continue
            if _is_macos_junk_posix(posix):
                continue
            suf = Path(norm).suffix.lower()
            if suf not in IMAGE_EXTS and suf not in VIDEO_EXTS and suf not in AUDIO_EXTS:
                continue
            rel = str(PurePosixPath(*posix.parts))
            folder = str(PurePosixPath(*posix.parts[:-1])) if len(posix.parts) > 1 else ""
            g = grouped.setdefault(folder, {"images": [], "videos": [], "audios": []})
            if suf in IMAGE_EXTS:
                g["images"].append(rel)
            elif suf in VIDEO_EXTS:
                g["videos"].append(rel)
            else:
                g["audios"].append(rel)
    for g in grouped.values():
        g["images"] = sorted(set(g.get("images") or []))
        g["videos"] = sorted(set(g.get("videos") or []))
        g["audios"] = sorted(set(g.get("audios") or []))
    return grouped


def _pick_first_by_keywords(paths: list[str], keywords: set[str]) -> str:
    for p in paths or []:
        if _has_any(Path(p).name, keywords):
            return p
    return str((paths or [""])[0] or "")


def _batch_params_for_index(batch_params: list[Any], *, idx: int, use_cycle: bool) -> dict[str, Any]:
    picked: dict[str, Any] = {}
    if batch_params and len(batch_params) >= idx:
        bp = batch_params[idx - 1]
        picked = bp if isinstance(bp, dict) else {}
    elif batch_params and use_cycle:
        bp = batch_params[(idx - 1) % len(batch_params)]
        picked = bp if isinstance(bp, dict) else {}
    return _strip_batch_meta_params(picked) if isinstance(picked, dict) else {}


def _apply_batch_params_to_items(items: list[dict[str, Any]], *, batch_params: list[Any], use_cycle: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        merged = dict(item)
        merged["params"] = _batch_params_for_index(batch_params, idx=idx, use_cycle=use_cycle)
        out.append(merged)
    return out


def _repair_strict_item_support_media(items: list[dict[str, Any]], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return items
    audio_entries = sorted([e for e in entries if e.get("kind") == "audio"], key=_batch_match_sort_key)
    video_entries = sorted([e for e in entries if e.get("kind") == "video"], key=_batch_match_sort_key)
    used_audio: set[str] = set()
    used_video: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        merged = dict(item)
        match_mode = str(merged.get("match_mode") or "")
        match_key = str(merged.get("match_key") or "")
        pair_info = {"match_mode": match_mode}
        if match_mode and match_key:
            pair_info[match_mode] = match_key
        if (not str(merged.get("audio") or "").strip()) and match_mode and match_key:
            audio_entry, audio_state = _pick_optional_support_entry(audio_entries, pair_info=pair_info, used_rels=used_audio)
            merged["audio"] = str((audio_entry or {}).get("rel") or "")
            merged["audio_match_state"] = str(audio_state or "missing")
        if (not str(merged.get("camera_video") or "").strip()) and match_mode and match_key:
            video_entry, _ = _pick_optional_support_entry(video_entries, pair_info=pair_info, used_rels=used_video)
            if video_entry:
                merged["camera_video"] = str(video_entry.get("rel") or "")
        out.append(merged)
    return out


def _pick_support_rel_by_match(paths: list[str], *, match_mode: str, match_key: str, used_paths: set[str]) -> tuple[str, str]:
    if not match_mode or not match_key:
        return "", "missing"
    candidates: list[str] = []
    for rel in sorted(set(paths)):
        if rel in used_paths:
            continue
        key_info = _normalize_batch_match_keys(Path(PurePosixPath(rel).name).stem)
        if str(key_info.get(match_mode) or "") == match_key:
            candidates.append(rel)
    if len(candidates) == 1:
        used_paths.add(candidates[0])
        return candidates[0], "matched"
    if len(candidates) > 1:
        return "", "ambiguous"
    return "", "missing"


def _build_batch_media_entries_from_slots(slots: dict[str, dict[str, list[str]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _, slot in sorted(slots.items(), key=lambda kv: kv[0]):
        for rel in sorted(set(slot.get("model_images") or [])):
            out.append(_make_batch_media_entry(rel, kind="image", role_hint="model"))
        for rel in sorted(set(slot.get("product_images") or [])):
            out.append(_make_batch_media_entry(rel, kind="image", role_hint="product"))
        for rel in sorted(set(slot.get("images") or [])):
            out.append(_make_batch_media_entry(rel, kind="image"))
        for rel in sorted(set(slot.get("videos") or [])):
            out.append(_make_batch_media_entry(rel, kind="video"))
        for rel in sorted(set(slot.get("audios") or [])):
            out.append(_make_batch_media_entry(rel, kind="audio"))
    return sorted(out, key=_batch_match_sort_key)


def _build_batch_payload_from_uploaded_zips(
    *,
    zips: list[dict[str, str]],
    params: dict[str, Any],
) -> dict[str, Any]:
    batch_params = params.get("batch_params") if isinstance(params.get("batch_params"), list) else []
    use_cycle = bool(params.get("cycle_params_on_shortage"))
    defaults = _strip_batch_meta_params(params)

    slots: dict[str, dict[str, list[str]]] = {}
    zip_paths: list[str] = []
    zip_meta: list[dict[str, Any]] = []
    image_zip_indices: list[int] = []
    image_zip_roles: dict[int, str] = {}

    for i, z in enumerate(zips or []):
        zp = Path(str(z.get("path") or "")).resolve()
        if not zp.exists():
            continue
        zip_paths.append(str(zp))
        name = str(z.get("name") or zp.name)
        cat = _zip_category(zp)
        groups = _scan_zip_media(zp)
        role = cat
        if cat == "images":
            image_zip_indices.append(i)
            if _has_any(name, BATCH_MODEL_KEYWORDS):
                role = "model_images"
            elif _has_any(name, BATCH_PRODUCT_KEYWORDS):
                role = "product_images"
            else:
                role = "images"
            if role == "images":
                folder_keys = list((groups or {}).keys())
                found_model = any(_has_any(f, BATCH_MODEL_KEYWORDS) for f in folder_keys)
                found_product = any(_has_any(f, BATCH_PRODUCT_KEYWORDS) for f in folder_keys)
                if found_model and (not found_product):
                    role = "model_images"
                elif found_product and (not found_model):
                    role = "product_images"
            image_zip_roles[i] = role
        elif cat == "videos":
            role = "videos"
        elif cat == "audios":
            role = "audios"
        else:
            role = "bundle"
        zip_meta.append({"i": i, "path": str(zp), "name": name, "cat": cat, "role": role, "groups": groups})

    if image_zip_indices:
        has_named_role = any(image_zip_roles.get(i) in {"model_images", "product_images"} for i in image_zip_indices)
        if (not has_named_role) and len(image_zip_indices) >= 2:
            image_zip_roles[image_zip_indices[0]] = "model_images"
            image_zip_roles[image_zip_indices[1]] = "product_images"

    for meta in zip_meta:
        i = int(meta.get("i") or 0)
        zp = Path(str(meta.get("path") or "")).resolve()
        if not zp.exists():
            continue
        cat = str(meta.get("cat") or "")
        role = str(meta.get("role") or "")
        if cat == "images":
            role = str(image_zip_roles.get(i) or role or "images")
        groups = meta.get("groups") if isinstance(meta.get("groups"), dict) else _scan_zip_media(zp)
        for folder, g in groups.items():
            slot = slots.setdefault(folder or "root", {"model_images": [], "product_images": [], "images": [], "videos": [], "audios": []})
            pref = f"z{i}/"
            if role == "model_images":
                slot["model_images"] += [pref + p for p in (g.get("images") or [])]
                slot["videos"] += [pref + p for p in (g.get("videos") or [])]
                slot["audios"] += [pref + p for p in (g.get("audios") or [])]
            elif role == "product_images":
                slot["product_images"] += [pref + p for p in (g.get("images") or [])]
                slot["videos"] += [pref + p for p in (g.get("videos") or [])]
                slot["audios"] += [pref + p for p in (g.get("audios") or [])]
            elif role == "images":
                slot["images"] += [pref + p for p in (g.get("images") or [])]
                slot["videos"] += [pref + p for p in (g.get("videos") or [])]
                slot["audios"] += [pref + p for p in (g.get("audios") or [])]
            elif role == "videos":
                slot["videos"] += [pref + p for p in (g.get("videos") or [])]
            elif role == "audios":
                slot["audios"] += [pref + p for p in (g.get("audios") or [])]
            else:
                slot["images"] += [pref + p for p in (g.get("images") or [])]
                slot["videos"] += [pref + p for p in (g.get("videos") or [])]
                slot["audios"] += [pref + p for p in (g.get("audios") or [])]

    items: list[dict[str, Any]] = []
    strict_entries = _build_batch_media_entries_from_slots(slots)
    if _should_try_strict_batch_matching(strict_entries):
        strict_items = _build_strict_batch_items(strict_entries)
        if strict_items:
            items = _apply_batch_params_to_items(strict_items, batch_params=batch_params, use_cycle=use_cycle)
            items = _repair_strict_item_support_media(items, strict_entries)
            all_audio_paths = sorted({rel for slot in slots.values() for rel in (slot.get("audios") or [])})
            all_video_paths = sorted({rel for slot in slots.values() for rel in (slot.get("videos") or [])})
            used_audio_paths = {str(it.get("audio") or "") for it in items if str(it.get("audio") or "")}
            used_video_paths = {str(it.get("camera_video") or "") for it in items if str(it.get("camera_video") or "")}
            repaired_items: list[dict[str, Any]] = []
            for item in items:
                merged = dict(item)
                match_mode = str(merged.get("match_mode") or "")
                match_key = str(merged.get("match_key") or "")
                if not str(merged.get("audio") or "").strip():
                    audio_rel, audio_state = _pick_support_rel_by_match(all_audio_paths, match_mode=match_mode, match_key=match_key, used_paths=used_audio_paths)
                    if audio_rel or audio_state != "missing":
                        merged["audio"] = audio_rel
                        merged["audio_match_state"] = audio_state
                if not str(merged.get("camera_video") or "").strip():
                    video_rel, _ = _pick_support_rel_by_match(all_video_paths, match_mode=match_mode, match_key=match_key, used_paths=used_video_paths)
                    if video_rel:
                        merged["camera_video"] = video_rel
                repaired_items.append(merged)
            items = repaired_items

    model_folders = [k for k, v in slots.items() if isinstance(v, dict) and (v.get("model_images") or [])]
    product_folders = [k for k, v in slots.items() if isinstance(v, dict) and (v.get("product_images") or [])]
    if (not items) and len(model_folders) == 1 and len(product_folders) == 1 and model_folders[0] != product_folders[0]:
        mslot = slots.get(model_folders[0]) or {}
        pslot = slots.get(product_folders[0]) or {}
        mimgs = sorted(set(mslot.get("model_images") or []))
        pimgs = sorted(set(pslot.get("product_images") or []))
        vids = sorted(set((mslot.get("videos") or []) + (pslot.get("videos") or [])))
        auds = sorted(set((mslot.get("audios") or []) + (pslot.get("audios") or [])))
        total = max(len(mimgs), len(pimgs))
        if total > 0 and mimgs and pimgs:
            for idx in range(1, total + 1):
                model_img = mimgs[(idx - 1) % len(mimgs)]
                product_img = pimgs[(idx - 1) % len(pimgs)]
                camera_video = vids[(idx - 1) % len(vids)] if vids else ""
                audio = auds[(idx - 1) % len(auds)] if auds else ""
                items.append(
                    {
                        "id": f"item_{idx}",
                        "model_image": str(model_img or ""),
                        "product_image": str(product_img or model_img or ""),
                        "camera_video": str(camera_video or ""),
                        "audio": str(audio or ""),
                        "match_key": str(idx),
                        "match_mode": "legacy_zip_sequence",
                        "audio_match_state": "matched" if audio else "missing",
                        "source_folder": f"{model_folders[0]}|{product_folders[0]}",
                        "params": _batch_params_for_index(batch_params, idx=idx, use_cycle=use_cycle),
                    }
                )

    if not items:
        for idx, (folder, slot) in enumerate(sorted(slots.items(), key=lambda kv: kv[0]), start=1):
            imgs_model = sorted(set(slot.get("model_images") or []))
            imgs_product = sorted(set(slot.get("product_images") or []))
            imgs_generic = sorted(set(slot.get("images") or []))
            vids = sorted(set(slot.get("videos") or []))
            auds = sorted(set(slot.get("audios") or []))

            model_img = _pick_first_by_keywords(imgs_model, BATCH_MODEL_KEYWORDS) if imgs_model else ""
            product_img = _pick_first_by_keywords(imgs_product, BATCH_PRODUCT_KEYWORDS) if imgs_product else ""
            if not model_img and imgs_generic:
                model_img = _pick_first_by_keywords(imgs_generic, BATCH_MODEL_KEYWORDS) or imgs_generic[0]
            if not product_img and imgs_generic:
                cand = [p for p in imgs_generic if p != model_img] or imgs_generic
                product_img = _pick_first_by_keywords(cand, BATCH_PRODUCT_KEYWORDS) or (cand[0] if cand else "")
            if not model_img and (imgs_generic or imgs_product):
                pool = imgs_generic or imgs_product
                model_img = pool[0] if pool else ""
            if not product_img and (imgs_generic or imgs_model):
                pool = imgs_generic or imgs_model
                product_img = pool[1] if len(pool) > 1 else (pool[0] if pool else "")

            camera_video = _pick_first_by_keywords(vids, BATCH_VIDEO_KEYWORDS) if vids else ""
            audio = _pick_first_by_keywords(auds, BATCH_AUDIO_KEYWORDS) if auds else ""
            item_id = str(folder or f"item_{idx}")
            items.append(
                {
                    "id": item_id,
                    "model_image": str(model_img or ""),
                    "product_image": str(product_img or model_img or ""),
                    "camera_video": str(camera_video or ""),
                    "audio": str(audio or ""),
                    "match_key": str(folder or item_id),
                    "match_mode": "legacy_zip_folder",
                    "audio_match_state": "matched" if audio else "missing",
                    "source_folder": str(folder or ""),
                    "params": _batch_params_for_index(batch_params, idx=idx, use_cycle=use_cycle),
                }
            )

    if items:
        all_audio_paths = sorted({rel for slot in slots.values() for rel in (slot.get("audios") or [])})
        all_video_paths = sorted({rel for slot in slots.values() for rel in (slot.get("videos") or [])})
        used_audio_paths = {str(it.get("audio") or "") for it in items if str(it.get("audio") or "")}
        used_video_paths = {str(it.get("camera_video") or "") for it in items if str(it.get("camera_video") or "")}
        finalized_items: list[dict[str, Any]] = []
        for item in items:
            merged = dict(item)
            match_mode = str(merged.get("match_mode") or "")
            match_key = str(merged.get("match_key") or "")
            if not str(merged.get("audio") or "").strip():
                audio_rel, audio_state = _pick_support_rel_by_match(all_audio_paths, match_mode=match_mode, match_key=match_key, used_paths=used_audio_paths)
                if audio_rel:
                    merged["audio"] = audio_rel
                    merged["audio_match_state"] = audio_state
            if not str(merged.get("camera_video") or "").strip():
                video_rel, _ = _pick_support_rel_by_match(all_video_paths, match_mode=match_mode, match_key=match_key, used_paths=used_video_paths)
                if video_rel:
                    merged["camera_video"] = video_rel
            finalized_items.append(merged)
        items = finalized_items

    if not zip_paths:
        raise RuntimeError("未找到可用 zip 文件")
    if not items:
        raise RuntimeError("zip 内未识别到可用素材（至少每组需要 1 张图片）")
    return {"zip_paths": zip_paths, "defaults": defaults, "items": items}


def _build_batch_payload_video_image_from_uploaded_zips(
    *,
    zips: list[dict[str, str]],
    params: dict[str, Any],
) -> dict[str, Any]:
    video_kw = {"video", "camera", "motion", "运镜", "镜头", "视频"}
    image_kw = {"image", "img", "picture", "photo", "图片", "图"}

    batch_params = params.get("batch_params") if isinstance(params.get("batch_params"), list) else []
    use_cycle = bool(params.get("cycle_params_on_shortage"))
    defaults = _strip_batch_meta_params(params)

    slots: dict[str, dict[str, list[str]]] = {}
    zip_paths: list[str] = []
    zip_meta: list[dict[str, Any]] = []

    for i, z in enumerate(zips or []):
        zp = Path(str(z.get("path") or "")).resolve()
        if not zp.exists():
            continue
        zip_paths.append(str(zp))
        name = str(z.get("name") or zp.name)
        cat = _zip_category(zp)
        groups = _scan_zip_media(zp)
        role = cat
        if cat == "videos":
            role = "videos"
        elif cat == "images":
            role = "images"
        elif cat == "mixed":
            role = "bundle"
        else:
            role = cat
        if role in {"images", "videos"}:
            folder_keys = list((groups or {}).keys())
            if role == "images" and _has_any(name, video_kw) and (not _has_any(name, image_kw)):
                role = "videos"
            if role == "videos" and _has_any(name, image_kw) and (not _has_any(name, video_kw)):
                role = "images"
            if role == "images" and any(_has_any(f, video_kw) for f in folder_keys) and (not any(_has_any(f, image_kw) for f in folder_keys)):
                role = "videos"
            if role == "videos" and any(_has_any(f, image_kw) for f in folder_keys) and (not any(_has_any(f, video_kw) for f in folder_keys)):
                role = "images"

        zip_meta.append({"i": i, "path": str(zp), "role": role, "groups": groups})

    for meta in zip_meta:
        i = int(meta.get("i") or 0)
        role = str(meta.get("role") or "")
        groups = meta.get("groups") if isinstance(meta.get("groups"), dict) else {}
        pref = f"z{i}/"
        for folder, g in groups.items():
            slot = slots.setdefault(folder or "root", {"images": [], "videos": [], "audios": []})
            if role == "images":
                slot["images"] += [pref + p for p in (g.get("images") or [])]
            elif role == "videos":
                slot["videos"] += [pref + p for p in (g.get("videos") or [])]
            else:
                slot["images"] += [pref + p for p in (g.get("images") or [])]
                slot["videos"] += [pref + p for p in (g.get("videos") or [])]
                slot["audios"] += [pref + p for p in (g.get("audios") or [])]

    items: list[dict[str, Any]] = []

    folders_with_both = [k for k, v in slots.items() if (v.get("images") or []) and (v.get("videos") or [])]
    if folders_with_both:
        for idx, folder in enumerate(sorted(set(folders_with_both)), start=1):
            slot = slots.get(folder) or {}
            imgs = sorted(set(slot.get("images") or []))
            vids = sorted(set(slot.get("videos") or []))
            if not imgs or not vids:
                continue
            picked: dict[str, Any] = {}
            if batch_params and len(batch_params) >= idx:
                bp = batch_params[idx - 1]
                picked = bp if isinstance(bp, dict) else {}
            elif batch_params and use_cycle:
                bp = batch_params[(idx - 1) % len(batch_params)]
                picked = bp if isinstance(bp, dict) else {}
            picked = _strip_batch_meta_params(picked) if isinstance(picked, dict) else {}
            items.append({"id": str(folder or f"item_{idx}"), "video": str(vids[0]), "image": str(imgs[0]), "params": picked})

    if not items:
        video_only_folders = [k for k, v in slots.items() if (v.get("videos") or []) and not (v.get("images") or [])]
        image_only_folders = [k for k, v in slots.items() if (v.get("images") or []) and not (v.get("videos") or [])]
        if len(video_only_folders) == 1 and len(image_only_folders) == 1 and video_only_folders[0] != image_only_folders[0]:
            vslot = slots.get(video_only_folders[0]) or {}
            islot = slots.get(image_only_folders[0]) or {}
            vids = sorted(set(vslot.get("videos") or []))
            imgs = sorted(set(islot.get("images") or []))
            total = max(len(vids), len(imgs))
            if total > 0 and vids and imgs:
                for idx in range(1, total + 1):
                    vid = vids[(idx - 1) % len(vids)]
                    img = imgs[(idx - 1) % len(imgs)]
                    picked: dict[str, Any] = {}
                    if batch_params and len(batch_params) >= idx:
                        bp = batch_params[idx - 1]
                        picked = bp if isinstance(bp, dict) else {}
                    elif batch_params and use_cycle:
                        bp = batch_params[(idx - 1) % len(batch_params)]
                        picked = bp if isinstance(bp, dict) else {}
                    picked = _strip_batch_meta_params(picked) if isinstance(picked, dict) else {}
                    items.append({"id": f"item_{idx}", "video": str(vid), "image": str(img), "params": picked})

    if not zip_paths:
        raise RuntimeError("未找到可用 zip 文件")
    if not items:
        raise RuntimeError("zip 内未识别到可用素材（每条至少需要 1 个视频 + 1 张图片）")
    return {"zip_paths": zip_paths, "defaults": defaults, "items": items}


def _plan_batch_params_with_gemini(
    *,
    user_prompt: str,
    items: list[dict[str, Any]],
    defaults: dict[str, Any],
    gemini_host: str,
    gemini_key: str,
    gemini_port: str | None,
) -> dict[str, Any]:
    schema_text = str(user_prompt or "").strip()
    if not schema_text:
        raise RuntimeError("参数要求不能为空")
    plan_prompt = (
        "你是批量视频参数生成器。请输出严格 JSON（不要代码块、不要多余文字）。\n"
        "用户会提供“参数要求”，你必须按要求生成每条 item 的 params。\n"
        "输出结构：\n"
        "{\n"
        '  "defaults": { ... },\n'
        '  "items": [\n'
        "    {\n"
        '      "id": "item id",\n'
        '      "model_image": "相对路径",\n'
        '      "product_image": "相对路径",\n'
        '      "camera_video": "相对路径或空",\n'
        '      "params": { ... }\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "约束：\n"
        "- items 的条数必须与输入 items 一致\n"
        "- model_image/product_image/camera_video 必须原样引用输入里给出的相对路径，不要编造\n"
        "- params 字段由“参数要求”决定，字段名与结构按用户要求生成\n"
        f"参数要求：\n{schema_text}\n"
        f"默认参数（供参考，可在 defaults 中复用）：\n{json.dumps(defaults, ensure_ascii=False)}\n"
        f"输入 items：\n{json.dumps(items, ensure_ascii=False)}\n"
    )
    plan = get_gemini.request_gemini3_pro_json(
        user_input="生成批量参数",
        host=gemini_host,
        api_key=gemini_key,
        system_prompt=plan_prompt,
        port=gemini_port,
        parameters="",
    )
    if not isinstance(plan, dict) or not plan.get("ok"):
        raise RuntimeError(f"Gemini 规划失败: {plan}")
    parsed = plan.get("parsed")
    if not isinstance(parsed, dict):
        raise RuntimeError("Gemini 未返回 JSON 对象")
    return parsed


def _run_batch_create_video(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    zip_paths_value = payload.get("zip_paths")
    zip_paths: list[Path] = []
    if isinstance(zip_paths_value, list):
        for z in zip_paths_value:
            t = str(z or "").strip()
            if not t:
                continue
            p = Path(t).resolve()
            if p.exists():
                zip_paths.append(p)
    if not zip_paths:
        zip_path_text = str(payload.get("zip_path") or "").strip()
        if not zip_path_text:
            raise RuntimeError("缺少 zip_path")
        zip_path = Path(zip_path_text).resolve()
        if not zip_path.exists():
            raise RuntimeError("zip 文件不存在")
        zip_paths = [zip_path]
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not items:
        raise RuntimeError("批量 items 不能为空")
    _emit_stage(payload, stage="parsing", status="running", message="解析文件中")

    with db() as conn:
        runtime = _get_runtime_config(conn)
    runtime_defaults: dict[str, Any] = {}
    for key in (
        "runninghub_api_key",
        "upload_server_ip",
        "upload_server_port",
        "upload_file_api_key",
        "image_model_provider_base_url",
        "image_model_provider_api_key_gemini",
        "image_model_provider_api_key_gpt",
        "image_model_default_model",
        "image_model_default_model_gemini",
        "image_model_default_model_gpt",
        "image_model_priority_order",
        "llm_base_url",
        "llm_api_key",
        "llm_api_key_gemini",
        "llm_api_key_gpt",
        "llm_default_model",
        "llm_default_model_gemini",
        "llm_default_model_gpt",
        "llm_model_priority_order",
        "create_audio_app_id",
        "video_app_id",
        "instance_type",
        "use_personal_queue",
    ):
        value = payload.get(key)
        if not str(value or "").strip():
            value = runtime.get(key)
        if str(value or "").strip():
            runtime_defaults[key] = value
    oral_chain = _workflow_chain_from_payload(
        payload,
        "oral_digital_human_workflow_ids",
        [runtime.get("create_audio_app_id"), runtime.get("create_video_app_id"), runtime.get("video_app_id")],
    )
    if oral_chain:
        runtime_defaults["oral_digital_human_workflow_ids"] = oral_chain
    digital_chain = _workflow_chain_from_payload(
        payload,
        "digital_human_workflow_ids",
        runtime.get("digital_human_workflow_ids") or [],
    )
    if digital_chain:
        runtime_defaults["digital_human_workflow_ids"] = digital_chain
    if not str(runtime_defaults.get("video_app_id") or "").strip():
        runtime_defaults["video_app_id"] = runtime.get("create_video_app_id") or runtime.get("video_app_id")
    if not str(runtime_defaults.get("create_audio_app_id") or "").strip():
        runtime_defaults["create_audio_app_id"] = runtime.get("create_audio_app_id")

    workdir = _build_task_workdir(task_id)
    src_dir = workdir / "src"
    for i, zp in enumerate(zip_paths):
        _extract_zip_to_dir(zp, src_dir / f"z{i}")
    _emit_stage(payload, stage="parse_result", status="success", message="解析结果", data={"zip_count": len(zip_paths), "item_count": len(items)})

    out_dir = workdir / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    agg_runninghub_usage: dict[str, Any] = {}
    agg_nano_images = 0
    agg_gemini_input_tokens = 0
    agg_gemini_output_tokens = 0
    charged_total_cents = 0
    stopped_for_balance = False
    stop_reason = ""
    stop_from_index = 0
    rh_task_ids: list[str] = []

    user_id = _to_int(payload.get("_user_id"), 0)
    is_admin_user = False
    if user_id > 0:
        with db() as conn:
            row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),)).fetchone()
            is_admin_user = bool(int(row["is_admin"] or 0)) if row else False

    for idx, it in enumerate(items, start=1):
        if user_id > 0 and (not is_admin_user):
            with db() as conn:
                row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (int(user_id),)).fetchone()
                bal = int(row["balance_cents"]) if row else 0
            if bal <= 0:
                stopped_for_balance = True
                stop_reason = f"余额不足（当前 {bal} 分），已中断批量任务"
                stop_from_index = int(idx)
                break
        if not isinstance(it, dict):
            continue
        sub_id = f"{task_id}_item_{idx}"
        model_rel = str(it.get("model_image") or "").strip()
        product_rel = str(it.get("product_image") or model_rel).strip()
        camera_rel = str(it.get("camera_video") or "").strip()
        audio_rel = str(it.get("audio") or it.get("audio_file") or "").strip()
        if not model_rel:
            results.append({"id": it.get("id") or f"item_{idx}", "ok": False, "error": "缺少 model_image"})
            continue
        try:
            model_path = _resolve_batch_media_path(src_dir, model_rel, field_name="model_image", required=True)
            product_path = _resolve_batch_media_path(src_dir, product_rel, field_name="product_image", required=True)
            camera_path = _resolve_batch_media_path(src_dir, camera_rel, field_name="camera_video") if camera_rel else None
            audio_path = _resolve_batch_media_path(src_dir, audio_rel, field_name="audio") if audio_rel else None
        except RuntimeError as exc:
            results.append({"id": it.get("id") or f"item_{idx}", "ok": False, "error": str(exc)})
            continue
        if model_path is None or product_path is None or not model_path.is_file() or not product_path.is_file():
            results.append({"id": it.get("id") or f"item_{idx}", "ok": False, "error": "图片文件不存在"})
            continue
        if camera_rel and (camera_path is None or not camera_path.is_file()):
            results.append({"id": it.get("id") or f"item_{idx}", "ok": False, "error": f"运镜视频文件不存在: {camera_rel}"})
            continue
        if audio_rel and (audio_path is None or not audio_path.is_file()):
            results.append({"id": it.get("id") or f"item_{idx}", "ok": False, "error": f"音频文件不存在: {audio_rel}"})
            continue

        params = it.get("params") if isinstance(it.get("params"), dict) else {}
        one_payload: dict[str, Any] = {}
        one_payload.update(runtime_defaults)
        one_payload.update(defaults)
        one_payload.update(params)
        one_payload["_username"] = str(payload.get("_username") or "").strip()
        one_payload["model_image_local_path"] = str(model_path)
        one_payload["product_image_local_path"] = str(product_path)
        if camera_path is not None:
            one_payload["camera_video_local_path"] = str(camera_path)
        if audio_path is not None:
            one_payload["audio_local_path"] = str(audio_path)

        try:
            _emit_stage(payload, stage="uploading", status="running", message="上传文件中", data={"item_index": idx, "item_id": it.get("id") or f"item_{idx}"})
            _emit_stage(payload, stage="processing", status="running", message="正在生成视频", data={"item_index": idx, "item_id": it.get("id") or f"item_{idx}"})
            output = _run_create_video_with_doubao(sub_id, one_payload)
            ok = _to_bool(output.get("ok"), False)
            one_rh_id = str(output.get("runninghub_task_id") or "").strip()
            if one_rh_id:
                rh_task_ids.append(one_rh_id)
            video_path = str(output.get("video_path") or "").strip()
            moved_path = ""
            if ok and video_path and Path(video_path).exists():
                safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(it.get("id") or "video")).strip("_") or "video"
                target = out_dir / f"{idx:03d}_{safe_id}.mp4"
                target.write_bytes(Path(video_path).read_bytes())
                moved_path = str(target)
            if isinstance(output.get("runninghub_usage"), dict):
                agg_runninghub_usage = _sum_usage([agg_runninghub_usage, output.get("runninghub_usage")])
            agg_nano_images += _to_int(output.get("nano_images"), 0)
            agg_gemini_input_tokens += _to_int(output.get("gemini_input_tokens"), 0)
            agg_gemini_output_tokens += _to_int(output.get("gemini_output_tokens"), 0)

            item_cost_cents = 0
            bal2 = 0
            if user_id > 0 and (not is_admin_user) and ok:
                with db() as conn:
                    pricing = _get_pricing_config(conn)
                    cost = compute_cost_cents(
                        runninghub_usage=output.get("runninghub_usage") if isinstance(output.get("runninghub_usage"), dict) else {},
                        rh_coins_per_10rmb=int(pricing.get("rh_coins_per_10rmb") or 2500),
                        usd_to_rmb=float(pricing.get("usd_to_rmb") or 7.2),
                        gemini_input_tokens=int(_to_int(output.get("gemini_input_tokens"), 0)),
                        gemini_output_tokens=int(_to_int(output.get("gemini_output_tokens"), 0)),
                        gemini_input_usd_per_1m=float(pricing.get("gemini_input_usd_per_1m") or 4.0),
                        gemini_output_usd_per_1m=float(pricing.get("gemini_output_usd_per_1m") or 18.0),
                        nano_images=int(_to_int(output.get("nano_images"), 0)),
                        nano_usd_per_image=float(pricing.get("nano_usd_per_image") or 0.134),
                    )
                    item_cost_cents = max(_to_int(cost.get("total_cents"), 0), 0)
                    if item_cost_cents > 0:
                        conn.execute(
                            "UPDATE users SET balance_cents = balance_cents - ?, updated_at = ? WHERE id = ?",
                            (int(item_cost_cents), _now_ts(), int(user_id)),
                        )
                        charged_total_cents += int(item_cost_cents)
                        _insert_ledger(
                            conn,
                            user_id=int(user_id),
                            typ="charge",
                            amount_cents=-int(item_cost_cents),
                            ref_task_id=f"{task_id}_item_{idx}",
                            meta={"task_type": "batch_create_video", "batch_task_id": task_id, "item_index": idx, "item_id": it.get("id") or f"item_{idx}", "cost": cost},
                        )

            results.append(
                {
                    "id": it.get("id") or f"item_{idx}",
                    "ok": ok,
                    "video_path": moved_path,
                    "runninghub_task_id": one_rh_id,
                    "error": "" if ok else str(output.get("message") or output.get("error") or ""),
                    "cost_cents": int(item_cost_cents),
                }
            )
            if ok:
                urls = []
                up = output.get("uploaded_urls")
                if isinstance(up, dict):
                    urls = [str(v) for v in up.values() if str(v).strip()]
                _emit_stage(payload, stage="upload_result", status="success", message="上传成功", data={"item_index": idx, "urls": urls, "runninghub_task_id": one_rh_id})
            else:
                _emit_stage(payload, stage="upload_result", status="failed", message="上传失败", data={"item_index": idx, "error": str(output.get("message") or output.get("error") or "")})
            if user_id > 0 and (not is_admin_user) and ok:
                with db() as conn:
                    row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (int(user_id),)).fetchone()
                    bal2 = int(row["balance_cents"]) if row else 0
                if bal2 <= 0:
                    stopped_for_balance = True
                    stop_reason = f"余额不足（当前 {bal2} 分），已中断批量任务"
                    stop_from_index = int(idx) + 1
                    break
        except Exception as exc:
            results.append({"id": it.get("id") or f"item_{idx}", "ok": False, "error": str(exc)})

    if stopped_for_balance:
        start = int(stop_from_index or 0)
        if start <= 0:
            start = len(items) + 1
        for j in range(start, len(items) + 1):
            it2 = items[j - 1] if j - 1 < len(items) else {}
            iid = it2.get("id") if isinstance(it2, dict) else None
            results.append({"id": iid or f"item_{j}", "ok": False, "error": stop_reason, "skipped": True})

    manifest_path = workdir / "results.json"
    manifest_path.write_text(_json_dumps({"items": results}), encoding="utf-8")

    zip_out = workdir / "batch_videos.zip"
    with zipfile.ZipFile(str(zip_out), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(manifest_path), arcname="results.json")
        for p in sorted(out_dir.glob("*.mp4")):
            zf.write(str(p), arcname=f"videos/{p.name}")

    ok_any = any(bool(r.get("ok")) for r in results)
    message = stop_reason or ("批量视频任务完成" if ok_any else "批量视频任务失败")
    if (not ok_any) and (not stop_reason):
        for r in results:
            err = str(r.get("error") or "").strip()
            if err:
                message = f"批量视频任务失败：{err}"
                break
    rh_task_ids = list(dict.fromkeys([x for x in rh_task_ids if str(x).strip()]))
    return {
        "ok": bool(ok_any),
        "message": message,
        "runninghub_task_id": rh_task_ids[0] if rh_task_ids else "",
        "runninghub_task_ids": rh_task_ids,
        "runninghub_usage": agg_runninghub_usage,
        "nano_images": int(agg_nano_images),
        "gemini_input_tokens": int(agg_gemini_input_tokens),
        "gemini_output_tokens": int(agg_gemini_output_tokens),
        "result_zip": str(zip_out),
        "download_path": str(zip_out),
        "items": results,
        "skip_billing": True if (user_id > 0 and (not is_admin_user)) else False,
        "billing": {"mode": "per_item", "cost_cents": int(charged_total_cents)},
    }


def _run_batch_replace_model(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    zip_paths_value = payload.get("zip_paths")
    zip_paths: list[Path] = []
    if isinstance(zip_paths_value, list):
        for z in zip_paths_value:
            t = str(z or "").strip()
            if not t:
                continue
            p = Path(t).resolve()
            if p.exists():
                zip_paths.append(p)
    if not zip_paths:
        raise RuntimeError("缺少 zip_paths")
    defaults = _normalize_replace_model_payload(payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {})
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not items:
        raise RuntimeError("批量 items 不能为空")
    _emit_stage(payload, stage="parsing", status="running", message="解析文件中")

    workdir = _build_task_workdir(task_id)
    src_dir = workdir / "src"
    for i, zp in enumerate(zip_paths):
        _extract_zip_to_dir(zp, src_dir / f"z{i}")
    _emit_stage(payload, stage="parse_result", status="success", message="解析结果", data={"zip_count": len(zip_paths), "item_count": len(items)})

    out_dir = workdir / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    agg_runninghub_usage: dict[str, Any] = {}
    charged_total_cents = 0
    stopped_for_balance = False
    stop_reason = ""
    stop_from_index = 0
    rh_task_ids: list[str] = []

    user_id = _to_int(payload.get("_user_id"), 0)
    is_admin_user = False
    if user_id > 0:
        with db() as conn:
            row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),)).fetchone()
            is_admin_user = bool(int(row["is_admin"] or 0)) if row else False

    def charge_one(*, idx: int, item: dict[str, Any], output: dict[str, Any]) -> int:
        nonlocal charged_total_cents
        if user_id <= 0 or is_admin_user:
            return 0
        if not _to_bool(output.get("ok"), False):
            return 0
        with db() as conn:
            pricing = _get_pricing_config(conn)
            cost = compute_cost_cents(
                runninghub_usage=output.get("runninghub_usage") if isinstance(output.get("runninghub_usage"), dict) else {},
                rh_coins_per_10rmb=int(pricing.get("rh_coins_per_10rmb") or 2500),
                usd_to_rmb=float(pricing.get("usd_to_rmb") or 7.2),
                gemini_input_tokens=0,
                gemini_output_tokens=0,
                gemini_input_usd_per_1m=float(pricing.get("gemini_input_usd_per_1m") or 4.0),
                gemini_output_usd_per_1m=float(pricing.get("gemini_output_usd_per_1m") or 18.0),
                nano_images=0,
                nano_usd_per_image=float(pricing.get("nano_usd_per_image") or 0.134),
            )
            item_cost_cents = max(_to_int(cost.get("total_cents"), 0), 0)
            if item_cost_cents > 0:
                conn.execute(
                    "UPDATE users SET balance_cents = balance_cents - ?, updated_at = ? WHERE id = ?",
                    (int(item_cost_cents), _now_ts(), int(user_id)),
                )
                charged_total_cents += int(item_cost_cents)
                _insert_ledger(
                    conn,
                    user_id=int(user_id),
                    typ="charge",
                    amount_cents=-int(item_cost_cents),
                    ref_task_id=f"{task_id}_item_{idx}",
                    meta={"task_type": "batch_replace_model", "batch_task_id": task_id, "item_index": idx, "item_id": item.get("id") or f"item_{idx}", "cost": cost},
                )
            return int(item_cost_cents)

    for idx, it in enumerate(items, start=1):
        if user_id > 0 and (not is_admin_user):
            with db() as conn:
                row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (int(user_id),)).fetchone()
                bal = int(row["balance_cents"]) if row else 0
            if bal <= 0:
                stopped_for_balance = True
                stop_reason = f"余额不足（当前 {bal} 分），已中断批量任务"
                stop_from_index = int(idx)
                break

        if not isinstance(it, dict):
            continue
        sub_id = f"{task_id}_item_{idx}"
        video_rel = str(it.get("video") or it.get("video_file") or "").strip()
        image_rel = str(it.get("image") or it.get("image_file") or "").strip()
        if not video_rel or not image_rel:
            results.append({"id": it.get("id") or f"item_{idx}", "ok": False, "error": "缺少 video/image"})
            continue
        video_path = (src_dir / video_rel).resolve()
        image_path = (src_dir / image_rel).resolve()
        if not video_path.exists() or not image_path.exists():
            results.append({"id": it.get("id") or f"item_{idx}", "ok": False, "error": "文件不存在"})
            continue

        params = it.get("params") if isinstance(it.get("params"), dict) else {}
        one_payload: dict[str, Any] = {}
        one_payload.update(defaults)
        one_payload.update(params)
        one_payload["video_local_path"] = str(video_path)
        one_payload["image_local_path"] = str(image_path)

        try:
            _emit_stage(payload, stage="uploading", status="running", message="上传文件中", data={"item_index": idx, "item_id": it.get("id") or f"item_{idx}"})
            _emit_stage(payload, stage="processing", status="running", message="正在执行视频模特替换", data={"item_index": idx, "item_id": it.get("id") or f"item_{idx}"})
            output = _run_replace_model(sub_id, _apply_runtime_defaults("replace_model", one_payload))
            ok = _to_bool(output.get("ok"), False)
            one_rh_id = str(output.get("runninghub_task_id") or "").strip()
            if one_rh_id:
                rh_task_ids.append(one_rh_id)
            moved_path = ""
            out_file = str(output.get("download_path") or "").strip()
            if ok and out_file and Path(out_file).exists():
                safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(it.get("id") or "video")).strip("_") or "video"
                target = out_dir / f"{idx:03d}_{safe_id}.mp4"
                target.write_bytes(Path(out_file).read_bytes())
                moved_path = str(target)
            if isinstance(output.get("runninghub_usage"), dict):
                agg_runninghub_usage = _sum_usage([agg_runninghub_usage, output.get("runninghub_usage")])
            cost_cents = charge_one(idx=idx, item=it, output=output if isinstance(output, dict) else {})
            item_result = {"id": it.get("id") or f"item_{idx}", "ok": ok, "video_path": moved_path, "runninghub_task_id": one_rh_id, "error": "" if ok else str(output.get("message") or output.get("error") or ""), "cost_cents": int(cost_cents)}
            results.append(item_result)
            _emit_batch_item_output_event(payload, item_index=idx, item_id=str(it.get("id") or f"item_{idx}"), result=item_result)
            _emit_stage(
                payload,
                stage="upload_result",
                status="success" if ok else "failed",
                message="上传成功" if ok else "上传失败",
                data={"item_index": idx, "runninghub_task_id": one_rh_id, "error": "" if ok else str(output.get("message") or output.get("error") or "")},
            )
            if user_id > 0 and (not is_admin_user) and ok:
                with db() as conn:
                    row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (int(user_id),)).fetchone()
                    bal2 = int(row["balance_cents"]) if row else 0
                if bal2 <= 0:
                    stopped_for_balance = True
                    stop_reason = f"余额不足（当前 {bal2} 分），已中断批量任务"
                    stop_from_index = int(idx) + 1
                    break
        except Exception as exc:
            item_result = {"id": it.get("id") or f"item_{idx}", "ok": False, "error": str(exc)}
            results.append(item_result)
            _emit_batch_item_output_event(payload, item_index=idx, item_id=str(it.get("id") or f"item_{idx}"), result=item_result)

    if stopped_for_balance:
        start = int(stop_from_index or 0)
        if start <= 0:
            start = len(items) + 1
        for j in range(start, len(items) + 1):
            it2 = items[j - 1] if j - 1 < len(items) else {}
            iid = it2.get("id") if isinstance(it2, dict) else None
            results.append({"id": iid or f"item_{j}", "ok": False, "error": stop_reason, "skipped": True})

    manifest_path = workdir / "results.json"
    manifest_path.write_text(_json_dumps({"items": results}), encoding="utf-8")

    zip_out = workdir / "batch_videos.zip"
    with zipfile.ZipFile(str(zip_out), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(manifest_path), arcname="results.json")
        for p in sorted(out_dir.glob("*.mp4")):
            zf.write(str(p), arcname=f"videos/{p.name}")

    ok_any = any(bool(r.get("ok")) for r in results)
    message = stop_reason or ("批量任务完成" if ok_any else "批量任务失败")
    if (not ok_any) and (not stop_reason):
        for r in results:
            err = str(r.get("error") or "").strip()
            if err:
                message = f"批量任务失败：{err}"
                break
    rh_task_ids = list(dict.fromkeys([x for x in rh_task_ids if str(x).strip()]))
    return {"ok": bool(ok_any), "message": message, "runninghub_task_id": rh_task_ids[0] if rh_task_ids else "", "runninghub_task_ids": rh_task_ids, "runninghub_usage": agg_runninghub_usage, "result_zip": str(zip_out), "download_path": str(zip_out), "items": results, "skip_billing": True if (user_id > 0 and (not is_admin_user)) else False, "billing": {"mode": "per_item", "cost_cents": int(charged_total_cents)}}


def _run_batch_replace_product(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    zip_paths_value = payload.get("zip_paths")
    zip_paths: list[Path] = []
    if isinstance(zip_paths_value, list):
        for z in zip_paths_value:
            t = str(z or "").strip()
            if not t:
                continue
            p = Path(t).resolve()
            if p.exists():
                zip_paths.append(p)
    if not zip_paths:
        raise RuntimeError("缺少 zip_paths")
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not items:
        raise RuntimeError("批量 items 不能为空")
    _emit_stage(payload, stage="parsing", status="running", message="解析文件中")

    workdir = _build_task_workdir(task_id)
    src_dir = workdir / "src"
    for i, zp in enumerate(zip_paths):
        _extract_zip_to_dir(zp, src_dir / f"z{i}")
    _emit_stage(payload, stage="parse_result", status="success", message="解析结果", data={"zip_count": len(zip_paths), "item_count": len(items)})

    out_dir = workdir / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    agg_runninghub_usage: dict[str, Any] = {}
    charged_total_cents = 0
    stopped_for_balance = False
    stop_reason = ""
    stop_from_index = 0
    rh_task_ids: list[str] = []

    user_id = _to_int(payload.get("_user_id"), 0)
    is_admin_user = False
    if user_id > 0:
        with db() as conn:
            row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),)).fetchone()
            is_admin_user = bool(int(row["is_admin"] or 0)) if row else False

    def charge_one(*, idx: int, item: dict[str, Any], output: dict[str, Any]) -> int:
        nonlocal charged_total_cents
        if user_id <= 0 or is_admin_user:
            return 0
        if not _to_bool(output.get("ok"), False):
            return 0
        with db() as conn:
            pricing = _get_pricing_config(conn)
            cost = compute_cost_cents(
                runninghub_usage=output.get("runninghub_usage") if isinstance(output.get("runninghub_usage"), dict) else {},
                rh_coins_per_10rmb=int(pricing.get("rh_coins_per_10rmb") or 2500),
                usd_to_rmb=float(pricing.get("usd_to_rmb") or 7.2),
                gemini_input_tokens=0,
                gemini_output_tokens=0,
                gemini_input_usd_per_1m=float(pricing.get("gemini_input_usd_per_1m") or 4.0),
                gemini_output_usd_per_1m=float(pricing.get("gemini_output_usd_per_1m") or 18.0),
                nano_images=0,
                nano_usd_per_image=float(pricing.get("nano_usd_per_image") or 0.134),
            )
            item_cost_cents = max(_to_int(cost.get("total_cents"), 0), 0)
            if item_cost_cents > 0:
                conn.execute(
                    "UPDATE users SET balance_cents = balance_cents - ?, updated_at = ? WHERE id = ?",
                    (int(item_cost_cents), _now_ts(), int(user_id)),
                )
                charged_total_cents += int(item_cost_cents)
                _insert_ledger(
                    conn,
                    user_id=int(user_id),
                    typ="charge",
                    amount_cents=-int(item_cost_cents),
                    ref_task_id=f"{task_id}_item_{idx}",
                    meta={"task_type": "batch_replace_product", "batch_task_id": task_id, "item_index": idx, "item_id": item.get("id") or f"item_{idx}", "cost": cost},
                )
            return int(item_cost_cents)

    for idx, it in enumerate(items, start=1):
        if user_id > 0 and (not is_admin_user):
            with db() as conn:
                row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (int(user_id),)).fetchone()
                bal = int(row["balance_cents"]) if row else 0
            if bal <= 0:
                stopped_for_balance = True
                stop_reason = f"余额不足（当前 {bal} 分），已中断批量任务"
                stop_from_index = int(idx)
                break

        if not isinstance(it, dict):
            continue
        sub_id = f"{task_id}_item_{idx}"
        video_rel = str(it.get("video") or it.get("video_file") or "").strip()
        image_rel = str(it.get("image") or it.get("image_file") or "").strip()
        if not video_rel or not image_rel:
            results.append({"id": it.get("id") or f"item_{idx}", "ok": False, "error": "缺少 video/image"})
            continue
        video_path = (src_dir / video_rel).resolve()
        image_path = (src_dir / image_rel).resolve()
        if not video_path.exists() or not image_path.exists():
            results.append({"id": it.get("id") or f"item_{idx}", "ok": False, "error": "文件不存在"})
            continue

        params = it.get("params") if isinstance(it.get("params"), dict) else {}
        one_payload: dict[str, Any] = {}
        one_payload.update(defaults)
        one_payload.update(params)
        one_payload["video_local_path"] = str(video_path)
        one_payload["image_local_path"] = str(image_path)

        try:
            _emit_stage(payload, stage="uploading", status="running", message="上传文件中", data={"item_index": idx, "item_id": it.get("id") or f"item_{idx}"})
            _emit_stage(payload, stage="processing", status="running", message="正在执行视频商品替换", data={"item_index": idx, "item_id": it.get("id") or f"item_{idx}"})
            output = _run_replace_product(sub_id, _apply_runtime_defaults("replace_product", one_payload))
            ok = _to_bool(output.get("ok"), False)
            one_rh_id = str(output.get("runninghub_task_id") or "").strip()
            if one_rh_id:
                rh_task_ids.append(one_rh_id)
            moved_path = ""
            out_file = str(output.get("download_path") or "").strip()
            if ok and out_file and Path(out_file).exists():
                safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(it.get("id") or "video")).strip("_") or "video"
                target = out_dir / f"{idx:03d}_{safe_id}.mp4"
                target.write_bytes(Path(out_file).read_bytes())
                moved_path = str(target)
            if isinstance(output.get("runninghub_usage"), dict):
                agg_runninghub_usage = _sum_usage([agg_runninghub_usage, output.get("runninghub_usage")])
            cost_cents = charge_one(idx=idx, item=it, output=output if isinstance(output, dict) else {})
            item_result = {"id": it.get("id") or f"item_{idx}", "ok": ok, "video_path": moved_path, "runninghub_task_id": one_rh_id, "error": "" if ok else str(output.get("message") or output.get("error") or ""), "cost_cents": int(cost_cents)}
            results.append(item_result)
            _emit_batch_item_output_event(payload, item_index=idx, item_id=str(it.get("id") or f"item_{idx}"), result=item_result)
            _emit_stage(
                payload,
                stage="upload_result",
                status="success" if ok else "failed",
                message="上传成功" if ok else "上传失败",
                data={"item_index": idx, "runninghub_task_id": one_rh_id, "error": "" if ok else str(output.get("message") or output.get("error") or "")},
            )
            if user_id > 0 and (not is_admin_user) and ok:
                with db() as conn:
                    row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (int(user_id),)).fetchone()
                    bal2 = int(row["balance_cents"]) if row else 0
                if bal2 <= 0:
                    stopped_for_balance = True
                    stop_reason = f"余额不足（当前 {bal2} 分），已中断批量任务"
                    stop_from_index = int(idx) + 1
                    break
        except Exception as exc:
            item_result = {"id": it.get("id") or f"item_{idx}", "ok": False, "error": str(exc)}
            results.append(item_result)
            _emit_batch_item_output_event(payload, item_index=idx, item_id=str(it.get("id") or f"item_{idx}"), result=item_result)

    if stopped_for_balance:
        start = int(stop_from_index or 0)
        if start <= 0:
            start = len(items) + 1
        for j in range(start, len(items) + 1):
            it2 = items[j - 1] if j - 1 < len(items) else {}
            iid = it2.get("id") if isinstance(it2, dict) else None
            results.append({"id": iid or f"item_{j}", "ok": False, "error": stop_reason, "skipped": True})

    manifest_path = workdir / "results.json"
    manifest_path.write_text(_json_dumps({"items": results}), encoding="utf-8")

    zip_out = workdir / "batch_videos.zip"
    with zipfile.ZipFile(str(zip_out), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(manifest_path), arcname="results.json")
        for p in sorted(out_dir.glob("*.mp4")):
            zf.write(str(p), arcname=f"videos/{p.name}")

    ok_any = any(bool(r.get("ok")) for r in results)
    message = stop_reason or ("批量任务完成" if ok_any else "批量任务失败")
    if (not ok_any) and (not stop_reason):
        for r in results:
            err = str(r.get("error") or "").strip()
            if err:
                message = f"批量任务失败：{err}"
                break
    rh_task_ids = list(dict.fromkeys([x for x in rh_task_ids if str(x).strip()]))
    return {"ok": bool(ok_any), "message": message, "runninghub_task_id": rh_task_ids[0] if rh_task_ids else "", "runninghub_task_ids": rh_task_ids, "runninghub_usage": agg_runninghub_usage, "result_zip": str(zip_out), "download_path": str(zip_out), "items": results, "skip_billing": True if (user_id > 0 and (not is_admin_user)) else False, "billing": {"mode": "per_item", "cost_cents": int(charged_total_cents)}}


def _run_text_to_image_disabled(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _run_remote_comfy_mapped_task(task_id, payload, "text_to_image")


def _collect_batch_usage(output_dir: Any) -> dict[str, Any]:
    return {}


def _enhance_tg_payload_with_llm_prompt(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    enhanced = dict(payload or {})
    if enhanced.get("tg_llm_prompt_enhanced"):
        return enhanced
    if not _to_bool(enhanced.get("tg_use_llm_prompt"), False):
        return enhanced
    if not str(enhanced.get("llm_base_url") or "").strip():
        try:
            with db() as conn:
                runtime = _get_runtime_config(conn)
            for key in (
                "llm_base_url",
                "llm_api_key",
                "llm_api_key_gemini",
                "llm_api_key_gpt",
                "llm_default_model",
                "llm_default_model_gemini",
                "llm_default_model_gpt",
                "llm_model_priority_order",
            ):
                if not str(enhanced.get(key) or "").strip():
                    enhanced[key] = runtime.get(key)
        except Exception:
            pass

    typ = str(task_type or "").strip()
    if typ not in {"text_to_image", "image_generate", "replace_model", "replace_product", "replace_productANDmodel", "video_i2v"}:
        return enhanced

    user_request = str(
        enhanced.get("tg_user_instruction")
        or enhanced.get("message")
        or enhanced.get("prompt_text")
        or enhanced.get("prompt")
        or enhanced.get("product_name")
        or ""
    ).strip()
    user_request = re.sub(r"^\s*用户(?:文生图|图生视频)?需求[:：]\s*", "", user_request).strip()
    if not user_request:
        return enhanced

    task_labels = {
        "text_to_image": "文生图",
        "image_generate": "图像编辑",
        "replace_model": "视频模特替换",
        "replace_product": "视频商品替换",
        "replace_productANDmodel": "视频模特和商品联合替换",
        "video_i2v": "图生视频",
    }
    system_prompt = "\n".join(
        [
            "你是图像和视频生成工作流的提示词工程师。",
            "请根据用户原始中文需求，改写成适合 ComfyUI 或图生视频 API 直接使用的生成提示词。",
            "必须用中文输出 prompt_text，不要输出英文。",
            "如果本次请求附带参考图片，必须先识别图片中的主体、构图、环境、服装、动作和可见物体，再结合用户需求写提示词；不能凭空编造图片中不存在的场景、服装或物体。",
            "必须输出严格 JSON，不要代码块，不要解释。",
            "字段：prompt_text。",
            "prompt_text 只允许普通提示词文本，不要 Markdown、emoji、标题、列表、引用符号或解释语。",
            "prompt_text 必须完整保留用户的具体主体、物品、动作、场景、材质、颜色和限制；不能把具体要求改成泛泛的质量词。",
            "如果用户要求苹果，prompt_text 必须明确包含苹果；如果用户要求某个服装、颜色、环境或动作，也必须保留。",
            "不要擅自替换用户指定的场景、材质或道具，例如木桌不能改成厨房台面，海边不能改成室内。",
            "在保留原始要求的基础上，补充画面主体、场景、镜头、构图、光线、质感、风格和细节。",
            "不要添加用户没有要求的敏感、违法、侵权或身份信息。",
            f"当前任务类型：{task_labels.get(typ, typ)}。",
        ]
    )
    image_hint_paths: list[str] = []
    for image_key in (
        "image_local_path",
        "input_image_local_path",
        "product_image_local_path",
        "model_image_local_path",
        "generated_scene_image_local_path",
    ):
        image_path = str(enhanced.get(image_key) or "").strip()
        if image_path and Path(image_path).exists() and image_path not in image_hint_paths:
            image_hint_paths.append(image_path)
    llm_result, selected, attempts = _request_llm_json_with_fallback(
        source=enhanced,
        user_input=user_request,
        system_prompt=system_prompt,
        parameters="",
        image_paths=image_hint_paths or None,
        allow_builtin=False,
        request_label="Telegram Grok 提示词改写",
    )
    parsed = llm_result.get("parsed") if isinstance(llm_result, dict) else None
    rewritten = str((parsed or {}).get("prompt_text") or "").strip() if isinstance(parsed, dict) else ""
    if not rewritten:
        raise RuntimeError("Grok 提示词改写未返回 prompt_text")
    rewritten_lines = []
    for line in rewritten.splitlines():
        cleaned_line = re.sub(r"^[>\\-•\\s]+", "", str(line or "").strip())
        cleaned_line = cleaned_line.replace("**", "").replace("__", "").strip()
        if not cleaned_line:
            continue
        if re.search(r"refining the prompt|prompt for comfyui|优化提示词|提示词改写", cleaned_line, re.IGNORECASE):
            continue
        rewritten_lines.append(cleaned_line)
    if rewritten_lines:
        rewritten = "，".join(rewritten_lines)
    final_prompt = rewritten
    if user_request not in rewritten:
        final_prompt = f"{user_request}。{rewritten}"

    enhanced["tg_original_prompt"] = user_request
    enhanced["tg_llm_prompt_enhanced"] = True
    enhanced["tg_llm_selected_model"] = str(selected.get("model") or "").strip() if isinstance(selected, dict) else ""
    enhanced["tg_llm_attempts"] = attempts
    enhanced["tg_llm_rewritten_prompt"] = rewritten
    enhanced["prompt_text"] = final_prompt
    enhanced["prompt"] = final_prompt
    if typ in {"replace_model", "replace_product", "replace_productANDmodel"}:
        enhanced["style_hint"] = final_prompt
    return enhanced


def _build_agent_task_payload(
    *,
    message: str,
    file_infos: list[dict[str, Any]],
    use_ai_copy: bool = True,
    default_duration: int = 15,
    production_only: bool = False,
) -> tuple[str, dict[str, Any], str]:
    return (
        "chat",
        {"reply": "远程 ComfyUI 工作流尚未接入，请先在后台完成工作流映射后再创建生产任务。"},
        "远程 ComfyUI 工作流尚未接入",
    )


def _run_replace_model(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _run_remote_comfy_mapped_task(task_id, payload, "replace_model")


def _run_replace_product(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _run_remote_comfy_mapped_task(task_id, payload, "replace_product")


def _run_create_audio(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _run_remote_comfy_mapped_task(task_id, payload, "create_audio")


def _run_get_nano_banana(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _run_remote_comfy_mapped_task(task_id, payload, "get_nano_banana")


TASK_RUNNERS = {
    "text_to_image": _run_text_to_image_disabled,
    "replace_model": _run_replace_model,
    "replace_product": _run_replace_product,
    "create_audio": _run_create_audio,
    "get_nano_banana": _run_get_nano_banana,
    "video_i2v": _run_mulerouter_wan_i2v,
    "image_generate": _run_image_generate,
    "get_gemini": _run_get_gemini,
    "replace_productANDmodel": _run_replace_product_and_model,
    "create_video": _run_create_video_with_doubao,
    "commerce_video": _run_create_video_with_doubao,
    "batch_create_video": _run_batch_create_video,
    "batch_replace_model": _run_batch_replace_model,
    "batch_replace_product": _run_batch_replace_product,
}
TG_AGENT_PRODUCTION_TASK_TYPES = set(TASK_RUNNERS.keys())


def _agent_chat_payload(*, reply: str, summary: str = "") -> tuple[str, dict[str, Any], str]:
    reply_text = str(reply or "").strip()
    summary_text = str(summary or reply_text or "未创建生产任务").strip()
    return "chat", {"reply": reply_text}, summary_text


def _task_worker(task_id: str, user_id: int, task_type: str, payload: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", ("running", _now_ts(), task_id))
        _insert_task_event(
            conn,
            task_id=task_id,
            user_id=int(user_id),
            kind="running",
            message="任务开始执行",
            data={"task_type": task_type, "stage": "start", "status": "running", "source": "webapp"},
        )

    task_output: dict[str, Any] = {}
    task_error = ""
    status = "failed"
    runninghub_task_id = ""
    usage_json: dict[str, Any] = {}
    cost_cents = 0
    effective_payload = _apply_runtime_defaults(task_type, payload)
    effective_payload["_task_id"] = str(task_id)
    effective_payload["_task_type"] = str(task_type)
    effective_payload["_user_id"] = int(user_id)
    username = ""
    if int(user_id) > 0:
        with db() as conn:
            row = conn.execute("SELECT username FROM users WHERE id = ?", (int(user_id),)).fetchone()
            username = str(row["username"] or "").strip() if row is not None else ""
    if username:
        effective_payload["_username"] = username
    effective_payload["_event_logger"] = lambda msg: _emit_task_event(
        task_id=task_id,
        user_id=int(user_id),
        kind="log",
        message=str(msg),
        data={"stage": "log", "status": "info", "source": "webapp"},
    )
    effective_payload["_event_progress"] = (
        lambda p: _emit_task_event(
            task_id=task_id,
            user_id=int(user_id),
            kind="progress",
            message=str((p or {}).get("status") or ""),
            data=p or {},
        )
    )
    _emit_stage(effective_payload, stage="start", status="running", message="任务开始")
    _emit_stage(effective_payload, stage="running", status="running", message="任务进行中")

    try:
        runner = TASK_RUNNERS.get(task_type)
        if runner is None:
            raise RuntimeError(f"未知任务类型: {task_type}")
        task_output = runner(task_id, effective_payload)
        if not isinstance(task_output, dict):
            task_output = {"raw_result": task_output}
        status = "success" if _to_bool(task_output.get("ok"), False) else "failed"
        if status == "failed" and not task_error:
            task_error = str(task_output.get("message") or task_output.get("error") or "").strip()
        runninghub_task_id = str(task_output.get("runninghub_task_id") or "").strip()

        usage_json = {
            "runninghub": task_output.get("runninghub_usage") if isinstance(task_output.get("runninghub_usage"), dict) else {},
            "gemini_input_tokens": max(_to_int(task_output.get("gemini_input_tokens"), _to_int(effective_payload.get("gemini_input_tokens"), 0)), 0),
            "gemini_output_tokens": max(_to_int(task_output.get("gemini_output_tokens"), _to_int(effective_payload.get("gemini_output_tokens"), 0)), 0),
            "nano_images": max(_to_int(task_output.get("nano_images"), _to_int(effective_payload.get("nano_images"), 0)), 0),
        }
        _emit_stage(
            effective_payload,
            stage="finished",
            status="success" if status == "success" else "failed",
            message="生成成功" if status == "success" else "生成失败",
            data={"error": str(task_error or "")},
        )
    except Exception as exc:
        task_error = str(exc)
        status = "failed"
        usage_json = {
            "runninghub": {},
            "gemini_input_tokens": max(_to_int(effective_payload.get("gemini_input_tokens"), 0), 0),
            "gemini_output_tokens": max(_to_int(effective_payload.get("gemini_output_tokens"), 0), 0),
            "nano_images": max(_to_int(effective_payload.get("nano_images"), 0), 0),
        }
        _emit_stage(effective_payload, stage="finished", status="failed", message="生成失败", data={"error": str(task_error)})

    with db() as conn:
        pricing = _get_pricing_config(conn)
        charge_info: dict[str, Any] = {}
        skip_billing = bool(task_output.get("skip_billing"))
        if skip_billing:
            billing = task_output.get("billing") if isinstance(task_output.get("billing"), dict) else {}
            cost_cents = max(_to_int(billing.get("cost_cents"), 0), 0)
            cost = billing.get("cost") if isinstance(billing.get("cost"), dict) else {"total_cents": cost_cents}
        else:
            cost = compute_cost_cents(
                runninghub_usage=usage_json.get("runninghub") if isinstance(usage_json.get("runninghub"), dict) else {},
                rh_coins_per_10rmb=int(pricing.get("rh_coins_per_10rmb") or 2500),
                usd_to_rmb=float(pricing.get("usd_to_rmb") or 7.2),
                gemini_input_tokens=int(usage_json.get("gemini_input_tokens") or 0),
                gemini_output_tokens=int(usage_json.get("gemini_output_tokens") or 0),
                gemini_input_usd_per_1m=float(pricing.get("gemini_input_usd_per_1m") or 4.0),
                gemini_output_usd_per_1m=float(pricing.get("gemini_output_usd_per_1m") or 18.0),
                nano_images=int(usage_json.get("nano_images") or 0),
                nano_usd_per_image=float(pricing.get("nano_usd_per_image") or 0.134),
            )
            cost_cents = max(_to_int(cost.get("total_cents"), 0), 0)

        if status != "success":
            cost_cents = 0
        elif (not skip_billing) and cost_cents > 0:
            row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (int(user_id),)).fetchone()
            balance = int(row["balance_cents"]) if row else 0
            allow_negative = bool(pricing.get("allow_negative_balance"))
            if (not allow_negative) and (balance < cost_cents):
                status = "failed"
                extra = f"余额不足（当前 {balance} 分，所需 {cost_cents} 分）"
                task_error = f"{task_error}; {extra}" if task_error else extra
                cost_cents = 0
            else:
                conn.execute(
                    "UPDATE users SET balance_cents = balance_cents - ?, updated_at = ? WHERE id = ?",
                    (int(cost_cents), _now_ts(), int(user_id)),
                )
                charge_info = {
                    "cost": cost,
                    "task_type": task_type,
                }
                _insert_ledger(
                    conn,
                    user_id=int(user_id),
                    typ="charge",
                    amount_cents=-int(cost_cents),
                    ref_task_id=task_id,
                    meta=charge_info,
                )

        output_to_store = dict(task_output)
        if cost_cents:
            existing = output_to_store.get("billing") if isinstance(output_to_store.get("billing"), dict) else {}
            merged = dict(existing)
            merged["cost_cents"] = cost_cents
            merged["pricing"] = pricing
            output_to_store["billing"] = merged
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, output_json = ?, error = ?, runninghub_task_id = ?, usage_json = ?, cost_cents = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                _json_dumps(_sanitize_payload(output_to_store)),
                str(task_error or ""),
                runninghub_task_id,
                _json_dumps(usage_json),
                int(cost_cents),
                _now_ts(),
                task_id,
            ),
        )
        _insert_task_event(
            conn,
            task_id=task_id,
            user_id=int(user_id),
            kind="done",
            message="任务完成" if status == "success" else "任务失败",
            data={
                "status": status,
                "stage": "finish",
                "error": str(task_error or ""),
                "cost_cents": int(cost_cents),
                "source": "webapp",
                "usage": usage_json,
                "has_download": bool(_task_has_download_file(output_to_store if isinstance(output_to_store, dict) else {})),
                "batch_summary": _extract_batch_summary(output_to_store if isinstance(output_to_store, dict) else {}),
            },
        )
        _insert_task_event(
            conn,
            task_id=task_id,
            user_id=int(user_id),
            kind="log",
            message="最终输出快照",
            data={
                "stage": "final_output",
                "status": status,
                "source": "webapp",
                "user_visible": True,
                "output_snapshot": _build_final_output_snapshot(output_to_store if isinstance(output_to_store, dict) else {}),
            },
        )
    _notify_tg_task_finished(
        task_id=task_id,
        task_type=task_type,
        payload=effective_payload,
        status=status,
        error=task_error,
        output_data=output_to_store if isinstance(output_to_store, dict) else {},
    )


async def _save_upload_file(username: str, task_id: str, field_name: str, upload: UploadFile | None) -> str:
    if upload is None:
        return ""
    filename = str(upload.filename or "")
    suffix = Path(filename).suffix or ".bin"
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(username or "")).strip("._-") or "user"
    upload_dir = UPLOAD_ROOT / safe / task_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{field_name}{suffix}"
    written = 0
    try:
        with target.open("wb") as f:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="上传文件过大")
                f.write(chunk)
    except Exception:
        try:
            if target.exists():
                target.unlink()
        except Exception:
            pass
        raise
    finally:
        try:
            await upload.close()
        except Exception:
            pass
    return str(target)


def _create_task_record(task_id: str, user_id: int, task_type: str, input_payload: dict[str, Any]) -> None:
    now = _now_ts()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO tasks(id, user_id, type, status, input_json, output_json, error, runninghub_task_id, usage_json, cost_cents, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                int(user_id),
                str(task_type),
                "queued",
                _json_dumps(input_payload),
                _json_dumps({}),
                "",
                "",
                _json_dumps({}),
                0,
                now,
                now,
            ),
        )
        _insert_task_event(conn, task_id=task_id, user_id=int(user_id), kind="queued", message="任务已进入队列", data={})


def _insert_task_event(conn, *, task_id: str, user_id: int, kind: str, message: str, data: Any) -> None:
    normalized = _normalize_task_event_data(str(kind), str(message), data)
    merged_data = _merge_task_log_meta(normalized, _get_task_log_context(conn, task_id))
    conn.execute(
        """
        INSERT INTO task_events(task_id, user_id, kind, message, data_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (str(task_id), int(user_id), str(kind), str(message), _json_dumps(merged_data), _now_ts()),
    )


def _emit_task_event(*, task_id: str, user_id: int, kind: str, message: str, data: Any) -> None:
    with db() as conn:
        _insert_task_event(conn, task_id=str(task_id), user_id=int(user_id), kind=str(kind), message=str(message), data=data)


def _emit_stage(
    payload: dict[str, Any],
    *,
    stage: str,
    status: str,
    message: str,
    data: dict[str, Any] | None = None,
    progress: float | int | None = None,
) -> None:
    cb = payload.get("_event_progress")
    if cb is None:
        return
    body = {"stage": str(stage), "status": str(status)}
    if isinstance(data, dict):
        body.update(data)
    progress_value = progress
    if progress_value is None and isinstance(body.get("progress"), (int, float)):
        progress_value = float(body.get("progress"))
    body.setdefault("source", "webapp")
    body.setdefault("user_visible", True)
    body.setdefault("level", "error" if str(status) == "failed" else "info")
    try:
        cb({"status": str(message), "progress": progress_value, "stage": str(stage), "state": str(status), "data": body})
    except Exception:
        pass


def _enqueue_task(task_id: str, user_id: int, task_type: str, payload: dict[str, Any]) -> None:
    effective_payload = _apply_runtime_defaults(task_type, payload)
    _create_task_record(task_id, user_id, task_type, effective_payload)
    try:
        _TASK_QUEUE.put((str(task_id), int(user_id), str(task_type), effective_payload), block=False)
    except Exception:
        with db() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                ("failed", "任务队列已满，无法入队", _now_ts(), str(task_id)),
            )
            _insert_task_event(
                conn,
                task_id=str(task_id),
                user_id=int(user_id),
                kind="done",
                message="任务失败",
                data={"status": "failed", "error": "任务队列已满，无法入队", "cost_cents": 0},
            )


def _internal_tg_submit_user_id() -> int:
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE is_admin = 1 AND is_disabled = 0 ORDER BY id ASC LIMIT 1").fetchone()
        if row is None:
            row = conn.execute("SELECT id FROM users WHERE is_disabled = 0 ORDER BY id ASC LIMIT 1").fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="没有可用于 TG 内部提交的后台账号")
    return int(row["id"])


def _require_internal_tg_request(request: Request) -> None:
    expected_token = str(os.getenv("TG_INTERNAL_API_TOKEN") or "").strip()
    provided_token = str(request.headers.get("x-tg-internal-token") or "").strip()
    if expected_token:
        if provided_token != expected_token:
            raise HTTPException(status_code=403, detail="TG 内部提交 token 不正确")
        return
    client_host = ""
    try:
        client_host = str(request.client.host if request.client else "")
    except Exception:
        client_host = ""
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=403, detail="TG 内部提交接口仅允许本机调用")


def _validated_local_file(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"{label} 不能为空")
    path = Path(text).expanduser()
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"{label} 文件不存在: {resolved}")
    return str(resolved)


def _build_internal_tg_task_payload(task_id: str, task_type: str, params: dict[str, Any]) -> dict[str, Any]:
    typ = str(task_type or "").strip()
    payload = dict(params or {})
    payload["source"] = "telegram"

    if typ == "image_generate":
        product_image = _validated_local_file(payload.get("product_image_local_path") or payload.get("image_local_path"), label="商品图")
        model_image = str(payload.get("model_image_local_path") or "").strip()
        if model_image:
            payload["mode"] = "model_product"
            payload["model_image_local_path"] = _validated_local_file(model_image, label="模特图")
        else:
            payload["mode"] = "product_only"
        payload["product_image_local_path"] = product_image
        payload = _enhance_tg_payload_with_llm_prompt(typ, payload)
        return payload

    if typ == "text_to_image":
        payload["prompt"] = str(payload.get("prompt") or payload.get("message") or payload.get("tg_user_instruction") or "").strip()
        if not payload["prompt"]:
            raise HTTPException(status_code=400, detail="text_to_image 需要 prompt")
        payload = _enhance_tg_payload_with_llm_prompt(typ, payload)
        return payload

    if typ == "video_i2v":
        image_path = payload.get("image_local_path") or payload.get("input_image_local_path")
        payload["image_local_path"] = _validated_local_file(image_path, label="图生视频参考图")
        payload["prompt"] = str(payload.get("prompt") or payload.get("prompt_text") or payload.get("message") or payload.get("tg_user_instruction") or "").strip()
        if not payload["prompt"]:
            raise HTTPException(status_code=400, detail="video_i2v 需要 prompt")
        payload["duration_seconds"] = min(max(_to_int(payload.get("duration_seconds") or payload.get("mulerouter_wan_i2v_duration"), 2), 2), 15)
        payload["mulerouter_wan_i2v_duration"] = payload["duration_seconds"]
        resolution = str(payload.get("resolution") or payload.get("mulerouter_wan_i2v_resolution") or "720p").strip()
        payload["mulerouter_wan_i2v_resolution"] = resolution if resolution in {"720p", "1080p"} else "720p"
        payload["mulerouter_wan_i2v_prompt_extend"] = _to_bool(payload.get("mulerouter_wan_i2v_prompt_extend", payload.get("prompt_extend")), False)
        payload = _enhance_tg_payload_with_llm_prompt(typ, payload)
        return payload

    if typ == "replace_model":
        payload = _normalize_replace_model_payload(payload)
        payload["video_local_path"] = _validated_local_file(payload.get("video_local_path"), label="原视频")
        payload["image_local_path"] = _validated_local_file(payload.get("image_local_path"), label="模特图")
        payload = _enhance_tg_payload_with_llm_prompt(typ, payload)
        return payload

    if typ == "replace_product":
        payload["video_local_path"] = _validated_local_file(payload.get("video_local_path"), label="原视频")
        payload["image_local_path"] = _validated_local_file(payload.get("image_local_path"), label="商品图")
        payload = _enhance_tg_payload_with_llm_prompt(typ, payload)
        return payload

    if typ == "replace_productANDmodel":
        model_zip = str(payload.get("model_zip_path") or "").strip()
        product_zip = str(payload.get("product_zip_path") or "").strip()
        video_zip = str(payload.get("video_zip_path") or "").strip()
        if model_zip or product_zip or video_zip:
            payload["model_zip_path"] = _validated_local_file(model_zip, label="模特 zip")
            payload["product_zip_path"] = _validated_local_file(product_zip, label="商品 zip")
            payload["video_zip_path"] = _validated_local_file(video_zip, label="原视频 zip")
            payload.setdefault("match_mode", "cycle")
            payload.setdefault("fixed_index", 1)
            payload.setdefault("auto_rename", True)
            payload = _enhance_tg_payload_with_llm_prompt(typ, payload)
            return payload

        mixed_image_paths = payload.get("mixed_image_paths") if isinstance(payload.get("mixed_image_paths"), list) else []
        video_paths = payload.get("video_paths") if isinstance(payload.get("video_paths"), list) else []
        if mixed_image_paths or video_paths:
            payload["mixed_image_paths"] = [_validated_local_file(item, label="模特/商品图") for item in mixed_image_paths]
            payload["video_paths"] = [_validated_local_file(item, label="原视频") for item in video_paths]
            payload.setdefault("match_mode", "cycle")
            payload.setdefault("fixed_index", 1)
            payload.setdefault("auto_rename", True)
            payload = _enhance_tg_payload_with_llm_prompt(typ, payload)
            return payload

        video_path = _validated_local_file(payload.get("video_local_path"), label="原视频")
        model_image = _validated_local_file(payload.get("model_image_local_path"), label="模特图")
        product_image = _validated_local_file(payload.get("product_image_local_path"), label="商品图")
        workdir = _build_task_workdir(task_id, fallback_username="telegram")
        model_dir = workdir / "tg_input" / "model"
        product_dir = workdir / "tg_input" / "product"
        video_dir = workdir / "tg_input" / "video"
        _copy_inputs_to_dir([model_image], model_dir)
        _copy_inputs_to_dir([product_image], product_dir)
        _copy_inputs_to_dir([video_path], video_dir)
        payload["model_dir_path"] = str(model_dir)
        payload["product_dir_path"] = str(product_dir)
        payload["video_dir_path"] = str(video_dir)
        payload.pop("model_image_local_path", None)
        payload.pop("product_image_local_path", None)
        payload.pop("video_local_path", None)
        payload.setdefault("match_mode", "cycle")
        payload.setdefault("fixed_index", 1)
        payload.setdefault("auto_rename", True)
        payload = _enhance_tg_payload_with_llm_prompt(typ, payload)
        return payload

    if typ in {"create_video", "commerce_video"}:
        model_image = payload.get("model_image_local_path") or payload.get("image_local_path")
        product_image = payload.get("product_image_local_path") or payload.get("scene_image_local_path") or model_image
        payload["model_image_local_path"] = _validated_local_file(model_image, label="模特图")
        payload["product_image_local_path"] = _validated_local_file(product_image, label="商品图")

        camera_video = str(payload.get("camera_video_local_path") or "").strip()
        if camera_video:
            payload["camera_video_local_path"] = _validated_local_file(camera_video, label="运镜视频")

        audio_local = str(payload.get("audio_local_path") or "").strip()
        if audio_local:
            payload["audio_local_path"] = _validated_local_file(audio_local, label="音频")

        scene_image = str(payload.get("generated_scene_image_local_path") or "").strip()
        if scene_image:
            payload["generated_scene_image_local_path"] = _validated_local_file(scene_image, label="场景图")

        payload["duration_seconds"] = max(_to_int(payload.get("duration_seconds"), 15), 1)
        payload = _enhance_tg_payload_with_llm_prompt(typ, payload)
        return payload

    if typ == "create_audio":
        speech_text = str(payload.get("speech_text") or payload.get("word") or "").strip()
        if not speech_text:
            raise HTTPException(status_code=400, detail="create_audio 需要 speech_text")
        payload["speech_text"] = speech_text
        return payload

    if typ == "get_nano_banana":
        input_image = payload.get("input_image_local_path") or payload.get("image_local_path")
        payload["input_image_local_path"] = _validated_local_file(input_image, label="参考图")
        payload = _enhance_tg_payload_with_llm_prompt(typ, payload)
        return payload

    if typ == "get_gemini":
        image_paths: list[str] = []
        for item in payload.get("image_paths") if isinstance(payload.get("image_paths"), list) else []:
            image_paths.append(_validated_local_file(item, label="图片"))
        video_paths: list[str] = []
        for item in payload.get("video_paths") if isinstance(payload.get("video_paths"), list) else []:
            video_paths.append(_validated_local_file(item, label="视频"))
        payload["image_paths"] = image_paths
        payload["video_paths"] = video_paths
        payload["user_input"] = str(payload.get("user_input") or payload.get("message") or "").strip()
        if not payload["user_input"]:
            raise HTTPException(status_code=400, detail="get_gemini 需要 user_input")
        return payload

    raise HTTPException(status_code=400, detail=f"TG 暂不支持的任务类型: {typ}")


def _tg_prompt_preview(payload: dict[str, Any]) -> str:
    source = payload if isinstance(payload, dict) else {}
    candidates: list[str] = []
    for key in ("prompt_text", "prompt", "style_hint", "speech_text"):
        value = str(source.get(key) or "").strip()
        if value:
            candidates.append(value)
    for nested_key in ("model_params", "product_params"):
        nested = source.get(nested_key) if isinstance(source.get(nested_key), dict) else {}
        for key in ("prompt", "prompt_text"):
            value = str(nested.get(key) or "").strip()
            if value:
                candidates.append(value)
    text = " / ".join(dict.fromkeys(candidates))
    return text[:500]

def _delete_task_artifacts(task_id: str) -> None:
    tid = str(task_id or "").strip()
    if not tid:
        return
    candidates: list[Path] = [UPLOAD_ROOT / tid, OUTPUT_ROOT / tid]
    try:
        candidates.extend(list(UPLOAD_ROOT.glob(f"*/{tid}")))
    except Exception:
        pass
    try:
        candidates.extend(list(OUTPUT_ROOT.glob(f"*/{tid}")))
    except Exception:
        pass
    for p in candidates:
        try:
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass


class RegisterPayload(BaseModel):
    username: str
    password: str


class LoginPayload(BaseModel):
    username: str
    password: str


class ChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str


class ChangeUsernamePayload(BaseModel):
    password: str
    new_username: str


class PricingPayload(BaseModel):
    rh_coins_per_10rmb: int = 2500
    usd_to_rmb: float = 7.2
    gemini_input_usd_per_1m: float = 4.0
    gemini_output_usd_per_1m: float = 18.0
    nano_usd_per_image: float = 0.134


class RuntimeConfigPayload(BaseModel):
    remote_comfy_gateway_url: str = ""
    remote_comfy_gateway_token: str = ""
    remote_comfy_workflow_mappings: dict[str, Any] = Field(default_factory=dict)
    upload_server_ip: str = ""
    upload_file_api_key: str = ""
    image_generate_mode_default: str = "closed_model_api"
    image_model_provider_base_url: str = "http://202.90.21.53:3008"
    image_model_provider_api_key_gemini: str = ""
    image_model_provider_api_key_gpt: str = ""
    image_model_default_model: str = "gemini-3-pro-image-preview"
    image_model_default_model_gemini: str = "gemini-3-pro-image-preview"
    image_model_default_model_gpt: str = "gpt-image-1"
    image_model_priority_order: str = "gemini-3-pro-image-preview, gpt-image-1"
    llm_base_url: str = "http://202.90.21.53:3008"
    llm_api_key: str = ""
    llm_api_key_gemini: str = ""
    llm_api_key_gpt: str = ""
    llm_default_model: str = "gemini-3.1-pro-preview"
    llm_default_model_gemini: str = "gemini-3.1-pro-preview"
    llm_default_model_gpt: str = "gpt-4.1"
    llm_model_priority_order: str = "gemini-3.1-pro-preview, gpt-4.1"
    mulerouter_api_name: str = ""
    mulerouter_api_key: str = ""
    mulerouter_base_url: str = "https://api.mulerouter.ai"
    mulerouter_wan_i2v_endpoint: str = "/vendors/carrothub/v1/wan2.7-i2v-spicy/generation"
    mulerouter_wan_i2v_resolution: str = "720p"
    mulerouter_wan_i2v_duration: int = 2
    mulerouter_wan_i2v_prompt_extend: bool = False
    mulerouter_wan_i2v_negative_prompt: str = "low quality, blurry, distorted, watermark, text, logo"
    oral_digital_human_workflow_ids: list[Any] = Field(default_factory=list)
    digital_human_workflow_ids: list[Any] = Field(default_factory=list)
    image_generate_workflow_ids: list[Any] = Field(default_factory=list)
    replace_model_original_workflow_ids: list[Any] = Field(default_factory=list)
    replace_product_workflow_ids: list[Any] = Field(default_factory=list)
    replace_union_model_workflow_ids: list[Any] = Field(default_factory=list)
    replace_union_product_workflow_ids: list[Any] = Field(default_factory=list)
    create_video_app_id: str = ""
    create_audio_app_id: str = ""
    video_app_id: str = ""
    replace_model_app_id: str = ""
    replace_model_original_app_id: str = ""
    replace_product_app_id: str = ""
    cleanup_enabled: bool = True
    cleanup_time: str = "03:30"
    cleanup_retention_days: int = 7


class LlmModelsPayload(BaseModel):
    llm_base_url: str = ""
    llm_api_key: str = ""


class RemoteComfyGatewayPayload(BaseModel):
    remote_comfy_gateway_url: str = ""
    remote_comfy_gateway_token: str = ""


class RemoteComfyWorkflowTestPayload(RemoteComfyGatewayPayload):
    workflow_path: str = ""
    prompt_text: str = "a simple red apple on a wooden table, studio lighting, high quality"
    negative_prompt: str = "low quality, blurry, distorted"
    width: int | None = 512
    height: int | None = 512
    steps: int | None = 6
    batch_size: int | None = 1
    timeout_seconds: int = 900


class RemoteComfyConvertPayload(RemoteComfyGatewayPayload):
    paths: list[str] = Field(default_factory=list)
    overwrite: bool = True
    force: bool = False


class RechargePayload(BaseModel):
    amount_cents: int
    note: str = ""


class UserTogglePayload(BaseModel):
    is_disabled: bool


class AdminCreateUserPayload(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    balance_cents: int = 0


class TgTrustedUserPayload(BaseModel):
    chat_id: int
    label: str = ""
    enabled: bool = True
    notify_busy: bool = True
    notify_available: bool = True


class TgTrustedUserTogglePayload(BaseModel):
    enabled: bool


class InternalTgSubmitPayload(BaseModel):
    task_type: str
    tg_chat_id: int
    params: dict[str, Any] = Field(default_factory=dict)


class InternalTgAgentFilePayload(BaseModel):
    name: str = ""
    path: str
    kind: str = ""


class InternalTgAgentSubmitPayload(BaseModel):
    message: str
    tg_chat_id: int
    files: list[InternalTgAgentFilePayload] = Field(default_factory=list)
    use_ai_copy: bool = True
    duration_seconds: int = 15


def create_app() -> FastAPI:
    _ensure_dirs()
    init_db()
    _ensure_default_pricing()
    _ensure_default_runtime_config()
    _ensure_admin_seed()
    _resume_pending_tasks()
    _start_task_workers()
    _start_cleanup_worker()

    app = FastAPI(title="Workflow WebApp", version="1.0.0")
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    def root(request: Request) -> RedirectResponse:
        token = str(request.cookies.get(SESSION_COOKIE) or "").strip()
        if token:
            try:
                user = get_current_user(session_token=token)
                if bool(int(user.get("is_admin") or 0)):
                    return RedirectResponse(url="/admin.html#admin-overview", status_code=302)
                return RedirectResponse(url="/index.html#app-generate", status_code=302)
            except HTTPException:
                pass
            except Exception:
                pass
        return RedirectResponse(url="/login.html", status_code=302)

    @app.get("/login.html", include_in_schema=False)
    def page_login() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "login.html"))

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            '<rect width="32" height="32" rx="6" fill="#0f8a5f"/>'
            '<path d="M9 10h14v3H9zM9 15h14v3H9zM9 20h9v3H9z" fill="#fff"/>'
            "</svg>"
        )
        return Response(content=svg, media_type="image/svg+xml")

    @app.get("/register.html", include_in_schema=False)
    def page_register() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "register.html"))

    @app.get("/index.html", include_in_schema=False)
    def page_index() -> HTMLResponse:
        return _html_response_with_versions(
            "index.html",
            replacements={
                "__STYLE_VERSION__": _asset_version("assets", "style.css"),
                "__APP_JS_VERSION__": _asset_version("assets", "app.js"),
            },
        )

    @app.get("/admin.html", include_in_schema=False)
    def page_admin() -> HTMLResponse:
        return _html_response_with_versions(
            "admin.html",
            replacements={
                "__STYLE_VERSION__": _asset_version("assets", "style.css"),
                "__ADMIN_JS_VERSION__": _asset_version("assets", "admin.js"),
            },
        )

    @app.get("/batch.html", include_in_schema=False)
    def page_batch() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "batch.html"))

    @app.post("/api/auth/register")
    def api_register(payload: RegisterPayload):
        if not _public_register_enabled():
            raise HTTPException(status_code=403, detail="账号由管理员开通，请联系运营管理员")
        username = str(payload.username or "").strip()
        password = str(payload.password or "")
        if not username:
            raise HTTPException(status_code=400, detail="用户名不能为空")
        now = _now_ts()
        with db() as conn:
            count_row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
            is_admin = 1 if count_row and int(count_row["c"] or 0) == 0 else 0
            try:
                conn.execute(
                    """
                    INSERT INTO users(username, password_hash, is_admin, is_disabled, balance_cents, created_at, updated_at)
                    VALUES (?, ?, ?, 0, 0, ?, ?)
                    """,
                    (username, hash_password(password), int(is_admin), now, now),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    raise HTTPException(status_code=409, detail="客户账号已存在") from exc
                raise
            user_row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user_row is None:
                raise HTTPException(status_code=500, detail="注册失败")
            token = create_session(conn, int(user_row["id"]))

        resp = {
            "id": int(user_row["id"]),
            "username": str(user_row["username"]),
            "is_admin": bool(int(user_row["is_admin"] or 0)),
            "balance_cents": int(user_row["balance_cents"] or 0),
        }
        response = JSONResponse(content=resp)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            max_age=14 * 24 * 3600,
            samesite="lax",
        )
        return response

    @app.post("/api/auth/login")
    def api_login(payload: LoginPayload):
        username = str(payload.username or "").strip()
        password = str(payload.password or "")
        with db() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row is None:
                raise HTTPException(status_code=401, detail="用户名或密码错误")
            user = dict(row)
            if int(user.get("is_disabled") or 0) == 1:
                raise HTTPException(status_code=403, detail="账号已禁用")
            if not verify_password(password, str(user.get("password_hash") or "")):
                raise HTTPException(status_code=401, detail="用户名或密码错误")
            token = create_session(conn, int(user["id"]))

        resp = {
            "id": int(user["id"]),
            "username": str(user["username"]),
            "is_admin": bool(int(user.get("is_admin") or 0)),
            "balance_cents": int(user.get("balance_cents") or 0),
        }
        response = JSONResponse(content=resp)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            max_age=14 * 24 * 3600,
            samesite="lax",
        )
        return response

    @app.post("/api/auth/logout")
    def api_logout(request: Request):
        token = str(request.cookies.get(SESSION_COOKIE) or "").strip()
        if token:
            with db() as conn:
                delete_session(conn, token)
        response = JSONResponse(content={"ok": True})
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.post("/api/auth/change_password")
    def api_change_password(payload: ChangePasswordPayload, user: dict[str, Any] = Depends(get_current_user)):
        old_pwd = str(payload.old_password or "")
        new_pwd = str(payload.new_password or "")
        if not verify_password(old_pwd, str(user.get("password_hash") or "")):
            raise HTTPException(status_code=400, detail="原密码错误")
        if not new_pwd or len(new_pwd) < 6:
            raise HTTPException(status_code=400, detail="新密码至少 6 位")
        new_hash = hash_password(new_pwd)
        with db() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (new_hash, _now_ts(), int(user["id"])),
            )
        return {"ok": True}

    @app.post("/api/auth/change_username")
    def api_change_username(payload: ChangeUsernamePayload, user: dict[str, Any] = Depends(get_current_user)):
        pwd = str(payload.password or "")
        new_username = str(payload.new_username or "").strip()
        current_username = str(user.get("username") or "").strip()
        if not verify_password(pwd, str(user.get("password_hash") or "")):
            raise HTTPException(status_code=400, detail="密码错误")
        if not new_username:
            raise HTTPException(status_code=400, detail="新用户名不能为空")
        if len(new_username) < 3 or len(new_username) > 32:
            raise HTTPException(status_code=400, detail="新用户名长度需在 3-32 之间")
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", new_username):
            raise HTTPException(status_code=400, detail="新用户名仅支持字母/数字/.-_")
        if new_username == current_username:
            return {"ok": True}
        with db() as conn:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (new_username,)).fetchone()
            if row is not None and int(row["id"] or 0) != int(user["id"]):
                raise HTTPException(status_code=400, detail="用户名已存在")
            conn.execute(
                "UPDATE users SET username = ?, updated_at = ? WHERE id = ?",
                (new_username, _now_ts(), int(user["id"])),
            )
        return {"ok": True}

    @app.get("/api/me")
    def api_me(user: dict[str, Any] = Depends(get_current_user)):
        return {
            "id": int(user.get("id") or 0),
            "username": str(user.get("username") or ""),
            "is_admin": bool(int(user.get("is_admin") or 0)),
            "is_disabled": bool(int(user.get("is_disabled") or 0)),
            "balance_cents": int(user.get("balance_cents") or 0),
            "created_at": int(user.get("created_at") or 0),
        }

    @app.get("/api/auth/me")
    def api_auth_me(user: dict[str, Any] = Depends(get_current_user)):
        return api_me(user)

    @app.get("/api/client_defaults")
    def api_client_defaults(user: dict[str, Any] = Depends(get_current_user)):
        with db() as conn:
            pricing = _get_pricing_config(conn)
        return {"pricing": pricing}

    @app.post("/api/internal/tg/submit")
    def api_internal_tg_submit(payload: InternalTgSubmitPayload, request: Request):
        _require_internal_tg_request(request)
        typ = str(payload.task_type or "").strip()
        if not typ:
            raise HTTPException(status_code=400, detail="task_type 不能为空")
        task_id = _new_id("task")
        params = payload.params if isinstance(payload.params, dict) else {}
        task_payload = _build_internal_tg_task_payload(task_id, typ, params)
        task_payload["tg_chat_id"] = int(payload.tg_chat_id)
        task_payload["source"] = "telegram"
        user_id = _internal_tg_submit_user_id()
        _enqueue_task(task_id, user_id, typ, task_payload)
        return {"ok": True, "id": task_id, "task_type": typ, "prompt_preview": _tg_prompt_preview(task_payload)}

    @app.post("/api/internal/tg/agent_submit")
    def api_internal_tg_agent_submit(payload: InternalTgAgentSubmitPayload, request: Request):
        _require_internal_tg_request(request)
        text = str(payload.message or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="message 不能为空")
        file_infos: list[dict[str, str]] = []
        for item in payload.files or []:
            path_text = _validated_local_file(item.path, label="TG 附件")
            kind = str(item.kind or "").strip() or _guess_file_kind(path_text)
            file_infos.append(
                {
                    "name": str(item.name or Path(path_text).name),
                    "path": path_text,
                    "kind": kind,
                }
            )
        try:
            typ, planned_payload, summary = _build_agent_task_payload(
                message=text,
                file_infos=file_infos,
                use_ai_copy=bool(payload.use_ai_copy),
                default_duration=max(_to_int(payload.duration_seconds, 15), 1),
                production_only=True,
            )
        except Exception as exc:
            typ, planned_payload, summary = _agent_chat_payload(
                reply=f"我还不能创建生产任务：{exc}。请补充具体任务类型和必要素材，或点击面板里的工作流入口按步骤提交。",
                summary="未创建生产任务",
            )

        if typ not in TG_AGENT_PRODUCTION_TASK_TYPES:
            reply = str((planned_payload or {}).get("reply") or summary or "").strip()
            if not reply:
                reply = "请补充具体生产任务和必要素材，或点击面板里的工作流入口按步骤提交。"
            return {"ok": True, "submitted": False, "task_type": typ, "summary": summary, "reply": reply}

        task_id = _new_id("task")
        planned_payload = dict(planned_payload or {})
        planned_payload["message"] = text
        planned_payload["tg_chat_id"] = int(payload.tg_chat_id)
        planned_payload["source"] = "telegram_agent"
        planned_payload.setdefault("tg_use_llm_prompt", True)
        planned_payload.setdefault("tg_user_instruction", text)
        task_payload = _build_internal_tg_task_payload(task_id, typ, planned_payload)
        task_payload["tg_chat_id"] = int(payload.tg_chat_id)
        task_payload["source"] = "telegram_agent"
        user_id = _internal_tg_submit_user_id()
        _enqueue_task(task_id, user_id, typ, task_payload)
        return {"ok": True, "id": task_id, "task_type": typ, "summary": summary, "prompt_preview": _tg_prompt_preview(task_payload)}

    @app.get("/api/internal/tg/tasks")
    def api_internal_tg_tasks(request: Request):
        _require_internal_tg_request(request)
        try:
            chat_id = int(str(request.query_params.get("chat_id") or "0").strip() or "0")
        except Exception:
            chat_id = 0
        if chat_id <= 0:
            raise HTTPException(status_code=400, detail="chat_id 必须为正整数")
        limit = min(max(_to_int(request.query_params.get("limit"), 5), 1), 20)
        tasks: list[dict[str, Any]] = []
        with db() as conn:
            rows = conn.execute(
                """
                SELECT id, type, status, input_json, output_json, error, runninghub_task_id, cost_cents, created_at, updated_at
                FROM tasks
                ORDER BY created_at DESC
                LIMIT 200
                """
            ).fetchall()
        for row in rows:
            input_payload = _json_loads(row["input_json"], {})
            if _get_tg_chat_id_from_payload(input_payload) != chat_id:
                continue
            output_payload = _json_loads(row["output_json"], {})
            tasks.append(
                {
                    "id": row["id"],
                    "type": row["type"],
                    "status": row["status"],
                    "error": row["error"],
                    "runninghub_task_id": row["runninghub_task_id"],
                    "cost_cents": int(row["cost_cents"] or 0),
                    "created_at": int(row["created_at"] or 0),
                    "updated_at": int(row["updated_at"] or 0),
                    "has_download": _task_has_download_file(output_payload),
                    "download_path": _extract_download_path(output_payload),
                    "batch_summary": _extract_batch_summary(output_payload),
                }
            )
            if len(tasks) >= limit:
                break
        return {"ok": True, "tasks": tasks}

    @app.get("/api/tasks")
    def api_tasks(limit: int = 50, user: dict[str, Any] = Depends(get_current_user)):
        lim = min(max(int(limit or 50), 1), 200)
        with db() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, type, status, error, runninghub_task_id, output_json, cost_cents, created_at, updated_at
                FROM tasks
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (int(user["id"]), lim),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["has_download"] = _task_has_download_file(_json_loads(item.get("output_json"), {}))
            item.pop("output_json", None)
            items.append(item)
        return {"items": items}

    @app.get("/api/tasks/{task_id}")
    def api_task_detail(task_id: str, user: dict[str, Any] = Depends(get_current_user)):
        with db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        task = dict(row)
        _ensure_user_can_access_task(user, task)
        return _build_task_detail_payload(task=task, include_logs=True, log_limit=1000)

    def _run_task_error_analysis_impl(task_id: str, user: dict[str, Any]) -> dict[str, Any]:
        tid = str(task_id or "").strip()
        if not tid:
            raise HTTPException(status_code=400, detail="task_id 不能为空")
        with db() as conn:
            task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
            if task_row is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            runtime = _get_runtime_config(conn)
        task = dict(task_row)
        _ensure_user_can_access_task(user, task)
        if str(task.get("status") or "").strip().lower() != "failed":
            raise HTTPException(status_code=409, detail="仅支持分析失败任务")

        gemini_host, gemini_key, _ = _resolve_llm_settings(runtime)
        gemini_port = None
        if not gemini_key or not gemini_host:
            raise HTTPException(status_code=400, detail="启用 AI 分析需先在后台配置文字模型 API")

        detail = _build_task_detail_payload(task=task, include_logs=True, log_limit=300)
        logs = detail.get("logs") if isinstance(detail.get("logs"), list) else []
        selected_logs = logs[-80:]
        event_lines: list[str] = []
        for payload in selected_logs:
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            extra: dict[str, Any] = {}
            for key in ("stage", "status", "level", "source", "item_index", "item_id", "runninghub_task_id", "error"):
                value = data.get(key)
                if value not in (None, "", [], {}):
                    extra[key] = value
            event_lines.append(
                f"[{int(payload.get('id') or 0)}|{int(payload.get('created_at') or 0)}] "
                f"{payload.get('kind') or '-'} | {payload.get('message') or '-'} | "
                f"{json.dumps(_sanitize_log_payload(extra), ensure_ascii=False)}"
            )

        analysis_payload = {
            "task": {
                "id": detail.get("id"),
                "type": detail.get("type"),
                "status": detail.get("status"),
                "workflow_name": detail.get("workflow_name"),
                "workflow_id": detail.get("workflow_id"),
                "runninghub_task_id": detail.get("runninghub_task_id"),
                "error": detail.get("error"),
                "cost_cents": detail.get("cost_cents"),
                "has_download": detail.get("has_download"),
                "batch_summary": {
                    "total_count": detail.get("total_count"),
                    "success_count": detail.get("success_count"),
                    "failed_count": detail.get("failed_count"),
                    "first_error": detail.get("first_error"),
                },
                "input": _sanitize_log_payload(detail.get("input") or {}),
                "output": _sanitize_log_payload(detail.get("output") or {}),
                "usage": _sanitize_log_payload(detail.get("usage") or {}),
            },
            "events": event_lines,
        }
        system_prompt = (
            "你是任务排障助手。请阅读任务摘要和日志，输出 JSON。"
            'JSON 字段固定为 {"summary": string, "root_causes": [string], "suggestions": [string], "confidence": number, "notable_events": [number]}。'
            "要求：结论简明、面向工程排障；不要编造不存在的信息；confidence 为 0 到 1。"
        )
        result = get_gemini.request_gemini3_pro_json(
            user_input=_truncate_text(json.dumps(analysis_payload, ensure_ascii=False), max_len=12000),
            host=gemini_host,
            api_key=gemini_key,
            system_prompt=system_prompt,
            port=gemini_port,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            raise HTTPException(status_code=502, detail=f"Gemini 错误分析失败：{str((result or {}).get('error') or '未知错误')}")
        parsed = result.get("parsed")
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=502, detail="Gemini 错误分析未返回有效 JSON")

        event_ids = [int(p.get("id") or 0) for p in selected_logs if int(p.get("id") or 0) > 0]
        event_data = {
            "stage": "error_analysis",
            "status": "success",
            "level": "info",
            "source": "gemini",
            "analysis_type": "gemini_error_analysis",
            "summary": _truncate_text(parsed.get("summary"), max_len=1200),
            "root_causes": _truncate_payload(parsed.get("root_causes") if isinstance(parsed.get("root_causes"), list) else [], max_string=600),
            "suggestions": _truncate_payload(parsed.get("suggestions") if isinstance(parsed.get("suggestions"), list) else [], max_string=600),
            "confidence": max(min(_to_float(parsed.get("confidence"), 0.0), 1.0), 0.0),
            "notable_events": [eid for eid in (parsed.get("notable_events") if isinstance(parsed.get("notable_events"), list) else []) if isinstance(eid, int)],
            "based_on_event_ids": event_ids,
            "user_visible": True,
        }
        with db() as conn:
            _insert_task_event(conn, task_id=tid, user_id=int(task["user_id"]), kind="analysis", message="Gemini 错误分析完成", data=event_data)
        return {"ok": True, "analysis": event_data}

    @app.post("/api/tasks/{task_id}/analyze_error")
    def api_task_analyze_error(task_id: str, user: dict[str, Any] = Depends(get_current_user)):
        return _run_task_error_analysis_impl(task_id, user)

    @app.get("/api/tasks/{task_id}/events")
    async def api_task_events(
        request: Request,
        task_id: str,
        last_event_id: int = 0,
        user: dict[str, Any] = Depends(get_current_user),
    ):
        tid = str(task_id or "").strip()
        if not tid:
            raise HTTPException(status_code=400, detail="task_id 不能为空")
        with db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        task = dict(row)
        _ensure_user_can_access_task(user, task)

        start_after = max(int(last_event_id or 0), 0)

        async def gen():
            nonlocal start_after
            done_seen = False
            while True:
                if await request.is_disconnected():
                    return
                with db() as conn:
                    rows = conn.execute(
                        """
                        SELECT id, kind, message, data_json, created_at
                        FROM task_events
                        WHERE task_id = ? AND user_id = ? AND id > ?
                        ORDER BY id ASC
                        LIMIT 200
                        """,
                        (tid, int(user["id"]), int(start_after)),
                    ).fetchall()
                    task_row = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()
                if rows:
                    for r in rows:
                        payload = _serialize_task_event_record(task=task, event_row=r)
                        eid = int(payload["id"])
                        start_after = eid
                        yield f"id: {eid}\n"
                        yield f"event: {payload['kind']}\n"
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                else:
                    status = str(task_row["status"]) if task_row else ""
                    if status in {"success", "failed"}:
                        if done_seen:
                            return
                        done_seen = True
                    await asyncio.sleep(1.0)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/tasks/{task_id}/download")
    def api_task_download(task_id: str, user: dict[str, Any] = Depends(get_current_user)):
        with db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        task = dict(row)
        _ensure_user_can_access_task(user, task)
        output_data = _json_loads(task.get("output_json"), {})
        path_text = _extract_download_path(output_data)
        if not path_text:
            raise HTTPException(status_code=404, detail="任务尚未生成可下载文件")
        path = Path(path_text).resolve()
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="下载文件不存在")
        return FileResponse(str(path), filename=path.name)

    @app.delete("/api/tasks/{task_id}")
    def api_task_delete(task_id: str, user: dict[str, Any] = Depends(get_current_user)):
        tid = str(task_id or "").strip()
        if not tid:
            raise HTTPException(status_code=400, detail="task_id 不能为空")
        with db() as conn:
            row = conn.execute("SELECT id, user_id, status FROM tasks WHERE id = ?", (tid,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            task = dict(row)
            _ensure_user_can_access_task(user, task)
            status = str(task.get("status") or "").strip().lower()
            if status in {"running", "queued"}:
                raise HTTPException(status_code=409, detail="运行中或排队中的任务不能删除")
            conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        _delete_task_artifacts(tid)
        return {"ok": True, "id": tid}

    @app.post("/api/tasks/{task_id}/retry")
    def api_task_retry(task_id: str, user: dict[str, Any] = Depends(get_current_user)):
        _require_positive_balance(user)
        tid = str(task_id or "").strip()
        if not tid:
            raise HTTPException(status_code=400, detail="task_id 不能为空")
        with db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        task = dict(row)
        _ensure_user_can_access_task(user, task)
        status = str(task.get("status") or "").strip().lower()
        if status != "failed":
            raise HTTPException(status_code=409, detail="仅支持重试失败任务")
        task_type = str(task.get("type") or "").strip()
        if not task_type:
            raise HTTPException(status_code=400, detail="任务类型缺失")

        payload = _json_loads(task.get("input_json"), {})
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="任务输入参数损坏，无法重试")

        def walk_and_collect(obj: Any, found: list[tuple[str, str]]):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        walk_and_collect(v, found)
                        continue
                    if not isinstance(v, str):
                        continue
                    key = str(k or "").strip().lower()
                    text = str(v or "").strip()
                    if not text:
                        continue
                    if text.startswith("http://") or text.startswith("https://"):
                        continue
                    if key.endswith("_url") and not key.endswith("_local_path"):
                        continue
                    if not (
                        key.endswith("_local_path")
                        or key.endswith("_zip_path")
                        or key.endswith("_dir")
                        or key.endswith("_path")
                    ):
                        continue
                    found.append((str(k), text))
            elif isinstance(obj, list):
                for it in obj:
                    walk_and_collect(it, found)

        candidates: list[tuple[str, str]] = []
        walk_and_collect(payload, candidates)
        missing: list[str] = []
        for k, p in candidates:
            path = Path(str(p)).expanduser()
            try:
                path = path.resolve()
            except Exception:
                path = Path(str(p)).expanduser()
            if not path.exists():
                missing.append(f"{k}={path}")
        if missing:
            raise HTTPException(status_code=409, detail="原任务素材已不存在，无法重试，请重新上传文件创建新任务")

        new_id = _new_id("task")
        _enqueue_task(new_id, int(task.get("user_id") or 0), task_type, payload)
        return {"id": new_id, "task_type": task_type, "source_task_id": tid}

    @app.post("/api/tasks/{task_id}/retry_resume")
    def api_task_retry_resume(task_id: str, user: dict[str, Any] = Depends(get_current_user)):
        _require_positive_balance(user)
        tid = str(task_id or "").strip()
        if not tid:
            raise HTTPException(status_code=400, detail="task_id 不能为空")
        with db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        task = dict(row)
        _ensure_user_can_access_task(user, task)
        if str(task.get("status") or "").strip().lower() != "failed":
            raise HTTPException(status_code=409, detail="仅支持断点重试失败任务")
        task_type = str(task.get("type") or "").strip()
        if task_type != "commerce_video":
            raise HTTPException(status_code=409, detail="当前仅商业视频生成支持断点重试")
        source_workdir = _build_task_workdir(tid)
        has_checkpoint = (source_workdir / "commerce_out").exists() or (source_workdir / "commerce_input").exists()
        if not has_checkpoint:
            raise HTTPException(status_code=409, detail="未找到可续跑的任务产物，请使用普通重试")

        payload = _json_loads(task.get("input_json"), {})
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="任务输入参数损坏，无法断点重试")
        payload["resume_from_task_id"] = tid
        payload["retry_mode"] = "resume"

        new_id = _new_id("task")
        _enqueue_task(new_id, int(task.get("user_id") or 0), task_type, payload)
        return {
            "id": new_id,
            "task_type": task_type,
            "source_task_id": tid,
            "retry_mode": "resume",
        }

    @app.get("/api/ledger")
    def api_ledger(limit: int = 50, user: dict[str, Any] = Depends(get_current_user)):
        lim = min(max(int(limit or 50), 1), 200)
        with db() as conn:
            rows = conn.execute(
                """
                SELECT id, type, amount_cents, ref_task_id, meta_json, created_at
                FROM ledger
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (int(user["id"]), lim),
            ).fetchall()
        return {
            "items": [
                {
                    "id": str(r["id"]),
                    "type": str(r["type"]),
                    "amount_cents": int(r["amount_cents"]),
                    "ref_task_id": str(r["ref_task_id"]),
                    "meta": _json_loads(r["meta_json"], {}),
                    "created_at": int(r["created_at"]),
                }
                for r in rows
            ]
        }

    @app.post("/api/agent/submit")
    async def api_agent_submit(
        message: str = Form(...),
        use_doubao: str = Form("1"),
        use_ai_copy: str | None = Form(None),
        duration_seconds: int = Form(15),
        files: list[UploadFile] = File(default=[]),
        user: dict[str, Any] = Depends(get_current_user),
    ):
        text = str(message or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="消息不能为空")

        task_id = _new_id("task")
        saved_files: list[dict[str, str]] = []
        for idx, upload in enumerate(files or [], start=1):
            saved = await _save_upload_file(str(user.get("username") or ""), task_id, f"attach_{idx}", upload)
            if not saved:
                continue
            saved_files.append(
                {
                    "name": str(upload.filename or ""),
                    "path": saved,
                    "kind": _guess_file_kind(saved),
                }
            )

        try:
            use_ai = _to_bool(use_ai_copy, _to_bool(use_doubao, True))
            task_type, payload, summary = _build_agent_task_payload(
                message=text,
                file_infos=saved_files,
                use_ai_copy=use_ai,
                default_duration=max(_to_int(duration_seconds, 15), 1),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload = dict(payload or {})
        payload["message"] = text
        payload["source"] = "agent_chat"
        _enqueue_task(task_id, int(user["id"]), task_type, payload)
        return {
            "id": task_id,
            "task_type": task_type,
            "summary": summary,
        }

    @app.post("/api/batch/create_video/plan")
    async def api_batch_create_video_plan(
        zip_file: UploadFile = File(...),
        defaults_json: str = Form("{}"),
        param_prompt: str = Form(""),
        enable_ai: str = Form("1"),
        user: dict[str, Any] = Depends(get_current_user),
    ):
        _require_positive_balance(user)
        plan_id = _new_id("plan")
        zip_path = await _save_upload_file(str(user.get("username") or ""), plan_id, "batch_zip", zip_file)
        if not zip_path:
            raise HTTPException(status_code=400, detail="zip_file 不能为空")
        defaults = _extract_json_from_text(defaults_json)
        defaults = defaults if isinstance(defaults, dict) else {}

        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(user.get("username") or "")).strip("._-") or "user"
        workdir = UPLOAD_ROOT / safe / plan_id
        src_dir = workdir / "plan_src"
        _extract_zip_to_dir(Path(zip_path), src_dir)
        try:
            items = _scan_batch_items(src_dir)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not items:
            raise HTTPException(status_code=400, detail="zip 内未找到可用的图片素材（至少每组需要 1 张图片）")

        enable = _to_bool(enable_ai, True)
        plan: dict[str, Any] = {
            "defaults": defaults,
            "items": [
                {
                    "id": it["id"],
                    "model_image": it["model_image"],
                    "product_image": it["product_image"],
                    "camera_video": it.get("camera_video") or "",
                    "audio": it.get("audio") or it.get("audio_file") or "",
                    "match_key": it.get("match_key") or "",
                    "match_mode": it.get("match_mode") or "",
                    "audio_match_state": it.get("audio_match_state") or "",
                    "source_folder": it.get("source_folder") or "",
                    "params": {},
                }
                for it in items
            ],
        }
        if enable:
            with db() as conn:
                runtime = _get_runtime_config(conn)
        gemini_host, gemini_key, _ = _resolve_llm_settings(runtime)
        gemini_port = None
        if not gemini_key or not gemini_host:
            raise HTTPException(status_code=400, detail="启用 AI 分析需先在后台配置文字模型 API")
            parsed_plan = _plan_batch_params_with_gemini(
                user_prompt=str(param_prompt or ""),
                items=items,
                defaults=defaults,
                gemini_host=gemini_host,
                gemini_key=gemini_key,
                gemini_port=gemini_port,
            )
            if isinstance(parsed_plan, dict) and isinstance(parsed_plan.get("items"), list):
                plan = parsed_plan

        return {
            "ok": True,
            "plan_id": plan_id,
            "items": items,
            "plan": plan,
        }

    @app.post("/api/batch/create_video/run")
    def api_batch_create_video_run(
        request: dict[str, Any],
        user: dict[str, Any] = Depends(get_current_user),
    ):
        _require_positive_balance(user)
        plan_id = str((request or {}).get("plan_id") or "").strip()
        plan = (request or {}).get("plan")
        if not plan_id:
            raise HTTPException(status_code=400, detail="plan_id 不能为空")
        if not re.fullmatch(r"plan_[0-9a-f]{20}", plan_id):
            raise HTTPException(status_code=400, detail="plan_id 非法")
        if not isinstance(plan, dict):
            raise HTTPException(status_code=400, detail="plan 必须是 JSON 对象")

        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(user.get("username") or "")).strip("._-") or "user"
        plan_dir = (UPLOAD_ROOT / safe / plan_id).resolve()
        alt = list(plan_dir.glob("batch_zip.*"))
        zip_path = alt[0].resolve() if alt else Path("")
        if not zip_path.exists():
            raise HTTPException(status_code=404, detail="找不到计划对应的 zip 文件，请重新生成计划")
        if plan_dir not in zip_path.parents:
            raise HTTPException(status_code=400, detail="zip_path 非法")

        defaults = plan.get("defaults") if isinstance(plan.get("defaults"), dict) else {}
        items = plan.get("items") if isinstance(plan.get("items"), list) else []
        if not items:
            raise HTTPException(status_code=400, detail="plan.items 不能为空")
        normalized_items: list[dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                model_rel = _normalize_batch_media_rel_path(it.get("model_image"), field_name="model_image")
                product_rel = _normalize_batch_media_rel_path(
                    it.get("product_image") or model_rel,
                    field_name="product_image",
                )
                camera_rel = _normalize_batch_media_rel_path(it.get("camera_video"), field_name="camera_video")
                audio_rel = _normalize_batch_media_rel_path(
                    it.get("audio") or it.get("audio_file"),
                    field_name="audio",
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            normalized_items.append(
                {
                    "id": str(it.get("id") or ""),
                    "model_image": model_rel,
                    "product_image": product_rel,
                    "camera_video": camera_rel,
                    "audio": audio_rel,
                    "params": it.get("params") if isinstance(it.get("params"), dict) else {},
                }
            )
        if not normalized_items:
            raise HTTPException(status_code=400, detail="plan.items 无有效条目")

        task_id = _new_id("task")
        payload = {
            "zip_path": str(zip_path),
            "defaults": defaults,
            "items": normalized_items,
        }
        _enqueue_task(task_id, int(user["id"]), "batch_create_video", payload)
        return {"ok": True, "id": task_id}

    @app.post("/api/tasks/replace_model")
    async def api_task_replace_model(
        mode: str = Form(replace_model.MODE_ORIGINAL),
        prompt: str = Form(""),
        width: int = Form(576),
        height: int = Form(1024),
        frame: int = Form(30),
        duration_seconds: int = Form(10),
        start_seconds: int = Form(0),
        app_id: str = Form(""),
        video_url: str = Form(""),
        image_url: str = Form(""),
        gemini_input_tokens: int = Form(0),
        gemini_output_tokens: int = Form(0),
        nano_images: int = Form(0),
        video_file: UploadFile | None = File(None),
        image_file: UploadFile | None = File(None),
        user: dict[str, Any] = Depends(get_current_user),
    ):
        _require_positive_balance(user)
        task_id = _new_id("task")
        payload = _normalize_replace_model_payload(
            {
                "mode": mode,
                "prompt": prompt,
                "width": width,
                "height": height,
                "frame": frame,
                "duration_seconds": duration_seconds,
                "start_seconds": start_seconds,
                "app_id": app_id,
                "video_url": video_url,
                "image_url": image_url,
                "gemini_input_tokens": gemini_input_tokens,
                "gemini_output_tokens": gemini_output_tokens,
                "nano_images": nano_images,
            }
        )
        payload["video_local_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "video", video_file)
        payload["image_local_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "image", image_file)
        _enqueue_task(task_id, int(user["id"]), "replace_model", payload)
        return {"id": task_id}

    @app.post("/api/tasks/replace_product")
    async def api_task_replace_product(
        product_name: str = Form(""),
        prompt_text: str = Form(""),
        width: int = Form(576),
        height: int = Form(1024),
        frame_rate: int = Form(30),
        duration_seconds: int = Form(15),
        app_id: str = Form(""),
        video_url: str = Form(""),
        image_url: str = Form(""),
        gemini_input_tokens: int = Form(0),
        gemini_output_tokens: int = Form(0),
        nano_images: int = Form(0),
        video_file: UploadFile | None = File(None),
        image_file: UploadFile | None = File(None),
        user: dict[str, Any] = Depends(get_current_user),
    ):
        _require_positive_balance(user)
        task_id = _new_id("task")
        payload = {
            "product_name": product_name,
            "prompt_text": prompt_text,
            "width": width,
            "height": height,
            "frame_rate": frame_rate,
            "duration_seconds": duration_seconds,
            "app_id": app_id,
            "video_url": video_url,
            "image_url": image_url,
            "gemini_input_tokens": gemini_input_tokens,
            "gemini_output_tokens": gemini_output_tokens,
            "nano_images": nano_images,
        }
        payload["video_local_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "video", video_file)
        payload["image_local_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "image", image_file)
        _enqueue_task(task_id, int(user["id"]), "replace_product", payload)
        return {"id": task_id}

    @app.post("/api/tasks/create_audio")
    def api_task_create_audio(
        request: dict[str, Any],
        user: dict[str, Any] = Depends(get_current_user),
    ):
        _require_positive_balance(user)
        task_id = _new_id("task")
        payload = dict(request or {})
        _enqueue_task(task_id, int(user["id"]), "create_audio", payload)
        return {"id": task_id}

    @app.post("/api/tasks/get_nano_banana")
    async def api_task_get_nano_banana(
        prompt: str = Form(...),
        image_model_provider_base_url: str = Form(""),
        image_model_provider_api_key_gemini: str = Form(""),
        image_model_provider_api_key_gpt: str = Form(""),
        image_generate_model: str = Form(""),
        input_image_url: str = Form(""),
        gemini_input_tokens: int = Form(0),
        gemini_output_tokens: int = Form(0),
        nano_images: int = Form(1),
        input_image_file: UploadFile | None = File(None),
        user: dict[str, Any] = Depends(get_current_user),
    ):
        _require_positive_balance(user)
        task_id = _new_id("task")
        payload = {
            "prompt": prompt,
            "image_model_provider_base_url": image_model_provider_base_url,
            "image_model_provider_api_key_gemini": image_model_provider_api_key_gemini,
            "image_model_provider_api_key_gpt": image_model_provider_api_key_gpt,
            "image_generate_model": image_generate_model,
            "input_image_url": input_image_url,
            "gemini_input_tokens": gemini_input_tokens,
            "gemini_output_tokens": gemini_output_tokens,
            "nano_images": nano_images,
        }
        payload["input_image_local_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "input_image", input_image_file)
        _enqueue_task(task_id, int(user["id"]), "get_nano_banana", payload)
        return {"id": task_id}

    @app.post("/api/tasks/get_gemini")
    async def api_task_get_gemini(
        user_input: str = Form(...),
        llm_base_url: str = Form(""),
        llm_api_key: str = Form(""),
        llm_model: str = Form(""),
        system_prompt: str = Form(""),
        parameters_json: str = Form(""),
        gemini_input_tokens: int = Form(0),
        gemini_output_tokens: int = Form(0),
        images: list[UploadFile] = File(default=[]),
        videos: list[UploadFile] = File(default=[]),
        user: dict[str, Any] = Depends(get_current_user),
    ):
        _require_positive_balance(user)
        task_id = _new_id("task")
        payload: dict[str, Any] = {
            "user_input": user_input,
            "llm_base_url": llm_base_url,
            "llm_api_key": llm_api_key,
            "llm_model": llm_model,
            "system_prompt": system_prompt,
            "gemini_input_tokens": gemini_input_tokens,
            "gemini_output_tokens": gemini_output_tokens,
        }
        params = _extract_json_from_text(parameters_json)
        payload["parameters"] = params if params else parameters_json

        image_paths: list[str] = []
        for idx, upload in enumerate(images or [], start=1):
            saved = await _save_upload_file(str(user.get("username") or ""), task_id, f"image_{idx}", upload)
            if saved:
                image_paths.append(saved)
        video_paths: list[str] = []
        for idx, upload in enumerate(videos or [], start=1):
            saved = await _save_upload_file(str(user.get("username") or ""), task_id, f"video_{idx}", upload)
            if saved:
                video_paths.append(saved)
        payload["image_paths"] = image_paths
        payload["video_paths"] = video_paths

        _enqueue_task(task_id, int(user["id"]), "get_gemini", payload)
        return {"id": task_id}

    @app.post("/api/tasks/create_video")
    async def api_task_create_video(
        image_model_provider_base_url: str = Form(""),
        image_model_provider_api_key_gemini: str = Form(""),
        image_model_provider_api_key_gpt: str = Form(""),
        image_generate_model: str = Form(""),
        speech_text: str = Form(""),
        prompt_text: str = Form(""),
        product_name: str = Form("商品"),
        style_hint: str = Form("自然口播，真实电商场景"),
        nano_prompt: str = Form("电商口播视频场景截图风格：真实人物在室内/直播间展示商品，手持商品或放在手掌上讲解；写实摄影、柔和补光、干净背景；9:16；画面不要文字/水印/海报排版。"),
        duration_seconds: int = Form(15),
        language: str = Form("Chinese"),
        emotion: str = Form("happy"),
        model_choice: str = Form("1.7B"),
        speaker: str = Form("Ryan"),
        video_app_id: str = Form(""),
        instance_type: str = Form("default"),
        use_personal_queue: str = Form("0"),
        camera_video_url: str = Form(""),
        use_ai_copy: str = Form("1"),
        gemini_input_tokens: int = Form(0),
        gemini_output_tokens: int = Form(0),
        model_image: UploadFile | None = File(None),
        product_image: UploadFile | None = File(None),
        camera_video_file: UploadFile | None = File(None),
        user: dict[str, Any] = Depends(get_current_user),
    ):
        _require_positive_balance(user)
        if model_image is None or product_image is None:
            raise HTTPException(status_code=400, detail="带货视频生成需要上传模特图和商品图")
        task_id = _new_id("task")
        payload = {
            "image_model_provider_base_url": image_model_provider_base_url,
            "image_model_provider_api_key_gemini": image_model_provider_api_key_gemini,
            "image_model_provider_api_key_gpt": image_model_provider_api_key_gpt,
            "image_generate_model": image_generate_model,
            "speech_text": speech_text,
            "prompt_text": prompt_text,
            "product_name": product_name,
            "style_hint": style_hint,
            "nano_prompt": nano_prompt,
            "duration_seconds": duration_seconds,
            "language": language,
            "emotion": emotion,
            "model_choice": model_choice,
            "speaker": speaker,
            "video_app_id": video_app_id,
            "instance_type": instance_type,
            "use_personal_queue": _to_bool(use_personal_queue),
            "camera_video_url": camera_video_url,
            "use_ai_copy": _to_bool(use_ai_copy, True),
            "gemini_input_tokens": gemini_input_tokens,
            "gemini_output_tokens": gemini_output_tokens,
        }
        payload["model_image_local_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "model_image", model_image)
        payload["product_image_local_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "product_image", product_image)
        payload["camera_video_local_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "camera_video", camera_video_file)
        _enqueue_task(task_id, int(user["id"]), "create_video", payload)
        return {"id": task_id}

    @app.post("/api/tasks/submit")
    async def api_task_submit(
        request: Request,
        task_type: str = Form(...),
        params_json: str = Form("{}"),
        files: list[UploadFile] = File(default=[]),
        user: dict[str, Any] = Depends(get_current_user),
    ):
        _require_positive_balance(user)
        typ = str(task_type or "").strip()
        if not typ:
            raise HTTPException(status_code=400, detail="task_type 不能为空")
        params = _extract_json_from_text(params_json)
        params = params if isinstance(params, dict) else {}

        task_id = _new_id("task")
        saved: list[dict[str, str]] = []
        for idx, upload in enumerate(files or [], start=1):
            path = await _save_upload_file(str(user.get("username") or ""), task_id, f"file_{idx}", upload)
            if not path:
                continue
            saved.append({"path": path, "name": str(upload.filename or ""), "kind": _guess_file_kind(path)})

        images = [s for s in saved if s.get("kind") == "image"]
        videos = [s for s in saved if s.get("kind") == "video"]
        audios = [s for s in saved if s.get("kind") == "audio"]
        zips = [s for s in saved if s.get("kind") == "zip"]

        try:
            payload: dict[str, Any] = dict(params)
            if typ == "replace_model":
                payload = _normalize_replace_model_payload(payload)
                if _to_bool(payload.get("batch_mode"), False) and zips:
                    if images or videos or audios:
                        raise HTTPException(status_code=400, detail="批量 zip 模式请仅上传 zip（不要混传图片/视频/音频）")
                    batch_payload = _build_batch_payload_video_image_from_uploaded_zips(zips=zips, params=payload)
                    batch_payload["mode"] = _normalize_replace_model_mode(batch_payload.get("defaults") if isinstance(batch_payload.get("defaults"), dict) else payload)
                    batch_payload["uploaded_files"] = [{"name": s["name"], "kind": s["kind"]} for s in saved]
                    batch_payload["source_task_type"] = str(typ)
                    _enqueue_task(task_id, int(user["id"]), "batch_replace_model", batch_payload)
                    return {"id": task_id, "task_type": "batch_replace_model"}
                if not videos or not images:
                    raise HTTPException(status_code=400, detail=f"replace_model 需要上传 1 个视频和 1 张图片（已识别：{_format_uploaded_files(saved)}）")
                payload["video_local_path"] = str(videos[0]["path"])
                payload["image_local_path"] = str(images[0]["path"])
            elif typ == "replace_product":
                if _to_bool(payload.get("batch_mode"), False) and zips:
                    if images or videos or audios:
                        raise HTTPException(status_code=400, detail="批量 zip 模式请仅上传 zip（不要混传图片/视频/音频）")
                    batch_payload = _build_batch_payload_video_image_from_uploaded_zips(zips=zips, params=payload)
                    batch_payload["uploaded_files"] = [{"name": s["name"], "kind": s["kind"]} for s in saved]
                    batch_payload["source_task_type"] = str(typ)
                    _enqueue_task(task_id, int(user["id"]), "batch_replace_product", batch_payload)
                    return {"id": task_id, "task_type": "batch_replace_product"}
                if not videos or not images:
                    raise HTTPException(status_code=400, detail=f"replace_product 需要上传 1 个视频和 1 张图片（已识别：{_format_uploaded_files(saved)}）")
                payload["video_local_path"] = str(videos[0]["path"])
                payload["image_local_path"] = str(images[0]["path"])
            elif typ == "create_audio":
                if not str(payload.get("speech_text") or payload.get("word") or "").strip():
                    raise HTTPException(status_code=400, detail="create_audio 需要 speech_text")
            elif typ in {"create_video", "commerce_video"}:
                if _to_bool(payload.get("batch_mode"), False) and zips:
                    if images or videos or audios:
                        raise HTTPException(status_code=400, detail="批量 zip 模式请仅上传 zip（不要混传图片/视频/音频）")
                    try:
                        batch_payload = _build_batch_payload_from_uploaded_zips(zips=zips, params=payload)
                    except RuntimeError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc
                    batch_payload["uploaded_files"] = [{"name": s["name"], "kind": s["kind"]} for s in saved]
                    batch_payload["source_task_type"] = str(typ)
                    _enqueue_task(task_id, int(user["id"]), "batch_create_video", batch_payload)
                    return {"id": task_id, "task_type": "batch_create_video"}
                scene_image_local_path = str(payload.get("scene_image_local_path") or "").strip()
                if scene_image_local_path:
                    if not images:
                        raise HTTPException(status_code=400, detail=f"场景图直出视频需要至少上传 1 张模特图（已识别：{_format_uploaded_files(saved)}）")
                    payload["model_image_local_path"] = str(images[0]["path"])
                    payload["product_image_local_path"] = scene_image_local_path
                    payload["generated_scene_image_local_path"] = scene_image_local_path
                else:
                    if len(images) < 2:
                        raise HTTPException(status_code=400, detail=f"带货视频生成需要上传 2 张图片（先模特后商品），可选 1 个运镜视频（已识别：{_format_uploaded_files(saved)}）")
                    payload["model_image_local_path"] = str(images[0]["path"])
                    payload["product_image_local_path"] = str(images[1]["path"])
                if videos:
                    payload["camera_video_local_path"] = str(videos[0]["path"])
                if audios:
                    payload["audio_local_path"] = str(audios[0]["path"])
            elif typ == "image_generate":
                mode = str(payload.get("mode") or "").strip() or ("model_product" if len(images) >= 2 else "product_only")
                payload["mode"] = mode
                if mode == "model_product":
                    if len(images) < 2:
                        raise HTTPException(status_code=400, detail=f"图片生成（模特+商品）需要上传 2 张图片（先模特后商品）（已识别：{_format_uploaded_files(saved)}）")
                    payload["model_image_local_path"] = str(images[0]["path"])
                    payload["product_image_local_path"] = str(images[1]["path"])
                else:
                    if not images:
                        raise HTTPException(status_code=400, detail=f"图片生成需要至少上传 1 张商品图（已识别：{_format_uploaded_files(saved)}）")
                    payload["mode"] = "product_only"
                    payload["product_image_local_path"] = str(images[0]["path"])
            elif typ == "get_nano_banana":
                if not images:
                    raise HTTPException(status_code=400, detail=f"get_nano_banana 需要上传 1 张图片（已识别：{_format_uploaded_files(saved)}）")
                payload["input_image_local_path"] = str(images[0]["path"])
            elif typ == "get_gemini":
                if images:
                    payload["image_paths"] = [str(s["path"]) for s in images]
                if videos:
                    payload["video_paths"] = [str(s["path"]) for s in videos]
            elif typ == "replace_productANDmodel":
                def pick_zip(keyword: str) -> str:
                    for s in zips:
                        if keyword in str(s.get("name") or "").lower():
                            return str(s["path"])
                    return ""
                model_zip = pick_zip("model")
                product_zip = pick_zip("product")
                video_zip = pick_zip("video")
                used = {p for p in (model_zip, product_zip, video_zip) if p}
                rest = [s for s in zips if str(s.get("path") or "") not in used]
                if not model_zip and rest:
                    model_zip = str(rest.pop(0).get("path") or "")
                if not product_zip and rest:
                    product_zip = str(rest.pop(0).get("path") or "")
                if not video_zip and rest:
                    video_zip = str(rest.pop(0).get("path") or "")

                if model_zip:
                    payload["model_zip_path"] = str(model_zip)
                if product_zip:
                    payload["product_zip_path"] = str(product_zip)
                if video_zip:
                    payload["video_zip_path"] = str(video_zip)
                if videos and not payload.get("video_zip_path"):
                    payload["video_paths"] = [str(s["path"]) for s in videos]
                if images and not payload.get("model_zip_path") and not payload.get("product_zip_path"):
                    payload["mixed_image_paths"] = [str(s["path"]) for s in images]

                has_model = bool(payload.get("model_zip_path")) or bool(payload.get("model_dir_path")) or bool(payload.get("mixed_image_paths"))
                has_product = bool(payload.get("product_zip_path")) or bool(payload.get("product_dir_path")) or bool(payload.get("mixed_image_paths"))
                has_video = bool(payload.get("video_zip_path")) or bool(payload.get("video_dir_path")) or bool(payload.get("video_paths"))
                if not (has_model and has_product and has_video):
                    raise HTTPException(
                        status_code=400,
                        detail=f"replace_productANDmodel 需要：model+product 图片（可 2 个 zip，或直接混传图片自动分拣）+ 原视频（zip 或视频文件）（已识别：{_format_uploaded_files(saved)}）",
                    )
                mp = payload.get("model_params")
                if not isinstance(mp, dict):
                    mp_json = payload.get("model_params_json")
                    if isinstance(mp_json, str) and mp_json.strip():
                        parsed = _extract_json_from_text(mp_json)
                        payload["model_params"] = parsed if isinstance(parsed, dict) else {}
                pp = payload.get("product_params")
                if not isinstance(pp, dict):
                    pp_json = payload.get("product_params_json")
                    if isinstance(pp_json, str) and pp_json.strip():
                        parsed = _extract_json_from_text(pp_json)
                        payload["product_params"] = parsed if isinstance(parsed, dict) else {}
                pm = payload.get("product_mapping")
                if not isinstance(pm, list):
                    pm_json = payload.get("product_mapping_json")
                    if isinstance(pm_json, str) and pm_json.strip():
                        parsed = _extract_json_from_text(pm_json)
                        if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
                            payload["product_mapping"] = parsed.get("items")
                        elif isinstance(parsed, list):
                            payload["product_mapping"] = parsed
            else:
                raise HTTPException(status_code=400, detail=f"不支持的 task_type: {typ}")
        except HTTPException:
            _delete_task_artifacts(task_id)
            raise

        payload["uploaded_files"] = [{"name": s["name"], "kind": s["kind"]} for s in saved]
        _enqueue_task(task_id, int(user["id"]), typ, payload)
        return {"id": task_id, "task_type": typ}

    @app.post("/api/tasks/replace_productANDmodel")
    async def api_task_replace_product_and_model(
        model_app_id: str = Form(""),
        product_app_id: str = Form(""),
        match_mode: str = Form("cycle"),
        fixed_index: int = Form(1),
        auto_rename: str = Form("1"),
        model_params_json: str = Form(""),
        product_params_json: str = Form(""),
        product_mapping_json: str = Form(""),
        gemini_input_tokens: int = Form(0),
        gemini_output_tokens: int = Form(0),
        nano_images: int = Form(0),
        model_zip: UploadFile | None = File(None),
        product_zip: UploadFile | None = File(None),
        video_zip: UploadFile | None = File(None),
        user: dict[str, Any] = Depends(get_current_user),
    ):
        _require_positive_balance(user)
        task_id = _new_id("task")
        payload: dict[str, Any] = {
            "model_app_id": model_app_id,
            "product_app_id": product_app_id,
            "match_mode": match_mode,
            "fixed_index": fixed_index,
            "auto_rename": _to_bool(auto_rename, True),
            "gemini_input_tokens": gemini_input_tokens,
            "gemini_output_tokens": gemini_output_tokens,
            "nano_images": nano_images,
        }
        model_params = _extract_json_from_text(model_params_json)
        product_params = _extract_json_from_text(product_params_json)
        product_mapping = _extract_json_from_text(product_mapping_json)
        payload["model_params"] = model_params if isinstance(model_params, dict) else {}
        payload["product_params"] = product_params if isinstance(product_params, dict) else {}
        if isinstance(product_mapping, dict) and isinstance(product_mapping.get("items"), list):
            payload["product_mapping"] = product_mapping.get("items")
        elif isinstance(product_mapping, list):
            payload["product_mapping"] = product_mapping

        payload["model_zip_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "model_zip", model_zip)
        payload["product_zip_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "product_zip", product_zip)
        payload["video_zip_path"] = await _save_upload_file(str(user.get("username") or ""), task_id, "video_zip", video_zip)

        _enqueue_task(task_id, int(user["id"]), "replace_productANDmodel", payload)
        return {"id": task_id}

    @app.get("/api/admin/runtime_config")
    def api_admin_get_runtime_config(user: dict[str, Any] = Depends(require_admin)):
        try:
            with db() as conn:
                runtime = _get_runtime_config(conn)
        except RuntimeConfigFileError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return runtime

    @app.put("/api/admin/runtime_config")
    def api_admin_set_runtime_config(payload: RuntimeConfigPayload, user: dict[str, Any] = Depends(require_admin)):
        data = payload.model_dump()
        merged = dict(DEFAULT_RUNTIME_CONFIG)
        merged.update({k: str(v).strip() if isinstance(v, str) else v for k, v in data.items()})
        try:
            merged = _normalize_runtime_config(merged)
            with _RUNTIME_CONFIG_LOCK:
                _write_runtime_config_file(merged)
        except RuntimeConfigFileError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"ok": True, "runtime_config": merged}

    @app.post("/api/admin/llm_models")
    def api_admin_llm_models(payload: LlmModelsPayload, user: dict[str, Any] = Depends(require_admin)):
        base_url = str(payload.llm_base_url or "").strip()
        api_key = str(payload.llm_api_key or "").strip()
        if not base_url or not api_key:
            with db() as conn:
                runtime = _get_runtime_config(conn)
            base_url = base_url or str(runtime.get("llm_base_url") or "").strip()
            api_key = api_key or str(runtime.get("llm_api_key_gpt") or runtime.get("llm_api_key") or "").strip()
        try:
            models = _fetch_openai_compatible_model_ids(base_url=base_url, api_key=api_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        grok_models = [model for model in models if "grok" in model.lower()]
        return {"ok": True, "models": grok_models or models}

    @app.post("/api/admin/remote_comfy/health")
    def api_admin_remote_comfy_health(payload: RemoteComfyGatewayPayload, user: dict[str, Any] = Depends(require_admin)):
        gateway_url = str(payload.remote_comfy_gateway_url or "").strip()
        token = str(payload.remote_comfy_gateway_token or "").strip()
        if not gateway_url or not token:
            with db() as conn:
                runtime = _get_runtime_config(conn)
            gateway_url = gateway_url or str(runtime.get("remote_comfy_gateway_url") or "").strip()
            token = token or str(runtime.get("remote_comfy_gateway_token") or "").strip()
        try:
            health = _remote_comfy_gateway_health(gateway_url=gateway_url, token=token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "gateway_url": _normalize_remote_comfy_gateway_url(gateway_url), "health": health}

    @app.post("/api/admin/remote_comfy/workflows")
    def api_admin_remote_comfy_workflows(payload: RemoteComfyGatewayPayload, user: dict[str, Any] = Depends(require_admin)):
        gateway_url = str(payload.remote_comfy_gateway_url or "").strip()
        token = str(payload.remote_comfy_gateway_token or "").strip()
        if not gateway_url or not token:
            with db() as conn:
                runtime = _get_runtime_config(conn)
            gateway_url = gateway_url or str(runtime.get("remote_comfy_gateway_url") or "").strip()
            token = token or str(runtime.get("remote_comfy_gateway_token") or "").strip()
        try:
            return _remote_comfy_gateway_json(
                gateway_url=gateway_url,
                token=token,
                method="GET",
                path="/api/workflows",
                timeout=60,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/admin/remote_comfy/run_test")
    def api_admin_remote_comfy_run_test(payload: RemoteComfyWorkflowTestPayload, user: dict[str, Any] = Depends(require_admin)):
        gateway_url = str(payload.remote_comfy_gateway_url or "").strip()
        token = str(payload.remote_comfy_gateway_token or "").strip()
        if not gateway_url or not token:
            with db() as conn:
                runtime = _get_runtime_config(conn)
            gateway_url = gateway_url or str(runtime.get("remote_comfy_gateway_url") or "").strip()
            token = token or str(runtime.get("remote_comfy_gateway_token") or "").strip()
        workflow_path = str(payload.workflow_path or "").strip()
        if not workflow_path:
            raise HTTPException(status_code=400, detail="workflow_path 不能为空")
        try:
            result = _run_remote_comfy_gateway_test(
                gateway_url=gateway_url,
                token=token,
                workflow_path=workflow_path,
                prompt_text=payload.prompt_text,
                negative_prompt=payload.negative_prompt,
                width=payload.width,
                height=payload.height,
                steps=payload.steps,
                batch_size=payload.batch_size,
                timeout_seconds=payload.timeout_seconds,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return result

    @app.post("/api/admin/remote_comfy/convert_workflows")
    def api_admin_remote_comfy_convert_workflows(payload: RemoteComfyConvertPayload, user: dict[str, Any] = Depends(require_admin)):
        gateway_url = str(payload.remote_comfy_gateway_url or "").strip()
        token = str(payload.remote_comfy_gateway_token or "").strip()
        if not gateway_url or not token:
            with db() as conn:
                runtime = _get_runtime_config(conn)
            gateway_url = gateway_url or str(runtime.get("remote_comfy_gateway_url") or "").strip()
            token = token or str(runtime.get("remote_comfy_gateway_token") or "").strip()
        body = {
            "paths": [str(path).strip() for path in payload.paths if str(path).strip()],
            "overwrite": bool(payload.overwrite),
            "force": bool(payload.force),
        }
        try:
            return _remote_comfy_gateway_json(
                gateway_url=gateway_url,
                token=token,
                method="POST",
                path="/api/workflows/convert",
                json_body=body,
                timeout=300,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/admin/tg_settings")
    def api_admin_tg_settings(user: dict[str, Any] = Depends(require_admin)):
        return _load_tg_settings_payload()

    @app.post("/api/admin/tg_trusted_users")
    def api_admin_upsert_tg_trusted_user(payload: TgTrustedUserPayload, user: dict[str, Any] = Depends(require_admin)):
        chat_id = int(payload.chat_id)
        if chat_id <= 0:
            raise HTTPException(status_code=400, detail="TG 用户 ID 必须为正整数")
        label = str(payload.label or "").strip() or f"TG-{chat_id}"
        now = time.time()
        conn = _connect_tg_workbench_db()
        try:
            conn.execute(
                """
                INSERT INTO workspace_members
                (chat_id, label, enabled, notify_busy, notify_available, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    label = excluded.label,
                    enabled = excluded.enabled,
                    notify_busy = excluded.notify_busy,
                    notify_available = excluded.notify_available,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    label,
                    1 if payload.enabled else 0,
                    1 if payload.notify_busy else 0,
                    1 if payload.notify_available else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "tg_settings": _load_tg_settings_payload()}

    @app.post("/api/admin/tg_trusted_users/{chat_id}/toggle")
    def api_admin_toggle_tg_trusted_user(
        chat_id: int,
        payload: TgTrustedUserTogglePayload,
        user: dict[str, Any] = Depends(require_admin),
    ):
        conn = _connect_tg_workbench_db()
        try:
            row = conn.execute("SELECT chat_id FROM workspace_members WHERE chat_id = ?", (int(chat_id),)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="找不到该 TG 用户 ID")
            conn.execute(
                "UPDATE workspace_members SET enabled = ?, updated_at = ? WHERE chat_id = ?",
                (1 if payload.enabled else 0, time.time(), int(chat_id)),
            )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "tg_settings": _load_tg_settings_payload()}

    @app.delete("/api/admin/tg_trusted_users/{chat_id}")
    def api_admin_delete_tg_trusted_user(chat_id: int, user: dict[str, Any] = Depends(require_admin)):
        conn = _connect_tg_workbench_db()
        try:
            conn.execute("DELETE FROM workspace_members WHERE chat_id = ?", (int(chat_id),))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "tg_settings": _load_tg_settings_payload()}

    @app.get("/api/admin/pricing")
    def api_admin_get_pricing(user: dict[str, Any] = Depends(require_admin)):
        with db() as conn:
            pricing = _get_pricing_config(conn)
        return pricing

    @app.put("/api/admin/pricing")
    def api_admin_set_pricing(payload: PricingPayload, user: dict[str, Any] = Depends(require_admin)):
        data = payload.model_dump()
        data["rh_coins_per_10rmb"] = max(_to_int(data.get("rh_coins_per_10rmb"), 2500), 1)
        data["usd_to_rmb"] = max(_to_float(data.get("usd_to_rmb"), 7.2), 0.01)
        data["gemini_input_usd_per_1m"] = max(_to_float(data.get("gemini_input_usd_per_1m"), 4.0), 0.0)
        data["gemini_output_usd_per_1m"] = max(_to_float(data.get("gemini_output_usd_per_1m"), 18.0), 0.0)
        data["nano_usd_per_image"] = max(_to_float(data.get("nano_usd_per_image"), 0.134), 0.0)
        data["allow_negative_balance"] = False
        with db() as conn:
            set_admin_config(conn, "pricing", data, _now_ts())
        return {"ok": True, "pricing": data}

    @app.get("/api/admin/users")
    def api_admin_users(limit: int = 200, user: dict[str, Any] = Depends(require_admin)):
        lim = min(max(int(limit or 200), 1), 1000)
        with db() as conn:
            rows = conn.execute(
                """
                SELECT id, username, is_admin, is_disabled, balance_cents, created_at, updated_at
                FROM users
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        return {"items": [dict(r) for r in rows]}

    @app.post("/api/admin/users")
    def api_admin_create_user(payload: AdminCreateUserPayload, user: dict[str, Any] = Depends(require_admin)):
        username = str(payload.username or "").strip()
        password = str(payload.password or "")
        if not username:
            raise HTTPException(status_code=400, detail="用户名不能为空")
        now = _now_ts()
        try:
            pwd_hash = hash_password(password)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with db() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO users(username, password_hash, is_admin, is_disabled, balance_cents, created_at, updated_at)
                    VALUES (?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        username,
                        pwd_hash,
                        1 if bool(payload.is_admin) else 0,
                        max(int(payload.balance_cents or 0), 0),
                        now,
                        now,
                    ),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    raise HTTPException(status_code=409, detail="用户名已存在") from exc
                raise
            row = conn.execute(
                "SELECT id, username, is_admin, is_disabled, balance_cents, created_at, updated_at FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="创建客户账号失败")
        return {"ok": True, "user": dict(row)}

    @app.delete("/api/admin/users/{target_user_id}")
    def api_admin_delete_user(target_user_id: int, user: dict[str, Any] = Depends(require_admin)):
        target_id = int(target_user_id)
        current_id = int(user.get("id") or 0)
        if target_id == current_id:
            raise HTTPException(status_code=400, detail="不能删除当前登录管理员")
        with db() as conn:
            row = conn.execute("SELECT id, is_admin FROM users WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="客户账号不存在")
            task_rows = conn.execute("SELECT id FROM tasks WHERE user_id = ?", (target_id,)).fetchall()
            task_ids = [str(r["id"]) for r in task_rows]
            conn.execute("DELETE FROM users WHERE id = ?", (target_id,))
        for tid in task_ids:
            _delete_task_artifacts(tid)
        return {"ok": True}

    @app.post("/api/admin/users/{target_user_id}/recharge")
    def api_admin_recharge(
        target_user_id: int,
        payload: RechargePayload,
        user: dict[str, Any] = Depends(require_admin),
    ):
        amount = int(payload.amount_cents)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="分配额度必须为正整数（分）")
        with db() as conn:
            target_row = conn.execute("SELECT * FROM users WHERE id = ?", (int(target_user_id),)).fetchone()
            if target_row is None:
                raise HTTPException(status_code=404, detail="客户账号不存在")
            conn.execute(
                "UPDATE users SET balance_cents = balance_cents + ?, updated_at = ? WHERE id = ?",
                (int(amount), _now_ts(), int(target_user_id)),
            )
            _insert_ledger(
                conn,
                user_id=int(target_user_id),
                typ="recharge",
                amount_cents=int(amount),
                ref_task_id="",
                meta={
                    "note": str(payload.note or ""),
                    "admin_id": int(user.get("id") or 0),
                    "admin_username": str(user.get("username") or ""),
                },
            )
            new_row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (int(target_user_id),)).fetchone()
        return {"ok": True, "balance_cents": int(new_row["balance_cents"] or 0)}

    @app.post("/api/admin/users/{target_user_id}/toggle")
    def api_admin_toggle_user(
        target_user_id: int,
        payload: UserTogglePayload,
        user: dict[str, Any] = Depends(require_admin),
    ):
        with db() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(target_user_id),)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="客户账号不存在")
            conn.execute(
                "UPDATE users SET is_disabled = ?, updated_at = ? WHERE id = ?",
                (1 if payload.is_disabled else 0, _now_ts(), int(target_user_id)),
            )
        return {"ok": True}

    @app.get("/api/admin/tasks")
    def api_admin_tasks(limit: int = 200, user: dict[str, Any] = Depends(require_admin)):
        lim = min(max(int(limit or 200), 1), 1000)
        with db() as conn:
            rows = conn.execute(
                """
                SELECT t.id, t.user_id, u.username, t.type, t.status, t.error, t.runninghub_task_id,
                       t.input_json, t.output_json, t.cost_cents, t.created_at, t.updated_at
                FROM tasks t
                LEFT JOIN users u ON u.id = t.user_id
                ORDER BY t.created_at DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            output_payload = _json_loads(item.get("output_json"), {})
            workflow_meta = _build_workflow_meta(
                task_id=str(item.get("id") or ""),
                task_type=str(item.get("type") or ""),
                input_payload=_json_loads(item.get("input_json"), {}),
                output_payload=output_payload,
                runninghub_task_id=item.get("runninghub_task_id"),
            )
            item["has_download"] = _task_has_download_file(output_payload)
            item.pop("input_json", None)
            item.pop("output_json", None)
            item.update(workflow_meta)
            items.append(item)
        return {"items": items}

    @app.get("/api/admin/tasks/{task_id}/logs")
    def api_admin_task_logs(task_id: str, limit: int = 1000, user: dict[str, Any] = Depends(require_admin)):
        tid = str(task_id or "").strip()
        if not tid:
            raise HTTPException(status_code=400, detail="task_id 不能为空")
        lim = min(max(int(limit or 1000), 1), 5000)
        with db() as conn:
            task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
            if task_row is None:
                raise HTTPException(status_code=404, detail="任务不存在")
        task = dict(task_row)
        with db() as conn:
            events = _load_task_events(conn, task=task, limit=lim)
        return {
            "task": _build_task_detail_payload(task=task, include_logs=False, log_limit=0),
            "items": events,
        }

    @app.get("/api/admin/tasks/{task_id}/logs/export")
    def api_admin_task_logs_export(task_id: str, user: dict[str, Any] = Depends(require_admin)):
        tid = str(task_id or "").strip()
        if not tid:
            raise HTTPException(status_code=400, detail="task_id 不能为空")
        with db() as conn:
            task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
            if task_row is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            user_row = conn.execute("SELECT username FROM users WHERE id = ?", (int(task_row["user_id"] or 0),)).fetchone()
            event_rows = conn.execute(
                """
                SELECT id, kind, message, data_json, created_at
                FROM task_events
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (tid,),
            ).fetchall()
        task = dict(task_row)
        username = str(user_row["username"] or "") if user_row else ""
        task_detail = _build_task_detail_payload(task=task, include_logs=False, log_limit=0)
        events = [_serialize_task_event_record(task=task, event_row=row) for row in event_rows]
        lines = _build_task_logs_export_lines(task_detail=task_detail, username=username, events=events)
        filename = f"task_{tid}_logs.jsonl"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(iter([("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")]), media_type="application/jsonl; charset=utf-8", headers=headers)

    @app.post("/api/admin/tasks/{task_id}/analyze_error")
    def api_admin_task_analyze_error(task_id: str, user: dict[str, Any] = Depends(require_admin)):
        return _run_task_error_analysis_impl(task_id, user)

    @app.delete("/api/admin/tasks/{task_id}")
    def api_admin_delete_task(task_id: str, user: dict[str, Any] = Depends(require_admin)):
        tid = str(task_id or "").strip()
        if not tid:
            raise HTTPException(status_code=400, detail="task_id 不能为空")
        with db() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            status = str(row["status"] or "").strip().lower()
            if status in {"running", "queued"}:
                raise HTTPException(status_code=409, detail="运行中或排队中的任务不能删除")
            conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        _delete_task_artifacts(tid)
        return {"ok": True, "id": tid}

    return app


app = create_app()
