"""Transcription interface — the one swap point.

`transcribe(image_bytes) -> StructuredDoc`. Provider precedence: OpenAI (gpt-4o)
> Hugging Face (Qwen2.5-VL) > offline stub. Callers never change.

Rules the model must follow:
- Transcribe prose faithfully; preserve bullets, numbering, indentation.
- Do NOT redraw diagrams/arrows/flows. For a non-prose region, return a
  `diagram_region` block with a bbox so the UI keeps the original crop as image.
- A section heading that reads like a task -> a todo with today's date.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

import httpx

from . import config

BlockType = Literal["text", "diagram_region"]


@dataclass
class Block:
    type: BlockType
    content: str = ""  # markdown for text blocks
    bbox: Optional[list[int]] = None  # [x, y, w, h] for diagram_region


@dataclass
class Todo:
    text: str
    date: str


@dataclass
class StructuredDoc:
    title: str
    folder_suggestion: str
    tags: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    todos: list[Todo] = field(default_factory=list)


_PROMPT = (
    "You are transcribing a photo of handwriting on a desk/whiteboard. The photo "
    "may be rotated; read it in whatever orientation the text runs.\n"
    "Return ONLY JSON with keys: title (3-5 words), folder_suggestion, "
    "tags (array), blocks (array), todos (array).\n"
    "- Transcribe VERBATIM into blocks of type 'text' with markdown content. Do "
    "NOT paraphrase, summarize, correct, or rephrase — copy the words exactly as "
    "written, even if terse or abbreviated. Preserve bullets and indentation.\n"
    "- Capture EVERY list and line, including numbered lists (1. 2. 3.) and text "
    "in the margins or corners. Do not drop or merge lines.\n"
    "- For any region that is a diagram/arrows/flowchart/table-sketch rather "
    "than words, DO NOT transcribe it. Emit a block of type 'diagram_region' "
    "with bbox [x, y, w, h] as FRACTIONS of the image (0 to 1): x,y = top-left "
    "corner, w,h = width,height. Make the box generous — include the whole "
    "diagram with a little margin, never clip it.\n"
    "- Todos: add a line as a todo when it is a task — under a heading like "
    "'TODO'/'Tasks'/'To-do'/'Action items', a checkbox, or a clear action. "
    "CRITICAL — a task list is all-or-nothing: if you add ANY item of a numbered "
    "or bulleted list as a todo, you MUST add EVERY sibling item in that same "
    "list too (all of 1, 2, 3 — never only the first and skip the rest). "
    "Plain notes and descriptions are NOT todos. If unsure, do not add it. "
    f"Todos are {{text, date}} with date {date.today().isoformat()}.\n"
    "Output JSON only, no prose."
)


class NoNotesFound(Exception):
    """Model returned no usable JSON — e.g. a refusal or a photo with no text."""


def _parse(raw: str) -> StructuredDoc:
    """Tolerant JSON parse — models often wrap JSON in prose/fences."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise NoNotesFound(raw[:200])
    data = json.loads(raw[start : end + 1])
    return StructuredDoc(
        title=data.get("title", "Untitled"),
        folder_suggestion=data.get("folder_suggestion", "Misc"),
        tags=list(data.get("tags", [])),
        blocks=[
            Block(
                type=b.get("type", "text"),
                content=b.get("content", ""),
                bbox=b.get("bbox"),
            )
            for b in data.get("blocks", [])
        ],
        todos=[
            # model sometimes returns date:"" — fall back to today
            Todo(text=t["text"], date=t.get("date") or date.today().isoformat())
            for t in data.get("todos", [])
        ],
    )


def _stub(image_bytes: bytes) -> StructuredDoc:
    """No provider key set -> deterministic placeholder so the app runs offline."""
    today = date.today().isoformat()
    return StructuredDoc(
        title="Sample capture (stub)",
        folder_suggestion="Misc",
        tags=["stub"],
        blocks=[
            Block(
                type="text",
                content="• Set OPENAI_API_KEY (or HF_TOKEN) to enable real transcription\n• This is stub output",
            ),
            Block(type="diagram_region", bbox=[0, 0, 100, 60]),
        ],
        todos=[Todo(text="Add OPENAI_API_KEY to backend/.env", date=today)],
    )


_MERMAID_PROMPT = (
    "Convert the ENTIRE hand-drawn flowchart in this image into Mermaid syntax. "
    "The image may be rotated — read it in whatever orientation the text runs.\n"
    "Requirements:\n"
    "- Include EVERY box/node and EVERY arrow. Follow arrow directions exactly.\n"
    "- Capture decision points (diamonds) and ALL their branches, with the label "
    "written on or near each arrow (e.g. |Bad Bowling|, |Good Bowling|).\n"
    "- Do NOT omit any node or connection, even if the handwriting is messy or "
    "nodes are spread far apart. Trace every line to where it ends.\n"
    "- Use [] for boxes and {} for decision diamonds.\n"
    "Output ONLY mermaid code starting with 'flowchart TD' or 'flowchart LR' — "
    "no code fences, no prose."
)


def _post(url: str, key: str, model: str, image_bytes: bytes, prompt: str) -> str:
    """POST an image + prompt to any OpenAI-compatible vision chat endpoint,
    return the raw text reply. OpenAI and the HF router both speak this."""
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": model,
        "max_tokens": 1500,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
    }
    resp = httpx.post(
        url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=120
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _provider() -> tuple[str, str, str]:
    """(url, key, model) for the active provider. Raises if none configured."""
    if config.OPENAI_API_KEY:
        return (
            "https://api.openai.com/v1/chat/completions",
            config.OPENAI_API_KEY,
            config.OPENAI_MODEL,
        )
    if config.HF_TOKEN:
        return (
            "https://router.huggingface.co/v1/chat/completions",
            config.HF_TOKEN,
            config.HF_MODEL,
        )
    raise RuntimeError("No transcription provider configured")


def transcribe(image_bytes: bytes) -> StructuredDoc:
    """Photo -> StructuredDoc. Provider precedence: OpenAI -> HF -> offline stub."""
    if not (config.OPENAI_API_KEY or config.HF_TOKEN):
        return _stub(image_bytes)
    url, key, model = _provider()
    return _parse(_post(url, key, model, image_bytes, _PROMPT))


def to_mermaid(image_bytes: bytes) -> str:
    """Diagram image -> Mermaid flowchart code (opt-in, non-destructive)."""
    url, key, model = _provider()
    code = _post(url, key, model, image_bytes, _MERMAID_PROMPT).strip()
    # strip ``` fences if the model added them despite instructions
    if code.startswith("```"):
        code = code.split("```")[1].replace("mermaid", "", 1).strip()
    return code
