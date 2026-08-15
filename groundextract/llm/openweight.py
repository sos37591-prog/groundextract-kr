"""Open-weight LLM extractor served by a local Ollama instance.

``OllamaExtractor`` implements the ``Extractor`` protocol by POSTing the
document text to Ollama's ``/api/chat`` endpoint (default model
``qwen2.5:7b``) and asking, per field, for ``{"value": ..., "quote": ...}``
where ``quote`` MUST be copied verbatim from the document. The deterministic
gate then verifies that claim — the model is never trusted on its own.

Standard library only (``urllib``): no new runtime dependency. The only
external requirement is a running Ollama server (``ollama serve``) with the
model pulled (``ollama pull qwen2.5:7b``). Tests monkeypatch urllib, so the
suite runs offline.

Model licensing
---------------
The default is ``qwen2.5:7b``, which is **Apache-2.0** — an OSI-approved
license with no field-of-use restriction. This is deliberate: within the
Qwen2.5 family the **3B and 72B sizes are released under the Qwen-Research
License, which is non-commercial** and therefore not OSI-approved. The other
sizes (0.5B / 1.5B / 7B / 14B / 32B) are Apache-2.0.

  Apache-2.0:  https://huggingface.co/Qwen/Qwen2.5-7B-Instruct
               https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct
  qwen-research (AVOID): https://huggingface.co/Qwen/Qwen2.5-3B-Instruct

7B is also the recommended *minimum* for real use. ``qwen2.5:1.5b`` is
Apache-2.0 and fine for smoke-testing the plumbing, but measured on the
``bench/verify_llm_only.py`` invoice it returns label-only quotes
(``"공급가액"`` instead of ``"공급가액  1,000,000원"``), so the gate correctly
discards every value — useful as a demo of the gate, useless as an extractor.

Any model tag can be passed to ``OllamaExtractor``, but keep the shipped
defaults on Apache-2.0 sizes so the project stays redistributable under
Apache-2.0.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..grounding import normalize_number_str
from ..models import ExtractedValue

DEFAULT_HOST = "http://localhost:11434"
# Apache-2.0 size. Do NOT default to qwen2.5:3b / :72b — those are
# Qwen-Research (non-commercial). See the module docstring.
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_TIMEOUT = 120.0

# Per-doc_type field specs: name -> human description fed into the prompt.
# Mirrors the rule packs (rules/tax_invoice.yaml, statement.yaml, balance_sheet.yaml).
FIELD_SPECS: dict[str, dict[str, str]] = {
    "tax_invoice": {
        "supply": "공급가액 (supply value, 세액 제외 금액)",
        "vat": "세액 (부가가치세)",
        "total": "합계금액 (공급가액 + 세액)",
    },
    "statement": {
        "debit_total": "차변 합계 (total debit)",
        "credit_total": "대변 합계 (total credit)",
    },
    "balance_sheet": {
        "current_assets": "유동자산",
        "noncurrent_assets": "비유동자산",
        "total_assets": "자산총계 (유동자산 + 비유동자산)",
        "total_liabilities": "부채총계",
        "total_equity": "자본총계",
        "total_liab_equity": "부채와자본총계 (부채총계 + 자본총계)",
    },
}

_SYSTEM_PROMPT = (
    "You are a precise data-extraction engine for Korean financial documents. "
    "You only ever answer with a single JSON object and nothing else."
)


def _parse_number(raw: str) -> float | None:
    """Best-effort numeric parse of a model-emitted string ("1,000,000원" -> 1000000.0)."""
    key = normalize_number_str(raw)
    if key is None:
        return None
    try:
        return float(key)
    except ValueError:  # pragma: no cover - normalize_number_str output is float-safe
        return None


class OllamaExtractor:
    """``Extractor`` backed by an Ollama-served open-weight model.

    Parameters
    ----------
    model:   Ollama model tag (default ``qwen2.5:7b``, Apache-2.0; use
             ``qwen2.5:1.5b`` for a lighter Apache-2.0 option).
    host:    base URL of the Ollama server (default ``http://localhost:11434``).
    timeout: socket timeout in seconds for the HTTP call.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    # --- Extractor protocol ----------------------------------------------------

    def extract(self, doc_text: str, *, doc_type: str) -> list[ExtractedValue]:
        fields = FIELD_SPECS.get(doc_type)
        if fields is None:
            known = ", ".join(sorted(FIELD_SPECS))
            raise ValueError(f"unknown doc_type {doc_type!r} (known: {known})")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": self._build_prompt(doc_text, fields)},
            ],
            "stream": False,
            "format": "json",  # constrain Ollama to emit valid JSON
            "options": {"temperature": 0},
        }
        content = self._chat(payload)
        return self._parse_content(content, fields)

    # --- prompt ----------------------------------------------------------------

    def _build_prompt(self, doc_text: str, fields: dict[str, str]) -> str:
        field_lines = "\n".join(f"- {name}: {desc}" for name, desc in fields.items())
        schema = ", ".join(f'"{name}": {{"value": "...", "quote": "..."}}' for name in fields)
        return (
            "다음 문서 텍스트에서 아래 필드 값을 추출해 JSON 객체 하나로만 답하라.\n\n"
            f"추출할 필드:\n{field_lines}\n\n"
            f"출력 형식(JSON): {{{schema}}}\n\n"
            "규칙:\n"
            '- "value"는 문서에 적힌 금액 문자열을 그대로 쓴다 (예: "1,000,000원").\n'
            '- "quote"는 그 값이 등장하는 부분을 문서 원문에서 한 글자도 바꾸지 말고 '
            "그대로 복사한다 (요약·재작성 금지).\n"
            "- 문서에 없는 필드는 결과 JSON에서 생략한다. 값을 추측하거나 지어내지 않는다.\n\n"
            "--- 문서 시작 ---\n"
            f"{doc_text}\n"
            "--- 문서 끝 ---"
        )

    # --- HTTP ------------------------------------------------------------------

    def _chat(self, payload: dict[str, Any]) -> str:
        """POST to /api/chat and return the assistant message content string."""
        url = f"{self.host}/api/chat"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # Server reachable but rejected the call (e.g. model not pulled).
            try:
                detail = exc.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001 - diagnostics only
                detail = ""
            raise RuntimeError(f"Ollama returned HTTP {exc.code} for {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ConnectionError(
                f"Cannot reach Ollama server at {self.host} ({reason}). "
                f"Start it with `ollama serve` and pull the model with "
                f"`ollama pull {self.model}`."
            ) from exc

        try:
            envelope = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ollama response body is not valid JSON: {body[:200]!r}") from exc
        message = envelope.get("message") if isinstance(envelope, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"Ollama response has no message content: {body[:200]!r}")
        return content

    # --- response parsing ------------------------------------------------------

    def _parse_content(self, content: str, fields: dict[str, str]) -> list[ExtractedValue]:
        """Parse the model's JSON answer into ``ExtractedValue``s (spec order)."""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"model did not return valid JSON (content starts: {content[:200]!r})"
            ) from exc
        # Tolerate a {"fields": {...}} wrapper some models like to emit.
        if isinstance(data, dict) and isinstance(data.get("fields"), dict):
            data = data["fields"]
        if not isinstance(data, dict):
            raise ValueError(f"model JSON is not an object: got {type(data).__name__}")

        values: list[ExtractedValue] = []
        for name in fields:
            raw, quote = self._unpack_entry(data.get(name))
            if raw is None:
                continue  # field omitted by the model (absent from the document)
            values.append(
                ExtractedValue(
                    field=name,
                    raw=raw,
                    number=_parse_number(raw),
                    grounding_quote=quote,
                )
            )
        return values

    @staticmethod
    def _unpack_entry(entry: Any) -> tuple[str | None, str | None]:
        """Return (raw, quote) from one field entry, or (None, None) to skip it."""
        if isinstance(entry, dict):
            value = entry.get("value")
            if value is None or isinstance(value, bool):
                return None, None
            raw = value if isinstance(value, str) else str(value)
            quote = entry.get("quote")
            clean_quote = quote if isinstance(quote, str) and quote.strip() else None
            return (raw.strip() or None), clean_quote
        # Tolerate a bare scalar (model skipped the {value, quote} envelope):
        # keep the value but leave the quote empty so grounding falls back to full text.
        if isinstance(entry, str | int | float) and not isinstance(entry, bool):
            raw = entry if isinstance(entry, str) else str(entry)
            return (raw.strip() or None), None
        return None, None
