import os
import uuid
import subprocess
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=FILES_DIR), name="files")

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/extract")
def extract(payload: dict):
    url = payload.get("url")
    if not url:
        return JSONResponse({"error": "Missing url"}, status_code=400)

    file_id = str(uuid.uuid4())
    outtmpl = os.path.join(FILES_DIR, f"{file_id}.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", "bv*+ba/best",
        "--merge-output-format", "mp4",
        "-o", outtmpl,
        url
    ]
    subprocess.check_call(cmd)

    base = os.environ.get("PUBLIC_BASE_URL")
    if not base:
        return JSONResponse({"error": "Missing PUBLIC_BASE_URL"}, status_code=500)

    return {"fileUrl": f"{base}/files/{file_id}.mp4"}
