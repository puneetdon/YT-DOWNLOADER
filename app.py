from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import yt_dlp
import os
import threading
import json
from datetime import datetime
from pathlib import Path

app = Flask(__name__, static_folder=".")
CORS(app)

# Default download folder
DEFAULT_DOWNLOAD_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads", "YT-Downloads")
os.makedirs(DEFAULT_DOWNLOAD_FOLDER, exist_ok=True)

# Config file
CONFIG_FILE = os.path.join(DEFAULT_DOWNLOAD_FOLDER, ".ytdown_config.json")
HISTORY_FILE = os.path.join(DEFAULT_DOWNLOAD_FOLDER, ".ytdown_history.json")

progress_store = {}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "download_folder": DEFAULT_DOWNLOAD_FOLDER,
        "theme": "dark",
        "default_quality": "720p"
    }

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

def add_to_history(title, url, quality, file_path, file_size):
    history = load_history()
    entry = {
        "title": title,
        "url": url,
        "quality": quality,
        "file_path": file_path,
        "file_size": file_size,
        "timestamp": datetime.now().isoformat(),
        "date_display": datetime.now().strftime("%b %d, %H:%M")
    }
    history.insert(0, entry)
    history = history[:100]  # Keep last 100
    save_history(history)

def download_video(url, task_id, quality, config):
    download_folder = config.get("download_folder", DEFAULT_DOWNLOAD_FOLDER)
    os.makedirs(download_folder, exist_ok=True)

    def progress_hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total else 0
            speed = d.get("_speed_str", "N/A")
            eta = d.get("_eta_str", "N/A")
            progress_store[task_id] = {
                "status": "downloading",
                "percent": round(percent, 1),
                "speed": speed,
                "eta": eta,
                "filename": d.get("filename", "")
            }
        elif d["status"] == "finished":
            progress_store[task_id] = {
                "status": "processing",
                "percent": 100,
                "speed": "-",
                "eta": "0s",
                "filename": d.get("filename", "")
            }

    format_map = {
        "best": "bestvideo+bestaudio/best",
        "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
        "audio": "bestaudio/best",
    }

    ydl_opts = {
        "format": format_map.get(quality, "bestvideo+bestaudio/best"),
        "outtmpl": os.path.join(download_folder, "%(title)s.%(ext)s"),
        "progress_hooks": [progress_hook],
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
    }

    if quality == "audio":
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Unknown")
            filename = ydl.prepare_filename(info)
            file_size = 0
            if os.path.exists(filename):
                file_size = os.path.getsize(filename)
            
            add_to_history(title, url, quality, filename, file_size)
            
            progress_store[task_id] = {
                "status": "done",
                "percent": 100,
                "title": title,
                "folder": download_folder,
                "file_path": filename,
                "file_size": file_size
            }
    except Exception as e:
        progress_store[task_id] = {"status": "error", "message": str(e)}


@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/config", methods=["GET", "POST"])
def config():
    if request.method == "POST":
        data = request.json
        current_config = load_config()
        current_config.update(data)
        save_config(current_config)
        return jsonify(current_config)
    else:
        return jsonify(load_config())

@app.route("/history")
def history():
    return jsonify(load_history())

@app.route("/stats")
def stats():
    history = load_history()
    total_downloads = len(history)
    total_size = sum(h.get("file_size", 0) for h in history)
    
    def format_size(bytes):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes < 1024:
                return f"{bytes:.1f} {unit}"
            bytes /= 1024
        return f"{bytes:.1f} TB"
    
    return jsonify({
        "total_downloads": total_downloads,
        "total_size": format_size(total_size),
        "total_size_bytes": total_size
    })

@app.route("/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "")
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Get available formats
            formats = []
            if info.get("formats"):
                seen = set()
                for fmt in info["formats"]:
                    height = fmt.get("height")
                    if height and height not in seen:
                        seen.add(height)
                        formats.append(f"{height}p")
            
            return jsonify({
                "title": info.get("title"),
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration_string") or str(info.get("duration", "")),
                "uploader": info.get("uploader"),
                "view_count": f'{info.get("view_count", 0):,}',
                "is_playlist": "playlist" in url,
                "formats": sorted(set(formats), reverse=True)[:5]
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "")
    quality = data.get("quality", "best")
    config = load_config()
    task_id = f"task_{len(progress_store) + 1}"
    progress_store[task_id] = {"status": "starting", "percent": 0}
    thread = threading.Thread(target=download_video, args=(url, task_id, quality, config))
    thread.daemon = True
    thread.start()
    return jsonify({"task_id": task_id})

@app.route("/progress/<task_id>")
def get_progress(task_id):
    return jsonify(progress_store.get(task_id, {"status": "not_found"}))

@app.route("/clear-history", methods=["POST"])
def clear_history():
    save_history([])
    return jsonify({"success": True})

if __name__ == "__main__":
    print(f"\n✅ YTDown running at http://localhost:5055")
    print(f"📁 Videos saved to: {load_config()['download_folder']}\n")
    app.run(port=5055, debug=False)
