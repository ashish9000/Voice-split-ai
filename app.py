"""
Voice Split AI — FastAPI Backend
Deploy on: Hugging Face Spaces (FREE) / Render / Railway
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import subprocess, os, uuid, shutil, json, time
from pathlib import Path

app = FastAPI(title="Voice Split AI API", version="2.0")

# Allow your PWA domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production: replace with your PWA URL
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("/tmp/voicesplit")
UPLOAD_DIR.mkdir(exist_ok=True)

SUPPORTED = {".mp3",".wav",".m4a",".aac",".mp4",".mov",".mkv",".webm"}

# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "ok", "app": "Voice Split AI", "version": "2.0"}

@app.get("/health")
def health():
    # Check if required tools are available
    tools = {}
    for tool in ["ffmpeg", "python3"]:
        tools[tool] = shutil.which(tool) is not None
    return {"status": "ok", "tools": tools}

# ─────────────────────────────────────────────
# UPLOAD & PROCESS  (main endpoint)
# ─────────────────────────────────────────────
@app.post("/process")
async def process_audio(file: UploadFile = File(...)):
    """
    Main endpoint:
    1. Extract audio from video if needed (FFmpeg)
    2. Separate vocals from background (Demucs)
    3. Apply noise reduction (FFmpeg filters)
    4. Return JSON with download URLs
    """
    # Validate
    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED:
        raise HTTPException(400, f"Unsupported format: {ext}")

    job_id = str(uuid.uuid4())[:8]
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir()

    input_path = job_dir / f"input{ext}"

    try:
        # Save uploaded file
        content = await file.read()
        with open(input_path, "wb") as f:
            f.write(content)

        # ── STEP 1: Extract audio to WAV ──
        wav_path = job_dir / "audio.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(input_path),
            "-vn",                    # no video
            "-acodec", "pcm_s16le",   # 16-bit PCM
            "-ar", "44100",           # 44.1kHz
            "-ac", "1",               # mono
            str(wav_path)
        ], check=True, capture_output=True)

        # ── STEP 2: Demucs vocal separation ──
        demucs_out = job_dir / "demucs_out"
        subprocess.run([
            "python3", "-m", "demucs",
            "--two-stems", "vocals",   # only vocals vs no_vocals
            "--out", str(demucs_out),
            "--name", "htdemucs",      # best model for vocals
            str(wav_path)
        ], check=True, capture_output=True)

        # Demucs output structure: demucs_out/htdemucs/audio/vocals.wav
        vocals_raw = demucs_out / "htdemucs" / "audio" / "vocals.wav"
        background_raw = demucs_out / "htdemucs" / "audio" / "no_vocals.wav"

        # ── STEP 3: Noise reduction on vocals ──
        vocals_clean = job_dir / "vocals_clean.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(vocals_raw),
            "-af",
            # Aggressive noise reduction pipeline:
            "highpass=f=80,"          # remove sub-bass rumble
            "lowpass=f=8000,"         # remove high freq hiss
            "afftdn=nf=-25,"          # FFT noise reduction
            "anlmdn=s=7:p=0.002,"     # non-local means denoising
            "agate=threshold=-40dB,"  # noise gate
            "dynaudnorm=g=5",         # normalize loudness
            "-ar", "44100",
            "-ac", "1",
            str(vocals_clean)
        ], check=True, capture_output=True)

        # ── STEP 4: Convert to MP3 for download ──
        vocals_mp3 = job_dir / "vocals_clean.mp3"
        background_mp3 = job_dir / "background.mp3"

        subprocess.run([
            "ffmpeg", "-y", "-i", str(vocals_clean),
            "-codec:a", "libmp3lame", "-b:a", "192k",
            str(vocals_mp3)
        ], check=True, capture_output=True)

        subprocess.run([
            "ffmpeg", "-y", "-i", str(background_raw),
            "-codec:a", "libmp3lame", "-b:a", "128k",
            str(background_mp3)
        ], check=True, capture_output=True)

        # Get durations
        def get_duration(path):
            try:
                r = subprocess.run([
                    "ffprobe", "-v", "quiet", "-print_format", "json",
                    "-show_format", str(path)
                ], capture_output=True, text=True)
                d = json.loads(r.stdout)
                return float(d["format"]["duration"])
            except:
                return 0.0

        vocal_dur = get_duration(vocals_mp3)
        bg_dur = get_duration(background_mp3)

        return JSONResponse({
            "job_id": job_id,
            "status": "success",
            "tracks": {
                "vocals": {
                    "url": f"/download/{job_id}/vocals_clean.mp3",
                    "wav_url": f"/download/{job_id}/vocals_clean.wav",
                    "duration": round(vocal_dur, 2),
                    "label": "Clean Human Voice",
                    "noise_reduced": True
                },
                "background": {
                    "url": f"/download/{job_id}/background.mp3",
                    "duration": round(bg_dur, 2),
                    "label": "Background / Noise"
                }
            },
            "original_file": file.filename,
            "processing_time": "done"
        })

    except subprocess.CalledProcessError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, f"Processing failed: {e.stderr.decode() if e.stderr else str(e)}")
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────
# DOWNLOAD ENDPOINT
# ─────────────────────────────────────────────
@app.get("/download/{job_id}/{filename}")
def download_file(job_id: str, filename: str):
    path = UPLOAD_DIR / job_id / filename
    if not path.exists():
        raise HTTPException(404, "File not found or expired")
    media_type = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
    return FileResponse(
        str(path),
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ─────────────────────────────────────────────
# CLEANUP — delete jobs older than 1 hour
# ─────────────────────────────────────────────
@app.delete("/cleanup/{job_id}")
def cleanup_job(job_id: str):
    job_dir = UPLOAD_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir)
    return {"deleted": job_id}
