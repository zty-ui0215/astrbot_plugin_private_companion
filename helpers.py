# -*- coding: utf-8 -*-

from __future__ import annotations

import re
import time
import zoneinfo
from datetime import date, datetime
from typing import Any

_today_key_timezone = ""


def _now_ts() -> float:
    return time.time()


def _set_today_key_timezone(timezone_name: Any) -> None:
    global _today_key_timezone
    _today_key_timezone = str(timezone_name or "").strip()


def _today_key() -> str:
    if _today_key_timezone:
        try:
            return datetime.now(zoneinfo.ZoneInfo(_today_key_timezone)).strftime("%Y-%m-%d")
        except Exception:
            pass
    return datetime.now().strftime("%Y-%m-%d")


def _date_key(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def _safe_int(value: Any, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _safe_float(value: Any, default: float, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _single_line(text: Any, limit: int = 80) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return normalized[:limit]


def _strip_internal_message_blocks(text: Any) -> str:
    normalized = str(text or "")
    normalized = re.sub(r"\[\[TTSBLOCK:[^\]]*\]\]", "", normalized)
    normalized = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", normalized)
    normalized = re.sub(r"<timer\b[^>]*>.*?</timer>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"<tts\b[^>]*>.*?</tts>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _strip_outbound_control_blocks(text: Any, *, preserve_private_tts_tokens: bool = False) -> str:
    normalized = str(text or "")
    normalized = re.sub(r"\[\[TTSBLOCK:[^\]]*\]\]", "", normalized)
    if not preserve_private_tts_tokens:
        normalized = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", normalized)
    normalized = re.sub(r"<timer\b[^>]*>.*?</timer>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized


# ---------------------------------------------------------------------------
# 兼容 schema 中 type:"object" / items 嵌套结构的配置读写
# ---------------------------------------------------------------------------

_MISSING = object()


def _flat_get(config: Any, key: str, default: Any = None) -> Any:
    """
    兼容扁平 / 任意深度 object-items 嵌套两种 schema 结构读取。

    优先直接命中顶层 key；找不到时递归搜索所有嵌套 dict，
    返回第一个命中的叶子值。适配 AstrBot 不展平 object.items 的情况。
    """
    if isinstance(config, dict):
        # 优先顶层直接命中（最快路径）
        if key in config:
            return config[key]
        # 递归搜索所有嵌套 dict
        for value in config.values():
            if isinstance(value, dict):
                found = _flat_get(value, key, _MISSING)
                if found is not _MISSING:
                    return found
    # AstrBotConfig 等 dict-like 对象（非纯 dict）
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            val = getter(key, None)
            if val is not None:
                return val
        except Exception:
            pass
    return default


def _set_into_config(config: Any, key: str, value: Any, _wrapper_key: Any = None) -> None:
    """
    兼容扁平 / 任意深度 object-items 嵌套两种 schema 结构写入。

    始终只写顶层 key——与 AstrBot 原始 config[key] = value 行为一致。
    AstrBot 保存时会根据 schema 自动把顶层值序列化到正确的嵌套路径，
    因此这里不应该直接写嵌套层，否则可能触发 AstrBot 的类型校验错误
    （PageUI 传来的值可能是 str 类型的 "true"/"false"，嵌套层期望 bool）。
    """
    try:
        config[key] = value
        return
    except Exception:
        pass
    setter = getattr(config, "set", None)
    if callable(setter):
        try:
            setter(key, value)
        except Exception:
            pass


def _detect_wrapper_key(config: Any) -> str | None:
    """保留向后兼容：如果顶层只有一个 dict 值，返回那个 key；否则 None。"""
    if not isinstance(config, dict) or len(config) != 1:
        return None
    only_key = next(iter(config))
    inner = config[only_key]
    if isinstance(inner, dict):
        return only_key
    return None
