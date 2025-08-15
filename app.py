# app.py
import os
import re
import shutil
import tempfile
from flask import Flask, request, send_file, render_template, abort, after_this_request
from flask_cors import CORS
from yt_dlp import YoutubeDL
from werkzeug.utils import secure_filename

app = Flask(__name__)
# Agar front-end alag host hoga (Netlify/GitHub Pages), CORS on rakho:
CORS(app)  # same-origin ho to bhi rehne do; problem nahi banega

# -- Helpers -----------------------------------------------------------------

def quality_to_height(selected: str) -> int | None:
    """
    "144p (Low)" -> 144, "Auto-detect quality" -> None, "4K (Ultra HD)" -> 2160
    """
    if not selected:
        return None
    s = selected.lower()
    if "auto" in s:
        return None
    if "4k" in s:
        return 2160
    m = re.search(r"(\d+)\s*p", s)
    return int(m.group(1)) if m else None

def audio_quality_kbps(selected: str) -> str:
    """
    "320 kbps (High Quality)" -> "320"
    """
    if not selected:
        return "192"
    m = re.search(r"(\d+)\s*kbps", selected.lower())
    return m.group(1) if m else "192"

def build_format_selector(fmt: str, quality: str) -> str:
    """
    yt-dlp format selector string based on mp4/mp3 + quality
    """
    if fmt == "mp3":
        # backend bestaudio pick; conversion handled by postprocessor
        return "bestaudio/best"
    # video (mp4) path
    h = quality_to_height(quality)
    if h:
        # pick best video up to given height + best audio, fallback to single file
        return f"bv*[height<={h}]+ba/b[height<={h}]"
    # auto
    return "bv*+ba/best"

# -- Routes ------------------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    """
    Agar tum 'templates/index.html' me apna UI rakhte ho to ye serve karega.
    (Agar tum front-end alag host kar rahe ho to is route ki zaroorat nahi.)
    """
    # comment kar sakte ho agar templates use nahi kar rahe
    return render_template("index.html")

@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}

@app.route("/download", methods=["GET"])
def download():
    """
    Front-end se GET ke saath call karo:
    /download?url=...&format=mp4|mp3&quality=...
    Ye response me direct downloadable file bhejta hai.
    """
    video_url = request.args.get("url", "").strip()
    fmt = (request.args.get("format", "mp4") or "mp4").lower()
    quality = request.args.get("quality", "")  # UI se aane wala label

    if not video_url:
        abort(400, "Missing url")

    # temp dir for this request
    tmpdir = tempfile.mkdtemp(prefix="dl_")

    # cleanup after response is sent
    @after_this_request
    def cleanup(response):
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
        return response

    # Build yt-dlp options
    fmt_selector = build_format_selector(fmt, quality)

    ydl_opts = {
        "noplaylist": True,
        "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
    }

    if fmt == "mp3":
        # Needs ffmpeg available on PATH
        ydl_opts.update({
            "format": fmt_selector,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": audio_quality_kbps(quality),  # "64/128/192/320"
            }],
        })
    else:
        # Video path -> merge to mp4
        ydl_opts.update({
            "format": fmt_selector,
            "merge_output_format": "mp4",
        })

    # Capture pre file list
    before = set(os.listdir(tmpdir))

    # Download
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
    except Exception as e:
        # Common causes: invalid URL, geo-blocked, rate-limit, FFmpeg missing (for mp3)
        abort(500, f"Download failed: {e}")

    # Find new file(s)
    after = set(os.listdir(tmpdir))
    new_files = list(after - before)
    if not new_files:
        # Sometimes files overwrite names; fallback to pick the largest file
        files = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)]
        if not files:
            abort(500, "Download produced no file")
        target_path = max(files, key=lambda p: os.path.getsize(p))
    else:
        # Prefer final container by extension
        candidates = sorted(new_files)
        # Try to prefer mp3/mp4 if present
        preferred_order = ["mp3", "mp4", "m4a", "webm"]
        target_path = None
        for ext in preferred_order:
            for f in candidates:
                if f.lower().endswith(f".{ext}"):
                    target_path = os.path.join(tmpdir, f)
                    break
            if target_path:
                break
        if not target_path:
            # take the largest among new files
            target_path = max(
                (os.path.join(tmpdir, f) for f in new_files),
                key=lambda p: os.path.getsize(p)
            )

    # Make a safe download name
    base = os.path.basename(target_path)
    safe_name = secure_filename(base)
    # Friendly rename based on fmt (optional)
    if fmt == "mp3" and not safe_name.lower().endswith(".mp3"):
        safe_name = os.path.splitext(safe_name)[0] + ".mp3"
    if fmt == "mp4" and not safe_name.lower().endswith(".mp4"):
        safe_name = os.path.splitext(safe_name)[0] + ".mp4"

    return send_file(
        target_path,
        as_attachment=True,
        download_name=safe_name
    )

if __name__ == "__main__":
    # 0.0.0.0 for container/cloud; debug=False in production
    app.run(host="0.0.0.0", port=5000, debug=True)
