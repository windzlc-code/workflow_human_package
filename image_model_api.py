import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

import requests


GEMINI_IMAGE_MODELS = {
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
}
GPT_IMAGE_MODELS = {
    "gpt-image-1",
    "gpt-image-1-mini",
    "gpt-image-2",
}


def _resolve_api_key(*, model: str, gemini_api_key: str, gpt_api_key: str) -> str:
    model_text = str(model or "").strip()
    if model_text in GPT_IMAGE_MODELS:
        return str(gpt_api_key or "").strip()
    return str(gemini_api_key or "").strip()


def _extract_markdown_data_uri_base64(text: str) -> str:
    source = str(text or "")
    match = re.search(r"data:image/[^;]+;base64,([^\)\s]+)", source, flags=re.IGNORECASE)
    if match:
        return str(match.group(1) or "").strip()
    return ""


def _extract_base64_image(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("b64_json", "base64", "image_base64"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        nested = data.get("data")
        if isinstance(nested, list):
            for item in nested:
                value = _extract_base64_image(item)
                if value:
                    return value
        candidates = data.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                value = _extract_base64_image(candidate)
                if value:
                    return value
        content = data.get("content")
        if isinstance(content, dict):
            parts = content.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    inline_data = part.get("inlineData") or part.get("inline_data")
                    if isinstance(inline_data, dict):
                        value = str(inline_data.get("data") or "").strip()
                        if value:
                            return value
                    text_value = _extract_markdown_data_uri_base64(part.get("text") or "")
                    if text_value:
                        return text_value
    elif isinstance(data, list):
        for item in data:
            value = _extract_base64_image(item)
            if value:
                return value
    return ""


def _extract_image_url(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("url", "image_url"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        nested = data.get("data")
        if isinstance(nested, list):
            for item in nested:
                value = _extract_image_url(item)
                if value:
                    return value
    elif isinstance(data, list):
        for item in data:
            value = _extract_image_url(item)
            if value:
                return value
    return ""


def _save_base64_image(*, output_image_path: str, image_base64: str) -> str:
    path = Path(output_image_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(image_base64))
    return str(path)


def _download_image(*, image_url: str, output_image_path: str) -> str:
    path = Path(output_image_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(str(image_url).strip(), timeout=120)
    response.raise_for_status()
    path.write_bytes(response.content)
    return str(path)


def _encode_input_image(input_image_path: str | None) -> tuple[str, str]:
    image_path = str(input_image_path or "").strip()
    if not image_path:
        return "", ""
    path = Path(image_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"输入图片不存在: {path}")
    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    return mime_type, base64.b64encode(path.read_bytes()).decode("utf-8")


def _generate_via_gemini_image_model(*, base_url: str, model: str, prompt: str, api_key: str, input_image_path: str | None = None) -> dict[str, Any]:
    request_parts: list[dict[str, Any]] = [{"text": prompt}]
    mime_type, encoded_image = _encode_input_image(input_image_path)
    if encoded_image:
        request_parts.append(
            {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": encoded_image,
                }
            }
        )
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": request_parts,
            }
        ],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"]
        },
    }
    response = requests.post(
        f"{base_url}/v1beta/models/{model}:generateContent",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        data=json.dumps(payload),
        timeout=180,
    )
    response.raise_for_status()
    return response.json()


def _generate_via_gpt_image_model(*, base_url: str, model: str, prompt: str, api_key: str, input_image_path: str | None = None) -> dict[str, Any]:
    image_path = str(input_image_path or "").strip()
    if image_path:
        path = Path(image_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"输入图片不存在: {path}")
        with path.open("rb") as f:
            response = requests.post(
                f"{base_url}/v1/images/edits",
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                data={"model": model, "prompt": prompt},
                files={"image": (path.name, f, mimetypes.guess_type(str(path))[0] or "image/png")},
                timeout=180,
            )
    else:
        payload = {
            "model": model,
            "prompt": prompt,
        }
        response = requests.post(
            f"{base_url}/v1/images/generations",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
            data=json.dumps(payload),
            timeout=180,
        )
    response.raise_for_status()
    return response.json()


def generate_image(
    *,
    base_url: str,
    model: str,
    prompt: str,
    output_image_path: str,
    gemini_api_key: str = "",
    gpt_api_key: str = "",
    input_image_path: str | None = None,
    logger=None,
) -> dict[str, Any]:
    model_candidates = [
        item.strip()
        for item in re.split(r"\s*[,，\n]+\s*", str(model or "").strip())
        if item.strip()
    ]
    if not model_candidates:
        raise RuntimeError("缺少闭源图像模型名称")
    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        raise RuntimeError("缺少图片生成提示词")
    base_url_text = str(base_url or "").strip().rstrip("/")
    if not base_url_text:
        raise RuntimeError("缺少闭源图像模型 Base URL")
    attempts: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, model_text in enumerate(model_candidates, start=1):
        api_key = _resolve_api_key(model=model_text, gemini_api_key=gemini_api_key, gpt_api_key=gpt_api_key)
        if not api_key:
            err = "缺少闭源图像模型 API Key"
            attempts.append({"attempt": idx, "model": model_text, "ok": False, "error": err})
            errors.append(f"{model_text}: {err}")
            continue
        try:
            if model_text in GEMINI_IMAGE_MODELS:
                result = _generate_via_gemini_image_model(
                    base_url=base_url_text,
                    model=model_text,
                    prompt=prompt_text,
                    api_key=api_key,
                    input_image_path=input_image_path,
                )
            else:
                result = _generate_via_gpt_image_model(
                    base_url=base_url_text,
                    model=model_text,
                    prompt=prompt_text,
                    api_key=api_key,
                    input_image_path=input_image_path,
                )
            image_base64 = _extract_base64_image(result)
            if image_base64:
                image_path = _save_base64_image(output_image_path=output_image_path, image_base64=image_base64)
                attempts.append({"attempt": idx, "model": model_text, "ok": True, "error": ""})
                return {"image_path": image_path, "raw_result": result, "selected_model": model_text, "attempts": attempts}
            image_url = _extract_image_url(result)
            if image_url:
                image_path = _download_image(image_url=image_url, output_image_path=output_image_path)
                attempts.append({"attempt": idx, "model": model_text, "ok": True, "error": ""})
                return {"image_path": image_path, "raw_result": result, "selected_model": model_text, "attempts": attempts}
            err = f"闭源图像模型未返回可用图片结果: {json.dumps(result, ensure_ascii=False)[:500]}"
            attempts.append({"attempt": idx, "model": model_text, "ok": False, "error": err})
            errors.append(f"{model_text}: 未返回可用图片")
        except Exception as exc:
            err = str(exc)
            attempts.append({"attempt": idx, "model": model_text, "ok": False, "error": err})
            errors.append(f"{model_text}: {err}")
            continue
    raise RuntimeError(f"闭源图像模型全部候选调用失败: {'; '.join(errors) if errors else '未知错误'}")
