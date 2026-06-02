import unittest

from aiogram.types import ReplyKeyboardMarkup

from src.digital_human_tg_bot import bot


class TextToImageStatusTextTests(unittest.TestCase):
    def test_status_text_omits_unselected_placeholders(self) -> None:
        text = bot._text_to_image_status_text(
            step="1/4 請選擇圖像比例",
            params={
                "ratio_selected": False,
                "resolution_selected": False,
                "persona_selected": False,
                "prompt_mode_selected": False,
            },
        )

        self.assertEqual(text, "文生圖設置\n當前步驟：1/4 請選擇圖像比例")
        self.assertNotIn("畫面比例：", text)
        self.assertNotIn("基礎分辨率：", text)
        self.assertNotIn("最終分辨率：", text)
        self.assertNotIn("人設 LoRA：", text)
        self.assertNotIn("提示詞方式：", text)

    def test_status_text_expands_only_selected_settings(self) -> None:
        text = bot._text_to_image_status_text(
            step="2/4 請選擇最終分辨率",
            params={
                "ratio_selected": True,
                "aspect_ratio": "2:3",
                "note": "基礎豎圖",
                "width": 640,
                "height": 960,
                "resolution_selected": False,
                "persona_selected": False,
                "prompt_mode_selected": False,
            },
        )

        self.assertEqual(
            text,
            "文生圖設置\n當前步驟：2/4 請選擇最終分辨率\n畫面比例：2:3（基礎豎圖）\n基礎分辨率：640 x 960",
        )
        self.assertNotIn("最終分辨率：", text)
        self.assertNotIn("人設 LoRA：", text)
        self.assertNotIn("提示詞方式：", text)

    def test_prompt_failure_keyboard_uses_reply_keyboard(self) -> None:
        markup = bot._text_to_image_prompt_failure_reply_keyboard()

        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        labels = [button.text for row in markup.keyboard for button in row]
        self.assertIn("重新生成提示詞", labels)
        self.assertIn("輸入自定義提示詞", labels)
        self.assertNotIn("使用這個提示詞生成", labels)

    def test_video_prompt_failure_keyboard_prioritizes_available_actions(self) -> None:
        markup = bot._video_i2v_prompt_failure_keyboard()

        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        rows = [[button.text for button in row] for row in markup.keyboard]
        self.assertEqual(
            rows,
            [
                ["重新生成提示詞"],
                ["輸入自定義提示詞提交"],
                ["返回參數設定", bot.MAIN_MENU_BUTTON],
            ],
        )
        labels = [label for row in rows for label in row]
        self.assertNotIn("使用這個提示詞生成", labels)
        self.assertNotIn("繼續讓 Grok 調整", labels)

    def test_image_edit_prompt_review_keyboard_uses_static_traditional_text(self) -> None:
        markup = bot._image_edit_prompt_review_keyboard()

        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        rows = [[button.text for button in row] for row in markup.keyboard]
        self.assertEqual(
            rows,
            [
                ["使用這個提示詞提交"],
                ["輸入自定義提示詞提交"],
                ["繼續讓 Grok 調整", "重新生成提示詞"],
                ["上一步", bot.MAIN_MENU_BUTTON],
            ],
        )
        labels = [label for row in rows for label in row]
        self.assertNotIn("提交單圖編輯任務", labels)
        self.assertNotIn("提交圖片編輯任務", labels)

    def test_face_swap_prompt_keyboard_offers_natural_swap_button(self) -> None:
        markup = bot._face_swap_prompt_keyboard()

        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        rows = [[button.text for button in row] for row in markup.keyboard]
        self.assertEqual(
            rows,
            [
                ["自然換臉"],
                ["輸入自定義換臉要求"],
                ["上一步", bot.MAIN_MENU_BUTTON],
            ],
        )

    def test_simplified_button_aliases_are_still_accepted(self) -> None:
        self.assertEqual(bot._canonical_button_text("图片编辑"), bot.IMAGE_EDIT_BUTTON)
        self.assertEqual(bot._canonical_button_text("返回主菜单"), bot.MAIN_MENU_BUTTON)
        self.assertEqual(bot._canonical_button_text("图生视频"), bot.VIDEO_GENERAL_EDIT_BUTTON)
        self.assertEqual(bot._canonical_button_text("人物换脸"), bot.FACE_SWAP_BUTTON)


if __name__ == "__main__":
    unittest.main()
