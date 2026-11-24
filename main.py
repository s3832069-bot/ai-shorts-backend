import os
import json
import uuid
import subprocess
from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
import yt_dlp
import imageio_ffmpeg

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("outputs", exist_ok=True)
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

@app.get("/")
def home():
    return {"status": "AI Shorts Maker Backend is Running!"}

@app.post("/generate")
async def generate_shorts(youtube_url: str = Form(...)):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY missing on server")

    job_id = str(uuid.uuid4())[:8]
    video_path = f"/tmp/{job_id}.mp4"

    try:
        # 1. YouTube से वीडियो डाउनलोड करें (तेज़ और हल्की क्वालिटी में ताकि फ्री सर्वर पर क्रैश न हो)
        ydl_opts = {
            "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
            "outtmpl": video_path,
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            title = info.get("title", "Video")

        # 2. Gemini AI को वीडियो भेजकर बेस्ट शॉर्ट्स के टाइमस्टैम्प निकालें
        client = genai.Client(api_key=api_key)
        uploaded_file = client.files.upload(file=video_path)

        prompt = (
            "Watch and listen to this video carefully. Identify the 3 most engaging, viral, "
            "or impactful moments suitable for YouTube Shorts or Instagram Reels (each 15 to 45 seconds long). "
            "Return ONLY a valid JSON array of objects with keys: 'title', 'start' (in seconds, integer), and 'end' (in seconds, integer). "
            "Example: [{\"title\": \"Best Hook\", \"start\": 12, \"end\": 40}]"
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

        clips_data = json.loads(response.text)
        generated_clips = []

        # 3. FFmpeg से हर क्लिप को काटकर 9:16 (Vertical Shorts) में बदलें
        for idx, clip in enumerate(clips_data[:3]):
            start = int(clip["start"])
            end = int(clip["end"])
            duration = max(5, min(60, end - start))
            out_name = f"{job_id}_short_{idx+1}.mp4"
            out_path = os.path.join("outputs", out_name)

            cmd = [
                FFMPEG_PATH, "-y",
                "-ss", str(start),
                "-i", video_path,
                "-t", str(duration),
                "-vf", "crop=ih*9/16:ih,scale=720:1280",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                "-c:a", "aac", "-b:a", "128k",
                out_path
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            generated_clips.append({
                "title": clip.get("title", f"Short Clip {idx+1}"),
                "start": start,
                "end": start + duration,
                "url": f"/outputs/{out_name}"
            })

        # टेम्पररी फाइल साफ करें
        if os.path.exists(video_path):
            os.remove(video_path)

        return {"video_title": title, "clips": generated_clips}

    except Exception as e:
        if os.path.exists(video_path):
            os.remove(video_path)
        raise HTTPException(status_code=500, detail=str(e))
