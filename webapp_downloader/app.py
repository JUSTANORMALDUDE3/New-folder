import os
import re
import sys
import uuid
import threading
import concurrent.futures
from flask import Flask, request, jsonify, render_template

import urllib3
from curl_cffi import requests as cffi_requests
from miyuki.video_downloader import VideoDownloader
from miyuki.config import VIDEO_M3U8_PREFIX, VIDEO_PLAYLIST_SUFFIX, MOVIE_SAVE_PATH_ROOT

# Disable insecure request warnings when bypassing SSL verification
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# In-memory progress tracking dict
progress_store = {}

# Folder to save output files
DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

def download_segment(scraper, url, index, max_retries=3):
    """Downloads a single video segment with retries."""
    for attempt in range(max_retries):
        try:
            resp = scraper.get(url, timeout=15)
            if resp.status_code == 200:
                return index, resp.content
        except Exception:
            pass
    return index, None

def get_scraper():
    """Create a curl_cffi session to bypass Cloudflare protection."""
    return cffi_requests.Session(impersonate="chrome")

def perform_download(download_id, url):
    """Background thread function to process the download without blocking HTTP requests."""
    progress_store[download_id] = {
        "status": "Starting metadata extraction...",
        "progress": 0,
        "file_name": None,
        "error": False
    }

    try:
        if not re.match(r'^https?://(www\.)?missav\.(com|ws|ai)/.*$', url):
            progress_store[download_id]["status"] = "Invalid MissAV URL provided."
            progress_store[download_id]["error"] = True
            return

        scraper = get_scraper()
        options = {}
        dl = VideoDownloader(url, scraper, options)

        # Temporary dummy directory requirement for miyuki
        if not os.path.exists(MOVIE_SAVE_PATH_ROOT):
            os.makedirs(MOVIE_SAVE_PATH_ROOT)

        if not dl._fetch_metadata():
            progress_store[download_id]["status"] = "Video not found or failed to fetch metadata."
            progress_store[download_id]["error"] = True
            return

        title = dl.title or dl.movie_name
        progress_store[download_id]["status"] = "Extracting highest quality stream..."

        playlist_url = f"{VIDEO_M3U8_PREFIX}{dl.uuid}{VIDEO_PLAYLIST_SUFFIX}"
        playlist_resp = scraper.get(playlist_url)
        if playlist_resp.status_code != 200:
            progress_store[download_id]["status"] = "Network failure while fetching video playlist."
            progress_store[download_id]["error"] = True
            return

        final_quality, resolution_url = dl._get_final_quality_and_resolution(playlist_resp.text)
        if not final_quality:
            progress_store[download_id]["status"] = "Could not determine available video quality stream."
            progress_store[download_id]["error"] = True
            return

        video_m3u8_url = f"{VIDEO_M3U8_PREFIX}{dl.uuid}/{resolution_url}"
        video_m3u8_resp = scraper.get(video_m3u8_url)
        if video_m3u8_resp.status_code != 200:
            progress_store[download_id]["status"] = "Network failure fetching video segments."
            progress_store[download_id]["error"] = True
            return

        # Detect total segments
        video_offset_max = -1
        # It's an M3U8 payload
        for line in reversed(video_m3u8_resp.text.strip().splitlines()):
            if line.endswith('.jpeg'):
                match = re.search(r'video(\d+)\.jpeg', line.strip())
                if match:
                    video_offset_max = int(match.group(1))
                    break

        if video_offset_max == -1:
            progress_store[download_id]["status"] = "Could not parse video stream segments."
            progress_store[download_id]["error"] = True
            return

        num_segments = video_offset_max + 1
        
        # File size safety limit (~2.5GB for Render free tier memory safety)
        if num_segments > 5000:
            progress_store[download_id]["status"] = "Video too large to process on this server instance."
            progress_store[download_id]["error"] = True
            return

        safe_title = re.sub(r'[<>:"/\\|?*\s]+', '_', title).strip('_')
        output_filename = os.path.join(DOWNLOAD_DIR, f"{safe_title}.mp4")

        progress_store[download_id]["file_name"] = f"{safe_title}.mp4"
        progress_store[download_id]["status"] = "Downloading chunks..."

        segments_content = {}
        downloaded_count = 0

        # Concurrent downloading
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            future_to_index = {
                executor.submit(
                    download_segment, 
                    scraper, 
                    f"https://surrit.com/{dl.uuid}/{resolution_url.split('/')[0]}/video{i}.jpeg", 
                    i
                ): i for i in range(num_segments)
            }

            for future in concurrent.futures.as_completed(future_to_index):
                index, content = future.result()
                if content:
                    segments_content[index] = content
                
                downloaded_count += 1
                progress = int((downloaded_count / num_segments) * 100)
                progress_store[download_id]["progress"] = progress

        progress_store[download_id]["status"] = "Merging file segments to mp4..."

        with open(output_filename, 'wb') as outfile:
            for i in range(num_segments):
                if i in segments_content:
                    outfile.write(segments_content[i])

        progress_store[download_id]["status"] = "Download Complete!"
        progress_store[download_id]["progress"] = 100

    except Exception as e:
        progress_store[download_id]["status"] = f"Error during download: {str(e)}"
        progress_store[download_id]["error"] = True


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download', methods=['POST'])
def start_download():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    download_id = str(uuid.uuid4())
    
    # Run the scraping operation safely in a new thread
    thread = threading.Thread(target=perform_download, args=(download_id, url))
    thread.start()

    return jsonify({"download_id": download_id})


@app.route('/progress/<download_id>')
def get_progress(download_id):
    info = progress_store.get(download_id)
    if not info:
        return jsonify({"error": "Invalid download ID"}), 404
    return jsonify(info)


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # Flask development server binding
    app.run(host='0.0.0.0', port=port)
