# -*- coding: utf-8 -*-
"""
ProactiveMixin — 从 main.py 重新拆分出的主动消息调度
"""
from __future__ import annotations

import asyncio
import base64
import gc
import hashlib
import html
import importlib
import json
import math
import os
import random
import re
import shutil
import sys
import time
import unicodedata
import uuid
import zoneinfo
from copy import deepcopy
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
try:
    from astrbot.api.message_components import At, Image, Plain, Record, Reply
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
from astrbot.core.db.po import Conversation
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

try:
    import chinese_calendar as calendar_cn
except Exception:
    calendar_cn = None

try:
    from lunarcalendar import Converter, Solar
except Exception:
    Converter = None
    Solar = None

from .constants import (
    DEFAULT_DAILY_PLAN_ITEMS,
    DEFAULT_HUMANIZED_STATE,
    PLUGIN_NAME,
    DATA_VERSION,
    PROACTIVE_ABILITY_REGISTRY,
    STYLE_TEMPLATES,
    VOICE_FALLBACK_TEMPLATES,
    TIMER_TAG_PATTERN,
    SUPPORTED_TIMER_FORMATS,
    _ACTION_TEXT,
    _DATA_STORE_KEYS,
    _DEFAULT_GROUP_TEMPLATE,
    _DEFAULT_USER_TEMPLATE,
    _REASON_TEXT,
    _SIMULATION_FALLBACK_EVENTS,
)
from .dreaming import (
    build_dream_memory_fragments,
    dream_fragment_effective_weight,
    dream_theme_specs,
    extract_weighted_dream_fragments,
    fallback_diary_payload,
    fallback_dream_fragments_for_diary,
    generate_daily_diary,
    generate_enhanced_dream_pick,
    merge_dream_fragment_pool,
    normalize_dream_fragment_item,
    normalize_dream_fragment_pool,
    recent_diary_context,
    recent_diary_tags,
    weighted_unique_fragment_sample,
)
from .helpers import _date_key, _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _today_key
from .planning import (
    build_daily_plan_prompt,
    build_detail_enhancement_prompt,
    format_plan_for_diary,
    generate_daily_plan,
    generate_detail_enhancement,
    get_schedule_planning_prompt,
    normalize_long_term_events,
    normalize_story_items,
    normalize_story_plan,
    pick_detail_segment,
)

DEFAULT_AI_DAILY_NEWS_SOURCE = "B站 AI早报|bilibili:285286947"

DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
        DEFAULT_AI_DAILY_NEWS_SOURCE,
    ]
)

LEGACY_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
    ]
)

PREVIOUS_TECH_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
    ]
)



_LUNAR_MONTH_NAMES = [
    "正月",
    "二月",
    "三月",
    "四月",
    "五月",
    "六月",
    "七月",
    "八月",
    "九月",
    "十月",
    "冬月",
    "腊月",
]
_LUNAR_DAY_NAMES = [
    "初一",
    "初二",
    "初三",
    "初四",
    "初五",
    "初六",
    "初七",
    "初八",
    "初九",
    "初十",
    "十一",
    "十二",
    "十三",
    "十四",
    "十五",
    "十六",
    "十七",
    "十八",
    "十九",
    "二十",
    "廿一",
    "廿二",
    "廿三",
    "廿四",
    "廿五",
    "廿六",
    "廿七",
    "廿八",
    "廿九",
    "三十",
]
_SOLAR_TERM_DATES = {
    (1, 5): "小寒",
    (1, 20): "大寒",
    (2, 4): "立春",
    (2, 19): "雨水",
    (3, 5): "惊蛰",
    (3, 20): "春分",
    (4, 4): "清明",
    (4, 20): "谷雨",
    (5, 5): "立夏",
    (5, 21): "小满",
    (6, 5): "芒种",
    (6, 21): "夏至",
    (7, 7): "小暑",
    (7, 22): "大暑",
    (8, 7): "立秋",
    (8, 23): "处暑",
    (9, 7): "白露",
    (9, 23): "秋分",
    (10, 8): "寒露",
    (10, 23): "霜降",
    (11, 7): "立冬",
    (11, 22): "小雪",
    (12, 7): "大雪",
    (12, 22): "冬至",
}
_ALMANAC_YI = ["整理房间", "写字", "散步", "读书", "听歌", "轻度创作", "复盘", "安静休息"]
_ALMANAC_JI = ["熬夜", "冲动发言", "硬撑", "反复纠结", "过度解释", "临时加压", "情绪化决定"]
_PLATFORM_DISPLAY_NAMES = {
    "aiocqhttp": "QQ",
    "qq": "QQ",
    "onebot": "QQ",
    "telegram": "Telegram",
    "wechat": "微信",
    "discord": "Discord",
}

