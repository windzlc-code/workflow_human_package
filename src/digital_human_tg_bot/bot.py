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

from aiohttp import ClientSession, TCPConnector
from aiohttp.resolver import ThreadedResolver
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from .config import AppConfig
from .media import extract_video_first_frame
from .workbench import WorkspaceService
from .workflow import WorkflowRequest


logger = logging.getLogger(__name__)
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
ZIP_EXTS = {".zip"}
AUTO_DURATION_TEXTS = {"跳过", "自动", "auto", "AUTO"}

DIGITAL_HUMAN_VIDEO_BUTTON = "数字人视频生成"
DIGITAL_HUMAN_REALISTIC_BUTTON = "写实带货视频"
DIGITAL_HUMAN_LIVE_BUTTON = "直播口播视频"
DIGITAL_HUMAN_PRODUCT_BUTTON = "产品展示视频"
DIGITAL_HUMAN_CUSTOM_BUTTON = "自定义数字人要求"
ORAL_UPLOAD_BUTTON = DIGITAL_HUMAN_VIDEO_BUTTON
LEGACY_ORAL_UPLOAD_BUTTON = "口播数字人：上传素材"
WORKFLOW_CONFIG_BUTTON = "查看后台工作流配置"
IMAGE_WORKFLOW_BUTTON = "图像编辑"
TEXT_TO_IMAGE_BUTTON = "文生图"
VIDEO_GENERAL_EDIT_BUTTON = "图生视频"
VIDEO_I2V_RES_PREFIX = "分辨率："
VIDEO_I2V_DURATION_PREFIX = "时长："
VIDEO_I2V_GROK_ON = "Grok提示词：开"
VIDEO_I2V_GROK_OFF = "Grok提示词：关"
VIDEO_I2V_EXTEND_ON = "接口扩写：开"
VIDEO_I2V_EXTEND_OFF = "接口扩写：关"
LEGACY_IMAGE_WORKFLOW_BUTTON = "图像编辑工作流"
LEGACY_IMAGE_GENERATE_WORKFLOW_BUTTON = "图片生成工作流"
VIDEO_EDIT_BUTTON = "视频编辑"
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
    "圖片編輯": IMAGE_WORKFLOW_BUTTON,
    "文生圖片": TEXT_TO_IMAGE_BUTTON,
    "圖像編輯工作流": LEGACY_IMAGE_WORKFLOW_BUTTON,
    "圖片生成工作流": LEGACY_IMAGE_GENERATE_WORKFLOW_BUTTON,
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
    text_to_image_waiting_for_prompt = State()
    image_waiting_for_product_image = State()
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
            IMAGE_WORKFLOW_BUTTON: _format_chain("图像编辑", image_chain),
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
            _format_chain("图像编辑", image_chain),
            _format_chain("视频模特替换", replace_model_original_chain),
            _format_chain("视频商品替换", replace_product_chain),
            _format_chain("联合替换·视频模特链", replace_union_model_chain),
            _format_chain("联合替换·视频商品链", replace_union_product_chain),
            "",
            selected_note,
            "TG 面板可直接建立任务：图像编辑、视频编辑。",
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
            "   点击后选择图像参数；当前已接入：文生图。",
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
        if _canonical_button_text(_message_text(message)) != "多智能体数字人":
            return False
        if not await ensure_authorized(message):
            return True
        await state.clear()
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
            "图像编辑：请选择要执行的图像参数。",
            reply_markup=_image_edit_keyboard(),
        )

    async def start_text_to_image_flow(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(ProductionWorkflowForm.text_to_image_waiting_for_prompt)
        await message.answer(
            "请输入文生图需求。Grok 会先生成最终提示词；远程 ComfyUI 工作流接入后会用于实际生成。",
            reply_markup=_image_edit_keyboard(),
        )

    def _video_i2v_defaults() -> dict[str, Any]:
        return {"resolution": "720p", "duration": 2, "use_grok": True, "prompt_extend": False}

    async def _answer_video_i2v_prompt(message: Message, state: FSMContext, *, text: str) -> None:
        data = await state.get_data()
        params = _video_i2v_defaults()
        params.update({k: data.get(k) for k in params.keys() if k in data})
        await message.answer(
            text,
            reply_markup=_video_i2v_keyboard(
                resolution=str(params["resolution"]),
                duration=int(params["duration"]),
                use_grok=bool(params["use_grok"]),
                prompt_extend=bool(params["prompt_extend"]),
            ),
        )

    async def _handle_video_i2v_param_button(message: Message, state: FSMContext) -> bool:
        text = _message_text(message)
        if not text:
            return False
        data = await state.get_data()
        params = _video_i2v_defaults()
        params.update({k: data.get(k) for k in params.keys() if k in data})
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
        await _answer_video_i2v_prompt(message, state, text="参数已更新。请继续上传参考图片或输入提示词。")
        return True

    async def start_video_i2v_flow(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(ProductionWorkflowForm.video_i2v_waiting_for_image)
        await state.update_data(**_video_i2v_defaults())
        await _answer_video_i2v_prompt(
            message,
            state,
            text="图生视频：请先上传一张参考图片。可点击按钮切换分辨率、时长、Grok 提示词和接口扩写。",
        )

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
            await submit_webapp_task_and_reply(message, "video_i2v", payload)
        except Exception as exc:
            await message.answer(f"图生视频任务提交失败：{exc}", reply_markup=_menu_keyboard())

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
        await _answer_video_i2v_prompt(message, state, text="已收到参考图。现在请输入视频需求，Grok 会先生成最终视频提示词。")

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
            await _answer_video_i2v_prompt(message, state, text="请直接输入这次图生视频的画面和动作需求。")
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

    @router.message(F.text == IMAGE_WORKFLOW_BUTTON)
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
            "视频编辑：请选择要建立的任务。",
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
            params = {
                "prompt": text,
                "prompt_text": text,
                "message": text,
                "tg_use_llm_prompt": True,
                "tg_user_instruction": f"用户文生图需求：{text}",
            }
            try:
                await submit_webapp_task_and_reply(message, "text_to_image", params)
            except Exception as exc:
                await message.answer(f"文生图任务提交失败：{exc}", reply_markup=_menu_keyboard())
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
