import base64
from contextlib import contextmanager
import json
import inspect
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from webapp import server
from webapp.db import get_db_path


ROOT = Path(__file__).parents[2]
SERVER_PATH = ROOT / "webapp" / "server.py"
CREATE_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-create-once.ts"
REWRITE_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-rewrite-intro-once.ts"
SET_TELEGRAM_GROUP_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-set-telegram-group-once.ts"
GENERATE_IMAGE_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-generate-image-once.ts"
GENERATE_POST_IMAGE_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-generate-post-image-once.ts"
PUBLISH_POST_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-publish-post-once.ts"
ENQUEUE_POSTS_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-enqueue-posts-once.ts"
PUBLISH_QUEUE_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "publish-queue-worker.ts"
PERSONA_WORKFLOW_SERVICE_PATH = ROOT / "tool_r18" / "src" / "core" / "persona" / "persona-workflow-service.ts"
OWN_POST_REPLY_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "threads-own-post-reply-once.ts"
SET_LINK_ENDING_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-set-link-ending-once.ts"
POST_ACTION_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-post-action-once.ts"
SENTIMENT_HOT_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-sentiment-hot-once.ts"
TELEGRAM_BOT_PATH = ROOT / "tool_r18" / "src" / "telegram-bot.ts"
WEB_TASK_PROGRESS_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "_web-task-progress.ts"


