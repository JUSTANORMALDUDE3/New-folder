const urlInput = document.getElementById('url-input');
const downloadBtn = document.getElementById('download-btn');
const progressBar = document.getElementById('progress-bar');
const progressText = document.getElementById('progress-text');
const statusBox = document.getElementById('status-box');
const fileNameBox = document.getElementById('file-name');

downloadBtn.addEventListener('click', startDownload);

async function startDownload() {
    const url = urlInput.value.trim();
    if (!url) {
        showStatus("Please enter a valid URL.", "error");
        return;
    }

    // Reset UI
    setProgress(0);
    downloadBtn.disabled = true;
    downloadBtn.textContent = "Preparing...";
    progressBar.classList.remove('complete');
    fileNameBox.textContent = "";
    showStatus("Extracting video metadata...", "");

    try {
        // Phase 1: Ask server to extract metadata (title, quality, segments)
        const prepRes = await fetch('/prepare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });

        const prepData = await prepRes.json();
        if (!prepRes.ok || prepData.error) {
            throw new Error(prepData.error || "Preparation failed.");
        }

        const { download_id, file_name, quality, num_segments } = prepData;

        setProgress(50);
        showStatus(`Found: ${file_name} (${quality}, ${num_segments} segments)`, "");
        downloadBtn.textContent = "Starting download...";

        // Phase 2: Trigger native browser download by navigating to the stream URL.
        // The browser's own download manager handles the file — no JS memory issues,
        // works perfectly for 1GB+ files, and shows real progress in the browser's
        // native download bar.
        const link = document.createElement('a');
        link.href = `/stream/${download_id}`;
        link.download = file_name;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        // Show success state
        setProgress(100);
        progressBar.classList.add('complete');
        fileNameBox.textContent = file_name;
        showStatus("Download started! Check your browser's download bar ↓", "success");

    } catch (err) {
        showStatus(err.message, "error");
    } finally {
        downloadBtn.disabled = false;
        downloadBtn.textContent = "Download";
    }
}

function setProgress(pct) {
    progressBar.style.width = `${pct}%`;
    progressText.textContent = `${pct}%`;
}

function showStatus(msg, type) {
    statusBox.textContent = msg;
    statusBox.className = "status-box" + (type ? ` ${type}` : "");
}
