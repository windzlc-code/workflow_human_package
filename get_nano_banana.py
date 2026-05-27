import base64
import json
import mimetypes
import os
import re
import requests
from model_endpoint_utils import build_model_request_url, clean_endpoint_input


def _resolve_nano_banana_request_url(host: str, port: str | int | None = None) -> str:
    return build_model_request_url(
        host=clean_endpoint_input(host),
        port=port,
        path="/v1beta/models/gemini-3-pro-image-preview:generateContent",
    )


def _ensure_output_dir(output_image_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_image_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)


def _save_image_bytes(output_image_path: str, image_bytes: bytes) -> dict[str, str]:
    _ensure_output_dir(output_image_path)
    with open(output_image_path, "wb") as output_file:
        output_file.write(image_bytes)
    return {"message": "Image generated successfully", "image_path": output_image_path}


def _looks_like_image_url(value: str) -> bool:
    text = str(value or "").strip().strip("`'\"")
    if not text.startswith(("http://", "https://")):
        return False
    lowered = text.lower()
    return any(ext in lowered for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"))


def _extract_image_url_from_text(value: str) -> str:
    source = str(value or "").strip().strip("`'\"")
    if not source:
        return ""

    if _looks_like_image_url(source):
        return source

    markdown_patterns = (
        r"!\[[^\]]*\]\((https?://[^)\s]+)\)",
        r"\[[^\]]*\]\((https?://[^)\s]+)\)",
    )
    for pattern in markdown_patterns:
        match = re.search(pattern, source, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = str(match.group(1) or "").strip().strip("`'\"")
        if _looks_like_image_url(candidate):
            return candidate

    for match in re.finditer(r"https?://[^\s]+", source, flags=re.IGNORECASE):
        candidate = str(match.group(0) or "").strip().strip("`'\"")
        candidate = candidate.rstrip("),]>}.,;:!?\uff0c\u3002\uff1b\uff1a\uff01\uff1f")
        if _looks_like_image_url(candidate):
            return candidate
    return ""


def _download_image_as_base64(*, image_url: str, timeout_seconds: float) -> str:
    resp = requests.get(str(image_url).strip().strip("`'\""), timeout=max(float(timeout_seconds or 0), 1.0))
    resp.raise_for_status()
    return base64.b64encode(resp.content).decode("utf-8")


def _extract_candidate_image_payload(*, candidate: object, timeout_seconds: float) -> dict[str, str] | None:
    if isinstance(candidate, str):
        if _looks_like_image_url(candidate):
            return {"kind": "url", "value": str(candidate).strip().strip("`'\"")}
        return None

    if not isinstance(candidate, dict):
        return None

    direct_url = str(
        candidate.get("url")
        or candidate.get("image_url")
        or candidate.get("imageUrl")
        or ""
    ).strip()
    if _looks_like_image_url(direct_url):
        return {"kind": "url", "value": direct_url.strip().strip("`'\"")}

    content = candidate.get("content", {})
    if isinstance(content, str):
        extracted_url = _extract_image_url_from_text(content)
        if extracted_url:
            return {"kind": "url", "value": extracted_url}

    parts = content.get("parts", []) if isinstance(content, dict) else []
    for part in parts:
        image_data = part.get("inlineData") or part.get("inline_data")
        if image_data:
            base64_data = str(image_data.get("data") or "").strip()
            if base64_data:
                return {"kind": "base64", "value": base64_data}
        text_value = str(part.get("text") or "").strip()
        extracted_url = _extract_image_url_from_text(text_value)
        if extracted_url:
            return {"kind": "url", "value": extracted_url}
    return None


def _save_base64_image(*, output_image_path: str, base64_data: str) -> dict[str, str]:
    try:
        image_bytes = base64.b64decode(base64_data)
    except Exception as exc:
        raise RuntimeError("Nano Banana 返回了图片 base64，但解码失败") from exc
    return _save_image_bytes(output_image_path, image_bytes)


def _download_image_to_path(*, image_url: str, output_image_path: str, timeout_seconds: float) -> dict[str, str]:
    try:
        image_bytes = base64.b64decode(_download_image_as_base64(image_url=image_url, timeout_seconds=timeout_seconds))
    except requests.RequestException as exc:
        raise RuntimeError(f"Nano Banana 返回了图片 URL，但下载失败: {str(image_url)[:300]} -> {exc}") from exc
    return _save_image_bytes(output_image_path, image_bytes)


def _candidate_debug_summary(candidate: object) -> str:
    candidate_type = type(candidate).__name__
    try:
        preview = repr(candidate)
    except Exception:
        try:
            preview = json.dumps(candidate, ensure_ascii=False)
        except Exception:
            preview = "<unreprable>"
    preview = str(preview).replace("\n", "\\n")[:500]
    return f"candidate_type={candidate_type}, candidate_preview={preview}"


def _emit_nano_log(logger, message: str, extra: dict | None = None) -> None:
    if logger is None:
        return
    text = str(message or "").strip()
    if extra:
        try:
            text = f"{text} | {json.dumps(extra, ensure_ascii=False)}"
        except Exception:
            pass
    try:
        logger(text)
    except Exception:
        pass


def get_nano_banana_pro(
        prompt: str,
        output_image_path: str,
        api_key: str,
        input_image_path: str,
        host: str,
        port: str | int | None = None,
        timeout_seconds: float = 90,
        retry_count: int = 2,
        retry_wait_seconds: float = 2.0,
        logger=None,
    ) -> dict[str, str]:
    if not str(prompt).strip():
        raise ValueError("nano banana prompt 不能为空")
    if not str(api_key).strip():
        raise ValueError("nano banana api_key 不能为空")
    if not str(output_image_path).strip():
        raise ValueError("nano banana output_image_path 不能为空")
    if not str(input_image_path).strip():
        raise ValueError("nano banana input_image_path 不能为空")
    if not str(host).strip():
        raise ValueError("api host 不能为空")

    url = _resolve_nano_banana_request_url(host=host, port=port)

    encoded_image = None
    image_mime_type = "image/png"
    if input_image_path is not None:
        if not os.path.exists(input_image_path):
            raise FileNotFoundError(f"Input image path '{input_image_path}' does not exist.")
        image_mime_type = mimetypes.guess_type(input_image_path)[0] or image_mime_type
        with open(input_image_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

    request_parts = [{"text": prompt}]
    if encoded_image is not None:
        request_parts.append(
            {
                "inline_data": {
                    "mime_type": image_mime_type,
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
            "responseModalities": [
                "TEXT",
                "IMAGE",
            ]
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    attempt = 0
    last_exc: Exception | None = None
    max_attempts = max(int(retry_count or 0), 0) + 1
    while attempt < max_attempts:
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=max(float(timeout_seconds or 0), 1.0),
            )
            response.raise_for_status()
            last_exc = None
            break
        except requests.HTTPError as exc:
            last_exc = exc
            status_code = exc.response.status_code if exc.response is not None else None
            response_preview = str(getattr(exc.response, "text", "") or "")[:500]
            retry_after_seconds = None
            retry_after_header = None
            if exc.response is not None:
                retry_after_header = exc.response.headers.get("Retry-After")
                if retry_after_header:
                    try:
                        retry_after_seconds = float(retry_after_header)
                    except Exception:
                        retry_after_seconds = None
            retriable = status_code in {408, 425, 429, 500, 502, 503, 504}
            attempt += 1
            if attempt >= max_attempts or not retriable:
                retry_after = f", retry-after={retry_after_header}" if retry_after_seconds is not None else ""
                _emit_nano_log(
                    logger,
                    "Nano Banana 请求失败",
                    {
                        "request_url": url,
                        "status": status_code or "unknown",
                        "attempt": f"{attempt}/{max_attempts}",
                        "response_preview": response_preview,
                    },
                )
                raise RuntimeError(f"nano banana 请求失败: HTTP {status_code or 'unknown'}{retry_after}, {exc}") from exc
            wait_s = float(retry_after_seconds) if retry_after_seconds is not None else float(retry_wait_seconds or 0.0)
            if wait_s > 0:
                import time
                time.sleep(wait_s)
        except requests.RequestException as exc:
            last_exc = exc
            attempt += 1
            if attempt >= max_attempts:
                _emit_nano_log(
                    logger,
                    "Nano Banana 请求失败",
                    {
                        "request_url": url,
                        "attempt": f"{attempt}/{max_attempts}",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
                raise RuntimeError(f"nano banana 请求失败: {exc}") from exc
            wait_s = float(retry_wait_seconds or 0.0)
            if wait_s > 0:
                import time
                time.sleep(wait_s)
    if last_exc is not None:
        _emit_nano_log(logger, "Nano Banana 请求失败", {"request_url": url, "error_message": str(last_exc)})
        raise RuntimeError(f"nano banana 请求失败: {last_exc}") from last_exc

    try:
        data = response.json()
    except ValueError as exc:
        preview = response.text[:300]
        _emit_nano_log(logger, "Nano Banana 返回非 JSON 响应", {"request_url": url, "response_preview": preview})
        raise RuntimeError(f"nano banana 返回非 JSON 响应: {preview}") from exc

    if isinstance(data, dict) and data.get("error"):
        _emit_nano_log(logger, "Nano Banana 接口返回错误", {"request_url": url, "error": data.get("error")})
        raise RuntimeError(f"nano banana 接口返回错误: {data.get('error')}")

    candidates = data.get("candidates", [])
    if not candidates:
        _emit_nano_log(logger, "Nano Banana 未返回候选结果", {"request_url": url, "response_preview": str(data)[:500]})
        raise RuntimeError(f"nano banana 未返回候选结果: {data}")

    candidate = candidates[0]
    image_payload = _extract_candidate_image_payload(candidate=candidate, timeout_seconds=timeout_seconds)
    if image_payload:
        kind = str(image_payload.get("kind") or "").strip()
        value = str(image_payload.get("value") or "").strip()
        if kind == "base64" and value:
            return _save_base64_image(output_image_path=output_image_path, base64_data=value)
        if kind == "url" and value:
            return _download_image_to_path(image_url=value, output_image_path=output_image_path, timeout_seconds=timeout_seconds)

    debug_summary = _candidate_debug_summary(candidate)
    _emit_nano_log(logger, "Nano Banana 响应中未找到图片数据", {"request_url": url, "candidate": debug_summary})
    raise RuntimeError(f"nano banana 响应中未找到图片数据: {debug_summary}; response={str(data)[:1000]}")


if __name__ == "__main__":
    print("get_nano_banana module")
