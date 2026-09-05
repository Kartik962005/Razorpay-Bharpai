"""Outbound messaging, priced.

Recovery is only real if it is worth more than it cost, so every message carries its channel
price and lands in a delivery log. In simulation nothing is actually sent; in live mode the
gateway's own SMS and email notifications carry the message.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from bharpai.core.models import Channel, Language, Message, Tone


class Messenger:
    """Records what was sent, to whom, on which channel, at what cost."""

    def __init__(self, costs: dict[str, int], path: Path | str | None = None):
        self.costs = costs
        self.path = Path(path) if path else None
        self.sent: list[Message] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def cost_of(self, channel: Channel) -> int:
        return int(self.costs[channel.value])

    def send(
        self,
        *,
        case_id: str,
        channel: Channel,
        to: str,
        text: str,
        language: Language,
        tone: Tone,
        now: datetime,
        template_id: str | None = None,
        llm_written: bool = False,
    ) -> Message:
        message = Message(
            case_id=case_id,
            channel=channel,
            to=to,
            text=text,
            language=language,
            tone=tone,
            sent_at=now,
            cost_paise=self.cost_of(channel),
            template_id=template_id,
            llm_written=llm_written,
        )
        self.sent.append(message)
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(message.model_dump_json() + "\n")
        return message

    def total_cost_paise(self) -> int:
        return sum(m.cost_paise for m in self.sent)

    def stats(self) -> dict[str, Any]:
        by_channel: dict[str, int] = {}
        for message in self.sent:
            by_channel[message.channel.value] = by_channel.get(message.channel.value, 0) + 1
        return {
            "messages": len(self.sent),
            "by_channel": by_channel,
            "cost_paise": self.total_cost_paise(),
            "llm_written": sum(1 for m in self.sent if m.llm_written),
        }
