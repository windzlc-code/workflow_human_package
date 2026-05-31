import sys
import types
import unittest
from unittest.mock import patch

bs4 = types.ModuleType("bs4")


class BeautifulSoup:
    def __init__(self, *args, **kwargs):
        pass


bs4.BeautifulSoup = BeautifulSoup
sys.modules.setdefault("bs4", bs4)

from webapp import server


class TgCustomPromptValidationTests(unittest.TestCase):
    def test_custom_text_to_image_prompt_allows_short_chinese(self) -> None:
        payload = {
            "prompt": "站著",
            "prompt_text": "站著",
            "custom_prompt_used": True,
        }

        result = server._ensure_internal_tg_payload_chinese_image_prompt("text_to_image", payload)

        self.assertEqual(result["prompt"], "站著")
        self.assertEqual(result["prompt_text"], "站著")

    def test_custom_text_to_image_prompt_allows_english(self) -> None:
        payload = {
            "prompt": "standing portrait, cinematic light",
            "prompt_text": "standing portrait, cinematic light",
            "custom_prompt_used": True,
        }

        result = server._ensure_internal_tg_payload_chinese_image_prompt("text_to_image", payload)

        self.assertEqual(result["prompt"], "standing portrait, cinematic light")
        self.assertEqual(result["prompt_text"], "standing portrait, cinematic light")


class RemoteComfyImageOutputTests(unittest.TestCase):
    def test_text_to_image_cannot_succeed_without_output_file(self) -> None:
        payload = {
            "prompt": "站立人像",
            "remote_comfy_node_inputs": {},
            "text_to_image_auto_qa_enabled": False,
        }

        with (
            patch.object(server, "_comfy_gateway_from_payload", return_value=("remote", "http://comfy-gateway", "")),
            patch.object(server, "_remote_comfy_workflow_mapping", return_value="ZIT_final_output.api.json"),
            patch.object(server, "_run_remote_comfy_gateway_test", return_value={"ok": True, "prompt_id": "p1", "outputs": [], "local_outputs": []}),
            patch.object(server, "_emit_stage"),
        ):
            with self.assertRaisesRegex(RuntimeError, "未返回可下载图片"):
                server._run_remote_comfy_mapped_task("task_test", payload, "text_to_image")


if __name__ == "__main__":
    unittest.main()
