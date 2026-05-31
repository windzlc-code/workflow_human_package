import unittest

from src.digital_human_tg_bot import bot


class TextToImageStatusTextTests(unittest.TestCase):
    def test_status_text_omits_unselected_placeholders(self) -> None:
        text = bot._text_to_image_status_text(
            step="1/4 请选择图像比例",
            params={
                "ratio_selected": False,
                "resolution_selected": False,
                "persona_selected": False,
                "prompt_mode_selected": False,
            },
        )

        self.assertEqual(text, "文生图设置\n当前步骤：1/4 请选择图像比例")
        self.assertNotIn("画面比例：", text)
        self.assertNotIn("基础分辨率：", text)
        self.assertNotIn("最终分辨率：", text)
        self.assertNotIn("人设 LoRA：", text)
        self.assertNotIn("提示词方式：", text)

    def test_status_text_expands_only_selected_settings(self) -> None:
        text = bot._text_to_image_status_text(
            step="2/4 请选择最终分辨率",
            params={
                "ratio_selected": True,
                "aspect_ratio": "2:3",
                "note": "基础竖图",
                "width": 640,
                "height": 960,
                "resolution_selected": False,
                "persona_selected": False,
                "prompt_mode_selected": False,
            },
        )

        self.assertEqual(
            text,
            "文生图设置\n当前步骤：2/4 请选择最终分辨率\n画面比例：2:3（基础竖图）\n基础分辨率：640 x 960",
        )
        self.assertNotIn("最终分辨率：", text)
        self.assertNotIn("人设 LoRA：", text)
        self.assertNotIn("提示词方式：", text)


if __name__ == "__main__":
    unittest.main()
