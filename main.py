import os, glob, shutil, secrets
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, HTTPException, APIRouter, Depends, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from pypdf import PdfReader
from google import genai
from google.genai import types
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

MODEL = "gemini-3.5-flash-lite"

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
app = FastAPI()

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

SYSTEM = ("Answer only from the documents provided. Quote the supporting "
          "passage and name the document it came from. If the answer is "
          "not in the documents, say: I couldn't find this in the documents.")


def read_pdf(path):
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)

def read_docx(path):
    d = Document(path)
    out = []
    for child in d.element.body.iterchildren():
        if child.tag.endswith("}p"):
            out.append(Paragraph(child, d).text)
        elif child.tag.endswith("}tbl"):
            for row in Table(child, d).rows:
                out.append(" | ".join(c.text for c in row.cells))
    return "\n".join(out)

def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

READERS = {
    ".pdf": read_pdf,
    ".docx": read_docx,
    ".md": read_text,
    ".txt": read_text,
}


def load_docs():
    parts = []
    for path in sorted(glob.glob("docs/**/*", recursive=True)):
        if not os.path.isfile(path):
            continue
        name = os.path.relpath(path, "docs").replace(os.sep, "/")
        ext = os.path.splitext(name)[1].lower()
        reader = READERS.get(ext)
        if reader is None:
            print(f"Skipping unsupported file: {name}")
            continue
        try:
            text = reader(path)
        except Exception as e:
            print(f"Could not read {name}: {e}")
            continue
        if not text.strip():
            print(f"Warning: no text found in {name}")
            continue
        print(f"Loaded {name} ({len(text)} characters)")
        parts.append(f'<document name="{name}">\n{text}\n</document>')
    return "\n\n".join(parts)

DOCS = load_docs()

def reload_docs():
    global DOCS
    DOCS = load_docs()


# ---------- public API: chat ----------

class Question(BaseModel):
    question: str

@app.get("/")
def home():
    return FileResponse("static/index.html")

@app.post("/ask")
def ask(body: Question):
    try:
        r = client.models.generate_content(
            model=MODEL,
            contents=f"{DOCS}\n\nQuestion: {body.question[:1000]}",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                max_output_tokens=2000,
            ),
        )
        return {"answer": r.text or "No answer returned. Try rephrasing."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- admin only: knowledge base ----------

security = HTTPBasic()

def require_admin(creds: HTTPBasicCredentials = Depends(security)):
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "Admin access is not configured. Set ADMIN_PASSWORD in .env")
    user_ok = secrets.compare_digest(creds.username.encode(), ADMIN_USER.encode())
    pass_ok = secrets.compare_digest(creds.password.encode(), ADMIN_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(401, "Incorrect username or password",
                            headers={"WWW-Authenticate": "Basic"})

admin = APIRouter(dependencies=[Depends(require_admin)])

ROOT = Path("docs").resolve()
ROOT.mkdir(exist_ok=True)

def safe(rel: str) -> Path:
    p = (ROOT / rel.lstrip("/\\")).resolve()
    if p != ROOT and ROOT not in p.parents:
        raise HTTPException(400, "Invalid path")
    return p

def tree(d: Path):
    out = []
    for c in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if c.name.startswith("."):
            continue
        rel = c.relative_to(ROOT).as_posix()
        if c.is_dir():
            out.append({"name": c.name, "path": rel, "type": "dir", "children": tree(c)})
        else:
            out.append({"name": c.name, "path": rel, "type": "file", "size": c.stat().st_size})
    return out

class Save(BaseModel):
    path: str
    content: str

class Folder(BaseModel):
    path: str

@admin.get("/knowledge")
def knowledge():
    return FileResponse("static/knowledge.html")

@admin.get("/kb/tree")
def kb_tree():
    return tree(ROOT)

@admin.get("/kb/file")
def kb_file(path: str):
    p = safe(path)
    if not p.is_file():
        raise HTTPException(404, "File not found")
    ext = p.suffix.lower()
    if ext not in READERS:
        raise HTTPException(415, "Unsupported file type")
    return {"path": path, "editable": ext in (".md", ".txt"), "content": READERS[ext](str(p))}

@admin.put("/kb/file")
def kb_save(b: Save):
    p = safe(b.path)
    if p.suffix.lower() not in (".md", ".txt"):
        raise HTTPException(400, "Only .md and .txt files can be edited")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(b.content, encoding="utf-8")
    reload_docs()
    return {"ok": True}

@admin.post("/kb/folder")
def kb_folder(b: Folder):
    safe(b.path).mkdir(parents=True, exist_ok=True)
    return {"ok": True}

@admin.post("/kb/upload")
async def kb_upload(folder: str = "", file: UploadFile = File(...)):
    dest = safe(f"{folder}/{os.path.basename(file.filename)}")
    if dest.suffix.lower() not in READERS:
        raise HTTPException(415, "Unsupported file type")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    reload_docs()
    return {"ok": True}

@admin.delete("/kb/item")
def kb_delete(path: str):
    p = safe(path)
    if p == ROOT:
        raise HTTPException(400, "Cannot delete the root folder")
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    reload_docs()
    return {"ok": True}

app.include_router(admin)