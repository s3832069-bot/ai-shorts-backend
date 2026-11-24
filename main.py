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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("downloads", exist_ok=True)
app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads")

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

@app.post("/generate")
async def generate_shorts(youtube_url: str = Form(...)):
    job_id = str(uuid.uuid4())[:8]
    output_dir = os.path.join("downloads", job_id)
    os.makedirs(output_dir, exist_ok=True)
    
    video_path = os.path.join(output_dir, "input.mp4")
    
    # YouTube bot-detection bypass options
    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': video_path,
        'socket_timeout': 30,
        'extractor-args': 'youtube:player_client=android',
        'geo_bypass': True,
        'nocheckcertificate': True
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            video_title = info.get('title', 'YouTube Video')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"YouTube Download Error: {str(e)}")
    
    # Fallback if extension varies
    actual_video_path = video_path
    if not os.path.exists(actual_video_path):
        for f in os.listdir(output_dir):
            if f.startswith("input"):
                actual_video_path = os.path.join(output_dir, f)
                break
                
    if not os.path.exists(actual_video_path):
        raise HTTPException(status_code=500, detail="Downloaded video file not found.")

    # Get video duration using ffmpeg
    try:
        probe_cmd = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-i", actual_video_path
        ]
        result = subprocess.run(probe_cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        duration = 60.0 # default fallback
        for line in result.stderr.split("\n"):
            if "Duration:" in line:
                parts = line.split("Duration:")[1].split(",")[0].strip().split(":")
                duration = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except Exception:
        duration = 60.0

    # Ask Gemini to find viral highlights
    prompt = f"""
    Analyze this YouTube video titled "{video_title}" with total duration {duration} seconds.
    Identify 3 most engaging, viral-worthy segments suitable for Shorts/Reels (each 15 to 45 seconds long).
    Provide the start time and end time in seconds, and a catchy title for each clip.
    Return ONLY valid JSON in this exact structure without any markdown formatting or extra text:
    [
      {{"start": 10, "end": 40, "title": "Catchy Title 1"}},
      {{"start": 60, "end": 95, "title": "Catchy Title 2"}},
      {{"start": 120, "end": 155, "title": "Catchy Title 3"}}
    ]
    """

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        text_response = response.text.strip()
        if text_response.startswith("```json"):
            text_response = text_response[7:-3].strip()
        elif text_response.startswith("```"):
            text_response = text_response[3:-3].strip()
            
        clips_data = json.loads(text_response)
    except Exception:
        # Fallback default timestamps if AI fails
        clip_len = min(30, int(duration / 4))
        clips_data = [
            {"start": clip_len, "end": clip_len * 2, "title": "Highlight 1"},
            {"start": clip_len * 2, "end": clip_len * 3, "title": "Highlight 2"},
            {"start": clip_len * 3, "end": min(int(duration), clip_len * 4), "title": "Highlight 3"}
        ]

    processed_clips = []
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    for i, clip in enumerate(clips_data):
        start = float(clip["start"])
        end = float(clip["end"])
        duration_clip = max(5, end - start)
        output_clip_filename = f"short_{i+1}.mp4"
        output_clip_path = os.path.join(output_dir, output_clip_filename)

        # FFmpeg filter to crop center 9:16 for vertical reels
        vf_filter = "crop=ih*(9/16):ih,scale=1080:1920"

        cmd = [
            ffmpeg_exe,
            "-y",
            "-ss", str(start),
            "-i", actual_video_path,
            "-t", str(duration_clip),
            "-vf", vf_filter,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            output_clip_path
        ]

        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processed_clips.endswith() # dummy
            processed_clips.append({
                "title": clip["title"],
                "start": int(start),
                "end": int(end),
                "url": f"/downloads/{job_id}/{output_clip_filename}"
            })
        except Exception:
            continue

    if not processed_clips:
        raise HTTPException(status_code=500, detail="Failed to crop video clips.")

    return {
        "video_title": video_title,
        "clips": processed_clips
}
