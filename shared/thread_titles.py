"""Utility helpers for formatting Discord thread titles."""

from __future__ import annotations

import discord


def format_thread_title(wo_data: dict, worker: discord.Member | None = None) -> str:
    """Build a work-order thread title following the shared rules."""
    status = wo_data.get("Status")
    title = wo_data.get("Title", "Work Order")[:80]
    total_sec = int(float(wo_data.get("TotalTimeSeconds", 0)))
    hours, remainder = divmod(total_sec, 3600)
    minutes, _ = divmod(remainder, 60)
    total_time_str = f"{hours:02}:{minutes:02}"

    if status == "InProgress":
        name = worker.name if worker else "Working"
        return f"⏱️ (@{name}) {title}"
    if status == "Approved":
        return f"✅ ({total_time_str}) {title}"
    if status in {"Open", "Rework"}:
        return f"({total_time_str}) {title}"
    if status == "InQA":
        return f"QA ➡️ ({total_time_str}) {title}"

    # Cancelled, Completed without approval, or any other status
    return f"({total_time_str}) {title}"
