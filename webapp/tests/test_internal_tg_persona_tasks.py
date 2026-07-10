import inspect
from pathlib import Path

from webapp import server


ROOT = Path(__file__).parents[2]
SERVER_PATH = ROOT / "webapp" / "server.py"
CREATE_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-create-once.ts"
REWRITE_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-rewrite-intro-once.ts"
GENERATE_IMAGE_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-generate-image-once.ts"
GENERATE_POST_IMAGE_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-generate-post-image-once.ts"
PUBLISH_POST_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-publish-post-once.ts"
ENQUEUE_POSTS_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "persona-enqueue-posts-once.ts"
OWN_POST_REPLY_SCRIPT_PATH = ROOT / "tool_r18" / "scripts" / "skills" / "threads-own-post-reply-once.ts"


def test_persona_internal_tg_runners_are_registered() -> None:
    source = SERVER_PATH.read_text(encoding="utf-8")

    assert '"persona_create": _run_persona_create' in source
    assert '"persona_rewrite_intro": _run_persona_rewrite_intro' in source
    assert 'if typ == "persona_create":' in source
    assert 'if typ == "persona_rewrite_intro":' in source
    assert 'mode not in {"direct", "replace"}' in source
    assert '"persona_generate_image": _run_persona_generate_image' in source
    assert '"persona_generate_post_image": _run_persona_generate_post_image' in source
    assert '"persona_publish_post": _run_persona_publish_post' in source
    assert '"persona_enqueue_posts": _run_persona_enqueue_posts' in source
    assert '"threads_own_post_reply": _run_threads_own_post_reply' in source


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


def test_persona_scripts_reuse_telegram_business_functions() -> None:
    create_source = CREATE_SCRIPT_PATH.read_text(encoding="utf-8")
    rewrite_source = REWRITE_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "derivePersonaSpecWithCodex" in create_source
    assert "createPersonaBySpec" in create_source
    assert "spec.name = name" in create_source
    assert "personaName: name" in create_source
    assert "customTopic: prompt" in create_source
    assert "rewritePersonaIntroWithCodex" in rewrite_source
    assert "loadPersonaArchive" in rewrite_source
    assert "updatePersonaArchiveProfile" in rewrite_source
    assert 'mode === "replace"' in rewrite_source
    assert "content: direction" in rewrite_source
    assert "personaDescription: direction" in rewrite_source
    assert "customTopic: direction" in rewrite_source


def test_internal_tg_task_detail_exposes_only_persona_results() -> None:
    source = SERVER_PATH.read_text(encoding="utf-8")
    detail_source = source[source.index("def api_internal_tg_task_detail"):]
    detail_source = detail_source[:detail_source.index('@app.get("/api/tasks")')]

    assert "else _sanitize_payload(output_payload)" in detail_source
    assert 'safe_persona_result' in detail_source
    assert '"persona_generate_image", "persona_generate_post_image", "persona_publish_post"' in detail_source


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


def test_persona_clis_reuse_real_telegram_and_archive_paths() -> None:
    generate_image = GENERATE_IMAGE_SCRIPT_PATH.read_text(encoding="utf-8")
    generate_post_image = GENERATE_POST_IMAGE_SCRIPT_PATH.read_text(encoding="utf-8")
    publish = PUBLISH_POST_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "generateAndPersistPersonaReferenceImage" in generate_image
    assert "loadPersonaArchive" in generate_image
    assert "regenerateArchivePostImage" in generate_post_image
    assert "publishPost" in publish
    assert "getArchivePendingPostsForPlatform" in publish
    assert 'action: "finalize-published"' in publish
    assert 'input.dryRun !== false' in publish


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
        "videoUrl": "https://cdn.example.com/post.mp4",
        "createdAt": "2026-07-10T00:00:00Z",
    }
    assert "pending_posts" in SERVER_PATH.read_text(encoding="utf-8")


def test_persona_dashboard_overview_includes_real_pending_posts(monkeypatch) -> None:
    archive = {
        "id": "archive-1",
        "name": "Persona",
        "content": "Profile",
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

    assert overview["personas"][0]["pending_posts"] == [{
        "id": "post-1",
        "content": "pending content",
        "title": "Pending",
        "orderIndex": 2,
        "telegramGroupContentType": "free",
        "mediaUrl": "https://cdn.example.com/pending.jpg",
        "mediaUrls": ["https://cdn.example.com/pending.jpg"],
        "videoUrl": "",
        "createdAt": "2026-07-10T00:00:00Z",
    }]


def test_persona_skill_tasks_invalidate_dashboard_cache() -> None:
    source = inspect.getsource(server._run_tool_r18_skill_task)
    assert 'startswith("persona_")' in source
    assert "_invalidate_persona_dashboard_overview_cache()" in source


def test_schedule_and_own_post_reply_scripts_use_tool_r18_sources() -> None:
    enqueue = ENQUEUE_POSTS_SCRIPT_PATH.read_text(encoding="utf-8")
    own_reply = OWN_POST_REPLY_SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'action: "enqueue-posts"' in enqueue
    assert "scheduled_at: scheduledAt" in enqueue
    assert "runThreadsOwnPostReplyOnce" in own_reply
    assert "dryRun: input.dryRun === true" in own_reply
