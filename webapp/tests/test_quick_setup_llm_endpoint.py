from pathlib import Path


WEBAPP_ROOT = Path(__file__).parents[1]
QUICK_SETUP_JS = WEBAPP_ROOT / "static" / "assets" / "quick-setup.js"
QUICK_SETUP_HTML = WEBAPP_ROOT / "static" / "quick-setup.html"


def test_quick_setup_defaults_to_runninghub_cn_llm_endpoint() -> None:
    script = QUICK_SETUP_JS.read_text(encoding="utf-8")
    markup = QUICK_SETUP_HTML.read_text(encoding="utf-8")

    assert 'const DEFAULT_LLM_BASE_URL = "https://llm.runninghub.cn/v1";' in script
    assert script.count("DEFAULT_LLM_BASE_URL") == 3
    assert "llm.runninghub.ai" not in script
    assert 'placeholder="https://llm.runninghub.cn/v1"' in markup
