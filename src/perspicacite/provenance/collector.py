"""ProvenanceCollector — per-RAG-request accumulator for trace data."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RetrievalEvent:
    paper_id: str | None
    doi: str | None
    title: str | None
    score: float
    kb_name: str | None
    content_type: str | None
    pipeline_step: str | None
    rank: int
    stage_label: str


@dataclass
class LLMCallRecord:
    stage_label: str
    provider: str
    model: str
    prompt_messages: list[dict[str, Any]]
    response_text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    ts: float


@dataclass
class ProvenanceCollector:
    conversation_id: str | None
    message_id: str | None
    rag_mode: str
    request_params: dict[str, Any]
    retrieval_events: list[RetrievalEvent] = field(default_factory=list)
    mode_trace: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[LLMCallRecord] = field(default_factory=list)

    def add_retrieval(
        self,
        *,
        paper_id: str | None,
        doi: str | None,
        title: str | None,
        score: float,
        kb_name: str | None,
        content_type: str | None,
        pipeline_step: str | None,
        rank: int,
        stage_label: str,
    ) -> None:
        self.retrieval_events.append(
            RetrievalEvent(
                paper_id=paper_id,
                doi=doi,
                title=title,
                score=float(score),
                kb_name=kb_name,
                content_type=content_type,
                pipeline_step=pipeline_step,
                rank=int(rank),
                stage_label=stage_label,
            )
        )

    def add_trace(self, step: str, **detail: Any) -> None:
        self.mode_trace.append({"step": step, "detail": detail.get("detail", detail)})

    def add_llm_call(
        self,
        *,
        stage_label: str,
        provider: str,
        model: str,
        prompt_messages: list[dict[str, Any]],
        response_text: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        ts: float | None = None,
    ) -> None:
        self.llm_calls.append(
            LLMCallRecord(
                stage_label=stage_label,
                provider=provider,
                model=model,
                prompt_messages=list(prompt_messages),
                response_text=response_text,
                prompt_tokens=int(prompt_tokens or 0),
                completion_tokens=int(completion_tokens or 0),
                latency_ms=float(latency_ms),
                ts=float(ts if ts is not None else time.time()),
            )
        )

    def finalize(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "rag_mode": self.rag_mode,
            "request_params": dict(self.request_params),
            "retrieval_events": [asdict(e) for e in self.retrieval_events],
            "mode_trace": list(self.mode_trace),
            "llm_calls": [asdict(c) for c in self.llm_calls],
        }
