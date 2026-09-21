"""Phase -1 spike: send one real photo to Qwen2.5-VL, print the StructuredDoc.

    HF_TOKEN=hf_xxx uv run python spike.py path/to/photo.jpg

Decides whether Qwen gives usable JSON (one call) or we fall back to TrOCR + a
separate structuring step. Prints raw parsed result — paste into the phase note.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

from app import transcribe


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: uv run python spike.py <photo>")
    image_bytes = Path(sys.argv[1]).read_bytes()
    doc = transcribe.transcribe(image_bytes)
    print(json.dumps(asdict(doc), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
