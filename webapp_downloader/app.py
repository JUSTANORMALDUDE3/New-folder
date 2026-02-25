import os
import re
import uuid
import threading
from flask import Flask, request, jsonify, render_template, Response

import urllib3
from curl_cffi import requests as cffi_requests
from miyuki.video_downloader import VideoDownloader
from miyuki.config import VIDEO_M3U8_PREFIX, VIDEO_PLAYLIST_SUFFIX, MOVIE_SAVE_PATH_ROOT

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# In-memory store for prepared download metadata (not the video itself)
prepared_downloads = {}


def get_scraper():
    """Create a curl_cffi session to bypass Cloudflare protection."""
    return cffi_requests.Session(impersonate="chrome")


def download_segment(scraper, url, max_retries=3):
    """Downloads a single video segment with retries. Returns bytes or None."""
    for _ in range(max_retries):
        try:
            resp = scraper.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.content
        except Exception:
            pass
    return None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/prepare', methods=['POST'])
def prepare_download():
    """
    Extracts video metadata (title, UUID, quality, segment count).
    Returns JSON with a download_id the client uses to start streaming.
    No video data is stored on the server.
    """
    data = request.json
    url = data.get('url', '').strip()

    if not re.match(r'^https?://(www\.)?missav\.(com|ws|ai)/.*$', url):
        return jsonify({"error": "Invalid MissAV URL."}), 400

    try:
        scraper = get_scraper()
        options = {}
        dl = VideoDownloader(url, scraper, options)

        if not os.path.exists(MOVIE_SAVE_PATH_ROOT):
            os.makedirs(MOVIE_SAVE_PATH_ROOT)

        if not dl._fetch_metadata():
            return jsonify({"error": "Video not found or metadata extraction failed."}), 404

        title = dl.title or dl.movie_name

        playlist_url = f"{VIDEO_M3U8_PREFIX}{dl.uuid}{VIDEO_PLAYLIST_SUFFIX}"
        playlist_resp = scraper.get(playlist_url)
        if playlist_resp.status_code != 200:
            return jsonify({"error": "Failed to fetch video playlist."}), 502

        final_quality, resolution_url = dl._get_final_quality_and_resolution(playlist_resp.text)
        if not final_quality:
            return jsonify({"error": "Could not determine video quality."}), 500

        video_m3u8_url = f"{VIDEO_M3U8_PREFIX}{dl.uuid}/{resolution_url}"
        video_m3u8_resp = scraper.get(video_m3u8_url)
        if video_m3u8_resp.status_code != 200:
            return jsonify({"error": "Failed to fetch video segment list."}), 502

        video_offset_max = -1
        for line in reversed(video_m3u8_resp.text.strip().splitlines()):
            if line.endswith('.jpeg'):
                match = re.search(r'video(\d+)\.jpeg', line.strip())
                if match:
                    video_offset_max = int(match.group(1))
                    break

        if video_offset_max == -1:
            return jsonify({"error": "Could not parse video segments."}), 500

        num_segments = video_offset_max + 1
        safe_title = re.sub(r'[<>:"/\\|?*\s]+', '_', title).strip('_')
        resolution_prefix = resolution_url.split('/')[0]

        # Estimate total size by probing the first segment with HEAD request
        estimated_size_str = "Unknown"
        try:
            sample_url = f"https://surrit.com/{dl.uuid}/{resolution_prefix}/video0.jpeg"
            head_resp = scraper.get(sample_url, timeout=8)
            content_length = int(head_resp.headers.get("Content-Length", 0))
            if content_length == 0:
                # HEAD not supported — use actual content length
                content_length = len(head_resp.content)
            if content_length > 0:
                total_bytes = content_length * num_segments
                if total_bytes >= 1_073_741_824:
                    estimated_size_str = f"~{total_bytes / 1_073_741_824:.1f} GB"
                elif total_bytes >= 1_048_576:
                    estimated_size_str = f"~{total_bytes / 1_048_576:.0f} MB"
                else:
                    estimated_size_str = f"~{total_bytes / 1024:.0f} KB"
        except Exception:
            pass

        download_id = str(uuid.uuid4())
        prepared_downloads[download_id] = {
            "uuid": dl.uuid,
            "resolution_prefix": resolution_prefix,
            "num_segments": num_segments,
            "file_name": f"{safe_title}.mp4",
            "quality": final_quality,
        }

        return jsonify({
            "download_id": download_id,
            "file_name": f"{safe_title}.mp4",
            "quality": final_quality,
            "num_segments": num_segments,
            "estimated_size": estimated_size_str,
        })

    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@app.route('/stream/<download_id>')
def stream_download(download_id):
    """
    Streams the video directly to the user's browser segment by segment.
    Nothing is saved on the server — the data flows straight through.
    """
    info = prepared_downloads.pop(download_id, None)
    if not info:
        return jsonify({"error": "Invalid or expired download ID."}), 404

    video_uuid = info["uuid"]
    resolution_prefix = info["resolution_prefix"]
    num_segments = info["num_segments"]
    file_name = info["file_name"]

    scraper = get_scraper()

    def generate():
        for i in range(num_segments):
            try:
                segment_url = f"https://surrit.com/{video_uuid}/{resolution_prefix}/video{i}.jpeg"
                content = download_segment(scraper, segment_url)
                if content:
                    yield content
            except GeneratorExit:
                return
            except Exception:
                continue

    response = Response(
        generate(),
        mimetype='application/octet-stream',
        headers={
            'Content-Disposition': f'attachment; filename="{file_name}"',
            'X-Total-Segments': str(num_segments),
            'Access-Control-Expose-Headers': 'X-Total-Segments',
            'Transfer-Encoding': 'chunked',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )
    response.timeout = None
    return response


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
