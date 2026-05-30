from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientSession, TCPConnector
from aiohttp.resolver import ThreadedResolver
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from .config import AppConfig
from .media import extract_video_first_frame
from .workbench import WorkspaceService
from .workflow import WorkflowRequest


logger = logging.getLogger(__name__)
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
ZIP_EXTS = {".zip"}
AUTO_DURATION_TEXTS = {"跳过", "自动", "auto", "AUTO"}
TG_PROMPT_PREVIEW_TIMEOUT_SECONDS = int(os.getenv("TG_PROMPT_PREVIEW_TIMEOUT_SECONDS") or "240")

DIGITAL_HUMAN_VIDEO_BUTTON = "数字人视频生成"
DIGITAL_HUMAN_REALISTIC_BUTTON = "写实带货视频"
DIGITAL_HUMAN_LIVE_BUTTON = "直播口播视频"
DIGITAL_HUMAN_PRODUCT_BUTTON = "产品展示视频"
DIGITAL_HUMAN_CUSTOM_BUTTON = "自定义数字人要求"
ORAL_UPLOAD_BUTTON = DIGITAL_HUMAN_VIDEO_BUTTON
LEGACY_ORAL_UPLOAD_BUTTON = "口播数字人：上传素材"
WORKFLOW_CONFIG_BUTTON = "查看后台工作流配置"
IMAGE_WORKFLOW_BUTTON = "图像生成"
TEXT_TO_IMAGE_BUTTON = "文生图"
MULTI_IMAGE_BUTTON = "多图生成"
IMAGE_REPLACE_BUTTON = "图片替换"
VIDEO_GENERAL_EDIT_BUTTON = "图生视频"
VIDEO_I2V_RES_PREFIX = "分辨率："
VIDEO_I2V_DURATION_PREFIX = "时长："
VIDEO_I2V_GROK_ON = "Grok提示词：开"
VIDEO_I2V_GROK_OFF = "Grok提示词：关"
VIDEO_I2V_EXTEND_ON = "接口扩写：开"
VIDEO_I2V_EXTEND_OFF = "接口扩写：关"
LEGACY_IMAGE_WORKFLOW_BUTTON = "图像编辑工作流"
LEGACY_IMAGE_GENERATE_WORKFLOW_BUTTON = "图片生成工作流"
VIDEO_EDIT_BUTTON = "视频生成"
MAIN_MENU_BUTTON = "返回主菜单"
REPLACE_MODEL_WORKFLOW_BUTTON = "视频模特替换"
LEGACY_REPLACE_MODEL_WORKFLOW_BUTTON = "模特替换工作流"
REPLACE_PRODUCT_WORKFLOW_BUTTON = "视频商品替换"
LEGACY_REPLACE_PRODUCT_WORKFLOW_BUTTON = "商品替换工作流"
REPLACE_UNION_WORKFLOW_BUTTON = "联合替换工作流"

LEGACY_UPLOAD_BUTTON = "上传素材建立任务"
STATUS_BUTTON = "查看工作台状态"
WORKBENCH_BUTTON = "工作台网址"
SET_SCRIPT_BUTTON = "设置预设文案"
RERUN_BUTTON = "重跑最近任务"
STOP_BUTTON = "强制停止当前任务"

TRADITIONAL_BUTTON_ALIASES = {
    "數字人視頻生成": DIGITAL_HUMAN_VIDEO_BUTTON,
    "寫實帶貨視頻": DIGITAL_HUMAN_REALISTIC_BUTTON,
    "直播口播視頻": DIGITAL_HUMAN_LIVE_BUTTON,
    "產品展示視頻": DIGITAL_HUMAN_PRODUCT_BUTTON,
    "自定義數字人要求": DIGITAL_HUMAN_CUSTOM_BUTTON,
    "口播數字人：上傳素材": LEGACY_ORAL_UPLOAD_BUTTON,
    "上傳素材建立任務": LEGACY_UPLOAD_BUTTON,
    "查看後台工作流配置": WORKFLOW_CONFIG_BUTTON,
    "图像编辑": IMAGE_WORKFLOW_BUTTON,
    "圖像生成": IMAGE_WORKFLOW_BUTTON,
    "圖片生成": IMAGE_WORKFLOW_BUTTON,
    "圖片編輯": IMAGE_WORKFLOW_BUTTON,
    "圖像編輯": IMAGE_WORKFLOW_BUTTON,
    "文生圖片": TEXT_TO_IMAGE_BUTTON,
    "多圖生成": MULTI_IMAGE_BUTTON,
    "圖片替換": IMAGE_REPLACE_BUTTON,
    "圖像編輯工作流": LEGACY_IMAGE_WORKFLOW_BUTTON,
    "圖片生成工作流": LEGACY_IMAGE_GENERATE_WORKFLOW_BUTTON,
    "视频编辑": VIDEO_EDIT_BUTTON,
    "視頻生成": VIDEO_EDIT_BUTTON,
    "視頻編輯": VIDEO_EDIT_BUTTON,
    "視頻編輯任務": VIDEO_GENERAL_EDIT_BUTTON,
    "圖生視頻": VIDEO_GENERAL_EDIT_BUTTON,
    "返回主菜單": MAIN_MENU_BUTTON,
    "視頻模特替換": REPLACE_MODEL_WORKFLOW_BUTTON,
    "模特替換工作流": LEGACY_REPLACE_MODEL_WORKFLOW_BUTTON,
    "視頻商品替換": REPLACE_PRODUCT_WORKFLOW_BUTTON,
    "商品替換工作流": LEGACY_REPLACE_PRODUCT_WORKFLOW_BUTTON,
    "聯合替換工作流": REPLACE_UNION_WORKFLOW_BUTTON,
    "查看工作台狀態": STATUS_BUTTON,
    "工作台網址": WORKBENCH_BUTTON,
    "設置預設文案": SET_SCRIPT_BUTTON,
    "設定預設文案": SET_SCRIPT_BUTTON,
    "重跑最近任務": RERUN_BUTTON,
    "強制停止目前任務": STOP_BUTTON,
    "強制停止當前任務": STOP_BUTTON,
    "多智能體數字人": "多智能体数字人",
}

WORKFLOW_REFERENCE_BUTTONS = {
    WORKFLOW_CONFIG_BUTTON,
    IMAGE_WORKFLOW_BUTTON,
    LEGACY_IMAGE_WORKFLOW_BUTTON,
    LEGACY_IMAGE_GENERATE_WORKFLOW_BUTTON,
    REPLACE_MODEL_WORKFLOW_BUTTON,
    LEGACY_REPLACE_MODEL_WORKFLOW_BUTTON,
    REPLACE_PRODUCT_WORKFLOW_BUTTON,
    LEGACY_REPLACE_PRODUCT_WORKFLOW_BUTTON,
    REPLACE_UNION_WORKFLOW_BUTTON,
}


def _canonical_button_text(text: str) -> str:
    return TRADITIONAL_BUTTON_ALIASES.get(str(text or "").strip(), str(text or "").strip())


class _ThreadedResolverConnector(TCPConnector):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("resolver", ThreadedResolver())
        super().__init__(*args, **kwargs)


class ScriptForm(StatesGroup):
    waiting_for_script = State()


class UploadFlowForm(StatesGroup):
    waiting_for_custom_requirement = State()
    waiting_for_video = State()
    waiting_for_script = State()
    waiting_for_portrait_prompt = State()
    waiting_for_duration = State()


class ProductionWorkflowForm(StatesGroup):
    text_to_image_waiting_for_ratio = State()
    text_to_image_waiting_for_resolution = State()
    text_to_image_waiting_for_persona = State()
    text_to_image_waiting_for_prompt_mode = State()
    text_to_image_waiting_for_prompt = State()
    text_to_image_waiting_for_revision = State()
    text_to_image_waiting_for_custom_prompt = State()
    image_waiting_for_product_image = State()
    image_waiting_for_model_image = State()
    image_waiting_for_prompt = State()
    video_i2v_waiting_for_image = State()
    video_i2v_waiting_for_prompt = State()
    replace_model_waiting_for_video = State()
    replace_model_waiting_for_image = State()
    replace_model_waiting_for_prompt = State()
    replace_model_waiting_for_duration = State()
    replace_product_waiting_for_video = State()
    replace_product_waiting_for_image = State()
    replace_product_waiting_for_name = State()
    replace_product_waiting_for_prompt = State()
    replace_product_waiting_for_duration = State()
    union_waiting_for_video = State()
    union_waiting_for_model_image = State()
    union_waiting_for_product_image = State()
    union_waiting_for_name = State()
    union_waiting_for_duration = State()


def _detect_proxy() -> str | None:
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    proxies = urllib.request.getproxies()
    return proxies.get("https") or proxies.get("http")


def _build_bot(config: AppConfig) -> Bot:
    proxy = _detect_proxy()
    session = AiohttpSession(proxy=proxy)
    if not proxy:
        # Prefer the system threaded resolver to avoid intermittent aiodns failures
        # that can leave Telegram polling stalled without processing updates.
        session._connector_type = _ThreadedResolverConnector
    return Bot(
        token=config.tg_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )


def _menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=IMAGE_WORKFLOW_BUTTON), KeyboardButton(text=VIDEO_EDIT_BUTTON)],
            [KeyboardButton(text=STATUS_BUTTON), KeyboardButton(text=STOP_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _image_edit_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=TEXT_TO_IMAGE_BUTTON)],
            [KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


TEXT_TO_IMAGE_RATIO_OPTIONS: dict[str, dict[str, Any]] = {
    "2:3": {"label": "2:3 竖图", "note": "基础竖图", "width": 640, "height": 960, "final": "2176 x 3264"},
    "3:4": {"label": "3:4 稳定竖图", "note": "稳定竖图", "width": 672, "height": 896, "final": "2285 x 3046"},
    "9:16": {"label": "9:16 手机竖屏", "note": "手机竖屏长图", "width": 576, "height": 1024, "final": "1958 x 3482"},
    "3:2": {"label": "3:2 横图", "note": "横图基准", "width": 960, "height": 640, "final": "3264 x 2176"},
    "4:3": {"label": "4:3 平衡横图", "note": "平衡横图", "width": 896, "height": 672, "final": "3046 x 2285"},
    "16:9": {"label": "16:9 宽屏", "note": "宽屏视频", "width": 1024, "height": 576, "final": "3482 x 1958"},
    "1:1": {"label": "1:1 正方形", "note": "正方形", "width": 768, "height": 768, "final": "2611 x 2611"},
}


TEXT_TO_IMAGE_PERSONA_LORA_NODE_INPUTS: dict[str, dict[str, Any]] = {
    "821": {
        "lora_1": {
            "on": True,
            "lora": r"Character Setting\人设1捞女1金君雅.safetensors",
            "strength": 1.0,
            "strengthTwo": None,
        }
    },
    "822": {
        "lora_1": {
            "on": True,
            "lora": r"Character Setting\人设1捞女1金君雅.safetensors",
            "strength": 0.3,
            "strengthTwo": None,
        }
    },
}


def _text_to_image_persona_available() -> bool:
    return bool(_text_to_image_persona_options())


def _text_to_image_persona_options() -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for values in TEXT_TO_IMAGE_PERSONA_LORA_NODE_INPUTS.values():
        if not isinstance(values, dict):
            continue
        for lora_value in values.values():
            if not isinstance(lora_value, dict):
                continue
            path = str(lora_value.get("lora") or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            label = Path(path.replace("\\", "/")).stem or path
            options.append({"id": str(len(options)), "label": label, "path": path})
    return options


def _text_to_image_persona_label(path: str | None) -> str:
    target = str(path or "").strip()
    for option in _text_to_image_persona_options():
        if option["path"] == target:
            return option["label"]
    return Path(target.replace("\\", "/")).stem if target else ""


def _text_to_image_default_persona_path() -> str:
    options = _text_to_image_persona_options()
    return options[0]["path"] if options else ""


def _text_to_image_persona_node_inputs(*, enabled: bool, persona_lora: str = "") -> dict[str, dict[str, Any]]:
    node_inputs: dict[str, dict[str, Any]] = {}
    selected_lora = str(persona_lora or _text_to_image_default_persona_path()).strip()
    for node_id, values in TEXT_TO_IMAGE_PERSONA_LORA_NODE_INPUTS.items():
        lora_value = dict(values.get("lora_1") or {})
        if enabled and selected_lora:
            lora_value["on"] = True
            lora_value["lora"] = selected_lora
            node_inputs[node_id] = {"lora_1": lora_value}
        else:
            lora_value["on"] = False
            lora_value["strength"] = 0.0
            node_inputs[node_id] = {"lora_1": lora_value}
    return node_inputs


def _text_to_image_params(data: dict[str, Any] | None = None) -> dict[str, Any]:
    source = data or {}
    ratio = str(source.get("aspect_ratio") or "2:3").strip()
    if ratio not in TEXT_TO_IMAGE_RATIO_OPTIONS:
        ratio = "2:3"
    option = dict(TEXT_TO_IMAGE_RATIO_OPTIONS[ratio])
    final_resolution_enabled = bool(source.get("final_resolution_enabled", False))
    persona_available = _text_to_image_persona_available()
    persona_enabled = bool(source.get("persona_enabled", True if persona_available else False))
    persona_lora = str(source.get("persona_lora") or _text_to_image_default_persona_path()).strip() if persona_available else ""
    return {
        "aspect_ratio": ratio,
        "width": int(option["width"]),
        "height": int(option["height"]),
        "final": str(option["final"]),
        "label": str(option["label"]),
        "note": str(option["note"]),
        "final_resolution_enabled": final_resolution_enabled,
        "persona_available": persona_available,
        "persona_enabled": bool(persona_enabled and persona_available),
        "persona_lora": persona_lora,
        "persona_label": _text_to_image_persona_label(persona_lora),
        "ratio_selected": bool(source.get("ratio_selected", False)),
        "resolution_selected": bool(source.get("resolution_selected", False)),
        "persona_selected": bool(source.get("persona_selected", False)),
        "prompt_mode_selected": bool(source.get("prompt_mode_selected", False)),
        "prompt_mode_label": str(source.get("prompt_mode_label") or "").strip(),
    }


def _text_to_image_remote_node_inputs(params: dict[str, Any]) -> dict[str, Any]:
    detailer_inputs = {
        "guide_size": 512.0,
        "guide_size_for": True,
        "max_size": 1440.0,
        "steps": 4,
        "cfg": 1.0,
        "sampler_name": "dpmpp_2m_sde",
        "scheduler": "sgm_uniform",
        "denoise": 0.45,
        "feather": 100,
        "noise_mask": True,
        "force_inpaint": True,
        "wildcard": "",
        "cycle": 1,
        "inpaint_model": False,
        "noise_mask_feather": 20,
        "tiled_encode": False,
        "tiled_decode": False,
    }
    safe_save_prefixes = {
        "698": {"width": int(params["width"]), "height": int(params["height"]), "batch_size": 1},
        "715": {"filename_prefix": "telegram/ZIT_upscale"},
        "732": {"filename_prefix": "telegram/ZIT_blend"},
    }
    if bool(params.get("final_resolution_enabled")):
        node_inputs = {
            "647": {"scale_by": 1.7},
            "637": {"value": 2.0},
            "663": {
                "steps": 3,
                "cfg": 1.0,
                "sampler_name": "dpmpp_2m_sde",
                "scheduler": "sgm_uniform",
                "denoise": 0.23,
                "mode_type": "Linear",
                "mask_blur": 64,
                "tile_padding": 96,
                "seam_fix_mode": "None",
                "seam_fix_denoise": 1.0,
                "seam_fix_width": 64,
                "seam_fix_mask_blur": 8,
                "seam_fix_padding": 16,
                "force_uniform_tiles": True,
                "tiled_decode": False,
                "batch_size": 1,
            },
            "713": {
                "resolution": 1080,
                "color_correction": "lab",
                "offload_device": "cpu",
                "temporal_overlap": 0,
            },
            "789": {"image": ["663", 0], **detailer_inputs},
            "790": {"image": ["663", 0]},
            **safe_save_prefixes,
        }
    else:
        node_inputs = {
            "647": {"scale_by": 1.0},
            "637": {"value": 1.0},
            "663": {
                "steps": 3,
                "cfg": 1.0,
                "sampler_name": "dpmpp_2m_sde",
                "scheduler": "sgm_uniform",
                "denoise": 0.23,
                "mode_type": "Linear",
                "mask_blur": 64,
                "tile_padding": 96,
                "seam_fix_mode": "None",
                "seam_fix_denoise": 1.0,
                "seam_fix_width": 64,
                "seam_fix_mask_blur": 8,
                "seam_fix_padding": 16,
                "force_uniform_tiles": True,
                "tiled_decode": False,
                "batch_size": 1,
            },
            "789": {"image": ["663", 0], **detailer_inputs},
            "790": {"image": ["663", 0]},
            **safe_save_prefixes,
        }
    if _text_to_image_persona_available():
        node_inputs.update(
            _text_to_image_persona_node_inputs(
                enabled=bool(params.get("persona_enabled")),
                persona_lora=str(params.get("persona_lora") or ""),
            )
        )
    return node_inputs


def _text_to_image_status_text(*, step: str, params: dict[str, Any]) -> str:
    ratio_text = f"{params['aspect_ratio']}（{params['note']}）" if params.get("ratio_selected") else ""
    base_resolution_text = f"{params['width']} x {params['height']}" if params.get("ratio_selected") else ""
    final_resolution_text = ""
    if params.get("resolution_selected"):
        final_resolution_text = "开启，预计 " + params["final"] if params.get("final_resolution_enabled") else "关闭，使用基础分辨率"
    persona_text = ""
    if params.get("persona_selected"):
        if params.get("persona_enabled"):
            persona_text = params.get("persona_label") or "使用人设"
        elif params.get("persona_available"):
            persona_text = "不使用"
        else:
            persona_text = "当前工作流未检测到可选人设"
    prompt_mode_text = str(params.get("prompt_mode_label") or "") if params.get("prompt_mode_selected") else ""
    return "\n".join(
        [
            "文生图设置",
            f"当前步骤：{step}",
            f"画面比例：{ratio_text}",
            f"基础分辨率：{base_resolution_text}",
            f"最终分辨率：{final_resolution_text}",
            f"人设 LoRA：{persona_text}",
            f"提示词方式：{prompt_mode_text}",
        ]
    )


def _text_to_image_ratio_keyboard(*, selected_ratio: str = "") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    items = list(TEXT_TO_IMAGE_RATIO_OPTIONS.items())
    for idx in range(0, len(items), 2):
        row: list[InlineKeyboardButton] = []
        for ratio, option in items[idx : idx + 2]:
            prefix = "✓ " if ratio == selected_ratio else ""
            row.append(InlineKeyboardButton(text=f"{prefix}{option['label']}", callback_data=f"t2i:ratio:{ratio}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="返回主菜单", callback_data="t2i:main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text_to_image_resolution_keyboard(*, final_resolution_enabled: bool = False, selected: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✓ ' if selected and not final_resolution_enabled else ''}使用基础分辨率",
                callback_data="t2i:final:off",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✓ ' if selected and final_resolution_enabled else ''}开启最终分辨率",
                callback_data="t2i:final:on",
            )
        ],
        [
            InlineKeyboardButton(text="上一步", callback_data="t2i:back:ratio"),
        ],
        [InlineKeyboardButton(text="返回主菜单", callback_data="t2i:main_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text_to_image_persona_keyboard(*, persona_enabled: bool = True, persona_lora: str = "", selected: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    selected_lora = str(persona_lora or "").strip()
    for option in _text_to_image_persona_options():
        prefix = "✓ " if selected and persona_enabled and option["path"] == selected_lora else ""
        rows.append([InlineKeyboardButton(text=f"{prefix}{option['label']}", callback_data=f"t2i:persona:{option['id']}")])
    rows.append([InlineKeyboardButton(text=f"{'✓ ' if selected and not persona_enabled else ''}不使用人设", callback_data="t2i:persona:off")])
    rows.append(
        [
            InlineKeyboardButton(text="上一步", callback_data="t2i:back:resolution"),
        ]
    )
    rows.append([InlineKeyboardButton(text="返回主菜单", callback_data="t2i:main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text_to_image_prompt_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="让 Grok 生成提示词", callback_data="t2i:ready_prompt")],
            [InlineKeyboardButton(text="输入自定义提示词", callback_data="t2i:custom_prompt")],
            [InlineKeyboardButton(text="上一步", callback_data="t2i:back:persona")],
            [InlineKeyboardButton(text="返回主菜单", callback_data="t2i:main_menu")],
        ]
    )


def _text_to_image_prompt_entry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="上一步", callback_data="t2i:back:prompt_mode")],
            [InlineKeyboardButton(text="返回主菜单", callback_data="t2i:main_menu")],
        ]
    )


def _text_to_image_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="使用这个提示词生成", callback_data="t2i:submit")],
            [InlineKeyboardButton(text="输入自定义提示词提交", callback_data="t2i:custom_prompt")],
            [InlineKeyboardButton(text="继续让 Grok 调整", callback_data="t2i:adjust")],
            [InlineKeyboardButton(text="重新生成提示词", callback_data="t2i:regen")],
            [InlineKeyboardButton(text="返回参数设置", callback_data="t2i:settings"), InlineKeyboardButton(text="返回主菜单", callback_data="t2i:main_menu")],
        ]
    )


