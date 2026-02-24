#!/usr/bin/env python3
"""
MissAV Video Downloader CLI
A command-line tool to download videos from MissAV using the miyuki library.
"""

import argparse
import sys
import re
import os
import requests
import urllib3
import concurrent.futures
from tqdm import tqdm

# Import the miyuki library for MissAV extraction
try:
    from miyuki.video_downloader import VideoDownloader
    from miyuki.config import VIDEO_M3U8_PREFIX, VIDEO_PLAYLIST_SUFFIX, MOVIE_SAVE_PATH_ROOT
except ImportError:
    print("Error: The 'miyuki' library is not installed.")
    print("Please install it using: pip install miyuki")
    sys.exit(1)

# Disable insecure request warnings when bypassing SSL verification
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


import cloudscraper
from curl_cffi import requests as cffi_requests

def download_segment(scraper, url, index, max_retries=3):
    """Downloads a single segment with retries and returns its content."""
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


def download_missav_video(url: str):
    # Requirements: Handle Invalid URL
    if not re.match(r'^https?://(www\.)?missav\.(com|ws|ai)/.*$', url):
        print("[-] Error: Invalid MissAV URL provided.")
        sys.exit(1)

    scraper = get_scraper()
    options = {}  # Empty options dictionary required by miyuki
    dl = VideoDownloader(url, scraper, options)

    print(f"[*] Extracting metadata for {url} using miyuki...")
    
    # Needs a dummy tmp file directory as miyuki writes tmp HTML
    if not os.path.exists(MOVIE_SAVE_PATH_ROOT):
        os.makedirs(MOVIE_SAVE_PATH_ROOT)

    try:
        # Fetch metadata using underlying miyuki scraper wrapper
        if not dl._fetch_metadata():
            print("[-] Error: Video not found or failed to fetch metadata.")
            sys.exit(1)
    except Exception as e:
        print(f"[-] Error: Video not found or network failure: {e}")
        sys.exit(1)

    title = dl.title or dl.movie_name
    print(f"[+] Title: {title}")
    
    # 2. Get playlist and highest quality resolution (m3u8 parsing)
    playlist_url = f"{VIDEO_M3U8_PREFIX}{dl.uuid}{VIDEO_PLAYLIST_SUFFIX}"
    try:
        playlist_resp = scraper.get(playlist_url)
        if playlist_resp.status_code != 200:
            print("[-] Error: Network failure while fetching video playlist.")
            sys.exit(1)
    except requests.RequestException as e:
        print(f"[-] Error: Network failure while fetching video playlist: {e}")
        sys.exit(1)
        
    final_quality, resolution_url = dl._get_final_quality_and_resolution(playlist_resp.text)
    if not final_quality:
        print("[-] Error: Could not determine available video quality stream.")
        sys.exit(1)
        
    print(f"[+] Highest quality stream selected: {final_quality}")
    
    # 3. Get segment info to build a tqdm download progress bar
    video_m3u8_url = f"{VIDEO_M3U8_PREFIX}{dl.uuid}/{resolution_url}"
    try:
        video_m3u8_resp = scraper.get(video_m3u8_url)
        if video_m3u8_resp.status_code != 200:
            print("[-] Error: Network failure fetching video segments.")
            sys.exit(1)
    except requests.RequestException as e:
        print(f"[-] Error: Network failure fetching video segments: {e}")
        sys.exit(1)
        
    # Analyze the .m3u8 text for segments
    video_offset_max = -1
    for line in reversed(video_m3u8_resp.text.strip().splitlines()):
        if line.endswith('.jpeg'):
            match = re.search(r'video(\d+)\.jpeg', line.strip())
            if match:
                video_offset_max = int(match.group(1))
                break
                
    if video_offset_max == -1:
        print("[-] Error: Could not parse video stream segments.")
        sys.exit(1)
        
    num_segments = video_offset_max + 1
    print(f"[+] Total segments to download: {num_segments}")
    
    # Clean up filename avoiding illegal characters
    safe_title = re.sub(r'[<>:"/\\|?*\s]+', '_', title).strip('_')
    output_filename = f"{safe_title}.mp4"
    print(f"[*] Starting download to '{output_filename}'...")
    
    # 4. Download and merge segments concurrently
    try:
        segments_content = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            # Submit all download tasks
            future_to_index = {
                executor.submit(
                    download_segment, 
                    scraper, 
                    f"https://surrit.com/{dl.uuid}/{resolution_url.split('/')[0]}/video{i}.jpeg", 
                    i
                ): i for i in range(num_segments)
            }
            
            # Progress bar for downloads
            with tqdm(total=num_segments, desc="Downloading video", unit="seg", ncols=100) as pbar:
                for future in concurrent.futures.as_completed(future_to_index):
                    index, content = future.result()
                    if content:
                        segments_content[index] = content
                    else:
                        print(f"\n[-] Warning: Failed to download segment {index}.")
                    pbar.update(1)

        print("[*] Merging segments...")
        with open(output_filename, 'wb') as outfile:
            for i in range(num_segments):
                if i in segments_content:
                    outfile.write(segments_content[i])
                else:
                    print(f"\n[-] Warning: Missing segment {i}. Skipping to maintain structure...")
                    
    except KeyboardInterrupt:
        print("\n[-] User interrupted the download. Exiting...")
        sys.exit(1)
    except Exception as e:
        print(f"\n[-] Error during download or merge: {e}")
        sys.exit(1)
        
    print(f"\n[+] Download successfully completed: {output_filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MissAV Video Downloader CLI using miyuki")
    parser.add_argument("url", help="MissAV video page URL (e.g. https://missav.com/...)")
    
    # Handle usage missing argument exception cleanly
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)
        
    args = parser.parse_args()
    download_missav_video(args.url)
