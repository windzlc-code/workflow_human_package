from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import re
import secrets
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
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"}
ZIP_EXTS = {".zip"}
AUTO_DURATION_TEXTS = {"跳过", "自动", "auto", "AUTO"}
TG_PROMPT_PREVIEW_TIMEOUT_SECONDS = int(os.getenv("TG_PROMPT_PREVIEW_TIMEOUT_SECONDS") or "240")
TG_PROMPT_DISPLAY_TIMEOUT_SECONDS = int(os.getenv("TG_PROMPT_DISPLAY_TIMEOUT_SECONDS") or "45")
TEXT_TO_IMAGE_MAX_SEED = 2147483647
TEXT_TO_IMAGE_REROLL_RUNTIME_KEYS = (
    "comfy_workflow_source",
    "remote_comfy_gateway_url",
    "remote_comfy_gateway_token",
    "remote_comfy_workflow_mappings",
    "local_comfy_gateway_url",
    "local_comfy_gateway_token",
    "local_comfy_workflow_mappings",
)

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
TEXT_TO_IMAGE_REROLL_IMAGE_BUTTON = "重新生成图片"
TEXT_TO_IMAGE_CONTINUE_IMAGE_BUTTON = "继续生成图片"
MULTI_IMAGE_BUTTON = "多图生成"
SINGLE_IMAGE_EDIT_BUTTON = "单图编辑"
IMAGE_EDIT_BUTTON = "图片编辑"
FACE_SWAP_BUTTON = "人物换脸"
IMAGE_REPLACE_BUTTON = "图片替换"
VIDEO_GENERAL_EDIT_BUTTON = "图生视频"
PERSON_T2I_DEFAULT_BATCH_SIZE = 6
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
    "單圖編輯": SINGLE_IMAGE_EDIT_BUTTON,
    "圖片編輯": IMAGE_EDIT_BUTTON,
    "圖像編輯": IMAGE_EDIT_BUTTON,
    "人物換臉": FACE_SWAP_BUTTON,
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
    image_edit_waiting_for_image = State()
    image_edit_waiting_for_reference_image = State()
    image_edit_waiting_for_prompt = State()
    image_edit_waiting_for_confirm = State()
    face_swap_waiting_for_target_image = State()
    face_swap_waiting_for_source_image = State()
    face_swap_waiting_for_prompt = State()
    face_swap_waiting_for_confirm = State()
    video_i2v_waiting_for_resolution = State()
    video_i2v_waiting_for_duration = State()
    video_i2v_waiting_for_audio = State()
    video_i2v_waiting_for_prompt_mode = State()
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
            [KeyboardButton(text=TEXT_TO_IMAGE_BUTTON), KeyboardButton(text=SINGLE_IMAGE_EDIT_BUTTON)],
            [KeyboardButton(text=IMAGE_EDIT_BUTTON), KeyboardButton(text=FACE_SWAP_BUTTON)],
            [KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _image_task_confirm_keyboard(submit_text: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=submit_text)],
            [KeyboardButton(text="上一步"), KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _image_task_step_keyboard(*, back: bool = True) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    if back:
        rows.append([KeyboardButton(text="上一步"), KeyboardButton(text=MAIN_MENU_BUTTON)])
    else:
        rows.append([KeyboardButton(text=MAIN_MENU_BUTTON)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


TEXT_TO_IMAGE_RATIO_OPTIONS: dict[str, dict[str, Any]] = {
    "2:3": {"label": "2:3 竖图", "note": "基础竖图", "width": 640, "height": 960, "final": "2176 x 3264"},
    "3:4": {"label": "3:4 稳定竖图", "note": "稳定竖图", "width": 672, "height": 896, "final": "2285 x 3046"},
    "9:16": {"label": "9:16 手机竖屏", "note": "手机竖屏长图", "width": 576, "height": 1024, "final": "1958 x 3482"},
    "3:2": {"label": "3:2 横图", "note": "横图基准", "width": 960, "height": 640, "final": "3264 x 2176"},
    "4:3": {"label": "4:3 平衡横图", "note": "平衡横图", "width": 896, "height": 672, "final": "3046 x 2285"},
    "16:9": {"label": "16:9 宽屏", "note": "宽屏视频", "width": 1024, "height": 576, "final": "3482 x 1958"},
    "1:1": {"label": "1:1 正方形", "note": "正方形", "width": 768, "height": 768, "final": "2611 x 2611"},
}


TEXT_TO_IMAGE_PERSON_T2I_RATIO_OPTIONS: dict[str, dict[str, Any]] = {
    "8:15": {"label": "8:15 人设竖图", "note": "人设_t2i 原生竖图", "width": 1024, "height": 1920, "final": "关闭"},
    "2:3": {"label": "2:3 人设竖图", "note": "人设_t2i 竖图", "width": 1024, "height": 1536, "final": "关闭"},
    "3:4": {"label": "3:4 人设竖图", "note": "人设_t2i 稳定竖图", "width": 1024, "height": 1365, "final": "关闭"},
}


TEXT_TO_IMAGE_PERSON_T2I_PERSONA_LORA_NODE_INPUTS: dict[str, dict[str, Any]] = {
    "184": {
        "lora_name": r"Character Setting\人设1捞女1金君雅.safetensors",
        "strength_model": 0.8,
        "strength_clip": 1.0,
    }
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


def _text_to_image_workflow_profile_from_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").lower()
    if "person_t2i" in text or "\u4eba\u8bbe_t2i" in text or "\u4eba\u8a2d_t2i" in text:
        return "person_t2i"
    return "person_t2i" if "person_t2i" in text or "人设_t2i" in text or "人設_t2i" in text else "zit_final"


def _text_to_image_profile(data: dict[str, Any] | None = None) -> str:
    source = data or {}
    explicit = str(source.get("text_to_image_workflow_profile") or "").strip().lower()
    if explicit:
        if explicit in {"person_t2i", "persona_t2i", "\u4eba\u8bbe_t2i", "\u4eba\u8a2d_t2i"}:
            return "person_t2i"
        return "person_t2i" if explicit in {"person_t2i", "persona_t2i", "人设_t2i", "人設_t2i"} else "zit_final"
    for key in ("text_to_image_workflow_path", "remote_comfy_workflow_path", "local_comfy_workflow_path"):
        profile = _text_to_image_workflow_profile_from_path(source.get(key))
        if profile == "person_t2i":
            return profile
    return "zit_final"


def _text_to_image_ratio_options(profile: str = "zit_final") -> dict[str, dict[str, Any]]:
    return TEXT_TO_IMAGE_PERSON_T2I_RATIO_OPTIONS if profile == "person_t2i" else TEXT_TO_IMAGE_RATIO_OPTIONS


def _text_to_image_final_resolution_available(profile: str = "zit_final") -> bool:
    return profile != "person_t2i"


def _text_to_image_persona_available(profile: str = "zit_final") -> bool:
    return bool(_text_to_image_persona_options(profile=profile))


def _text_to_image_persona_options(*, profile: str = "zit_final") -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    if profile == "person_t2i":
        source_values = [{"lora": values.get("lora_name")} for values in TEXT_TO_IMAGE_PERSON_T2I_PERSONA_LORA_NODE_INPUTS.values()]
    else:
        source_values = []
        for values in TEXT_TO_IMAGE_PERSONA_LORA_NODE_INPUTS.values():
            if isinstance(values, dict):
                source_values.extend(value for value in values.values() if isinstance(value, dict))
    for lora_value in source_values:
        if not isinstance(lora_value, dict):
            continue
        path = str(lora_value.get("lora") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        label = Path(path.replace("\\", "/")).stem or path
        options.append({"id": str(len(options)), "label": label, "path": path})
    return options


def _text_to_image_persona_label(path: str | None, *, profile: str = "zit_final") -> str:
    target = str(path or "").strip()
    for option in _text_to_image_persona_options(profile=profile):
        if option["path"] == target:
            return option["label"]
    return Path(target.replace("\\", "/")).stem if target else ""


def _text_to_image_default_persona_path(*, profile: str = "zit_final") -> str:
    options = _text_to_image_persona_options(profile=profile)
    return options[0]["path"] if options else ""


def _text_to_image_persona_node_inputs(*, enabled: bool, persona_lora: str = "", profile: str = "zit_final") -> dict[str, dict[str, Any]]:
    node_inputs: dict[str, dict[str, Any]] = {}
    selected_lora = str(persona_lora or _text_to_image_default_persona_path(profile=profile)).strip()
    if profile == "person_t2i":
        for node_id, values in TEXT_TO_IMAGE_PERSON_T2I_PERSONA_LORA_NODE_INPUTS.items():
            lora_name = str(selected_lora or values.get("lora_name") or "").strip()
            if enabled and lora_name:
                node_inputs[node_id] = {
                    "lora_name": lora_name,
                    "strength_model": float(values.get("strength_model") or 0.8),
                    "strength_clip": float(values.get("strength_clip") or 1.0),
                }
            else:
                node_inputs[node_id] = {
                    "lora_name": str(values.get("lora_name") or lora_name),
                    "strength_model": 0.0,
                    "strength_clip": 0.0,
                }
        return node_inputs
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
    profile = _text_to_image_profile(source)
    ratio_options = _text_to_image_ratio_options(profile)
    default_ratio = next(iter(ratio_options.keys()))
    ratio = str(source.get("aspect_ratio") or default_ratio).strip()
    if ratio not in ratio_options:
        ratio = default_ratio
    option = dict(ratio_options[ratio])
    final_resolution_available = _text_to_image_final_resolution_available(profile)
    final_resolution_enabled = bool(source.get("final_resolution_enabled", False)) if final_resolution_available else False
    persona_available = _text_to_image_persona_available(profile)
    persona_enabled = bool(source.get("persona_enabled", True if persona_available else False))
    persona_lora = str(source.get("persona_lora") or _text_to_image_default_persona_path(profile=profile)).strip() if persona_available else ""
    return {
        "text_to_image_workflow_profile": profile,
        "aspect_ratio": ratio,
        "width": int(option["width"]),
        "height": int(option["height"]),
        "final": str(option["final"]),
        "label": str(option["label"]),
        "note": str(option["note"]),
        "final_resolution_available": final_resolution_available,
        "final_resolution_enabled": final_resolution_enabled,
        "persona_available": persona_available,
        "persona_enabled": bool(persona_enabled and persona_available),
        "persona_lora": persona_lora,
        "persona_label": _text_to_image_persona_label(persona_lora, profile=profile),
        "ratio_selected": bool(source.get("ratio_selected", False)),
        "resolution_selected": bool(source.get("resolution_selected", False)),
        "persona_selected": bool(source.get("persona_selected", False)),
        "prompt_mode_selected": bool(source.get("prompt_mode_selected", False)),
        "prompt_mode_label": str(source.get("prompt_mode_label") or "").strip(),
    }


def _text_to_image_remote_node_inputs(params: dict[str, Any]) -> dict[str, Any]:
    profile = _text_to_image_profile(params)
    if profile == "person_t2i":
        node_inputs: dict[str, Any] = {
            "160": {"width": int(params["width"]), "height": int(params["height"]), "batch_size": PERSON_T2I_DEFAULT_BATCH_SIZE},
            "167": {
                "steps": 10,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
            "185": {"lora_name": r"ZIT\臀部Z-Hip-Slider.safetensors", "strength_model": 0.6, "strength_clip": 1.0},
            "186": {"lora_name": r"ZIT\胸部Z-Breast-Slider.safetensors", "strength_model": 0.6, "strength_clip": 1.0},
            "191": {"lora_name": r"Z-Image\Z-ImageTubro big-nipples.safetensors", "strength_model": 0.0, "strength_clip": 0.0},
            "171": {"filename_prefix": "telegram/person_t2i"},
        }
        node_inputs.update(
            _text_to_image_persona_node_inputs(
                enabled=bool(params.get("persona_enabled")),
                persona_lora=str(params.get("persona_lora") or ""),
                profile=profile,
            )
        )
        return node_inputs
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
    if _text_to_image_persona_available(profile):
        node_inputs.update(
            _text_to_image_persona_node_inputs(
                enabled=bool(params.get("persona_enabled")),
                persona_lora=str(params.get("persona_lora") or ""),
                profile=profile,
            )
        )
    return node_inputs


def _new_text_to_image_seed(excluded: set[int] | None = None) -> int:
    excluded = excluded or set()
    for _ in range(32):
        seed = secrets.randbelow(TEXT_TO_IMAGE_MAX_SEED) + 1
        if seed not in excluded:
            return seed
    seed = secrets.randbelow(TEXT_TO_IMAGE_MAX_SEED) + 1
    while seed in excluded:
        seed = 1 if seed >= TEXT_TO_IMAGE_MAX_SEED else seed + 1
    return seed


def _collect_text_to_image_seed_fields(value: Any) -> set[int]:
    seeds: set[int] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in {"seed", "noise_seed"}:
                try:
                    seeds.add(int(item))
                except Exception:
                    pass
            else:
                seeds.update(_collect_text_to_image_seed_fields(item))
    elif isinstance(value, list):
        for item in value:
            seeds.update(_collect_text_to_image_seed_fields(item))
    return seeds


def _replace_text_to_image_seed_fields(value: Any, seed: int) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if str(key) in {"seed", "noise_seed"}:
                value[key] = int(seed)
            else:
                _replace_text_to_image_seed_fields(item, seed)
    elif isinstance(value, list):
        for item in value:
            _replace_text_to_image_seed_fields(item, seed)


def _text_to_image_reroll_payload(input_payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    payload = copy.deepcopy(input_payload if isinstance(input_payload, dict) else {})
    for key in TEXT_TO_IMAGE_REROLL_RUNTIME_KEYS:
        payload.pop(key, None)
    params = _text_to_image_params(payload)
    final_prompt = str(
        payload.get("prompt_text")
        or payload.get("prompt")
        or payload.get("message")
        or payload.get("tg_llm_rewritten_prompt")
        or ""
    ).strip()
    if not final_prompt:
        raise ValueError("上次任务没有可复用的最终提示词")

    node_inputs = payload.get("remote_comfy_node_inputs")
    if not isinstance(node_inputs, dict) or not node_inputs:
        node_inputs = _text_to_image_remote_node_inputs(params)
    else:
        node_inputs = copy.deepcopy(node_inputs)

    excluded_seeds = _collect_text_to_image_seed_fields(node_inputs)
    try:
        excluded_seeds.add(int(payload.get("seed")))
    except Exception:
        pass
    seed = _new_text_to_image_seed(excluded_seeds)
    _replace_text_to_image_seed_fields(node_inputs, seed)
    payload.update(
        {
            "prompt": final_prompt,
            "prompt_text": final_prompt,
            "message": final_prompt,
            "width": params["width"],
            "height": params["height"],
            "aspect_ratio": params["aspect_ratio"],
            "batch_size": PERSON_T2I_DEFAULT_BATCH_SIZE if str(params.get("text_to_image_workflow_profile") or "") == "person_t2i" else 1,
            "final_resolution_enabled": bool(params["final_resolution_enabled"]),
            "persona_enabled": bool(params["persona_enabled"]),
            "persona_lora": str(params.get("persona_lora") or ""),
            "persona_label": str(params.get("persona_label") or ""),
            "tg_use_llm_prompt": False,
            "tg_llm_prompt_enhanced": True,
            "tg_llm_rewritten_prompt": final_prompt,
            "remote_comfy_node_inputs": node_inputs,
            "seed": seed,
        }
    )
    return payload, seed


def _text_to_image_status_text(*, step: str, params: dict[str, Any]) -> str:
    lines = ["文生图设置", f"当前步骤：{step}"]
    if params.get("ratio_selected"):
        lines.append(f"画面比例：{params['aspect_ratio']}（{params['note']}）")
        lines.append(f"基础分辨率：{params['width']} x {params['height']}")
    if params.get("resolution_selected"):
        final_resolution_text = "开启，预计 " + params["final"] if params.get("final_resolution_enabled") else "关闭，使用基础分辨率"
        lines.append(f"最终分辨率：{final_resolution_text}")
    if params.get("persona_selected"):
        if params.get("persona_enabled"):
            persona_text = params.get("persona_label") or "使用人设"
        elif params.get("persona_available"):
            persona_text = "不使用"
        else:
            persona_text = "当前工作流未检测到可选人设"
        lines.append(f"人设 LoRA：{persona_text}")
    if params.get("prompt_mode_selected"):
        prompt_mode_text = str(params.get("prompt_mode_label") or "").strip()
        if prompt_mode_text:
            lines.append(f"提示词方式：{prompt_mode_text}")
    return "\n".join(lines)


def _text_to_image_ratio_keyboard(*, selected_ratio: str = "", profile: str = "zit_final") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    items = list(_text_to_image_ratio_options(profile).items())
    for idx in range(0, len(items), 2):
        row: list[InlineKeyboardButton] = []
        for ratio, option in items[idx : idx + 2]:
            prefix = "✓ " if ratio == selected_ratio else ""
            row.append(InlineKeyboardButton(text=f"{prefix}{option['label']}", callback_data=f"t2i:ratio:{ratio}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="返回主菜单", callback_data="t2i:main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text_to_image_resolution_keyboard(
    *,
    final_resolution_enabled: bool = False,
    selected: bool = False,
    final_resolution_available: bool = True,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✓ ' if selected and not final_resolution_enabled else ''}使用基础分辨率",
                callback_data="t2i:final:off",
            )
        ],
    ]
    if final_resolution_available:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{'✓ ' if selected and final_resolution_enabled else ''}开启最终分辨率",
                    callback_data="t2i:final:on",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="上一步", callback_data="t2i:back:ratio")])
    rows.append([InlineKeyboardButton(text="返回主菜单", callback_data="t2i:main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text_to_image_persona_keyboard(*, persona_enabled: bool = True, persona_lora: str = "", selected: bool = False, profile: str = "zit_final") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    selected_lora = str(persona_lora or "").strip()
    for option in _text_to_image_persona_options(profile=profile):
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


def _text_to_image_prompt_display_retry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="重新生成中文预览", callback_data="t2i:retry_display")],
            [InlineKeyboardButton(text="重新生成提示词", callback_data="t2i:regen")],
            [InlineKeyboardButton(text="输入自定义提示词", callback_data="t2i:custom_prompt")],
            [InlineKeyboardButton(text="返回主菜单", callback_data="t2i:main_menu")],
        ]
    )


def _text_to_image_ratio_reply_keyboard(*, profile: str = "zit_final") -> ReplyKeyboardMarkup:
    items = [str(option["label"]) for option in _text_to_image_ratio_options(profile).values()]
    rows = [
        [KeyboardButton(text=items[idx]), KeyboardButton(text=items[idx + 1])]
        for idx in range(0, len(items) - 1, 2)
    ]
    if len(items) % 2:
        rows.append([KeyboardButton(text=items[-1])])
    rows.append([KeyboardButton(text=MAIN_MENU_BUTTON)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _text_to_image_resolution_reply_keyboard(*, final_resolution_available: bool = True) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text="使用基础分辨率")]]
    if final_resolution_available:
        rows.append([KeyboardButton(text="开启最终分辨率")])
    rows.append([KeyboardButton(text="上一步"), KeyboardButton(text=MAIN_MENU_BUTTON)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _text_to_image_persona_reply_keyboard(*, profile: str = "zit_final") -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=str(option["label"]))] for option in _text_to_image_persona_options(profile=profile)]
    rows.append([KeyboardButton(text="不使用人设")])
    rows.append([KeyboardButton(text="上一步"), KeyboardButton(text=MAIN_MENU_BUTTON)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _text_to_image_prompt_mode_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="让 Grok 生成提示词")],
            [KeyboardButton(text="输入自定义提示词")],
            [KeyboardButton(text="上一步"), KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _text_to_image_prompt_entry_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="上一步"), KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _text_to_image_prompt_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="使用这个提示词生成")],
            [KeyboardButton(text="输入自定义提示词提交")],
            [KeyboardButton(text="继续让 Grok 调整"), KeyboardButton(text="重新生成提示词")],
            [KeyboardButton(text="返回参数设置"), KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _text_to_image_prompt_failure_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="重新生成提示词")],
            [KeyboardButton(text="输入自定义提示词")],
            [KeyboardButton(text="上一步"), KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _format_grok_preview_error(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return f"Grok 响应超时（超过 {TG_PROMPT_PREVIEW_TIMEOUT_SECONDS} 秒）。可以点击“重新生成提示词”再试一次，或先输入自定义提示词。"
    text = str(exc or "").strip()
    lower_text = text.lower()
    if "read timed out" in lower_text or "read timeout" in lower_text or "timed out" in lower_text:
        return "Grok 模型响应超时，上游接口长时间没有返回。可以点击“重新生成提示词”再试一次，或先输入自定义提示词。"
    if "http 502" in lower_text and ("全部候选模型调用失败" in text or "connectionpool" in lower_text):
        return "Grok 模型服务暂时不可用或响应超时。可以点击“重新生成提示词”再试一次，或先输入自定义提示词。"
    if not text:
        return f"Grok 提示词生成失败（{type(exc).__name__}）。可以点击“重新生成提示词”再试一次。"
    return _format_tg_user_error(text)


def _format_tg_user_error(error: Any) -> str:
    text = str(error or "").strip()
    text = re.sub(r"工作台[:：]\s*https?://\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bfor url:\s*https?://\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\burl:\s*https?://\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?:/[^\s，。；;]*)?", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ：:，,。；;") or "未知错误"


def _video_edit_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=VIDEO_GENERAL_EDIT_BUTTON)],
            [KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _video_i2v_prompt_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="上一步"), KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _video_i2v_prompt_review_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="使用这个提示词生成")],
            [KeyboardButton(text="输入自定义提示词提交")],
            [KeyboardButton(text="继续让 Grok 调整"), KeyboardButton(text="重新生成提示词")],
            [KeyboardButton(text="返回参数设置"), KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _video_i2v_prompt_failure_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="重新生成提示词")],
            [KeyboardButton(text="输入自定义提示词提交")],
            [KeyboardButton(text="返回参数设置"), KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
    )


