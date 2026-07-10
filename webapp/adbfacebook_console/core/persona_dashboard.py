"""Persona dashboard aggregation for the local Web console."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from core.runtime_paths import DATA_DIR
from db.repo import AccountRepo, DeviceRepo, Persona, PersonaRepo, TaskRepo

REMOTE_OVERVIEW_URL = os.getenv(
    "PERSONA_DASHBOARD_REMOTE_URL",
    "http://43.167.237.120/api/persona_dashboard/overview",
)
REMOTE_CACHE = DATA_DIR / "persona_dashboard_remote_cache.json"
REMOTE_SAMPLE = DATA_DIR / "remote_persona_dashboard_sample.json"
REMOTE_CACHE_TTL_SECONDS = int(os.getenv("PERSONA_DASHBOARD_REMOTE_CACHE_SECONDS", "180") or 180)
_PAD_CODE_RE = re.compile(r"\b[A-Z]{2,4}\d[A-Z0-9]{6,}\b")
_PLACEHOLDER_PERSONA_NAMES = {
    "",
    "人设",
    "人設",
    "未命名人设",
    "未命名人設",
    "imported persona",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _num(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _clean_persona_name(value: Any, pad_code: str = "") -> str:
    text = str(value or "").strip()
    if pad_code:
        text = text.replace(pad_code, "")
    text = _PAD_CODE_RE.sub("", text)
    text = re.sub(r"[¥￥]+(?:\s*\d+(?:\.\d+)?)?", "", text)
    text = re.sub(r"(?:NT\$|TWD|\$)\s*\d+(?:\.\d+)?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ·-_/|:：()（）")
    return text


def _is_placeholder_persona_name(value: Any, pad_code: str = "") -> bool:
    return _clean_persona_name(value, pad_code).lower() in _PLACEHOLDER_PERSONA_NAMES


def _resolved_persona_name(
    local_name: str,
    *,
    pad_code: str = "",
    remote_match: dict[str, Any] | None = None,
    device_alias: str = "",
    account_username: str = "",
    prefer_remote: bool = False,
) -> str:
    local_clean = _clean_persona_name(local_name, pad_code)

    remote_name = _clean_persona_name((remote_match or {}).get("name"), pad_code)
    if prefer_remote and remote_name and not _is_placeholder_persona_name(remote_name, pad_code):
        return remote_name

    if local_clean and not _is_placeholder_persona_name(local_name, pad_code):
        return local_clean

    if remote_name and not _is_placeholder_persona_name(remote_name, pad_code):
        return remote_name

    alias = _clean_persona_name(device_alias, pad_code)
    if alias and not _is_placeholder_persona_name(alias, pad_code):
        return f"{alias} 工作流人设"

    if account_username:
        return f"{account_username} 工作流人设"
    if pad_code:
        return f"{pad_code} 工作流人设"
    return "未命名工作流人设"


def _is_device_generated_persona(persona: Persona) -> bool:
    return str(persona.source_archive_id or "").startswith("device:")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _decode_response(resp: requests.Response) -> dict[str, Any]:
    raw = resp.content or b"{}"
    for encoding in ("utf-8-sig", "utf-8", resp.encoding or "", "gb18030"):
        if not encoding:
            continue
        try:
            parsed = json.loads(raw.decode(encoding))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            continue
    return {}


def _load_remote_overview(force: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = {
        "url": REMOTE_OVERVIEW_URL,
        "ok": False,
        "source": "",
        "error": "",
        "cached": False,
    }
    if REMOTE_CACHE.exists() and not force:
        age = time.time() - REMOTE_CACHE.stat().st_mtime
        cached = _read_json(REMOTE_CACHE)
        if isinstance(cached, dict) and cached.get("ok") and age <= REMOTE_CACHE_TTL_SECONDS:
            meta.update({"ok": True, "source": "cache", "cached": True})
            return cached, meta
    if REMOTE_OVERVIEW_URL:
        try:
            resp = requests.get(
                REMOTE_OVERVIEW_URL,
                params={"force_refresh": "true"} if force else None,
                timeout=12,
            )
            resp.raise_for_status()
            payload = _decode_response(resp)
            if payload.get("ok"):
                _write_json(REMOTE_CACHE, payload)
                meta.update({"ok": True, "source": "remote"})
                return payload, meta
        except Exception as exc:
            meta["error"] = str(exc)
    cached = _read_json(REMOTE_CACHE)
    if isinstance(cached, dict) and cached.get("ok"):
        meta.update({"ok": True, "source": "stale-cache", "cached": True})
        return cached, meta
    sample = _read_json(REMOTE_SAMPLE)
    if isinstance(sample, dict) and sample.get("ok"):
        meta.update({"ok": True, "source": "sample", "cached": True})
        return sample, meta
    return {}, meta


def _hot_score(hot: dict[str, Any]) -> int:
    return sum(_num(hot.get(key)) for key in ("post_views", "likes", "comments", "shares", "reposts"))


def _normalize_remote_persona(row: dict[str, Any]) -> dict[str, Any]:
    hot = row.get("hot") if isinstance(row.get("hot"), dict) else {}
    normalized_hot = {
        "likes": _num(hot.get("likes")),
        "comments": _num(hot.get("comments")),
        "shares": _num(hot.get("shares")),
        "reposts": _num(hot.get("reposts")),
        "recent_views": _num(hot.get("recent_views")),
        "post_views": _num(hot.get("post_views")),
        "scanned_posts": _num(hot.get("scanned_posts")),
        "view_resolved_posts": _num(hot.get("view_resolved_posts")),
        "view_missing_posts": _num(hot.get("view_missing_posts")),
    }
    normalized_hot["hot_score"] = _hot_score(normalized_hot)
    return {
        "id": str(row.get("id") or "").strip(),
        "name": str(row.get("name") or "未命名人设").strip(),
        "content": str(row.get("content") or ""),
        "reference_image_url": str(row.get("reference_image_url") or row.get("referenceImageUrl") or "").strip(),
        "source": "remote",
        "created_at": row.get("created_at") or row.get("createdAt") or "",
        "updated_at": row.get("updated_at") or row.get("updatedAt") or "",
        "bound_pad_code": str(row.get("bound_pad_code") or row.get("boundPadCode") or "").strip(),
        "bound_pad_name": str(row.get("bound_pad_name") or row.get("boundPadName") or "").strip(),
        "account_username": str((row.get("threads_account") or {}).get("handle") or "").strip(),
        "threads_account": row.get("threads_account") if isinstance(row.get("threads_account"), dict) else {},
        "telegram": row.get("telegram") if isinstance(row.get("telegram"), dict) else {},
        "counts": row.get("counts") if isinstance(row.get("counts"), dict) else {},
        "hot": normalized_hot,
        "hot_platforms": row.get("hot_platforms") if isinstance(row.get("hot_platforms"), list) else [],
        "post_metrics": row.get("post_metrics") if isinstance(row.get("post_metrics"), list) else [],
        "publish_history": row.get("publish_history") if isinstance(row.get("publish_history"), list) else [],
        "pending_posts": row.get("pending_posts") if isinstance(row.get("pending_posts"), list) else [],
        "favorite_posts": row.get("favorite_posts") if isinstance(row.get("favorite_posts"), list) else [],
        "memory_entries": row.get("memory_entries") if isinstance(row.get("memory_entries"), list) else [],
        "warnings": row.get("warnings") if isinstance(row.get("warnings"), list) else [],
        "hot_score_formula": "热度 = 逐帖浏览 + 点赞 + 评论 + 分享 + 转发；不包含账号主页浏览。",
    }


def _empty_hot() -> dict[str, int]:
    return {
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "reposts": 0,
        "recent_views": 0,
        "post_views": 0,
        "hot_score": 0,
        "scanned_posts": 0,
        "view_resolved_posts": 0,
        "view_missing_posts": 0,
    }


def _local_persona_row(persona: Persona, remote_match: dict[str, Any] | None = None) -> dict[str, Any]:
    matched = _normalize_remote_persona(remote_match) if remote_match else {}
    hot = matched.get("hot") if isinstance(matched.get("hot"), dict) else _empty_hot()
    account = AccountRepo.get(persona.account_username) if persona.account_username else None
    device = DeviceRepo.get(persona.pad_code) if persona.pad_code else None
    name = _resolved_persona_name(
        persona.name,
        pad_code=persona.pad_code,
        remote_match=matched,
        device_alias=device.alias if device else "",
        account_username=persona.account_username,
        prefer_remote=_is_placeholder_persona_name(persona.name, persona.pad_code),
    )
    return {
        "id": persona.id,
        "name": name,
        "content": persona.description,
        "source": "local+remote" if remote_match else "local",
        "source_archive_id": str(matched.get("id") or ""),
        "reference_image_url": str(matched.get("reference_image_url") or ""),
        "created_at": datetime.fromtimestamp(persona.created_at).isoformat() if persona.created_at else "",
        "updated_at": datetime.fromtimestamp(persona.updated_at).isoformat() if persona.updated_at else "",
        "bound_pad_code": persona.pad_code,
        "bound_pad_name": device.alias if device and device.alias else persona.pad_code,
        "bound_vmos_account_name": device.vmos_account if device else "",
        "account_username": persona.account_username,
        "threads_account": {
            "handle": persona.account_username or (account.username if account else ""),
            "bound": bool(persona.account_username),
        },
        "telegram": {
            "free_group": persona.tg_free_group_name or persona.tg_free_group_id,
            "paid_group": persona.tg_paid_group_name or persona.tg_paid_group_id,
        },
        "counts": matched.get("counts") if isinstance(matched.get("counts"), dict) else {"posts": 0, "published": 0, "images": 0},
        "hot": hot,
        "hot_platforms": matched.get("hot_platforms") if isinstance(matched.get("hot_platforms"), list) else [],
        "post_metrics": matched.get("post_metrics") if isinstance(matched.get("post_metrics"), list) else [],
        "publish_history": matched.get("publish_history") if isinstance(matched.get("publish_history"), list) else [],
        "pending_posts": matched.get("pending_posts") if isinstance(matched.get("pending_posts"), list) else [],
        "favorite_posts": matched.get("favorite_posts") if isinstance(matched.get("favorite_posts"), list) else [],
        "memory_entries": matched.get("memory_entries") if isinstance(matched.get("memory_entries"), list) else [],
        "warnings": matched.get("warnings") if isinstance(matched.get("warnings"), list) else [],
        "hot_score_formula": "热度 = 逐帖浏览 + 点赞 + 评论 + 分享 + 转发；不包含账号主页浏览。",
    }


def _match_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _name_match_key(value: Any, pad_code: str = "") -> str:
    return _clean_persona_name(value, pad_code).lower()


def _remote_index_keys(row: dict[str, Any]) -> list[str]:
    pad_code = str(row.get("bound_pad_code") or row.get("boundPadCode") or "").strip()
    raw_keys = [
        row.get("id"),
        row.get("name"),
        pad_code,
        row.get("bound_pad_name") or row.get("boundPadName"),
        (row.get("threads_account") or {}).get("handle") if isinstance(row.get("threads_account"), dict) else "",
    ]
    keys: list[str] = []
    for key in raw_keys:
        raw = _match_key(key)
        if raw:
            keys.append(raw)
        clean = _name_match_key(key, pad_code)
        if clean:
            keys.append(clean)
    return keys


def _local_candidate_keys(persona: Persona) -> list[str]:
    device = DeviceRepo.get(persona.pad_code) if persona.pad_code else None
    values = [
        persona.id,
        persona.name,
        device.alias if device else "",
        persona.account_username,
    ]
    if _is_placeholder_persona_name(persona.name, persona.pad_code) or not _name_match_key(persona.name, persona.pad_code):
        values.append(persona.pad_code)
    keys: list[str] = []
    for value in values:
        raw = _match_key(value)
        if raw:
            keys.append(raw)
        clean = _name_match_key(value, persona.pad_code)
        if clean:
            keys.append(clean)
    return keys


def _build_remote_indexes(remote_personas: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    index: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for row in remote_personas:
        if not isinstance(row, dict):
            continue
        for normalized in _remote_index_keys(row):
            if normalized not in index:
                index[normalized] = row
    return index, used


def _find_remote_match(persona: Persona, index: dict[str, dict[str, Any]], used: set[str]) -> dict[str, Any] | None:
    for candidate in _local_candidate_keys(persona):
        row = index.get(candidate)
        if row and str(row.get("id") or id(row)) not in used:
            used.add(str(row.get("id") or id(row)))
            return row
    return None


def _summarize(personas: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "persona_count": len(personas),
        "post_count": 0,
        "published_count": 0,
        "image_count": 0,
        "bound_pad_count": len({p.get("bound_pad_code") for p in personas if p.get("bound_pad_code")}),
        "task_count": len(TaskRepo.list_all(limit=10000)),
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "reposts": 0,
        "recent_views": 0,
        "post_views": 0,
        "hot_score": 0,
        "total_interactions": 0,
    }
    for row in personas:
        counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
        hot = row.get("hot") if isinstance(row.get("hot"), dict) else {}
        summary["post_count"] += _num(counts.get("posts"))
        summary["published_count"] += _num(counts.get("published"))
        summary["image_count"] += _num(counts.get("images"))
        for key in ("likes", "comments", "shares", "reposts", "recent_views", "post_views", "hot_score"):
            summary[key] += _num(hot.get(key))
    summary["total_interactions"] = sum(summary[key] for key in ("likes", "comments", "shares", "reposts"))
    return summary


def build_overview(force_remote: bool = False) -> dict[str, Any]:
    remote_payload, remote_meta = _load_remote_overview(force_remote)
    remote_rows = [
        row for row in remote_payload.get("personas", [])
        if isinstance(row, dict)
    ] if isinstance(remote_payload.get("personas"), list) else []
    remote_index, used_remote = _build_remote_indexes(remote_rows)

    rows: list[dict[str, Any]] = []
    local_personas = PersonaRepo.list_all(limit=10000)
    for persona in local_personas:
        rows.append(_local_persona_row(persona, _find_remote_match(persona, remote_index, used_remote)))

    for remote in remote_rows:
        rid = str(remote.get("id") or id(remote))
        if rid in used_remote:
            continue
        rows.append(_normalize_remote_persona(remote))

    rows.sort(key=lambda item: _num((item.get("hot") or {}).get("hot_score")), reverse=True)
    summary = _summarize(rows)
    return {
        "ok": True,
        "updated_at": _now_iso(),
        "summary": summary,
        "charts": {
            "persona_hot_rank": [
                {"id": row["id"], "name": row["name"], "value": _num((row.get("hot") or {}).get("hot_score"))}
                for row in rows[:12]
            ],
            "engagement_mix": {key: summary[key] for key in ("likes", "comments", "shares", "reposts")},
            "platform_distribution": _platform_distribution(rows),
            "pad_distribution": _pad_distribution(rows),
        },
        "personas": rows,
        "data_sources": {
            "local_personas": {"count": len(local_personas)},
            "remote_persona_dashboard": remote_meta,
        },
        "settings": {
            "remote_url": REMOTE_OVERVIEW_URL,
            "cache_seconds": REMOTE_CACHE_TTL_SECONDS,
        },
    }


def _platform_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        platforms = row.get("hot_platforms") if isinstance(row.get("hot_platforms"), list) else []
        for item in platforms:
            if not isinstance(item, dict):
                continue
            name = str(item.get("platform") or "unknown").strip() or "unknown"
            out[name] = out.get(name, 0) + 1
    return out


def _pad_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        pad = str(row.get("bound_pad_code") or "").strip()
        if pad:
            out[pad] = out.get(pad, 0) + 1
    return out


def metrics_for_personas(personas: list[Persona], overview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = overview.get("personas") if isinstance(overview.get("personas"), list) else []
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in (row.get("id"), row.get("name"), row.get("bound_pad_code"), row.get("account_username")):
            normalized = _match_key(key)
            if normalized:
                by_key[normalized] = row
    result: dict[str, dict[str, Any]] = {}
    for persona in personas:
        for key in (persona.id, persona.name, persona.pad_code, persona.account_username):
            row = by_key.get(_match_key(key))
            if row:
                result[persona.id] = row
                break
        result.setdefault(persona.id, _local_persona_row(persona, None))
    return result


def find_persona(overview: dict[str, Any], persona_id: str) -> dict[str, Any] | None:
    needle = _match_key(persona_id)
    for row in overview.get("personas", []):
        if not isinstance(row, dict):
            continue
        if needle in {_match_key(row.get("id")), _match_key(row.get("name")), _match_key(row.get("bound_pad_code"))}:
            return row
    return None
