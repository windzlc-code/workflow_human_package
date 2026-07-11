import ast
from pathlib import Path


WEB_BOT_PATH = Path(__file__).parents[1] / "adbfacebook_console" / "core" / "web_bot.py"
BOT_CONSOLE_PATH = Path(__file__).parents[1] / "adbfacebook_console" / "web" / "templates" / "bot_console.html"
PERSONA_DASHBOARD_PATH = Path(__file__).parents[1] / "adbfacebook_console" / "core" / "persona_dashboard.py"
SERVER_PATH = Path(__file__).parents[1] / "server.py"
GENERATOR_PATH = Path(__file__).parents[2] / "tool_r18" / "scripts" / "skills" / "persona-generate-posts-once.ts"
PERSONA_CREATE_PATH = Path(__file__).parents[2] / "tool_r18" / "scripts" / "skills" / "persona-create-once.ts"
SOURCE = WEB_BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function_source(name: str) -> str:
    matches = [
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert matches, f"missing function: {name}"
    return ast.get_source_segment(SOURCE, matches[-1]) or ""


def test_console_uses_8098_proxy_prefix_when_served_under_threads_console() -> None:
    template = BOT_CONSOLE_PATH.read_text(encoding="utf-8")

    assert "window.location.pathname.startsWith('/threads-console')" in template
    assert "'/threads-console/api/web-bot/message'" in template
    assert "fetch(webBotEndpoint" in template


def test_compact_console_reuses_full_client_and_syncs_named_sessions() -> None:
    template = BOT_CONSOLE_PATH.read_text(encoding="utf-8")

    assert "query.get('compact') === '1'" in template
    assert "query.get('entry')" in template
    assert "query.get('session')" in template
    assert "new BroadcastChannel('workflow-web-bot:' + sessionId)" in template
    assert "send({ action: entryAction })" in template


def test_console_broadcasts_persona_revisions_for_live_page_refresh() -> None:
    template = BOT_CONSOLE_PATH.read_text(encoding="utf-8")
    response = _function_source("_response")

    assert '"persona_revision": _persona_revision()' in response
    assert "new BroadcastChannel('workflow-console-data')" in template
    assert "data.persona_revision" in template
    assert "workflow:persona-changed" in template
    assert "if (!personaRevision)" in template
    assert "personaRevision," in template
    assert "activePanelAction," in template
    assert "isPersonaPanelAction(activePanelAction)" in template
    assert "persona_dashboard_module.REMOTE_CACHE" in SOURCE


def test_persona_hot_refresh_runs_real_dashboard_task_and_polls_to_completion() -> None:
    start = _function_source("_dashboard_metrics_refresh_start")
    poll = _function_source("_dashboard_metrics_refresh_poll")
    handle = _function_source("handle")

    assert '"POST",\n            "/api/persona_dashboard/refresh"' in start
    assert 'response["poll"]' in start
    assert '"GET", f"/api/persona_dashboard/refresh/' in poll
    assert 'status in {"queued", "running"}' in poll
    assert 'return _hot_metrics_summary(persona_id, force=True)' in poll
    assert 'return _dashboard_metrics_refresh_start(' in handle
    assert 'return _dashboard_metrics_refresh_start()' in handle
    assert handle.index('if action.startswith("shr_")') < handle.index('if action.startswith("dashboard_metrics_refresh_poll:")')


def test_persona_pages_poll_and_subscribe_to_shared_live_updates() -> None:
    template = BOT_CONSOLE_PATH.read_text(encoding="utf-8")
    dashboard_js = (Path(__file__).parents[1] / "static" / "assets" / "persona-dashboard.js").read_text(encoding="utf-8")
    app_js = (Path(__file__).parents[1] / "static" / "assets" / "app.js").read_text(encoding="utf-8")

    assert "pollSourcePersonaRevision" in template
    assert "setInterval(pollSourcePersonaRevision, 3000)" in template
    assert "window.location.protocol + '//' + window.location.host + '/api/persona_dashboard/revision'" in template
    assert "action.startsWith('shs:')" in template
    assert "workflow-console-data" in dashboard_js
    assert "workflow-console-data" in app_js
    assert 'pdApi("/api/persona_dashboard/revision")' in dashboard_js
    assert 'api("/api/persona_dashboard/revision")' in app_js
    assert "}, 3000);" in dashboard_js
    assert "}, 3000);" in app_js


def test_dashboard_recomputes_immediately_when_archive_file_changes() -> None:
    server = SERVER_PATH.read_text(encoding="utf-8")

    assert "def _persona_dashboard_archive_source_changed(" in server
    build_start = server.index("def _build_persona_dashboard_overview(")
    build_end = server.index("\ndef ", build_start + 5)
    build = server[build_start:build_end]
    assert "_persona_dashboard_archive_source_changed(attached)" in build
    assert "payload = _compute_persona_dashboard_overview()" in build
    assert build.index("_persona_dashboard_archive_source_changed(attached)") < build.index("_persona_dashboard_overview_cache_is_fresh(attached)")
    assert '_start_persona_dashboard_refresh("", trigger="page_view")' not in build


def test_dashboard_automatic_full_refresh_defaults_to_daily() -> None:
    server = SERVER_PATH.read_text(encoding="utf-8")

    assert '"persona_dashboard_refresh_interval_seconds": 86400' in server
    assert 'or "86400"' in server
    assert "def _persona_dashboard_auto_monitor_remaining_seconds(" in server
    assert '"next_refresh_in_seconds": remaining' in server
    assert '@app.get("/api/persona_dashboard/revision")' in server


def test_dashboard_refresh_status_survives_service_restart() -> None:
    server = SERVER_PATH.read_text(encoding="utf-8")

    assert 'return TOOL_R18_RUNTIME_DIR / "persona_dashboard_refresh_tasks.json"' in server
    assert "def _persist_persona_dashboard_refresh_tasks_unlocked(" in server
    assert "def _load_persona_dashboard_refresh_tasks(" in server
    assert '"服务重启，上一轮热点刷新已中断，请重新点击刷新。"' in server
    assert server.count("_persist_persona_dashboard_refresh_tasks_unlocked()") >= 4


def test_dashboard_refresh_deduplicates_active_persona_jobs() -> None:
    server = SERVER_PATH.read_text(encoding="utf-8")
    start = server.index("def _start_persona_dashboard_refresh(")
    end = server.index("\ndef ", start + 5)
    source = server[start:end]

    assert 'str(task.get("archive_id") or "").strip() == clean_archive_id' in source
    assert 'bool(clean_archive_id)' in source
    assert 'in {"queued", "running"}' in source
    assert "return dict(existing)" in source


def test_persona_dashboard_prefers_archive_ids_and_hides_confirmed_device_duplicates() -> None:
    source = PERSONA_DASHBOARD_PATH.read_text(encoding="utf-8")

    assert "def visible_local_personas(" in source
    assert '_source_archive_key(persona.source_archive_id)' in source
    assert 'str(persona.source_archive_id or "").startswith("device:")' in source
    assert '"raw_count": len(raw_local_personas)' in source
    assert 'for candidate in (_source_archive_key(persona.source_archive_id), _match_key(persona.id))' in source
    assert 'if needle == _match_key(row.get("id"))' in source
    assert "from core.persona_dashboard import build_overview, find_persona, visible_local_personas" in SOURCE
    assert "for persona in visible_local_personas()" in SOURCE


def test_web_persona_merge_deduplicates_local_projection_by_source_archive_id() -> None:
    local_row = _function_source("_local_persona_row")
    merge = _function_source("_merge_source_and_local_rows")
    refresh = _function_source("_refresh_persona_overview_cache")

    assert '"source_archive_id": persona.source_archive_id' in local_row
    assert 'str(row.get("source_archive_id")' in merge
    assert 'value.startswith("source:")' in merge
    assert "keys.isdisjoint(seen)" in merge
    assert 'overview.get("personas", [])' in refresh


def test_exact_source_persona_id_is_not_replaced_by_another_persona_on_the_same_pad() -> None:
    resolver = _function_source("_resolve_persona_for_action")

    assert 'exact_source_row = local is None and str(row.get("id")' in resolver
    assert "if not exact_source_row:" in resolver
    assert resolver.index("if not exact_source_row:") < resolver.index("_find_related_real_persona(row, local)")


def test_console_restores_and_caps_persistent_message_history() -> None:
    template = BOT_CONSOLE_PATH.read_text(encoding="utf-8")

    assert "const historyLimit = 100" in template
    assert "indexedDB.open(historyDbName, 1)" in template
    assert "groups.length - historyLimit" in template
    assert "html: log.innerHTML" in template
    assert "state," in template
    assert "pendingSourcePoll," in template
    assert "const restored = await restoreHistory()" in template
    assert "if (!restored) send({ action: entryAction })" in template


def test_source_poll_retries_transient_proxy_errors_without_breaking_the_panel() -> None:
    template = BOT_CONSOLE_PATH.read_text(encoding="utf-8")

    assert "if (isPoll && payload.action)" in template
    assert "scheduleSourcePoll({ action: payload.action, interval_ms: 3000 }, targetGroup)" in template


def test_specific_publish_callbacks_are_routed_before_generic_pub_prefix() -> None:
    handle = _function_source("handle")
    generic = handle.index('if action.startswith("pub_"):')

    for callback in ('if action.startswith("pub_direct:"):', 'if action.startswith("pub_posts:"):', 'if action.startswith("pub_history:"):'):
        assert handle.index(callback) < generic, f"{callback} is shadowed by pub_"


def test_manual_bind_pad_callback_is_routed_before_generic_bindpad_prefix() -> None:
    handle = _function_source("handle")
    manual = handle.index('if action.startswith("bindpad_manual:"):')
    generic = handle.index('if action.startswith("bindpad_") or action.startswith("bindpad:"):')

    assert manual < generic


def test_every_static_button_action_has_a_handler() -> None:
    handle = _function_source("handle")
    assert 'action == "custom_publish_multi_now"' in handle


def test_post_generation_delegates_to_real_tool_r18_workflow() -> None:
    flow = _function_source("_continue_generate_posts")
    words_branch = flow[flow.index('if flow == "genpost_words":'):]

    assert "persona_generate_posts" in words_branch
    assert "_source_submit_task" in words_branch
    assert "_generate_draft_posts(" not in words_branch


def test_persona_create_flow_delegates_keywords_and_creation_to_tg_functions() -> None:
    continue_flow = _function_source("_continue_create_persona")
    keyword_submit = _function_source("_submit_create_persona_keywords")
    finish = _function_source("_finish_create_persona")
    task_detail = _function_source("_source_task_detail")
    script = PERSONA_CREATE_PATH.read_text(encoding="utf-8")

    assert "_derive_keywords(" not in SOURCE
    assert "_submit_create_persona_keywords(name, text)" in continue_flow
    assert '_source_submit_task("persona_create_keywords", params)' in keyword_submit
    assert 'response["poll"]' in keyword_submit
    assert '"flow": "create_persona_keywords_wait"' in keyword_submit
    assert 'response["poll"]' in finish
    assert 'response["replace_panel"] = False' in finish
    assert 'derivePersonaDirectionKeywordsWithCodex' in script
    assert 'derivePersonaSpecFromPromptSelection' in script
    assert 'input.action === "keywords"' in script
    assert '.then(() => process.exit(0))' in script
    assert 'process.exit(1)' in script
    assert 'task_type == "persona_create_keywords"' in task_detail
    assert '"flow": "create_persona_keywords"' in task_detail
    assert 'task_type == "persona_create" and status in {"queued", "running"}' in task_detail
    assert 'task_type == "persona_generate_image" and status in {"queued", "running"}' in task_detail
    assert 'status == "success" and task_type == "persona_generate_image" and preview_image' in task_detail
    assert '"步驟 3/3：請先生成人設圖。後續生成推文配圖會優先使用人設圖鎖定人物長相。"' in task_detail


def test_persona_create_has_one_real_finish_implementation() -> None:
    matches = [
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "_finish_create_persona"
    ]

    assert len(matches) == 1


def test_persona_generation_messages_hide_internal_task_fields() -> None:
    keyword_submit = _function_source("_submit_create_persona_keywords")
    finish = _function_source("_finish_create_persona")
    submit = _function_source("_submit_source_post_task")
    detail = _function_source("_source_task_detail")

    assert 'f"❌ 人設核心關鍵詞提煉失敗\\n\\n{exc}"' not in keyword_submit
    assert 'f"❌ 新建人設任務提交失敗\\n\\n{exc}"' not in finish
    assert '人設 ID：{archive_id}' not in submit
    assert 'status in {"failed", "cancelled"} and task_type == "persona_generate_image"' in detail
    assert 'lines = ["❌ 新建人設失敗，請稍後重試。"]' in detail


def test_server_maps_post_generation_to_persona_workflow_service() -> None:
    server = SERVER_PATH.read_text(encoding="utf-8")
    generator = GENERATOR_PATH.read_text(encoding="utf-8")

    assert '"persona_generate_posts": _run_persona_generate_posts' in server
    assert 'if typ == "persona_generate_posts":' in server
    assert 'action: "generate-posts"' in generator
    assert "runPersonaWorkflow" in generator


def test_workflow_persona_entry_uses_tg_content_type_branches() -> None:
    detail = _function_source("_persona_detail")
    handle = _function_source("handle")

    assert "posts_branch_" in detail
    assert "history_branch_" in detail
    assert "pub_branch_" in detail
    assert handle.index('if action.startswith("posts_branch_"):') < handle.index('if action.startswith("posts_"):')
    assert '_persona_content_type_picker(action[len("history_branch_") :], "history")' in handle
    assert '_persona_content_type_picker(action[len("pub_branch_") :], "publish")' in handle


def test_workflow_persona_group_settings_match_tg_contract() -> None:
    settings = _function_source("_persona_settings")
    workflow_gate = _function_source("_is_workflow_persona_row")
    save_group = _function_source("_save_persona_telegram_group")
    input_handler = _function_source("_continue_state_text")

    assert 'row_has_source_groups' in settings
    assert 'if not row_has_source_groups:' in settings
    assert 'row = _fresh_persona_row(persona_id, local, row)' in settings
    assert 'f"TG免費群：{free_group or \'未綁定\'}"' in settings
    assert 'f"TG付費群：{paid_group or \'未綁定\'}"' in settings
    assert 'f"TG通用群：{free_group or \'未綁定\'}"' in settings
    assert 'setup.get("imageWorkflow")' in workflow_gate
    assert 'row.get("imageWorkflow")' in workflow_gate
    assert '"persona_set_telegram_group"' in save_group
    assert '_source_submit_task("persona_set_telegram_group", params)' in save_group
    assert '_save_persona_telegram_group(persona, text, "free")' in input_handler
    assert '_save_persona_telegram_group(persona, text, "paid")' in input_handler


def test_persona_dashboard_preserves_server_workflow_group_fields() -> None:
    source = PERSONA_DASHBOARD_PATH.read_text(encoding="utf-8")

    assert '"setup": row.get("setup")' in source
    assert '"imageWorkflow": bool(row.get("imageWorkflow"))' in source
    assert 'matched_telegram.get("free_group")' in source
    assert 'matched_telegram.get("paid_group")' in source
    assert '"bound_pad_code": remote_pad_code or persona.pad_code' in source
    assert '"http://workflow-delivery-r18:8098/api/persona_dashboard/overview"' in source


def test_workflow_persona_bound_pad_prefers_tool_r18_source() -> None:
    bound_info = _function_source("_persona_bound_info")

    assert 'source_pad = str(row.get("bound_pad_code")' in bound_info
    assert 'workflow_persona = _is_workflow_persona_row' in bound_info
    assert '(source_pad if workflow_persona else "")' in bound_info


def test_source_post_actions_keep_real_archive_and_post_ids() -> None:
    detail = _function_source("_source_post_detail")
    image = _function_source("_source_post_generate_image")
    publish = _function_source("_source_post_publish_execute")

    assert "pa_pub_{action_key}" in detail
    assert "post_img_regen_{archive_id}_{post_index}" in detail
    assert "source_post_publish:{archive_id}:{post_id}" not in detail
    assert '"persona_generate_post_image"' in image
    assert '"persona_publish_post"' in publish
    assert '"dryRun": False' in publish


def test_persona_image_delegates_to_tg_source_task() -> None:
    image = _function_source("_generate_persona_image_response")

    assert '"persona_generate_image"' in image
    assert "_submit_persona_image_job(" not in image


def test_source_persona_results_return_media_and_keep_tg_callbacks() -> None:
    detail = _function_source("_source_task_detail")

    assert 'result.get("imageUrl")' in detail
    assert "image=message_preview_image" in detail
    assert 'f"source_task_detail:{task_id}"' in detail
    assert 'f"pd_{archive_id}"' in detail
    assert 'f"settings_{archive_id}"' in detail
    assert 'result.get("publishedUrl")' in detail
    assert 'result.get("screenshotUrl")' in detail


def test_persona_and_post_views_render_source_media() -> None:
    view_image = _function_source("_view_persona_image")
    settings = _function_source("_persona_settings")
    post_detail = _function_source("_source_post_detail")

    assert "_persona_reference_image_url(row)" in view_image
    assert "_fresh_persona_row(" in view_image
    assert "_generate_persona_image_response(" not in view_image
    assert "_persona_reference_image_url(row)" in settings
    assert "_fresh_persona_row(" in settings
    assert "image=preview_image" in post_detail
    assert "cards=media_cards" in post_detail


def test_post_generation_reference_gate_uses_source_archive_image() -> None:
    flow = _function_source("_continue_generate_posts")
    resume = _function_source("_continue_no_reference_generate")
    gate = _function_source("_generation_persona_reference")

    assert "_persona_reference_image_url(row)" in gate
    assert "_fresh_persona_row(" in gate
    assert "_is_workflow_persona_row(" in gate
    assert "not has_reference and not is_workflow" in flow
    assert "not has_reference and not is_workflow" in resume
    assert 'draft["source_archive_id"]' in flow


def test_source_archive_id_wins_over_local_projection_id() -> None:
    resolver = _function_source("_tool_r18_archive_id")

    assert resolver.index('get("source_archive_id")') < resolver.index('source_id.startswith("source:")')
    assert resolver.index('source_id.startswith("source:")') < resolver.index("if row_id:")
    assert 'get("source_archive_id")' in resolver


def test_background_persona_refresh_keeps_remote_callback_ids() -> None:
    refresh = _function_source("_refresh_persona_overview_cache")

    assert "build_overview(force_remote=force_remote)" in refresh
    assert "source_rows = _cached_remote_persona_rows()" in refresh
    assert "_merge_source_and_local_rows(source_rows)" in refresh
    assert 'overview.get("personas")' not in refresh


def test_persona_detail_and_post_context_keep_tool_r18_archive_id() -> None:
    detail = _function_source("_persona_detail")
    publish = _function_source("_publish_context")

    assert "persona_id = _tool_r18_archive_id(persona_id, local, row)" in detail
    assert "source_persona_id = _tool_r18_archive_id(persona_id, local, row)" in publish
    assert "return source_persona_id, local, row, name" in publish


def test_source_media_tasks_request_web_polling_until_result_is_ready() -> None:
    submit = _function_source("_submit_source_post_task")
    detail = _function_source("_source_task_detail")

    assert 'response["poll"]' in submit
    assert 'response["poll"]' in detail


def test_post_generation_polls_and_returns_generated_content() -> None:
    flow = _function_source("_continue_generate_posts")
    detail = _function_source("_source_task_detail")
    image_followup = _function_source("_source_generated_post_image_start")

    assert 'f"source_task_poll:{source_task_id}"' in flow
    assert 'result.get("posts")' in detail
    assert "推文生成完成" in detail
    assert 'f"source_genpost_image_start:{task_id}"' in detail
    assert '"persona_generate_post_image"' in image_followup
    assert '"action": "generate_candidates"' in image_followup


def test_web_console_edits_callback_panels_and_polls_in_place() -> None:
    template = BOT_CONSOLE_PATH.read_text(encoding="utf-8")

    assert "activeBotGroup" in template
    assert "replacePanel" in template
    assert "replaceTarget.replaceWith(group)" in template
    assert "btn.closest('.msg-group')" in template
    assert "showUser: false" in template
    assert "isPoll: true" in template
    assert "scheduleSourceFollowup" in template
    assert "isFollowup: true" in template


def test_publish_poll_keeps_progress_and_appends_terminal_messages() -> None:
    poll = _function_source("_source_task_poll")
    detail = _function_source("_source_task_detail")
    template = BOT_CONSOLE_PATH.read_text(encoding="utf-8")

    assert 'return {"messages": []' not in poll
    assert 'response["replace_panel"] = False' in detail
    assert 'response["messages"].append(' in detail
    assert '"📸 發佈驗證截圖' in detail
    assert "data.replace_panel !== false" in template


def test_publish_entry_and_custom_media_route_to_real_source_publish() -> None:
    center = _function_source("_publish_center")
    handle = _function_source("handle")
    custom = _function_source("_custom_publish_execute")
    multi = _function_source("_source_post_multi_publish_execute")
    template = BOT_CONSOLE_PATH.read_text(encoding="utf-8")

    assert "custom_publish_persona:" in center
    assert "stored_publish:" in center
    for route in (
        'action.startswith("custom_publish_persona:")',
        'action.startswith("stored_publish:")',
        'action.startswith("stored_platform:")',
        'action.startswith("stored_pick:")',
        'action.startswith("stored_count:")',
        'action == "custom_publish_publish_now"',
        'action == "custom_publish_multi_now"',
    ):
        assert route in handle
    assert 'state.get("flow") in {"custom_publish_content", "custom_publish_ready"} and (message or media)' in handle
    assert '"persona_publish_post"' in custom
    assert '"customContent"' in custom
    assert '"customMediaUrl"' in custom
    assert '"generateImage"' in custom
    assert '"padCodes"' in multi
    assert "function mediaUploadFlowActive()" in template
    assert "if (mediaUploadFlowActive())" in template
    assert "async function handleMediaFile(file)" in template
    assert 'action == "custom_publish_add_media"' in handle
    assert '"linkTemplateApplied"' in custom
    assert '"idempotencyKey"' in custom
    assert "custom_media_token" in _function_source("_continue_custom_publish")


def test_publish_platforms_links_multi_pad_and_retry_match_tg_contract() -> None:
    start = _function_source("_source_post_publish_start")
    platform = _function_source("_source_post_publish_platform")
    bulk = _function_source("_source_bulk_publish_platform")
    pads = _function_source("_source_post_pad_menu")
    detail = _function_source("_source_task_detail")
    publish_cli = (WEB_BOT_PATH.parents[3] / "tool_r18" / "scripts" / "skills" / "persona-publish-post-once.ts").read_text(encoding="utf-8")

    assert "_allowed_publish_platforms()" in start
    assert "_valid_publish_platform(platform)" in platform
    assert "選擇鏈接模板" in platform
    assert "選擇多智能體手機發布" in bulk
    for callback in ("sppad_all_pages", "sppad_clear_all", "sppad_page:"):
        assert callback in pads
    assert "只重試失敗/未完成項" in detail
    assert "createNodePublishQueueRepository" in publish_cli
    assert "acquirePadLock" in publish_cli
    assert "releasePadLock" in publish_cli
    assert "contentOverrides" in publish_cli
    assert "generateArchivePostImageCandidates" in publish_cli
    assert "input.linkTemplateApplied" in publish_cli
    assert 'input.uiContentType === "paid"' in publish_cli
    assert "legacyPublishCheckpointKey" in publish_cli
    assert 'createHash("sha256")' in publish_cli
    assert publish_cli.index("savePublishCheckpoint(archiveId, checkpointKey") < publish_cli.index("releasePadLock(targetPadCode, lockOwner)")
    assert "recoveredHistoryPostIds" in publish_cli
    assert sum(1 for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name == "_publish_platform") == 1


def test_post_generation_text_steps_match_tg_input_contract() -> None:
    count_prompt = _function_source("_genpost_tg_count_prompt")
    words_prompt = _function_source("_genpost_tg_words_prompt")
    flow = _function_source("_continue_generate_posts")

    assert "1-20" in flow
    assert "10-2000" in flow
    assert "例如：3" in count_prompt
    assert "例如：120" in words_prompt
    assert "genpost_words_20" not in words_prompt


def test_web_generation_uses_tool_r18_memory_and_mode_labels() -> None:
    memory_options = _function_source("_genpost_memory_options")
    hot_filter = _function_source("_is_auto_imported_hot_memory")
    memory_actions = _function_source("_genmem_action")
    continuation = _function_source("_continue_generate_posts")
    ratio_picker = _function_source("_genpost_ratio_picker")
    server = SERVER_PATH.read_text(encoding="utf-8")
    dashboard = PERSONA_DASHBOARD_PATH.read_text(encoding="utf-8")

    assert 'row.get("memory_entries")' in memory_options
    assert '"source": "tool_r18"' in memory_options
    assert "reverse=True" in memory_options
    assert "_is_auto_imported_hot_memory" in memory_options
    assert "輿情熱點素材" in hot_filter
    assert '"memory_entries": persona_memories.get(archive_id, [])' in server
    assert '"memory_entries": row.get("memory_entries")' in dashboard
    assert '"memory_entries": matched.get("memory_entries")' in dashboard
    assert 'memories/delete' in memory_actions
    assert 'memories/add' in continuation
    assert '/api/internal/tg/personas/{archive_id}/memories/delete' in server
    assert '/api/internal/tg/personas/{archive_id}/memories/add' in server
    assert "生成推文+配圖/視頻" in ratio_picker
    assert "未指定 + 配圖" not in ratio_picker


def test_hot_post_auto_reply_keeps_all_tg_steps() -> None:
    handle = _function_source("handle")
    continuation = _function_source("_continue_state_text")

    assert '_own_reply_mode_menu(action[len("persona_autoreply_hot_") :])' in handle
    assert "ownreply_mode_manual_" in handle
    assert "ownreply_mode_ai_" in handle
    assert 'flow == "ownreply_reply_text"' in continuation
    assert 'flow == "ownreply_views"' in continuation
    assert 'flow == "ownreply_days"' in continuation
    assert '"threads_own_post_reply"' in _function_source("_own_reply_submit")


def test_matrix_and_schedule_use_real_tool_r18_posts() -> None:
    matrix = _function_source("_matrix_run")
    schedule = _function_source("_schedule_submit_at")

    assert '"persona_publish_post"' in matrix
    assert "TaskRepo.add_many" not in matrix
    assert '"persona_enqueue_posts"' in schedule
    assert "TaskRepo.add_many" not in schedule


def test_view_posts_uses_real_archive_state_and_tg_layout() -> None:
    listing = _function_source("_publish_posts_list")
    handle = _function_source("handle")

    assert "_source_post_collection(row, source, content_type)" in listing
    assert '"source_post_ids"' in listing
    assert 'f"vp_{start + index}"' in listing
    assert '"⭐ 收藏推文（' in listing
    assert '"🚀 發布推文"' in listing
    assert '"🗑 刪除推文"' in listing
    assert handle.index('if action.startswith("vp_"):') < handle.index('if action.startswith("pa_pp_"):')
    assert "_source_post_view_from_state" in handle


def test_post_and_history_pages_match_telegram_five_item_page_size() -> None:
    assert "STORED_POSTS_PAGE_SIZE = 5" in SOURCE


def test_generate_mode_uses_cached_persona_memories_before_remote_refresh() -> None:
    memory_options = _function_source("_genpost_memory_options")

    assert 'isinstance(row.get("memory_entries"), list)' in memory_options
    assert 'if not has_cached_source_entries:' in memory_options
    assert memory_options.index('if not has_cached_source_entries:') < memory_options.index('_fresh_persona_row(')


def test_source_post_detail_exposes_all_tg_actions() -> None:
    detail = _function_source("_source_post_detail")
    handle = _function_source("handle")

    for callback in ("pa_pub_", "pa_mp_", "pa_mm_", "pa_ed_", "pa_rg_", "pa_rf_", "pa_fav_", "pa_del_"):
        assert callback in detail
    for route in ("pa_mp_", "pa_mm_", "pa_ed_", "pa_rg_", "pa_rai_", "pa_ras_", "pa_rap_", "pa_rc_", "pa_rf_", "pa_fav_", "pa_del_"):
        assert f'action.startswith("{route}")' in handle
    assert '"source"' in detail
    assert '"group_content_type"' in detail
    assert '"post_page"' in detail


def test_source_post_mutations_delegate_to_real_archive_task() -> None:
    submit = _function_source("_source_post_action_submit")
    bulk_delete = _function_source("_source_bulk_delete_execute")
    continuation = _function_source("_continue_state_text")

    assert '"persona_post_action"' in submit
    assert '"delete_many"' in bulk_delete
    assert 'flow == "source_post_custom_content"' in continuation
    assert '"update_content"' in continuation


def test_post_image_flow_generates_candidates_then_persists_selection() -> None:
    ratio = _function_source("_source_post_image_ratio_submit")
    pick = _function_source("_source_post_pick_candidate")
    detail = _function_source("_source_task_detail")

    assert '"action": "generate_candidates"' in ratio
    assert '"imageWidth"' in ratio and '"imageHeight"' in ratio
    assert '"action": "select_candidate"' in pick
    assert 'f"pimgpick:{task_id}:{index}"' in detail
    assert "cards=" in detail


def test_generated_post_image_groups_preserve_progress_and_expose_next_callback() -> None:
    start = _function_source("_source_generated_post_image_start")
    pick = _function_source("_source_post_pick_candidate")
    next_group = _function_source("_source_generated_post_image_next")
    detail = _function_source("_source_task_detail")
    handle = _function_source("handle")

    assert '"uiGeneratedPostIds"' in start
    assert '"uiGeneratedPostIds"' in pick
    assert '"uiPostIndex"' in pick
    assert '"uiGeneratedPostIds"' in next_group
    assert '"uiPostIndex": next_index' in next_group
    assert 'f"source_genpost_image_next:{task_id}"' in detail
    assert 'action.startswith("source_genpost_image_next:")' in handle


def test_content_type_source_and_page_survive_every_return_path() -> None:
    callback = _function_source("_source_post_detail_callback")
    detail = _function_source("_source_post_detail")
    publish = _function_source("_source_post_publish_platform")

    assert '"favorites" if source == "favorites" else "posts"' in callback
    assert 'content_type if content_type in {"free", "paid"} else "all"' in callback
    assert '"post_page": page' in detail
    assert "_source_post_detail_callback" in publish


def test_expired_source_pa_callbacks_never_fall_into_legacy_draft_actions() -> None:
    handle = _function_source("handle")
    source_guard = handle.index('if action.startswith("pa_") and _is_source_post_action_state(state):')

    assert source_guard < handle.index('if action.startswith("pa_img_"):')
    assert '_expired_source_post_action()' in handle[handle.index('if action.startswith("pa_pp_"):'):source_guard]


def test_candidate_retry_encodes_exact_post_identity_not_list_index_only() -> None:
    detail = _function_source("_source_task_detail")
    retry = _function_source("_source_post_image_retry_callback")

    assert "_source_post_image_retry_callback" in detail
    assert 'urllib.parse.quote(str(post_id or ""), safe="")' in retry
    assert '"favorites" if source == "favorites" else "posts"' in retry


def test_hot_fetch_and_import_delegate_to_one_real_tool_r18_task() -> None:
    handle = _function_source("handle")
    submit = _function_source("_sentiment_hot_submit")
    fetch = _function_source("_sentiment_hot_fetch_start")
    import_flow = _function_source("_sentiment_hot_import")

    assert '"persona_sentiment_hot"' in fetch
    assert '"action": "fetch"' in fetch
    assert '"limit": 10' in fetch
    assert '"action": "import"' in import_flow
    assert '"fetchTaskId": fetch_task_id' in import_flow
    assert '"candidateIds"' in import_flow
    assert "_source_submit_task(task_type, params)" in submit
    assert 'replacement["url"] = "[uploaded media]"' in submit
    assert "_record_post_memory(" not in import_flow
    assert "_sentiment_hot_fetch_start(persona_id, content_branch" in handle


def test_hot_candidate_callbacks_keep_snapshot_and_tg_layout() -> None:
    listing = _function_source("_genpost_hot_menu")
    detail = _function_source("_sentiment_hot_detail")
    media = _function_source("_sentiment_hot_media_edit")
    continuation = _function_source("_continue_sentiment_hot_edit")

    assert 'incoming_draft.get("hot_candidates"' in listing
    assert "_genpost_hot_candidates(" not in listing
    for label in ("查看第 {index + 1} 篇", "使用第 {index + 1} 篇", "全选", "清空选择", "保存已选", "刷新抓取", "返回新建推文"):
        assert label in listing
    assert "_sentiment_hot_key_matches" in detail
    assert "_sentiment_hot_media_cards(candidate)" in detail
    for label in ("☑️ 已加入多选", "⬜️ 加入多选", "✅ 使用第", "✏️ 编辑后使用", "全选", "清空选择", "保存已选"):
        assert label in detail
    assert 'action == "shmedia_select_all"' in media
    assert 'action == "shmedia_clear"' in media
    assert 'action == "shmedia_save"' in media
    assert "可以直接发送新文案；也可以发送图片/视频+文案来整体替换媒体。" in media
    assert 'deleted_map[candidate_id] = []' in media
    assert '"replacementMedia"' in _function_source("_sentiment_hot_import")
    assert "_sentiment_hot_import" in continuation


def test_hot_entry_has_one_tg_picker_and_no_fake_global_scope() -> None:
    prompt = _function_source("_genpost_memory_prompt")
    memory_list = _function_source("_genpost_memory_list")
    handle = _function_source("handle")

    for source in (prompt, memory_list):
        assert "gph_x_" not in source
        assert "全局輿情熱點" not in source
        assert "genpost_trending" not in source
    assert "return _genpost_branch_picker" in handle


def test_hot_media_upload_uses_web_composer_and_tg_edit_flow() -> None:
    html = BOT_CONSOLE_PATH.read_text(encoding="utf-8")
    handle = _function_source("handle")
    media_input = _function_source("_sentiment_hot_input_media")
    server = SERVER_PATH.read_text(encoding="utf-8")

    assert 'id="bot-media-input"' in html
    assert "webBotUploadEndpoint" in html
    assert "new FormData()" in html
    assert "uploaded.source_url" in html
    assert "visibleMedia" in html
    assert "document.createElement(media.type === 'video' ? 'video' : 'img')" in html
    assert 'card.video ? `<video' in html
    assert "function mediaUploadFlowActive()" in html
    assert "state.flow === 'replace_persona_image'" in html
    assert "if (mediaUploadFlowActive())" in html
    assert "async function handleMediaFile(file)" in html
    assert "phone.addEventListener(eventName" in html
    assert "event.dataTransfer.files[0]" in html
    assert "send({ action: 'image_menu' }" in html
    assert 'state.get("flow") == "sentiment_hot_edit_input"' in handle
    assert 'state.get("flow") == "replace_persona_image" and media' in handle
    assert "_replace_persona_image_from_media(media, state)" in handle
    assert 'preview_url' in media_input
    assert 'media_type not in {"image", "video"}' in media_input
    assert '@app.post("/threads-console/api/web-bot/upload"' in server
    assert "user: dict[str, Any] = Depends(get_current_user)" in server
    assert 'WEB_BOT_MEDIA_MAX_BYTES = 25 * 1024 * 1024' in server
    assert 'TOOL_R18_UPLOAD_ROOT / "web_bot"' in server
    assert "for stale_path in uploads[200:]" in server


def test_persona_image_upload_rejects_arbitrary_remote_urls() -> None:
    validator = _function_source("_safe_custom_upload_image_url")
    replace = _function_source("_replace_persona_image_from_media")
    downloader = _function_source("_save_persona_image_from_url")

    assert 'path.startswith("/tool_r18_uploads/web_bot/")' in validator
    assert 'allowed_hosts = {"workflow-delivery-r18"' in validator
    assert "_safe_custom_upload_image_url" in replace
    assert "response.read(max_bytes + 1)" in downloader


def test_console_guards_duplicate_and_stale_publish_requests() -> None:
    html = BOT_CONSOLE_PATH.read_text(encoding="utf-8")
    server = SERVER_PATH.read_text(encoding="utf-8")

    assert "requestGeneration" in html
    assert "userRequestInFlight" in html
    assert "generation !== requestGeneration" in html
    assert "button.disabled = true" in html
    assert "_INTERNAL_TG_SUBMIT_LOCK" in server
    assert 'previous.get("idempotencyKey")' in server
    assert '"deduplicated": True' in server


def test_hot_task_result_restores_candidates_media_and_closed_loop_buttons() -> None:
    task_result = _function_source("_sentiment_hot_source_task_response")
    fetch_draft = _function_source("_sentiment_hot_draft_from_fetch")
    restore = _function_source("_sentiment_hot_restore_import_draft")

    assert 'action == "fetch"' in task_result
    assert "_genpost_hot_menu" in task_result
    assert "_sentiment_hot_restore_import_draft" in task_result
    assert "查看推文列表" in task_result
    assert "返回候选列表" in task_result
    assert "继续刷新抓取" in task_result
    assert "返回人设详情" in task_result
    assert 'result.get("candidates")' in fetch_draft
    assert 'result.get("cookieStatuses")' in fetch_draft
    assert 'result.get("warnings")' in fetch_draft
    assert "_source_task_detail_data(fetch_task_id)" in restore
    assert '"hot_edited_contents"' in restore
    assert '"hot_deleted_media_indexes"' in restore


def test_hot_import_is_visible_immediately_and_polls_without_two_second_lag() -> None:
    task_result = _function_source("_sentiment_hot_source_task_response")
    pending_posts = _function_source("_source_pending_posts")
    submit = _function_source("_sentiment_hot_submit")
    adapter = (WEB_BOT_PATH.parents[3] / "tool_r18" / "scripts" / "skills" / "persona-sentiment-hot-once.ts").read_text(encoding="utf-8")

    assert "_remember_recent_imported_posts" in task_result
    assert "_schedule_persona_overview_refresh(force_remote=True)" in task_result
    assert "_sentiment_hot_import_target_page" in task_result
    assert "_recent_imported_posts_for_row" in pending_posts
    assert '750 if str(params.get("action") or "").lower() == "import" else 2000' in submit
    assert "await Promise.all(" in adapter
    assert "prefetchedMedia[index]" in adapter


def test_threads_automation_matches_tg_progress_and_evidence_lifecycle() -> None:
    automation = _function_source("_automation_run")
    own_reply = _function_source("_own_reply_submit")
    detail = _function_source("_source_task_detail")
    warmup_cli = (WEB_BOT_PATH.parents[3] / "tool_r18" / "scripts" / "skills" / "threads-warmup-once.ts").read_text(encoding="utf-8")
    auto_reply_cli = (WEB_BOT_PATH.parents[3] / "tool_r18" / "scripts" / "skills" / "threads-auto-reply-once.ts").read_text(encoding="utf-8")
    own_reply_cli = (WEB_BOT_PATH.parents[3] / "tool_r18" / "scripts" / "skills" / "threads-own-post-reply-once.ts").read_text(encoding="utf-8")

    assert "_source_submit_task" in automation
    assert 'response["poll"]' in automation
    assert "_source_submit_task" in own_reply
    assert 'response["poll"]' in own_reply
    for task_type in ("threads_warmup", "threads_auto_reply", "threads_own_post_reply"):
        assert task_type in detail
    assert "automation_screenshots" in detail
    assert 'response["messages"].append(' in detail
    assert "own_reply_no_match" in detail
    assert "if own_reply_no_match:" in detail
    for source in (warmup_cli, auto_reply_cli, own_reply_cli):
        assert "emitWebTaskProgress" in source
