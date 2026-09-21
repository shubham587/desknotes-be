"""DeskNotes API — Phase 0 shell + capture endpoint.

Run: uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from . import config, transcribe
from .auth import current_user, make_token, seed_account, verify_password
from .db import Repo, init_db

app = FastAPI(title="DeskNotes")

# Dev CORS — Vite dev server. Tighten for prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media", StaticFiles(directory=config.MEDIA_DIR), name="media")
repo = Repo()


@app.on_event("startup")
def _startup() -> None:
    init_db()
    seed_account()


# ---------- auth ----------
class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/login")
def login(body: LoginIn):
    user = repo.get_user(body.username)
    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    return {"token": make_token(body.username)}


@app.get("/me")
def me(user: str = Depends(current_user)):
    return {"username": user}


# ---------- folders / tags / docs ----------
@app.get("/folders")
def folders(user: str = Depends(current_user)):
    return repo.list_folders()


class FolderIn(BaseModel):
    name: str
    parent_id: Optional[int] = None


@app.post("/folders")
def create_folder(body: FolderIn, user: str = Depends(current_user)):
    return {"id": repo.create_folder(body.name, body.parent_id)}


@app.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, user: str = Depends(current_user)):
    """Delete a folder; its notes are kept (just unfiled)."""
    repo.delete_folder(folder_id)
    return {"ok": True}


@app.get("/tags")
def tags_list(user: str = Depends(current_user)):
    return repo.list_tags()


@app.get("/notes")
def notes(
    q: Optional[str] = None,
    folder_id: Optional[int] = None,
    tag: Optional[str] = None,
    user: str = Depends(current_user),
):
    return repo.list_docs(q=q, folder_id=folder_id, tag=tag)


class NewNote(BaseModel):
    title: str = "Untitled"
    folder_id: Optional[int] = None


@app.post("/notes")
def create_note(body: NewNote, user: str = Depends(current_user)):
    """Create a blank note to type into (no photo)."""
    doc_id = repo.create_doc(body.title, body.folder_id, [], [])
    return repo.get_doc(doc_id)


@app.get("/notes/{doc_id}")
def note(doc_id: int, user: str = Depends(current_user)):
    d = repo.get_doc(doc_id)
    if d is None:
        raise HTTPException(404, "Not found")
    return d


class NoteUpdate(BaseModel):
    title: str
    folder_id: Optional[int] = None
    font_style: str = "handwriting"
    blocks: list[dict] = []
    tags: list[str] = []


@app.put("/notes/{doc_id}")
def update_note(doc_id: int, body: NoteUpdate, user: str = Depends(current_user)):
    if repo.get_doc(doc_id) is None:
        raise HTTPException(404, "Not found")
    repo.update_doc(
        doc_id, body.title, body.folder_id, body.font_style, body.blocks, body.tags
    )
    return repo.get_doc(doc_id)


@app.delete("/notes/{doc_id}")
def delete_note(doc_id: int, user: str = Depends(current_user)):
    if repo.get_doc(doc_id) is None:
        raise HTTPException(404, "Not found")
    repo.delete_doc(doc_id)
    return {"ok": True}


class MermaidIn(BaseModel):
    image_path: str  # e.g. /media/abc.png


@app.post("/mermaid")
def diagram_to_mermaid(body: MermaidIn, user: str = Depends(current_user)):
    """Convert a stored diagram crop to Mermaid code. Opt-in, non-destructive."""
    name = Path(body.image_path).name  # avoid path traversal
    path = config.MEDIA_DIR / name
    if not path.exists():
        raise HTTPException(404, "Image not found")
    try:
        return {"mermaid": transcribe.to_mermaid(path.read_bytes())}
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            502, f"Diagram conversion failed ({e.response.status_code})."
        ) from e
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


class MoveIn(BaseModel):
    folder_id: Optional[int] = None  # null = remove from folder


@app.patch("/notes/{doc_id}/folder")
def move_note(doc_id: int, body: MoveIn, user: str = Depends(current_user)):
    if repo.get_doc(doc_id) is None:
        raise HTTPException(404, "Not found")
    repo.set_doc_folder(doc_id, body.folder_id)
    return {"ok": True}


@app.get("/todos")
def todos(user: str = Depends(current_user)):
    return repo.list_todos()


class TodoCreate(BaseModel):
    text: str
    doc_id: Optional[int] = None  # link to the note it came from (for edit-sync)


@app.post("/todos")
def add_todo(body: TodoCreate, user: str = Depends(current_user)):
    """Add a todo, dated today. Optionally linked to a source note via doc_id."""
    tid = repo.create_todo(body.text, date.today().isoformat(), body.doc_id)
    return {"id": tid}


class TodoOrder(BaseModel):
    ids: list[int]  # todo ids in the desired order


@app.put("/todos/reorder")
def reorder_todos(body: TodoOrder, user: str = Depends(current_user)):
    repo.reorder_todos(body.ids)
    return {"ok": True}


class TodoUpdate(BaseModel):
    done: Optional[bool] = None
    text: Optional[str] = None
    details: Optional[str] = None


@app.patch("/todos/{todo_id}")
def update_todo(todo_id: int, body: TodoUpdate, user: str = Depends(current_user)):
    if body.done is not None:
        repo.set_todo_done(todo_id, body.done)
    if body.text is not None:
        repo.update_todo_text(todo_id, body.text)
    if body.details is not None:
        repo.update_todo_details(todo_id, body.details)
    return {"ok": True}


@app.delete("/todos")
def clear_todos(done: Optional[bool] = None, user: str = Depends(current_user)):
    """Clear todos — all, or only done (?done=true) / only open (?done=false)."""
    repo.clear_todos(done)
    return {"ok": True}


@app.delete("/todos/{todo_id}")
def delete_todo(todo_id: int, user: str = Depends(current_user)):
    repo.delete_todo(todo_id)
    return {"ok": True}


# ---------- checklists (a named todo_list with checkable items) ----------
@app.get("/todolists")
def todolists(user: str = Depends(current_user)):
    return repo.list_todo_lists()


class TodoListCreate(BaseModel):
    name: str
    items: list[str] = []
    doc_id: Optional[int] = None


@app.post("/todolists")
def create_todolist(body: TodoListCreate, user: str = Depends(current_user)):
    lid = repo.create_todo_list(
        body.name, body.doc_id, date.today().isoformat(), body.items
    )
    return {"id": lid}


class TodoListRename(BaseModel):
    name: str


@app.patch("/todolists/{list_id}")
def rename_todolist(list_id: int, body: TodoListRename, user: str = Depends(current_user)):
    repo.rename_todo_list(list_id, body.name)
    return {"ok": True}


@app.delete("/todolists/{list_id}")
def delete_todolist(list_id: int, user: str = Depends(current_user)):
    repo.delete_todo_list(list_id)
    return {"ok": True}


class ListItemCreate(BaseModel):
    text: str


@app.post("/todolists/{list_id}/items")
def add_list_item(list_id: int, body: ListItemCreate, user: str = Depends(current_user)):
    return {"id": repo.add_list_item(list_id, body.text, date.today().isoformat())}


# ---------- capture (Phase 1) ----------
def _crop_diagram(img_path: Path, bbox: list) -> str:
    """Crop a diagram region from the original photo, save it, return media path.

    bbox may be fractions (0..1) of image size (preferred, scale-independent) or
    legacy pixels. Pads ~8% and clamps; falls back to the full image if the box
    is degenerate — better to show the whole photo than a wrong crop.
    """
    with Image.open(img_path) as im:
        W, H = im.size
        try:
            x, y, w, h = (float(v) for v in bbox)
            if max(x, y, w, h) <= 1.0:  # fractional -> scale to pixels
                x, y, w, h = x * W, y * H, w * W, h * H
            px, py = w * 0.08, h * 0.08
            left, top = max(0, int(x - px)), max(0, int(y - py))
            right, bottom = min(W, int(x + w + px)), min(H, int(y + h + py))
            region = im if right - left < 20 or bottom - top < 20 else im.crop((left, top, right, bottom))
        except (ValueError, TypeError):
            region = im  # bad bbox -> keep the whole photo
        name = f"{uuid.uuid4().hex}.png"
        region.save(config.MEDIA_DIR / name)
    return f"/media/{name}"


async def _process(files: list[UploadFile]) -> dict:
    """Transcribe photo(s) -> blocks/todos/tags/title + saved originals.
    Shared by /capture (new note) and /notes/{id}/append (add to a note)."""
    blocks: list[dict] = []
    todos: list[dict] = []
    tags: list[str] = []
    title = "Untitled"
    folder_suggestion = "Misc"
    saved_originals: list[str] = []

    for f in files:
        raw = await f.read()
        # keep the original photo (trust feature)
        orig_name = f"{uuid.uuid4().hex}_{f.filename or 'photo'}"
        orig_path = config.MEDIA_DIR / orig_name
        orig_path.write_bytes(raw)
        saved_originals.append(f"/media/{orig_name}")

        try:
            result = transcribe.transcribe(raw)
        except httpx.HTTPStatusError as e:
            provider = "OpenAI" if config.OPENAI_API_KEY else "Hugging Face"
            code = e.response.status_code
            msg = {
                402: f"{provider} credits/quota exhausted — check billing.",
                401: f"{provider} API key invalid — check backend/.env.",
                429: f"{provider} rate limit or quota hit — try again shortly.",
            }.get(code, f"{provider} transcription error ({code}).")
            raise HTTPException(502, msg) from e
        except transcribe.NoNotesFound as e:
            raise HTTPException(
                422,
                "Couldn't read notes from that photo. Point the camera at "
                "handwriting or printed text — photos of people or scenes can't "
                f"be transcribed. (model said: {str(e)[:120]})",
            ) from e
        if title == "Untitled":
            title, folder_suggestion = result.title, result.folder_suggestion
        tags.extend(result.tags)
        todos.extend([{"text": t.text, "date": t.date} for t in result.todos])
        for b in result.blocks:
            if b.type == "diagram_region" and b.bbox:
                blocks.append(
                    {
                        "type": "image",
                        "content": "",
                        "image_path": _crop_diagram(orig_path, b.bbox),
                    }
                )
            else:
                blocks.append({"type": "text", "content": b.content})

    return {
        "title": title,
        "folder_suggestion": folder_suggestion,
        "blocks": blocks,
        "todos": todos,
        "tags": tags,
        "originals": saved_originals,
    }


@app.post("/capture")
async def capture(
    files: list[UploadFile] = File(...),
    folder_id: Optional[int] = Form(None),
    user: str = Depends(current_user),
):
    """Photo(s) -> transcribe -> assemble a new saved doc. Diagrams kept as images."""
    p = await _process(files)
    doc_id = repo.create_doc(p["title"], folder_id, p["blocks"], sorted(set(p["tags"])))
    repo.add_todos(doc_id, p["todos"])
    for orig in p["originals"]:
        repo.add_media(doc_id, orig)
    return {
        "doc_id": doc_id,
        "title": p["title"],
        "folder_suggestion": p["folder_suggestion"],
        "tags": sorted(set(p["tags"])),
        "doc": repo.get_doc(doc_id),
    }


@app.post("/upload")
async def upload(file: UploadFile = File(...), user: str = Depends(current_user)):
    """Store a pasted/dropped file (e.g. an image) and return its media URL."""
    name = f"{uuid.uuid4().hex}_{file.filename or 'file'}"
    (config.MEDIA_DIR / name).write_bytes(await file.read())
    return {"url": f"/media/{name}"}


@app.post("/notes/{doc_id}/append")
async def append_photos(
    doc_id: int,
    files: list[UploadFile] = File(...),
    user: str = Depends(current_user),
):
    """Transcribe more photo(s) and append their blocks/todos/tags to an existing note."""
    existing = repo.get_doc(doc_id)
    if existing is None:
        raise HTTPException(404, "Not found")
    p = await _process(files)
    merged_blocks = existing["blocks"] + p["blocks"]
    merged_tags = sorted(set(existing["tags"]) | set(p["tags"]))
    repo.update_doc(
        doc_id,
        existing["title"],
        existing["folder_id"],
        existing["font_style"],
        merged_blocks,
        merged_tags,
    )
    repo.add_todos(doc_id, p["todos"])
    for orig in p["originals"]:
        repo.add_media(doc_id, orig)
    return repo.get_doc(doc_id)
