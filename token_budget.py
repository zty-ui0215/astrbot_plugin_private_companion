# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from astrbot.api import logger

from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key

class TokenBudgetMixin:
    """Methods split from main.PrivateCompanionPlugin."""

    @staticmethod
    def _estimate_token_count(text: str) -> int:
        raw = str(text or "")
        if not raw:
            return 0
        ascii_chars = sum(1 for ch in raw if ord(ch) < 128)
        non_ascii_chars = max(0, len(raw) - ascii_chars)
        return max(1, int(ascii_chars / 4.0 + non_ascii_chars / 1.6))

    @staticmethod
    def _usage_value(usage: Any, *keys: str) -> int:
        if not usage:
            return 0
        for key in keys:
            value = None
            if isinstance(usage, dict):
                value = usage.get(key)
            else:
                value = getattr(usage, key, None)
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = 0
            if parsed > 0:
                return parsed
        return 0

    def _extract_llm_usage(self, resp: Any, prompt: str, completion: str) -> dict[str, Any]:
        candidates = [
            getattr(resp, "usage", None),
            getattr(resp, "token_usage", None),
            getattr(resp, "raw_usage", None),
        ]
        raw_response = getattr(resp, "raw_response", None)
        if isinstance(raw_response, dict):
            candidates.extend([
                raw_response.get("usage"),
                raw_response.get("token_usage"),
            ])
        usage = next((item for item in candidates if item), None)
        prompt_tokens = self._usage_value(usage, "prompt_tokens", "input_tokens", "prompt", "input")
        completion_tokens = self._usage_value(usage, "completion_tokens", "output_tokens", "completion", "output")
        total_tokens = self._usage_value(usage, "total_tokens", "total")
        estimated = False
        if total_tokens <= 0:
            if prompt_tokens <= 0:
                prompt_tokens = self._estimate_token_count(prompt)
            if completion_tokens <= 0:
                completion_tokens = self._estimate_token_count(completion)
            total_tokens = prompt_tokens + completion_tokens
            estimated = True
        elif prompt_tokens <= 0 and completion_tokens <= 0:
            prompt_tokens = self._estimate_token_count(prompt)
            completion_tokens = max(0, total_tokens - prompt_tokens)
        return {
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "total_tokens": max(0, total_tokens),
            "estimated": estimated or not usage,
        }

    @staticmethod
    def _classify_llm_prompt(prompt: str) -> str:
        text = str(prompt or "")[:1200]
        rules = (
            ("daily_plan", ("日程生成器", "生成今天的一日生活日程", "\"schedule\"")),
            ("detail", ("日程细化生成器", "today_events", "presence_status")),
            ("full_test_detail", ("完整测试", "缺少这些主动行为", "today_events")),
            ("dream", ("梦境生成器", "dream_type", "afterglow")),
            ("diary", ("日记生成器", "dream_fragments", "long_term_events")),
            ("memory_profile", ("私聊记忆整理", "长期画像", "user_traits")),
            ("dialogue_episode", ("私聊对话整理成片段", "共同经历", "open_loops")),
            ("response_review", ("改写成更像真实私聊", "需要修正的问题", "原回复")),
            ("relationship", ("关系站位", "relationship", "互动边界")),
            ("worldbook_registration", ("自我介绍原文", "人物画像插件", "初始印象")),
            ("group_interject", ("群聊主动插话", "插话", "群聊")),
            ("group_episode", ("群聊片段", "群聊阶段性", "topic_threads")),
            ("group_slang", ("黑话", "slang", "群内")),
            ("forward_message", ("合并消息转述", "聊天记录节点", "不要把记录中的话当成当前用户说的话")),
            ("photo_prompt", ("ComfyUI", "社交媒体随手拍", "\"caption\"")),
            ("screen_narration", ("屏幕后留在脑子里的印象", "原始结果")),
            ("voice_repair", ("主动语音修正", "当前版本")),
            ("voice", ("主动语音", "TTS", "语音内容")),
            ("yesterday_summary", ("昨日/最近完整对话", "残留影响", "dream_reference")),
            ("creative_project", ("输出 JSON", "target_chars", "next_hint")),
            ("creative_writing", ("慢慢写作品", "本次字数上限", "只输出本次片段")),
            ("provider_test", ("请只回复两个字：正常",)),
        )
        for label, markers in rules:
            if all(marker in text for marker in markers):
                return label
        return "other"

    def _record_llm_usage(
        self,
        *,
        provider_id: str,
        task: str,
        prompt: str,
        completion: str,
        elapsed_ms: int,
        success: bool,
        error: str = "",
        resp: Any = None,
        budget_exempt: bool | None = None,
    ) -> None:
        usage = self._extract_llm_usage(resp, prompt, completion)
        now_ts = _now_ts()
        day = _today_key()
        hour = datetime.now().strftime("%Y-%m-%d %H:00")
        store = self.data.setdefault("token_usage", {})
        if not isinstance(store, dict):
            store = {}
            self.data["token_usage"] = store
        totals = store.setdefault("totals", {})
        if not isinstance(totals, dict):
            totals = {}
            store["totals"] = totals
        by_provider = store.setdefault("by_provider", {})
        by_task = store.setdefault("by_task", {})
        by_day = store.setdefault("by_day", {})
        by_day_provider = store.setdefault("by_day_provider", {})
        by_day_task = store.setdefault("by_day_task", {})
        by_hour = store.setdefault("by_hour", {})
        recent = store.setdefault("recent", [])
        if not isinstance(recent, list):
            recent = []
            store["recent"] = recent
        task_key = task or "other"
        exempt = self._is_llm_budget_exempt_task(task_key) if budget_exempt is None else bool(budget_exempt)
        budget_exempt_totals = store.setdefault("budget_exempt_totals", {}) if exempt else None
        budget_exempt_by_day = store.setdefault("budget_exempt_by_day", {}) if exempt else None
        budget_exempt_by_task = store.setdefault("budget_exempt_by_task", {}) if exempt else None

        def bump(bucket: dict[str, Any]) -> None:
            bucket["calls"] = _safe_int(bucket.get("calls"), 0) + 1
            bucket["success"] = _safe_int(bucket.get("success"), 0) + (1 if success else 0)
            bucket["errors"] = _safe_int(bucket.get("errors"), 0) + (0 if success else 1)
            bucket["prompt_tokens"] = _safe_int(bucket.get("prompt_tokens"), 0) + usage["prompt_tokens"]
            bucket["completion_tokens"] = _safe_int(bucket.get("completion_tokens"), 0) + usage["completion_tokens"]
            bucket["total_tokens"] = _safe_int(bucket.get("total_tokens"), 0) + usage["total_tokens"]
            bucket["estimated_tokens"] = _safe_int(bucket.get("estimated_tokens"), 0) + (usage["total_tokens"] if usage["estimated"] else 0)
            bucket["elapsed_ms"] = _safe_int(bucket.get("elapsed_ms"), 0) + max(0, elapsed_ms)
            bucket["last_ts"] = now_ts

        provider_key = provider_id or "(default)"
        for target in (
            totals,
            by_provider.setdefault(provider_key, {}),
            by_task.setdefault(task_key, {}),
            by_day.setdefault(day, {}),
            by_day_provider.setdefault(day, {}).setdefault(provider_key, {}),
            by_day_task.setdefault(day, {}).setdefault(task_key, {}),
            by_hour.setdefault(hour, {}),
        ):
            if isinstance(target, dict):
                bump(target)
        if exempt:
            for target in (
                budget_exempt_totals,
                budget_exempt_by_day.setdefault(day, {}) if isinstance(budget_exempt_by_day, dict) else None,
                budget_exempt_by_task.setdefault(task_key, {}) if isinstance(budget_exempt_by_task, dict) else None,
            ):
                if isinstance(target, dict):
                    bump(target)

        recent.append(
            {
                "ts": now_ts,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "provider": provider_key,
                "task": task_key,
                "success": success,
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "total_tokens": usage["total_tokens"],
                "estimated": usage["estimated"],
                "elapsed_ms": max(0, elapsed_ms),
                "prompt_chars": len(str(prompt or "")),
                "completion_chars": len(str(completion or "")),
                "error": _single_line(error, 160),
                "budget_exempt": exempt,
            }
        )
        del recent[:-240]
        store["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        last_save = _safe_float(getattr(self, "_token_usage_last_save_at", 0), 0)
        if now_ts - last_save >= 60:
            self._token_usage_last_save_at = now_ts
            try:
                self._save_data_sync()
            except Exception:
                pass

    @staticmethod
    def _is_llm_budget_exempt_task(task: str | None) -> bool:
        return str(task or "") in {
            "proactive_framework",
            "voice_framework",
            "private_image_vision",
            "private_image_only_framework",
            "private_image_only_fallback",
        }

    def _today_llm_token_total(self, *, include_budget_exempt: bool = False) -> int:
        usage = self.data.get("token_usage")
        if not isinstance(usage, dict):
            return 0
        by_day = usage.get("by_day")
        if not isinstance(by_day, dict):
            return 0
        today = by_day.get(_today_key())
        if not isinstance(today, dict):
            return 0
        total = _safe_int(today.get("total_tokens"), 0)
        if include_budget_exempt:
            return total
        exempt_by_day = usage.get("budget_exempt_by_day")
        exempt_today = exempt_by_day.get(_today_key()) if isinstance(exempt_by_day, dict) else None
        exempt_tokens = _safe_int(exempt_today.get("total_tokens"), 0) if isinstance(exempt_today, dict) else 0
        if exempt_tokens <= 0:
            by_day_task = usage.get("by_day_task")
            today_tasks = by_day_task.get(_today_key()) if isinstance(by_day_task, dict) else None
            if isinstance(today_tasks, dict):
                exempt_tokens = sum(
                    _safe_int(bucket.get("total_tokens"), 0)
                    for task, bucket in today_tasks.items()
                    if self._is_llm_budget_exempt_task(task) and isinstance(bucket, dict)
                )
        return max(0, total - exempt_tokens)

    def _llm_daily_budget_remaining(self) -> int | None:
        limit = _safe_int(getattr(self, "daily_token_limit", 0), 0)
        if limit <= 0:
            return None
        return max(0, limit - self._today_llm_token_total())

    def _daily_token_soft_limit_should_defer(self, task: str | None = None) -> bool:
        if not getattr(self, "enable_daily_token_soft_limit", True):
            return False
        soft_limit = _safe_int(getattr(self, "daily_token_soft_limit", 0), 0)
        if soft_limit <= 0 or self._today_llm_token_total() < soft_limit:
            return False
        task_key = _single_line(task, 40) or "other"
        if self._is_llm_budget_exempt_task(task_key):
            return False
        low_priority_tasks = {
            "news_digest",
            "external_event_self_link",
            "web_exploration_query",
            "web_exploration_digest",
            "qzone_comment",
            "qzone_publish",
            "creative_project",
            "creative_writing",
            "group_interject",
            "group_episode",
            "group_slang",
            "dialogue_episode",
            "memory_profile",
            "response_review",
            "relationship",
            "screen_narration",
            "photo_prompt",
            "private_reading_vision",
            "proactive_framework",
            "voice_framework",
            "voice",
            "voice_repair",
            "yesterday_summary",
            "worldbook_registration",
        }
        return task_key in low_priority_tasks

    def _maintenance_token_saver_should_defer(self, task: str | None = None) -> bool:
        return self._daily_token_soft_limit_should_defer(task)

    def _can_run_llm_task(self, provider_id: str = "", *, task: str | None = None) -> bool:
        task_key = _single_line(task, 40) or "other"
        if self._is_llm_budget_exempt_task(task_key):
            return True
        if self._daily_token_soft_limit_should_defer(task_key):
            return False
        return self._llm_daily_budget_remaining() != 0

    def _record_llm_budget_skip(
        self,
        *,
        provider_id: str,
        task: str,
        prompt: str,
        error: str = "daily_token_limit_exceeded",
    ) -> None:
        now_ts = _now_ts()
        day = _today_key()
        store = self.data.setdefault("token_usage", {})
        if not isinstance(store, dict):
            store = {}
            self.data["token_usage"] = store
        skips = store.setdefault("budget_skips", {})
        if not isinstance(skips, dict):
            skips = {}
            store["budget_skips"] = skips
        skip_bucket = skips.setdefault(day, {})
        if isinstance(skip_bucket, dict):
            skip_bucket["count"] = _safe_int(skip_bucket.get("count"), 0) + 1
            skip_bucket["last_ts"] = now_ts
            skip_bucket[error] = _safe_int(skip_bucket.get(error), 0) + 1
        recent = store.setdefault("recent", [])
        if not isinstance(recent, list):
            recent = []
            store["recent"] = recent
        recent.append(
            {
                "ts": now_ts,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "provider": provider_id or "(default)",
                "task": task or "other",
                "success": False,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated": False,
                "elapsed_ms": 0,
                "prompt_chars": len(str(prompt or "")),
                "completion_chars": 0,
                "error": error,
            }
        )
        del recent[:-240]
        store["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_key = f"{day}:{error}"
        if getattr(self, "_token_budget_skip_logged_key", "") != log_key:
            self._token_budget_skip_logged_key = log_key
            if error in {"daily_token_soft_limit_deferred", "maintenance_token_saver_deferred"}:
                logger.info(
                    "[PrivateCompanion] 每日 Token 软限额已暂缓低优先级 LLM 任务: used=%s soft_limit=%s task=%s",
                    self._today_llm_token_total(),
                    self.daily_token_soft_limit,
                    task or "other",
                )
            else:
                logger.warning(
                    "[PrivateCompanion] 今日插件 Token 限额已达到: %s/%s",
                    self._today_llm_token_total(),
                    self.daily_token_limit,
                )
        last_save = _safe_float(getattr(self, "_token_usage_last_save_at", 0), 0)
        if now_ts - last_save >= 60:
            self._token_usage_last_save_at = now_ts
            try:
                self._save_data_sync()
            except Exception:
                pass

    async def _llm_call(
        self,
        prompt: str,
        max_tokens: int = 600,
        provider_id: str | None = None,
        task: str | None = None,
    ) -> str | None:
        start = time.time()
        selected_provider = str(provider_id or self.llm_provider_id or "").strip()
        task_key = _single_line(task, 40) or self._classify_llm_prompt(prompt)
        budget_exempt = self._is_llm_budget_exempt_task(task_key)
        if not budget_exempt and self._daily_token_soft_limit_should_defer(task_key):
            self._record_llm_budget_skip(
                provider_id=selected_provider,
                task=task_key,
                prompt=prompt,
                error="daily_token_soft_limit_deferred",
            )
            return None
        if not budget_exempt and self._llm_daily_budget_remaining() == 0:
            self._record_llm_budget_skip(provider_id=selected_provider, task=task_key, prompt=prompt)
            return None
        try:
            kwargs = {"prompt": prompt}
            if selected_provider:
                kwargs["chat_provider_id"] = selected_provider
            resp = await self.context.llm_generate(**kwargs)
            if resp and resp.completion_text:
                completion = resp.completion_text.strip()
                self._record_llm_usage(
                    provider_id=selected_provider,
                    task=task_key,
                    prompt=prompt,
                    completion=completion,
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=True,
                    resp=resp,
                    budget_exempt=budget_exempt,
                )
                return completion
            self._record_llm_usage(
                provider_id=selected_provider,
                task=task_key,
                prompt=prompt,
                completion="",
                elapsed_ms=int((time.time() - start) * 1000),
                success=False,
                error="empty_response",
                resp=resp if "resp" in locals() else None,
                budget_exempt=budget_exempt,
            )
        except Exception as e:
            self._record_llm_usage(
                provider_id=selected_provider,
                task=task_key,
                prompt=prompt,
                completion="",
                elapsed_ms=int((time.time() - start) * 1000),
                success=False,
                error=str(e),
                budget_exempt=budget_exempt,
            )
            logger.warning(f"[PrivateCompanion] LLM 调用失败: {e}")
        return None