def _text_to_image_prompt_failure_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="重新生成提示词", callback_data="t2i:regen")],
            [InlineKeyboardButton(text="输入自定义提示词", callback_data="t2i:custom_prompt")],
            [InlineKeyboardButton(text="上一步", callback_data="t2i:back:prompt_mode")],
            [InlineKeyboardButton(text="返回主菜单", callback_data="t2i:main_menu")],
        ]
    )


def _format_grok_preview_error(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return f"Grok 响应超时（超过 {TG_PROMPT_PREVIEW_TIMEOUT_SECONDS} 秒）。可以点击“重新生成提示词”再试一次，或先输入自定义提示词。"
    text = str(exc or "").strip()
    if not text:
        return f"Grok 提示词生成失败（{type(exc).__name__}）。可以点击“重新生成提示词”再试一次。"
    return text


def _video_edit_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=VIDEO_GENERAL_EDIT_BUTTON)],
            [KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _video_i2v_keyboard(*, resolution: str = "720p", duration: int = 2, use_grok: bool = True, prompt_extend: bool = False) -> ReplyKeyboardMarkup:
    resolution = "1080p" if str(resolution or "").strip() == "1080p" else "720p"
    duration = int(duration or 2)
    if duration not in {2, 5, 8, 15}:
        duration = 2
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{VIDEO_I2V_RES_PREFIX}{resolution}"), KeyboardButton(text=f"{VIDEO_I2V_DURATION_PREFIX}{duration}秒")],
            [KeyboardButton(text=VIDEO_I2V_GROK_ON if use_grok else VIDEO_I2V_GROK_OFF), KeyboardButton(text=VIDEO_I2V_EXTEND_ON if prompt_extend else VIDEO_I2V_EXTEND_OFF)],
            [KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _video_i2v_inline_keyboard(*, resolution: str = "720p", duration: int = 2, use_grok: bool = True, prompt_extend: bool = False) -> InlineKeyboardMarkup:
    resolution = "1080p" if str(resolution or "").strip() == "1080p" else "720p"
    duration = int(duration or 2)
    if duration not in {2, 5, 8, 15}:
        duration = 2
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"分辨率：{resolution}", callback_data="video_i2v:toggle_resolution"),
                InlineKeyboardButton(text=f"时长：{duration}秒", callback_data="video_i2v:cycle_duration"),
            ],
            [
                InlineKeyboardButton(text=f"Grok提示词：{'开' if use_grok else '关'}", callback_data="video_i2v:toggle_grok"),
                InlineKeyboardButton(text=f"接口扩写：{'开' if prompt_extend else '关'}", callback_data="video_i2v:toggle_extend"),
            ],
            [InlineKeyboardButton(text="下一步：上传参考图", callback_data="video_i2v:ready_image")],
            [InlineKeyboardButton(text="返回主菜单", callback_data="video_i2v:main_menu")],
        ]
    )


