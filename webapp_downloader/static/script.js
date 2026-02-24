const urlInput = document.getElementById('url-input');
const downloadBtn = document.getElementById('download-btn');
const progressBar = document.getElementById('progress-bar');
const progressText = document.getElementById('progress-text');
const statusBox = document.getElementById('status-box');

let pollInterval;

downloadBtn.addEventListener('click', async () => {
    const url = urlInput.value.trim();
    if (!url) {
        statusBox.textContent = "Please enter a valid URL.";
        statusBox.className = "status-box error";
        return;
    }

    // Reset UI state
    downloadBtn.disabled = true;
    downloadBtn.textContent = "Downloading...";
    progressBar.style.height = "0%";
    progressBar.classList.remove('complete');
    progressText.textContent = "0%";
    statusBox.textContent = "Connecting to server...";
    statusBox.className = "status-box";

    try {
        const response = await fetch('/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });

        const data = await response.json();
        if (data.error) {
            throw new Error(data.error);
        }

        const downloadId = data.download_id;

        // Polling loop
        clearInterval(pollInterval);
        pollInterval = setInterval(() => pollProgress(downloadId), 500);

    } catch (err) {
        handleError(err.message);
    }
});

async function pollProgress(downloadId) {
    try {
        const res = await fetch(`/progress/${downloadId}`);
        const data = await res.json();

        if (data.error === true || res.status !== 200) {
            clearInterval(pollInterval);
            handleError(data.status || data.error);
            return;
        }

        // Apply percent text inside vertical UI bar
        const percent = data.progress;
        progressBar.style.height = `${percent}%`;
        progressText.textContent = `${percent}%`;
        statusBox.textContent = data.status;

        if (percent >= 100 && data.status === "Download Complete!") {
            clearInterval(pollInterval);
            progressBar.classList.add('complete');
            downloadBtn.textContent = "Download";
            downloadBtn.disabled = false;
            statusBox.innerHTML = `Success! Saved to downloads as:<br><strong>${data.file_name}</strong>`;
            statusBox.className = "status-box success";
        }
    } catch (err) {
        clearInterval(pollInterval);
        handleError("Connection to server lost. Retrying manually req.");
    }
}

function handleError(msg) {
    statusBox.textContent = msg;
    statusBox.className = "status-box error";
    downloadBtn.textContent = "Download";
    downloadBtn.disabled = false;
}