def _video_i2v_audio_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="\u8df3\u8fc7\u97f3\u9891")],
            [KeyboardButton(text="\u4e0a\u4e00\u6b65"), KeyboardButton(text=MAIN_MENU_BUTTON)],
        ],
        resize_keyboard=True,
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


def _looks_like_clean_chinese_preview(prompt_text: str) -> bool:
    text = str(prompt_text or "")
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    english_words = re.findall(r"[A-Za-z][A-Za-z'-]{1,}", text)
    return len(cjk_chars) >= 6 and not english_words


def _tg_prompt_preview_unavailable_text() -> str:
    return "提示词预览暂时不可用，实际提交到后台的原提示词已保存。"


def _format_prompt_display_fallback(exc: Exception | None = None) -> str:
    text = str(exc or "").strip().lower()
    if isinstance(exc, asyncio.TimeoutError) or "timed out" in text or "timeout" in text or "超时" in text or "504" in text:
        return "提示词预览生成超时，实际提交到后台的提示词已保存，可直接使用。"
    return _tg_prompt_preview_unavailable_text()


def _chat_identity_text(message: Message) -> str:
    user = message.from_user
    username = f"@{user.username}" if user and user.username else ""
    user_id = int(user.id) if user and getattr(user, "id", None) is not None else None
    full_name = " ".join(
        part for part in [getattr(user, "first_name", "") if user else "", getattr(user, "last_name", "") if user else ""] if part
    ).strip()
    lines = [
        "你的 Telegram 身份信息：",
        f"chat_id: {int(message.chat.id)}",
    ]
    if user_id is not None and user_id != int(message.chat.id):
        lines.append(f"user_id: {user_id}")
    if username:
        lines.append(f"username: {username}")
    if full_name:
        lines.append(f"name: {full_name}")
    lines.extend(
        [
            "",
            "私聊机器人时请添加 chat_id；在群里使用时可以添加 user_id 或群 chat_id。不要填写机器人 ID。",
        ]
    )
    return "\n".join(lines)


def _message_authorization_ids(message: Message) -> list[int]:
    ids: list[int] = []
    try:
        ids.append(int(message.chat.id))
    except (AttributeError, TypeError, ValueError):
        pass
    user = getattr(message, "from_user", None)
    try:
        user_id = int(user.id) if user and getattr(user, "id", None) is not None else None
    except (TypeError, ValueError):
        user_id = None
    if user_id is not None and user_id not in ids:
        ids.append(user_id)
    return ids


def _is_message_authorized(service: WorkspaceService, message: Message) -> bool:
    return any(service.is_chat_authorized(candidate) for candidate in _message_authorization_ids(message))


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


def _runtime_text_to_image_workflow_path(runtime: dict[str, Any] | None) -> str:
    source = str((runtime or {}).get("comfy_workflow_source") or "remote").strip().lower()
    mappings_key = "local_comfy_workflow_mappings" if source == "local" else "remote_comfy_workflow_mappings"
    mappings = (runtime or {}).get(mappings_key)
    if not isinstance(mappings, dict):
        mappings = {}
    value = mappings.get("text_to_image") or mappings.get("default") or ""
    return str(value or "").strip()


def _text_to_image_runtime_params(runtime: dict[str, Any] | None) -> dict[str, Any]:
    workflow_path = _runtime_text_to_image_workflow_path(runtime)
    workflow_profile = _text_to_image_workflow_profile_from_path(workflow_path)
    params = _text_to_image_params(
        {
            "text_to_image_workflow_profile": workflow_profile,
            "text_to_image_workflow_path": workflow_path,
        }
    )
    params["text_to_image_workflow_path"] = workflow_path
    return params


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


def _runtime_mapped_workflow(runtime: dict[str, Any], task_type: str) -> str:
    source = str((runtime or {}).get("comfy_workflow_source") or "remote").strip().lower()
    mappings_key = "local_comfy_workflow_mappings" if source == "local" else "remote_comfy_workflow_mappings"
    mappings = runtime.get(mappings_key)
    if not isinstance(mappings, dict):
        return ""
    value = mappings.get(task_type)
    if value is None and str(task_type or "").strip() == "single_image_edit":
        value = mappings.get("get_nano_banana")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("workflow") or value.get("path") or value.get("value") or "").strip()
    return ""


def _format_mapping(label: str, workflow_path: str) -> str:
    text = str(workflow_path or "").strip()
    return f"{label}: {text or '未配置'}"


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
    image_edit_workflow = _runtime_mapped_workflow(runtime, "get_nano_banana")
    face_swap_workflow = _runtime_mapped_workflow(runtime, "face_swap")
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
            SINGLE_IMAGE_EDIT_BUTTON: _format_mapping("单图编辑", image_edit_workflow),
            IMAGE_EDIT_BUTTON: _format_mapping("图片编辑", image_edit_workflow),
            FACE_SWAP_BUTTON: _format_mapping("人物换脸", face_swap_workflow),
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
            _format_mapping("单图编辑", image_edit_workflow),
            _format_mapping("图片编辑", image_edit_workflow),
            _format_mapping("人物换脸", face_swap_workflow),
            _format_chain("视频模特替换", replace_model_original_chain),
            "",
            selected_note,
            "TG 面板可直接建立任务：图像生成、图片编辑、人物换脸、视频生成。",
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
            "   点击后选择文生图、图片编辑或人物换脸。",
            f"2. {VIDEO_EDIT_BUTTON}",
            "   点击后选择图生视频，可用按钮设置分辨率、时长、音频和提示词。",
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


def _audio_ext_from_message(message: Message) -> str | None:
    if message.audio:
        suffix = Path(message.audio.file_name or "").suffix.lower()
        return suffix if suffix in AUDIO_EXTS else ".mp3"
    if message.voice:
        return ".ogg"
    if message.document:
        suffix = Path(message.document.file_name or "").suffix.lower()
        if suffix in AUDIO_EXTS:
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
    elif message.audio:
        downloadable = message.audio
    elif message.voice:
        downloadable = message.voice
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


def _fetch_webapp_runtime_config() -> dict[str, Any]:
    headers: dict[str, str] = {}
    token = str(os.getenv("TG_INTERNAL_API_TOKEN") or "").strip()
    if token:
        headers["x-tg-internal-token"] = token
    url = f"{_internal_webapp_base_url()}/api/internal/tg/runtime_config"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read().decode("utf-8", errors="replace")
    data = json.loads(body)
    if not isinstance(data, dict):
        return {}
    runtime = data.get("runtime_config")
    return runtime if isinstance(runtime, dict) else {}


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
    data: dict[str, Any] | None = None
    last_client_error: ClientError | None = None
    for attempt in range(1, 4):
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
                    break
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"后台 Grok 提示词生成超时（超过 {TG_PROMPT_PREVIEW_TIMEOUT_SECONDS} 秒）。"
                "通常是 Grok 响应慢、供应商排队，或提示词被二次校验重试拖长。"
            ) from exc
        except ClientError as exc:
            last_client_error = exc
            if attempt >= 3:
                raise RuntimeError(f"连接后台 Grok 提示词服务失败：{exc}") from exc
            await asyncio.sleep(0.8 * attempt)
    if data is None:
        raise RuntimeError(f"连接后台 Grok 提示词服务失败：{last_client_error}")
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
            timeout=TG_PROMPT_DISPLAY_TIMEOUT_SECONDS,
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
    display_text = str(data.get("display_text") or "").strip()
    if display_text and not _looks_like_clean_chinese_preview(display_text):
        raise RuntimeError("后台提示词中文预览包含英文残留")
    return display_text


