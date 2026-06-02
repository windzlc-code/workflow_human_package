import unittest

from aiogram.types import ReplyKeyboardMarkup

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

    def test_prompt_failure_keyboard_uses_reply_keyboard(self) -> None:
        markup = bot._text_to_image_prompt_failure_reply_keyboard()

        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        labels = [button.text for row in markup.keyboard for button in row]
        self.assertIn("重新生成提示词", labels)
        self.assertIn("输入自定义提示词", labels)
        self.assertNotIn("使用这个提示词生成", labels)

    def test_video_prompt_failure_keyboard_prioritizes_available_actions(self) -> None:
        markup = bot._video_i2v_prompt_failure_keyboard()

        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        rows = [[button.text for button in row] for row in markup.keyboard]
        self.assertEqual(
            rows,
            [
                ["重新生成提示词"],
                ["输入自定义提示词提交"],
                ["返回参数设置", bot.MAIN_MENU_BUTTON],
            ],
        )
        labels = [label for row in rows for label in row]
        self.assertNotIn("使用这个提示词生成", labels)
        self.assertNotIn("继续让 Grok 调整", labels)

    def test_image_edit_prompt_review_keyboard_requires_prompt_confirmation(self) -> None:
        markup = bot._image_edit_prompt_review_keyboard()

        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        rows = [[button.text for button in row] for row in markup.keyboard]
        self.assertEqual(
            rows,
            [
                ["使用这个提示词提交"],
                ["输入自定义提示词提交"],
                ["继续让 Grok 调整", "重新生成提示词"],
                ["上一步", bot.MAIN_MENU_BUTTON],
            ],
        )
        labels = [label for row in rows for label in row]
        self.assertNotIn("提交单图编辑任务", labels)
        self.assertNotIn("提交图片编辑任务", labels)

    def test_face_swap_prompt_keyboard_offers_natural_swap_button(self) -> None:
        markup = bot._face_swap_prompt_keyboard()

        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        rows = [[button.text for button in row] for row in markup.keyboard]
        self.assertEqual(
            rows,
            [
                ["自然换脸"],
                ["输入自定义换脸要求"],
                ["上一步", bot.MAIN_MENU_BUTTON],
            ],
        )


if __name__ == "__main__":
    unittest.main()
