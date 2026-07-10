import ast
from pathlib import Path


WEB_BOT_PATH = Path(__file__).parents[1] / "adbfacebook_console" / "core" / "web_bot.py"
SERVER_PATH = Path(__file__).parents[1] / "server.py"
GENERATOR_PATH = Path(__file__).parents[2] / "tool_r18" / "scripts" / "skills" / "persona-generate-posts-once.ts"
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


def test_source_post_actions_keep_real_archive_and_post_ids() -> None:
    detail = _function_source("_source_post_detail")
    image = _function_source("_source_post_generate_image")
    publish = _function_source("_source_post_publish_execute")

    assert "source_post_publish:{archive_id}:{post_id}" in detail
    assert "source_post_image:{archive_id}:{post_id}" in detail
    assert '"persona_generate_post_image"' in image
    assert '"persona_publish_post"' in publish
    assert '"dryRun": False' in publish


def test_persona_image_delegates_to_tg_source_task() -> None:
    image = _function_source("_generate_persona_image_response")

    assert '"persona_generate_image"' in image
    assert "_submit_persona_image_job(" not in image


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
