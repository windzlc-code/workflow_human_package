import sys
import types
import unittest

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


if __name__ == "__main__":
    unittest.main()
