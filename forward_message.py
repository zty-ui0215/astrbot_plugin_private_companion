# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from datetime import datetime
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
try:
    from astrbot.api.message_components import Plain
except ImportError:
    from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest

from .helpers import _safe_float, _safe_int, _single_line, _strip_internal_message_blocks

class ForwardMessageMixin:
    """Forward-message parsing and prompt-context helpers."""

    def _forward_descriptor_cache_keys(self, event: AstrMessageEvent) -> list[str]:
        keys: list[str] = []
        message_id = ""
        event_message_id = getattr(self, "_event_message_id", None)
        if callable(event_message_id):
            try:
                message_id = _single_line(event_message_id(event), 120)
            except Exception:
                message_id = ""
        if not message_id:
            for attr in ("message_id", "id", "seq", "message_seq", "real_id"):
                value = _single_line(getattr(event, attr, ""), 120)
                if value:
                    message_id = value
                    break
        sender_id = ""
        try:
            sender_id = _single_line(event.get_sender_id(), 80)
        except Exception:
            sender_id = ""
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 160)
        if message_id:
            keys.append(f"id:{message_id}")
            if sender_id:
                keys.append(f"user:{sender_id}:id:{message_id}")
            if umo:
                keys.append(f"umo:{umo}:id:{message_id}")
        if sender_id:
            keys.append(f"user:{sender_id}:latest")
        return list(dict.fromkeys(key for key in keys if key))

    def _remember_forward_descriptor_for_event(
        self,
        event: AstrMessageEvent,
        forward_id: str,
        forward_payload: dict[str, Any],
    ) -> None:
        if not (forward_id or forward_payload):
            return
        descriptor = (_single_line(forward_id, 240), dict(forward_payload or {}))
        try:
            setattr(event, "_private_companion_forward_descriptor", descriptor)
        except Exception:
            pass
        cache = getattr(self, "_private_companion_forward_descriptor_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                setattr(self, "_private_companion_forward_descriptor_cache", cache)
            except Exception:
                return
        now = time.time()
        for key in self._forward_descriptor_cache_keys(event):
            cache[key] = {"ts": now, "descriptor": descriptor}
        for key, value in list(cache.items()):
            if not isinstance(value, dict) or now - _safe_float(value.get("ts"), 0) > 10 * 60:
                cache.pop(key, None)
        if len(cache) > 80:
            ranked = sorted(cache.items(), key=lambda item: _safe_float(item[1].get("ts") if isinstance(item[1], dict) else 0, 0))
            for key, _value in ranked[:-80]:
                cache.pop(key, None)

    def _cached_forward_descriptor_for_event(self, event: AstrMessageEvent) -> tuple[str, dict[str, Any]]:
        cache = getattr(self, "_private_companion_forward_descriptor_cache", None)
        if not isinstance(cache, dict):
            return "", {}
        message_text = _single_line(getattr(event, "message_str", ""), 180)
        allow_latest = "转发" in message_text or "合并消息" in message_text or "聊天记录" in message_text
        now = time.time()
        for key in self._forward_descriptor_cache_keys(event):
            if key.endswith(":latest") and not allow_latest:
                continue
            value = cache.get(key)
            if not isinstance(value, dict) or now - _safe_float(value.get("ts"), 0) > 10 * 60:
                cache.pop(key, None)
                continue
            descriptor = value.get("descriptor")
            if (
                isinstance(descriptor, tuple)
                and len(descriptor) == 2
                and isinstance(descriptor[0], str)
                and isinstance(descriptor[1], dict)
            ):
                try:
                    setattr(event, "_private_companion_forward_descriptor", descriptor)
                except Exception:
                    pass
                return descriptor
        return "", {}

    def _component_type_name(self, item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("type") or "").strip().lower()
        return str(getattr(item, "type", "") or item.__class__.__name__).strip().lower()

    def _component_data(self, item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            data = item.get("data", {})
            return data if isinstance(data, dict) else {}
        data = getattr(item, "data", {}) or {}
        return data if isinstance(data, dict) else {}

    def _message_chain_items(self, message_obj: Any) -> list[Any]:
        if message_obj is None:
            return []
        chain = getattr(message_obj, "message", None)
        if isinstance(chain, list):
            return chain
        if isinstance(message_obj, list):
            return message_obj
        if isinstance(message_obj, dict):
            raw = message_obj.get("message") or message_obj.get("content") or message_obj.get("messages")
            if isinstance(raw, list):
                return raw
            if raw:
                return [raw]
        return []

    def _extract_forward_id_from_segment_data(self, seg_data: dict[str, Any]) -> str:
        if not isinstance(seg_data, dict):
            return ""
        for key in ("id", "resid", "forward_id"):
            value = seg_data.get(key)
            if value:
                return str(value).strip()
        return ""

    def _extract_image_url_from_segment_data(self, seg_data: dict[str, Any]) -> str:
        if not isinstance(seg_data, dict):
            return ""
        for key in ("url", "source_url", "src", "origin", "origin_url"):
            value = seg_data.get(key)
            if isinstance(value, str) and value.strip().startswith(("http://", "https://", "file://", "data:")):
                return value.strip()
        value = seg_data.get("file")
        if isinstance(value, str) and value.strip().startswith(("http://", "https://", "file://", "data:")):
            return value.strip()
        for key in ("path", "file"):
            value = seg_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _extract_image_sources_from_message_obj(self, message_obj: Any) -> list[str]:
        sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        def visit(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if isinstance(value, dict):
                type_name = self._component_type_name(value)
                data = self._component_data(value)
                if type_name == "image":
                    add(self._extract_image_url_from_segment_data(data))
                    for key in ("url", "file", "path", "src", "origin_url", "source_url"):
                        add(data.get(key))
                        add(value.get(key))
                for key in ("message", "raw_message", "content", "messages", "data"):
                    nested = value.get(key)
                    if nested is not value:
                        visit(nested)
                return
            type_name = self._component_type_name(value)
            if type_name == "image":
                add(self._image_component_source(value))
                data = self._component_data(value)
                add(self._extract_image_url_from_segment_data(data))
                for key in ("url", "file", "path", "src", "origin_url", "source_url"):
                    add(data.get(key))
            raw_text = str(value or "")
            for match in re.finditer(r"\[CQ:image,([^\]]+)\]", raw_text):
                fields: dict[str, str] = {}
                for part in match.group(1).split(","):
                    if "=" not in part:
                        continue
                    key, val = part.split("=", 1)
                    fields[key.strip()] = html.unescape(val.strip())
                add(self._extract_image_url_from_segment_data(fields))
                for key in ("url", "file", "path"):
                    add(fields.get(key))

        visit(message_obj)
        return [source for source in sources if source]

    def _extract_messages_from_forward_data(self, forward_data: Any) -> list[dict[str, Any]]:
        if isinstance(forward_data, list):
            return [dict(item) for item in forward_data if isinstance(item, dict)]
        if not isinstance(forward_data, dict):
            return []
        for key in ("messages", "message", "nodes"):
            value = forward_data.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
        data = forward_data.get("data")
        if isinstance(data, list):
            return [dict(item) for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("messages", "message", "nodes"):
                value = data.get(key)
                if isinstance(value, list):
                    return [dict(item) for item in value if isinstance(item, dict)]
            nested = data.get("data")
            if isinstance(nested, list):
                return [dict(item) for item in nested if isinstance(item, dict)]
            if isinstance(nested, dict):
                for key in ("messages", "message", "nodes"):
                    value = nested.get(key)
                    if isinstance(value, list):
                        return [dict(item) for item in value if isinstance(item, dict)]
        return []

    def _forward_payload_shape(self, value: Any, *, depth: int = 0) -> str:
        if depth > 2:
            return "..."
        if isinstance(value, list):
            preview = ", ".join(self._forward_payload_shape(item, depth=depth + 1) for item in value[:3])
            return f"list[{len(value)}]({preview})"
        if isinstance(value, dict):
            keys = list(value.keys())[:10]
            parts = []
            for key in keys:
                child = value.get(key)
                if isinstance(child, (dict, list)):
                    parts.append(f"{key}:{self._forward_payload_shape(child, depth=depth + 1)}")
                else:
                    parts.append(f"{key}:{type(child).__name__}")
            return "{" + ", ".join(parts) + "}"
        return type(value).__name__

    def _forward_node_data(self, node: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(node, dict):
            return {}
        data = node.get("data")
        return data if isinstance(data, dict) else {}

    def _forward_node_content_chain(self, node: dict[str, Any]) -> Any:
        if not isinstance(node, dict):
            return []
        node_data = self._forward_node_data(node)
        for source in (node, node_data):
            for key in ("content", "message", "raw_message"):
                value = source.get(key)
                if value not in (None, "", []):
                    return value
        return []

    def _extract_forward_payload_from_message_obj(self, message_obj: Any) -> dict[str, Any]:
        if isinstance(message_obj, list):
            for item in message_obj:
                found = self._extract_forward_payload_from_message_obj(item)
                if found:
                    return found
            return {}
        if not isinstance(message_obj, dict):
            return {}
        if self._component_type_name(message_obj) == "forward":
            seg_data = self._component_data(message_obj)
            if isinstance(seg_data.get("messages"), list):
                return {"messages": seg_data.get("messages", [])}
            if isinstance(message_obj.get("messages"), list):
                return {"messages": message_obj.get("messages", [])}
        for key in ("message", "messages", "content", "data"):
            found = self._extract_forward_payload_from_message_obj(message_obj.get(key))
            if found:
                return found
        return {}

    def _extract_forward_id_from_message_obj(self, message_obj: Any) -> str:
        if isinstance(message_obj, list):
            for item in message_obj:
                found = self._extract_forward_id_from_message_obj(item)
                if found:
                    return found
            return ""
        if not isinstance(message_obj, dict):
            return ""
        if self._component_type_name(message_obj) == "forward":
            found = self._extract_forward_id_from_segment_data(self._component_data(message_obj))
            if found:
                return found
            found = self._extract_forward_id_from_segment_data(message_obj)
            if found:
                return found
        for key in ("message", "messages", "content", "data"):
            found = self._extract_forward_id_from_message_obj(message_obj.get(key))
            if found:
                return found
        return ""

    def _extract_reply_message_id(self, reply_seg: Any) -> str:
        candidates = [
            getattr(reply_seg, "id", None),
            getattr(reply_seg, "message_id", None),
            getattr(reply_seg, "msg_id", None),
        ]
        data = self._component_data(reply_seg)
        candidates.extend([data.get("id"), data.get("message_id"), data.get("msg_id")])
        if isinstance(reply_seg, dict):
            candidates.extend([reply_seg.get("id"), reply_seg.get("message_id"), reply_seg.get("msg_id")])
        for value in candidates:
            value_str = str(value or "").strip()
            if value_str:
                return value_str
        return ""

    def _build_inline_forward_id(self, payload: dict[str, Any]) -> str:
        try:
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            raw = str(payload)
        return "inline:" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

    async def _call_platform_action(self, event: AstrMessageEvent, action: str, **kwargs: Any) -> Any:
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return None
        return await call_action(action, **kwargs)

    async def _call_forward_msg_action(self, event: AstrMessageEvent, forward_id: str) -> Any:
        safe_id = str(forward_id or "").strip()
        if not safe_id:
            return None
        attempts = [
            ("get_forward_msg", {"id": safe_id}),
            ("get_forward_msg", {"message_id": safe_id}),
            ("get_forward_msg", {"res_id": safe_id}),
            ("get_forward_msg", {"resid": safe_id}),
        ]
        if safe_id.isdigit():
            attempts.extend(
                [
                    ("get_forward_msg", {"message_id": int(safe_id)}),
                    ("get_msg", {"message_id": int(safe_id)}),
                ]
            )
        attempts.append(("get_msg", {"message_id": safe_id}))
        last_error = ""
        for action, kwargs in attempts:
            try:
                raw = await self._call_platform_action(event, action, **kwargs)
            except Exception as exc:
                last_error = _single_line(str(exc), 120)
                continue
            if raw:
                logger.info(
                    "[PrivateCompanion] 合并消息平台接口返回: action=%s args=%s shape=%s",
                    action,
                    ",".join(kwargs.keys()),
                    self._forward_payload_shape(raw),
                )
                return raw
        if last_error:
            logger.info("[PrivateCompanion] 合并消息平台接口全部失败: id=%s error=%s", _single_line(safe_id, 80), last_error)
        return None

    async def _extract_forward_from_reply(self, event: AstrMessageEvent, reply_seg: Any) -> tuple[str, dict[str, Any]]:
        message_id = self._extract_reply_message_id(reply_seg)
        if not message_id:
            return "", {}
        message_obj = None
        try:
            message_obj = await self._call_platform_action(event, "get_msg", message_id=int(message_id))
        except Exception:
            try:
                message_obj = await self._call_platform_action(event, "get_msg", message_id=message_id)
            except Exception:
                message_obj = None
        if not message_obj:
            return "", {}
        raw_message = message_obj.get("message") if isinstance(message_obj, dict) else message_obj
        return self._extract_forward_id_from_message_obj(raw_message), self._extract_forward_payload_from_message_obj(raw_message)

    async def _extract_image_sources_from_reply(self, event: AstrMessageEvent, reply_seg: Any) -> list[str]:
        message_id = self._extract_reply_message_id(reply_seg)
        if not message_id:
            return []
        message_obj = None
        try:
            message_obj = await self._call_platform_action(event, "get_msg", message_id=int(message_id))
        except Exception:
            try:
                message_obj = await self._call_platform_action(event, "get_msg", message_id=message_id)
            except Exception as exc:
                logger.info("[PrivateCompanion] 引用图片读取失败: message_id=%s error=%s", message_id, _single_line(exc, 120))
                return []
        if isinstance(message_obj, dict):
            raw_message = message_obj.get("message") or message_obj.get("raw_message") or message_obj.get("content")
        else:
            raw_message = message_obj
        sources = self._extract_image_sources_from_message_obj(raw_message)
        if sources:
            logger.info("[PrivateCompanion] 引用消息图片已解析: message_id=%s images=%s", message_id, len(sources))
        return sources[:4]

    async def _find_reply_image_sources_for_event(self, event: AstrMessageEvent) -> list[str]:
        for item in self._event_components(event):
            type_name = self._component_type_name(item)
            if type_name == "reply" or "reply" in type_name:
                sources = await self._extract_image_sources_from_reply(event, item)
                if sources:
                    return sources
        return []

    async def _find_forward_descriptor_for_event(self, event: AstrMessageEvent) -> tuple[str, dict[str, Any]]:
        cached = getattr(event, "_private_companion_forward_descriptor", None)
        if (
            isinstance(cached, tuple)
            and len(cached) == 2
            and isinstance(cached[0], str)
            and isinstance(cached[1], dict)
        ):
            return cached
        cached_forward_id, cached_payload = self._cached_forward_descriptor_for_event(event)
        if cached_forward_id or cached_payload:
            return cached_forward_id, cached_payload
        message_obj = getattr(event, "message_obj", None)
        forward_id = ""
        forward_payload: dict[str, Any] = {}
        reply_seg = None
        for item in self._message_chain_items(message_obj):
            type_name = self._component_type_name(item)
            data = self._component_data(item)
            if "forward" in type_name:
                forward_id = (
                    str(getattr(item, "id", "") or getattr(item, "resid", "") or "").strip()
                    or self._extract_forward_id_from_segment_data(data)
                    or self._extract_forward_id_from_segment_data(item if isinstance(item, dict) else {})
                )
                if isinstance(data.get("messages"), list):
                    forward_payload = {"messages": data.get("messages", [])}
                elif isinstance(item, dict) and isinstance(item.get("messages"), list):
                    forward_payload = {"messages": item.get("messages", [])}
            elif "reply" in type_name:
                reply_seg = item
        if not (forward_id or forward_payload) and reply_seg is not None:
            forward_id, forward_payload = await self._extract_forward_from_reply(event, reply_seg)
        if forward_payload and not forward_id:
            forward_id = self._build_inline_forward_id(forward_payload)
        self._remember_forward_descriptor_for_event(event, forward_id, forward_payload)
        return forward_id, forward_payload

    async def _extract_forward_messages_for_prompt(
        self,
        event: AstrMessageEvent,
        forward_id: str,
        *,
        forward_payload: dict[str, Any] | None = None,
        depth: int = 0,
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        if depth > 2:
            return [], [], 0
        messages = self._extract_messages_from_forward_data(forward_payload or {})
        if not messages and forward_id and not forward_id.startswith("inline:"):
            raw = await self._call_forward_msg_action(event, forward_id)
            messages = self._extract_messages_from_forward_data(raw)
            if raw and not messages:
                logger.info(
                    "[PrivateCompanion] 合并消息接口返回未解析出节点: id=%s shape=%s",
                    _single_line(forward_id, 80),
                    self._forward_payload_shape(raw),
                )
        if not messages:
            return [], [], 0
        rows: list[dict[str, Any]] = []
        image_urls: list[str] = []
        nested_count = 0
        for node in messages:
            if len(rows) >= self.forward_message_max_messages:
                break
            node_data = self._forward_node_data(node)
            sender = node.get("sender") if isinstance(node.get("sender"), dict) else {}
            if not sender and isinstance(node_data.get("sender"), dict):
                sender = node_data.get("sender") or {}
            sender_id = str(
                sender.get("user_id")
                or sender.get("uin")
                or node.get("user_id")
                or node.get("sender_id")
                or node.get("uin")
                or node_data.get("user_id")
                or node_data.get("sender_id")
                or node_data.get("uin")
                or ""
            ).strip()
            raw_name = _single_line(
                sender.get("card")
                or sender.get("nickname")
                or sender.get("name")
                or node.get("nickname")
                or node.get("name")
                or node_data.get("nickname")
                or node_data.get("name")
                or sender_id
                or "未知用户",
                60,
            )
            display_name = self._group_member_identity_name(sender_id, raw_name, limit=40) if sender_id else raw_name
            sent_at = ""
            ts = _safe_float(node.get("time") or node.get("timestamp") or node_data.get("time") or node_data.get("timestamp"), 0)
            if ts > 0:
                try:
                    sent_at = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
                except Exception:
                    sent_at = ""
            content_chain = self._forward_node_content_chain(node)
            pending_nested_rows: list[dict[str, Any]] = []
            if isinstance(content_chain, str):
                text = content_chain
            else:
                if isinstance(content_chain, dict):
                    content_chain = [content_chain]
                elif not isinstance(content_chain, list):
                    content_chain = [content_chain] if content_chain else []
                text_parts: list[str] = []
                for segment in content_chain:
                    if isinstance(segment, str):
                        text_parts.append(segment)
                        continue
                    seg_type = self._component_type_name(segment)
                    seg_data = self._component_data(segment)
                    if seg_type in {"text", "plain"}:
                        text_parts.append(str(seg_data.get("text") or ""))
                    elif seg_type == "at":
                        qq = str(seg_data.get("qq") or getattr(segment, "qq", "") or "").strip()
                        text_parts.append("@" + self._group_member_identity_name(qq, qq, limit=30))
                    elif seg_type == "image":
                        url = self._extract_image_url_from_segment_data(seg_data)
                        if url:
                            image_urls.append(url)
                        text_parts.append("[图片]")
                    elif seg_type in {"face", "mface", "emoji"}:
                        text_parts.append("[表情]")
                    elif seg_type == "video":
                        text_parts.append("[视频]")
                    elif seg_type == "record":
                        text_parts.append("[语音]")
                    elif seg_type == "file":
                        name = _single_line(seg_data.get("name") or seg_data.get("file"), 80)
                        text_parts.append(f"[文件:{name}]" if name else "[文件]")
                    elif seg_type == "forward" and self.forward_message_parse_nested:
                        nested_id = self._extract_forward_id_from_segment_data(seg_data)
                        nested_payload = {"messages": seg_data.get("messages", [])} if isinstance(seg_data.get("messages"), list) else None
                        nested_rows, nested_images, child_nested = await self._extract_forward_messages_for_prompt(
                            event,
                            nested_id or self._build_inline_forward_id(nested_payload or {}),
                            forward_payload=nested_payload,
                            depth=depth + 1,
                        )
                        nested_count += 1 + child_nested
                        pending_nested_rows.extend(nested_rows)
                        image_urls.extend(nested_images)
                        text_parts.append("[嵌套合并消息已展开]")
                    elif seg_type == "node":
                        nested_rows, nested_images, child_nested = await self._extract_forward_messages_for_prompt(
                            event,
                            self._build_inline_forward_id({"messages": [segment]}),
                            forward_payload={"messages": [segment]},
                            depth=depth + 1,
                        )
                        nested_count += child_nested
                        pending_nested_rows.extend(nested_rows)
                        image_urls.extend(nested_images)
                        if nested_rows:
                            text_parts.append("[合并消息节点已展开]")
                text = "".join(text_parts).strip()
            text = _single_line(text, 500)
            if text:
                rows.append({"sender_id": sender_id, "sender": display_name or raw_name, "time": sent_at, "text": text, "depth": depth})
            rows.extend(pending_nested_rows)
        return rows[: self.forward_message_max_messages], image_urls[: max(0, self.forward_message_image_limit)], nested_count

    async def _format_forward_message_context_for_prompt(self, event: AstrMessageEvent, req: ProviderRequest) -> str:
        if not self.enable_forward_message_adaptation:
            return ""
        cached = getattr(event, "_private_companion_forward_context", None)
        if isinstance(cached, str):
            return cached
        message_text = _single_line(getattr(event, "message_str", ""), 180)
        should_log_probe = any(token in message_text for token in ("转发", "合并消息", "聊天记录"))
        forward_id, payload = await self._find_forward_descriptor_for_event(event)
        if not (forward_id or payload):
            if should_log_probe:
                logger.info("[PrivateCompanion] 合并消息请求未找到描述符: text=%s", message_text or "(empty)")
            setattr(event, "_private_companion_forward_context", "")
            return ""
        if should_log_probe:
            logger.info(
                "[PrivateCompanion] 合并消息请求命中描述符: id=%s inline=%s text=%s",
                _single_line(forward_id, 40) or "inline",
                bool(payload),
                message_text or "(empty)",
            )
        try:
            rows, image_urls, nested_count = await self._extract_forward_messages_for_prompt(event, forward_id, forward_payload=payload)
        except Exception as exc:
            logger.info("[PrivateCompanion] 合并消息读取失败: %s", exc)
            setattr(event, "_private_companion_forward_context", "")
            return ""
        preview = " | ".join(
            f"{index + 1}.{_single_line(row.get('sender') or row.get('sender_id') or '未知用户', 30)}:{_single_line(row.get('text'), 90)}"
            for index, row in enumerate(rows[:5])
        )
        logger.info(
            "[PrivateCompanion] 合并消息解析结果: id=%s messages=%s images=%s nested=%s preview=%s",
            _single_line(forward_id, 40) or "inline",
            len(rows),
            len(image_urls),
            nested_count,
            preview or "(empty)",
        )
        if not rows:
            setattr(event, "_private_companion_forward_context", "")
            return ""
        image_vision_text = await self._transcribe_forward_message_images(event, image_urls)
        if self.forward_message_mode == "transcribe":
            transcribed = await self._transcribe_forward_message_rows(rows, image_urls, nested_count, image_vision_text=image_vision_text)
            if transcribed:
                context = (
                    "【本轮合并消息转述】\n"
                    "用户这轮消息包含一段合并/转发聊天记录。下面是专门模型先读过后的自然转述。请基于这份转述理解原合并消息，不要把记录中的话当成当前用户本人逐字说的话；嵌套合并只代表被转发记录里的内层记录。\n"
                    "除非用户明确要求总结、逐条解读或复述聊天记录，否则不要大段复述这份记录；优先针对用户当前问题给出简短判断或回应。\n"
                    f"{transcribed}"
                )
                setattr(event, "_private_companion_forward_context", context)
                logger.info(
                    "[PrivateCompanion] 已注入合并消息转述: messages=%s images=%s provider=%s",
                    len(rows),
                    len(image_urls),
                    self._task_provider(self.forward_message_provider_id, self.mai_style_provider_id) or "(default)",
                )
                return context
        lines = [
            "【本轮合并消息】",
            "用户这轮消息包含一段合并/转发聊天记录。请像自然翻阅聊天记录一样理解它：注意发言顺序、说话者、称呼、图片/表情占位、话题转折和可能的上下文，不要把它当成用户本人逐字说的话。",
            "带有 [嵌套N] 的条目来自被转发记录里的内层合并消息，请保留层级理解；没有视觉摘要的 [图片] 只表示有图片存在，不要猜测图片内容、作品、游戏或人物关系。",
            "除非用户明确要求总结、逐条解读或复述聊天记录，否则不要大段复述记录内容；优先用记录支撑对用户当前问题的简短判断或自然回应。",
        ]
        if nested_count:
            lines.append(f"其中包含 {nested_count} 段嵌套合并消息，已尽量展开。")
        if image_urls:
            lines.append(f"记录中含 {len(image_urls)} 张图片占位。")
        if image_vision_text:
            lines.append("【合并消息图片视觉摘要】")
            lines.append(image_vision_text)
        used = 0
        for index, row in enumerate(rows, 1):
            sender_id = row.get("sender_id") or ""
            identity_note = self._group_member_identity_note(sender_id, limit=90) if sender_id else ""
            note = f"（{identity_note}）" if identity_note else ""
            when = f"{row.get('time')} " if row.get("time") else ""
            row_depth = _safe_int(row.get("depth"), 0, 0, 6)
            indent = "  " * row_depth
            nested_label = f"[嵌套{row_depth}] " if row_depth else ""
            line = f"{indent}{index}. {nested_label}{when}{row.get('sender') or sender_id or '未知用户'}{note}: {row.get('text') or ''}"
            used += len(line)
            if used > self.forward_message_max_chars:
                lines.append("……后续内容因长度限制已省略。")
                break
            lines.append(line)
        context = "\n".join(lines)
        setattr(event, "_private_companion_forward_context", context)
        logger.info("[PrivateCompanion] 已注入合并消息上下文: id=%s messages=%s images=%s", _single_line(forward_id, 40) or "inline", len(rows), len(image_urls))
        return context

    async def _transcribe_forward_message_images(self, event: AstrMessageEvent, image_sources: list[str]) -> str:
        if not self.forward_message_image_vision:
            return ""
        limit = max(0, int(getattr(self, "forward_message_image_limit", 0) or 0))
        sources = [str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:limit]
        if not sources:
            return ""
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        provider_id, provider_source, _configured_prompt = self._private_image_caption_provider_id(umo)
        if not provider_id:
            logger.info("[PrivateCompanion] 合并消息图片视觉跳过: 未配置首选识图模型或备选识图模型")
            return ""
        getter = getattr(self.context, "get_provider_by_id", None)
        provider = getter(provider_id) if callable(getter) else None
        if provider is None:
            fallback_provider_id = self._task_provider(self.jm_cosmos_vision_provider_id, self.narration_provider_id)
            if fallback_provider_id and fallback_provider_id != provider_id and callable(getter):
                provider_id = fallback_provider_id
                provider_source = "plugin_vision_fallback"
                provider = getter(provider_id)
            if provider is None:
                logger.info("[PrivateCompanion] 合并消息图片视觉跳过: provider 不可用 id=%s", provider_id)
                return ""
        image_items: list[tuple[str, str]] = []
        seen_image_keys: set[str] = set()
        for source in sources:
            url = self._private_image_source_to_model_url(source)
            if not url:
                continue
            image_key = self._private_image_source_cache_key(source) or ("model_url:" + hashlib.sha1(url.encode("utf-8", errors="ignore")).hexdigest())
            if image_key in seen_image_keys:
                continue
            seen_image_keys.add(image_key)
            image_items.append((image_key, url))
        image_keys = [key for key, _ in image_items]
        image_urls = [url for _, url in image_items]
        if not image_urls:
            return ""
        prompt = (
            "请按出现顺序阅读用户转发的合并聊天记录里的图片,输出供后续聊天模型理解消息集的视觉摘要。\n"
            "要求：\n"
            "1. 使用“第1张、第2张...”标明每张图片的大意,不要把图片当成用户当前单独发来的图片。\n"
            "2. 说明图片类型、可见文字、主体、情绪、梗或截图里的关键信息；如果像表情包,概括它在聊天记录中可能表达的语气。\n"
            "3. 不要替 Bot 回复用户,不要输出工具名、模型名、路径或插件信息。\n"
            "4. 不要编造看不见的细节；不确定时直接说明不确定。\n"
            "5. 总体保持简洁自然,每张图片一句到两句。"
        )
        self_recognition_prompt = self._private_image_self_recognition_prompt()
        if self_recognition_prompt and self_recognition_prompt not in prompt:
            prompt = f"{prompt}\n\n{self_recognition_prompt}"
        cache_key = self._private_image_vision_cache_key(image_keys, provider_id, prompt, scope="forward_image")
        cached_text = self._get_private_image_vision_cache(cache_key, provider_id=provider_id, image_keys=image_keys, scope="forward_image")
        if cached_text:
            logger.info(
                "[PrivateCompanion] 合并消息图片视觉命中缓存: provider=%s images=%s preview=%s",
                provider_id,
                len(image_urls),
                _single_line(cached_text, 220),
            )
            return cached_text
        if not self._can_run_llm_task(provider_id, task="forward_message_image_vision"):
            self._record_llm_budget_skip(provider_id=provider_id, task="forward_message_image_vision", prompt=prompt)
            return ""
        try:
            start = time.time()
            timeout = max(0.0, float(getattr(self, "forward_message_image_vision_timeout_seconds", 6.0) or 0.0))
            if timeout > 0:
                result = await asyncio.wait_for(provider.text_chat(prompt=prompt, image_urls=image_urls), timeout=timeout)
            else:
                result = await provider.text_chat(prompt=prompt, image_urls=image_urls)
            text = str(getattr(result, "completion_text", result) or "").strip()
            cleaned_text = _single_line(_strip_internal_message_blocks(text), 900)
            self._record_llm_usage(
                provider_id=provider_id,
                task="forward_message_image_vision",
                prompt=prompt,
                completion=text,
                resp=result,
                elapsed_ms=int((time.time() - start) * 1000),
                success=True,
                budget_exempt=True,
            )
            logger.info(
                "[PrivateCompanion] 合并消息图片视觉完成: provider=%s source=%s images=%s chars=%s preview=%s",
                provider_id,
                provider_source,
                len(image_urls),
                len(cleaned_text),
                _single_line(cleaned_text, 220),
            )
            self._set_private_image_vision_cache(cache_key, cleaned_text, provider_id=provider_id, image_keys=image_keys, prompt=prompt, scope="forward_image")
            return cleaned_text
        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - start) * 1000) if "start" in locals() else 0
            self._record_llm_usage(
                provider_id=provider_id,
                task="forward_message_image_vision",
                prompt=prompt,
                completion="",
                elapsed_ms=elapsed_ms,
                success=False,
                error=f"timeout after {timeout:.1f}s",
                budget_exempt=True,
            )
            logger.info(
                "[PrivateCompanion] 合并消息图片视觉超时跳过: provider=%s images=%s timeout=%.1fs",
                provider_id,
                len(image_urls),
                timeout,
            )
            return ""
        except Exception as exc:
            logger.info("[PrivateCompanion] 合并消息图片视觉失败: %s", _single_line(exc, 160))
            return ""

    async def _transcribe_forward_message_rows(
        self,
        rows: list[dict[str, Any]],
        image_urls: list[str],
        nested_count: int,
        *,
        image_vision_text: str = "",
    ) -> str:
        provider_id = self._task_provider(self.forward_message_provider_id, self.mai_style_provider_id)
        raw_lines: list[str] = []
        used = 0
        for index, row in enumerate(rows, 1):
            when = f"{row.get('time')} " if row.get("time") else ""
            row_depth = _safe_int(row.get("depth"), 0, 0, 6)
            indent = "  " * row_depth
            nested_label = f"[嵌套{row_depth}] " if row_depth else ""
            line = f"{indent}{index}. {nested_label}{when}{row.get('sender') or '未知用户'}: {row.get('text') or ''}"
            used += len(line)
            if used > max(800, self.forward_message_max_chars):
                raw_lines.append("……后续节点因长度限制已省略。")
                break
            raw_lines.append(line)
        prompt = (
            "你是合并消息转述器。请阅读下面的聊天记录节点，把它转述成一份自然、清晰、方便另一个人格模型继续回应用户的中文记录。\n"
            "要求：\n"
            "1. 保留发言顺序、说话者、关键事实、争议点、情绪变化和未解决问题。\n"
            "2. 不要把记录中的话当成当前用户说的话；它们只是被转来的聊天记录。\n"
            "3. 遇到 [图片]、[表情]、[语音]、[文件] 只说明它们存在，不要编造具体内容。\n"
            "4. 带有 [嵌套N] 的条目来自内层合并消息，转述时要说明它是内层记录，不要和外层聊天混成同一层。\n"
            "5. 不要替 Bot 回复用户，不要输出寒暄，只输出转述内容。\n"
            "6. 如果内容很短，可以简短转述；如果内容较长，用条理清楚的段落或要点。\n\n"
            f"合并消息转述任务信息：节点数={len(rows)}，图片占位数={len(image_urls)}，嵌套合并数={nested_count}。\n"
            "聊天记录节点：\n"
            + "\n".join(raw_lines)
        )
        if image_vision_text:
            prompt += "\n\n合并消息图片视觉摘要：\n" + image_vision_text
        result = await self._llm_call(
            prompt,
            max_tokens=900,
            provider_id=provider_id,
            task="forward_message",
        )
        return _strip_internal_message_blocks(result or "").strip()

    async def _append_forward_message_context_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_forward_message_v1 -->"
        current_prompt = req.system_prompt or ""
        message_text = _single_line(getattr(event, "message_str", ""), 180)
        should_log_probe = any(token in message_text for token in ("转发", "合并消息", "聊天记录"))
        if marker in current_prompt:
            if should_log_probe:
                logger.info("[PrivateCompanion] 合并消息请求注入跳过: 已存在 marker text=%s", message_text or "(empty)")
            return
        if should_log_probe:
            logger.info("[PrivateCompanion] 合并消息请求开始注入检查: text=%s", message_text or "(empty)")
        context = await self._format_forward_message_context_for_prompt(event, req)
        if context:
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{context}".strip()
        elif should_log_probe:
            logger.info("[PrivateCompanion] 合并消息请求未生成上下文: text=%s", message_text or "(empty)")