class ProactiveMixin:
    """主动消息调度"""

    def _configured_target_ids(self) -> list[str]:
        raw = self.target_user_ids
        if isinstance(raw, str):
            parts = re.split(r"[,\s,、;；]+", raw)
        elif isinstance(raw, list):
            parts = raw
        else:
            parts = []
        ids = []
        for part in parts:
            user_id = str(part).strip()
            if user_id and user_id.isdigit() and user_id not in ids:
                ids.append(user_id)
        return ids

    def _sync_configured_targets(self):
        for user_id in self._configured_target_ids():
            user = self._get_user(user_id)
            user["enabled"] = True
            user["target_user"] = True
            user.setdefault("nickname", self.default_nickname)
            if not user.get("umo"):
                user["umo"] = f"{self.target_platform}:FriendMessage:{user_id}"
            if _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=_now_ts())

    def _prime_enabled_user_schedules(self) -> bool:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return False
        changed = False
        now = _now_ts()
        for raw_user in users.values():
            if not isinstance(raw_user, dict):
                continue
            raw_user_id = str(raw_user.get("user_id") or "")
            if not self._is_target_private_user(raw_user_id, raw_user):
                raw_user["enabled"] = False
                raw_user["next_proactive_at"] = 0
                continue
            if not raw_user.get("umo"):
                continue
            if _safe_float(raw_user.get("next_proactive_at"), 0) > 0:
                if self._promote_earlier_daily_greeting_event(raw_user, now=now):
                    changed = True
                continue
            self._schedule_next_proactive(raw_user, now=now)
            changed = True
        return changed

    def _is_quiet_time(self) -> bool:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", self.quiet_hours)
        if not match:
            return False
        sh, sm, eh, em = [int(part) for part in match.groups()]
        start = sh * 60 + sm
        end = eh * 60 + em
        now = datetime.now()
        current = now.hour * 60 + now.minute
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end

    def _reset_daily_counter_if_needed(self, user: dict[str, Any]):
        today = _today_key()
        if user.get("sent_day") != today:
            user["sent_day"] = today
            user["sent_today"] = 0
            user["proactive_daypart_day"] = today
            user["proactive_daypart_counts"] = {}
        if user.get("photo_generated_day") != today:
            user["photo_generated_day"] = today
            user["photo_generated_today"] = 0
        if user.get("screen_peek_day") != today:
            user["screen_peek_day"] = today
            user["screen_peek_today"] = 0
            user["screen_peek_last_at"] = 0
        if user.get("greeting_sent_day") != today:
            user["greeting_sent_day"] = today
            user["greetings_sent"] = []
            user["greetings_suppressed_by_inbound"] = []
        if user.get("proactive_daypart_day") != today:
            user["proactive_daypart_day"] = today
            user["proactive_daypart_counts"] = {}

    def _effective_min_interval_seconds(self, user: dict[str, Any]) -> int:
        ignored_streak = _safe_int(user.get("ignored_streak"), 0)
        multiplier = min(2.2, 1.0 + ignored_streak * 0.35)
        return int(self.min_interval_minutes * 60 * multiplier)

    def _soft_daily_target(self, user: dict[str, Any]) -> float:
        if self.max_daily_messages <= 0:
            return 0.0
        state = self.data.get("daily_state", {})
        important_dates = self._get_relevant_important_dates()
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        active_conditions = state.get("conditions", []) if isinstance(state, dict) else []
        ratio = 0.68
        if energy > 80:
            ratio += 0.06
        elif energy < 40:
            ratio -= 0.02
        if isinstance(active_conditions, list) and active_conditions:
            ratio += min(0.1, len(active_conditions) * 0.03)
        if important_dates:
            ratio += 0.1 if _safe_int(important_dates[0].get("_days_until"), 0) == 0 else 0.05
        ratio = max(0.45, min(0.95, ratio))
        if self.max_daily_messages == 1:
            ratio = max(ratio, 0.75)
        return max(0.6, self.max_daily_messages * ratio)

    def _daily_intensity_factor(self, user: dict[str, Any]) -> float:
        if self.max_daily_messages <= 0:
            return 0.0
        sent_today = _safe_int(user.get("sent_today"), 0)
        soft_target = self._soft_daily_target(user)
        capacity_factor = min(1.35, 0.9 + self.max_daily_messages * 0.08)
        if soft_target <= 0:
            return max(0.35, capacity_factor)
        usage = sent_today / soft_target
        if usage < 0.2:
            pressure = 1.18
        elif usage < 0.5:
            pressure = 1.03
        elif usage < 0.85:
            pressure = 0.88
        elif usage < 1.0:
            pressure = 0.72
        else:
            pressure = 0.5
        return max(0.3, min(1.4, capacity_factor * pressure))

    def _fallback_proactive_delay_hours(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[float, float]:
        now_dt = datetime.fromtimestamp(now or _now_ts())
        sent_today = _safe_int(user.get("sent_today"), 0)
        remaining_target = max(1, math.ceil(max(0.0, self._soft_daily_target(user) - sent_today)))
        counts = self._today_proactive_daypart_counts(user)
        current_bucket = self._proactive_daypart_bucket_for_minute(now_dt.hour * 60 + now_dt.minute)
        if current_bucket == "late_night" and _safe_int(counts.get("late_night"), 0, 0) >= 1:
            return (7.5, 10.5)
        if current_bucket == "evening" and _safe_int(counts.get("evening"), 0, 0) >= 1 and remaining_target <= 2:
            return (3.0, 5.0)

        if now_dt.hour < 12:
            if remaining_target >= 4:
                return (0.45, 1.4)
            if remaining_target >= 3:
                return (0.75, 2.0)
            if remaining_target >= 2:
                return (1.0, 2.8)
            return (1.6, 4.2)
        if now_dt.hour < 18:
            if remaining_target >= 3:
                return (0.55, 1.8)
            if remaining_target >= 2:
                return (0.9, 2.6)
            return (1.8, 4.5)
        if remaining_target >= 2:
            return (0.5, 1.6)
        return (0.9, 2.4)

    def _schedule_next_proactive(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        delay_hours: tuple[float, float] | None = None,
    ):
        now = now or _now_ts()
        planned_event = self._pick_best_planned_event(user, now)
        reason = (
            str(planned_event.get("reason") or "")
            if planned_event else self._choose_planned_reason()
        )
        motive = (
            _single_line(planned_event.get("motive"), 120)
            if planned_event and planned_event.get("motive")
            else ""
        )
        action = (
            str(planned_event.get("action") or "message")
            if planned_event else ""
        )
        if not action:
            if not motive:
                motive = self._choose_proactive_motive(reason, user, planned_event=planned_event)
            action = self._choose_action_for_reason(reason, user, motive=motive)
        elif not motive:
            motive = self._choose_proactive_motive(reason, user, action=action, planned_event=planned_event)
        action = self._maybe_upgrade_planned_message_action(
            action,
            reason=reason,
            user=user,
            motive=motive,
            planned_event=planned_event,
        )
        if delay_hours is None:
            delay_hours = self._fallback_proactive_delay_hours(user, now=now)
        intensity_factor = self._daily_intensity_factor(user)
        if delay_hours is not None and intensity_factor > 0:
            widen = max(0.85, min(1.8, 1.25 - intensity_factor * 0.45))
            delay_hours = (delay_hours[0] * widen, delay_hours[1] * widen)
        if planned_event:
            scheduled = self._timestamp_from_story_event(planned_event, reason)
            if scheduled <= now:
                scheduled = now + random.uniform(3, 15)
        else:
            base_time = now + random.uniform(delay_hours[0] * 3600, delay_hours[1] * 3600)
            scheduled = self._move_timestamp_into_reason_window(base_time, reason)
        timer_event = self._get_active_llm_timer(user)
        if isinstance(timer_event, dict):
            timer_scheduled = _safe_float(timer_event.get("scheduled_ts"), 0)
            if timer_scheduled > now and (scheduled <= 0 or timer_scheduled <= scheduled):
                user["next_proactive_at"] = timer_scheduled
                user["planned_proactive_reason"] = _single_line(timer_event.get("reason"), 40) or reason
                user["planned_proactive_action"] = _single_line(timer_event.get("action"), 24) or "message"
                user["planned_proactive_source"] = "timer"
                user["planned_proactive_motive"] = self._normalize_internal_motive_text(
                    _single_line(timer_event.get("motive"), 140) or motive
                )
                user["planned_proactive_topic"] = _single_line(timer_event.get("topic"), 60) or (
                    _single_line(planned_event.get("topic"), 60)
                    if isinstance(planned_event, dict)
                    else self._choose_proactive_topic(reason, user)
                )
                self._set_planned_proactive_trigger(
                    user,
                    message_id=_single_line(timer_event.get("trigger_message_id"), 120),
                    umo=_single_line(timer_event.get("trigger_umo"), 160),
                    created_at=_safe_float(timer_event.get("trigger_ts"), 0),
                )
                user["planned_event_chain"] = (
                    list(timer_event.get("chain") or [])
                    if isinstance(timer_event.get("chain"), list)
                    else []
                )
                user["planned_opener_mode"] = ""
                user["planned_followup_kind"] = ""
                user["planned_proactive_quota_exempt"] = False
                item = self._record_proactive_candidate(
                    str(user.get("user_id") or user.get("id") or ""),
                    {
                        "source": "timer",
                        "reason": user["planned_proactive_reason"],
                        "action": user["planned_proactive_action"],
                        "scheduled_ts": timer_scheduled,
                        "topic": user["planned_proactive_topic"],
                        "motive": user["planned_proactive_motive"],
                        "score": 100,
                    },
                    status="accepted",
                    note="用户预约/定时主动",
                )
                user["planned_candidate_id"] = item.get("id", "")
                return
        user["next_proactive_at"] = scheduled
        user["planned_proactive_reason"] = reason
        user["planned_proactive_action"] = action
        user["planned_proactive_source"] = "event" if planned_event else "random"
        user["planned_proactive_motive"] = motive
        user["planned_proactive_topic"] = (
            _single_line(planned_event.get("topic"), 60)
            if isinstance(planned_event, dict)
            else self._choose_proactive_topic(reason, user)
        )
        self._clear_planned_proactive_trigger(user)
        user["planned_event_chain"] = (
            list(planned_event.get("chain") or [])
            if isinstance(planned_event, dict) and isinstance(planned_event.get("chain"), list)
            else []
        )
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = (
            "suspended_opener"
            if isinstance(planned_event, dict) and planned_event.get("_opener_followup")
            else "chain_followup"
            if isinstance(planned_event, dict) and planned_event.get("_chain_followup")
            else ""
        )
        user["planned_proactive_quota_exempt"] = bool(
            isinstance(planned_event, dict) and planned_event.get("_free_screen_peek")
        )
        item = self._record_proactive_candidate(
            str(user.get("user_id") or user.get("id") or ""),
            {
                "source": user["planned_proactive_source"],
                "reason": user["planned_proactive_reason"],
                "action": user["planned_proactive_action"],
                "scheduled_ts": user["next_proactive_at"],
                "topic": user["planned_proactive_topic"],
                "motive": user["planned_proactive_motive"],
                "score": 60 if planned_event else 42,
            },
            status="accepted",
            note="日程/随机调度",
        )
        user["planned_candidate_id"] = item.get("id", "")

    def _promote_earlier_daily_greeting_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        if not self.enable_daily_greetings:
            return False
        if str(user.get("planned_proactive_source") or "") == "timer":
            return False
        current_next = _safe_float(user.get("next_proactive_at"), 0)
        if current_next <= 0:
            return False
        now = now or _now_ts()
        event = self._pick_daily_greeting_event(user, now)
        if not isinstance(event, dict):
            return False
        reason = str(event.get("reason") or "")
        if not self._is_sticky_greeting_reason(reason):
            return False
        scheduled = self._timestamp_from_story_event(event, reason)
        if scheduled <= 0 or scheduled >= current_next - 60:
            return False
        action = str(event.get("action") or "message")
        motive = _single_line(event.get("motive"), 120) or self._choose_proactive_motive(
            reason,
            user,
            action=action,
            planned_event=event,
        )
        user["next_proactive_at"] = scheduled
        user["planned_proactive_reason"] = reason
        user["planned_proactive_action"] = action
        user["planned_proactive_source"] = "event"
        user["planned_proactive_motive"] = motive
        user["planned_proactive_topic"] = _single_line(event.get("topic"), 60)
        self._clear_planned_proactive_trigger(user)
        user["planned_event_chain"] = list(event.get("chain") or []) if isinstance(event.get("chain"), list) else []
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = bool(event.get("_free_screen_peek"))
        return True

    def _is_proactive_plan_stale(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        if next_at <= 0:
            return False
        check_now = _now_ts() if now is None else now
        return check_now - next_at > self.max_proactive_plan_lag_minutes * 60

    def _clear_pending_proactive_plan(self, user: dict[str, Any]) -> None:
        user["next_proactive_at"] = 0
        user["planned_proactive_reason"] = ""
        user["planned_proactive_action"] = ""
        user["planned_proactive_source"] = ""
        user["planned_proactive_motive"] = ""
        user["planned_proactive_topic"] = ""
        user["planned_event_chain"] = []
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""

    async def _scheduler_loop(self):
        while not self._stop_event.is_set():
            try:
                timeout = self._next_scheduler_timeout()
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=timeout
                )
            except asyncio.TimeoutError:
                await self._ensure_daily_state()
                await self._ensure_daily_plan()
                await self._ensure_detail_enhancement()
                await self._ensure_current_detail_presence_status()
                await self._ensure_daily_diary()
                await self._maybe_advance_creative_projects()
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[PrivateCompanion] 主动消息循环异常: {e}", exc_info=True)

    async def _kick_proactive_loop_once(self) -> None:
        try:
            await self._ensure_daily_state()
            await self._ensure_daily_plan()
            await self._ensure_detail_enhancement()
            await self._ensure_current_detail_presence_status()
            await self._ensure_daily_diary()
            await self._maybe_advance_creative_projects()
            await self._tick()
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 主动链即时唤醒失败: {e}", exc_info=True)

    def _next_scheduler_timeout(self) -> float:
        base = max(30.0, float(self.check_interval_seconds))
        now = _now_ts()
        nearest_due_in: float | None = None
        users = self.data.get("users", {})
        if isinstance(users, dict):
            for raw_user in users.values():
                if not isinstance(raw_user, dict):
                    continue
                if not raw_user.get("umo"):
                    continue
                next_at = _safe_float(raw_user.get("next_proactive_at"), 0)
                if next_at <= 0:
                    continue
                due_in = max(0.0, next_at - now)
                if nearest_due_in is None or due_in < nearest_due_in:
                    nearest_due_in = due_in

        if nearest_due_in is None:
            detail_due_in = self._next_detail_due_in_seconds(now)
            if detail_due_in is not None:
                nearest_due_in = detail_due_in
        elif self.enable_detail_enhancement:
            detail_due_in = self._next_detail_due_in_seconds(now)
            if detail_due_in is not None and detail_due_in < nearest_due_in:
                nearest_due_in = detail_due_in

        if nearest_due_in is None:
            return max(35.0, min(base, random.uniform(base * 0.55, base * 0.95)))
        if nearest_due_in <= 20:
            return max(3.0, nearest_due_in + random.uniform(0.8, 3.2))
        if nearest_due_in <= 90:
            return max(8.0, nearest_due_in * random.uniform(0.35, 0.7))
        if nearest_due_in <= 6 * 60:
            return max(20.0, min(base * 0.5, nearest_due_in * random.uniform(0.18, 0.42)))
        return max(35.0, min(base, random.uniform(base * 0.55, base * 0.95)))