def _digital_human_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=DIGITAL_HUMAN_REALISTIC_BUTTON), KeyboardButton(text=DIGITAL_HUMAN_LIVE_BUTTON)],
            [KeyboardButton(text=DIGITAL_HUMAN_PRODUCT_BUTTON), KeyboardButton(text=DIGITAL_HUMAN_CUSTOM_BUTTON)],
            [KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _message_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()


def _strip_prompt_char_count_note(text: str, *, preserve_english: bool = False) -> str:
    cleaned = re.sub(
        r"[（(]\s*(?:共\s*)?(?:字符数|字数|汉字数)?\s*[：:]?\s*约?\s*\d+\s*(?:个\s*)?(?:中文)?(?:字符|汉字|字)?[^）)]*[）)]",
        "",
        str(text or ""),
    )
    cleaned = re.sub(
        r"(?i)\b(?:drafting|crafting|creating|generating|rewriting|optimizing|translating|converting)\b\s+(?:the\s+)?(?:image\s+|final\s+|text\s+)?(?:prompt|description|request)\s*[:：,，。；;.\-]?\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(r"^分析[^，。；、\n]{0,40}提示词(?:要求|并生成[^，。；、\n]{0,40}(?:描述|正文|内容))?\s*", "", cleaned)
    if not preserve_english:
        cleaned = re.sub(r"[A-Za-z][A-Za-z0-9'/_-]*", "", cleaned)
    precise_replacements = {
        "身体张力明显": "身体重心前移，手部位置明确，衣物开合状态清晰",
        "挑逗氛围": "身体前倾，手指靠近衣物边缘，暖色床头灯照在皮肤和床面",
        "挑逗姿势": "身体前倾，手指靠近衣物开口或大腿内侧",
        "挑逗": "身体前倾，手指靠近衣物边缘",
        "诱惑姿势": "衣物半开，身体侧向镜头，手部停在大腿内侧",
        "诱惑": "衣物半开，身体侧向镜头",
        "暧昧氛围": "暖色床头灯照在皮肤和床面",
        "暧昧": "暖色侧光照在皮肤和布料上",
        "氛围": "光线、场景物件和身体姿势",
        "张力": "身体重心、手部位置和衣物开合状态",
        "高级真实摄影质感": "真实皮肤纹理、布料褶皱、浅景深和柔和侧光",
        "高级摄影质感": "真实皮肤纹理、布料褶皱、浅景深和柔和侧光",
        "高级质感": "真实纹理、浅景深和柔和侧光",
        "福利感": "明确的裸露范围和半身构图",
        "私密福利": "室内半身构图",
        "视线避开镜头": "脸部清晰可见",
        "人物不露脸": "人物脸部清晰可见",
        "头部自然入镜": "脸部清晰可见",
        "不合常理的破洞": "纽扣自然解开，布料沿身体曲线滑落",
        "破洞": "纽扣自然解开，布料沿身体曲线滑落",
        "破口": "衣物边缘自然打开",
        "洞口": "衣物开口",
        "撕裂": "衣物自然松开",
        "撕破": "衣物自然松开",
        "撕开": "衣物自然解开",
        "布料缺失": "衣物开合状态清晰",
        "避开镜头": "脸部清晰可见",
        "不露脸": "脸部清晰可见",
        "遮住脸": "脸部清晰可见",
        "遮脸": "脸部清晰可见",
        "裁掉头部": "脸部清晰可见",
        "头部裁切": "脸部清晰可见",
        "面部避开": "脸部清晰可见",
        "面部遮挡": "脸部清晰可见",
        "脸部遮挡": "脸部清晰可见",
        "脸部无遮挡": "脸部清晰可见",
        "脸部清晰进入画面没有遮挡": "脸部清晰可见",
        "脸部清晰进入画面且无遮挡": "脸部清晰可见",
        "脸部清晰进入画面": "脸部清晰可见",
        "脸部没有遮挡": "脸部清晰可见",
        "露出脸部不遮挡": "脸部清晰可见",
        "脸部不遮挡": "脸部清晰可见",
        "清晰露出脸部无任何遮挡": "脸部清晰可见",
        "露出脸部无任何遮挡": "脸部清晰可见",
        "脸部无任何遮挡": "脸部清晰可见",
        "近景构图": "半身构图，镜头距离拉开，头顶保留少量留白",
        "室内近景": "室内半身构图，镜头距离拉开，头顶保留少量留白",
        "低角度特写": "平视半身构图，镜头距离拉开，头顶保留少量留白",
        "私密部位特写": "半身构图，镜头距离拉开，头顶保留少量留白",
        "静态特写": "半身构图，镜头距离拉开，头顶保留少量留白",
        "特写": "半身构图，镜头距离拉开，头顶保留少量留白",
    }
    for source, replacement in precise_replacements.items():
        cleaned = cleaned.replace(source, replacement)
    cleanup_replacements = {
        "性器官区域可见或在场景允许时完全裸露": "性器官区域完整裸露，边界清晰可见，衣物没有遮挡该区域",
        "阴部可见或在场景允许时完全裸露": "阴部完整裸露，边界清晰可见，衣物没有遮挡该区域",
        "阴茎可见或在场景允许时完全裸露": "阴茎完整裸露，边界清晰可见，衣物没有遮挡该区域",
        "性器官区域可见或完全裸露": "性器官区域完整裸露，边界清晰可见，衣物没有遮挡该区域",
        "阴部可见或完全裸露": "阴部完整裸露，边界清晰可见，衣物没有遮挡该区域",
        "阴茎可见或完全裸露": "阴茎完整裸露，边界清晰可见，衣物没有遮挡该区域",
        "裸露必须来自合理服装状态和身体姿势": "纽扣解开、拉链松开、衣摆掀起，服装结构完整",
        "精简写入脸型、眉眼、唇形和表情状态": "保留表情状态",
        "最终提示词只保留其中最关键的三到五个脸部特征和一个表情状态": "",
        "不要整段堆叠": "",
        "保留用户要求的服装、场景和道具": "用户指定服装、场景和道具",
        "用户指定服装、场景和道具": "原设服装、场景和道具",
        "裸露只能来自自然开扣、拉链松开、衣摆掀起、肩带滑落、裙摆上移、腰头下拉、布料贴身或半脱状态": "纽扣解开、拉链松开、衣摆掀起、肩带滑落、裙摆上移、腰头下拉、布料贴身或半脱",
        "禁止为了裸露强行制造破洞、撕裂、破口、布料凭空消失、不合受力逻辑的开口": "服装结构完整",
        "构图必须能看到人物脸部": "脸部清晰可见",
        "人物脸部需要精简描述": "脸部清晰可见",
        "允许写脸型、肤质、眉眼、鼻梁、嘴唇和表情": "保留表情状态",
        "金君雅": "",
        "人设1": "",
        "捞女1": "",
        "当前人设": "人物",
        "人设脸部": "脸部",
        "人设名称": "",
        "人物名称": "",
        "名字": "",
        "忠实匹配用户指定主体": "人物主体",
        "用户指定主体": "人物主体",
        "单帧静态画面": "静态摄影画面",
        "明确写出身体朝向、手放置位置、衣物开合状态、镜头距离、半身或全身构图、脸部清晰可见、脸部特征和裸露范围": "身体朝向镜头，手部位置明确，衣物开合状态清晰，半身或全身构图，脸部清晰可见",
        "脸部特征和裸露范围": "脸部清晰可见和裸露范围",
        "明确的情色裸露": "",
        "根据场景动态判断": "",
        "或在场景允许时": "",
        "在场景允许时": "",
        "场景允许": "",
        "若隐若现": "清晰可见",
        "边缘可见": "边界清晰可见",
        "部分遮挡": "无遮挡",
        "明确写出身体朝向、手放置位置、衣物开合状态、镜头距离、头部自然入镜和裸露范围": "身体朝向镜头，手部位置明确，衣物开合状态清晰，镜头距离为半身或全身构图，脸部清晰可见",
        "明确写出身体朝向、手放置位置、衣物开合状态、镜头距离、脸部完整露出且无遮挡、头部自然入镜和裸露范围": "身体朝向镜头，手部位置明确，衣物开合状态清晰，镜头距离为半身或全身构图，脸部清晰可见",
        "禁止凭空纽扣自然解开，布料沿身体曲线滑落、衣物自然松开、衣物边缘自然打开和不合受力逻辑的衣物开合状态清晰": "服装结构完整，纽扣或拉链自然解开，布料沿身体曲线滑落",
        "禁止凭空破坏服装结构": "服装结构完整",
        "禁止凭空": "",
        "不合受力逻辑的": "",
        "保留用户要求的服装、场景和道具": "用户指定服装、场景和道具",
        "头部自然进入画面但不描述表情状态": "脸部清晰可见，表情自然",
        "头部自然进入画面但不描述五官": "脸部清晰可见",
    }
    for source, replacement in cleanup_replacements.items():
        cleaned = cleaned.replace(source, replacement)
    cleaned = cleaned.replace("视线头部自然入镜", "脸部清晰可见")
    cleaned = cleaned.replace("出现纽扣自然解开", "纽扣自然解开")
    cleaned = cleaned.replace("卧，室", "卧室")
    cleaned = cleaned.replace("解，开", "解开")
    cleaned = cleaned.replace("皮肤，和布料", "皮肤和布料")
    cleaned = cleaned.replace("可见或", "完整裸露，")
    cleaned = re.sub(r"(?:Character Setting|人设\d*|捞女\d*|金君雅|人设名称|人物名称|名称)[\\/\w\u4e00-\u9fff.-]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"裸露程度[，、\s]*", "", cleaned)
    cleaned = re.sub(r"(?:例如|比如)[，、\s]*", "", cleaned)
    cleaned = cleaned.replace("明确写出", "")
    cleaned = re.sub(r"(?:例如|比如)[^，。；、\n]{0,80}?(?:禁止|不要|必须|允许)[^，。；、\n]{0,80}", "", cleaned)
    cleaned = re.sub(r"(?:必须|禁止|不要|允许|需要|只保留|保留)[^，。；、\n]{0,80}?(?:提示词|规则|字段|描述|写入|来自)[^，。；、\n]{0,80}", "", cleaned)
    cleaned = cleaned.replace("低角度，", "")
    cleaned = cleaned.replace("低角度", "平视角度")
    cleaned = cleaned.replace("头部自然进入画面", "脸部清晰可见")
    cleaned = cleaned.replace("头部自然入镜", "脸部清晰可见")
    cleaned = re.sub(r"(头部完整入镜[，、\s]*){2,}", "头部完整入镜，", cleaned)
    cleaned = re.sub(r"(脸部完整露出且无遮挡[，、\s]*){2,}", "脸部完整露出且无遮挡，", cleaned)
    cleaned = re.sub(r"(头顶额头下巴都在画面内[，、\s]*){2,}", "头顶额头下巴都在画面内，", cleaned)
    cleaned = re.sub(r"(镜头距离拉开[，、\s]*){2,}", "镜头距离拉开，", cleaned)
    cleaned = re.sub(r"(头顶保留少量留白[，、\s]*){2,}", "头顶保留少量留白，", cleaned)
    cleaned = re.sub(r"(半身构图[，、\s]*){2,}", "半身构图，", cleaned)
    if preserve_english:
        cleaned = re.sub(r"[\w\u4e00-\u9fff .\\/-]*\.safetensors", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?:Character Setting|人设\d*|捞女\d*|金君雅|人设名称|人物名称|名称)[^,，。;\n]*", "", cleaned, flags=re.IGNORECASE)
        face_feature_patterns = [
            r"\b(?:with|wearing|having)?\s*(?:(?:long|short|medium|shoulder[-\s]?length|wavy|curly|straight|black|dark|light|brown|blonde|golden|silver|white|silver-white|gray|grey|red|pink|blue|purple|messy|neat|loose|tied|braided|flowing|silky)[-\s]+)+hair\b",
            r"\b(?:hair\s+)?(?:color|colour)\s+[^,.;，。；、\n]+",
            r"\b(?:hairstyle|haircut|bangs|fringe|ponytail|twin\s*tails?|braids?)\b",
            r"\b(?:oval|round|small|soft|delicate|slim|v-shaped|heart-shaped)\s+face(?:\s+shape)?\b",
            r"\b(?:fair|white|pale|delicate|glowing|water|smooth)\s+(?:facial\s+)?skin\b",
            r"\b(?:soft\s+)?apple\s+cheeks?\b",
            r"\b(?:bright|large|clear|natural|slender|long|beautiful|almond|phoenix|double-lidded)\s+(?:almond\s+)?eyes\b",
            r"\b(?:clear\s+)?double\s+eyelids?\b",
            r"\b(?:natural|slender|long|arched|thin)\s+eyebrows?\b",
            r"\b(?:long|slender|curled|thick)\s+eyelashes?\b",
            r"\b(?:straight|small|delicate|high)\s+nose(?:\s+bridge|\s+tip)?\b",
            r"\b(?:narrow|small)\s+nostrils?\b",
            r"\b(?:pink|rosy|full|plump|soft|clear)(?:\s+(?:pink|rosy|full|plump|soft|clear))*\s+lips?\b",
            r"\b(?:clear\s+)?lip\s+shape\b",
            r"\b(?:soft|defined|clean)\s+jawline\b",
            r"\b(?:round|small)\s+chin\b",
            r"\b(?:full|smooth)\s+forehead\b",
            r"\b(?:natural|clean|delicate)\s+makeup\b",
        ]
        for pattern in face_feature_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"(?:鹅蛋脸|脸型|白皙水光肌|苹果肌|杏仁眼|双眼皮|卧蚕|睫毛|眉毛|眉形|鼻梁|鼻头|鼻翼|嘴唇|唇形|唇峰|下颌线|下巴|额头|妆感|发型|头发)",
            "",
            cleaned,
        )
        cleaned = re.sub(r"[#@$%^&_=+<>\[\]{}|~`]+", "", cleaned)
        cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
        cleaned = re.sub(r"(?:,\s*){2,}", ", ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned.strip(" ,.;:\n\t")
    cleaned = cleaned.replace(",", "，").replace(";", "；").replace(":", "：")
    cleaned = re.sub(r"[\\/*#@$%^&_=+<>\[\]{}|~`]+", "", cleaned)
    cleaned = re.sub(r"[\"'“”‘’]+", "", cleaned)
    cleaned = re.sub(r"[()\uFF08\uFF09]+", "", cleaned)
    cleaned = re.sub(r"(?<!\d)[.\-]+(?!\d)", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([，。；、])", r"\1", cleaned)
    cleaned = re.sub(r"[，、]{2,}", "，", cleaned)
    cleaned = re.sub(r"([，。；、])\s*([，。；、])+", r"\1", cleaned)
    if cleaned and ("近景" in cleaned or "特写" in cleaned) and "半身构图" not in cleaned and "全身构图" not in cleaned:
        cleaned = f"{cleaned}，半身构图，镜头距离拉开"
    return cleaned.strip(" ，。；、,.;\n\t ")


def _telegram_prompt_chinese_preview(prompt_text: str) -> str:
    text = str(prompt_text or "").strip()
    if not text:
        return ""
    phrase_map = [
        ("a half body portrait", "半身人像构图"),
        ("a full body portrait", "全身人像构图"),
        ("half body portrait", "半身人像构图"),
        ("full body portrait", "全身人像构图"),
        ("full body composition", "全身构图"),
        ("half body composition", "半身构图"),
        ("full body visible", "全身可见"),
        ("head and face clearly unobstructed", "头部和脸部无遮挡、清晰可见"),
        ("face clearly visible", "脸部清晰可见"),
        ("head fully in frame", "头部完整入镜"),
        ("facing the viewer", "面向观看者"),
        ("facing the camera", "面向镜头"),
        ("eyes looking at the camera", "视线看向镜头"),
        ("looking at the camera", "看向镜头"),
        ("direct eye contact", "直视镜头"),
        ("mouth slightly open", "嘴部微张"),
        ("natural expression", "自然表情"),
        ("neutral expression", "自然平静的表情"),
        ("soft indoor light", "柔和室内光线"),
        ("soft warm bedroom lighting", "柔和暖色卧室光线"),
        ("soft side light", "柔和侧光"),
        ("side lamps", "侧边台灯"),
        ("warm bedside light", "暖色床头灯"),
        ("shallow depth of field", "浅景深"),
        ("realistic skin texture", "真实皮肤纹理"),
        ("natural fabric folds", "自然布料褶皱"),
        ("fabric folds", "布料褶皱"),
        ("body curves", "身体曲线"),
        ("subtle shadows on curves", "身体曲线带有细腻阴影"),
        ("stable anatomy", "人体结构稳定"),
        ("high quality photography", "高质量摄影质感"),
        ("high resolution", "高分辨率"),
        ("intricate details", "细节丰富"),
        ("masterpiece", "高完成度画面"),
        ("best quality", "最佳画质"),
        ("cinematic lighting", "电影感光线"),
        ("photorealistic", "真实摄影风格"),
        ("realistic", "写实风格"),
        ("luxurious bedroom", "豪华卧室"),
        ("bedroom", "卧室"),
        ("indoor", "室内"),
        ("studio", "棚拍空间"),
        ("camera", "镜头"),
        ("front facing", "正面朝向"),
        ("body facing the camera", "身体朝向镜头"),
        ("body slightly angled but fully framed", "身体轻微侧向但完整入镜"),
        ("wearing a", "穿着"),
        ("wearing", "穿着"),
        ("with", ""),
        ("from", "来自"),
        ("hands placed", "手部放置"),
        ("hands resting", "双手自然放置"),
        ("one hand", "一只手"),
        ("both hands", "双手"),
        ("partially open", "半开状态"),
        ("silk blouse", "丝质上衣"),
        ("short tight skirt", "短款紧身裙"),
        ("button undone", "纽扣解开"),
        ("buttons undone", "纽扣解开"),
        ("clothing naturally loosened", "服装自然松开"),
        ("unbuttoned", "纽扣解开"),
        ("zipper loosened", "拉链松开"),
        ("hem lifted", "衣摆掀起"),
        ("skirt lifted", "裙摆上移"),
        ("skirt moved upward", "裙摆上移"),
        ("shoulder strap slipped", "肩带滑落"),
        ("waistband pulled down", "腰头下拉"),
        ("clear clothing state", "衣物状态清晰"),
        ("detailed composition", "构图细节清晰"),
        ("soft background", "柔和背景"),
        ("clean background", "干净背景"),
        ("natural pose", "自然姿态"),
        ("standing", "站立"),
        ("slightly parted", "轻微分开"),
        ("legs slightly parted", "双腿轻微分开"),
        ("inner thighs", "大腿内侧"),
        ("standing pose", "站立姿态"),
        ("sitting pose", "坐姿"),
        ("kneeling pose", "跪姿"),
        ("lying pose", "躺姿"),
    ]
    clauses = [part.strip(" ,.;:\n\t") for part in re.split(r"[,;]\s*", text) if part.strip(" ,.;:\n\t")]
    rendered: list[str] = []
    for clause in clauses:
        item = clause
        for source, target in phrase_map:
            item = re.sub(re.escape(source), target, item, flags=re.IGNORECASE)
        item = re.sub(r"^(?:a|an|the)\s+", "", item, flags=re.IGNORECASE)
        item = re.sub(r"\s{2,}", " ", item).strip(" ,.;:\n\t")
        rendered.append(item)
    return "，".join(rendered).strip("，。；、,.;\n\t ")


def _chat_identity_text(message: Message) -> str:
    user = message.from_user
    username = f"@{user.username}" if user and user.username else ""
    full_name = " ".join(
        part for part in [getattr(user, "first_name", "") if user else "", getattr(user, "last_name", "") if user else ""] if part
    ).strip()
    lines = [
        "你的 Telegram 身份信息：",
        f"chat_id: {int(message.chat.id)}",
    ]
    if username:
        lines.append(f"username: {username}")
    if full_name:
        lines.append(f"name: {full_name}")
    lines.extend(
        [
            "",
            "请把上面的 chat_id 添加到后台「可信 TG 用户」，不要填写机器人 ID。",
        ]
    )
    return "\n".join(lines)


def _is_text(message: Message, *values: str) -> bool:
    return _message_text(message) in set(values)


def _load_runtime_config(config: AppConfig) -> dict[str, Any]:
    path = config.runtime_config_path
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read runtime workflow config: %s", path)
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_workflow_chain(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        parts = value
    else:
        text = str(value or "")
        for needle in ("->", ">", "，", "\n", "\r", ";"):
            text = text.replace(needle, ",")
        parts = text.split(",")
    result: list[str] = []
    for part in parts:
        workflow_id = str(part or "").strip()
        if workflow_id and workflow_id not in result:
            result.append(workflow_id)
    return result


def _workflow_chain(runtime: dict[str, Any], key: str, fallback: list[Any]) -> list[str]:
    chain = _normalize_workflow_chain(runtime.get(key))
    if chain:
        return chain
    return _normalize_workflow_chain(fallback)


def _format_chain(label: str, workflow_ids: list[str]) -> str:
    if not workflow_ids:
        return f"{label}: 未配置"
    return f"{label}: {' > '.join(workflow_ids)}"


def _workflow_config_text(service: WorkspaceService, selected_button: str = "") -> str:
    selected_button = _canonical_button_text(selected_button)
    config = service.resolve_config()
    runtime = _load_runtime_config(config)
    oral_chain = _workflow_chain(
        runtime,
        "oral_digital_human_workflow_ids",
        [config.audio_workflow_id, config.video_workflow_id],
    )
    image_chain = _workflow_chain(
        runtime,
        "image_generate_workflow_ids",
        [runtime.get("image_runninghub_workflow_id")],
    )
    replace_model_original_chain = _workflow_chain(
        runtime,
        "replace_model_original_workflow_ids",
        [runtime.get("replace_model_original_app_id") or runtime.get("replace_model_app_id")],
    )
    replace_model_primary_chain = _workflow_chain(
        runtime,
        "replace_model_primary_workflow_ids",
        [runtime.get("replace_model_primary_app_id")],
    )
    replace_model_slice_chain = _workflow_chain(
        runtime,
        "replace_model_slice_workflow_ids",
        [runtime.get("replace_model_slice_app_id")],
    )
    replace_model_motion_chain = _workflow_chain(
        runtime,
        "replace_model_motion_transfer_workflow_ids",
        [runtime.get("replace_model_motion_transfer_app_id")],
    )
    replace_product_chain = _workflow_chain(
        runtime,
        "replace_product_workflow_ids",
        [runtime.get("replace_product_app_id")],
    )
    replace_union_model_chain = _workflow_chain(
        runtime,
        "replace_union_model_workflow_ids",
        replace_model_original_chain,
    )
    replace_union_product_chain = _workflow_chain(
        runtime,
        "replace_union_product_workflow_ids",
        replace_product_chain,
    )

    selected_note = ""
    legacy_button_labels = {
        LEGACY_IMAGE_WORKFLOW_BUTTON: IMAGE_WORKFLOW_BUTTON,
        LEGACY_IMAGE_GENERATE_WORKFLOW_BUTTON: IMAGE_WORKFLOW_BUTTON,
        LEGACY_REPLACE_MODEL_WORKFLOW_BUTTON: REPLACE_MODEL_WORKFLOW_BUTTON,
        LEGACY_REPLACE_PRODUCT_WORKFLOW_BUTTON: REPLACE_PRODUCT_WORKFLOW_BUTTON,
    }
    display_selected_button = legacy_button_labels.get(selected_button, selected_button)
    if display_selected_button and display_selected_button != WORKFLOW_CONFIG_BUTTON:
        selected_note = f"你选择的是「{display_selected_button}」。"

    if display_selected_button and display_selected_button != WORKFLOW_CONFIG_BUTTON:
        selected_map = {
            IMAGE_WORKFLOW_BUTTON: _format_chain("图像生成", image_chain),
            REPLACE_MODEL_WORKFLOW_BUTTON: _format_chain("视频模特替换", replace_model_original_chain),
            REPLACE_PRODUCT_WORKFLOW_BUTTON: _format_chain("视频商品替换", replace_product_chain),
            REPLACE_UNION_WORKFLOW_BUTTON: "\n".join(
                [
                    _format_chain("联合替换·视频模特链", replace_union_model_chain),
                    _format_chain("联合替换·视频商品链", replace_union_product_chain),
                ]
            ),
        }
        return "\n".join(
            [
                f"你选择的是「{display_selected_button}」。",
                selected_map.get(display_selected_button, "").strip(),
                "",
                "这是生产工作流入口。",
                "请按面板提示依序上传素材；提交后可按「查看工作台状态」跟进进度。",
                f"工作台网址: {config.public_base_url}",
            ]
        ).strip()

    return "\n".join(
        [
            "后台工作流配置：",
            _format_chain("口播数字人工作流", oral_chain),
            _format_chain("图像生成", image_chain),
            _format_chain("视频模特替换", replace_model_original_chain),
            _format_chain("视频商品替换", replace_product_chain),
            _format_chain("联合替换·视频模特链", replace_union_model_chain),
            _format_chain("联合替换·视频商品链", replace_union_product_chain),
            "",
            selected_note,
            "TG 面板可直接建立任务：图像生成、视频生成。",
            f"工作台网址: {config.public_base_url}",
        ]
    ).strip()


def _quick_start_text(service: WorkspaceService) -> str:
    return "\n".join(
        [
            f"🌟 {service.get_app_title()} 已启动",
            "",
            "🌟 可用工作流",
            f"1. {IMAGE_WORKFLOW_BUTTON}",
            "   点击后进入文生图。",
            f"2. {VIDEO_EDIT_BUTTON}",
            "   点击后选择图生视频，可用按钮切换分辨率、时长、Grok 提示词和接口扩写。",
            "",
            "🌟 直接对话",
            "也可以发送 /status 查看后台任务进度，发送 /stop 停止当前任务。",
            "",
            "🌟 常用操作",
            f"- {RERUN_BUTTON}：重跑最近一次任务。",
            f"- {STATUS_BUTTON}：查看任务进度。",
            f"- {STOP_BUTTON} 或 /stop：强制停止目前任务。",
            "",
            "✨ 详细执行纪录请到工作台任务详情查看。",
        ]
    )


def _video_ext_from_message(message: Message) -> str | None:
    if message.video:
        file_name = (message.video.file_name or "").strip()
        suffix = Path(file_name).suffix.lower() if file_name else ".mp4"
        return suffix if suffix in VIDEO_EXTS else ".mp4"
    if message.document:
        suffix = Path(message.document.file_name or "").suffix.lower()
        if suffix in VIDEO_EXTS:
            return suffix
    return None


def _image_ext_from_message(message: Message) -> str | None:
    if message.photo:
        return ".jpg"
    if message.document:
        suffix = Path(message.document.file_name or "").suffix.lower()
        if suffix in IMAGE_EXTS:
            return suffix
    return None


def _agent_file_ext_from_message(message: Message) -> tuple[str, str] | None:
    video_suffix = _video_ext_from_message(message)
    if video_suffix:
        return video_suffix, "video"
    image_suffix = _image_ext_from_message(message)
    if image_suffix:
        return image_suffix, "image"
    if message.document:
        suffix = Path(message.document.file_name or "").suffix.lower()
        if suffix in ZIP_EXTS:
            return suffix, "zip"
    return None


def _parse_duration_seconds(text: str) -> int | None:
    value = str(text or "").strip()
    if not value:
        raise ValueError("秒数不能为空")
    if value in AUTO_DURATION_TEXTS:
        return None
    seconds = math.ceil(float(value))
    if seconds <= 0:
        raise ValueError("秒数必須大於 0")
    return seconds


async def _download_message_media(message: Message, target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    downloadable = None
    if message.video:
        downloadable = message.video
    elif message.photo:
        downloadable = message.photo[-1]
    elif message.document:
        downloadable = message.document
    else:
        raise RuntimeError("这則讯息没有可下载的媒体文件")
    await message.bot.download(downloadable, destination=target_path)
    return target_path


async def _download_agent_message_file(message: Message, work_dir: Path) -> dict[str, str] | None:
    detected = _agent_file_ext_from_message(message)
    if detected is None:
        return None
    suffix, kind = detected
    if message.document and message.document.file_name:
        raw_name = Path(message.document.file_name).name
    elif message.video and message.video.file_name:
        raw_name = Path(message.video.file_name).name
    else:
        raw_name = f"telegram_{kind}{suffix}"
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw_name).strip("._-") or f"telegram_{kind}{suffix}"
    if not Path(safe_name).suffix:
        safe_name = f"{safe_name}{suffix}"
    target = work_dir / safe_name
    await _download_message_media(message, target)
    return {"name": safe_name, "path": str(target.resolve()), "kind": kind}


def _internal_webapp_base_url() -> str:
    return str(os.getenv("TG_INTERNAL_WEBAPP_BASE_URL") or "http://127.0.0.1:8091").strip().rstrip("/")


async def _submit_internal_webapp_task(
    *,
    chat_id: int,
    task_type: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    token = str(os.getenv("TG_INTERNAL_API_TOKEN") or "").strip()
    if token:
        headers["x-tg-internal-token"] = token
    url = f"{_internal_webapp_base_url()}/api/internal/tg/submit"
    async with ClientSession() as session:
        async with session.post(
            url,
            json={"task_type": str(task_type), "tg_chat_id": int(chat_id), "params": dict(params or {})},
            headers=headers,
            timeout=30,
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"后台任务提交失败 HTTP {response.status}: {body[:500]}")
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"后台任务提交返回非 JSON: {body[:300]}") from exc
    if not isinstance(data, dict) or not data.get("id"):
        raise RuntimeError(f"后台任务提交返回缺少任务 ID: {data}")
    return data


async def _preview_internal_webapp_prompt(
    *,
    chat_id: int,
    task_type: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    token = str(os.getenv("TG_INTERNAL_API_TOKEN") or "").strip()
    if token:
        headers["x-tg-internal-token"] = token
    url = f"{_internal_webapp_base_url()}/api/internal/tg/prompt_preview"
    try:
        async with ClientSession() as session:
            async with session.post(
                url,
                json={"task_type": str(task_type), "tg_chat_id": int(chat_id), "params": dict(params or {})},
                headers=headers,
                timeout=TG_PROMPT_PREVIEW_TIMEOUT_SECONDS,
            ) as response:
                body = await response.text()
                if response.status >= 400:
                    detail = ""
                    try:
                        error_data = json.loads(body)
                        if isinstance(error_data, dict):
                            detail = str(
                                error_data.get("detail")
                                or error_data.get("message")
                                or error_data.get("error")
                                or ""
                            ).strip()
                    except json.JSONDecodeError:
                        detail = ""
                    raise RuntimeError(
                        f"后台 Grok 提示词生成失败 HTTP {response.status}: {(detail or body)[:500]}"
                    )
                try:
                    data = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"后台 Grok 提示词生成返回非 JSON: {body[:300]}") from exc
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"后台 Grok 提示词生成超时（超过 {TG_PROMPT_PREVIEW_TIMEOUT_SECONDS} 秒）。"
            "通常是 Grok 响应慢、供应商排队，或提示词被二次校验重试拖长。"
        ) from exc
    except ClientError as exc:
        raise RuntimeError(f"连接后台 Grok 提示词服务失败：{exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"后台 Grok 提示词生成返回格式异常: {data}")
    prompt_text = str(data.get("prompt_text") or "").strip()
    if not prompt_text:
        raise RuntimeError("Grok 未返回可用提示词")
    return data


async def _display_internal_webapp_prompt(
    *,
    chat_id: int,
    task_type: str,
    prompt_text: str,
) -> str:
    prompt_text = str(prompt_text or "").strip()
    if not prompt_text:
        return ""
    headers: dict[str, str] = {}
    token = str(os.getenv("TG_INTERNAL_API_TOKEN") or "").strip()
    if token:
        headers["x-tg-internal-token"] = token
    url = f"{_internal_webapp_base_url()}/api/internal/tg/prompt_display"
    async with ClientSession() as session:
        async with session.post(
            url,
            json={"task_type": str(task_type), "tg_chat_id": int(chat_id), "prompt_text": prompt_text},
            headers=headers,
            timeout=120,
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"后台提示词中文预览失败 HTTP {response.status}: {body[:500]}")
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"后台提示词中文预览返回非 JSON: {body[:300]}") from exc
    if not isinstance(data, dict):
        return ""
    return str(data.get("display_text") or "").strip()


async def _submit_internal_webapp_agent_task(
    *,
    chat_id: int,
    message_text: str,
    files: list[dict[str, str]],
    duration_seconds: int = 15,
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    token = str(os.getenv("TG_INTERNAL_API_TOKEN") or "").strip()
    if token:
        headers["x-tg-internal-token"] = token
    url = f"{_internal_webapp_base_url()}/api/internal/tg/agent_submit"
    async with ClientSession() as session:
        async with session.post(
            url,
            json={
                "message": str(message_text or "").strip(),
                "tg_chat_id": int(chat_id),
                "files": list(files or []),
                "use_ai_copy": True,
                "duration_seconds": int(duration_seconds or 15),
            },
            headers=headers,
            timeout=45,
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"后台智能提交失败 HTTP {response.status}: {body[:500]}")
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"后台智能提交返回非 JSON: {body[:300]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"后台智能提交返回格式异常: {data}")
    if data.get("submitted") is False:
        return data
    if not data.get("id"):
        raise RuntimeError(f"后台智能提交返回缺少任务 ID: {data}")
    return data


async def _fetch_internal_webapp_tg_tasks(*, chat_id: int, limit: int = 5) -> list[dict[str, Any]]:
    headers: dict[str, str] = {}
    token = str(os.getenv("TG_INTERNAL_API_TOKEN") or "").strip()
    if token:
        headers["x-tg-internal-token"] = token
    url = f"{_internal_webapp_base_url()}/api/internal/tg/tasks"
    async with ClientSession() as session:
        async with session.get(
            url,
            params={"chat_id": int(chat_id), "limit": int(limit or 5)},
            headers=headers,
            timeout=20,
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"后台 TG 任务查询失败 HTTP {response.status}: {body[:500]}")
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"后台 TG 任务查询返回非 JSON: {body[:300]}") from exc
    tasks = data.get("tasks") if isinstance(data, dict) else None
    return [item for item in tasks if isinstance(item, dict)] if isinstance(tasks, list) else []


async def _fetch_internal_webapp_tg_task_detail(*, chat_id: int, task_id: str) -> dict[str, Any]:
    headers: dict[str, str] = {}
    token = str(os.getenv("TG_INTERNAL_API_TOKEN") or "").strip()
    if token:
        headers["x-tg-internal-token"] = token
    tid = str(task_id or "").strip()
    url = f"{_internal_webapp_base_url()}/api/internal/tg/tasks/{tid}"
    async with ClientSession() as session:
        async with session.get(
            url,
            params={"chat_id": int(chat_id)},
            headers=headers,
            timeout=20,
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"后台 TG 任务详情查询失败 HTTP {response.status}: {body[:500]}")
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"后台 TG 任务详情返回非 JSON: {body[:300]}") from exc
    task = data.get("task") if isinstance(data, dict) else None
    if not isinstance(task, dict):
        raise RuntimeError(f"后台 TG 任务详情格式异常: {data}")
    return task


def _format_internal_webapp_tg_tasks(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "后台生成任务：暂无记录。"
    status_labels = {
        "queued": "排队中",
        "running": "生成中",
        "success": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
    }
    lines = ["后台生成任务："]
    for item in tasks[:5]:
        status = str(item.get("status") or "").strip()
        label = status_labels.get(status, status or "unknown")
        download = "，有结果文件" if item.get("has_download") else ""
        error = str(item.get("error") or "").strip()
        if len(error) > 80:
            error = f"{error[:80]}..."
        suffix = f"，{error}" if status == "failed" and error else download
        lines.append(f"- {item.get('type')}: {label}{suffix}（{item.get('id')}）")
    return "\n".join(lines)


def build_dispatcher(config: AppConfig, service: WorkspaceService) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    router = Router(name="workspace-bot")
    dispatcher.include_router(router)
    chat_script_drafts: dict[int, str] = {}

    async def ensure_authorized(message: Message) -> bool:
        if service.is_chat_authorized(int(message.chat.id)):
            return True
        await message.answer(
            "\n".join(
                [
                    "你的 Telegram 账号还没有加入后台可信用户。",
                    "",
                    _chat_identity_text(message),
                ]
            )
        )
        return False

    async def start_upload_flow(message: Message, state: FSMContext, requirement: str = "") -> None:
        work_dir = service.create_job_dir(prefix="tg")
        await state.clear()
        await state.set_state(UploadFlowForm.waiting_for_video)
        await state.update_data(work_dir=str(work_dir), digital_human_requirement=str(requirement or "").strip())
        await message.answer(
            "\n".join(
                [
                    "🌟 数字人视频生成",
                    f"已选择：{requirement}" if requirement else "请先上传素材，后续会交给 Grok 生成提示词。",
                    "步骤 1/4：上传原视频",
                    "",
                    "✨ 用途：运镜与首帧参考。",
                    "可以直接传视频，也可以当成 document 传送。",
                ]
            ),
            reply_markup=_menu_keyboard(),
        )

    async def handle_entry_keyword(message: Message, state: FSMContext) -> bool:
        text = _canonical_button_text(_message_text(message))
        if text not in {
            "多智能体数字人",
            IMAGE_WORKFLOW_BUTTON,
            TEXT_TO_IMAGE_BUTTON,
            MULTI_IMAGE_BUTTON,
            IMAGE_REPLACE_BUTTON,
            VIDEO_EDIT_BUTTON,
            VIDEO_GENERAL_EDIT_BUTTON,
            MAIN_MENU_BUTTON,
        }:
            return False
        if not await ensure_authorized(message):
            return True
        await state.clear()
        if text == MAIN_MENU_BUTTON:
            await message.answer("已返回主菜单。", reply_markup=_menu_keyboard())
        elif text == IMAGE_WORKFLOW_BUTTON:
            await start_image_generate_flow(message, state)
        elif text == TEXT_TO_IMAGE_BUTTON:
            await start_text_to_image_flow(message, state)
        elif text == MULTI_IMAGE_BUTTON:
            await start_image_reference_flow(message, state, mode="multi_image")
        elif text == IMAGE_REPLACE_BUTTON:
            await start_image_reference_flow(message, state, mode="image_replace")
        elif text == VIDEO_EDIT_BUTTON:
            await message.answer("视频生成：请选择要建立的任务。", reply_markup=_video_edit_keyboard())
        elif text == VIDEO_GENERAL_EDIT_BUTTON:
            await start_video_i2v_flow(message, state)
        else:
            await message.answer(_quick_start_text(service), reply_markup=_menu_keyboard())
        return True

    async def handle_workflow_reference_request(message: Message, state: FSMContext | None = None) -> bool:
        text = _canonical_button_text(_message_text(message))
        if text not in WORKFLOW_REFERENCE_BUTTONS:
            return False
        if not await ensure_authorized(message):
            return True
        if state is not None:
            await state.clear()
        await message.answer(_workflow_config_text(service, selected_button=text), reply_markup=_menu_keyboard())
        return True

    async def handle_stop_request(message: Message, state: FSMContext) -> bool:
        text = _canonical_button_text(_message_text(message))
        if text != STOP_BUTTON and not text.startswith("/stop"):
            return False
        if not await ensure_authorized(message):
            return True
        await state.clear()

        active_task = service.store.get_active_task()
        target_task = active_task or service.get_latest_open_task_for_submitter(int(message.chat.id))
        if target_task is None:
            await message.answer("目前没有可强制停止的任务。", reply_markup=_menu_keyboard())
            return True

        result = await service.cancel_task(target_task.id, requested_by=f"TG-{int(message.chat.id)}")
        await message.answer(result.message, reply_markup=_menu_keyboard())
        return True

    async def enqueue_request(
        message: Message,
        request: WorkflowRequest,
        *,
        source: str,
        is_default_assets: bool,
    ) -> None:
        service.submit_task(
            request=request,
            submitter_chat_id=int(message.chat.id),
            source=source,
            is_default_assets=is_default_assets,
        )

    async def submit_webapp_task_and_reply(message: Message, task_type: str, params: dict[str, Any]) -> None:
        result = await _submit_internal_webapp_task(
            chat_id=int(message.chat.id),
            task_type=task_type,
            params=params,
        )
        await message.answer(
            "\n".join(
                part
                for part in [
                    "任务已提交到后台队列。",
                    f"工作流: {task_type}",
                    f"任务编号: {result.get('id')}",
                    f"Grok 生成提示词: {str(result.get('prompt_preview') or '').strip()}" if str(result.get("prompt_preview") or "").strip() else "",
                    "可按「查看工作台状态」跟进进度。",
                ]
                if part
            ),
            reply_markup=_menu_keyboard(),
        )

    async def answer_status(message: Message) -> None:
        parts: list[str] = []
        try:
            tasks = await _fetch_internal_webapp_tg_tasks(chat_id=int(message.chat.id), limit=5)
            parts.append(_format_internal_webapp_tg_tasks(tasks))
        except Exception as exc:
            parts.append(f"后台生成任务：查询失败（{exc}）")
        legacy_status = service.get_status_text(chat_id=int(message.chat.id))
        if legacy_status:
            parts.append(legacy_status)
        await message.answer("\n\n".join(parts), reply_markup=_menu_keyboard())

    async def start_image_generate_flow(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "图像生成：请选择要执行的图片模式。",
            reply_markup=_image_edit_keyboard(),
        )

    def _image_mode_title(mode: str) -> str:
        return "图片替换" if mode == "image_replace" else "多图生成"

    async def start_image_reference_flow(message: Message, state: FSMContext, *, mode: str) -> None:
        mode = "image_replace" if mode == "image_replace" else "multi_image"
        title = _image_mode_title(mode)
        await state.clear()
        await state.set_state(ProductionWorkflowForm.image_waiting_for_product_image)
        await state.update_data(image_mode=mode, work_dir=str(service.create_job_dir(prefix=f"tg_{mode}")))
        first_step = "请上传原图。" if mode == "image_replace" else "请上传第一张参考图。"
        await message.answer(
            f"{title}\n步骤 1/3：{first_step}",
            reply_markup=_image_edit_keyboard(),
        )

    def _video_i2v_defaults() -> dict[str, Any]:
        return {"resolution": "720p", "duration": 2, "use_grok": True, "prompt_extend": False}

    def _video_i2v_status_text(*, step: str, resolution: str, duration: int, use_grok: bool, prompt_extend: bool) -> str:
        return "\n".join(
            [
                "图生视频设置",
                f"当前步骤：{step}",
                f"分辨率：{resolution}",
                f"时长：{duration}秒",
                f"Grok提示词：{'开启，会识别参考图并用中文改写最终提示词' if use_grok else '关闭'}",
                f"接口扩写：{'开启' if prompt_extend else '关闭'}",
            ]
        )

    def _video_i2v_state_params(data: dict[str, Any]) -> dict[str, Any]:
        params = _video_i2v_defaults()
        params.update({k: data.get(k) for k in params.keys() if k in data})
        params["resolution"] = "1080p" if str(params.get("resolution") or "").strip() == "1080p" else "720p"
        params["duration"] = int(params.get("duration") or 2)
        if params["duration"] not in {2, 5, 8, 15}:
            params["duration"] = 2
        params["use_grok"] = bool(params.get("use_grok"))
        params["prompt_extend"] = bool(params.get("prompt_extend"))
        return params

    async def _video_i2v_step_text(state: FSMContext, *, fallback: str = "1/2 调整参数，然后上传参考图") -> str:
        current_state = await state.get_state()
        if current_state == ProductionWorkflowForm.video_i2v_waiting_for_prompt.state:
            return "2/2 已收到参考图，请输入视频需求"
        if current_state == ProductionWorkflowForm.video_i2v_waiting_for_image.state:
            return "1/2 调整参数，然后上传参考图"
        return fallback

    async def _try_delete_message(message: Message) -> None:
        try:
            await message.delete()
        except Exception:
            pass

    async def _remove_reply_keyboard(message: Message, *, text: str = "请使用上方按钮调整参数。") -> None:
        try:
            sent = await message.answer(text, reply_markup=ReplyKeyboardRemove())
            await sent.delete()
        except Exception:
            pass

    async def _edit_video_i2v_control_message(message: Message, state: FSMContext, *, step: str) -> None:
        data = await state.get_data()
        params = _video_i2v_state_params(data)
        text = _video_i2v_status_text(step=step, **params)
        markup = _video_i2v_inline_keyboard(**params)
        control_message_id = int(data.get("control_message_id") or 0)
        if control_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=int(message.chat.id),
                    message_id=control_message_id,
                    text=text,
                    reply_markup=markup,
                )
                return
            except Exception:
                pass
        sent = await message.answer(text, reply_markup=markup)
        await state.update_data(control_message_id=int(sent.message_id))

    async def _edit_video_i2v_control_from_callback(callback: CallbackQuery, state: FSMContext, *, step: str) -> None:
        data = await state.get_data()
        params = _video_i2v_state_params(data)
        text = _video_i2v_status_text(step=step, **params)
        markup = _video_i2v_inline_keyboard(**params)
        if callback.message:
            try:
                await callback.message.edit_text(text, reply_markup=markup)
                await state.update_data(control_message_id=int(callback.message.message_id))
                return
            except Exception:
                pass

    async def _answer_video_i2v_prompt(message: Message, state: FSMContext, *, text: str) -> None:
        await _edit_video_i2v_control_message(message, state, step=text)

    async def _handle_video_i2v_param_button(message: Message, state: FSMContext) -> bool:
        text = _message_text(message)
        if not text:
            return False
        data = await state.get_data()
        params = _video_i2v_state_params(data)
        changed = False
        if text.startswith(VIDEO_I2V_RES_PREFIX):
            params["resolution"] = "1080p" if str(params["resolution"]) == "720p" else "720p"
            changed = True
        elif text.startswith(VIDEO_I2V_DURATION_PREFIX):
            order = [2, 5, 8, 15]
            current = int(params.get("duration") or 2)
            params["duration"] = order[(order.index(current) + 1) % len(order)] if current in order else 2
            changed = True
        elif text in {VIDEO_I2V_GROK_ON, VIDEO_I2V_GROK_OFF}:
            params["use_grok"] = not bool(params["use_grok"])
            changed = True
        elif text in {VIDEO_I2V_EXTEND_ON, VIDEO_I2V_EXTEND_OFF}:
            params["prompt_extend"] = not bool(params["prompt_extend"])
            changed = True
        if not changed:
            return False
        await state.update_data(**params)
        await _edit_video_i2v_control_message(message, state, step=await _video_i2v_step_text(state))
        await _try_delete_message(message)
        return True

    async def start_video_i2v_flow(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(ProductionWorkflowForm.video_i2v_waiting_for_image)
        await state.update_data(**_video_i2v_defaults())
        await _remove_reply_keyboard(message, text="请使用上方按钮调整图生视频参数。")
        await _edit_video_i2v_control_message(message, state, step="1/2 调整参数，然后上传参考图")

    async def _submit_video_i2v_from_state(message: Message, state: FSMContext, prompt: str) -> None:
        data = await state.get_data()
        image_path = str(data.get("image_local_path") or "").strip()
        if not image_path:
            await state.set_state(ProductionWorkflowForm.video_i2v_waiting_for_image)
            await _answer_video_i2v_prompt(message, state, text="缺少参考图，请先上传一张图片。")
            return
        params = _video_i2v_defaults()
        params.update({k: data.get(k) for k in params.keys() if k in data})
        payload = {
            "image_local_path": image_path,
            "prompt": prompt,
            "prompt_text": prompt,
            "message": prompt,
            "resolution": str(params["resolution"]),
            "duration_seconds": int(params["duration"]),
            "mulerouter_wan_i2v_resolution": str(params["resolution"]),
            "mulerouter_wan_i2v_duration": int(params["duration"]),
            "mulerouter_wan_i2v_prompt_extend": bool(params["prompt_extend"]),
            "tg_use_llm_prompt": bool(params["use_grok"]),
            "tg_user_instruction": f"用户图生视频需求：{prompt}",
        }
        await state.clear()
        try:
            result = await _submit_internal_webapp_task(
                chat_id=int(message.chat.id),
                task_type="video_i2v",
                params=payload,
            )
            reply = "\n".join(
                part
                for part in [
                    "图生视频任务已提交。",
                    f"任务编号：{result.get('id')}",
                    f"Grok最终提示词：{str(result.get('prompt_preview') or '').strip()}" if str(result.get("prompt_preview") or "").strip() else "",
                    "生成完成后会自动把视频发回这里。",
                ]
                if part
            )
            control_message_id = int(data.get("control_message_id") or 0)
            if control_message_id:
                try:
                    await message.bot.edit_message_text(chat_id=int(message.chat.id), message_id=control_message_id, text=reply)
                    return
                except Exception:
                    pass
            await message.answer(reply, reply_markup=_menu_keyboard())
        except Exception as exc:
            await message.answer(f"图生视频任务提交失败：{exc}", reply_markup=_menu_keyboard())

    @router.callback_query(F.data.startswith("video_i2v:"))
    async def on_video_i2v_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None:
            await callback.answer()
            return
        if not service.is_chat_authorized(int(callback.message.chat.id)):
            await callback.answer("当前账号未授权", show_alert=True)
            return
        data_value = str(callback.data or "")
        current = await state.get_data()
        params = _video_i2v_state_params(current)
        if data_value.endswith(":main_menu"):
            await state.clear()
            try:
                await callback.message.edit_text("已返回主菜单。")
            except Exception:
                pass
            await callback.message.answer("请选择任务类型。", reply_markup=_menu_keyboard())
            await callback.answer()
            return
        if data_value.endswith(":toggle_resolution"):
            params["resolution"] = "1080p" if params["resolution"] == "720p" else "720p"
        elif data_value.endswith(":cycle_duration"):
            order = [2, 5, 8, 15]
            params["duration"] = order[(order.index(int(params["duration"])) + 1) % len(order)]
        elif data_value.endswith(":toggle_grok"):
            params["use_grok"] = not bool(params["use_grok"])
        elif data_value.endswith(":toggle_extend"):
            params["prompt_extend"] = not bool(params["prompt_extend"])
        elif data_value.endswith(":ready_image"):
            await state.set_state(ProductionWorkflowForm.video_i2v_waiting_for_image)
            await state.update_data(**params, control_message_id=int(callback.message.message_id))
            await _edit_video_i2v_control_from_callback(callback, state, step="1/2 请上传参考图")
            await callback.answer("请上传参考图")
            return
        await state.update_data(**params, control_message_id=int(callback.message.message_id))
        await _edit_video_i2v_control_from_callback(callback, state, step="1/2 调整参数，然后上传参考图")
        await callback.answer("已更新")

    async def start_replace_model_flow(message: Message, state: FSMContext) -> None:
        work_dir = service.create_job_dir(prefix="tg_replace_model")
        await state.clear()
        await state.set_state(ProductionWorkflowForm.replace_model_waiting_for_video)
        await state.update_data(work_dir=str(work_dir))
        await message.answer("🌟 视频模特替换\n步骤 1/4：请上传原视频。", reply_markup=_menu_keyboard())

    async def start_replace_product_flow(message: Message, state: FSMContext) -> None:
        work_dir = service.create_job_dir(prefix="tg_replace_product")
        await state.clear()
        await state.set_state(ProductionWorkflowForm.replace_product_waiting_for_video)
        await state.update_data(work_dir=str(work_dir))
        await message.answer("🌟 视频商品替换\n步骤 1/5：请上传原视频。", reply_markup=_menu_keyboard())

    async def start_union_flow(message: Message, state: FSMContext) -> None:
        work_dir = service.create_job_dir(prefix="tg_union")
        await state.clear()
        await state.set_state(ProductionWorkflowForm.union_waiting_for_video)
        await state.update_data(work_dir=str(work_dir))
        await message.answer("🌟 联合替换工作流\n步骤 1/5：请上传原视频。", reply_markup=_menu_keyboard())

    async def start_text_to_image_flow(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_ratio)
        params = _text_to_image_params()
        await state.update_data(
            aspect_ratio=params["aspect_ratio"],
            width=params["width"],
            height=params["height"],
            final_resolution_enabled=bool(params["final_resolution_enabled"]),
            persona_available=bool(params["persona_available"]),
            persona_enabled=bool(params["persona_enabled"]),
            persona_lora=str(params["persona_lora"] or ""),
            ratio_selected=False,
            resolution_selected=False,
            persona_selected=False,
            prompt_mode_selected=False,
            prompt_mode_label="",
        )
        await _remove_reply_keyboard(message, text="请按步骤选择文生图参数。")
        sent = await message.answer(
            _text_to_image_status_text(step="1/4 请选择图像比例", params=params),
            reply_markup=_text_to_image_ratio_keyboard(),
        )
        await state.update_data(t2i_control_message_id=int(sent.message_id))

    async def _show_text_to_image_prompt_review(message: Message, state: FSMContext, *, prompt_text: str, selected_model: str = "") -> None:
        data = await state.get_data()
        params = _text_to_image_params(data)
        clean_prompt_text = _strip_prompt_char_count_note(prompt_text, preserve_english=True)
        display_prompt_text = clean_prompt_text
        if clean_prompt_text:
            try:
                display_prompt_text = (
                    await _display_internal_webapp_prompt(
                        chat_id=int(message.chat.id),
                        task_type="text_to_image",
                        prompt_text=clean_prompt_text,
                    )
                ) or clean_prompt_text
            except Exception:
                display_prompt_text = _telegram_prompt_chinese_preview(clean_prompt_text) or clean_prompt_text
        text = "\n\n".join(
            [
                "文生图 3/3：Grok 已生成最终提示词。",
                f"画面比例：{params['aspect_ratio']}，基础分辨率：{params['width']} x {params['height']}，最终分辨率：{'开启，预计 ' + params['final'] if params.get('final_resolution_enabled') else '关闭'}",
                f"人设 LoRA：{params.get('persona_label') or '使用人设'}" if params.get("persona_enabled") else ("人设 LoRA：不使用" if params.get("persona_available") else ""),
                f"模型：{selected_model or 'Grok'}",
                "最终提示词：",
                display_prompt_text,
                "你可以直接使用，也可以继续告诉 Grok 如何调整。",
            ]
        )
        await message.answer(text, reply_markup=_text_to_image_prompt_keyboard())

    async def _preview_text_to_image_prompt(
        message: Message,
        state: FSMContext,
        *,
        user_request: str,
        original_user_request: str | None = None,
        latest_only: bool = True,
    ) -> None:
        data = await state.get_data()
        params = _text_to_image_params(data)
        original_for_state = str(original_user_request or data.get("original_user_request") or user_request).strip()
        await state.update_data(
            original_user_request=original_for_state,
            last_grok_user_request=str(user_request or "").strip(),
            final_prompt_text="",
            selected_model="",
            custom_prompt_used=False,
        )
        generation_context = (
            f"画面比例：{params['aspect_ratio']}，基础分辨率：{params['width']} x {params['height']}，"
            f"最终分辨率：{'开启，预计 ' + params['final'] if params.get('final_resolution_enabled') else '关闭，使用基础分辨率'}，"
            f"人设 LoRA：{params.get('persona_label') or '使用人设' if params.get('persona_enabled') else '不使用'}。"
        )
        payload = {
            "prompt": user_request,
            "prompt_text": user_request,
            "message": user_request,
            "width": params["width"],
            "height": params["height"],
            "aspect_ratio": params["aspect_ratio"],
            "final_resolution_enabled": bool(params["final_resolution_enabled"]),
            "persona_enabled": bool(params["persona_enabled"]),
            "persona_lora": str(params.get("persona_lora") or ""),
            "persona_label": str(params.get("persona_label") or ""),
            "tg_use_llm_prompt": True,
            "tg_latest_prompt_only": bool(latest_only),
            "tg_preserve_original_prompt": False,
            "tg_original_user_request": original_for_state,
            "tg_generation_context": generation_context,
            "tg_user_instruction": user_request,
        }
        await message.answer("正在让 Grok 生成最终提示词...")
        result = await _preview_internal_webapp_prompt(chat_id=int(message.chat.id), task_type="text_to_image", params=payload)
        prompt_text = _strip_prompt_char_count_note(str(result.get("prompt_text") or "").strip(), preserve_english=True)
        selected_model = str(result.get("selected_model") or "").strip()
        await state.update_data(
            original_user_request=original_for_state,
            final_prompt_text=prompt_text,
            selected_model=selected_model,
        )
        await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_revision)
        await _show_text_to_image_prompt_review(message, state, prompt_text=prompt_text, selected_model=selected_model)

    async def _submit_text_to_image_from_state(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        params = _text_to_image_params(data)
        final_prompt = _strip_prompt_char_count_note(str(data.get("final_prompt_text") or "").strip(), preserve_english=True)
        if not final_prompt:
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
            await message.answer("还没有可用的 Grok 提示词，请先输入图片需求。")
            return
        payload = {
            "prompt": final_prompt,
            "prompt_text": final_prompt,
            "message": final_prompt,
            "width": params["width"],
            "height": params["height"],
            "aspect_ratio": params["aspect_ratio"],
            "final_resolution_enabled": bool(params["final_resolution_enabled"]),
            "persona_enabled": bool(params["persona_enabled"]),
            "persona_lora": str(params.get("persona_lora") or ""),
            "persona_label": str(params.get("persona_label") or ""),
            "tg_use_llm_prompt": False,
            "tg_llm_prompt_enhanced": True,
            "tg_original_prompt": str(data.get("original_user_request") or "").strip(),
            "tg_llm_rewritten_prompt": final_prompt,
            "tg_llm_selected_model": str(data.get("selected_model") or "").strip(),
            "custom_prompt_used": bool(data.get("custom_prompt_used")),
        }
        payload["remote_comfy_node_inputs"] = _text_to_image_remote_node_inputs(params)
        await state.clear()
        await submit_webapp_task_and_reply(message, "text_to_image", payload)

    async def _show_text_to_image_prompt_entry(message: Message, state: FSMContext) -> None:
        await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
        params = _text_to_image_params(await state.get_data())
        step = "4/4 请输入图片需求" if params.get("persona_available") else "3/3 请输入图片需求"
        await message.answer(
            _text_to_image_status_text(step=step, params=params)
            + "\n\n请直接发送图片需求，Grok 会生成最终提示词供你确认。",
            reply_markup=_text_to_image_prompt_entry_keyboard(),
        )

    async def _show_text_to_image_prompt_mode(message: Message, state: FSMContext) -> None:
        await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode)
        params = _text_to_image_params(await state.get_data())
        step = "4/4 请选择提示词方式" if params.get("persona_available") else "3/3 请选择提示词方式"
        await message.answer(
            _text_to_image_status_text(step=step, params=params)
            + "\n\n请选择让 Grok 根据你的需求生成提示词，或直接输入自定义最终提示词。",
            reply_markup=_text_to_image_prompt_mode_keyboard(),
        )

    @router.callback_query(F.data.startswith("t2i:"))
    async def on_text_to_image_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None:
            await callback.answer()
            return
        if not service.is_chat_authorized(int(callback.message.chat.id)):
            await callback.answer("当前账号未授权", show_alert=True)
            return
        action = str(callback.data or "")
        data = await state.get_data()
        if action == "t2i:main_menu":
            await state.clear()
            try:
                await callback.message.edit_text("已返回主菜单。")
            except Exception:
                pass
            await callback.message.answer("请选择任务类型。", reply_markup=_menu_keyboard())
            await callback.answer()
            return
        if action.startswith("t2i:continue:"):
            task_id = action.rsplit(":", 1)[-1].strip()
            try:
                task = await _fetch_internal_webapp_tg_task_detail(chat_id=int(callback.message.chat.id), task_id=task_id)
            except Exception as exc:
                await callback.answer(f"读取上次任务失败：{exc}", show_alert=True)
                return
            if str(task.get("type") or "").strip() != "text_to_image":
                await callback.answer("这个任务不是文生图任务", show_alert=True)
                return
            input_payload = task.get("input") if isinstance(task.get("input"), dict) else {}
            params = _text_to_image_params(input_payload)
            await state.clear()
            await state.update_data(
                aspect_ratio=params["aspect_ratio"],
                width=params["width"],
                height=params["height"],
                final_resolution_enabled=bool(input_payload.get("final_resolution_enabled", params["final_resolution_enabled"])),
                persona_available=bool(params["persona_available"]),
                persona_enabled=bool(input_payload.get("persona_enabled", params["persona_enabled"])),
                persona_lora=str(input_payload.get("persona_lora") or params.get("persona_lora") or ""),
                ratio_selected=True,
                resolution_selected=True,
                persona_selected=bool(params["persona_available"]),
                prompt_mode_selected=False,
                prompt_mode_label="",
                original_user_request="",
                final_prompt_text="",
                selected_model="",
                custom_prompt_used=False,
            )
            try:
                await callback.message.edit_caption(caption="继续生成图片：保留上次参数，重新进入提示词步骤。")
            except Exception:
                try:
                    await callback.message.edit_text("继续生成图片：保留上次参数，重新进入提示词步骤。")
                except Exception:
                    pass
            await _show_text_to_image_prompt_mode(callback.message, state)
            await callback.answer("请继续输入提示词")
            return
        if action.startswith("t2i:ratio:"):
            ratio = action.split(":", 2)[-1]
            if ratio in TEXT_TO_IMAGE_RATIO_OPTIONS:
                option = _text_to_image_params({**data, "aspect_ratio": ratio})
                current_params = _text_to_image_params(data)
                final_enabled = bool(current_params["final_resolution_enabled"])
                option["final_resolution_enabled"] = final_enabled
                option["persona_enabled"] = bool(current_params["persona_enabled"])
                await state.update_data(
                    aspect_ratio=ratio,
                    width=option["width"],
                    height=option["height"],
                    final_resolution_enabled=final_enabled,
                    persona_available=bool(option["persona_available"]),
                    persona_enabled=bool(option["persona_enabled"]),
                    persona_lora=str(option.get("persona_lora") or ""),
                    ratio_selected=True,
                    resolution_selected=False,
                    persona_selected=False,
                    prompt_mode_selected=False,
                    prompt_mode_label="",
                )
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_resolution)
                option["ratio_selected"] = True
                option["resolution_selected"] = False
                option["persona_selected"] = False
                option["prompt_mode_selected"] = False
                option["prompt_mode_label"] = ""
                try:
                    await callback.message.edit_text(f"已选择图像比例：{option['label']}（{option['width']} x {option['height']}）。")
                except Exception:
                    pass
                await callback.message.answer(
                    _text_to_image_status_text(step="2/4 请选择最终分辨率", params=option),
                    reply_markup=_text_to_image_resolution_keyboard(final_resolution_enabled=final_enabled),
                )
                await callback.answer("请选择分辨率")
                return
            await callback.answer("无效比例", show_alert=True)
            return
        if action == "t2i:next:resolution":
            params = _text_to_image_params(data)
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_resolution)
            try:
                await callback.message.edit_text(f"已选择图像比例：{params['label']}（{params['width']} x {params['height']}）。")
            except Exception:
                pass
            await callback.message.answer(
                _text_to_image_status_text(step="2/4 请选择最终分辨率", params=params),
                reply_markup=_text_to_image_resolution_keyboard(
                    final_resolution_enabled=bool(params["final_resolution_enabled"]),
                    selected=bool(params.get("resolution_selected")),
                ),
            )
            await callback.answer("请选择分辨率")
            return
        if action == "t2i:back:ratio":
            params = _text_to_image_params(data)
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_ratio)
            try:
                await callback.message.edit_text("已返回上一步：图像比例。")
            except Exception:
                pass
            await callback.message.answer(
                _text_to_image_status_text(step="1/4 请选择图像比例", params=params),
                reply_markup=_text_to_image_ratio_keyboard(
                    selected_ratio=params["aspect_ratio"] if params.get("ratio_selected") else ""
                ),
            )
            await callback.answer("已返回比例")
            return
        if action == "t2i:choose_prompt_mode":
            await _show_text_to_image_prompt_mode(callback.message, state)
            await callback.answer("请选择提示词方式")
            return
        if action == "t2i:ready_prompt":
            await state.update_data(prompt_mode_selected=True, prompt_mode_label="Grok 生成")
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
            try:
                await callback.message.edit_text("已选择：让 Grok 生成提示词。")
            except Exception:
                pass
            await callback.message.answer(
                "请输入图片需求，Grok 会根据你的要求生成最终提示词供你确认。",
                reply_markup=_text_to_image_prompt_entry_keyboard(),
            )
            await callback.answer("请输入图片需求")
            return
        if action.startswith("t2i:final:") or action == "t2i:toggle_final":
            params = _text_to_image_params(data)
            if action == "t2i:toggle_final":
                final_enabled = not bool(params["final_resolution_enabled"])
            else:
                final_enabled = action.endswith(":on")
            await state.update_data(
                final_resolution_enabled=final_enabled,
                resolution_selected=True,
                persona_selected=False,
                prompt_mode_selected=False,
                prompt_mode_label="",
            )
            params = _text_to_image_params(
                {
                    **data,
                    "final_resolution_enabled": final_enabled,
                    "resolution_selected": True,
                    "persona_selected": False,
                    "prompt_mode_selected": False,
                    "prompt_mode_label": "",
                }
            )
            try:
                await callback.message.edit_text(
                    f"已选择最终分辨率：{'开启，预计 ' + params['final'] if final_enabled else '关闭，使用基础分辨率'}。"
                )
            except Exception:
                pass
            if params.get("persona_available"):
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_persona)
                await callback.message.answer(
                    _text_to_image_status_text(step="3/4 请选择人设 LoRA", params=params),
                    reply_markup=_text_to_image_persona_keyboard(
                        persona_enabled=bool(params["persona_enabled"]),
                        persona_lora=str(params.get("persona_lora") or ""),
                        selected=bool(params.get("persona_selected")),
                    ),
                )
                await callback.answer("请选择人设")
            else:
                await _show_text_to_image_prompt_mode(callback.message, state)
                await callback.answer("请选择提示词方式")
            return
        if action == "t2i:next:persona":
            params = _text_to_image_params(data)
            if params.get("persona_available"):
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_persona)
                try:
                    await callback.message.edit_text(
                        f"已选择最终分辨率：{'开启，预计 ' + params['final'] if params.get('final_resolution_enabled') else '关闭，使用基础分辨率'}。"
                    )
                except Exception:
                    pass
                await callback.message.answer(
                    _text_to_image_status_text(step="3/4 请选择人设 LoRA", params=params),
                    reply_markup=_text_to_image_persona_keyboard(
                        persona_enabled=bool(params["persona_enabled"]),
                        persona_lora=str(params.get("persona_lora") or ""),
                        selected=bool(params.get("persona_selected")),
                    ),
                )
                await callback.answer("请选择人设")
            else:
                await _show_text_to_image_prompt_mode(callback.message, state)
                await callback.answer("请选择提示词方式")
            return
        if action == "t2i:back:resolution":
            params = _text_to_image_params(data)
            await state.update_data(persona_selected=False, prompt_mode_selected=False, prompt_mode_label="")
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_resolution)
            params = _text_to_image_params(
                {
                    **data,
                    "persona_selected": False,
                    "prompt_mode_selected": False,
                    "prompt_mode_label": "",
                }
            )
            try:
                await callback.message.edit_text("已返回上一步：最终分辨率。")
            except Exception:
                pass
            await callback.message.answer(
                _text_to_image_status_text(step="2/4 请选择最终分辨率", params=params),
                reply_markup=_text_to_image_resolution_keyboard(
                    final_resolution_enabled=bool(params["final_resolution_enabled"]),
                    selected=bool(params.get("resolution_selected")),
                ),
            )
            await callback.answer("已返回分辨率")
            return
        if action.startswith("t2i:persona:"):
            persona_key = action.rsplit(":", 1)[-1]
            options = _text_to_image_persona_options()
            persona_enabled = persona_key != "off"
            selected_lora = ""
            if persona_enabled:
                for option in options:
                    if option["id"] == persona_key:
                        selected_lora = option["path"]
                        break
                if not selected_lora:
                    await callback.answer("没有找到这个人设", show_alert=True)
                    return
            await state.update_data(
                persona_enabled=persona_enabled,
                persona_lora=selected_lora or _text_to_image_default_persona_path(),
                persona_selected=True,
                prompt_mode_selected=False,
                prompt_mode_label="",
            )
            params = _text_to_image_params(
                {
                    **data,
                    "persona_enabled": persona_enabled,
                    "persona_lora": selected_lora,
                    "persona_selected": True,
                    "prompt_mode_selected": False,
                    "prompt_mode_label": "",
                }
            )
            try:
                await callback.message.edit_text(
                    f"已选择人设 LoRA：{params.get('persona_label') if params.get('persona_enabled') else '不使用'}。",
                )
            except Exception:
                pass
            await _show_text_to_image_prompt_mode(callback.message, state)
            await callback.answer("请选择提示词方式")
            return
        if action == "t2i:next:prompt":
            params = _text_to_image_params(data)
            try:
                await callback.message.edit_text(
                    f"已选择人设 LoRA：{params.get('persona_label') if params.get('persona_enabled') else '不使用'}。",
                )
            except Exception:
                pass
            await _show_text_to_image_prompt_mode(callback.message, state)
            await callback.answer("请选择提示词方式")
            return
        if action == "t2i:back:prompt_mode":
            try:
                await callback.message.edit_text("已返回上一步：提示词方式。")
            except Exception:
                pass
            await _show_text_to_image_prompt_mode(callback.message, state)
            await callback.answer("已返回提示词方式")
            return
        if action == "t2i:back:persona":
            params = _text_to_image_params(data)
            await state.update_data(prompt_mode_selected=False, prompt_mode_label="")
            params = _text_to_image_params({**data, "prompt_mode_selected": False, "prompt_mode_label": ""})
            if params.get("persona_available"):
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_persona)
                try:
                    await callback.message.edit_text("已返回上一步：人设 LoRA。")
                except Exception:
                    pass
                await callback.message.answer(
                    _text_to_image_status_text(step="3/4 请选择人设 LoRA", params=params),
                    reply_markup=_text_to_image_persona_keyboard(
                        persona_enabled=bool(params["persona_enabled"]),
                        persona_lora=str(params.get("persona_lora") or ""),
                        selected=bool(params.get("persona_selected")),
                    ),
                )
            else:
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_resolution)
                await callback.message.answer(
                    _text_to_image_status_text(step="2/3 请选择最终分辨率", params=params),
                    reply_markup=_text_to_image_resolution_keyboard(
                        final_resolution_enabled=bool(params["final_resolution_enabled"]),
                        selected=bool(params.get("resolution_selected")),
                    ),
                )
            await callback.answer("已返回上一步")
            return
        if action == "t2i:settings":
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_ratio)
            params = _text_to_image_params(data)
            await state.update_data(
                ratio_selected=False,
                resolution_selected=False,
                persona_selected=False,
                prompt_mode_selected=False,
                prompt_mode_label="",
            )
            params = _text_to_image_params(
                {
                    **data,
                    "ratio_selected": False,
                    "resolution_selected": False,
                    "persona_selected": False,
                    "prompt_mode_selected": False,
                    "prompt_mode_label": "",
                }
            )
            await callback.message.answer(
                _text_to_image_status_text(step="1/4 请重新选择图像比例", params=params),
                reply_markup=_text_to_image_ratio_keyboard(),
            )
            await callback.answer()
            return
        if action == "t2i:adjust":
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_revision)
            await callback.message.answer("请直接输入你希望 Grok 如何调整提示词，例如：更写实、换成夜景、保留人物姿势但改变服装。")
            await callback.answer()
            return
        if action == "t2i:custom_prompt":
            await state.update_data(prompt_mode_selected=True, prompt_mode_label="自定义输入")
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_custom_prompt)
            try:
                await callback.message.edit_text("已选择：输入自定义提示词。")
            except Exception:
                pass
            await callback.message.answer(
                "请输入自定义最终提示词。下一条消息会跳过 Grok，直接提交到 ComfyUI 工作流生成。",
                reply_markup=_text_to_image_prompt_entry_keyboard(),
            )
            await callback.answer()
            return
        if action == "t2i:regen":
            original = str(data.get("last_grok_user_request") or data.get("original_user_request") or data.get("final_prompt_text") or "").strip()
            if not original:
                await callback.answer("没有原始需求，请重新输入", show_alert=True)
                return
            try:
                await _preview_text_to_image_prompt(callback.message, state, user_request=original)
            except Exception as exc:
                await callback.message.answer(
                    f"Grok 提示词生成失败：{_format_grok_preview_error(exc)}",
                    reply_markup=_text_to_image_prompt_failure_keyboard(),
                )
            await callback.answer()
            return
        if action == "t2i:submit":
            try:
                await _submit_text_to_image_from_state(callback.message, state)
                await callback.answer("已提交生成")
            except Exception as exc:
                await callback.message.answer(f"文生图任务提交失败：{exc}", reply_markup=_menu_keyboard())
                await callback.answer()
            return

    @router.message(ProductionWorkflowForm.text_to_image_waiting_for_ratio)
    @router.message(ProductionWorkflowForm.text_to_image_waiting_for_resolution)
    @router.message(ProductionWorkflowForm.text_to_image_waiting_for_persona)
    @router.message(ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode)
    async def on_text_to_image_step_message(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        data = await state.get_data()
        params = _text_to_image_params(data)
        current_state = await state.get_state()
        if current_state == ProductionWorkflowForm.text_to_image_waiting_for_ratio.state:
            await message.answer(
                _text_to_image_status_text(step="1/4 请先选择图像比例", params=params),
                reply_markup=_text_to_image_ratio_keyboard(
                    selected_ratio=params["aspect_ratio"] if params.get("ratio_selected") else ""
                ),
            )
        elif current_state == ProductionWorkflowForm.text_to_image_waiting_for_resolution.state:
            await message.answer(
                _text_to_image_status_text(step="2/4 请先选择最终分辨率", params=params),
                reply_markup=_text_to_image_resolution_keyboard(
                    final_resolution_enabled=bool(params["final_resolution_enabled"]),
                    selected=bool(params.get("resolution_selected")),
                ),
            )
        elif current_state == ProductionWorkflowForm.text_to_image_waiting_for_persona.state:
            await message.answer(
                _text_to_image_status_text(step="3/4 请先选择人设 LoRA", params=params),
                reply_markup=_text_to_image_persona_keyboard(
                    persona_enabled=bool(params["persona_enabled"]),
                    persona_lora=str(params.get("persona_lora") or ""),
                    selected=bool(params.get("persona_selected")),
                ),
            )
        else:
            step = "4/4 请先选择提示词方式" if params.get("persona_available") else "3/3 请先选择提示词方式"
            await message.answer(
                _text_to_image_status_text(step=step, params=params),
                reply_markup=_text_to_image_prompt_mode_keyboard(),
            )

    @router.message(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
    async def on_text_to_image_prompt_v2(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        prompt = _message_text(message)
        if not prompt:
            data = await state.get_data()
            params = _text_to_image_params(data)
            await message.answer(
                _text_to_image_status_text(step="4/4 请输入图片需求" if params.get("persona_available") else "3/3 请输入图片需求", params=params),
                reply_markup=_text_to_image_prompt_entry_keyboard(),
            )
            return
        try:
            await _preview_text_to_image_prompt(message, state, user_request=prompt)
        except Exception as exc:
            params = _text_to_image_params(await state.get_data())
            await message.answer(
                f"Grok 提示词生成失败：{_format_grok_preview_error(exc)}",
                reply_markup=_text_to_image_prompt_failure_keyboard(),
            )

    @router.message(ProductionWorkflowForm.text_to_image_waiting_for_revision)
    async def on_text_to_image_revision(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        revision = _message_text(message)
        if not revision:
            await message.answer("请直接输入调整要求，或点击“使用这个提示词生成”。", reply_markup=_text_to_image_prompt_keyboard())
            return
        data = await state.get_data()
        original = str(data.get("original_user_request") or "").strip()
        current = str(data.get("final_prompt_text") or "").strip()
        combined = "\n".join(
            part
            for part in [
                f"原始需求：{original}" if original else "",
                f"当前提示词：{current}" if current else "",
                f"调整要求：{revision}",
                "请基于当前提示词按调整要求重写，保留用户明确要求，只输出最新版最终提示词。",
                "不要输出“原始需求/当前提示词/调整要求”等标签，不要重复旧提示词，不要把上面的上下文原文拼进结果。",
            ]
            if part
        )
        try:
            await _preview_text_to_image_prompt(
                message,
                state,
                user_request=combined,
                original_user_request=original or revision,
                latest_only=True,
            )
        except Exception as exc:
            await message.answer(
                f"Grok 提示词调整失败：{_format_grok_preview_error(exc)}",
                reply_markup=_text_to_image_prompt_failure_keyboard(),
            )

    @router.message(ProductionWorkflowForm.text_to_image_waiting_for_custom_prompt)
    async def on_text_to_image_custom_prompt(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        custom_prompt = _strip_prompt_char_count_note(_message_text(message), preserve_english=True)
        if not custom_prompt:
            await message.answer("请输入自定义最终提示词。", reply_markup=_text_to_image_prompt_entry_keyboard())
            return
        data = await state.get_data()
        await state.update_data(
            final_prompt_text=custom_prompt,
            selected_model="自定义提示词",
            original_user_request=str(data.get("original_user_request") or custom_prompt).strip(),
            custom_prompt_used=True,
        )
        try:
            await message.answer("已收到自定义提示词，正在提交生成。")
            await _submit_text_to_image_from_state(message, state)
        except Exception as exc:
            await message.answer(f"自定义提示词提交失败：{exc}", reply_markup=_text_to_image_prompt_keyboard())

    @router.message(Command("whoami"))
    @router.message(Command("id"))
    async def cmd_whoami(message: Message) -> None:
        await message.answer(_chat_identity_text(message))

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        if not await ensure_authorized(message):
            return
        await message.answer(_quick_start_text(service), reply_markup=_menu_keyboard())

    @router.message(F.text == "多智能体数字人")
    @router.message(F.text == "多智能體數字人")
    async def on_keyword_entry(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await state.clear()
        await message.answer(_quick_start_text(service), reply_markup=_menu_keyboard())

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        if not await ensure_authorized(message):
            return
        await answer_status(message)

    @router.message(Command("workflow"))
    async def cmd_workflow(message: Message) -> None:
        if not await ensure_authorized(message):
            return
        await message.answer(_workflow_config_text(service), reply_markup=_menu_keyboard())

    @router.message(Command("stop"))
    async def cmd_stop(message: Message, state: FSMContext) -> None:
        if await handle_stop_request(message, state):
            return

    @router.message(Command("workbench"))
    async def cmd_workbench(message: Message) -> None:
        if not await ensure_authorized(message):
            return
        await message.answer(
            f"工作台网址: {service.resolve_config().public_base_url}",
            reply_markup=_menu_keyboard(),
        )

    @router.message(Command("setscript"))
    async def cmd_setscript(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await state.clear()
        await state.set_state(ScriptForm.waiting_for_script)
        await message.answer("请直接贴上你想作为预设的文案内容。", reply_markup=_menu_keyboard())

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await state.clear()
        await message.answer("本次素材上传流程已取消。", reply_markup=_menu_keyboard())

    @router.message(Command("custom"))
    async def cmd_custom(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_upload_flow(message, state)

    @router.message(UploadFlowForm.waiting_for_custom_requirement)
    async def on_digital_human_custom_requirement(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if _canonical_button_text(_message_text(message)) == MAIN_MENU_BUTTON:
            await state.clear()
            await message.answer("已返回主菜单。", reply_markup=_menu_keyboard())
            return
        if not await ensure_authorized(message):
            return
        requirement = _message_text(message)
        if not requirement:
            await message.answer("请用一句话写出这次数字人视频的风格或要求。", reply_markup=_digital_human_keyboard())
            return
        await start_upload_flow(message, state, requirement=requirement)

    @router.message(Command("run"))
    async def cmd_run(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await state.clear()
        await message.answer(f"预设素材功能已移除。请使用「{DIGITAL_HUMAN_VIDEO_BUTTON}」建立数字人视频。", reply_markup=_menu_keyboard())

    @router.message(Command("rerun"))
    async def cmd_rerun(message: Message) -> None:
        if not await ensure_authorized(message):
            return
        latest_task = service.get_latest_task_for_submitter(int(message.chat.id))
        if latest_task is None:
            await message.answer("你目前还没有可重跑的历史任务。", reply_markup=_menu_keyboard())
            return
        request = service.clone_task_request(latest_task.id)
        await enqueue_request(message, request, source="telegram-rerun", is_default_assets=request.publish_to_default_paths)

    @router.message(ScriptForm.waiting_for_script)
    async def on_default_script_input(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_workflow_reference_request(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        script = _message_text(message)
        if not script:
            await message.answer("文案不能为空，请重新输入。", reply_markup=_menu_keyboard())
            return
        chat_script_drafts[int(message.chat.id)] = script
        await state.clear()
        await message.answer("你的预设文案已更新。", reply_markup=_menu_keyboard())

    @router.message(UploadFlowForm.waiting_for_video)
    async def on_upload_video(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_workflow_reference_request(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        suffix = _video_ext_from_message(message)
        if suffix is None:
            await message.answer("请上传视频文件，或把视频当成 document 传送。", reply_markup=_menu_keyboard())
            return
        params = {
            "speech_text": str(data["script_text"]),
            "prompt_text": prompt_text,
            "style_hint": portrait_prompt or "口播数字人写实人像",
            "duration_seconds": int(duration or 15),
            "use_ai_copy": False,
            "tg_use_llm_prompt": True,
            "tg_user_instruction": f"用户文生图需求：{prompt}",
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "text_to_image", params)
        except Exception as exc:
            await message.answer(f"文生图任务提交失败：{exc}", reply_markup=_menu_keyboard())

    @router.message(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
    async def on_text_to_image_prompt(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        prompt = _message_text(message)
        if not prompt:
            await message.answer("文生图\n步骤 1/1：请直接输入图片需求。", reply_markup=_image_edit_keyboard())
            return
        params = {
            "prompt": prompt,
            "prompt_text": prompt,
            "message": prompt,
            "tg_use_llm_prompt": True,
            "tg_user_instruction": f"用户文生图需求：{prompt}",
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "text_to_image", params)
        except Exception as exc:
            await message.answer(f"文生图任务提交失败：{exc}", reply_markup=_menu_keyboard())

    @router.message(ProductionWorkflowForm.image_waiting_for_product_image)
    async def on_image_first_reference(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        suffix = _image_ext_from_message(message)
        data = await state.get_data()
        mode = str(data.get("image_mode") or "multi_image")
        title = _image_mode_title(mode)
        if suffix is None:
            first_step = "请上传原图。" if mode == "image_replace" else "请上传第一张参考图。"
            await message.answer(f"{title}\n步骤 1/3：{first_step}", reply_markup=_image_edit_keyboard())
            return
        work_dir = Path(str(data.get("work_dir") or service.create_job_dir(prefix=f"tg_{mode}")))
        target = work_dir / f"primary{suffix}"
        await _download_message_media(message, target)
        await state.update_data(work_dir=str(work_dir), product_image_local_path=str(target.resolve()))
        await state.set_state(ProductionWorkflowForm.image_waiting_for_model_image)
        second_step = "请上传要替换成的参考图。" if mode == "image_replace" else "请上传第二张参考图。"
        await message.answer(f"{title}\n步骤 2/3：{second_step}", reply_markup=_image_edit_keyboard())

    @router.message(ProductionWorkflowForm.image_waiting_for_model_image)
    async def on_image_second_reference(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        suffix = _image_ext_from_message(message)
        data = await state.get_data()
        mode = str(data.get("image_mode") or "multi_image")
        title = _image_mode_title(mode)
        if suffix is None:
            second_step = "请上传要替换成的参考图。" if mode == "image_replace" else "请上传第二张参考图。"
            await message.answer(f"{title}\n步骤 2/3：{second_step}", reply_markup=_image_edit_keyboard())
            return
        work_dir = Path(str(data.get("work_dir") or service.create_job_dir(prefix=f"tg_{mode}")))
        target = work_dir / f"secondary{suffix}"
        await _download_message_media(message, target)
        await state.update_data(work_dir=str(work_dir), model_image_local_path=str(target.resolve()))
        await state.set_state(ProductionWorkflowForm.image_waiting_for_prompt)
        await message.answer(f"{title}\n步骤 3/3：请输入这次图片生成需求。", reply_markup=_image_edit_keyboard())

    @router.message(ProductionWorkflowForm.image_waiting_for_prompt)
    async def on_image_reference_prompt(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        prompt = _message_text(message)
        data = await state.get_data()
        mode = str(data.get("image_mode") or "multi_image")
        title = _image_mode_title(mode)
        if not prompt:
            await message.answer(f"{title}\n步骤 3/3：请直接输入这次图片生成需求。", reply_markup=_image_edit_keyboard())
            return
        params = {
            "product_image_local_path": str(data.get("product_image_local_path") or ""),
            "model_image_local_path": str(data.get("model_image_local_path") or ""),
            "prompt": prompt,
            "prompt_text": prompt,
            "message": prompt,
            "tg_use_llm_prompt": True,
            "tg_user_instruction": f"用户{title}需求：{prompt}",
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "image_generate", params)
        except Exception as exc:
            await message.answer(f"{title}任务提交失败：{exc}", reply_markup=_menu_keyboard())

    @router.message(ProductionWorkflowForm.video_i2v_waiting_for_image)
    async def on_video_i2v_image(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        if await _handle_video_i2v_param_button(message, state):
            return
        suffix = _image_ext_from_message(message)
        if suffix is None:
            await _answer_video_i2v_prompt(message, state, text="请上传一张参考图片，或点击按钮调整参数。")
            return
        data = await state.get_data()
        work_dir = Path(str(data.get("work_dir") or service.create_job_dir(prefix="tg_video_i2v")))
        target = work_dir / f"reference{suffix}"
        await _download_message_media(message, target)
        await state.update_data(work_dir=str(work_dir), image_local_path=str(target.resolve()))
        caption = _message_text(message)
        if caption:
            await _submit_video_i2v_from_state(message, state, caption)
            return
        await state.set_state(ProductionWorkflowForm.video_i2v_waiting_for_prompt)
        await _edit_video_i2v_control_message(message, state, step="2/2 已收到参考图，请输入视频需求")

    @router.message(ProductionWorkflowForm.video_i2v_waiting_for_prompt)
    async def on_video_i2v_prompt(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        if await _handle_video_i2v_param_button(message, state):
            return
        prompt = _message_text(message)
        if not prompt:
            await _edit_video_i2v_control_message(message, state, step="2/2 请输入这次图生视频的画面和动作需求")
            return
        await _submit_video_i2v_from_state(message, state, prompt)

    @router.message(ProductionWorkflowForm.replace_model_waiting_for_video)
    async def on_replace_model_video(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        suffix = _video_ext_from_message(message)
        if suffix is None:
            await message.answer("请上传原视频，或把视频当成 document 传送。", reply_markup=_menu_keyboard())
            return
        params = {
            "video_local_path": str(data["video_local_path"]),
            "image_local_path": str(data["image_local_path"]),
            "prompt": str(data.get("prompt") or ""),
            "duration_seconds": duration,
            "mode": "original",
            "tg_use_llm_prompt": True,
            "tg_user_instruction": str(data.get("prompt") or "保持原视频动作、镜头和环境，自然替换成上传模特图。"),
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "replace_model", params)
        except Exception as exc:
            await message.answer(f"视频模特替换任务提交失败：{exc}", reply_markup=_menu_keyboard())

    @router.message(ProductionWorkflowForm.replace_product_waiting_for_video)
    async def on_replace_product_video(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        suffix = _video_ext_from_message(message)
        if suffix is None:
            await message.answer("请上传原视频，或把视频当成 document 传送。", reply_markup=_menu_keyboard())
            return
        params = {
            "video_local_path": str(data["video_local_path"]),
            "image_local_path": str(data["image_local_path"]),
            "product_name": str(data.get("product_name") or "商品"),
            "prompt_text": str(data.get("prompt_text") or ""),
            "duration_seconds": duration,
            "tg_use_llm_prompt": True,
            "tg_user_instruction": "\n".join(
                [
                    f"商品名称：{str(data.get('product_name') or '商品')}",
                    str(data.get("prompt_text") or "保持原视频镜头和人物动作，自然替换成上传商品图。"),
                ]
            ),
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "replace_product", params)
        except Exception as exc:
            await message.answer(f"视频商品替换任务提交失败：{exc}", reply_markup=_menu_keyboard())

    @router.message(ProductionWorkflowForm.union_waiting_for_video)
    async def on_union_video(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        suffix = _video_ext_from_message(message)
        if suffix is None:
            await message.answer("请上传原视频，或把视频当成 document 传送。", reply_markup=_menu_keyboard())
            return
        params = {
            "video_local_path": str(data["video_local_path"]),
            "model_image_local_path": str(data["model_image_local_path"]),
            "product_image_local_path": str(data["product_image_local_path"]),
            "product_name": str(data.get("product_name") or "商品"),
            "model_params": {"duration_seconds": duration},
            "product_params": {"product_name": str(data.get("product_name") or "商品"), "duration_seconds": duration},
            "tg_use_llm_prompt": True,
            "tg_user_instruction": f"联合替换：自然替换视频模特和商品。商品名称：{str(data.get('product_name') or '商品')}",
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "replace_productANDmodel", params)
        except Exception as exc:
            await message.answer(f"联合替换任务提交失败：{exc}", reply_markup=_menu_keyboard())

    @router.message(F.text == DIGITAL_HUMAN_VIDEO_BUTTON)
    @router.message(F.text == "数字人视频生成")
    @router.message(F.text == "數字人視頻生成")
    @router.message(F.text == LEGACY_ORAL_UPLOAD_BUTTON)
    @router.message(F.text == "口播數字人：上傳素材")
    @router.message(F.text == LEGACY_UPLOAD_BUTTON)
    @router.message(F.text == "上傳素材建立任務")
    async def on_upload_task_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await state.clear()
        await message.answer(
            "请选择这次数字人视频的方向；选择后继续上传素材，Grok 会根据你的选项和文字生成提示词。",
            reply_markup=_digital_human_keyboard(),
        )

    @router.message(F.text == DIGITAL_HUMAN_REALISTIC_BUTTON)
    @router.message(F.text == "寫實帶貨視頻")
    async def on_digital_human_realistic(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_upload_flow(message, state, requirement="写实电商带货视频，人物自然展示商品，镜头干净，真实质感，无文字水印。")

    @router.message(F.text == DIGITAL_HUMAN_LIVE_BUTTON)
    @router.message(F.text == "直播口播視頻")
    async def on_digital_human_live(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_upload_flow(message, state, requirement="直播间口播风格，人物正面自然讲解商品，光线柔和，节奏清晰，适合短视频带货。")

    @router.message(F.text == DIGITAL_HUMAN_PRODUCT_BUTTON)
    @router.message(F.text == "產品展示視頻")
    async def on_digital_human_product(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_upload_flow(message, state, requirement="产品展示型数字人视频，突出商品细节和使用场景，人物动作自然，画面高级干净。")

    @router.message(F.text == DIGITAL_HUMAN_CUSTOM_BUTTON)
    @router.message(F.text == "自定義數字人要求")
    async def on_digital_human_custom_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await state.clear()
        await state.set_state(UploadFlowForm.waiting_for_custom_requirement)
        await message.answer("请直接输入这次数字人视频的客制化要求；收到后我会继续让你上传素材。", reply_markup=_digital_human_keyboard())

    @router.message(F.text == TEXT_TO_IMAGE_BUTTON)
    @router.message(F.text == "文生图")
    @router.message(F.text == "文生图片")
    @router.message(F.text == "文生圖")
    @router.message(F.text == "文生圖片")
    async def on_text_to_image_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_text_to_image_flow(message, state)

    @router.message(F.text == MULTI_IMAGE_BUTTON)
    @router.message(F.text == "多圖生成")
    async def on_multi_image_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_image_reference_flow(message, state, mode="multi_image")

    @router.message(F.text == IMAGE_REPLACE_BUTTON)
    @router.message(F.text == "圖片替換")
    async def on_image_replace_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_image_reference_flow(message, state, mode="image_replace")

    @router.message(F.text == IMAGE_WORKFLOW_BUTTON)
    @router.message(F.text == "图像编辑")
    @router.message(F.text == "图片编辑")
    @router.message(F.text == "圖片編輯")
    @router.message(F.text == LEGACY_IMAGE_WORKFLOW_BUTTON)
    @router.message(F.text == "圖像編輯工作流")
    @router.message(F.text == LEGACY_IMAGE_GENERATE_WORKFLOW_BUTTON)
    @router.message(F.text == "圖片生成工作流")
    async def on_image_workflow_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_image_generate_flow(message, state)

    @router.message(F.text == VIDEO_EDIT_BUTTON)
    @router.message(F.text == "视频编辑")
    @router.message(F.text == "視頻編輯")
    async def on_video_edit_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await state.clear()
        await message.answer(
            "视频生成：请选择要建立的任务。",
            reply_markup=_video_edit_keyboard(),
        )

    @router.message(F.text == VIDEO_GENERAL_EDIT_BUTTON)
    @router.message(F.text == "视频编辑任务")
    @router.message(F.text == "圖生視頻")
    @router.message(F.text == "視頻編輯任務")
    async def on_video_general_edit_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_video_i2v_flow(message, state)

    @router.message(F.text == MAIN_MENU_BUTTON)
    @router.message(F.text == "返回主菜单")
    @router.message(F.text == "返回主菜單")
    async def on_main_menu_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await state.clear()
        await message.answer("已返回主菜单。", reply_markup=_menu_keyboard())

    @router.message(F.text == REPLACE_MODEL_WORKFLOW_BUTTON)
    @router.message(F.text == "视频模特替换")
    @router.message(F.text == "視頻模特替換")
    @router.message(F.text == LEGACY_REPLACE_MODEL_WORKFLOW_BUTTON)
    @router.message(F.text == "模特替換工作流")
    async def on_replace_model_workflow_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_replace_model_flow(message, state)

    @router.message(F.text == REPLACE_PRODUCT_WORKFLOW_BUTTON)
    @router.message(F.text == "视频商品替换")
    @router.message(F.text == "視頻商品替換")
    @router.message(F.text == LEGACY_REPLACE_PRODUCT_WORKFLOW_BUTTON)
    @router.message(F.text == "商品替換工作流")
    async def on_replace_product_workflow_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_replace_product_flow(message, state)

    @router.message(F.text == REPLACE_UNION_WORKFLOW_BUTTON)
    @router.message(F.text == "联合替换工作流")
    @router.message(F.text == "聯合替換工作流")
    async def on_replace_union_workflow_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_union_flow(message, state)

    @router.message(F.text == WORKFLOW_CONFIG_BUTTON)
    @router.message(F.text == "查看後台工作流配置")
    async def on_workflow_config_button(message: Message) -> None:
        if not await ensure_authorized(message):
            return
        await message.answer(_workflow_config_text(service, selected_button=_message_text(message)), reply_markup=_menu_keyboard())

    @router.message(F.text == STATUS_BUTTON)
    @router.message(F.text == "查看工作台状态")
    @router.message(F.text == "查看工作台狀態")
    async def on_status_button(message: Message) -> None:
        if not await ensure_authorized(message):
            return
        await answer_status(message)

    @router.message(F.text == WORKBENCH_BUTTON)
    @router.message(F.text == "工作台網址")
    async def on_workbench_button(message: Message) -> None:
        if not await ensure_authorized(message):
            return
        await message.answer(
            f"工作台网址: {service.resolve_config().public_base_url}",
            reply_markup=_menu_keyboard(),
        )

    @router.message(F.text == SET_SCRIPT_BUTTON)
    @router.message(F.text == "設置預設文案")
    @router.message(F.text == "設定預設文案")
    async def on_setscript_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await state.clear()
        await state.set_state(ScriptForm.waiting_for_script)
        await message.answer("请直接贴上你想作为预设的文案内容。", reply_markup=_menu_keyboard())

    @router.message(F.text == RERUN_BUTTON)
    @router.message(F.text == "重跑最近任务")
    @router.message(F.text == "重跑最近任務")
    async def on_rerun_button(message: Message) -> None:
        if not await ensure_authorized(message):
            return
        latest_task = service.get_latest_task_for_submitter(int(message.chat.id))
        if latest_task is None:
            await message.answer("你目前还没有可重跑的历史任务。", reply_markup=_menu_keyboard())
            return
        request = service.clone_task_request(latest_task.id)
        await enqueue_request(message, request, source="telegram-rerun", is_default_assets=request.publish_to_default_paths)

    @router.message(F.text == STOP_BUTTON)
    @router.message(F.text == "强制停止当前任务")
    @router.message(F.text == "強制停止目前任務")
    @router.message(F.text == "強制停止當前任務")
    async def on_stop_button(message: Message, state: FSMContext) -> None:
        if await handle_stop_request(message, state):
            return

    @router.message()
    async def on_natural_language_message(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_workflow_reference_request(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        text = _message_text(message)
        work_dir = service.create_job_dir(prefix="tg_agent")
        files: list[dict[str, str]] = []
        try:
            downloaded = await _download_agent_message_file(message, work_dir)
            if downloaded:
                files.append(downloaded)
        except Exception as exc:
            await message.answer(f"素材下载失败：{exc}", reply_markup=_menu_keyboard())
            return
        if not text and not files:
            await message.answer("请用文字描述你要建立的生产任务，或按面板入口依序提交素材。", reply_markup=_menu_keyboard())
            return
        if not text:
            text = "根据我上传的素材判断最合适的生产工作流，并生成需要的提示词。"
        await state.clear()
        if text and not files:
            try:
                params = _text_to_image_params()
                await state.update_data(aspect_ratio=params["aspect_ratio"], width=params["width"], height=params["height"])
                await _preview_text_to_image_prompt(message, state, user_request=text)
            except Exception as exc:
                await message.answer(f"Grok ????????{exc}", reply_markup=_menu_keyboard())
            return
        try:
            result = await _submit_internal_webapp_agent_task(
                chat_id=int(message.chat.id),
                message_text=text,
                files=files,
            )
        except Exception as exc:
            await message.answer(
                f"智能任务提交失败：{exc}\n\n你也可以按面板中的具体工作流入口，依序上传素材。",
                reply_markup=_menu_keyboard(),
            )
            return
        summary = str(result.get("summary") or "已通过文字模型識別任务").strip()
        if result.get("submitted") is False:
            reply = str(result.get("reply") or summary or "").strip()
            if not reply:
                reply = "请补充具体生产任务和必要素材，或按面板入口依序提交。"
            await message.answer(reply, reply_markup=_menu_keyboard())
            return
        await message.answer(
            "\n".join(
                part
                for part in [
                    "已通过文字模型理解你的会话，并生成工作流提示词。",
                    summary,
                    f"Grok 生成提示词: {str(result.get('prompt_preview') or '').strip()}" if str(result.get("prompt_preview") or "").strip() else "",
                    f"工作流: {result.get('task_type')}",
                    f"任务编号: {result.get('id')}",
                    "可按「查看工作台状态」跟进进度。",
                ]
                if part
            ),
            reply_markup=_menu_keyboard(),
        )

    return dispatcher


class TelegramWorkbenchBot:
    def __init__(self, config: AppConfig, service: WorkspaceService) -> None:
        self.config = config
        self.service = service
        self.bot = _build_bot(config)
        self.dispatcher = build_dispatcher(config, service)
        self.polling_task: asyncio.Task | None = None

    async def _polling_loop(self) -> None:
        while True:
            try:
                await self.dispatcher.start_polling(self.bot, handle_signals=False)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram polling stopped unexpectedly; retrying in 5 seconds.")
                await asyncio.sleep(5)

    async def start(self) -> None:
        self.service.attach_bot(self.bot)
        self.polling_task = asyncio.create_task(self._polling_loop(), name="workspace-bot-polling")
        for member in self.service.list_members():
            if member.enabled:
                try:
                    await self.bot.send_message(
                        member.chat_id,
                        "\n".join(
                            [
                                f"{self.service.get_app_title()} 已上线。",
                                f"图像任务按「{IMAGE_WORKFLOW_BUTTON}」后选择「{TEXT_TO_IMAGE_BUTTON}」。",
                                f"视频任务按「{VIDEO_EDIT_BUTTON}」后选择「{VIDEO_GENERAL_EDIT_BUTTON}」。",
                                "提交后任务会进入后台队列；可按「查看工作台状态」，并在 Web 任务详情查看进度与成品。",
                            ]
                        ),
                        reply_markup=_menu_keyboard(),
                    )
                except (asyncio.CancelledError, Exception):
                    continue

    async def stop(self) -> None:
        if self.polling_task is not None:
            self.polling_task.cancel()
            await asyncio.gather(self.polling_task, return_exceptions=True)
            self.polling_task = None
        await self.bot.session.close()
