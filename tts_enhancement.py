# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import random
import re
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
try:
    from astrbot.api.message_components import Plain, Record
except ImportError:
    from astrbot.api.message_components import Plain
    from astrbot.core.message.components import Record
from astrbot.core import file_token_service
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .helpers import _single_line


TTS_BLOCK_PATTERN = re.compile(r"<t{2,}s\b[^>]*>.*?</t{2,}s>", re.IGNORECASE | re.DOTALL)
TTS_TAG_PATTERN = re.compile(r"</?t{2,}s\b[^>]*>", re.IGNORECASE)
TTS_BLOCK_TOKEN_PATTERN = re.compile(r"\[\[TTSBLOCK:([0-9a-f]{16})\]\]")
EMOTION_TAG_PATTERN = re.compile(r"\[([^\[\]\n]{1,24})\]")
DEFAULT_AUTO_VOICE_PROMPT_MARKERS = (
    "随机日语语音模式",
    "日语语音",
    "原中文文本",
    "自动日语语音",
)
DEFAULT_TTS_SANITIZE_REMOVE_PATTERNS = (
    r"[（(][^（()]*[）)]",
    r"[＞>][＿_][＜<]",
    r"[＾^][＿_][＾^]",
    r"[oO][＿_][oO]",
    r"[xX][＿_][xX]",
    r"[－-][＿_][－-]",
    r"[★☆♪♫♬♩♡♥❤️💖💕💗💓💝💟💜💛💚💙🧡🤍🖤🤎💔❣️💋]",
    r"[→←↑↓↖↗↘↙↔↕↺↻]",
)
DEFAULT_TTS_SANITIZE_FILTER_WORDS = (
    "ω", "Ω", "σ", "Σ", "ε", "д", "Д",
    "´", "`", "＝", "∀", "∇",
    "orz", "OTZ", "QAQ", "QWQ", "TAT", "TUT", "www",
)
DEFAULT_TTS_SANITIZE_REPLACEMENTS = {
    "233": "哈哈哈",
    "666": "厉害",
    "999": "很棒",
    "555": "呜呜呜",
}
TTS_EMOTION_PLACEHOLDER_PREFIX = "__PRIVATE_COMPANION_TTS_EMOTION_"


