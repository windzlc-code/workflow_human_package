import ast
from pathlib import Path


WEB_BOT_PATH = Path(__file__).parents[1] / "adbfacebook_console" / "core" / "web_bot.py"


def test_main_keyboard_matches_telegram_bot() -> None:
    tree = ast.parse(WEB_BOT_PATH.read_text(encoding="utf-8"))
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_main_keyboard"
    ]

    assert len(definitions) == 1, "duplicate definitions silently override the TG-aligned menu"

    calls = [
        node
        for node in ast.walk(definitions[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_btn"
    ]
    buttons = [(ast.literal_eval(call.args[0]), ast.literal_eval(call.args[1])) for call in calls]

    assert buttons == [
        ("👤 我的人設", "list_personas"),
        ("📊 排程狀態", "menu_status"),
        ("⏰ 定時任務", "schedule_publish"),
        ("📱 智能體手機管理", "pad_mgmt"),
        ("🛑 強制中止目前任務", "force_stop_current_task"),
    ]


def test_web_bot_ui_does_not_rewrite_telegram_wording() -> None:
    tree = ast.parse(WEB_BOT_PATH.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {"_btn", "_message"}
    }

    for name in ("_btn", "_message"):
        rewritten_calls = [
            node
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "to_traditional"
        ]
        assert not rewritten_calls, f"{name} must preserve the exact Telegram Bot wording"