def _run_tool_r18_cli(script_path: Path, payload: dict, runtime_dir: Path) -> dict:
    env = os.environ.copy()
    env["TOOL_R18_RUNTIME_DIR"] = str(runtime_dir)
    completed = subprocess.run(
        ["node", "--import", "tsx", str(script_path), json.dumps(payload, ensure_ascii=False)],
        cwd=ROOT / "tool_r18",
        env=env,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = server._extract_last_json_object(completed.stdout)
    assert isinstance(result, dict), completed.stdout
    return result


def test_persona_internal_tg_runners_are_registered() -> None:
    source = SERVER_PATH.read_text(encoding="utf-8")

    assert '"persona_create": _run_persona_create' in source
    assert '"persona_create_keywords": _run_persona_create_keywords' in source
    assert '"persona_rewrite_intro": _run_persona_rewrite_intro' in source
    assert '"persona_set_telegram_group": _run_persona_set_telegram_group' in source
    assert 'if typ in {"persona_create", "persona_create_keywords"}:' in source
    assert 'if typ == "persona_rewrite_intro":' in source
    assert 'if typ == "persona_set_telegram_group":' in source
    assert 'mode not in {"direct", "replace"}' in source
    assert '"persona_generate_image": _run_persona_generate_image' in source
    assert '"persona_generate_post_image": _run_persona_generate_post_image' in source
    assert '"persona_publish_post": _run_persona_publish_post' in source
    assert '"persona_enqueue_posts": _run_persona_enqueue_posts' in source
    assert '"persona_post_action": _run_persona_post_action' in source
    assert '"persona_sentiment_hot": _run_persona_sentiment_hot' in source
    assert '"threads_own_post_reply": _run_threads_own_post_reply' in source
    assert '"persona_set_link_ending": _run_persona_set_link_ending' in source


def test_auto_reply_link_suffix_survives_internal_task_normalization() -> None:
    comments = server._build_internal_tg_task_payload(
        "task-comments",
        "threads_auto_reply",
        {"padCode": "PAD-1", "replySuffix": "查看更多\nhttps://example.com"},
    )
    hot = server._build_internal_tg_task_payload(
        "task-hot",
        "threads_own_post_reply",
        {
            "archiveId": "archive-1",
            "padCode": "PAD-1",
            "replyMode": "ai",
            "replySuffix": "查看更多\nhttps://example.com",
        },
    )

    assert comments["replySuffix"] == "查看更多\nhttps://example.com"
    assert hot["replySuffix"] == "查看更多\nhttps://example.com"
    assert "replySuffix?: string" in OWN_POST_REPLY_SCRIPT_PATH.read_text(encoding="utf-8")
    assert "updatePersonaArchiveProfile" in SET_LINK_ENDING_SCRIPT_PATH.read_text(encoding="utf-8")


def test_dashboard_setup_preserves_auto_reply_link_presets() -> None:
    setup = {
        "linkEndingPresets": [
            {
                "id": f"preset-{index}",
                "name": f"template {index}",
                "endingText": "read more",
                "linkUrl": f"https://example.com/{index}",
                "enabled": True,
            }
            for index in range(5)
        ],
        "history": [{"secret": "not returned"}],
    }

    compact = server._compact_dashboard_setup(setup)

    assert isinstance(compact["linkEndingPresets"], list)
    assert len(compact["linkEndingPresets"]) == 5
    assert compact["linkEndingPresets"][4]["id"] == "preset-4"
    assert "history" not in compact


def test_auto_reply_suffix_length_is_rejected_before_execution() -> None:
    with pytest.raises(Exception, match="500 character limit"):
        server._build_internal_tg_task_payload(
            "task-comments-long-suffix",
            "threads_auto_reply",
            {"padCode": "PAD-1", "replySuffix": "x" * 501},
        )
    with pytest.raises(Exception, match="500 character limit"):
        server._build_internal_tg_task_payload(
            "task-hot-long-suffix",
            "threads_own_post_reply",
            {
                "archiveId": "archive-1",
                "padCode": "PAD-1",
                "replyMode": "ai",
                "replySuffix": "x" * 501,
            },
        )


def test_own_post_reply_uses_local_dry_run_and_profile_scan_in_production() -> None:
    source = TELEGRAM_BOT_PATH.read_text(encoding="utf-8")
    function_start = source.index("export async function runThreadsOwnPostReplyOnce")
    function_source = source[function_start:]

    assert "collectPublishedThreadsOwnPostReplyTargets(archive, padCode)" in function_source
    assert "resolvePublishedThreadsOwnPostReplyTargets(archive, padCode)" not in function_source
    assert "profileScan:" in function_source
    assert function_source.index("if (dryRun) {") < function_source.index("profileScan:")
    assert "const repliedPostKeys: string[] = [];" in function_source


def test_auto_reply_production_clis_resolve_and_execute(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    archive_path = runtime_dir / "persona_archives.json"
    archive_path.write_text(
        json.dumps(
            [
                {
                    "id": "archive-auto-reply-smoke",
                    "name": "Auto Reply Smoke",
                    "content": "Runtime dependency smoke test persona",
                    "createdAt": "2026-07-12T00:00:00Z",
                    "updatedAt": "2026-07-12T00:00:00Z",
                    "setup": {},
                    "posts": [],
                    "publishHistory": [],
                }
            ]
        ),
        encoding="utf-8",
    )

    own_reply = _run_tool_r18_cli(
        OWN_POST_REPLY_SCRIPT_PATH,
        {
            "archiveId": "archive-auto-reply-smoke",
            "padCode": "PAD-SMOKE",
            "replyMode": "ai",
            "minViews": 999999999,
            "maxAgeDays": 1,
            "dryRun": True,
        },
        runtime_dir,
    )
    link_ending = _run_tool_r18_cli(
        SET_LINK_ENDING_SCRIPT_PATH,
        {
            "archiveId": "archive-auto-reply-smoke",
            "name": "Smoke template",
            "endingText": "Read more",
            "linkUrl": "https://example.com/smoke",
        },
        runtime_dir,
    )

    assert WEB_TASK_PROGRESS_PATH.is_file()
    assert own_reply["ok"] is True
    assert own_reply["dryRun"] is True
    assert link_ending["ok"] is True
    stored = json.loads(archive_path.read_text(encoding="utf-8"))[0]
    assert stored["setup"]["linkEndingPresets"][0]["name"] == "Smoke template"


def test_tool_r18_runner_extracts_final_json_after_console_logs() -> None:
    raw = "\n".join([
        '[telegram-group][vision_send_result] {"status":"sent"}',
        "visual verification completed",
        json.dumps({"ok": True, "screenshotUrl": "https://example.com/evidence.png"}, ensure_ascii=False, indent=2),
    ])

    assert server._extract_last_json_object(raw) == {
        "ok": True,
        "screenshotUrl": "https://example.com/evidence.png",
    }


def test_persona_sentiment_hot_fetch_payload_is_strictly_normalized() -> None:
    payload = server._build_internal_tg_task_payload(
        "task-hot-fetch",
        "persona_sentiment_hot",
        {
            "action": "fetch",
            "archive_id": "archive-1",
            "limit": 99,
            "refresh": True,
            "memorySummaries": [" memory 1 ", "memory 2"],
            "content_branch": "nonr18",
            "uiPersonaId": "web-persona-1",
            "ignored": "secret",
        },
    )

    assert payload == {
        "action": "fetch",
        "archiveId": "archive-1",
        "limit": 10,
        "refresh": True,
        "prompt": "",
        "memorySummaries": ["memory 1", "memory 2"],
        "contentBranch": "nonr18",
        "uiPersonaId": "web-persona-1",
        "uiPersonaName": "",
    }


def test_threads_own_post_reply_keeps_web_persona_metadata() -> None:
    payload = server._build_internal_tg_task_payload(
        "task-own-reply",
        "threads_own_post_reply",
        {
            "archiveId": "workflow-persona-jinjunya",
            "padCode": "ATP64K6RON7LCGMR",
            "replyMode": "manual",
            "replyText": "你好",
            "minViews": 0,
            "maxAgeDays": 1,
            "uiPersonaId": "web-persona-jinjunya",
            "uiPersonaName": "金君雅",
        },
    )

    assert payload["uiPersonaId"] == "web-persona-jinjunya"
    assert payload["uiPersonaName"] == "金君雅"


def test_own_post_reply_cli_reports_semantic_failure_and_progress_screenshots() -> None:
    source = OWN_POST_REPLY_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "isThreadsOwnPostReplyExecutionFailure(result)" in source
    assert "result.replied <= 0 && result.matched > 0" not in source
    assert "replyScreenshots: progress.replyScreenshots" in source
    assert "error: semanticFailure" in source
    assert "if (semanticFailure) process.exitCode = 1" in source


def test_own_post_reply_semantic_failure_distinguishes_execution_errors_from_no_match() -> None:
    code = """
import { isThreadsOwnPostReplyExecutionFailure as failed } from './src/telegram-bot.ts';
console.log(JSON.stringify({
  timeout: failed({ matched: 0, replied: 0, error: 'threadsOwnPostReply open latest profile post timeout' }),
  execution: failed({ matched: 0, replied: 0, error: 'VMOS ADB execution failed' }),
  matchedFailure: failed({ matched: 2, replied: 0, error: 'reply failed' }),
  noMatch: failed({ matched: 0, replied: 0, error: '沒有成功回覆任何推文' }),
  dryRun: failed({ dryRun: true, matched: 0, replied: 0, error: 'no locally recorded Threads post URLs for this PAD' }),
}));
"""
    completed = subprocess.run(
        ["node", "--import", "tsx", "--input-type=module", "-e", code],
        cwd=ROOT / "tool_r18",
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = server._extract_last_json_object(completed.stdout)
    assert result == {
        "timeout": True,
        "execution": True,
        "matchedFailure": True,
        "noMatch": False,
        "dryRun": False,
    }


def test_tg_own_post_reply_shows_execution_error_before_no_match() -> None:
    source = TELEGRAM_BOT_PATH.read_text(encoding="utf-8")
    assert '▶️ 開始執行' in source
    start = source.index("const result = await runThreadsOwnPostReplyOnce(", source.index('if (data.startsWith("acctautoreplyhot_threads_"))'))
    end = source.index("pendingThreadsOwnPostReplyRuns.delete(chatId);", start)
    completion = source[start:end]

    error_guard = completion.index("if (isThreadsOwnPostReplyExecutionFailure(result))")
    no_match_guard = completion.index("if (!result.matched)")
    assert error_guard < no_match_guard
    assert "或已回覆過" not in completion


def test_api_sidecar_can_enable_task_workers_without_other_background_workers() -> None:
    source = inspect.getsource(server.create_app)

    assert 'WEBAPP_TASK_WORKERS_ENABLED' in source
    assert "if task_workers_enabled:" in source
    assert "if background_workers_enabled:" in source


def test_persona_sentiment_hot_import_payload_is_a_thin_candidate_mapping(monkeypatch) -> None:
    source = inspect.getsource(server._sentiment_hot_import_items)
    candidate = {
        "id": "candidate-1",
        "platform": "threads",
        "sourceUrl": "https://www.threads.net/@a/post/1",
        "author": "a",
        "content": "source",
        "media": [
            {"type": "image", "url": "https://cdn.example.com/a.jpg"},
            {"type": "video", "url": "https://cdn.example.com/b.mp4"},
        ],
        "hotScore": 100,
        "metrics": {},
        "capturedAt": "2026-07-10T00:00:00Z",
    }
    resolved = [{"candidate": candidate, "content": "edited", "media": [candidate["media"][1]], "edited": True, "sourceIndex": 0}]
    monkeypatch.setattr(server, "_sentiment_hot_import_items", lambda fetch_task_id, archive_id, payload, **kwargs: resolved)
    payload = server._build_internal_tg_task_payload(
        "task-hot-import",
        "persona_sentiment_hot",
        {
            "action": "import",
            "archiveId": "archive-1",
            "fetchTaskId": "task-hot-fetch",
            "candidateIds": ["candidate-1"],
            "edits": [{"candidateId": "candidate-1", "content": "edited", "keptMediaIndexes": [1]}],
            "contentBranch": "r18",
        },
    )

    assert payload["items"][0]["candidate"]["id"] == "candidate-1"
    assert payload["items"][0]["content"] == "edited"
    assert payload["items"][0]["media"] == [candidate["media"][1]]
    assert payload["items"][0]["edited"] is True
    assert payload["contentBranch"] == "r18"

    assert 'SELECT type, status, input_json, output_json FROM tasks WHERE id = ?' in source
    assert 'output.get("archiveId")' in source
    assert 'candidate_id not in by_id' in source
    assert "_get_tg_chat_id_from_payload(fetch_input) != request_chat_id" in source


def test_sentiment_hot_replacement_media_is_written_inside_persistent_runtime(tmp_path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(server, "_tool_r18_runtime_dir", lambda: runtime_dir)
    encoded = base64.b64encode(b"web-hot-image").decode("ascii")

    result = server._persist_sentiment_hot_replacement_media(
        "task-hot-media",
        {"replacementMedia": {"url": f"data:image/png;base64,{encoded}", "type": "image"}},
    )

    assert result is not None
    path, media_type = result
    assert media_type == "image"
    assert Path(path).is_relative_to(runtime_dir / "sentiment-hot-media")
    assert Path(path).read_bytes() == b"web-hot-image"


def test_sentiment_hot_task_result_maps_local_media_to_browser_url(tmp_path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    media_path = runtime_dir / "sentiment-hot-media" / "candidate.jpg"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"image")
    monkeypatch.setattr(server, "_tool_r18_runtime_dir", lambda: runtime_dir)

    result = server._safe_persona_sentiment_hot_result({
        "ok": True,
        "action": "fetch",
        "candidates": [{
            "id": "candidate-1",
            "media": [{"type": "image", "url": "relative/candidate.jpg", "localPath": str(media_path)}],
        }],
        "posts": [{
            "postId": "post-1",
            "mediaUrl": str(media_path),
            "mediaItems": [{"type": "image", "localPath": str(media_path)}],
        }],
    })

    assert result["candidates"][0]["media"] == [{
        "url": "/persona_media/sentiment-hot-media/candidate.jpg",
        "type": "image",
    }]
    assert result["posts"][0]["mediaUrl"] == "/persona_media/sentiment-hot-media/candidate.jpg"
    assert result["posts"][0]["mediaItems"] == [{
        "url": "/persona_media/sentiment-hot-media/candidate.jpg",
        "type": "image",
    }]


def test_sentiment_hot_import_binds_fetch_owner_and_uploaded_replacement(tmp_path, monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tasks (id TEXT, type TEXT, status TEXT, input_json TEXT, output_json TEXT)")
    candidate = {
        "id": "candidate-1",
        "platform": "threads",
        "content": "source content",
        "media": [{"type": "image", "url": "https://cdn.example.com/source.jpg"}],
    }
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
        (
            "fetch-1",
            "persona_sentiment_hot",
            "success",
            json.dumps({"tg_chat_id": 8080001}),
            json.dumps({"action": "fetch", "archiveId": "archive-1", "candidates": [candidate]}),
        ),
    )

    @contextmanager
    def fake_db():
        yield conn

    monkeypatch.setattr(server, "db", fake_db)
    monkeypatch.setattr(server, "_tool_r18_runtime_dir", lambda: tmp_path / "runtime")
    encoded = base64.b64encode(b"replacement-image").decode("ascii")
    items = server._sentiment_hot_import_items(
        "fetch-1",
        "archive-1",
        {
            "candidateIds": ["candidate-1"],
            "edits": [{
                "candidateId": "candidate-1",
                "content": "edited content",
                "replacementMedia": {"url": f"data:image/png;base64,{encoded}", "type": "image"},
            }],
        },
        task_id="import-1",
        request_chat_id=8080001,
    )

    assert items[0]["content"] == "edited content"
    assert items[0]["overrideMediaType"] == "image"
    assert Path(items[0]["overrideMediaUrl"]).read_bytes() == b"replacement-image"
    with pytest.raises(server.HTTPException) as exc:
        server._sentiment_hot_import_items(
            "fetch-1",
            "archive-1",
            {"candidateIds": ["candidate-1"]},
            request_chat_id=999,
        )
    assert exc.value.status_code == 404
    conn.close()


def test_sentiment_hot_runner_does_not_block_shared_worker_pool() -> None:
    source = inspect.getsource(server._run_persona_sentiment_hot)

    assert "_PERSONA_SENTIMENT_HOT_IMPORT_LOCK" not in source
    assert "with " not in source


def test_default_app_db_uses_the_persistent_webapp_data_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("APP_DB_PATH", raising=False)
    monkeypatch.setenv("WEBAPP_DATA_DIR", str(tmp_path))

    assert Path(get_db_path()) == tmp_path / "app.db"


def test_persona_rewrite_payload_normalizes_direct_and_replace_modes() -> None:
    direct = server._build_internal_tg_task_payload(
        "task-direct",
        "persona_rewrite_intro",
        {"archive_id": "archive-1", "prompt": "new intro"},
    )
    replace = server._build_internal_tg_task_payload(
        "task-replace",
        "persona_rewrite_intro",
        {"archiveId": "archive-2", "direction": "rewrite this", "mode": "replace"},
    )

    assert direct["archiveId"] == "archive-1"
    assert direct["direction"] == "new intro"
    assert direct["mode"] == "direct"
    assert "prompt" not in direct
    assert replace["archiveId"] == "archive-2"
    assert replace["direction"] == "rewrite this"
    assert replace["mode"] == "replace"


def test_persona_telegram_group_payload_is_strictly_normalized() -> None:
    payload = server._build_internal_tg_task_payload(
        "task-group",
        "persona_set_telegram_group",
        {
            "archive_id": "workflow-persona-1",
            "group_content_type": "paid",
            "group_name": " paid group ",
            "ignored": "secret",
        },
    )

    assert payload == {
        "archiveId": "workflow-persona-1",
        "groupName": "paid group",
        "groupContentType": "paid",
    }


def test_persona_scripts_reuse_telegram_business_functions() -> None:
    create_source = CREATE_SCRIPT_PATH.read_text(encoding="utf-8")
    telegram_source = TELEGRAM_BOT_PATH.read_text(encoding="utf-8")
    rewrite_source = REWRITE_SCRIPT_PATH.read_text(encoding="utf-8")
    group_source = SET_TELEGRAM_GROUP_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "derivePersonaSpecFromPromptSelection" in create_source
    assert "createPersonaBySpec" in create_source
    assert "spec.name = personaName" in telegram_source
    assert "personaName," in telegram_source
    assert "customTopic: userPrompt" in telegram_source
    assert "derivePersonaInterestTags" in telegram_source
    assert "options: { maxOutputTokens?: number } = {}" in telegram_source
    assert "runCodexJsonInstruction(instruction, { maxOutputTokens: 2048 })" in telegram_source
    assert "rewritePersonaIntroWithCodex" in rewrite_source
    assert "loadPersonaArchive" in rewrite_source
    assert "updatePersonaArchiveProfile" in rewrite_source
    assert 'mode === "replace"' in rewrite_source
    assert "content: direction" in rewrite_source
    assert "personaDescription: direction" in rewrite_source
    assert "customTopic: direction" in rewrite_source
    assert "installNodePersonaArchiveBridge" in create_source
    assert "installNodePersonaArchiveBridge" in rewrite_source
    assert "updatePersonaArchivePadBinding" in group_source
    assert "telegramFreeGroupName" in group_source
    assert "telegramPaidGroupName" in group_source
    assert 'groupContentType = workflowPersona ? requestedType : "free"' in group_source


def test_persona_post_action_payloads_are_strictly_normalized() -> None:
    regenerate = server._build_internal_tg_task_payload(
        "task-regenerate",
        "persona_post_action",
        {
            "archive_id": "archive-1",
            "post_id": "post-1",
            "action": "regenerate_content",
            "source": "favorites",
            "rewrite_mode": "source_structure",
            "ignored": "secret",
        },
    )
    delete_many = server._build_internal_tg_task_payload(
        "task-delete-many",
        "persona_post_action",
        {"archiveId": "archive-1", "action": "delete_many", "postIds": ["post-1", "post-1", "post-2"]},
    )
    delete_media = server._build_internal_tg_task_payload(
        "task-delete-media",
        "persona_post_action",
        {"archiveId": "archive-1", "postId": "post-1", "action": "delete_media", "selectedIndexes": [2, 0, 2]},
    )

    assert regenerate == {
        "archiveId": "archive-1",
        "postId": "post-1",
        "action": "regenerate_content",
        "postSource": "favorites",
        "rewriteMode": "source_structure",
    }
    assert delete_many == {
        "archiveId": "archive-1",
        "action": "delete_many",
        "postSource": "posts",
        "postIds": ["post-1", "post-2"],
    }
    assert delete_media["selectedIndexes"] == [0, 2]
    with pytest.raises(server.HTTPException):
        server._build_internal_tg_task_payload(
            "task-invalid",
            "persona_post_action",
            {"archiveId": "archive-1", "postId": "post-1", "action": "refresh_metrics", "source": "favorites"},
        )


def test_persona_post_action_result_exposes_only_safe_fields() -> None:
    safe = server._safe_persona_post_action_result({
        "ok": True,
        "archiveId": "archive-1",
        "postId": "post-1",
        "action": "update_content",
        "remaining": 2,
        "favoriteCount": 1,
        "content": "updated",
        "mediaUrls": ["https://cdn.example.com/a.jpg", "C:\\private\\b.jpg"],
        "stdout": "secret process output",
        "input_path": "C:\\private\\input.json",
    })

    assert safe == {
        "ok": True,
        "archiveId": "archive-1",
        "postId": "post-1",
        "action": "update_content",
        "remaining": 2,
        "favoriteCount": 1,
        "content": "updated",
        "mediaUrls": ["https://cdn.example.com/a.jpg"],
    }


def test_internal_tg_task_detail_exposes_only_persona_results() -> None:
    source = SERVER_PATH.read_text(encoding="utf-8")
    detail_source = source[source.index("def api_internal_tg_task_detail"):]
    detail_source = detail_source[:detail_source.index('@app.get("/api/tasks")')]

    assert "else _sanitize_payload(output_payload)" in detail_source
    assert 'safe_persona_result' in detail_source
    assert '"screenshotUrl"' in detail_source
    assert '"persona_generate_image", "persona_generate_post_image", "persona_publish_post"' in detail_source
    assert 'persona_task_type == "persona_post_action"' in detail_source
    assert "safe_persona_post_action_result" in detail_source


def test_persona_media_and_publish_payloads_are_normalized() -> None:
    image = server._build_internal_tg_task_payload(
        "task-image",
        "persona_generate_image",
        {"archive_id": "archive-1", "ignored": "secret"},
    )
    post_image = server._build_internal_tg_task_payload(
        "task-post-image",
        "persona_generate_post_image",
        {"archive_id": "archive-1", "post_id": "post-1"},
    )
    publish = server._build_internal_tg_task_payload(
        "task-publish",
        "persona_publish_post",
        {
            "archive_id": "archive-1",
            "post_id": "post-1",
            "pad_code": "PAD-1",
            "platform": "THREADS",
            "dryRun": True,
        },
    )

    assert image == {"archiveId": "archive-1"}
    assert post_image == {"archiveId": "archive-1", "postId": "post-1"}
    assert publish == {
        "archiveId": "archive-1",
        "postId": "post-1",
        "padCode": "PAD-1",
        "platform": "threads",
    }


def test_post_image_group_progress_survives_internal_task_normalization() -> None:
    generated_post_ids = ["post-1", "post-2"]
    candidates = server._build_internal_tg_task_payload(
        "task-candidates",
        "persona_generate_post_image",
        {
            "archiveId": "archive-1",
            "postId": "post-1",
            "action": "generate_candidates",
            "uiPostIndex": 0,
            "uiGeneratedPostIds": generated_post_ids,
        },
    )
    selected = server._build_internal_tg_task_payload(
        "task-selected",
        "persona_generate_post_image",
        {
            "archiveId": "archive-1",
            "postId": "post-1",
            "action": "select_candidate",
            "imageUrl": "https://cdn.example.com/selected.jpg",
            "uiPostIndex": 0,
            "uiGeneratedPostIds": generated_post_ids,
            "uiImageAspectRatio": "2:3",
            "uiImageWidth": 848,
            "uiImageHeight": 1264,
            "uiImageRatioLabel": "2:3 基礎豎圖",
        },
    )

    assert candidates["uiGeneratedPostIds"] == generated_post_ids
    assert selected["uiGeneratedPostIds"] == generated_post_ids
    assert selected["uiImageAspectRatio"] == "2:3"
    assert selected["uiImageWidth"] == 848
    assert selected["uiImageHeight"] == 1264
    assert selected["uiImageRatioLabel"] == "2:3 基礎豎圖"


def test_persona_publish_runner_is_the_only_layer_forcing_live_publish(monkeypatch) -> None:
    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(server, "_run_tool_r18_skill_task", fake_run)
    server._run_persona_publish_post("task-1", {"dryRun": True})

    assert captured["kwargs"]["force_dry_run_false"] is True
    assert captured["args"][3] == "persona-publish-post-once.ts"


def test_persona_publish_progress_reader_keeps_safe_ordered_events(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(server, "_build_task_workdir", lambda *_args, **_kwargs: tmp_path)
    progress_path = tmp_path / "publish-progress.jsonl"
    progress_path.write_text(
        "\n".join([
            json.dumps({"step": "打开 Threads", "line": "▶️ 打开 Threads", "padCode": "PAD-1", "postIndex": 1, "postCount": 2}),
            "not-json",
            json.dumps({"step": "发布完成", "line": "✅ 发布完成", "done": True, "private": "ignored"}),
        ]),
        encoding="utf-8",
    )

    assert server._read_persona_publish_progress("task-1") == [
        {"step": "打开 Threads", "line": "▶️ 打开 Threads", "padCode": "PAD-1", "postIndex": 1, "postCount": 2},
        {"step": "发布完成", "line": "✅ 发布完成", "done": True},
    ]


def test_threads_automation_progress_reader_and_result_are_safe(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(server, "_build_task_workdir", lambda *_args, **_kwargs: tmp_path)
    (tmp_path / "task-progress.jsonl").write_text(
        json.dumps({
            "step": "正在瀏覽",
            "line": "🌱 正在瀏覽｜已瀏覽 3 條",
            "browsed": 3,
            "liked": 1,
            "private": "ignored",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    assert server._read_tool_r18_task_progress("task-1", "threads_warmup") == [{
        "step": "正在瀏覽",
        "line": "🌱 正在瀏覽｜已瀏覽 3 條",
        "browsed": 3,
        "liked": 1,
    }]
    assert server._safe_threads_automation_result("threads_warmup", {
        "ok": True,
        "result": {
            "step": "養號完成",
            "browsed": 10,
            "liked": 2,
            "commented": 1,
            "likeScreenshots": ["https://cdn.example.com/like.jpg", "C:\\private\\shot.jpg"],
            "commentScreenshots": ["https://cdn.example.com/comment.jpg"],
            "done": True,
        },
        "stdout": "secret",
    }) == {
        "ok": True,
        "step": "養號完成",
        "browsed": 10,
        "liked": 2,
        "commented": 1,
        "likeScreenshots": ["https://cdn.example.com/like.jpg"],
        "commentScreenshots": ["https://cdn.example.com/comment.jpg"],
        "done": True,
    }


def test_persona_publish_payload_accepts_custom_media_and_content_overrides() -> None:
    custom = server._build_internal_tg_task_payload(
        "task-custom",
        "persona_publish_post",
        {
            "archiveId": "archive-1",
            "padCodes": ["PAD-1", "PAD-1", "PAD-2"],
            "platform": "threads",
            "customContent": "custom text",
            "customMediaUrl": "data:image/png;base64,YWJj",
            "generateImage": True,
            "linkTemplateApplied": True,
            "uiContentType": "paid",
        },
    )
    stored = server._build_internal_tg_task_payload(
        "task-stored",
        "persona_publish_post",
        {
            "archiveId": "archive-1",
            "postIds": ["post-1", "post-2"],
            "padCode": "PAD-1",
            "platform": "telegram",
            "contentOverrides": {"post-1": "with link", "unknown": "ignored"},
        },
    )

    assert custom["customContent"] == "custom text"
    staged_media = Path(custom["customMediaUrl"])
    assert staged_media.name == "custom-publish-media.png"
    assert staged_media.read_bytes() == b"abc"
    assert custom["generateImage"] is True
    assert custom["linkTemplateApplied"] is True
    assert custom["uiContentType"] == "paid"
    assert custom["padCodes"] == ["PAD-1", "PAD-2"]
    assert stored["contentOverrides"] == {"post-1": "with link"}

    retry = server._build_internal_tg_task_payload(
        "task-custom-retry",
        "persona_publish_post",
        {
            "archiveId": "archive-1",
            "padCode": "PAD-1",
            "platform": "threads",
            "customContent": "custom text",
            "customMediaUrl": custom["customMediaUrl"],
        },
    )
    assert retry["customMediaUrl"] == custom["customMediaUrl"]


def test_persona_publish_payload_rejects_non_media_custom_urls() -> None:
    with pytest.raises(Exception, match="customMediaUrl must be image/video data or http"):
        server._build_internal_tg_task_payload(
            "task-invalid-media",
            "persona_publish_post",
            {
                "archiveId": "archive-1",
                "padCode": "PAD-1",
                "platform": "threads",
                "customContent": "text",
                "customMediaUrl": "data:text/html;base64,PGgxPmJhZDwvaDE+",
            },
        )


def test_persona_clis_reuse_real_telegram_and_archive_paths() -> None:
    generate_image = GENERATE_IMAGE_SCRIPT_PATH.read_text(encoding="utf-8")
    generate_post_image = GENERATE_POST_IMAGE_SCRIPT_PATH.read_text(encoding="utf-8")
    publish = PUBLISH_POST_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "generateAndPersistPersonaReferenceImage" in generate_image
    assert "loadPersonaArchive" in generate_image
    assert "regenerateArchivePostImage" in generate_post_image
    assert "generateArchivePostImageCandidates" in generate_post_image
    assert "attachSelectedImageCandidateToArchivePost" in generate_post_image
    assert "publishPost" in publish
    assert "getArchivePendingPostsForPlatform" in publish
    assert 'action: "finalize-published"' in publish
    assert 'input.dryRun !== false' in publish
    assert "input.padCodes" in publish
    assert "input.postIds" in publish
    assert "for (const [postIndex, post] of posts.entries())" in publish
    assert "for (const targetPadCode of padCodes)" in publish
    assert "publishedTargets" in publish
    assert "webPublishCheckpoints" in publish
    assert "savePublishCheckpoint" in publish
    assert "clearPublishCheckpoints" in publish

    post_action = POST_ACTION_SCRIPT_PATH.read_text(encoding="utf-8")
    telegram_source = (ROOT / "tool_r18" / "src" / "telegram-bot.ts").read_text(encoding="utf-8")
    assert "regenerateArchivePostContent" in post_action
    assert "refreshStoredPostSentimentMetrics" in post_action
    assert "installNodePersonaArchiveBridge" in post_action
    assert "savePersonaArchive" in post_action
    assert "export async function regenerateArchivePostContent" in telegram_source
    assert "export async function refreshStoredPostSentimentMetrics" in telegram_source


def test_pending_posts_are_whitelisted_and_media_urls_are_safe() -> None:
    pending = server._compact_pending_archive_post({
        "id": "post-1",
        "content": "real pending content",
        "title": "Pending title",
        "orderIndex": 4,
        "telegramGroupContentType": "paid",
        "mediaUrl": "https://cdn.example.com/post.jpg",
        "imageUrls": ["data:image/png;base64,YWJj", "C:\\secret\\image.png"],
        "videoUrl": "file:///tmp/secret.mp4",
        "mediaItems": [
            {"url": "https://cdn.example.com/post.mp4", "api_token": "secret"},
            {"url": "https://user:password@example.com/private.jpg"},
        ],
        "sourceMeta": {
            "mediaItems": [{"url": "https://cdn.example.com/source.webp"}],
            "password": "do-not-expose",
        },
        "imageHistory": [{"imageUrl": "file:///tmp/history.png"}],
        "createdAt": "2026-07-10T00:00:00Z",
        "api_token": "do-not-expose",
    })

    assert pending == {
        "id": "post-1",
        "content": "real pending content",
        "title": "Pending title",
        "orderIndex": 4,
        "telegramGroupContentType": "paid",
        "mediaUrl": "https://cdn.example.com/post.jpg",
        "mediaUrls": [
            "https://cdn.example.com/post.jpg",
            "data:image/png;base64,YWJj",
            "https://cdn.example.com/post.mp4",
            "https://cdn.example.com/source.webp",
        ],
        "mediaItems": [
            {"url": "https://cdn.example.com/post.mp4", "type": "video"},
            {"url": "https://cdn.example.com/source.webp", "type": "image"},
            {"url": "https://cdn.example.com/post.jpg", "type": "image"},
        ],
        "videoUrl": "https://cdn.example.com/post.mp4",
        "sourceMeta": {
            "mediaItems": [{"url": "https://cdn.example.com/source.webp", "type": "image"}],
        },
        "createdAt": "2026-07-10T00:00:00Z",
    }
    assert "pending_posts" in SERVER_PATH.read_text(encoding="utf-8")


def test_runtime_sentiment_media_is_rewritten_to_public_read_only_path() -> None:
    assert server._safe_pending_post_media_url(
        "/data/tool_r18_runtime/sentiment-hot-media/example image.jpg"
    ) == "/persona_media/sentiment-hot-media/example%20image.jpg"


def test_persona_dashboard_overview_includes_real_pending_posts(monkeypatch) -> None:
    archive = {
        "id": "archive-1",
        "name": "Persona",
        "content": "Profile",
        "personaReferenceSheet": "https://cdn.example.com/persona-reference.png",
        "setup": {},
        "posts": [{
            "id": "post-1",
            "content": "pending content",
            "title": "Pending",
            "orderIndex": 2,
            "telegramGroupContentType": "free",
            "mediaUrl": "https://cdn.example.com/pending.jpg",
            "createdAt": "2026-07-10T00:00:00Z",
        }],
        "favoritePosts": [{
            "id": "favorite-1",
            "content": "favorite content",
            "title": "Favorite",
            "orderIndex": 0,
            "mediaItems": [{"url": "https://cdn.example.com/favorite.webp", "type": "image"}],
            "sourceMeta": {"favoriteSourcePostId": "post-1"},
            "createdAt": "2026-07-10T01:00:00Z",
        }],
        "platformPosts": {},
        "publishHistory": [],
        "personaImageLibrary": [],
    }
    monkeypatch.setattr(server, "_read_tool_r18_persona_archives", lambda: ([archive], {"exists": True}))
    monkeypatch.setattr(server, "_read_tool_r18_publish_queue_stats", lambda: {"by_archive": {}, "by_status": {}, "rows": [], "total": 0})
    monkeypatch.setattr(server, "_read_tool_r18_sentiment_hot_stats", lambda: {})
    monkeypatch.setattr(server, "_read_persona_dashboard_deleted_posts", lambda: {})
    monkeypatch.setattr(server, "_load_admin_vmos_pads", lambda: [])
    monkeypatch.setattr(server, "_load_persona_dashboard_settings_payload", lambda: {})
    monkeypatch.setattr(server, "_persona_dashboard_deleted_posts_version", lambda: "")

    overview = server._compute_persona_dashboard_overview()

    assert overview["personas"][0]["reference_image_url"] == "https://cdn.example.com/persona-reference.png"
    assert overview["personas"][0]["pending_posts"] == [{
        "id": "post-1",
        "content": "pending content",
        "title": "Pending",
        "orderIndex": 2,
        "telegramGroupContentType": "free",
        "mediaUrl": "https://cdn.example.com/pending.jpg",
        "mediaUrls": ["https://cdn.example.com/pending.jpg"],
        "mediaItems": [{"url": "https://cdn.example.com/pending.jpg", "type": "image"}],
        "videoUrl": "",
        "sourceMeta": {},
        "createdAt": "2026-07-10T00:00:00Z",
    }]
    assert overview["personas"][0]["favorite_posts"] == [{
        "id": "favorite-1",
        "content": "favorite content",
        "title": "Favorite",
        "orderIndex": 0,
        "telegramGroupContentType": "",
        "mediaUrl": "https://cdn.example.com/favorite.webp",
        "mediaUrls": ["https://cdn.example.com/favorite.webp"],
        "mediaItems": [{"url": "https://cdn.example.com/favorite.webp", "type": "image"}],
        "videoUrl": "",
        "sourceMeta": {"favoriteSourcePostId": "post-1", "mediaItems": []},
        "createdAt": "2026-07-10T01:00:00Z",
    }]


def test_persona_dashboard_reference_image_falls_back_to_latest_library_item(monkeypatch) -> None:
    archive = {
        "id": "archive-2",
        "name": "Persona 2",
        "content": "Profile",
        "setup": {},
        "posts": [],
        "platformPosts": {},
        "publishHistory": [],
        "personaImageLibrary": [
            {"imageUrl": "https://cdn.example.com/latest-reference.webp"},
            {"imageUrl": "file:///tmp/private-reference.png"},
        ],
    }
    monkeypatch.setattr(server, "_read_tool_r18_persona_archives", lambda: ([archive], {"exists": True}))
    monkeypatch.setattr(server, "_read_tool_r18_publish_queue_stats", lambda: {"by_archive": {}, "by_status": {}, "rows": [], "total": 0})
    monkeypatch.setattr(server, "_read_tool_r18_sentiment_hot_stats", lambda: {})
    monkeypatch.setattr(server, "_read_persona_dashboard_deleted_posts", lambda: {})
    monkeypatch.setattr(server, "_load_admin_vmos_pads", lambda: [])
    monkeypatch.setattr(server, "_load_persona_dashboard_settings_payload", lambda: {})
    monkeypatch.setattr(server, "_persona_dashboard_deleted_posts_version", lambda: "")

    overview = server._compute_persona_dashboard_overview()

    assert overview["personas"][0]["reference_image_url"] == "https://cdn.example.com/latest-reference.webp"


def test_persona_skill_tasks_invalidate_dashboard_cache() -> None:
    source = inspect.getsource(server._run_tool_r18_skill_task)
    assert 'startswith("persona_")' in source
    assert "_invalidate_persona_dashboard_overview_cache()" in source


def test_schedule_and_own_post_reply_scripts_use_tool_r18_sources() -> None:
    enqueue = ENQUEUE_POSTS_SCRIPT_PATH.read_text(encoding="utf-8")
    workflow = PERSONA_WORKFLOW_SERVICE_PATH.read_text(encoding="utf-8")
    queue = PUBLISH_QUEUE_SCRIPT_PATH.read_text(encoding="utf-8")
    own_reply = OWN_POST_REPLY_SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'action: "enqueue-posts"' in enqueue
    assert "scheduledAt," in enqueue
    assert "scheduled_at: input.scheduledAt" in workflow
    assert "telegramChatId" in enqueue
    assert "updateTaskStatus" not in enqueue
    assert 'input.action === "reschedule"' in queue
    assert 'input.action === "batch-reschedule" || input.action === "batch-cancel"' in queue
    assert "runThreadsOwnPostReplyOnce" in own_reply
    assert "dryRun: input.dryRun === true" in own_reply


def test_web_schedule_submit_preserves_queue_owner() -> None:
    payload = server._build_internal_tg_task_payload(
        "task-schedule-owner",
        "persona_enqueue_posts",
        {
            "archiveId": "archive-1",
            "postIds": ["post-1"],
            "padCode": "PAD-1",
            "platform": "threads",
            "scheduledAt": "2026-08-01T09:00:00.000Z",
            "_requestTgChatId": 8080001,
            "idempotencyKey": "schedule-request-PAD-1",
        },
    )

    assert payload["telegramChatId"] == "8080001"
    assert payload["idempotencyKey"] == "schedule-request-PAD-1"


def test_publish_queue_api_reader_filters_in_sql(tmp_path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    db_path = runtime_dir / "publish_queue.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE publish_tasks (
            id TEXT, archive_id TEXT, archive_post_id TEXT, pad_code TEXT, platform TEXT,
            caption TEXT, media_url TEXT, status TEXT, attempts INTEGER, last_error TEXT,
            failure_step TEXT, manual_intervention_required INTEGER, scheduled_at TEXT,
            created_at TEXT, started_at TEXT, finished_at TEXT, telegram_chat_id TEXT
        )""")
        conn.executemany(
            "INSERT INTO publish_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("owned", "a1", "p1", "PAD-1", "threads", "owned caption", None, "pending", 0, None, None, 0, "2026-08-01T09:00:00Z", "2026-07-01T09:00:00Z", None, None, "8080001"),
                ("other", "a2", "p2", "PAD-2", "telegram", "other caption", None, "failed", 1, "x", None, 0, "2026-08-01T09:00:00Z", "2026-07-01T09:00:00Z", None, None, "999"),
            ],
        )
    monkeypatch.setattr(server, "TOOL_R18_RUNTIME_DIR", runtime_dir)

    data = server._read_tool_r18_publish_queue_rows(8080001, status="pending", scheduled_only=True)

    assert [row["id"] for row in data["tasks"]] == ["owned"]
    assert data["by_status"] == {"pending": 1}
    assert data["truncated"] is False


def test_persona_enqueue_writes_schedule_and_owner_atomically(tmp_path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "persona_archives.json").write_text(json.dumps([{
        "id": "archive-scheduled",
        "name": "Scheduled Persona",
        "content": "Profile",
        "createdAt": "2026-07-10T00:00:00Z",
        "updatedAt": "2026-07-10T00:00:00Z",
        "setup": {},
        "boundPadCode": "PAD-1",
        "posts": [{
            "id": "post-scheduled",
            "title": "Post",
            "content": "scheduled content",
            "wordCount": 17,
            "orderIndex": 0,
            "createdAt": "2026-07-10T00:00:00Z",
            "updatedAt": "2026-07-10T00:00:00Z",
        }],
        "favoritePosts": [],
    }]), encoding="utf-8")

    result = _run_tool_r18_cli(
        ENQUEUE_POSTS_SCRIPT_PATH,
        {
            "archiveId": "archive-scheduled",
            "postIds": ["post-scheduled"],
            "padCode": "PAD-1",
            "platform": "threads",
            "scheduledAt": "2026-08-01T09:00:00.000Z",
            "telegramChatId": "8080001",
        },
        runtime_dir,
    )
    second = _run_tool_r18_cli(
        ENQUEUE_POSTS_SCRIPT_PATH,
        {
            "archiveId": "archive-scheduled",
            "postIds": ["post-scheduled"],
            "padCode": "PAD-1",
            "platform": "threads",
            "scheduledAt": "2026-08-02T09:00:00.000Z",
            "telegramChatId": "8080001",
        },
        runtime_dir,
    )

    assert len(result["enqueued"]) == 1
    assert len(second["enqueued"]) == 1
    with sqlite3.connect(runtime_dir / "publish_queue.db") as conn:
        rows = conn.execute("SELECT scheduled_at, telegram_chat_id, status FROM publish_tasks ORDER BY scheduled_at").fetchall()
    assert rows == [
        ("2026-08-01T09:00:00.000Z", "8080001", "pending"),
        ("2026-08-02T09:00:00.000Z", "8080001", "pending"),
    ]


def test_persona_enqueue_idempotency_keeps_all_posts_without_duplicates(tmp_path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    now = "2026-07-10T00:00:00Z"
    (runtime_dir / "persona_archives.json").write_text(json.dumps([{
        "id": "archive-idempotent",
        "name": "Idempotent Persona",
        "content": "Profile",
        "createdAt": now,
        "updatedAt": now,
        "setup": {},
        "boundPadCode": "PAD-1",
        "posts": [
            {"id": "post-1", "title": "One", "content": "first", "wordCount": 5, "orderIndex": 0, "createdAt": now, "updatedAt": now},
            {"id": "post-2", "title": "Two", "content": "second", "wordCount": 6, "orderIndex": 1, "createdAt": now, "updatedAt": now},
        ],
        "favoritePosts": [],
    }]), encoding="utf-8")
    payload = {
        "archiveId": "archive-idempotent",
        "postIds": ["post-1", "post-2"],
        "padCode": "PAD-1",
        "platform": "threads",
        "scheduledAt": "2026-08-01T09:00:00.000Z",
        "telegramChatId": "8080001",
        "idempotencyKey": "web-request-1",
    }

    first = _run_tool_r18_cli(ENQUEUE_POSTS_SCRIPT_PATH, payload, runtime_dir)
    repeated = _run_tool_r18_cli(ENQUEUE_POSTS_SCRIPT_PATH, payload, runtime_dir)

    assert [item["taskId"] for item in repeated["enqueued"]] == [item["taskId"] for item in first["enqueued"]]
    with sqlite3.connect(runtime_dir / "publish_queue.db") as conn:
        rows = conn.execute("SELECT archive_post_id, idempotency_key FROM publish_tasks ORDER BY archive_post_id").fetchall()
    assert rows == [
        ("post-1", "web-request-1:post-1:threads:PAD-1"),
        ("post-2", "web-request-1:post-2:threads:PAD-1"),
    ]


def test_publish_queue_worker_reuses_real_queue_for_reschedule_and_cancel(tmp_path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    created = _run_tool_r18_cli(
        PUBLISH_QUEUE_SCRIPT_PATH,
        {
            "action": "enqueue",
            "task": {
                "archive_id": "archive-1",
                "archive_post_id": "post-1",
                "pad_code": "PAD-1",
                "platform": "threads",
                "caption": "scheduled post",
                "scheduled_at": "2026-08-01T09:00:00.000Z",
                "telegram_chat_id": "8080001",
            },
        },
        runtime_dir,
    )
    task_id = created["task"]["id"]

    changed = _run_tool_r18_cli(
        PUBLISH_QUEUE_SCRIPT_PATH,
        {
            "action": "reschedule",
            "taskId": task_id,
            "scheduledAt": "2026-08-02T10:30:00.000Z",
            "telegramChatId": "8080001",
        },
        runtime_dir,
    )
    assert changed["task"]["scheduled_at"] == "2026-08-02T10:30:00.000Z"

    cancelled = _run_tool_r18_cli(
        PUBLISH_QUEUE_SCRIPT_PATH,
        {"action": "cancel", "taskId": task_id, "telegramChatId": "8080001"},
        runtime_dir,
    )
    assert cancelled["task"]["status"] == "cancelled"
    assert cancelled["task"]["last_error"] == "已手动取消"
    retried = _run_tool_r18_cli(
        PUBLISH_QUEUE_SCRIPT_PATH,
        {"action": "batch-retry", "status": "failed", "telegramChatId": "8080001"},
        runtime_dir,
    )
    assert retried["count"] == 0
    assert retried["tasks"] == []
    with sqlite3.connect(runtime_dir / "publish_queue.db") as conn:
        conn.execute("UPDATE publish_tasks SET status = 'publishing' WHERE id = ?", (task_id,))
    env = os.environ.copy()
    env["TOOL_R18_RUNTIME_DIR"] = str(runtime_dir)
    raced = subprocess.run(
        ["node", "--import", "tsx", str(PUBLISH_QUEUE_SCRIPT_PATH), json.dumps({
            "action": "cancel",
            "taskId": task_id,
            "telegramChatId": "8080001",
        })],
        cwd=ROOT / "tool_r18",
        env=env,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    assert raced.returncode != 0
    assert "state changed" in raced.stdout
    with sqlite3.connect(runtime_dir / "publish_queue.db") as conn:
        assert conn.execute("SELECT status FROM publish_tasks WHERE id = ?", (task_id,)).fetchone()[0] == "publishing"


def test_persona_post_action_cli_writes_the_real_archive_store(tmp_path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    archive_path = runtime_dir / "persona_archives.json"
    archive_path.write_text(json.dumps([{
        "id": "archive-1",
        "name": "Persona",
        "content": "Profile",
        "createdAt": "2026-07-10T00:00:00Z",
        "updatedAt": "2026-07-10T00:00:00Z",
        "posts": [{
            "id": "post-1",
            "title": "Post",
            "content": "before",
            "wordCount": 6,
            "orderIndex": 0,
            "createdAt": "2026-07-10T00:00:00Z",
            "updatedAt": "2026-07-10T00:00:00Z",
            "imageUrl": "https://cdn.example.com/a.jpg",
            "mediaUrl": "https://cdn.example.com/a.jpg",
            "mediaItems": [
                {"url": "https://cdn.example.com/a.jpg", "type": "image"},
                {"url": "https://cdn.example.com/b.mp4", "type": "video"},
            ],
        }],
        "favoritePosts": [],
    }]), encoding="utf-8")
    env = os.environ.copy()
    env["TOOL_R18_RUNTIME_DIR"] = str(runtime_dir)
    tool_dir = ROOT / "tool_r18"

    def run_action(payload: dict) -> dict:
        completed = subprocess.run(
            ["node", "--import", "tsx", str(POST_ACTION_SCRIPT_PATH), json.dumps(payload)],
            cwd=tool_dir,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        return json.loads(completed.stdout.strip().splitlines()[-1])

    updated = run_action({
        "archiveId": "archive-1",
        "postId": "post-1",
        "action": "update_content",
        "content": "after",
    })
    assert updated["content"] == "after"
    media = run_action({
        "archiveId": "archive-1",
        "postId": "post-1",
        "action": "delete_media",
        "selectedIndexes": [0],
    })
    assert media["mediaUrls"] == ["https://cdn.example.com/b.mp4"]
    favorite = run_action({
        "archiveId": "archive-1",
        "postId": "post-1",
        "action": "favorite",
    })
    assert favorite["favoriteCount"] == 1
    deleted = run_action({
        "archiveId": "archive-1",
        "action": "delete_many",
        "postIds": ["post-1"],
    })
    assert deleted["remaining"] == 0

    stored = json.loads(archive_path.read_text(encoding="utf-8"))[0]
    assert stored["posts"] == []
    assert stored["favoritePosts"][0]["content"] == "after"
    assert stored["favoritePosts"][0]["mediaItems"] == [{
        "url": "https://cdn.example.com/b.mp4",
        "type": "video",
    }]


def test_persona_sentiment_hot_import_cli_writes_once_to_real_archive(tmp_path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    archive_path = runtime_dir / "persona_archives.json"
    archive_path.write_text(json.dumps([{
        "id": "archive-hot",
        "name": "Hot Persona",
        "content": "Profile",
        "createdAt": "2026-07-10T00:00:00Z",
        "updatedAt": "2026-07-10T00:00:00Z",
        "posts": [],
        "favoritePosts": [],
    }]), encoding="utf-8")
    candidate = {
        "id": "candidate-hot-1",
        "platform": "threads",
        "sourceUrl": "https://www.threads.net/@author/post/example",
        "author": "author",
        "content": "original hot content",
        "media": [{"type": "image", "url": "data:image/png;base64,YWJj"}],
        "hotScore": 12000,
        "metrics": {"view_count": 12000},
        "capturedAt": "2026-07-10T00:00:00Z",
    }
    payload = {
        "action": "import",
        "archiveId": "archive-hot",
        "fetchTaskId": "task-hot-fetch",
        "contentBranch": "nonr18",
        "items": [{"candidate": candidate, "content": "edited hot content", "edited": True}],
    }
    env = os.environ.copy()
    env["TOOL_R18_RUNTIME_DIR"] = str(runtime_dir)

    def run_import() -> dict:
        completed = subprocess.run(
            ["node", "--import", "tsx", str(SENTIMENT_HOT_SCRIPT_PATH), json.dumps(payload)],
            cwd=ROOT / "tool_r18",
            env=env,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        return json.loads(completed.stdout.strip().splitlines()[-1])

    first = run_import()
    second = run_import()
    stored = json.loads(archive_path.read_text(encoding="utf-8"))[0]

    assert first["importedCount"] == 1
    assert first["posts"][0]["postId"]
    assert second["posts"][0]["duplicate"] is True
    assert len(stored["posts"]) == 1
    assert stored["posts"][0]["content"] == "edited hot content"
    assert stored["posts"][0]["telegramGroupContentType"] == "free"
    assert stored["posts"][0]["sourceMeta"]["source"] == "sentiment_hot_import"
    assert stored["posts"][0]["sourceMeta"]["sourceUrl"] == candidate["sourceUrl"]
    assert stored["posts"][0]["mediaItems"] == [{"url": "data:image/png;base64,YWJj", "type": "image"}]

    script = SENTIMENT_HOT_SCRIPT_PATH.read_text(encoding="utf-8")
    telegram = (ROOT / "tool_r18" / "src" / "telegram-bot.ts").read_text(encoding="utf-8")
    assert "appendSentimentHotCandidatePost" in script
    assert "rememberSentimentHotSelected" in script
    assert "rememberSentimentHotImported" in script
    assert "loadSelectablePersonaMemories" in script
    assert "formatSentimentHotCandidateLine" in script
    assert "appendCustomPersonaArchivePost" not in script
    assert "export async function appendSentimentHotCandidatePost" in telegram
