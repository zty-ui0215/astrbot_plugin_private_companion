# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Callable


def split_plain_component_chain(
    chain: list[Any],
    *,
    plain_type: type,
    split_text: Callable[[str], list[str]],
    fallback_line_split: bool = False,
) -> list[list[Any]]:
    """Split text components while keeping media/components as atomic chunks."""
    chunks: list[list[Any]] = []
    for comp in chain or []:
        if isinstance(comp, plain_type):
            text = str(getattr(comp, "text", "") or "").strip()
            if not text:
                continue
            if fallback_line_split:
                segments = [part.strip() for part in text.splitlines() if part.strip()]
            else:
                segments = split_text(text)
            segments = [str(segment or "").strip() for segment in segments if str(segment or "").strip()] or [text]
            chunks.extend([[plain_type(segment)] for segment in segments])
            continue
        chunks.append([comp])
    return chunks


def split_plain_component_chain_detailed(
    chain: list[Any],
    *,
    plain_type: type,
    split_text: Callable[[str], list[str]],
) -> tuple[list[list[Any]], bool, bool, str]:
    chunks: list[list[Any]] = []
    changed = False
    split_changed = False
    text_parts: list[str] = []
    plain_buffer: list[str] = []

    def flush_plain_buffer() -> None:
        nonlocal changed, split_changed
        if not plain_buffer:
            return
        raw_text = "".join(plain_buffer).strip()
        plain_buffer.clear()
        if not raw_text:
            changed = True
            return
        text_parts.append(raw_text)
        segments = [str(item or "").strip() for item in split_text(raw_text) if str(item or "").strip()]
        if not segments:
            changed = True
            return
        if len(segments) != 1 or segments[0] != raw_text:
            changed = True
        if len(segments) > 1:
            split_changed = True
        for segment in segments:
            chunks.append([plain_type(segment)])

    for comp in chain or []:
        if isinstance(comp, plain_type):
            plain_buffer.append(str(getattr(comp, "text", "") or ""))
            continue
        flush_plain_buffer()
        chunks.append([comp])
    flush_plain_buffer()
    return chunks, changed, split_changed, "".join(text_parts).strip()


def flatten_component_chunks(chunks: list[list[Any]]) -> list[Any]:
    flattened: list[Any] = []
    for chunk in chunks or []:
        flattened.extend(chunk or [])
    return flattened