class TtsEnhancementMixin:
    """Integrated TTS enhancement for private_companion.

    This is intentionally not a verbatim copy of tts_modify. It keeps the useful
    behavior surface but maps identity and prompts to private_companion concepts.
    """

    def _load_tts_enhancement_config(self, config: Any) -> None:
        self.enable_tts_enhancement = self._cfg_bool(config, "enable_tts_enhancement", False)
        self.tts_generation_mode = self._cfg_str(config, "tts_generation_mode", "hybrid", "hybrid").lower()
        if self.tts_generation_mode not in {"hybrid", "direct", "convert"}:
            self.tts_generation_mode = "hybrid"
        self.tts_voice_language = self._cfg_str(config, "tts_voice_language", "ja", "ja").lower()
        if self.tts_voice_language not in {"ja", "zh", "en"}:
            self.tts_voice_language = "ja"
        self.tts_conversion_provider_id = self._cfg_str(config, "tts_conversion_provider_id", "")
        self.tts_extra_prompt = self._cfg_str(config, "tts_extra_prompt", "")
        self.auto_voice_enabled = self._cfg_bool(config, "auto_voice_enabled", self._cfg_bool(config, "auto_japanese_voice_enabled", False))
        self.auto_voice_full_conversion_enabled = self._cfg_bool(
            config,
            "auto_voice_full_conversion_enabled",
            self._cfg_bool(config, "auto_japanese_voice_full_conversion_enabled", False),
        )
        self.auto_voice_probability = self._cfg_int(
            config,
            "auto_voice_probability",
            self._cfg_int(config, "auto_japanese_voice_probability", 20, 0, 100),
            0,
            100,
        ) / 100.0
        self.auto_voice_max_chars = self._cfg_int(
            config,
            "auto_voice_max_chars",
            self._cfg_int(config, "auto_japanese_voice_max_chars", 50, 0),
            0,
        )
        self.auto_voice_cooldown_seconds = self._cfg_int(
            config,
            "auto_voice_cooldown_seconds",
            self._cfg_int(config, "auto_japanese_voice_cooldown_seconds", 120, 0),
            0,
        )
        self.main_user_voice_probability = self._cfg_int(
            config,
            "main_user_voice_probability",
            self._cfg_int(config, "auto_japanese_voice_admin_probability", -1, -1, 100),
            -1,
            100,
        ) / 100.0
        self.main_user_mention_voice_keywords = self._parse_text_list_config(
            config.get("main_user_mention_voice_keywords", config.get("admin_mention_keyword_voice_keywords", "")),
            limit=80,
        )
        self.main_user_mention_voice_probability = self._cfg_int(
            config,
            "main_user_mention_voice_probability",
            self._cfg_int(config, "admin_mention_keyword_voice_probability", 0, 0, 100),
            0,
            100,
        ) / 100.0
        self.main_user_mention_voice_prompt = self._cfg_str(
            config,
            "main_user_mention_voice_prompt",
            self._cfg_str(config, "admin_mention_keyword_voice_prompt", ""),
        )
        self._tts_auto_voice_last_at: dict[str, float] = {}

    def _tts_provider_kind(self, tts_provider: Any = None, provider_settings: dict[str, Any] | None = None) -> str:
        pieces: list[str] = []
        if tts_provider is not None:
            pieces.extend([
                tts_provider.__class__.__name__,
                str(getattr(tts_provider, "name", "") or ""),
                str(getattr(tts_provider, "provider_type", "") or ""),
            ])
        if provider_settings:
            for key in ("type", "provider", "provider_id", "api_base", "model", "name"):
                pieces.append(str(provider_settings.get(key, "") or ""))
        text = " ".join(pieces).lower()
        if "fish" in text:
            return "fishaudio"
        if "gsv" in text or "gptsovits" in text or "so-vits" in text:
            return "gsv"
        if "openai" in text:
            return "openai"
        if "edge" in text:
            return "edge"
        if "azure" in text:
            return "azure"
        if "gemini" in text:
            return "gemini"
        if "minimax" in text:
            return "minimax"
        if "aliyun" in text or "alibaba" in text or "阿里" in text:
            return "aliyun"
        if "volc" in text or "huoshan" in text or "火山" in text:
            return "volcengine"
        return "generic"

    def _tts_provider_allows_emotion_tags(self, kind: str) -> bool:
        return kind in {"fishaudio", "gsv"}

    def _tts_language_label(self) -> str:
        return {"ja": "日语", "zh": "中文", "en": "英语"}.get(getattr(self, "tts_voice_language", "ja"), "日语")

    def _normalize_tts_tags(self, text: str) -> str:
        source = str(text or "")
        source = re.sub(r"<(/?)t{2,}s\b[^>]*>", lambda m: f"</tts>" if m.group(1) else "<tts>", source, flags=re.IGNORECASE)
        source = re.sub(r"</tts>\s*</tts>+", "</tts>", source, flags=re.IGNORECASE)
        pieces: list[str] = []
        open_count = 0
        pos = 0
        for match in re.finditer(r"</?tts>", source, flags=re.IGNORECASE):
            pieces.append(source[pos:match.start()])
            tag = match.group(0).lower()
            if tag == "<tts>":
                open_count += 1
                pieces.append("<tts>")
            elif open_count > 0:
                open_count -= 1
                pieces.append("</tts>")
            pos = match.end()
        pieces.append(source[pos:])
        if open_count > 0:
            pieces.append("</tts>" * open_count)
        return "".join(pieces)

    def _strip_or_keep_emotion_tags(self, text: str, *, provider_kind: str) -> str:
        if self._tts_provider_allows_emotion_tags(provider_kind):
            return str(text or "")
        return EMOTION_TAG_PATTERN.sub("", str(text or "")).strip()

    def _normalize_tts_spoken_text(self, text: str, *, provider_kind: str) -> str:
        cleaned = self._normalize_tts_tags(text)
        cleaned = self._strip_or_keep_emotion_tags(cleaned, provider_kind=provider_kind)
        cleaned = re.sub(r"</?tts>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        return _single_line(cleaned, 2000)

    def _sanitize_tts_spoken_text(self, text: str, *, provider_kind: str) -> str:
        """Clean text immediately before get_audio, scoped to TTS强化 only."""
        if not text:
            return ""
        source = str(text)
        protected: dict[str, str] = {}
        if self._tts_provider_allows_emotion_tags(provider_kind):
            def _protect_emotion(match: re.Match[str]) -> str:
                token = f"{TTS_EMOTION_PLACEHOLDER_PREFIX}{len(protected)}__"
                protected[token] = match.group(0)
                return token

            source = EMOTION_TAG_PATTERN.sub(_protect_emotion, source)

        if len(source) > 10000:
            return ""

        for pattern in DEFAULT_TTS_SANITIZE_REMOVE_PATTERNS:
            try:
                source = re.sub(pattern, "", source)
            except re.error:
                continue
        for word in DEFAULT_TTS_SANITIZE_FILTER_WORDS:
            source = source.replace(word, "")
        for original, replacement in DEFAULT_TTS_SANITIZE_REPLACEMENTS.items():
            source = source.replace(original, replacement)

        source = re.sub(r"(.)\1{2,}", lambda m: m.group(1) * 2, source)
        source = re.sub(r'[""\u201c\u201d]\s*[""\u201c\u201d]', "", source)
        source = re.sub(r"[''\u2018\u2019]\s*[''\u2018\u2019]", "", source)
        source = re.sub(r"[「」『』【】\[\]]\s*[「」『』【】\[\]]", "", source)
        source = re.sub(r"[,，、;；]\s*(?=[,，、;；\s])", "", source)
        source = re.sub(r"[,，、;；]\s*$", "", source)
        source = re.sub(r"^\s*[,，、;；]\s*", "", source)
        source = re.sub(r"\s+", " ", source).strip()

        for token, original in protected.items():
            source = source.replace(token, original)
        return source.strip()

    def _tts_text_matches_language(self, text: str) -> bool:
        spoken = self._normalize_tts_spoken_text(text, provider_kind="fishaudio")
        lang = getattr(self, "tts_voice_language", "ja")
        if lang == "ja":
            return bool(re.search(r"[\u3040-\u30ff\u31f0-\u31ff]", spoken))
        if lang == "en":
            letters = len(re.findall(r"[A-Za-z]", spoken))
            cjk = len(re.findall(r"[\u4e00-\u9fff]", spoken))
            return letters >= max(4, cjk)
        return bool(spoken)

    def _build_tts_rule_prompt(self, provider_kind: str = "generic") -> str:
        lang = self._tts_language_label()
        mode = getattr(self, "tts_generation_mode", "hybrid")
        tag_rule = (
            "可以使用 <tts>...</tts> 标出真正需要朗读的内容；标签外文本会作为普通聊天文字保留。适合采用“中文显示文本 + 外语语音块”的表达：标签外中文自然聊天，标签内按目标语种朗读。"
            if mode in {"hybrid", "direct"}
            else "本轮主模型可以正常回复，不需要主动写 <tts>；后处理会生成语音格式。"
        )
        emotion_rule = (
            "当前 TTS provider 适合保留 [happy]、[sad] 这类方括号情绪标签。"
            if self._tts_provider_allows_emotion_tags(provider_kind)
            else ""
        )
        extra = _single_line(getattr(self, "tts_extra_prompt", ""), 800)
        if not extra:
            extra = self._legacy_nondefault_tts_prompt()
        return "\n".join(
            item
            for item in [
                "【TTS强化】",
                f"当前语音正文目标语种：{lang}。",
                tag_rule,
                emotion_rule,
                "如果写错成 <ttts>、<tttts> 等多 t 标签，系统会规范化，但你应优先输出标准 <tts>...</tts>。",
                f"补充规则：{extra}" if extra else "",
            ]
            if item
        )

    def _legacy_nondefault_tts_prompt(self) -> str:
        try:
            config = getattr(self, "config", {}) or {}
            value = str(config.get("tts_prompt", "") or "").strip()
        except Exception:
            value = ""
        if not value:
            return ""
        lowered = value.lower()
        if "<tts>" in lowered and "日语" in value and len(value) > 80:
            return ""
        return _single_line(value, 800)

    async def apply_tts_enhancement_request(self, event: Any, req: Any) -> None:
        if not getattr(self, "enabled", False) or not getattr(self, "enable_tts_enhancement", False):
            return
        if not hasattr(req, "system_prompt"):
            return
        try:
            config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
        except Exception:
            config = getattr(self, "config", {}) or {}
        provider_settings = dict(config.get("provider_tts_settings", {}) or {})
        provider_kind = self._tts_provider_kind(provider_settings=provider_settings)
        marker = "<!-- private_companion_tts_enhancement_v1 -->"
        prompt = str(getattr(req, "system_prompt", "") or "")
        if marker not in prompt and getattr(self, "tts_generation_mode", "hybrid") in {"hybrid", "direct"}:
            req.system_prompt = f"{prompt}\n\n{marker}\n{self._build_tts_rule_prompt(provider_kind)}".strip()
        if self._should_force_tts_for_main_user_event(event):
            req.system_prompt = (
                f"{req.system_prompt}\n\n【本轮 TTS 强化触发】\n"
                "这轮消息来自主用户或明确 @ 到主用户。请在自然回复里包含一段适合 TTS 朗读的 <tts>...</tts> 内容。"
            ).strip()

    async def protect_tts_enhancement_response_blocks(self, event: Any, resp: Any) -> None:
        if not getattr(self, "enable_tts_enhancement", False):
            return
        text = self._normalize_tts_tags(str(getattr(resp, "completion_text", "") or ""))
        if text:
            resp.completion_text = text

    async def apply_tts_enhancement_before_send(self, event: Any) -> None:
        if not getattr(self, "enabled", False) or not getattr(self, "enable_tts_enhancement", False):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(isinstance(comp, Record) for comp in chain):
            return
        plain_parts = [str(getattr(comp, "text", "") or "") for comp in chain if isinstance(comp, Plain)]
        if len(plain_parts) != len(chain):
            return
        text = "".join(plain_parts).strip()
        if not text:
            return
        normalized = self._normalize_tts_tags(text)
        if "<tts>" in normalized.lower() and "</tts>" in normalized.lower():
            new_chain = await self._process_tts_tags(normalized, event)
        else:
            new_chain = await self._maybe_convert_plain_reply_to_tts(normalized, event)
        if not new_chain:
            return
        event.set_result(self._build_result_from_chain(new_chain))

    async def _maybe_convert_plain_reply_to_tts(self, text: str, event: Any) -> list[Any]:
        mode = getattr(self, "tts_generation_mode", "hybrid")
        if mode == "direct":
            return []
        should_convert = mode == "convert"
        reason = "convert_mode" if should_convert else ""
        if not should_convert:
            ok, reason = self._auto_voice_trigger_reason(text, event)
            should_convert = ok
        if not should_convert:
            return []
        converted = await self._convert_text_to_tts_markup(text, event, full=(mode == "convert" or self.auto_voice_full_conversion_enabled))
        if not converted:
            return []
        chain = await self._process_tts_tags(converted, event, fallback_plain=text)
        if chain:
            session = str(getattr(event, "unified_msg_origin", "") or "")
            self._tts_auto_voice_last_at[session] = time.time()
            logger.info("[PrivateCompanion] TTS强化已转换纯文本回复: reason=%s session=%s", reason, _single_line(session, 80))
        return chain

    def _auto_voice_trigger_reason(self, text: str, event: Any) -> tuple[bool, str]:
        if not getattr(self, "auto_voice_enabled", False):
            return False, ""
        session = str(getattr(event, "unified_msg_origin", "") or "")
        is_main = self._event_targets_main_user(event)
        if is_main and self.main_user_voice_probability >= 0:
            probability = self.main_user_voice_probability
            bypass_limits = True
            reason = "main_user"
        else:
            probability = getattr(self, "auto_voice_probability", 0.0)
            bypass_limits = False
            reason = "auto"
        if self._event_mentions_main_user_with_keyword(event):
            probability = max(probability, getattr(self, "main_user_mention_voice_probability", 0.0))
            bypass_limits = True
            reason = "main_user_keyword"
        if probability <= 0 or random.random() > probability:
            return False, ""
        cleaned = _single_line(self._normalize_tts_spoken_text(text, provider_kind="generic"), 10000)
        max_chars = int(getattr(self, "auto_voice_max_chars", 0) or 0)
        if max_chars > 0 and not bypass_limits and len(cleaned) > max_chars:
            return False, ""
        cooldown = int(getattr(self, "auto_voice_cooldown_seconds", 0) or 0)
        if cooldown > 0 and not bypass_limits and session:
            last = float(getattr(self, "_tts_auto_voice_last_at", {}).get(session, 0) or 0)
            if time.time() - last < cooldown:
                return False, ""
        return True, reason

    def _configured_main_user_ids(self) -> set[str]:
        ids: set[str] = set()
        for value in getattr(self, "target_user_ids", []) or []:
            text = re.sub(r"\D+", "", str(value or ""))
            if text:
                ids.add(text)
        aliases = getattr(self, "private_user_aliases", {}) or {}
        if isinstance(aliases, dict):
            for key, value in aliases.items():
                for raw in (key, value):
                    text = re.sub(r"\D+", "", str(raw or ""))
                    if text:
                        ids.add(text)
        return ids

    def _event_targets_main_user(self, event: Any) -> bool:
        main_ids = self._configured_main_user_ids()
        if not main_ids:
            return False
        try:
            sender = re.sub(r"\D+", "", str(event.get_sender_id()))
        except Exception:
            sender = ""
        if sender and sender in main_ids:
            return True
        for target in self._event_at_qq_ids(event):
            if target in main_ids:
                return True
        return False

    def _event_mentions_main_user_with_keyword(self, event: Any) -> bool:
        if not self._event_targets_main_user(event):
            return False
        keywords = [item for item in getattr(self, "main_user_mention_voice_keywords", []) or [] if item]
        if not keywords:
            return False
        text = str(getattr(event, "message_str", "") or "")
        return any(keyword in text for keyword in keywords)

    def _should_force_tts_for_main_user_event(self, event: Any) -> bool:
        if not getattr(self, "auto_voice_enabled", False):
            return False
        if self._event_mentions_main_user_with_keyword(event) and self.main_user_mention_voice_probability > 0:
            return random.random() <= self.main_user_mention_voice_probability
        if self._event_targets_main_user(event) and self.main_user_voice_probability >= 0:
            return random.random() <= self.main_user_voice_probability
        return False

    def _event_at_qq_ids(self, event: Any) -> set[str]:
        ids: set[str] = set()
        message_obj = getattr(event, "message_obj", None)
        chain = getattr(message_obj, "message", None)
        for comp in chain or []:
            qq = getattr(comp, "qq", None) or getattr(comp, "target", None)
            text = re.sub(r"\D+", "", str(qq or ""))
            if text:
                ids.add(text)
        raw = str(getattr(event, "message_str", "") or "")
        for match in re.finditer(r"\[At:(\d+)\]|@(\d{5,})", raw):
            ids.add(match.group(1) or match.group(2))
        return ids

    async def _convert_text_to_tts_markup(self, text: str, event: Any, *, full: bool = False) -> str:
        source = _single_line(text, 1200)
        if not source:
            return ""
        provider = await self._get_tts_conversion_provider(event)
        provider_kind = self._tts_provider_kind(provider_settings={})
        lang = self._tts_language_label()
        extra = _single_line(getattr(self, "main_user_mention_voice_prompt", ""), 500) if self._event_mentions_main_user_with_keyword(event) else ""
        prompt = f"""
请把下面这条回复转换成适合 TTS 朗读的最终输出。

目标语种：{lang}
输出格式：{"整条回复只输出一个 <tts>...</tts> 语音块" if full else "可保留少量原文铺垫，但必须包含一个 <tts>...</tts> 语音块"}
Provider 规则：{"可保留少量方括号情绪标签" if self._tts_provider_allows_emotion_tags(provider_kind) else "按普通朗读文本处理"}
补充要求：{extra or "无"}

原回复：
{source}

只输出最终消息，不要解释。
""".strip()
        try:
            if provider is not None:
                resp = await self._tts_provider_text_chat(provider, prompt, max_tokens=700)
                converted = str(getattr(resp, "completion_text", resp) or "").strip()
            else:
                converted = f"<tts>{source}</tts>"
        except Exception as exc:
            logger.warning("[PrivateCompanion] TTS强化转换模型失败: %s", _single_line(exc, 120))
            converted = f"<tts>{source}</tts>"
        converted = self._normalize_tts_tags(converted)
        if "<tts>" not in converted.lower():
            converted = f"<tts>{converted}</tts>"
        return converted

    async def _get_tts_conversion_provider(self, event: Any) -> Any:
        provider_id = str(getattr(self, "tts_conversion_provider_id", "") or "").strip()
        if provider_id:
            getter = getattr(self.context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    return getter(provider_id)
                except Exception:
                    pass
        get_using = getattr(self.context, "get_using_provider", None)
        if callable(get_using) and event is not None:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            try:
                return get_using(umo=umo)
            except TypeError:
                try:
                    return get_using(umo)
                except Exception:
                    return None
            except Exception:
                return None
        return None

    async def _tts_provider_text_chat(self, provider: Any, prompt: str, *, max_tokens: int = 700) -> Any:
        try:
            return await provider.text_chat(prompt=prompt, max_tokens=max_tokens)
        except TypeError:
            return await provider.text_chat(prompt=prompt)

    async def _process_tts_tags(self, text: str, event_or_provider: Any, provider_settings: dict[str, Any] | None = None, config: dict[str, Any] | None = None, fallback_plain: str = "") -> list[Any]:
        if hasattr(event_or_provider, "get_result"):
            event = event_or_provider
            try:
                config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
            except Exception:
                config = getattr(self, "config", {}) or {}
            provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
            try:
                tts_provider = self.context.get_using_tts_provider(str(getattr(event, "unified_msg_origin", "") or ""))
            except Exception:
                tts_provider = None
        else:
            event = None
            tts_provider = event_or_provider
            config = config or getattr(self, "config", {}) or {}
            provider_settings = provider_settings or dict((config or {}).get("provider_tts_settings", {}) or {})
        if not tts_provider:
            return [Plain(fallback_plain or re.sub(TTS_TAG_PATTERN, "", str(text or "")).strip())] if fallback_plain else []
        provider_kind = self._tts_provider_kind(tts_provider, provider_settings)
        normalized = self._normalize_tts_tags(text)
        output: list[Any] = []
        pos = 0
        for match in re.finditer(r"<tts>(.*?)</tts>", normalized, flags=re.IGNORECASE | re.DOTALL):
            before = normalized[pos:match.start()]
            if before.strip():
                output.append(Plain(before.strip()))
            spoken = self._normalize_tts_spoken_text(match.group(1), provider_kind=provider_kind)
            if not spoken:
                pos = match.end()
                continue
            if getattr(self, "tts_voice_language", "ja") == "ja" and not self._tts_text_matches_language(spoken):
                spoken = await self._convert_text_to_spoken_language(spoken, event, provider_kind=provider_kind)
            record = await self._tts_record_component(spoken, tts_provider, provider_settings, config or {})
            if record is not None:
                output.append(record)
            pos = match.end()
        after = re.sub(r"</?t{2,}s\b[^>]*>", "", normalized[pos:], flags=re.IGNORECASE).strip()
        if after:
            output.append(Plain(after))
        return output

    async def _convert_text_to_spoken_language(self, text: str, event: Any, *, provider_kind: str) -> str:
        provider = await self._get_tts_conversion_provider(event) if event is not None else None
        lang = self._tts_language_label()
        prompt = f"把下面内容改写成自然{lang}口语，只输出朗读文本，不要解释：\n{text}"
        try:
            if provider is not None:
                resp = await self._tts_provider_text_chat(provider, prompt, max_tokens=360)
                converted = str(getattr(resp, "completion_text", resp) or "").strip()
                return self._normalize_tts_spoken_text(converted, provider_kind=provider_kind) or text
        except Exception:
            pass
        return text

    async def _tts_record_component(self, spoken: str, tts_provider: Any, provider_settings: dict[str, Any], config: dict[str, Any]) -> Any | None:
        provider_kind = self._tts_provider_kind(tts_provider, provider_settings)
        sanitized = self._sanitize_tts_spoken_text(spoken, provider_kind=provider_kind)
        if not sanitized:
            return None
        if sanitized != spoken:
            logger.info(
                "[PrivateCompanion] TTS强化朗读文本已清洗: '%s' -> '%s'",
                _single_line(spoken, 80),
                _single_line(sanitized, 80),
            )
        try:
            audio_path = await tts_provider.get_audio(sanitized)
        except Exception as exc:
            logger.warning("[PrivateCompanion] TTS强化生成语音失败: %s", _single_line(exc, 120))
            return None
        if not audio_path:
            return None
        try:
            audio_file = Path(audio_path).resolve()
            expected_dir = Path(get_astrbot_data_path()).resolve()
            if not audio_file.is_relative_to(expected_dir):
                logger.warning("[PrivateCompanion] TTS强化拒绝不安全语音路径: %s", _single_line(audio_path, 160))
                return None
        except Exception as exc:
            logger.warning("[PrivateCompanion] TTS强化检查语音路径失败: %s", _single_line(exc, 120))
            return None
        final_ref = str(audio_path)
        if provider_settings.get("use_file_service", False):
            callback_api_base = str((config or {}).get("callback_api_base", "") or "").strip()
            if callback_api_base:
                try:
                    token = await file_token_service.register_file(str(audio_path))
                    final_ref = f"{callback_api_base}/api/file/{token}"
                except Exception as exc:
                    logger.warning("[PrivateCompanion] TTS强化注册语音文件失败: %s", _single_line(exc, 120))
        try:
            return Record(file=final_ref, url=final_ref)
        except TypeError:
            try:
                return Record(file=final_ref)
            except TypeError:
                return Record.fromFileSystem(str(audio_path))
