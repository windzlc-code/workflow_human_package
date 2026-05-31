import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webapp import db as db_module
import webapp.server as server


class RuntimeConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self._old_db_path = os.environ.get("APP_DB_PATH")
        self._old_runtime_config_path = os.environ.get("APP_RUNTIME_CONFIG_PATH")
        self._old_server_runtime_config_path = server.RUNTIME_CONFIG_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "app.db")
        self.runtime_config_path = Path(self._tmpdir.name) / "runtime_config.json"
        os.environ["APP_DB_PATH"] = self.db_path
        os.environ["APP_RUNTIME_CONFIG_PATH"] = str(self.runtime_config_path)
        server.RUNTIME_CONFIG_PATH = self.runtime_config_path
        db_module.init_db()
        with db_module.db() as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, is_admin, balance_cents, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (1, "admin", "test-hash", 1, 0, server._now_ts(), server._now_ts()),
            )
        self.admin_user = {"id": 1, "username": "admin", "is_admin": 1}

    def tearDown(self):
        server.RUNTIME_CONFIG_PATH = self._old_server_runtime_config_path
        if self._old_db_path is None:
            os.environ.pop("APP_DB_PATH", None)
        else:
            os.environ["APP_DB_PATH"] = self._old_db_path
        if self._old_runtime_config_path is None:
            os.environ.pop("APP_RUNTIME_CONFIG_PATH", None)
        else:
            os.environ["APP_RUNTIME_CONFIG_PATH"] = self._old_runtime_config_path
        self._tmpdir.cleanup()

    def _route_endpoint(self, path: str, method: str):
        for route in server.app.router.routes:
            if getattr(route, "path", None) != path:
                continue
            methods = {m.upper() for m in getattr(route, "methods", set())}
            if method.upper() in methods:
                return route.endpoint
        raise AssertionError(f"route not found: {method} {path}")

    def test_initialize_runtime_config_file_migrates_legacy_db_value(self):
        with db_module.db() as conn:
            db_module.set_admin_config(
                conn,
                "runtime_config",
                {
                    "replace_model_app_id": "legacy_app_123",
                    "nano_host": "example.internal",
                },
                server._now_ts(),
            )

        server._ensure_default_runtime_config()

        stored = json.loads(self.runtime_config_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["replace_model_app_id"], "legacy_app_123")
        self.assertEqual(stored["nano_host"], "example.internal")
        self.assertEqual(stored["cleanup_enabled"], True)
        self.assertEqual(stored["runninghub_api_key"], "")

    def test_initialize_runtime_config_file_recovers_broken_file(self):
        self.runtime_config_path.write_text("{broken json", encoding="utf-8")
        with db_module.db() as conn:
            db_module.set_admin_config(
                conn,
                "runtime_config",
                {"replace_model_app_id": "restored_from_db"},
                server._now_ts(),
            )

        server._ensure_default_runtime_config()

        stored = json.loads(self.runtime_config_path.read_text(encoding="utf-8"))
        backups = list(self.runtime_config_path.parent.glob("runtime_config.broken-*.json"))
        self.assertEqual(stored["replace_model_app_id"], "restored_from_db")
        self.assertTrue(backups)

    def test_get_runtime_config_raises_on_broken_file(self):
        self.runtime_config_path.write_text("{broken json", encoding="utf-8")

        with db_module.db() as conn:
            with self.assertRaises(server.RuntimeConfigFileError):
                server._get_runtime_config(conn)

    def test_get_runtime_config_fills_missing_fields_without_overwriting_explicit_values(self):
        self.runtime_config_path.write_text(
            json.dumps(
                {
                    "replace_model_app_id": "custom_app_456",
                    "cleanup_enabled": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with db_module.db() as conn:
            runtime = server._get_runtime_config(conn)

        stored = json.loads(self.runtime_config_path.read_text(encoding="utf-8"))
        self.assertEqual(runtime["replace_model_app_id"], "custom_app_456")
        self.assertEqual(stored["replace_model_app_id"], "custom_app_456")
        self.assertEqual(runtime["cleanup_enabled"], False)
        self.assertEqual(stored["cleanup_enabled"], False)
        self.assertIn("runninghub_api_key", runtime)
        self.assertEqual(stored["runninghub_api_key"], "")

    def test_runtime_config_api_save_and_refresh_keeps_local_file_value(self):
        put_runtime_config = self._route_endpoint("/api/admin/runtime_config", "PUT")
        get_runtime_config = self._route_endpoint("/api/admin/runtime_config", "GET")
        with db_module.db() as conn:
            db_module.set_admin_config(
                conn,
                "runtime_config",
                {"replace_model_app_id": "legacy_db_app"},
                server._now_ts(),
            )

        payload = server.RuntimeConfigPayload(
            replace_model_app_id="custom_runtime_app",
            replace_model_original_app_id="custom_runtime_app",
            replace_model_primary_app_id="2047889041936355329",
            replace_model_slice_app_id="1955095782514987010",
            replace_model_motion_transfer_app_id="2047889041936355329",
            nano_host="runtime.example.internal",
            cleanup_enabled=False,
        )
        resp = put_runtime_config(payload, self.admin_user)
        self.assertEqual(resp["runtime_config"]["replace_model_app_id"], "custom_runtime_app")
        self.assertEqual(resp["runtime_config"]["nano_host"], "runtime.example.internal")
        self.assertEqual(resp["runtime_config"]["cleanup_enabled"], False)

        stored = json.loads(self.runtime_config_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["replace_model_app_id"], "custom_runtime_app")
        self.assertEqual(stored["replace_model_original_app_id"], "custom_runtime_app")
        self.assertEqual(stored["replace_model_primary_app_id"], "2047889041936355329")
        self.assertEqual(stored["nano_host"], "runtime.example.internal")
        self.assertEqual(stored["cleanup_enabled"], False)

        current = get_runtime_config(self.admin_user)
        self.assertEqual(current["replace_model_app_id"], "custom_runtime_app")
        self.assertEqual(current["replace_model_original_app_id"], "custom_runtime_app")
        self.assertEqual(current["nano_host"], "runtime.example.internal")
        self.assertEqual(current["cleanup_enabled"], False)

    def test_runtime_config_api_preserves_comfy_when_saving_partial_config(self):
        put_runtime_config = self._route_endpoint("/api/admin/runtime_config", "PUT")
        server._write_runtime_config_file(
            {
                "image_generate_mode_default": "remote_comfy",
                "comfy_workflow_source": "remote",
                "remote_comfy_gateway_url": "http://comfy.local",
                "remote_comfy_gateway_token": "secret-token",
                "remote_comfy_workflow_mappings": {"text_to_image": "wf-text", "image_generate": "wf-image"},
            }
        )

        resp = put_runtime_config(server.RuntimeConfigPayload(llm_base_url="http://llm.local"), self.admin_user)

        self.assertEqual(resp["runtime_config"]["llm_base_url"], "http://llm.local")
        self.assertEqual(resp["runtime_config"]["image_generate_mode_default"], "remote_comfy")
        self.assertEqual(resp["runtime_config"]["comfy_workflow_source"], "remote")
        self.assertEqual(resp["runtime_config"]["remote_comfy_gateway_url"], "http://comfy.local")
        self.assertEqual(resp["runtime_config"]["remote_comfy_gateway_token"], "secret-token")
        self.assertEqual(resp["runtime_config"]["remote_comfy_workflow_mappings"]["text_to_image"], "wf-text")

    def test_runtime_defaults_prefer_local_file_and_keep_explicit_app_id(self):
        with db_module.db() as conn:
            db_module.set_admin_config(
                conn,
                "runtime_config",
                {
                    "replace_model_app_id": "legacy_model_app",
                    "replace_product_app_id": "legacy_product_app",
                },
                server._now_ts(),
            )

        server._write_runtime_config_file(
            {
                "replace_model_app_id": "file_model_app",
                "replace_product_app_id": "file_product_app",
            }
        )

        replace_model_payload = server._apply_runtime_defaults("replace_model", {})
        replace_product_payload = server._apply_runtime_defaults("replace_product", {})
        combo_payload = server._apply_runtime_defaults("replace_productANDmodel", {})
        explicit_payload = server._apply_runtime_defaults("replace_model", {"app_id": "1234567890123456789"})

        self.assertEqual(replace_model_payload["app_id"], "file_model_app")
        self.assertEqual(replace_model_payload["workflow_id"], "file_model_app")
        self.assertEqual(replace_model_payload["workflow_ids"], ["file_model_app"])
        self.assertEqual(replace_product_payload["app_id"], "file_product_app")
        self.assertEqual(replace_product_payload["workflow_id"], "file_product_app")
        self.assertEqual(combo_payload["model_app_id"], "file_model_app")
        self.assertEqual(combo_payload["product_app_id"], "file_product_app")
        self.assertEqual(combo_payload["workflow_ids"], ["file_model_app", "file_product_app"])
        self.assertEqual(explicit_payload["app_id"], "1234567890123456789")
        self.assertEqual(explicit_payload["workflow_id"], "1234567890123456789")

    def test_runtime_defaults_fill_empty_comfy_mappings_from_runtime(self):
        server._write_runtime_config_file(
            {
                "comfy_workflow_source": "remote",
                "remote_comfy_gateway_url": "http://comfy.local",
                "remote_comfy_workflow_mappings": {"text_to_image": "wf-text"},
            }
        )

        payload = server._apply_runtime_defaults("text_to_image", {"remote_comfy_workflow_mappings": {}})

        self.assertEqual(payload["remote_comfy_gateway_url"], "http://comfy.local")
        self.assertEqual(payload["remote_comfy_workflow_mappings"], {"text_to_image": "wf-text"})

    def test_runtime_defaults_replace_invalid_replace_workflow_ids(self):
        server._write_runtime_config_file(
            {
                "replace_model_app_id": "1977634608437174274",
                "replace_product_app_id": "1977410328592031746",
            }
        )

        replace_model_payload = server._apply_runtime_defaults("replace_model", {"app_id": "runtime_rm_app"})
        replace_product_payload = server._apply_runtime_defaults("replace_product", {"app_id": "runtime_rp_app"})
        combo_payload = server._apply_runtime_defaults(
            "replace_productANDmodel",
            {"model_app_id": "runtime_rm_app", "product_app_id": "runtime_rp_app"},
        )

        self.assertEqual(replace_model_payload["app_id"], "1977634608437174274")
        self.assertEqual(replace_model_payload["workflow_id"], "1977634608437174274")
        self.assertEqual(replace_product_payload["app_id"], "1977410328592031746")
        self.assertEqual(replace_product_payload["workflow_id"], "1977410328592031746")
        self.assertEqual(combo_payload["model_app_id"], "1977634608437174274")
        self.assertEqual(combo_payload["product_app_id"], "1977410328592031746")
        self.assertEqual(combo_payload["workflow_ids"], ["1977634608437174274", "1977410328592031746"])

    def test_runtime_defaults_pick_mode_specific_replace_model_app_id(self):
        server._write_runtime_config_file(
            {
                "replace_model_original_app_id": "1977634608437174274",
                "replace_model_primary_app_id": "2047889041936355329",
                "replace_model_slice_app_id": "1955095782514987010",
                "replace_model_motion_transfer_app_id": "2047889041936355999",
            }
        )

        original_payload = server._apply_runtime_defaults("replace_model", {"mode": "original"})
        primary_payload = server._apply_runtime_defaults("replace_model", {"mode": "primary"})
        slice_payload = server._apply_runtime_defaults("replace_model", {"mode": "slice"})
        motion_payload = server._apply_runtime_defaults("replace_model", {"mode": "motion_transfer"})

        self.assertEqual(original_payload["app_id"], "1977634608437174274")
        self.assertEqual(original_payload["workflow_name"], "替换模特（原版工作流）")
        self.assertEqual(primary_payload["app_id"], "2047889041936355329")
        self.assertEqual(primary_payload["workflow_name"], "替换模特（主要工作流）")
        self.assertEqual(slice_payload["app_id"], "1955095782514987010")
        self.assertEqual(slice_payload["workflow_name"], "替换模特（切片工作流）")
        self.assertEqual(motion_payload["app_id"], "2047889041936355999")
        self.assertEqual(motion_payload["workflow_name"], "替换模特（动作迁移工作流）")

    def test_runtime_config_api_returns_error_when_file_is_broken(self):
        get_runtime_config = self._route_endpoint("/api/admin/runtime_config", "GET")
        put_runtime_config = self._route_endpoint("/api/admin/runtime_config", "PUT")
        self.runtime_config_path.write_text("{broken json", encoding="utf-8")

        with self.assertRaises(server.HTTPException) as get_ctx:
            get_runtime_config(self.admin_user)

        save_resp = put_runtime_config(
            server.RuntimeConfigPayload(replace_model_app_id="should_not_save"),
            self.admin_user,
        )

        self.assertEqual(get_ctx.exception.status_code, 500)
        self.assertIn("运行配置文件", str(get_ctx.exception.detail))
        self.assertTrue(self.runtime_config_path.exists())
        self.assertEqual(save_resp["runtime_config"]["replace_model_app_id"], server.DEFAULT_RUNTIME_CONFIG["replace_model_original_app_id"])

    def test_create_task_record_keeps_raw_secrets_for_retry(self):
        payload = {
            "nano_api_key": "sk-raw-nano-secret",
            "gemini_api_key": "sk-raw-gemini-secret",
            "runninghub_api_key": "rh-raw-secret",
            "nano_host": "example.internal",
            "gemini_host": "example.internal",
        }
        server._create_task_record("task_raw_secret", 1, "commerce_video", payload)

        with db_module.db() as conn:
            row = conn.execute("SELECT input_json FROM tasks WHERE id = ?", ("task_raw_secret",)).fetchone()
        stored = json.loads(row["input_json"])
        self.assertEqual(stored["nano_api_key"], "sk-raw-nano-secret")
        self.assertEqual(stored["gemini_api_key"], "sk-raw-gemini-secret")
        self.assertEqual(stored["runninghub_api_key"], "rh-raw-secret")

    def test_runninghub_image_generate_waits_until_query_reaches_success(self):
        calls = [
            {"status": "RUNNING", "progress": None, "raw": {"taskId": "rh_task_1", "status": "RUNNING"}},
            {"status": "RUNNING", "progress": None, "raw": {"taskId": "rh_task_1", "status": "RUNNING"}},
            {
                "status": "success",
                "results": [{"url": "https://example.com/out.png"}],
                "usage": {"consumeCoins": "91"},
                "raw": {"taskId": "rh_task_1", "status": "SUCCESS", "results": [{"url": "https://example.com/out.png"}]},
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "input.png"
            src.write_bytes(b"fake")
            out = Path(tmpdir) / "out.png"
            with patch.object(server, "_upload_binary_to_runninghub", return_value="https://example.com/in.png"), \
                 patch.object(server.requests, "post") as mock_post, \
                 patch.object(server.runninghub_common, "query_task", side_effect=calls), \
                 patch.object(server, "_download_to_file", side_effect=lambda url, path: Path(path).write_bytes(b"png")), \
                 patch.object(server, "_build_task_workdir", return_value=Path(tmpdir)), \
                 patch.object(server.time, "sleep", return_value=None):
                response = type("Resp", (), {"raise_for_status": lambda self: None, "json": lambda self: {"taskId": "rh_task_1", "status": "RUNNING"}})()
                mock_post.return_value = response
                result = server._run_image_generate_via_runninghub_workflow(
                    "task_demo",
                    {
                        "runninghub_api_key": "rh-key",
                        "image_runninghub_workflow_id": "1900814586436534274",
                        "product_name": "耳环",
                        "style_hint": "白底",
                    },
                    ref_input=src,
                    prompt_text="生成图片",
                    mode="product_only",
                )
        self.assertEqual(result["runninghub_task_id"], "rh_task_1")
        self.assertTrue(str(result["image_path"]).endswith("image_generate_output.png"))
        self.assertEqual(mock_post.call_count, 1)

    def test_runninghub_image_generate_surfaces_audit_failure_clearly(self):
        calls = [
            {
                "status": "failed",
                "errorCode": "805",
                "errorMessage": "工作流运行失败",
                "raw": {
                    "taskId": "rh_task_2",
                    "status": "FAILED",
                    "errorCode": "805",
                    "errorMessage": "工作流运行失败",
                    "failedReason": {
                        "exception_type": "audit.RHAuditException",
                        "msg": "Porn",
                        "node_name": "PreviewBridge",
                    },
                },
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "input.png"
            src.write_bytes(b"fake")
            with patch.object(server, "_upload_binary_to_runninghub", return_value="https://example.com/in.png"), \
                 patch.object(server.requests, "post") as mock_post, \
                 patch.object(server.runninghub_common, "query_task", side_effect=calls), \
                 patch.object(server, "_build_task_workdir", return_value=Path(tmpdir)), \
                 patch.object(server.time, "sleep", return_value=None):
                response = type("Resp", (), {"raise_for_status": lambda self: None, "json": lambda self: {"taskId": "rh_task_2", "status": "RUNNING"}})()
                mock_post.return_value = response
                with self.assertRaises(RuntimeError) as ctx:
                    server._run_image_generate_via_runninghub_workflow(
                        "task_demo",
                        {
                            "runninghub_api_key": "rh-key",
                            "image_runninghub_workflow_id": "1900814586436534274",
                            "product_name": "耳环",
                            "style_hint": "白底",
                        },
                        ref_input=src,
                        prompt_text="生成图片",
                        mode="product_only",
                    )
        self.assertIn("图片疑似触发平台审核", str(ctx.exception))
        self.assertIn("PreviewBridge", str(ctx.exception))
        self.assertIn("Porn", str(ctx.exception))


    def test_apply_runtime_defaults_restores_masked_task_secrets(self):
        server._write_runtime_config_file(
            {
                "runninghub_api_key": "rh-live-secret",
                "nano_api_key": "sk-live-nano-secret",
                "nano_host": "202.90.21.53",
                "nano_port": "3008",
                "gemini_api_key": "sk-live-gemini-secret",
                "gemini_host": "202.90.21.53",
                "gemini_port": "3008",
            }
        )

        restored = server._apply_runtime_defaults(
            "commerce_video",
            {
                "runninghub_api_key": "rh-l***cret",
                "nano_api_key": "sk-A***DJwt",
                "gemini_api_key": "sk-A***DJwt",
                "nano_host": "202.90.21.53",
                "nano_port": "3008",
                "gemini_host": "202.90.21.53",
                "gemini_port": "3008",
            },
        )

        self.assertEqual(restored["runninghub_api_key"], "rh-live-secret")
        self.assertEqual(restored["nano_api_key"], "sk-live-nano-secret")
        self.assertEqual(restored["gemini_api_key"], "sk-live-gemini-secret")

    def test_task_detail_masks_stored_secrets(self):
        get_task_detail = self._route_endpoint("/api/tasks/{task_id}", "GET")
        server._write_runtime_config_file({"llm_api_key": "sk-live-llm-secret", "llm_base_url": "http://example.internal:3008"})
        payload = {
            "nano_api_key": "sk-raw-nano-secret",
            "gemini_api_key": "sk-raw-gemini-secret",
            "runninghub_api_key": "rh-raw-secret",
            "nano_host": "example.internal",
            "gemini_host": "example.internal",
            "llm_api_key": "sk-raw-llm-secret",
            "llm_base_url": "http://example.internal:3008",
        }
        server._create_task_record("task_detail_secret", 1, "commerce_video", payload)

        detail = get_task_detail("task_detail_secret", self.admin_user)
        self.assertEqual(detail["input"]["nano_api_key"], "sk-r***cret")
        self.assertEqual(detail["input"]["gemini_api_key"], "sk-r***cret")
        self.assertEqual(detail["input"]["runninghub_api_key"], "rh-r***cret")
        self.assertEqual(detail["error_analysis_available"], True)

    def test_task_detail_reports_error_analysis_capability(self):
        get_task_detail = self._route_endpoint("/api/tasks/{task_id}", "GET")
        server._write_runtime_config_file({"gemini_api_key": "g-key-001", "gemini_host": "202.90.21.53"})
        server._create_task_record("task_detail_analysis_flag", 1, "commerce_video", {})

        detail = get_task_detail("task_detail_analysis_flag", self.admin_user)

        self.assertEqual(detail["error_analysis_available"], True)

    def test_image_generate_task_detail_keeps_comfy_runtime_fields(self):
        get_task_detail = self._route_endpoint("/api/tasks/{task_id}", "GET")
        server._write_runtime_config_file(
            {
                "image_generate_mode_default": "remote_comfy",
                "comfy_workflow_source": "remote",
                "remote_comfy_gateway_url": "http://comfy.local",
                "remote_comfy_workflow_mappings": {"image_generate": "wf-image"},
                "llm_base_url": "http://202.90.21.53:3008",
                "llm_api_key": "sk-gemini-llm",
                "llm_default_model": "gemini-3.1-pro-preview",
            }
        )
        payload = server._apply_runtime_defaults(
            "image_generate",
            {
                "image_generate_provider": "remote_comfy",
                "prompt": "生成图片",
            },
        )
        server._create_task_record("task_image_detail_clean", 1, "image_generate", payload)

        detail = get_task_detail("task_image_detail_clean", self.admin_user)

        self.assertEqual(detail["workflow_id"], "wf-image")
        self.assertEqual(detail["workflow_ids"], ["wf-image"])
        self.assertEqual(detail["input"]["image_generate_mode_default"], "remote_comfy")
        self.assertEqual(detail["input"]["remote_comfy_gateway_url"], "http://comfy.local")
        self.assertEqual(detail["input"]["remote_comfy_workflow_mappings"], {"image_generate": "wf-image"})
        self.assertEqual(detail["input"]["llm_base_url"], "http://202.90.21.53:3008")
        self.assertEqual(detail["input"]["llm_api_key"], "sk-g***-llm")
        self.assertEqual(detail["input"]["llm_default_model"], "gemini-3.1-pro-preview")
        self.assertEqual(detail["input"]["image_generate_provider"], "remote_comfy")
        self.assertNotIn("image_model_provider_base_url", detail["input"])
    def test_runtime_config_supports_comfy_image_generation_and_llm_fields(self):
        payload = server.RuntimeConfigPayload(
            image_generate_mode_default="remote_comfy",
            comfy_workflow_source="remote",
            remote_comfy_gateway_url="http://comfy.local",
            remote_comfy_workflow_mappings={"image_generate": "wf-image"},
            llm_base_url="http://202.90.21.53:3008",
            llm_api_key="sk-gemini-llm",
            llm_default_model="gemini-3.1-pro-preview",
        )
        self.assertEqual(payload.image_generate_mode_default, "remote_comfy")
        self.assertEqual(payload.comfy_workflow_source, "remote")
        self.assertEqual(payload.remote_comfy_gateway_url, "http://comfy.local")
        self.assertEqual(payload.remote_comfy_workflow_mappings, {"image_generate": "wf-image"})
        self.assertEqual(payload.llm_base_url, "http://202.90.21.53:3008")
        self.assertEqual(payload.llm_api_key, "sk-gemini-llm")
        self.assertEqual(payload.llm_default_model, "gemini-3.1-pro-preview")

    def test_chinese_image_prompt_format_keeps_punctuation_and_8k(self):
        prompt = (
            "一位女子全身躺在床上穿着丝质睡袍她的左手轻轻抚摸胸前而右手放在大腿内侧"
            "她的身体平躺微微拱起朝向镜头她的头转向直视镜头带着诱惑眼神"
            "豪华卧室背景柔和大床和枕头柔和的暖光从侧面照射浅景深真实皮肤纹理细节布料褶皱自然身体比例高细节8写实摄影风格"
        )

        normalized = server._normalize_tg_chinese_image_prompt_format(prompt)

        self.assertIn("床上，穿着", normalized)
        self.assertIn("，她的左手", normalized)
        self.assertIn("，她的身体", normalized)
        self.assertIn("，她的头", normalized)
        self.assertIn("浅景深，真实皮肤纹理", normalized)
        self.assertNotIn("真实，皮肤，纹理", normalized)
        self.assertIn("高细节，8K，写实摄影风格", normalized)

    def test_finished_text_to_image_uses_reply_keyboard(self):
        markup = server._send_telegram_reply_markup_for_finished_task("task_1", "text_to_image")

        self.assertIn("keyboard", markup)
        self.assertNotIn("inline_keyboard", markup)
        self.assertEqual(markup["keyboard"][0][0]["text"], "重新生成图片")
        self.assertEqual(markup["keyboard"][1][0]["text"], "继续生成图片")
        self.assertEqual(markup["keyboard"][2][0]["text"], "返回主菜单")

    def test_text_to_image_auto_qa_retries_rejected_candidate(self):
        first_image = Path(self._tmpdir.name) / "first.png"
        second_image = Path(self._tmpdir.name) / "second.png"
        first_image.write_bytes(b"first")
        second_image.write_bytes(b"second")
        comfy_results = [
            {"ok": True, "prompt_id": "prompt_1", "local_outputs": [{"local_path": str(first_image), "filename": "first.png"}]},
            {"ok": True, "prompt_id": "prompt_2", "local_outputs": [{"local_path": str(second_image), "filename": "second.png"}]},
        ]
        qa_results = [
            {
                "inspected": True,
                "passed": False,
                "overall_score": 40,
                "prompt_match_score": 80,
                "anatomy_score": 20,
                "visual_score": 60,
                "limb_or_body_broken": True,
                "issues": ["人物肢体错乱"],
            },
            {
                "inspected": True,
                "passed": True,
                "overall_score": 88,
                "prompt_match_score": 86,
                "anatomy_score": 85,
                "visual_score": 84,
                "deliverable_ready": True,
                "issues": [],
            },
        ]

        with patch.object(server, "_run_remote_comfy_gateway_test", side_effect=comfy_results) as run_mock, patch.object(
            server,
            "_analyze_generated_person_image_quality",
            side_effect=qa_results,
        ), patch.object(server, "_new_image_qa_seed", return_value=123456):
            output = server._run_remote_comfy_mapped_task(
                "task_qa",
                {
                    "remote_comfy_gateway_url": "http://comfy.local",
                    "remote_comfy_workflow_mappings": {"text_to_image": "ZIT_final_output.api.json"},
                    "prompt": "一位人物肖像，清晰自然",
                    "width": 640,
                    "height": 960,
                    "text_to_image_auto_qa_enabled": True,
                    "text_to_image_auto_qa_max_attempts": 3,
                },
                "text_to_image",
            )

        self.assertEqual(run_mock.call_count, 2)
        self.assertIsNone(run_mock.call_args_list[0].kwargs["seed"])
        self.assertEqual(run_mock.call_args_list[1].kwargs["seed"], 123456)
        self.assertEqual(output["image_path"], str(second_image))
        self.assertEqual(output["seed"], 123456)
        self.assertEqual(output["image_qa"]["rejected_rounds"], 1)
        self.assertEqual(output["image_qa"]["attempts"], 2)
        self.assertIn("已筛选 1 轮候选图", server._text_to_image_qa_notice(output))

    def test_text_to_image_auto_qa_unavailable_is_blocking(self):
        report = {"inspected": False, "passed": False, "qa_unavailable": True}

        self.assertTrue(server._should_reject_generated_person_image(report))


if __name__ == "__main__":
    unittest.main()