async def _send_long_text(message: Message, text: str, *, reply_markup: Any | None = None) -> None:
    body = str(text or "")
    if len(body) <= 3900:
        await message.answer(body, reply_markup=reply_markup)
        return
    chunks = [body[idx : idx + 3900] for idx in range(0, len(body), 3900)]
    for idx, chunk in enumerate(chunks):
        await message.answer(chunk, reply_markup=reply_markup if idx == len(chunks) - 1 else None)


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
        error = _format_tg_user_error(item.get("error") or "")
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
        if _is_message_authorized(service, message):
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
            SINGLE_IMAGE_EDIT_BUTTON,
            IMAGE_EDIT_BUTTON,
            FACE_SWAP_BUTTON,
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
        elif text == SINGLE_IMAGE_EDIT_BUTTON:
            await start_single_image_edit_flow(message, state, single_input=True)
        elif text == IMAGE_EDIT_BUTTON:
            await start_single_image_edit_flow(message, state, single_input=False)
        elif text == FACE_SWAP_BUTTON:
            await start_face_swap_flow(message, state)
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
        task_label = {
            "text_to_image": "文生图",
            "image_generate": "图像生成",
            "get_nano_banana": "图片编辑",
            "single_image_edit": "单图编辑",
            "face_swap": "人物换脸",
            "video_i2v": "图生视频",
        }.get(str(task_type), str(task_type))
        prompt_preview = str(result.get("prompt_preview") or "").strip()
        prompt_preview_display = str(params.get("tg_prompt_display_text") or "").strip()
        if not prompt_preview_display and prompt_preview:
            prompt_preview_display = _telegram_prompt_chinese_preview(prompt_preview)
            if not _looks_like_clean_chinese_preview(prompt_preview_display):
                prompt_preview_display = _tg_prompt_preview_unavailable_text()
        await message.answer(
            "\n".join(
                part
                for part in [
                    "任务已提交到后台队列。",
                    f"工作流: {task_label}",
                    f"提示词: {prompt_preview_display}" if prompt_preview_display else "",
                    f"任务编号: {result.get('id')}",
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
            parts.append(f"后台生成任务：查询失败（{_format_tg_user_error(exc)}）")
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

    def _tg_workflow_display_name(workflow_path: str) -> str:
        text = str(workflow_path or "").strip().replace("\\", "/")
        if not text:
            return ""
        text = text.removeprefix("__converted__/")
        name = Path(text).stem or text
        if name.endswith(".api"):
            name = name[:-4]
        parent = str(Path(text).parent).replace("\\", "/")
        if parent and parent not in {".", "__converted__"}:
            return f"{parent}/{name}"
        return name

    def _tg_mapped_workflow_line(task_type: str) -> str:
        runtime = _load_runtime_config(service.resolve_config())
        workflow_path = _runtime_mapped_workflow(runtime, task_type)
        if not workflow_path:
            return "可用工作流：未配置，请先在后台映射工作流。"
        return f"可用工作流：{_tg_workflow_display_name(workflow_path)}"

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

    async def start_single_image_edit_flow(message: Message, state: FSMContext, *, single_input: bool = False) -> None:
        await state.clear()
        await state.set_state(ProductionWorkflowForm.image_edit_waiting_for_image)
        mode = "single" if single_input else "two"
        title = "单图编辑" if single_input else "图片编辑"
        total_steps = "3" if single_input else "4"
        workflow_type = "single_image_edit" if single_input else "get_nano_banana"
        await state.update_data(work_dir=str(service.create_job_dir(prefix="tg_image_edit")), image_edit_mode=mode)
        await message.answer(
            "\n".join(
                [
                    title,
                    _tg_mapped_workflow_line(workflow_type),
                    f"步骤 1/{total_steps}：请上传需要编辑的原图。",
                ]
            ),
            reply_markup=_image_task_step_keyboard(back=False),
        )

    async def start_face_swap_flow(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(ProductionWorkflowForm.face_swap_waiting_for_target_image)
        await state.update_data(work_dir=str(service.create_job_dir(prefix="tg_face_swap")))
        await message.answer(
            "\n".join(
                [
                    "人物换脸",
                    _tg_mapped_workflow_line("face_swap"),
                    "步骤 1/4：请上传原图，也就是需要被换脸的图片。",
                ]
            ),
            reply_markup=_image_task_step_keyboard(back=False),
        )

    def _video_i2v_defaults() -> dict[str, Any]:
        return {
            "resolution": "720p",
            "duration": 2,
            "use_grok": True,
            "prompt_extend": False,
            "safety_filter": False,
            "seed": "1024",
            "negative_prompt": "",
            "resolution_selected": False,
            "duration_selected": False,
            "audio_selected": False,
            "audio_local_path": "",
            "prompt_mode_selected": False,
            "prompt_extend_selected": False,
            "prompt_mode_label": "",
        }

    async def _video_i2v_runtime_defaults() -> dict[str, Any]:
        defaults = _video_i2v_defaults()
        try:
            runtime = await asyncio.to_thread(_fetch_webapp_runtime_config)
        except Exception:
            runtime = {}
        defaults["negative_prompt"] = str(
            runtime.get("mulerouter_wan_i2v_negative_prompt")
            or "low quality, blurry, distorted, watermark, text, logo"
        ).strip()
        seed_value = str(runtime.get("mulerouter_wan_i2v_seed") or defaults["seed"]).strip()
        defaults["seed"] = seed_value if seed_value.isdigit() else "1024"
        return defaults

    def _video_i2v_state_params(data: dict[str, Any]) -> dict[str, Any]:
        params = _video_i2v_defaults()
        params.update({k: data.get(k) for k in params.keys() if k in data})
        params["resolution"] = "1080p" if str(params.get("resolution") or "").strip() == "1080p" else "720p"
        params["duration"] = min(max(int(params.get("duration") or 2), 2), 15)
        params["use_grok"] = bool(params.get("use_grok"))
        params["prompt_extend"] = bool(params.get("prompt_extend"))
        params["safety_filter"] = False
        params["resolution_selected"] = bool(params.get("resolution_selected"))
        params["duration_selected"] = bool(params.get("duration_selected"))
        params["audio_selected"] = bool(params.get("audio_selected"))
        params["audio_local_path"] = str(params.get("audio_local_path") or "").strip()
        params["prompt_mode_selected"] = bool(params.get("prompt_mode_selected"))
        params["prompt_extend"] = False
        params["prompt_extend_selected"] = False
        params["prompt_mode_label"] = str(params.get("prompt_mode_label") or "").strip()
        seed_text = str(params.get("seed") or "1024").strip()
        params["seed"] = seed_text if seed_text.isdigit() else "1024"
        params["negative_prompt"] = str(params.get("negative_prompt") or "").strip()
        return params

    def _video_i2v_status_text(*, step: str, params: dict[str, Any]) -> str:
        lines = ["\u89c6\u9891\u751f\u6210\u8bbe\u7f6e", f"\u5f53\u524d\u6b65\u9aa4\uff1a{step}"]
        if params.get("resolution_selected"):
            lines.append(f"\u5206\u8fa8\u7387\uff1a{params['resolution']}")
        if params.get("duration_selected"):
            lines.append(f"\u65f6\u957f\uff1a{params['duration']}\u79d2")
        if params.get("audio_selected"):
            lines.append("\u97f3\u9891\uff1a\u5df2\u4e0a\u4f20" if params.get("audio_local_path") else "\u97f3\u9891\uff1a\u8df3\u8fc7")
        if params.get("prompt_mode_selected"):
            label = str(params.get("prompt_mode_label") or "").strip()
            prompt_mode_text = label or ("Grok \u751f\u6210" if params["use_grok"] else "\u81ea\u5b9a\u4e49\u63d0\u4ea4")
            lines.append(f"\u63d0\u793a\u8bcd\u65b9\u5f0f\uff1a{prompt_mode_text}")
        return "\n".join(lines)

    def _video_i2v_step_keyboard(step: str, params: dict[str, Any]) -> ReplyKeyboardMarkup:
        if step == "resolution":
            return ReplyKeyboardMarkup(
                keyboard=[
                    [
                        KeyboardButton(text="720p\uff08\u6700\u5c0f\u8d44\u6e90\uff09"),
                        KeyboardButton(text="1080p"),
                    ],
                    [KeyboardButton(text="\u8fd4\u56de\u4e3b\u83dc\u5355")],
                ],
                resize_keyboard=True,
            )
        if step == "duration":
            return ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="\u4e0a\u4e00\u6b65"), KeyboardButton(text="\u8fd4\u56de\u4e3b\u83dc\u5355")],
                ],
                resize_keyboard=True,
            )
        if step == "prompt_mode":
            return ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="\u8ba9 Grok \u751f\u6210\u63d0\u793a\u8bcd")],
                    [KeyboardButton(text="\u8f93\u5165\u81ea\u5b9a\u4e49\u63d0\u793a\u8bcd\u63d0\u4ea4")],
                    [KeyboardButton(text="\u4e0a\u4e00\u6b65"), KeyboardButton(text="\u8fd4\u56de\u4e3b\u83dc\u5355")],
                ],
                resize_keyboard=True,
            )
        if step == "audio":
            return _video_i2v_audio_keyboard()
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="\u4e0a\u4e00\u6b65"), KeyboardButton(text="\u8fd4\u56de\u4e3b\u83dc\u5355")],
            ],
            resize_keyboard=True,
        )

    async def _show_video_i2v_step(message: Message, state: FSMContext, *, step: str) -> None:
        data = await state.get_data()
        params = _video_i2v_state_params(data)
        state_map = {
            "resolution": ProductionWorkflowForm.video_i2v_waiting_for_resolution,
            "duration": ProductionWorkflowForm.video_i2v_waiting_for_duration,
            "audio": ProductionWorkflowForm.video_i2v_waiting_for_audio,
            "prompt_mode": ProductionWorkflowForm.video_i2v_waiting_for_prompt_mode,
            "image": ProductionWorkflowForm.video_i2v_waiting_for_image,
            "prompt": ProductionWorkflowForm.video_i2v_waiting_for_prompt,
        }
        await state.set_state(state_map.get(step, ProductionWorkflowForm.video_i2v_waiting_for_resolution))
        labels = {
            "resolution": "1/5 \u9009\u62e9\u5206\u8fa8\u7387",
            "duration": "2/5 \u8f93\u5165\u89c6\u9891\u65f6\u957f",
            "image": "3/5 \u4e0a\u4f20\u53c2\u8003\u56fe",
            "audio": "4/5 \u4e0a\u4f20\u97f3\u9891\uff08\u53ef\u9009\uff09",
            "prompt_mode": "5/5 \u9009\u62e9\u63d0\u793a\u8bcd\u65b9\u5f0f",
            "prompt": "\u5df2\u6536\u5230\u53c2\u8003\u56fe\uff0c\u8bf7\u8f93\u5165\u89c6\u9891\u9700\u6c42",
        }
        text = _video_i2v_status_text(step=labels.get(step, step), params=params)
        if step == "duration":
            text += "\n\n\u8bf7\u76f4\u63a5\u8f93\u5165\u89c6\u9891\u65f6\u957f\uff0c\u8303\u56f4 2 \u5230 15 \u79d2\uff0c\u4f8b\u5982\uff1a5\u3002"
        elif step == "audio":
            text += "\n\n\u53ef\u4ee5\u4e0a\u4f20\u97f3\u9891\u6587\u4ef6\uff08mp3/wav/m4a/ogg \u7b49\uff09\uff0c\u6216\u70b9\u51fb\u201c\u8df3\u8fc7\u97f3\u9891\u201d\u3002"
        elif step == "image":
            text += "\n\n\u8bf7\u4e0a\u4f20\u4e00\u5f20\u53c2\u8003\u56fe\u7247\u3002\u4e0b\u4e00\u6b65\u518d\u9009\u62e9\u662f\u5426\u4e0a\u4f20\u97f3\u9891\u3002"
        elif step == "prompt_mode":
            text += "\n\n\u8bf7\u9009\u62e9\u8ba9 Grok \u751f\u6210\u63d0\u793a\u8bcd\uff0c\u6216\u8f93\u5165\u81ea\u5b9a\u4e49\u63d0\u793a\u8bcd\u63d0\u4ea4\u3002"
        elif step == "prompt":
            if params.get("use_grok"):
                text += "\n\n\u8bf7\u8f93\u5165\u89c6\u9891\u9700\u6c42\u3002Grok \u4f1a\u5728\u6700\u540e\u751f\u6210\u5b8c\u6574\u63d0\u793a\u8bcd\uff0c\u5e76\u5728\u804a\u5929\u4e2d\u5b8c\u6574\u663e\u793a\u540e\u518d\u63d0\u4ea4\u3002"
            else:
                text += "\n\n\u8bf7\u8f93\u5165\u81ea\u5b9a\u4e49\u6700\u7ec8\u63d0\u793a\u8bcd\u3002\u4e0b\u4e00\u6761\u6d88\u606f\u4f1a\u8df3\u8fc7 Grok \u76f4\u63a5\u63d0\u4ea4\u3002"
        markup = _video_i2v_step_keyboard(step, params)
        await message.answer(text, reply_markup=markup)

    async def _show_video_i2v_step_from_callback(callback: CallbackQuery, state: FSMContext, *, step: str) -> None:
        if callback.message is None:
            return
        data = await state.get_data()
        params = _video_i2v_state_params(data)
        state_map = {
            "resolution": ProductionWorkflowForm.video_i2v_waiting_for_resolution,
            "duration": ProductionWorkflowForm.video_i2v_waiting_for_duration,
            "audio": ProductionWorkflowForm.video_i2v_waiting_for_audio,
            "prompt_mode": ProductionWorkflowForm.video_i2v_waiting_for_prompt_mode,
            "image": ProductionWorkflowForm.video_i2v_waiting_for_image,
            "prompt": ProductionWorkflowForm.video_i2v_waiting_for_prompt,
        }
        await state.set_state(state_map.get(step, ProductionWorkflowForm.video_i2v_waiting_for_resolution))
        labels = {
            "resolution": "1/5 \u9009\u62e9\u5206\u8fa8\u7387",
            "duration": "2/5 \u8f93\u5165\u89c6\u9891\u65f6\u957f",
            "image": "3/5 \u4e0a\u4f20\u53c2\u8003\u56fe",
            "audio": "4/5 \u4e0a\u4f20\u97f3\u9891\uff08\u53ef\u9009\uff09",
            "prompt_mode": "5/5 \u9009\u62e9\u63d0\u793a\u8bcd\u65b9\u5f0f",
            "prompt": "\u5df2\u6536\u5230\u53c2\u8003\u56fe\uff0c\u8bf7\u8f93\u5165\u89c6\u9891\u9700\u6c42",
        }
        text = _video_i2v_status_text(step=labels.get(step, step), params=params)
        if step == "duration":
            text += "\n\n\u8bf7\u76f4\u63a5\u8f93\u5165\u89c6\u9891\u65f6\u957f\uff0c\u8303\u56f4 2 \u5230 15 \u79d2\uff0c\u4f8b\u5982\uff1a5\u3002"
        elif step == "audio":
            text += "\n\n\u53ef\u4ee5\u4e0a\u4f20\u97f3\u9891\u6587\u4ef6\uff08mp3/wav/m4a/ogg \u7b49\uff09\uff0c\u6216\u70b9\u51fb\u201c\u8df3\u8fc7\u97f3\u9891\u201d\u3002"
        elif step == "image":
            text += "\n\n\u8bf7\u4e0a\u4f20\u4e00\u5f20\u53c2\u8003\u56fe\u7247\u3002\u4e0b\u4e00\u6b65\u518d\u9009\u62e9\u662f\u5426\u4e0a\u4f20\u97f3\u9891\u3002"
        elif step == "prompt_mode":
            text += "\n\n\u8bf7\u9009\u62e9\u8ba9 Grok \u751f\u6210\u63d0\u793a\u8bcd\uff0c\u6216\u8f93\u5165\u81ea\u5b9a\u4e49\u63d0\u793a\u8bcd\u63d0\u4ea4\u3002"
        elif step == "prompt":
            if params.get("use_grok"):
                text += "\n\n\u8bf7\u8f93\u5165\u89c6\u9891\u9700\u6c42\u3002Grok \u4f1a\u5728\u6700\u540e\u751f\u6210\u5b8c\u6574\u63d0\u793a\u8bcd\uff0c\u5e76\u5728\u804a\u5929\u4e2d\u5b8c\u6574\u663e\u793a\u540e\u518d\u63d0\u4ea4\u3002"
            else:
                text += "\n\n\u8bf7\u8f93\u5165\u81ea\u5b9a\u4e49\u6700\u7ec8\u63d0\u793a\u8bcd\u3002\u4e0b\u4e00\u6761\u6d88\u606f\u4f1a\u8df3\u8fc7 Grok \u76f4\u63a5\u63d0\u4ea4\u3002"
        await callback.message.answer(text, reply_markup=_video_i2v_step_keyboard(step, params))

    async def _remove_reply_keyboard(message: Message, *, text: str = "\u8bf7\u4f7f\u7528\u4e0a\u65b9\u6309\u94ae\u7ee7\u7eed\u3002") -> None:
        try:
            sent = await message.answer(text, reply_markup=ReplyKeyboardRemove())
            await sent.delete()
        except Exception:
            pass

    async def start_video_i2v_flow(message: Message, state: FSMContext) -> None:
        await state.clear()
        defaults = await _video_i2v_runtime_defaults()
        defaults.update(
            {
                "use_grok": True,
                "prompt_extend": False,
                "resolution_selected": False,
                "duration_selected": False,
                "audio_selected": False,
                "audio_local_path": "",
                "prompt_mode_selected": False,
                "prompt_extend_selected": False,
                "prompt_mode_label": "Grok \u751f\u6210",
                "work_dir": str(service.create_job_dir(prefix="tg_video_i2v")),
            }
        )
        await state.update_data(**defaults)
        await _show_video_i2v_step(message, state, step="resolution")

    def _build_video_i2v_payload(data: dict[str, Any], params: dict[str, Any], prompt: str) -> dict[str, Any] | None:
        image_path = str(data.get("image_local_path") or "").strip()
        if not image_path:
            return None
        payload = {
            "image_local_path": image_path,
            "prompt": prompt,
            "prompt_text": prompt,
            "message": prompt,
            "resolution": str(params["resolution"]),
            "duration_seconds": int(params["duration"]),
            "mulerouter_wan_i2v_resolution": str(params["resolution"]),
            "mulerouter_wan_i2v_duration": int(params["duration"]),
            "mulerouter_wan_i2v_prompt_extend": False,
            "mulerouter_wan_i2v_safety_filter": False,
            "mulerouter_wan_i2v_negative_prompt": str(params["negative_prompt"]),
            "negative_prompt": str(params["negative_prompt"]),
            "prompt_extend": False,
            "safety_filter": False,
            "tg_use_llm_prompt": bool(params["use_grok"]),
            "tg_user_instruction": f"User image-to-video request: {prompt}",
        }
        if str(params.get("audio_local_path") or "").strip():
            payload["audio_local_path"] = str(params["audio_local_path"]).strip()
        if str(params.get("seed") or "").isdigit():
            payload["seed"] = int(str(params["seed"]))
            payload["mulerouter_wan_i2v_seed"] = int(str(params["seed"]))
        return payload

    async def _submit_video_i2v_payload(message: Message, state: FSMContext, payload: dict[str, Any], params: dict[str, Any]) -> None:
        await state.clear()
        result = await _submit_internal_webapp_task(chat_id=int(message.chat.id), task_type="video_i2v", params=payload)
        prompt_mode_text = str(params.get("prompt_mode_label") or "").strip() or ("Grok 生成" if params["use_grok"] else "自定义提交")
        reply = "\n".join(
            part for part in [
                "图生视频任务已提交。",
                f"任务编号：{result.get('id')}",
                f"分辨率：{params['resolution']}，时长：{params['duration']}秒，提示词方式：{prompt_mode_text}",
                "生成完成后会自动把视频发回这里。",
            ] if part
        )
        await message.answer(reply, reply_markup=_menu_keyboard())

    async def _submit_video_i2v_from_state(message: Message, state: FSMContext, prompt: str) -> None:
        data = await state.get_data()
        params = _video_i2v_state_params(data)
        payload = _build_video_i2v_payload(data, params, prompt)
        if payload is None:
            await message.answer("\u8bf7\u5148\u4e0a\u4f20\u4e00\u5f20\u53c2\u8003\u56fe\u3002")
            await _show_video_i2v_step(message, state, step="image")
            return
        try:
            if params["use_grok"]:
                await message.answer("\u6b63\u5728\u8ba9 Grok \u751f\u6210\u89c6\u9891\u63d0\u793a\u8bcd...")
                preview = await _preview_internal_webapp_prompt(
                    chat_id=int(message.chat.id),
                    task_type="video_i2v",
                    params=payload,
                )
                generated_prompt = str(preview.get("prompt_text") or "").strip()
                if not generated_prompt:
                    raise RuntimeError("Grok \u672a\u8fd4\u56de\u53ef\u7528\u7684\u89c6\u9891\u63d0\u793a\u8bcd")
                await state.update_data(
                    video_i2v_user_request=prompt,
                    video_i2v_generated_prompt=generated_prompt,
                    video_i2v_prompt_ready=True,
                )
                await state.set_state(ProductionWorkflowForm.video_i2v_waiting_for_prompt)
                await _send_long_text(
                    message,
                    "\u89c6\u9891 Grok \u751f\u6210\u63d0\u793a\u8bcd\uff1a\n\n" + generated_prompt + "\n\n请确认后再提交。",
                    reply_markup=_video_i2v_prompt_review_keyboard(),
                )
                return
            await _submit_video_i2v_payload(message, state, payload, params)
        except Exception as exc:
            if params.get("use_grok"):
                await state.update_data(
                    **params,
                    video_i2v_user_request=prompt,
                    video_i2v_prompt_ready=False,
                    video_i2v_generated_prompt="",
                )
                await state.set_state(ProductionWorkflowForm.video_i2v_waiting_for_prompt)
                await message.answer(
                    "Grok 视频提示词生成失败："
                    f"{_format_grok_preview_error(exc)}\n\n"
                    "任务还没有提交，当前图生视频参数已保留。可以点击“重新生成提示词”再试一次，"
                    "或点击“输入自定义提示词提交”跳过 Grok。",
                    reply_markup=_video_i2v_prompt_failure_keyboard(),
                )
                return
            await message.answer(f"\u56fe\u751f\u89c6\u9891\u4efb\u52a1\u63d0\u4ea4\u5931\u8d25\uff1a{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

    @router.callback_query(F.data.startswith("video_i2v:"))
    async def on_video_i2v_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None:
            await callback.answer()
            return
        if not _is_message_authorized(service, callback.message):
            await callback.answer("\u5f53\u524d\u8d26\u53f7\u672a\u6388\u6743", show_alert=True)
            return
        action = str(callback.data or "")
        data = await state.get_data()
        params = _video_i2v_state_params(data)
        if action.endswith(":main_menu"):
            await state.clear()
            try:
                await callback.message.edit_text("\u5df2\u8fd4\u56de\u4e3b\u83dc\u5355\u3002")
            except Exception:
                pass
            await callback.message.answer("\u8bf7\u9009\u62e9\u4efb\u52a1\u7c7b\u578b\u3002", reply_markup=_menu_keyboard())
            await callback.answer()
            return
        if action == "video_i2v:back:resolution":
            params.update(
                {
                    "resolution_selected": False,
                    "duration_selected": False,
                    "prompt_mode_selected": False,
                    "prompt_extend_selected": False,
                }
            )
            await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="resolution")
            await callback.answer()
            return
        if action == "video_i2v:back:duration":
            params.update({"duration_selected": False, "prompt_mode_selected": False, "prompt_extend_selected": False})
            await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="duration")
            await callback.answer()
            return
        if action == "video_i2v:back:prompt_mode":
            params.update({"prompt_mode_selected": False, "prompt_extend_selected": False})
            await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="audio")
            await callback.answer()
            return
        if action == "video_i2v:back:extend":
            params.update({"prompt_extend": False, "prompt_extend_selected": False})
            await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="audio")
            await callback.answer()
            return
        if action == "video_i2v:next:duration":
            params["resolution_selected"] = True
            await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="duration")
            await callback.answer()
            return
        if action == "video_i2v:next:prompt_mode":
            if not params.get("audio_selected"):
                await callback.answer("\u8bf7\u5148\u9009\u62e9\u97f3\u9891\u6b65\u9aa4", show_alert=True)
                return
            await _show_video_i2v_step_from_callback(callback, state, step="prompt_mode")
            await callback.answer()
            return
        if action == "video_i2v:next:prompt_extend":
            if not params.get("prompt_mode_selected"):
                params["prompt_mode_selected"] = True
                params["prompt_mode_label"] = "Grok \u751f\u6210" if params.get("use_grok") else "\u81ea\u5b9a\u4e49\u63d0\u4ea4"
                await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="prompt")
            await callback.answer()
            return
        if action == "video_i2v:next:image":
            params["prompt_extend"] = False
            params["prompt_extend_selected"] = True
            await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="image")
            await callback.answer()
            return
        if action.startswith("video_i2v:resolution:"):
            params["resolution"] = action.rsplit(":", 1)[-1]
            params["resolution_selected"] = True
            params.update({"duration_selected": False, "prompt_mode_selected": False, "prompt_extend_selected": False})
            await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="duration")
            await callback.answer("\u5df2\u9009\u62e9\u5206\u8fa8\u7387")
            return
        if action.startswith("video_i2v:duration:"):
            value = action.rsplit(":", 1)[-1]
            try:
                duration = int(value)
            except ValueError:
                duration = 2
            if duration not in {2, 5, 8, 15}:
                await callback.answer("\u8bf7\u9009\u62e9\u53ef\u7528\u7684\u89c6\u9891\u65f6\u957f", show_alert=True)
                return
            params["duration"] = duration
            params["duration_selected"] = True
            params.update({"prompt_mode_selected": False, "prompt_extend_selected": False})
            await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="image")
            await callback.answer("\u5df2\u9009\u62e9\u89c6\u9891\u65f6\u957f")
            return
        if action.startswith("video_i2v:prompt_mode:"):
            params["use_grok"] = action.endswith(":grok")
            params["prompt_mode_selected"] = True
            params["prompt_mode_label"] = "Grok \u751f\u6210" if params["use_grok"] else "\u81ea\u5b9a\u4e49\u63d0\u4ea4"
            params["prompt_extend"] = False
            params["prompt_extend_selected"] = True
            await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="prompt")
            await callback.answer("\u5df2\u9009\u62e9\u63d0\u793a\u8bcd\u65b9\u5f0f")
            return
        if action.startswith("video_i2v:extend:"):
            params["prompt_extend"] = False
            params["prompt_extend_selected"] = True
            await state.update_data(**params)
            await _show_video_i2v_step_from_callback(callback, state, step="image")
            await callback.answer()
            return
        await callback.answer()

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

    async def _current_text_to_image_runtime_params() -> dict[str, Any]:
        try:
            runtime = await asyncio.to_thread(_fetch_webapp_runtime_config)
        except Exception:
            logger.exception("Failed to fetch runtime config from webapp API; falling back to runtime file")
            runtime = _load_runtime_config(config)
        return _text_to_image_runtime_params(runtime)

    async def _set_text_to_image_runtime_state(state: FSMContext, params: dict[str, Any]) -> None:
        await state.update_data(
            text_to_image_workflow_profile=str(params.get("text_to_image_workflow_profile") or "zit_final"),
            text_to_image_workflow_path=str(params.get("text_to_image_workflow_path") or ""),
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

    async def _refresh_text_to_image_runtime_state(state: FSMContext) -> tuple[dict[str, Any], bool]:
        data = await state.get_data()
        current_path = str(data.get("text_to_image_workflow_path") or "").strip()
        current_profile = str(data.get("text_to_image_workflow_profile") or "").strip()
        params = await _current_text_to_image_runtime_params()
        latest_path = str(params.get("text_to_image_workflow_path") or "").strip()
        latest_profile = str(params.get("text_to_image_workflow_profile") or "").strip()
        changed = latest_path != current_path or latest_profile != current_profile
        if changed:
            await _set_text_to_image_runtime_state(state, params)
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_ratio)
        else:
            params = _text_to_image_params(data)
            params["text_to_image_workflow_path"] = current_path
        return params, changed

    async def start_text_to_image_flow(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_ratio)
        runtime = _load_runtime_config(config)
        source = str(runtime.get("comfy_workflow_source") or "remote").strip().lower()
        mappings = runtime.get("local_comfy_workflow_mappings") if source == "local" else runtime.get("remote_comfy_workflow_mappings")
        mappings = mappings if isinstance(mappings, dict) else {}
        workflow_path = str(mappings.get("text_to_image") or "").strip()
        workflow_profile = _text_to_image_workflow_profile_from_path(workflow_path)
        params = _text_to_image_params(
            {
                "text_to_image_workflow_profile": workflow_profile,
                "text_to_image_workflow_path": workflow_path,
            }
        )
        await state.update_data(
            text_to_image_workflow_profile=workflow_profile,
            text_to_image_workflow_path=workflow_path,
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
        params = await _current_text_to_image_runtime_params()
        workflow_profile = str(params.get("text_to_image_workflow_profile") or "zit_final")
        await _set_text_to_image_runtime_state(state, params)
        await message.answer(
            _text_to_image_status_text(step="1/4 请选择图像比例", params=params),
            reply_markup=_text_to_image_ratio_reply_keyboard(profile=workflow_profile),
        )

    async def _show_text_to_image_prompt_review(message: Message, state: FSMContext, *, prompt_text: str, selected_model: str = "") -> None:
        data = await state.get_data()
        params = _text_to_image_params(data)
        display_prompt_text = str(prompt_text or "").strip()
        if not display_prompt_text:
            raise RuntimeError("Grok \u672a\u8fd4\u56de\u53ef\u7528\u63d0\u793a\u8bcd\uff0c\u8bf7\u91cd\u65b0\u751f\u6210\u63d0\u793a\u8bcd\u3002")
        await state.update_data(
            prompt_display_text=display_prompt_text,
            prompt_display_ready=True,
            prompt_display_pending=False,
        )
        await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_revision)
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
        await message.answer(text, reply_markup=_text_to_image_prompt_reply_keyboard())

    async def _show_text_to_image_display_pending(message: Message, state: FSMContext, *, exc: Exception | None = None) -> None:
        await state.update_data(prompt_display_ready=False, prompt_display_pending=True)
        await message.answer(
            "\n".join(
                [
                    "Grok 生成的提示词还没有通过中文校验。",
                    "暂不提交到队列。请重新生成提示词，或输入自定义提示词。",
                    _format_prompt_display_fallback(exc),
                ]
            ),
            reply_markup=_text_to_image_prompt_failure_reply_keyboard(),
        )

    async def _preview_text_to_image_prompt(
        message: Message,
        state: FSMContext,
        *,
        user_request: str,
        original_user_request: str | None = None,
        reference_image_path: str | None = None,
        latest_only: bool = True,
    ) -> None:
        data = await state.get_data()
        params = _text_to_image_params(data)
        original_for_state = str(original_user_request or data.get("original_user_request") or user_request).strip()
        reference_image = str(reference_image_path or data.get("prompt_reference_image_local_path") or "").strip()
        await state.update_data(
            original_user_request=original_for_state,
            last_grok_user_request=str(user_request or "").strip(),
            last_grok_reference_image_path=reference_image,
            final_prompt_text="",
            selected_model="",
            custom_prompt_used=False,
            prompt_display_text="",
            prompt_display_ready=False,
            prompt_display_pending=False,
        )
        generation_context = (
            f"Aspect ratio: {params['aspect_ratio']}; base resolution: {params['width']} x {params['height']}; "
            f"final resolution: {'enabled, estimated ' + params['final'] if params.get('final_resolution_enabled') else 'disabled, use base resolution'}; "
            f"persona LoRA: {'enabled' if params.get('persona_enabled') else 'disabled'}."
        )
        if reference_image:
            generation_context += " The user uploaded a reference image. First identify the subject, composition, scene, clothing, pose, style, and visible details, then combine them with the text request to write the final prompt."
        payload = {
            "prompt": user_request,
            "prompt_text": user_request,
            "message": user_request,
            "width": params["width"],
            "height": params["height"],
            "aspect_ratio": params["aspect_ratio"],
            "batch_size": PERSON_T2I_DEFAULT_BATCH_SIZE if str(params.get("text_to_image_workflow_profile") or "") == "person_t2i" else 1,
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
        if reference_image:
            payload["input_image_local_path"] = reference_image
            payload["image_local_path"] = reference_image
        await message.answer("正在让 Grok 生成最终提示词...")
        result = await _preview_internal_webapp_prompt(chat_id=int(message.chat.id), task_type="text_to_image", params=payload)
        prompt_text = str(result.get("prompt_text") or "").strip()
        selected_model = str(result.get("selected_model") or "").strip()
        if not prompt_text:
            raise RuntimeError("Grok 未返回可用提示词，请重新生成提示词。")
        await state.update_data(
            original_user_request=original_for_state,
            final_prompt_text=prompt_text,
            selected_model=selected_model,
            prompt_display_text=prompt_text,
            prompt_display_ready=True,
            prompt_display_pending=False,
        )
        await _show_text_to_image_prompt_review(message, state, prompt_text=prompt_text, selected_model=selected_model)

    async def _submit_text_to_image_from_state(message: Message, state: FSMContext) -> None:
        params, runtime_changed = await _refresh_text_to_image_runtime_state(state)
        if runtime_changed:
            profile = str(params.get("text_to_image_workflow_profile") or "zit_final")
            await message.answer(
                "\u540e\u53f0\u6587\u751f\u56fe\u5de5\u4f5c\u6d41\u5df2\u66f4\u65b0\uff0c"
                "\u672c\u6b21\u672a\u63d0\u4ea4\u5230\u961f\u5217\u3002\u8bf7\u6309\u6700\u65b0\u53c2\u6570\u91cd\u65b0\u9009\u62e9\u3002",
                reply_markup=_text_to_image_ratio_reply_keyboard(profile=profile),
            )
            return
        data = await state.get_data()
        params = _text_to_image_params(data)
        final_prompt = str(data.get("final_prompt_text") or "").strip()
        if not final_prompt:
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
            await message.answer("还没有可用的 Grok 提示词，请先输入图片需求。")
            return
        if (not bool(data.get("custom_prompt_used"))) and not bool(data.get("prompt_display_ready")):
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_revision)
            await message.answer(
                "当前提示词还没有通过中文校验，暂不提交到队列。请重新生成提示词，或输入自定义提示词。",
                reply_markup=_text_to_image_prompt_failure_reply_keyboard(),
            )
            return
        payload = {
            "prompt": final_prompt,
            "prompt_text": final_prompt,
            "message": final_prompt,
            "text_to_image_workflow_profile": str(params.get("text_to_image_workflow_profile") or ""),
            "text_to_image_workflow_path": str(data.get("text_to_image_workflow_path") or ""),
            "width": params["width"],
            "height": params["height"],
            "aspect_ratio": params["aspect_ratio"],
            "batch_size": PERSON_T2I_DEFAULT_BATCH_SIZE if str(params.get("text_to_image_workflow_profile") or "") == "person_t2i" else 1,
            "final_resolution_enabled": bool(params["final_resolution_enabled"]),
            "persona_enabled": bool(params["persona_enabled"]),
            "persona_lora": str(params.get("persona_lora") or ""),
            "persona_label": str(params.get("persona_label") or ""),
            "tg_use_llm_prompt": False,
            "tg_llm_prompt_enhanced": True,
            "tg_original_prompt": str(data.get("original_user_request") or "").strip(),
            "tg_llm_rewritten_prompt": final_prompt,
            "tg_llm_selected_model": str(data.get("selected_model") or "").strip(),
            "tg_prompt_display_text": str(data.get("prompt_display_text") or "").strip(),
            "custom_prompt_used": bool(data.get("custom_prompt_used")),
        }
        payload["remote_comfy_node_inputs"] = _text_to_image_remote_node_inputs(params)
        await submit_webapp_task_and_reply(message, "text_to_image", payload)
        await state.clear()

    async def _show_text_to_image_prompt_entry(message: Message, state: FSMContext) -> None:
        await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
        params = _text_to_image_params(await state.get_data())
        step = "4/4 输入图片需求或上传参考图" if params.get("persona_available") else "3/3 输入图片需求或上传参考图"
        await message.answer(
            _text_to_image_status_text(step=step, params=params)
            + "\n\n可以直接输入图片需求，也可以上传参考图片；上传图片时可在图片说明里补充要求。Grok 会识别图片内容，并结合你的文字生成最终提示词供你确认。",
            reply_markup=_text_to_image_prompt_entry_reply_keyboard(),
        )

    async def _show_text_to_image_prompt_mode(message: Message, state: FSMContext) -> None:
        await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode)
        params = _text_to_image_params(await state.get_data())
        step = "4/4 请选择提示词方式" if params.get("persona_available") else "3/3 请选择提示词方式"
        await message.answer(
            _text_to_image_status_text(step=step, params=params)
            + "\n\n请选择让 Grok 根据你的需求生成提示词，或直接输入自定义最终提示词。",
            reply_markup=_text_to_image_prompt_mode_reply_keyboard(),
        )

    async def _latest_text_to_image_task(chat_id: int) -> dict[str, Any]:
        tasks = await _fetch_internal_webapp_tg_tasks(chat_id=int(chat_id), limit=20)
        selected = next(
            (
                item
                for item in tasks
                if str(item.get("type") or "").strip() == "text_to_image"
                and str(item.get("status") or "").strip() == "success"
            ),
            None,
        )
        if selected is None:
            selected = next((item for item in tasks if str(item.get("type") or "").strip() == "text_to_image"), None)
        if not isinstance(selected, dict):
            raise RuntimeError("没有找到最近的文生图任务")
        task_id = str(selected.get("id") or "").strip()
        if not task_id:
            raise RuntimeError("最近的文生图任务缺少任务编号")
        return await _fetch_internal_webapp_tg_task_detail(chat_id=int(chat_id), task_id=task_id)

    async def _reroll_latest_text_to_image(message: Message, state: FSMContext) -> None:
        task = await _latest_text_to_image_task(int(message.chat.id))
        if str(task.get("type") or "").strip() != "text_to_image":
            raise RuntimeError("最近任务不是文生图任务")
        input_payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        payload, seed = _text_to_image_reroll_payload(input_payload)
        payload["tg_reroll_from_task_id"] = str(task.get("id") or "").strip()
        await state.clear()
        await message.answer(f"已切换 seed，重新提交生成。Seed: {seed}", reply_markup=_menu_keyboard())
        await submit_webapp_task_and_reply(message, "text_to_image", payload)
        logger.info("Submitted text_to_image reroll from latest task %s with seed %s", task.get("id"), seed)

    async def _continue_latest_text_to_image(message: Message, state: FSMContext) -> None:
        task = await _latest_text_to_image_task(int(message.chat.id))
        if str(task.get("type") or "").strip() != "text_to_image":
            raise RuntimeError("最近任务不是文生图任务")
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
        await message.answer("继续生成图片：保留上次参数，重新进入提示词步骤。", reply_markup=_menu_keyboard())
        await _show_text_to_image_prompt_mode(message, state)

    def _face_swap_resubmit_payload(input_payload: dict[str, Any], *, seedvr_upscale: bool = False) -> dict[str, Any]:
        target_image = str(input_payload.get("target_image_local_path") or input_payload.get("image_local_path") or "").strip()
        source_image = str(
            input_payload.get("source_image_local_path")
            or input_payload.get("reference_image_local_path")
            or input_payload.get("face_image_local_path")
            or ""
        ).strip()
        prompt = str(
            input_payload.get("prompt_text")
            or input_payload.get("prompt")
            or input_payload.get("message")
            or "自然换脸，保持目标图姿态、服装、光线和背景，只替换脸部身份"
        ).strip()
        if not target_image or not source_image:
            raise RuntimeError("上次人物换脸任务缺少原图或人脸参考图，无法重新提交。")
        payload = {
            "target_image_local_path": target_image,
            "source_image_local_path": source_image,
            "prompt": prompt,
            "prompt_text": prompt,
            "message": prompt,
            "tg_use_llm_prompt": False,
            "tg_llm_prompt_enhanced": True,
            "tg_original_prompt": str(input_payload.get("tg_original_prompt") or input_payload.get("tg_user_instruction") or prompt).strip(),
            "tg_llm_rewritten_prompt": prompt,
        }
        if seedvr_upscale:
            seed_value = 0
            try:
                seed_value = int(input_payload.get("face_swap_random_seed") or input_payload.get("seed") or 0)
            except Exception:
                seed_value = 0
            if seed_value > 0:
                payload["seed"] = seed_value
                payload["face_swap_random_seed"] = seed_value
            payload["face_swap_seedvr_upscale"] = True
            payload["remote_comfy_timeout_seconds"] = max(int(input_payload.get("remote_comfy_timeout_seconds") or 900), 900)
        else:
            seed_value = secrets.randbelow(TEXT_TO_IMAGE_MAX_SEED) + 1
            payload["seed"] = seed_value
            payload["face_swap_random_seed"] = seed_value
        return payload

    async def _resubmit_face_swap_from_task(
        message: Message,
        state: FSMContext,
        *,
        task_id: str,
        seedvr_upscale: bool = False,
    ) -> None:
        task = await _fetch_internal_webapp_tg_task_detail(chat_id=int(message.chat.id), task_id=str(task_id))
        if str(task.get("type") or "").strip() != "face_swap":
            raise RuntimeError("这条记录不是人物换脸任务，无法继续操作。")
        input_payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        payload = _face_swap_resubmit_payload(input_payload, seedvr_upscale=seedvr_upscale)
        if seedvr_upscale:
            payload["tg_seedvr_from_task_id"] = str(task_id)
        else:
            payload["tg_rerun_from_task_id"] = str(task_id)
        await state.clear()
        await submit_webapp_task_and_reply(message, "face_swap", payload)

    @router.callback_query(F.data.startswith("face_swap:"))
    async def on_face_swap_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None:
            await callback.answer()
            return
        if not _is_message_authorized(service, callback.message):
            await callback.answer("当前账号未授权", show_alert=True)
            return
        action = str(callback.data or "")
        if action == "face_swap:main_menu":
            await state.clear()
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await callback.message.answer("已返回主菜单。", reply_markup=_menu_keyboard())
            await callback.answer()
            return
        if action.startswith("face_swap:seedvr:") or action.startswith("face_swap:rerun:"):
            parts = action.split(":", 2)
            task_id = parts[2].strip() if len(parts) >= 3 else ""
            seedvr_upscale = parts[1] == "seedvr"
            if not task_id:
                await callback.answer("缺少任务编号", show_alert=True)
                return
            try:
                await _resubmit_face_swap_from_task(
                    callback.message,
                    state,
                    task_id=task_id,
                    seedvr_upscale=seedvr_upscale,
                )
            except Exception as exc:
                label = "SeedVR 放大" if seedvr_upscale else "重新生成"
                await callback.answer(f"{label}提交失败：{_format_tg_user_error(exc)}", show_alert=True)
                return
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await callback.answer("已提交 SeedVR 放大任务" if seedvr_upscale else "已提交重新生成任务")
            return
        await callback.answer("未知操作", show_alert=True)

    @router.callback_query(F.data.startswith("t2i:"))
    async def on_text_to_image_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None:
            await callback.answer()
            return
        if not _is_message_authorized(service, callback.message):
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
        if action.startswith("t2i:reroll:"):
            task_id = action.rsplit(":", 1)[-1].strip()
            try:
                task = await _fetch_internal_webapp_tg_task_detail(chat_id=int(callback.message.chat.id), task_id=task_id)
            except Exception as exc:
                await callback.answer(f"读取上次任务失败：{_format_tg_user_error(exc)}", show_alert=True)
                return
            if str(task.get("type") or "").strip() != "text_to_image":
                await callback.answer("这个任务不是文生图任务", show_alert=True)
                return
            input_payload = task.get("input") if isinstance(task.get("input"), dict) else {}
            try:
                payload, seed = _text_to_image_reroll_payload(input_payload)
            except Exception as exc:
                await callback.answer(f"重新生成图片失败：{_format_tg_user_error(exc)}", show_alert=True)
                return
            payload["tg_reroll_from_task_id"] = task_id
            await state.clear()
            await callback.answer("已切换 seed，重新提交生成")
            await submit_webapp_task_and_reply(callback.message, "text_to_image", payload)
            logger.info("Submitted text_to_image reroll from task %s with seed %s", task_id, seed)
            return
        if action.startswith("t2i:continue:"):
            task_id = action.rsplit(":", 1)[-1].strip()
            try:
                task = await _fetch_internal_webapp_tg_task_detail(chat_id=int(callback.message.chat.id), task_id=task_id)
            except Exception as exc:
                await callback.answer(f"读取上次任务失败：{_format_tg_user_error(exc)}", show_alert=True)
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
        params, runtime_changed = await _refresh_text_to_image_runtime_state(state)
        if runtime_changed:
            profile = str(params.get("text_to_image_workflow_profile") or "zit_final")
            text = (
                "\u540e\u53f0\u6587\u751f\u56fe\u5de5\u4f5c\u6d41\u5df2\u66f4\u65b0\uff0c"
                "\u5df2\u540c\u6b65\u6700\u65b0\u53ef\u9009\u53c2\u6570\u3002\n\n"
                + _text_to_image_status_text(step="1/4 \u8bf7\u9009\u62e9\u56fe\u50cf\u6bd4\u4f8b", params=params)
            )
            try:
                await callback.message.edit_text(text, reply_markup=_text_to_image_ratio_keyboard(profile=profile))
            except Exception:
                await callback.message.answer(text, reply_markup=_text_to_image_ratio_reply_keyboard(profile=profile))
            await callback.answer("\u5df2\u540c\u6b65\u540e\u53f0\u5de5\u4f5c\u6d41")
            return
        data = await state.get_data()
        if action.startswith("t2i:ratio:"):
            ratio = action.split(":", 2)[-1]
            if ratio in _text_to_image_ratio_options(str(_text_to_image_params(data).get("text_to_image_workflow_profile") or "zit_final")):
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
                    await callback.message.edit_text(
                        _text_to_image_status_text(step="2/4 请选择最终分辨率", params=option),
                        reply_markup=_text_to_image_resolution_keyboard(
                            final_resolution_enabled=final_enabled,
                            final_resolution_available=bool(option.get("final_resolution_available")),
                        ),
                    )
                except Exception:
                    await callback.message.answer(
                        _text_to_image_status_text(step="2/4 请选择最终分辨率", params=option),
                        reply_markup=_text_to_image_resolution_keyboard(
                            final_resolution_enabled=final_enabled,
                            final_resolution_available=bool(option.get("final_resolution_available")),
                        ),
                    )
                await callback.answer("请选择分辨率")
                return
            await callback.answer("无效比例", show_alert=True)
            return
        if action == "t2i:next:resolution":
            params = _text_to_image_params(data)
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_resolution)
            try:
                await callback.message.edit_text(
                    _text_to_image_status_text(step="2/4 请选择最终分辨率", params=params),
                    reply_markup=_text_to_image_resolution_keyboard(
                        final_resolution_enabled=bool(params["final_resolution_enabled"]),
                        selected=bool(params.get("resolution_selected")),
                        final_resolution_available=bool(params.get("final_resolution_available")),
                    ),
                )
            except Exception:
                await callback.message.answer(
                    _text_to_image_status_text(step="2/4 请选择最终分辨率", params=params),
                    reply_markup=_text_to_image_resolution_keyboard(
                        final_resolution_enabled=bool(params["final_resolution_enabled"]),
                        selected=bool(params.get("resolution_selected")),
                        final_resolution_available=bool(params.get("final_resolution_available")),
                    ),
                )
            await callback.answer("请选择分辨率")
            return
        if action == "t2i:back:ratio":
            params = _text_to_image_params(data)
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_ratio)
            try:
                await callback.message.edit_text(
                    _text_to_image_status_text(step="1/4 请选择图像比例", params=params),
                    reply_markup=_text_to_image_ratio_keyboard(
                        selected_ratio=params["aspect_ratio"] if params.get("ratio_selected") else "",
                        profile=str(params.get("text_to_image_workflow_profile") or "zit_final"),
                    ),
                )
            except Exception:
                await callback.message.answer(
                    _text_to_image_status_text(step="1/4 请选择图像比例", params=params),
                    reply_markup=_text_to_image_ratio_keyboard(
                        selected_ratio=params["aspect_ratio"] if params.get("ratio_selected") else "",
                        profile=str(params.get("text_to_image_workflow_profile") or "zit_final"),
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
            params = _text_to_image_params({**data, "prompt_mode_selected": True, "prompt_mode_label": "Grok 生成"})
            text = (
                _text_to_image_status_text(
                    step="4/4 请输入图片需求或上传参考图" if params.get("persona_available") else "3/3 请输入图片需求或上传参考图",
                    params=params,
                )
                + "\n\n可以直接输入图片需求，也可以上传参考图片；上传图片时可在图片说明里补充要求。Grok 会识别图片内容，并结合你的文字生成最终提示词供你确认。"
            )
            try:
                await callback.message.edit_text(text, reply_markup=_text_to_image_prompt_entry_keyboard())
            except Exception:
                await callback.message.answer(text, reply_markup=_text_to_image_prompt_entry_keyboard())
            await callback.answer("请输入需求或上传参考图")
            return
        if action.startswith("t2i:final:") or action == "t2i:toggle_final":
            params = _text_to_image_params(data)
            if action == "t2i:toggle_final":
                final_enabled = not bool(params["final_resolution_enabled"])
            else:
                final_enabled = action.endswith(":on")
            if final_enabled and not bool(params.get("final_resolution_available")):
                await callback.answer("当前工作流不支持最终分辨率，请选择使用基础分辨率。", show_alert=True)
                return
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
            if params.get("persona_available"):
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_persona)
                text = _text_to_image_status_text(step="3/4 请选择人设 LoRA", params=params)
                markup = _text_to_image_persona_keyboard(
                    persona_enabled=bool(params["persona_enabled"]),
                    persona_lora=str(params.get("persona_lora") or ""),
                    selected=bool(params.get("persona_selected")),
                    profile=str(params.get("text_to_image_workflow_profile") or "zit_final"),
                )
                try:
                    await callback.message.edit_text(text, reply_markup=markup)
                except Exception:
                    await callback.message.answer(text, reply_markup=markup)
                await callback.answer("请选择人设")
            else:
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode)
                step = "3/3 请选择提示词方式"
                text = _text_to_image_status_text(step=step, params=params) + "\n\n请选择让 Grok 根据你的需求生成提示词，或直接输入自定义最终提示词。"
                try:
                    await callback.message.edit_text(text, reply_markup=_text_to_image_prompt_mode_keyboard())
                except Exception:
                    await callback.message.answer(text, reply_markup=_text_to_image_prompt_mode_keyboard())
                await callback.answer("请选择提示词方式")
            return
        if action == "t2i:next:persona":
            params = _text_to_image_params(data)
            if params.get("persona_available"):
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_persona)
                text = _text_to_image_status_text(step="3/4 请选择人设 LoRA", params=params)
                markup = _text_to_image_persona_keyboard(
                    persona_enabled=bool(params["persona_enabled"]),
                    persona_lora=str(params.get("persona_lora") or ""),
                    selected=bool(params.get("persona_selected")),
                    profile=str(params.get("text_to_image_workflow_profile") or "zit_final"),
                )
                try:
                    await callback.message.edit_text(text, reply_markup=markup)
                except Exception:
                    await callback.message.answer(text, reply_markup=markup)
                await callback.answer("请选择人设")
            else:
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode)
                text = _text_to_image_status_text(step="3/3 请选择提示词方式", params=params) + "\n\n请选择让 Grok 根据你的需求生成提示词，或直接输入自定义最终提示词。"
                try:
                    await callback.message.edit_text(text, reply_markup=_text_to_image_prompt_mode_keyboard())
                except Exception:
                    await callback.message.answer(text, reply_markup=_text_to_image_prompt_mode_keyboard())
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
                await callback.message.edit_text(
                    _text_to_image_status_text(step="2/4 请选择最终分辨率", params=params),
                    reply_markup=_text_to_image_resolution_keyboard(
                        final_resolution_enabled=bool(params["final_resolution_enabled"]),
                        selected=bool(params.get("resolution_selected")),
                        final_resolution_available=bool(params.get("final_resolution_available")),
                    ),
                )
            except Exception:
                await callback.message.answer(
                    _text_to_image_status_text(step="2/4 请选择最终分辨率", params=params),
                    reply_markup=_text_to_image_resolution_keyboard(
                        final_resolution_enabled=bool(params["final_resolution_enabled"]),
                        selected=bool(params.get("resolution_selected")),
                        final_resolution_available=bool(params.get("final_resolution_available")),
                    ),
                )
            await callback.answer("已返回分辨率")
            return
        if action.startswith("t2i:persona:"):
            persona_key = action.rsplit(":", 1)[-1]
            params = _text_to_image_params(data)
            profile = str(params.get("text_to_image_workflow_profile") or "zit_final")
            options = _text_to_image_persona_options(profile=profile)
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
                persona_lora=selected_lora or _text_to_image_default_persona_path(profile=profile),
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
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode)
            text = (
                _text_to_image_status_text(step="4/4 请选择提示词方式", params=params)
                + "\n\n请选择让 Grok 根据你的需求生成提示词，或直接输入自定义最终提示词。"
            )
            try:
                await callback.message.edit_text(text, reply_markup=_text_to_image_prompt_mode_keyboard())
            except Exception:
                await callback.message.answer(text, reply_markup=_text_to_image_prompt_mode_keyboard())
            await callback.answer("请选择提示词方式")
            return
        if action == "t2i:next:prompt":
            params = _text_to_image_params(data)
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode)
            text = (
                _text_to_image_status_text(step="4/4 请选择提示词方式", params=params)
                + "\n\n请选择让 Grok 根据你的需求生成提示词，或直接输入自定义最终提示词。"
            )
            try:
                await callback.message.edit_text(text, reply_markup=_text_to_image_prompt_mode_keyboard())
            except Exception:
                await callback.message.answer(text, reply_markup=_text_to_image_prompt_mode_keyboard())
            await callback.answer("请选择提示词方式")
            return
        if action == "t2i:back:prompt_mode":
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode)
            params = _text_to_image_params(data)
            step = "4/4 请选择提示词方式" if params.get("persona_available") else "3/3 请选择提示词方式"
            text = _text_to_image_status_text(step=step, params=params) + "\n\n请选择让 Grok 根据你的需求生成提示词，或直接输入自定义最终提示词。"
            try:
                await callback.message.edit_text(text, reply_markup=_text_to_image_prompt_mode_keyboard())
            except Exception:
                await callback.message.answer(text, reply_markup=_text_to_image_prompt_mode_keyboard())
            await callback.answer("已返回提示词方式")
            return
        if action == "t2i:back:persona":
            params = _text_to_image_params(data)
            await state.update_data(prompt_mode_selected=False, prompt_mode_label="")
            params = _text_to_image_params({**data, "prompt_mode_selected": False, "prompt_mode_label": ""})
            if params.get("persona_available"):
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_persona)
                text = _text_to_image_status_text(step="3/4 请选择人设 LoRA", params=params)
                markup = _text_to_image_persona_keyboard(
                    persona_enabled=bool(params["persona_enabled"]),
                    persona_lora=str(params.get("persona_lora") or ""),
                    selected=bool(params.get("persona_selected")),
                    profile=str(params.get("text_to_image_workflow_profile") or "zit_final"),
                )
                try:
                    await callback.message.edit_text(text, reply_markup=markup)
                except Exception:
                    await callback.message.answer(text, reply_markup=markup)
            else:
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_resolution)
                text = _text_to_image_status_text(step="2/3 请选择最终分辨率", params=params)
                markup = _text_to_image_resolution_keyboard(
                    final_resolution_enabled=bool(params["final_resolution_enabled"]),
                    selected=bool(params.get("resolution_selected")),
                    final_resolution_available=bool(params.get("final_resolution_available")),
                )
                try:
                    await callback.message.edit_text(text, reply_markup=markup)
                except Exception:
                    await callback.message.answer(text, reply_markup=markup)
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
                reply_markup=_text_to_image_ratio_reply_keyboard(profile=str(params.get("text_to_image_workflow_profile") or "zit_final")),
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
            params = _text_to_image_params({**data, "prompt_mode_selected": True, "prompt_mode_label": "自定义输入"})
            text = (
                _text_to_image_status_text(
                    step="4/4 请输入自定义最终提示词" if params.get("persona_available") else "3/3 请输入自定义最终提示词",
                    params=params,
                )
                + "\n\n请输入自定义最终提示词。下一条消息会跳过 Grok，直接提交到 ComfyUI 工作流生成。"
            )
            try:
                await callback.message.edit_text(text, reply_markup=_text_to_image_prompt_entry_keyboard())
            except Exception:
                await callback.message.answer(text, reply_markup=_text_to_image_prompt_entry_keyboard())
            await callback.answer()
            return
        if action == "t2i:regen":
            original = str(data.get("last_grok_user_request") or data.get("original_user_request") or data.get("final_prompt_text") or "").strip()
            if not original:
                await callback.answer("没有原始需求，请重新输入", show_alert=True)
                return
            try:
                await _preview_text_to_image_prompt(
                    callback.message,
                    state,
                    user_request=original,
                    reference_image_path=str(data.get("last_grok_reference_image_path") or data.get("prompt_reference_image_local_path") or ""),
                )
            except Exception as exc:
                await callback.message.answer(
                    f"Grok 提示词生成失败：{_format_grok_preview_error(exc)}",
                    reply_markup=_text_to_image_prompt_failure_reply_keyboard(),
                )
            await callback.answer()
            return
        if action == "t2i:retry_display":
            final_prompt = _strip_prompt_char_count_note(str(data.get("final_prompt_text") or "").strip(), preserve_english=True)
            if not final_prompt:
                await callback.answer("没有已保存的提示词，请重新生成", show_alert=True)
                return
            try:
                await _show_text_to_image_prompt_review(
                    callback.message,
                    state,
                    prompt_text=final_prompt,
                    selected_model=str(data.get("selected_model") or "").strip(),
                )
                await callback.answer("中文预览已通过")
            except Exception as exc:
                await _show_text_to_image_display_pending(callback.message, state, exc=exc)
                await callback.answer("中文预览未通过", show_alert=True)
            return
        if action == "t2i:submit":
            try:
                await _submit_text_to_image_from_state(callback.message, state)
                await callback.answer("已提交生成")
            except Exception as exc:
                await callback.message.answer(f"文生图任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())
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
        params, runtime_changed = await _refresh_text_to_image_runtime_state(state)
        if runtime_changed:
            profile = str(params.get("text_to_image_workflow_profile") or "zit_final")
            await message.answer(
                "\u540e\u53f0\u6587\u751f\u56fe\u5de5\u4f5c\u6d41\u5df2\u66f4\u65b0\uff0c"
                "\u5df2\u540c\u6b65\u6700\u65b0\u53ef\u9009\u53c2\u6570\u3002\n\n"
                + _text_to_image_status_text(step="1/4 \u8bf7\u9009\u62e9\u56fe\u50cf\u6bd4\u4f8b", params=params),
                reply_markup=_text_to_image_ratio_reply_keyboard(profile=profile),
            )
            return
        data = await state.get_data()
        params = _text_to_image_params(data)
        current_state = await state.get_state()
        text = _message_text(message)

        if current_state == ProductionWorkflowForm.text_to_image_waiting_for_ratio.state:
            selected_ratio = ""
            profile = str(params.get("text_to_image_workflow_profile") or "zit_final")
            for ratio, option in _text_to_image_ratio_options(profile).items():
                if text == str(option.get("label") or ""):
                    selected_ratio = ratio
                    break
            if selected_ratio:
                option = _text_to_image_params({**data, "aspect_ratio": selected_ratio})
                option["ratio_selected"] = True
                option["resolution_selected"] = False
                option["persona_selected"] = False
                option["prompt_mode_selected"] = False
                await state.update_data(
                    aspect_ratio=selected_ratio,
                    width=option["width"],
                    height=option["height"],
                    final_resolution_enabled=bool(option["final_resolution_enabled"]),
                    ratio_selected=True,
                    resolution_selected=False,
                    persona_selected=False,
                    prompt_mode_selected=False,
                    prompt_mode_label="",
                )
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_resolution)
                await message.answer(
                    _text_to_image_status_text(step="2/4 请选择最终分辨率", params=option),
                    reply_markup=_text_to_image_resolution_reply_keyboard(
                        final_resolution_available=bool(option.get("final_resolution_available"))
                    ),
                )
                return
            await message.answer(
                _text_to_image_status_text(step="1/4 请先选择图像比例", params=params),
                reply_markup=_text_to_image_ratio_reply_keyboard(profile=str(params.get("text_to_image_workflow_profile") or "zit_final")),
            )
            return

        if current_state == ProductionWorkflowForm.text_to_image_waiting_for_resolution.state:
            if text == "上一步":
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_ratio)
                await message.answer(
                    _text_to_image_status_text(step="1/4 请选择图像比例", params=params),
                    reply_markup=_text_to_image_ratio_reply_keyboard(profile=str(params.get("text_to_image_workflow_profile") or "zit_final")),
                )
                return
            if text in {"使用基础分辨率", "开启最终分辨率"}:
                if text == "开启最终分辨率" and not bool(params.get("final_resolution_available")):
                    await message.answer(
                        "当前工作流不支持最终分辨率，请选择“使用基础分辨率”。",
                        reply_markup=_text_to_image_resolution_reply_keyboard(final_resolution_available=False),
                    )
                    return
                final_enabled = text == "开启最终分辨率"
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
                if params.get("persona_available"):
                    await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_persona)
                    await message.answer(
                        _text_to_image_status_text(step="3/4 请选择人设 LoRA", params=params),
                        reply_markup=_text_to_image_persona_reply_keyboard(profile=str(params.get("text_to_image_workflow_profile") or "zit_final")),
                    )
                else:
                    await _show_text_to_image_prompt_mode(message, state)
                return
                await message.answer(
                    _text_to_image_status_text(step="2/4 请先选择最终分辨率", params=params),
                    reply_markup=_text_to_image_resolution_reply_keyboard(
                        final_resolution_available=bool(params.get("final_resolution_available"))
                    ),
            )
            return

        if current_state == ProductionWorkflowForm.text_to_image_waiting_for_persona.state:
            if text == "上一步":
                await state.update_data(persona_selected=False, prompt_mode_selected=False, prompt_mode_label="")
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_resolution)
                await message.answer(
                    _text_to_image_status_text(step="2/4 请选择最终分辨率", params=params),
                    reply_markup=_text_to_image_resolution_reply_keyboard(
                        final_resolution_available=bool(params.get("final_resolution_available"))
                    ),
                )
                return
            persona_enabled = text != "不使用人设"
            selected_lora = ""
            if persona_enabled:
                for option in _text_to_image_persona_options(profile=str(params.get("text_to_image_workflow_profile") or "zit_final")):
                    if text == str(option.get("label") or ""):
                        selected_lora = str(option.get("path") or "")
                        break
                if not selected_lora:
                    await message.answer(
                        _text_to_image_status_text(step="3/4 请先选择人设 LoRA", params=params),
                        reply_markup=_text_to_image_persona_reply_keyboard(profile=str(params.get("text_to_image_workflow_profile") or "zit_final")),
                    )
                    return
            await state.update_data(
                persona_enabled=persona_enabled,
                persona_lora=selected_lora or _text_to_image_default_persona_path(profile=str(params.get("text_to_image_workflow_profile") or "zit_final")),
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
            await _show_text_to_image_prompt_mode(message, state)
            return

        if current_state == ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode.state:
            if text == "上一步":
                if params.get("persona_available"):
                    await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_persona)
                    await message.answer(
                        _text_to_image_status_text(step="3/4 请选择人设 LoRA", params=params),
                        reply_markup=_text_to_image_persona_reply_keyboard(profile=str(params.get("text_to_image_workflow_profile") or "zit_final")),
                    )
                else:
                    await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_resolution)
                    await message.answer(
                        _text_to_image_status_text(step="2/3 请选择最终分辨率", params=params),
                        reply_markup=_text_to_image_resolution_reply_keyboard(
                            final_resolution_available=bool(params.get("final_resolution_available"))
                        ),
                    )
                return
            if text == "让 Grok 生成提示词":
                await state.update_data(prompt_mode_selected=True, prompt_mode_label="Grok 生成")
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
                await message.answer(
                    _text_to_image_status_text(
                        step="4/4 请输入图片需求或上传参考图" if params.get("persona_available") else "3/3 请输入图片需求或上传参考图",
                        params={**params, "prompt_mode_selected": True, "prompt_mode_label": "Grok 生成"},
                    )
                    + "\n\n可以直接输入图片需求，也可以上传参考图片；上传图片时可在图片说明里补充要求。Grok 会识别图片内容，并结合你的文字生成最终提示词供你确认。",
                    reply_markup=_text_to_image_prompt_entry_reply_keyboard(),
                )
                return
            if text == "输入自定义提示词":
                await state.update_data(prompt_mode_selected=True, prompt_mode_label="自定义提示词", custom_prompt_used=True)
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_custom_prompt)
                await message.answer(
                    _text_to_image_status_text(
                        step="4/4 请输入自定义最终提示词" if params.get("persona_available") else "3/3 请输入自定义最终提示词",
                        params={**params, "prompt_mode_selected": True, "prompt_mode_label": "自定义提示词"},
                    )
                    + "\n\n请输入自定义最终提示词。",
                    reply_markup=_text_to_image_prompt_entry_reply_keyboard(),
                )
                return
            step = "4/4 请先选择提示词方式" if params.get("persona_available") else "3/3 请先选择提示词方式"
            await message.answer(
                _text_to_image_status_text(step=step, params=params),
                reply_markup=_text_to_image_prompt_mode_reply_keyboard(),
            )

    @router.message(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
    async def on_text_to_image_prompt_v2(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        params, runtime_changed = await _refresh_text_to_image_runtime_state(state)
        if runtime_changed:
            profile = str(params.get("text_to_image_workflow_profile") or "zit_final")
            await message.answer(
                "\u540e\u53f0\u6587\u751f\u56fe\u5de5\u4f5c\u6d41\u5df2\u66f4\u65b0\uff0c"
                "\u5df2\u540c\u6b65\u6700\u65b0\u53ef\u9009\u53c2\u6570\u3002\n\n"
                + _text_to_image_status_text(step="1/4 \u8bf7\u9009\u62e9\u56fe\u50cf\u6bd4\u4f8b", params=params),
                reply_markup=_text_to_image_ratio_reply_keyboard(profile=profile),
            )
            return
        prompt = _message_text(message)
        data = await state.get_data()
        if prompt == "使用这个提示词生成" and not _image_ext_from_message(message):
            if str(data.get("final_prompt_text") or "").strip():
                try:
                    await _submit_text_to_image_from_state(message, state)
                except Exception as exc:
                    await message.answer(f"文生图任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_text_to_image_prompt_reply_keyboard())
                return
            await message.answer("还没有可用的最终提示词，请先输入图片需求。", reply_markup=_text_to_image_prompt_entry_reply_keyboard())
            return
        if prompt == "上一步" and not _image_ext_from_message(message):
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode)
            await _show_text_to_image_prompt_mode(message, state)
            return
        if prompt == "输入自定义提示词" and not _image_ext_from_message(message):
            await state.update_data(prompt_mode_selected=True, prompt_mode_label="自定义输入")
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_custom_prompt)
            params = _text_to_image_params({**data, "prompt_mode_selected": True, "prompt_mode_label": "自定义输入"})
            await message.answer(
                _text_to_image_status_text(
                    step="4/4 请输入自定义最终提示词" if params.get("persona_available") else "3/3 请输入自定义最终提示词",
                    params=params,
                )
                + "\n\n请输入自定义最终提示词。下一条消息会跳过 Grok，直接提交到 ComfyUI 工作流生成。",
                reply_markup=_text_to_image_prompt_entry_reply_keyboard(),
            )
            return
        if prompt == "重新生成提示词" and not _image_ext_from_message(message):
            original = str(data.get("last_grok_user_request") or data.get("original_user_request") or "").strip()
            if not original:
                await message.answer("没有原始需求，请先输入图片需求。", reply_markup=_text_to_image_prompt_entry_reply_keyboard())
                return
            try:
                await _preview_text_to_image_prompt(
                    message,
                    state,
                    user_request=original,
                    reference_image_path=str(data.get("last_grok_reference_image_path") or data.get("prompt_reference_image_local_path") or ""),
                )
            except Exception as exc:
                await message.answer(
                    f"Grok 提示词生成失败：{_format_grok_preview_error(exc)}",
                    reply_markup=_text_to_image_prompt_failure_reply_keyboard(),
                )
            return
        reference_image_path = ""
        image_suffix = _image_ext_from_message(message)
        if image_suffix:
            work_dir = Path(str(data.get("work_dir") or service.create_job_dir(prefix="tg_text_to_image_ref")))
            target = work_dir / f"prompt_reference_{int(message.message_id)}{image_suffix}"
            await _download_message_media(message, target)
            reference_image_path = str(target.resolve())
            await state.update_data(
                work_dir=str(work_dir),
                prompt_reference_image_local_path=reference_image_path,
            )
            if not prompt:
                prompt = "CRITICAL FORMAT RULE - COPY THIS EXACT PATTERN: The prompt MUST be ONE continuous Chinese sentence with EXACTLY these 9 segments separated by commas (，): 1.[人物+全身姿态+场景] 2.穿着[服装] 3.[暴露器官] 4.她的[左手动作]而[右手动作] 5.她的身体[朝向] 6.她的头[转向+眼神] 7.[背景] 8.[光线] 9.[技术参数]。 You MUST include segment 4, 5, and 6 exactly as shown. DO NOT skip them. DO NOT change their order. Example: 一位美丽女郎全身站立在豪华卧室中，穿着优雅的黑色丝绸睡裙，薄薄的意大利面条肩带，低领部部分解开露出深深的乳沟和侧乳，她的左手放在臀部上而右手触摸着裸露的大腿，她的身体微微侧向一边朝向镜头，她的头转向直视镜头带着诱惑的眼神，卧室背景有大大的特大号床铺，白色床单凌乱不堪，柔和的卧室灯投射出温柔的阴影，浅景深让主体清晰而背景微微模糊，真实皮肤纹理，细节的织物褶皱，自然的身体曲线，高细节，写实摄影风格，电影摄影。 This is the ONLY acceptable format."
        if not prompt:
            params = _text_to_image_params(data)
            await message.answer(
                _text_to_image_status_text(step="4/4 请输入图片需求或上传参考图" if params.get("persona_available") else "3/3 请输入图片需求或上传参考图", params=params)
                + "\n\n可以发送文字需求，也可以上传一张参考图片；上传图片时可在图片说明里补充要求。",
                reply_markup=_text_to_image_prompt_entry_reply_keyboard(),
            )
            return
        try:
            await _preview_text_to_image_prompt(
                message,
                state,
                user_request=prompt,
                reference_image_path=reference_image_path or str(data.get("prompt_reference_image_local_path") or ""),
            )
        except Exception as exc:
            params = _text_to_image_params(await state.get_data())
            await message.answer(
                f"Grok 提示词生成失败：{_format_grok_preview_error(exc)}",
                reply_markup=_text_to_image_prompt_failure_reply_keyboard(),
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
            await message.answer("请直接输入调整要求，或点击“使用这个提示词生成”。", reply_markup=_text_to_image_prompt_reply_keyboard())
            return
        data = await state.get_data()
        if revision == "使用这个提示词生成":
            try:
                await _submit_text_to_image_from_state(message, state)
            except Exception as exc:
                await message.answer(f"文生图任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_text_to_image_prompt_reply_keyboard())
            return
        if revision in {"输入自定义提示词提交", "输入自定义提示词"}:
            await state.update_data(prompt_mode_selected=True, prompt_mode_label="自定义输入")
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_custom_prompt)
            await message.answer("请输入自定义最终提示词。下一条消息会跳过 Grok，直接提交到 ComfyUI 工作流生成。", reply_markup=_text_to_image_prompt_entry_reply_keyboard())
            return
        if revision == "上一步":
            await _show_text_to_image_prompt_mode(message, state)
            return
        if revision == "继续让 Grok 调整":
            await message.answer("请直接输入你希望 Grok 如何调整提示词，例如：更写实、换成夜景、保留人物姿势但改变服装。", reply_markup=_text_to_image_prompt_reply_keyboard())
            return
        if revision == "重新生成提示词":
            original = str(data.get("last_grok_user_request") or data.get("original_user_request") or data.get("final_prompt_text") or "").strip()
            if not original:
                await message.answer("没有原始需求，请重新输入。", reply_markup=_text_to_image_prompt_entry_reply_keyboard())
                await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
                return
            try:
                await _preview_text_to_image_prompt(
                    message,
                    state,
                    user_request=original,
                    reference_image_path=str(data.get("last_grok_reference_image_path") or data.get("prompt_reference_image_local_path") or ""),
                )
            except Exception as exc:
                await message.answer(
                    f"Grok 提示词生成失败：{_format_grok_preview_error(exc)}",
                    reply_markup=_text_to_image_prompt_failure_reply_keyboard(),
                )
            return
        if revision == "返回参数设置":
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_ratio)
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
            await state.update_data(
                ratio_selected=False,
                resolution_selected=False,
                persona_selected=False,
                prompt_mode_selected=False,
                prompt_mode_label="",
            )
            await message.answer(
                _text_to_image_status_text(step="1/4 请重新选择图像比例", params=params),
                reply_markup=_text_to_image_ratio_reply_keyboard(profile=str(params.get("text_to_image_workflow_profile") or "zit_final")),
            )
            return
        original = str(data.get("original_user_request") or "").strip()
        current = str(data.get("final_prompt_text") or "").strip()
        combined = "\n".join(
            part
            for part in [
                f"Original request: {original}" if original else "",
                f"Current prompt: {current}" if current else "",
                f"Revision request: {revision}",
                "Rewrite the current prompt according to the revision request, preserve explicit user requirements, and output only the latest final prompt.",
                "Do not output labels such as Original request, Current prompt, or Revision request. Do not repeat the old prompt as a separate block, and do not paste the context text into the result.",
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
                reply_markup=_text_to_image_prompt_failure_reply_keyboard(),
            )

    @router.message(ProductionWorkflowForm.text_to_image_waiting_for_custom_prompt)
    async def on_text_to_image_custom_prompt(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        custom_prompt = _message_text(message)
        if custom_prompt == "上一步":
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt_mode)
            await _show_text_to_image_prompt_mode(message, state)
            return
        if not custom_prompt:
            await message.answer("请输入自定义最终提示词。", reply_markup=_text_to_image_prompt_entry_reply_keyboard())
            return
        data = await state.get_data()
        await state.update_data(
            final_prompt_text=custom_prompt,
            selected_model="自定义提示词",
            original_user_request=str(data.get("original_user_request") or custom_prompt).strip(),
            custom_prompt_used=True,
            prompt_display_ready=True,
            prompt_display_pending=False,
        )
        try:
            await message.answer("已收到自定义提示词，正在提交生成。")
            await _submit_text_to_image_from_state(message, state)
        except Exception as exc:
            await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_custom_prompt)
            await message.answer(
                f"自定义提示词提交失败：{_format_tg_user_error(exc)}\n\n请重新输入提示词，或返回上一步。",
                reply_markup=_text_to_image_prompt_entry_reply_keyboard(),
            )

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
            "tg_user_instruction": f"User text-to-image request: {prompt}",
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "text_to_image", params)
        except Exception as exc:
            await message.answer(f"文生图任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

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
            await message.answer("文生图\n步骤 1/1：可以直接输入图片需求，也可以上传参考图片并在图片说明里补充要求。", reply_markup=_image_edit_keyboard())
            return
        params = {
            "prompt": prompt,
            "prompt_text": prompt,
            "message": prompt,
            "tg_use_llm_prompt": True,
            "tg_user_instruction": f"User text-to-image request: {prompt}",
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "text_to_image", params)
        except Exception as exc:
            await message.answer(f"文生图任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

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
            "tg_user_instruction": f"User {mode} request: {prompt}",
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "image_generate", params)
        except Exception as exc:
            await message.answer(f"{title}任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

    @router.message(ProductionWorkflowForm.image_edit_waiting_for_image)
    async def on_image_edit_image(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        text = _canonical_button_text(_message_text(message))
        if text == "上一步":
            await start_image_generate_flow(message, state)
            return
        suffix = _image_ext_from_message(message)
        data = await state.get_data()
        single_input = str(data.get("image_edit_mode") or "").strip() == "single"
        title = "单图编辑" if single_input else "图片编辑"
        total_steps = "3" if single_input else "4"
        if suffix is None:
            await message.answer(f"{title}\n步骤 1/{total_steps}：请上传需要编辑的原图。", reply_markup=_image_task_step_keyboard(back=False))
            return
        work_dir = Path(str(data.get("work_dir") or service.create_job_dir(prefix="tg_image_edit")))
        target = work_dir / f"input{suffix}"
        await _download_message_media(message, target)
        await state.update_data(work_dir=str(work_dir), input_image_local_path=str(target.resolve()))
        if single_input:
            await state.update_data(reference_image_local_path=str(target.resolve()))
            await state.set_state(ProductionWorkflowForm.image_edit_waiting_for_prompt)
            await message.answer("单图编辑\n步骤 2/3：请输入这次图片编辑要求。", reply_markup=_image_task_step_keyboard())
            return
        await state.set_state(ProductionWorkflowForm.image_edit_waiting_for_reference_image)
        await message.answer("图片编辑\n步骤 2/4：请上传参考图或素材图。", reply_markup=_image_task_step_keyboard())

    @router.message(ProductionWorkflowForm.image_edit_waiting_for_reference_image)
    async def on_image_edit_reference_image(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        text = _canonical_button_text(_message_text(message))
        if text == "上一步":
            await state.set_state(ProductionWorkflowForm.image_edit_waiting_for_image)
            await message.answer("图片编辑\n步骤 1/4：请重新上传需要编辑的原图。", reply_markup=_image_task_step_keyboard(back=False))
            return
        suffix = _image_ext_from_message(message)
        if suffix is None:
            await message.answer("图片编辑\n步骤 2/4：请上传参考图或素材图。", reply_markup=_image_task_step_keyboard())
            return
        data = await state.get_data()
        work_dir = Path(str(data.get("work_dir") or service.create_job_dir(prefix="tg_image_edit")))
        target = work_dir / f"reference{suffix}"
        await _download_message_media(message, target)
        await state.update_data(work_dir=str(work_dir), reference_image_local_path=str(target.resolve()))
        await state.set_state(ProductionWorkflowForm.image_edit_waiting_for_prompt)
        await message.answer("图片编辑\n步骤 3/4：请输入这次图片编辑要求。", reply_markup=_image_task_step_keyboard())

    @router.message(ProductionWorkflowForm.image_edit_waiting_for_prompt)
    async def on_image_edit_prompt(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        data = await state.get_data()
        single_input = str(data.get("image_edit_mode") or "").strip() == "single"
        title = "单图编辑" if single_input else "图片编辑"
        total_steps = "3" if single_input else "4"
        text = _canonical_button_text(_message_text(message))
        if text == "上一步":
            if single_input:
                await state.set_state(ProductionWorkflowForm.image_edit_waiting_for_image)
                await message.answer("单图编辑\n步骤 1/3：请重新上传需要编辑的原图。", reply_markup=_image_task_step_keyboard(back=False))
            else:
                await state.set_state(ProductionWorkflowForm.image_edit_waiting_for_reference_image)
                await message.answer("图片编辑\n步骤 2/4：请重新上传参考图或素材图。", reply_markup=_image_task_step_keyboard())
            return
        prompt = _message_text(message)
        if not prompt:
            await message.answer(f"{title}\n步骤 {int(total_steps) - 1}/{total_steps}：请直接输入这次图片编辑要求。", reply_markup=_image_task_step_keyboard())
            return
        await state.update_data(image_edit_prompt=prompt)
        await state.set_state(ProductionWorkflowForm.image_edit_waiting_for_confirm)
        submit_text = "提交单图编辑任务" if single_input else "提交图片编辑任务"
        workflow_type = "single_image_edit" if single_input else "get_nano_banana"
        await message.answer(
            "\n".join(
                [
                    title,
                    f"步骤 {total_steps}/{total_steps}：请确认任务信息，点击提交后才会进入后台队列。",
                    _tg_mapped_workflow_line(workflow_type),
                    f"编辑要求：{prompt}",
                ]
            ),
            reply_markup=_image_task_confirm_keyboard(submit_text),
        )

    @router.message(ProductionWorkflowForm.image_edit_waiting_for_confirm)
    async def on_image_edit_confirm(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        data = await state.get_data()
        single_input = str(data.get("image_edit_mode") or "").strip() == "single"
        title = "单图编辑" if single_input else "图片编辑"
        total_steps = "3" if single_input else "4"
        submit_text = "提交单图编辑任务" if single_input else "提交图片编辑任务"
        task_type = "single_image_edit" if single_input else "get_nano_banana"
        text = _canonical_button_text(_message_text(message))
        if text == "上一步":
            await state.set_state(ProductionWorkflowForm.image_edit_waiting_for_prompt)
            await message.answer(f"{title}\n步骤 {int(total_steps) - 1}/{total_steps}：请重新输入这次图片编辑要求。", reply_markup=_image_task_step_keyboard())
            return
        if text != submit_text:
            await message.answer(f"{title}\n步骤 {total_steps}/{total_steps}：请点击「{submit_text}」后再提交。", reply_markup=_image_task_confirm_keyboard(submit_text))
            return
        prompt = str(data.get("image_edit_prompt") or "").strip()
        if not prompt:
            await state.set_state(ProductionWorkflowForm.image_edit_waiting_for_prompt)
            await message.answer(f"{title}\n步骤 {int(total_steps) - 1}/{total_steps}：请重新输入这次图片编辑要求。", reply_markup=_image_task_step_keyboard())
            return
        params = {
            "input_image_local_path": str(data.get("input_image_local_path") or ""),
            "reference_image_local_path": str(data.get("reference_image_local_path") or ""),
            "prompt": prompt,
            "prompt_text": prompt,
            "message": prompt,
            "tg_use_llm_prompt": True,
            "tg_user_instruction": f"User image editing request: {prompt}",
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, task_type, params)
        except Exception as exc:
            await message.answer(f"{title}任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

    @router.message(ProductionWorkflowForm.face_swap_waiting_for_target_image)
    async def on_face_swap_target_image(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        text = _canonical_button_text(_message_text(message))
        if text == "上一步":
            await start_image_generate_flow(message, state)
            return
        suffix = _image_ext_from_message(message)
        if suffix is None:
            await message.answer("人物换脸\n步骤 1/4：请上传原图。", reply_markup=_image_task_step_keyboard(back=False))
            return
        data = await state.get_data()
        work_dir = Path(str(data.get("work_dir") or service.create_job_dir(prefix="tg_face_swap")))
        target = work_dir / f"target{suffix}"
        await _download_message_media(message, target)
        await state.update_data(work_dir=str(work_dir), target_image_local_path=str(target.resolve()))
        await state.set_state(ProductionWorkflowForm.face_swap_waiting_for_source_image)
        await message.answer("人物换脸\n步骤 2/4：请上传人脸参考图。", reply_markup=_image_task_step_keyboard())

    @router.message(ProductionWorkflowForm.face_swap_waiting_for_source_image)
    async def on_face_swap_source_image(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        text = _canonical_button_text(_message_text(message))
        if text == "上一步":
            await state.set_state(ProductionWorkflowForm.face_swap_waiting_for_target_image)
            await message.answer("人物换脸\n步骤 1/4：请重新上传原图。", reply_markup=_image_task_step_keyboard(back=False))
            return
        suffix = _image_ext_from_message(message)
        if suffix is None:
            await message.answer("人物换脸\n步骤 2/4：请上传人脸参考图。", reply_markup=_image_task_step_keyboard())
            return
        data = await state.get_data()
        work_dir = Path(str(data.get("work_dir") or service.create_job_dir(prefix="tg_face_swap")))
        target = work_dir / f"source_face{suffix}"
        await _download_message_media(message, target)
        await state.update_data(work_dir=str(work_dir), source_image_local_path=str(target.resolve()))
        await state.set_state(ProductionWorkflowForm.face_swap_waiting_for_prompt)
        await message.answer("人物换脸\n步骤 3/4：请输入换脸要求；如果没有额外要求，可输入“自然换脸”。", reply_markup=_image_task_step_keyboard())

    @router.message(ProductionWorkflowForm.face_swap_waiting_for_prompt)
    async def on_face_swap_prompt(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        text = _canonical_button_text(_message_text(message))
        if text == "上一步":
            await state.set_state(ProductionWorkflowForm.face_swap_waiting_for_source_image)
            await message.answer("人物换脸\n步骤 2/4：请重新上传人脸参考图。", reply_markup=_image_task_step_keyboard())
            return
        prompt = _message_text(message) or "自然换脸，保持原图姿态、服装、光线和背景，只替换人物脸部身份。"
        await state.update_data(face_swap_prompt=prompt)
        await state.set_state(ProductionWorkflowForm.face_swap_waiting_for_confirm)
        await message.answer(
            "\n".join(
                [
                    "人物换脸",
                    "步骤 4/4：请确认任务信息，点击提交后才会进入后台队列。",
                    _tg_mapped_workflow_line("face_swap"),
                    f"换脸要求：{prompt}",
                ]
            ),
            reply_markup=_image_task_confirm_keyboard("提交人物换脸任务"),
        )

    @router.message(ProductionWorkflowForm.face_swap_waiting_for_confirm)
    async def on_face_swap_confirm(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        text = _canonical_button_text(_message_text(message))
        if text == "上一步":
            await state.set_state(ProductionWorkflowForm.face_swap_waiting_for_prompt)
            await message.answer("人物换脸\n步骤 3/4：请重新输入换脸要求；如果没有额外要求，可输入“自然换脸”。", reply_markup=_image_task_step_keyboard())
            return
        if text != "提交人物换脸任务":
            await message.answer("人物换脸\n步骤 4/4：请点击「提交人物换脸任务」后再提交。", reply_markup=_image_task_confirm_keyboard("提交人物换脸任务"))
            return
        data = await state.get_data()
        prompt = str(data.get("face_swap_prompt") or "").strip() or "自然换脸，保持原图姿态、服装、光线和背景，只替换人物脸部身份。"
        seed_value = secrets.randbelow(TEXT_TO_IMAGE_MAX_SEED) + 1
        params = {
            "target_image_local_path": str(data.get("target_image_local_path") or ""),
            "source_image_local_path": str(data.get("source_image_local_path") or ""),
            "prompt": prompt,
            "prompt_text": prompt,
            "message": prompt,
            "seed": seed_value,
            "face_swap_random_seed": seed_value,
            "tg_use_llm_prompt": True,
            "tg_user_instruction": f"User face swap request: {prompt}",
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "face_swap", params)
        except Exception as exc:
            await message.answer(f"人物换脸任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

    @router.message(ProductionWorkflowForm.video_i2v_waiting_for_resolution)
    @router.message(ProductionWorkflowForm.video_i2v_waiting_for_duration)
    @router.message(ProductionWorkflowForm.video_i2v_waiting_for_audio)
    @router.message(ProductionWorkflowForm.video_i2v_waiting_for_prompt_mode)
    async def on_video_i2v_param_text(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        current_state = await state.get_state()
        text = _message_text(message).strip()
        button_text = _canonical_button_text(text).replace("\u2713", "").strip()
        data = await state.get_data()
        params = _video_i2v_state_params(data)
        if button_text == "\u4e0a\u4e00\u6b65":
            if current_state == ProductionWorkflowForm.video_i2v_waiting_for_duration.state:
                params.update(
                    {
                        "resolution_selected": False,
                        "duration_selected": False,
                        "prompt_mode_selected": False,
                        "prompt_extend_selected": False,
                    }
                )
                await state.update_data(**params)
                await _show_video_i2v_step(message, state, step="resolution")
                return
            if current_state == ProductionWorkflowForm.video_i2v_waiting_for_prompt_mode.state:
                params.update({"prompt_mode_selected": False})
                await state.update_data(**params)
                await _show_video_i2v_step(message, state, step="audio")
                return
            if current_state == ProductionWorkflowForm.video_i2v_waiting_for_audio.state:
                params.update({"audio_selected": False, "audio_local_path": "", "prompt_mode_selected": False})
                await state.update_data(**params)
                await _show_video_i2v_step(message, state, step="image")
                return
        if current_state == ProductionWorkflowForm.video_i2v_waiting_for_resolution.state:
            if button_text.startswith("720p"):
                params["resolution"] = "720p"
            elif button_text.startswith("1080p"):
                params["resolution"] = "1080p"
            else:
                await message.answer("\u8bf7\u70b9\u51fb\u4e0b\u65b9\u6309\u94ae\u9009\u62e9\u5206\u8fa8\u7387\u3002")
                await _show_video_i2v_step(message, state, step="resolution")
                return
            params["resolution_selected"] = True
            params.update({"duration_selected": False, "prompt_mode_selected": False, "prompt_extend_selected": False})
            await state.update_data(**params)
            await _show_video_i2v_step(message, state, step="duration")
            return
        if current_state == ProductionWorkflowForm.video_i2v_waiting_for_audio.state:
            if button_text == "\u8df3\u8fc7\u97f3\u9891":
                params["audio_selected"] = True
                params["audio_local_path"] = ""
                await state.update_data(**params)
                await _show_video_i2v_step(message, state, step="prompt_mode")
                return
            audio_suffix = _audio_ext_from_message(message)
            if audio_suffix is None:
                await message.answer("\u8bf7\u4e0a\u4f20\u97f3\u9891\u6587\u4ef6\uff0c\u6216\u70b9\u51fb\u201c\u8df3\u8fc7\u97f3\u9891\u201d\u3002", reply_markup=_video_i2v_audio_keyboard())
                return
            work_dir = Path(str(data.get("work_dir") or service.create_job_dir(prefix="tg_video_i2v")))
            target = work_dir / f"audio{audio_suffix}"
            await _download_message_media(message, target)
            params["audio_selected"] = True
            params["audio_local_path"] = str(target.resolve())
            await state.update_data(**params, work_dir=str(work_dir))
            await _show_video_i2v_step(message, state, step="prompt_mode")
            return
        if current_state == ProductionWorkflowForm.video_i2v_waiting_for_duration.state:
            if button_text != text:
                text = button_text
        if current_state == ProductionWorkflowForm.video_i2v_waiting_for_prompt_mode.state:
            if button_text == "\u8ba9 Grok \u751f\u6210\u63d0\u793a\u8bcd":
                params["use_grok"] = True
                params["prompt_mode_label"] = "Grok \u751f\u6210"
            elif button_text == "\u8f93\u5165\u81ea\u5b9a\u4e49\u63d0\u793a\u8bcd\u63d0\u4ea4":
                params["use_grok"] = False
                params["prompt_mode_label"] = "\u81ea\u5b9a\u4e49\u63d0\u4ea4"
            else:
                await message.answer("\u8bf7\u70b9\u51fb\u4e0b\u65b9\u6309\u94ae\u9009\u62e9\u63d0\u793a\u8bcd\u65b9\u5f0f\u3002")
                await _show_video_i2v_step(message, state, step="prompt_mode")
                return
            params["prompt_mode_selected"] = True
            params["prompt_extend_selected"] = False
            await state.update_data(**params)
            await _show_video_i2v_step(message, state, step="prompt")
            return
        if current_state == ProductionWorkflowForm.video_i2v_waiting_for_duration.state:
            if not text.isdigit():
                await message.answer("请输入 2 到 15 秒之间的整数，例如：5。")
                await _show_video_i2v_step(message, state, step="duration")
                return
            duration = int(text)
            if duration < 2 or duration > 15:
                await message.answer("时长范围是 2 到 15 秒，请重新输入。")
                await _show_video_i2v_step(message, state, step="duration")
                return
            await state.update_data(duration=duration, duration_selected=True, prompt_mode_selected=False, prompt_extend_selected=False)
            await _show_video_i2v_step(message, state, step="image")
            return
        step = "resolution"
        if current_state == ProductionWorkflowForm.video_i2v_waiting_for_prompt_mode.state:
            step = "prompt_mode"
        await message.answer("请点击上方按钮选择当前参数。")
        await _show_video_i2v_step(message, state, step=step)

    @router.message(ProductionWorkflowForm.video_i2v_waiting_for_image)
    async def on_video_i2v_image(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        if _canonical_button_text(_message_text(message)).strip() == "\u4e0a\u4e00\u6b65":
            data = await state.get_data()
            params = _video_i2v_state_params(data)
            params["duration_selected"] = False
            await state.update_data(**params)
            await _show_video_i2v_step(message, state, step="duration")
            return
        suffix = _image_ext_from_message(message)
        if suffix is None:
            await message.answer("请上传一张参考图片。")
            await _show_video_i2v_step(message, state, step="image")
            return
        data = await state.get_data()
        work_dir = Path(str(data.get("work_dir") or service.create_job_dir(prefix="tg_video_i2v")))
        target = work_dir / f"reference{suffix}"
        await _download_message_media(message, target)
        caption = _message_text(message)
        await state.update_data(work_dir=str(work_dir), image_local_path=str(target.resolve()), video_i2v_initial_prompt=caption)
        await _show_video_i2v_step(message, state, step="audio")
        return

    @router.message(ProductionWorkflowForm.video_i2v_waiting_for_prompt)
    async def on_video_i2v_prompt(message: Message, state: FSMContext) -> None:
        if await handle_entry_keyword(message, state):
            return
        if await handle_stop_request(message, state):
            return
        if not await ensure_authorized(message):
            return
        prompt = _message_text(message)
        button_text = _canonical_button_text(prompt).replace("\u2713", "").strip()
        if button_text == "\u4e0a\u4e00\u6b65":
            await _show_video_i2v_step(message, state, step="prompt_mode")
            return
        data = await state.get_data()
        params = _video_i2v_state_params(data)
        if button_text == "使用这个提示词生成":
            final_prompt = str(data.get("video_i2v_generated_prompt") or "").strip()
            if not final_prompt:
                await message.answer("还没有可用的视频提示词，请先输入需求让 Grok 生成。", reply_markup=_video_i2v_prompt_failure_keyboard())
                return
            submit_params = dict(params)
            submit_params["use_grok"] = False
            submit_params["prompt_mode_label"] = "Grok 生成"
            payload = _build_video_i2v_payload(data, submit_params, final_prompt)
            if payload is None:
                await message.answer("请先上传一张参考图。")
                await _show_video_i2v_step(message, state, step="image")
                return
            payload.update(
                {
                    "tg_use_llm_prompt": False,
                    "tg_llm_rewritten_prompt": final_prompt,
                    "tg_user_instruction": str(data.get("video_i2v_user_request") or final_prompt),
                }
            )
            try:
                await _submit_video_i2v_payload(message, state, payload, submit_params)
            except Exception as exc:
                await message.answer(f"图生视频任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_video_i2v_prompt_review_keyboard())
            return
        if button_text == "输入自定义提示词提交":
            params["use_grok"] = False
            params["prompt_mode_label"] = "自定义提交"
            await state.update_data(**params, video_i2v_prompt_ready=False)
            await message.answer("请输入自定义最终视频提示词。下一条消息会跳过 Grok，直接提交。", reply_markup=_video_i2v_prompt_keyboard())
            return
        if button_text == "返回参数设置":
            params["prompt_mode_selected"] = False
            await state.update_data(**params)
            await _show_video_i2v_step(message, state, step="prompt_mode")
            return
        if button_text == "重新生成提示词":
            original_request = str(data.get("video_i2v_user_request") or data.get("video_i2v_initial_prompt") or "").strip()
            if not original_request:
                await message.answer("没有原始视频需求，请重新输入。", reply_markup=_video_i2v_prompt_keyboard())
                return
            params["use_grok"] = True
            params["prompt_mode_label"] = "Grok 生成"
            await state.update_data(**params)
            await _submit_video_i2v_from_state(message, state, original_request)
            return
        if button_text == "继续让 Grok 调整":
            await message.answer("请直接输入调整要求，例如：动作更慢、镜头更近、保持原图姿态。", reply_markup=_video_i2v_prompt_review_keyboard())
            await state.update_data(video_i2v_waiting_for_adjustment=True)
            return
        if bool(data.get("video_i2v_waiting_for_adjustment")):
            base_prompt = str(data.get("video_i2v_generated_prompt") or "").strip()
            original_request = str(data.get("video_i2v_user_request") or "").strip()
            adjusted_request = "\n".join(
                part for part in [
                    f"Original request: {original_request}" if original_request else "",
                    f"Current video prompt: {base_prompt}" if base_prompt else "",
                    f"Revision request: {prompt}",
                    "Return one revised final video prompt only.",
                ] if part
            )
            params["use_grok"] = True
            params["prompt_mode_label"] = "Grok 生成"
            await state.update_data(**params, video_i2v_waiting_for_adjustment=False)
            await _submit_video_i2v_from_state(message, state, adjusted_request)
            return
        if not prompt:
            await message.answer("请直接输入这次图生视频的画面和动作需求。", reply_markup=_video_i2v_prompt_keyboard())
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
            "tg_user_instruction": str(data.get("prompt") or "Preserve the original video action, camera, and environment, and naturally replace the subject with the uploaded model image."),
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "replace_model", params)
        except Exception as exc:
            await message.answer(f"视频模特替换任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

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
                    f"Product name: {str(data.get('product_name') or 'product')}",
                    str(data.get("prompt_text") or "Preserve the original video camera and character action, and naturally replace the product with the uploaded product image."),
                ]
            ),
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "replace_product", params)
        except Exception as exc:
            await message.answer(f"视频商品替换任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

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
            "tg_user_instruction": f"Combined replacement: naturally replace the video model and product. Product name: {str(data.get('product_name') or 'product')}",
        }
        await state.clear()
        try:
            await submit_webapp_task_and_reply(message, "replace_productANDmodel", params)
        except Exception as exc:
            await message.answer(f"联合替换任务提交失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

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

    @router.message(F.text == TEXT_TO_IMAGE_REROLL_IMAGE_BUTTON)
    async def on_text_to_image_reroll_image_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        try:
            await _reroll_latest_text_to_image(message, state)
        except Exception as exc:
            await message.answer(f"重新生成图片失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

    @router.message(F.text == TEXT_TO_IMAGE_CONTINUE_IMAGE_BUTTON)
    async def on_text_to_image_continue_image_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        try:
            await _continue_latest_text_to_image(message, state)
        except Exception as exc:
            await message.answer(f"继续生成图片失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())

    @router.message(F.text == MULTI_IMAGE_BUTTON)
    @router.message(F.text == "多圖生成")
    async def on_multi_image_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_image_reference_flow(message, state, mode="multi_image")

    @router.message(F.text == SINGLE_IMAGE_EDIT_BUTTON)
    @router.message(F.text == "单图编辑")
    async def on_single_image_edit_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_single_image_edit_flow(message, state, single_input=True)

    @router.message(F.text == IMAGE_EDIT_BUTTON)
    @router.message(F.text == "图片编辑")
    @router.message(F.text == "圖片編輯")
    async def on_image_edit_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_single_image_edit_flow(message, state, single_input=False)

    @router.message(F.text == FACE_SWAP_BUTTON)
    @router.message(F.text == "人物換臉")
    async def on_face_swap_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_face_swap_flow(message, state)

    @router.message(F.text == IMAGE_REPLACE_BUTTON)
    @router.message(F.text == "圖片替換")
    async def on_image_replace_button(message: Message, state: FSMContext) -> None:
        if not await ensure_authorized(message):
            return
        await start_image_reference_flow(message, state, mode="image_replace")

    @router.message(F.text == IMAGE_WORKFLOW_BUTTON)
    @router.message(F.text == "图像编辑")
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
            await message.answer(f"素材下载失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())
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
                await message.answer(f"Grok 提示词生成失败：{_format_tg_user_error(exc)}", reply_markup=_menu_keyboard())
            return
        try:
            result = await _submit_internal_webapp_agent_task(
                chat_id=int(message.chat.id),
                message_text=text,
                files=files,
            )
        except Exception as exc:
            await message.answer(
                f"智能任务提交失败：{_format_tg_user_error(exc)}\n\n你也可以按面板中的具体工作流入口，依序上传素材。",
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
        prompt_preview = str(result.get("prompt_preview") or "").strip()
        prompt_preview_display = ""
        if prompt_preview:
            try:
                prompt_preview_display = await _display_internal_webapp_prompt(
                    chat_id=int(message.chat.id),
                    task_type=str(result.get("task_type") or "text_to_image"),
                    prompt_text=prompt_preview,
                )
            except Exception as exc:
                prompt_preview_display = _telegram_prompt_chinese_preview(prompt_preview)
                if not _looks_like_clean_chinese_preview(prompt_preview_display):
                    prompt_preview_display = _format_prompt_display_fallback(exc)
        await message.answer(
            "\n".join(
                part
                for part in [
                    "已通过文字模型理解你的会话，并生成工作流提示词。",
                    summary,
                    f"Grok 生成提示词: {prompt_preview_display}" if prompt_preview_display else "",
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
