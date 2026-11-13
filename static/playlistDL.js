document.getElementById('searchPodcast').addEventListener('input', function() {
    const term = this.value.trim().toLowerCase();
    const collapseGroups = document.querySelectorAll('#playlists > li[id^="dir_collapse_"]');

    collapseGroups.forEach(collapseLi => {
        const headerLi = collapseLi.previousElementSibling;
        const headerText = headerLi ? headerLi.textContent.toLowerCase() : '';
        const headerMatches = term !== '' && headerText.includes(term);
        const childItems = collapseLi.querySelectorAll('ul > li');
        let anyChildVisible = false;

        childItems.forEach(child => {
            const childText = child.textContent.toLowerCase();
            const showChild = term === '' || headerMatches || childText.includes(term);
            child.style.display = showChild ? '' : 'none';
            if (showChild) anyChildVisible = true;
        });

        const showGroup = term === '' || headerMatches || anyChildVisible;
        if (headerLi) headerLi.style.display = showGroup ? '' : 'none';
        collapseLi.style.display = showGroup ? '' : 'none';

        if (term !== '' && showGroup) {
            collapseLi.classList.add('show');
        } else if (term === '') {
            collapseLi.classList.remove('show');
        }
    });
});

document.addEventListener('click', function (e) {
  const link = e.target.closest('.feed-link');
  if (link) {
    e.preventDefault();
    const xml = link.getAttribute('data-xml');
    loadFeed(xml);
  }
});

document.addEventListener('DOMContentLoaded', function () {
    const noteInput = document.getElementById('note-input');
    if (noteInput) {
        noteInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                submitNote();
            }
        });
    }

    const searchInput = document.getElementById('vector-search-input');
    if (searchInput) {
        searchInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                performVectorSearch();
            }
        });
    }

    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
        chatInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                sendPlaylistChatMessage();
            }
        });
    }

    const downloadModal = document.getElementById('downloadModal');
    if (downloadModal) {
        downloadModal.addEventListener('shown.bs.modal', function () {
            refreshDownloadTasks();
            startDownloadPolling(2000);
        });
        downloadModal.addEventListener('hidden.bs.modal', function () {
            refreshDownloadTasks();
        });
    }

    // Initial check for any background tasks running on server startup
    refreshDownloadTasks();
});

let currentQueue = [];
let currentIndex = -1;
let activePlayer = null;
let currentPlaylistName = '';
let currentChatHistory = [];
let currentTrackTranscript = null;

// --- Playlist & Feed Loading ---

async function loadFeed(path) {
    const res = await fetch(`/api/feed/${encodeURIComponent(path)}`);
    const data = await res.json();
    if (!res.ok || !Array.isArray(data.items)) {
        console.error('Failed to load feed:', data.error || res.statusText);
        return;
    }

    currentPlaylistName = path.replace(/\.xml$/, '');
    currentChatHistory = [];
    currentQueue = data.items;

    const emptyWorkspace = document.getElementById('empty-workspace');
    const workspaceView = document.getElementById('workspace-view');
    if (emptyWorkspace) emptyWorkspace.classList.add('d-none');
    if (workspaceView) workspaceView.classList.remove('d-none');

    resetSearchAndChatUI();

    const infoId = "info";
    const playlistId = "playlist";

    const infoElement = document.getElementById(infoId);
    if (infoElement) {
        infoElement.innerHTML = `<h5 class="mb-1 fw-bold">${escapeHTML(data.title || '')}</h5><p class="small app-muted mb-0">${escapeHTML(data.description || '')}</p>`;
    }

    const rssLink = document.getElementById('rss-link');
    if (rssLink && data.link) {
        rssLink.href = data.link.replace(/^https?/, 'podcast');
        rssLink.classList.remove('disabled');
        rssLink.removeAttribute('aria-disabled');
    }

    const playAllBtn = document.getElementById('play-all-btn');
    if (playAllBtn) {
        playAllBtn.disabled = data.items.length === 0;
    }

    const list = document.getElementById(playlistId);
    if (list) {
        list.innerHTML = '';
        data.items.forEach((item, index) => {
            const li = document.createElement('li');
            li.className = "list-group-item list-group-item-action";
            li.dataset.index = index;
            li.innerText = item.title;
            li.onclick = () => playItem(index);
            list.appendChild(li);
        });
    }

    if (!activePlayer) {
        const content = document.getElementById('media-content');
        if (content) {
            content.innerHTML = `
                <div class="text-center py-5 px-3 app-muted">
                    <i class="bi bi-play-circle fs-1 d-block mb-2 app-accent-icon"></i>
                    <p class="mb-0">Playlist loaded. Select a track or click <strong>Play All</strong> to begin.</p>
                </div>`;
        }
    }

    const menuEl = document.getElementById('mainMenuOffcanvas');
    if (menuEl && window.bootstrap && bootstrap.Offcanvas) {
        const offcanvas = bootstrap.Offcanvas.getInstance(menuEl);
        if (offcanvas) offcanvas.hide();
    }
}

function resetSearchAndChatUI() {
    const searchInput = document.getElementById('vector-search-input');
    if (searchInput) searchInput.value = '';

    const searchStatus = document.getElementById('vector-search-status');
    if (searchStatus) {
        searchStatus.innerHTML = '';
        searchStatus.classList.add('d-none');
    }

    const searchResults = document.getElementById('vector-search-results');
    if (searchResults) {
        searchResults.innerHTML = '<small class="app-muted d-block text-center py-4">Enter a concept or question to find matching moments in this playlist.</small>';
    }

    const chatMessages = document.getElementById('chat-messages');
    if (chatMessages) {
        chatMessages.innerHTML = `
            <div class="app-chat-msg assistant mb-2 p-2 rounded">
                <div class="fw-bold small text-info mb-1"><i class="bi bi-robot me-1"></i>Study Assistant (${escapeHTML(currentPlaylistName)})</div>
                <div class="small">Ask questions about this playlist's lectures. I will synthesize answers with clickable timestamps!</div>
            </div>`;
    }

    const chatInput = document.getElementById('chat-input');
    if (chatInput) chatInput.value = '';

    const transcriptTitle = document.getElementById('transcript-track-title');
    if (transcriptTitle) transcriptTitle.innerText = 'No track selected';

    const transcriptContent = document.getElementById('transcript-content');
    if (transcriptContent) {
        transcriptContent.innerHTML = '<small class="app-muted d-block text-center py-3">Select a track to read its timestamped transcript.</small>';
    }

    const langBadge = document.getElementById('transcript-lang-badge');
    if (langBadge) {
        langBadge.innerText = '';
        langBadge.classList.add('d-none');
    }

    const tracksTabBtn = document.getElementById('tab-tracks-btn');
    if (tracksTabBtn && window.bootstrap && bootstrap.Tab) {
        const tab = new bootstrap.Tab(tracksTabBtn);
        tab.show();
    }
}

async function performVectorSearch() {
    const input = document.getElementById('vector-search-input');
    const query = input ? input.value.trim() : '';
    if (!query) return;

    const statusEl = document.getElementById('vector-search-status');
    const resultsEl = document.getElementById('vector-search-results');
    const searchBtn = document.getElementById('vector-search-btn');

    if (statusEl) {
        statusEl.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Searching playlist transcripts...`;
        statusEl.classList.remove('d-none');
    }
    if (searchBtn) searchBtn.disabled = true;

    try {
        const res = await fetch('/api/playlist/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist: currentPlaylistName,
                query: query,
                limit: 15
            })
        });

        const data = await res.json();
        if (!res.ok) {
            if (statusEl) statusEl.innerHTML = `<span class="text-danger">${escapeHTML(data.error || 'Search error')}</span>`;
            return;
        }

        const results = data.results || [];
        if (statusEl) {
            statusEl.innerHTML = `Found <strong>${results.length}</strong> matching moments for "<em>${escapeHTML(query)}</em>":`;
        }

        if (results.length === 0) {
            resultsEl.innerHTML = `
                <div class="text-center py-4 app-muted">
                    <i class="bi bi-search fs-3 d-block mb-2"></i>
                    <p class="small mb-1">No transcript matches found for "${escapeHTML(query)}".</p>
                    <small>Run <code>uv run ./transcribe.py</code> to transcribe and embed this playlist.</small>
                </div>`;
            return;
        }

        resultsEl.innerHTML = '';
        results.forEach(item => {
            const div = document.createElement('div');
            div.className = 'app-search-item';
            const pct = Math.round(item.similarity * 100);
            div.innerHTML = `
                <div class="d-flex justify-content-between align-items-start mb-1">
                    <strong class="small text-truncate me-2 text-info" title="${escapeHTML(item.track_title)}">${escapeHTML(item.track_title)}</strong>
                    <div class="d-flex align-items-center gap-1 flex-shrink-0">
                        <span class="badge app-badge" style="font-size: 0.7rem;">${pct}% match</span>
                        <span class="time-badge"><i class="bi bi-play-circle"></i> ${escapeHTML(item.formatted_time)}</span>
                    </div>
                </div>
                <div class="small text-break app-muted" style="font-size: 0.8rem; line-height: 1.3;">
                    ${escapeHTML(item.text)}
                </div>
            `;
            div.onclick = () => jumpToPlaylistTrackAndTime(item.file_path, item.start_seconds);
            resultsEl.appendChild(div);
        });
    } catch (err) {
        console.error('Vector search failed:', err);
        if (statusEl) statusEl.innerHTML = `<span class="text-danger">Failed to connect to search service.</span>`;
    } finally {
        if (searchBtn) searchBtn.disabled = false;
    }
}

async function sendPlaylistChatMessage() {
    const input = document.getElementById('chat-input');
    const msg = input ? input.value.trim() : '';
    if (!msg) return;

    const messagesEl = document.getElementById('chat-messages');
    const sendBtn = document.getElementById('chat-send-btn');
    const statusEl = document.getElementById('chat-status');

    // Append user message bubble
    const userDiv = document.createElement('div');
    userDiv.className = 'app-chat-msg user mb-2 p-2 rounded';
    userDiv.innerHTML = `<div class="small">${escapeHTML(msg)}</div>`;
    messagesEl.appendChild(userDiv);
    input.value = '';
    messagesEl.scrollTop = messagesEl.scrollHeight;

    if (sendBtn) sendBtn.disabled = true;
    if (statusEl) statusEl.classList.remove('d-none');

    try {
        const res = await fetch('/api/playlist/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist: currentPlaylistName,
                message: msg,
                history: currentChatHistory
            })
        });

        const data = await res.json();
        if (!res.ok) {
            const errDiv = document.createElement('div');
            errDiv.className = 'app-chat-msg assistant mb-2 p-2 rounded text-danger small';
            errDiv.innerHTML = `<i class="bi bi-exclamation-triangle me-1"></i>${escapeHTML(data.error || 'Failed to get answer.')}`;
            messagesEl.appendChild(errDiv);
            return;
        }

        currentChatHistory.push({ role: 'user', content: msg });
        currentChatHistory.push({ role: 'assistant', content: data.reply });

        const replyHtml = formatChatReply(data.reply, data.sources || []);
        const asstDiv = document.createElement('div');
        asstDiv.className = 'app-chat-msg assistant mb-2 p-2 rounded';
        asstDiv.innerHTML = `
            <div class="fw-bold small text-info mb-1"><i class="bi bi-robot me-1"></i>Assistant (${escapeHTML(data.model || 'AI')})</div>
            <div class="small mb-2" style="white-space: pre-wrap; line-height: 1.4;">${replyHtml}</div>
            ${renderSourcePills(data.sources || [])}
        `;
        messagesEl.appendChild(asstDiv);
    } catch (err) {
        console.error('Chat error:', err);
        const errDiv = document.createElement('div');
        errDiv.className = 'app-chat-msg assistant mb-2 p-2 rounded text-danger small';
        errDiv.innerHTML = `<i class="bi bi-exclamation-triangle me-1"></i>Failed to connect to AI chat service.`;
        messagesEl.appendChild(errDiv);
    } finally {
        if (sendBtn) sendBtn.disabled = false;
        if (statusEl) statusEl.classList.add('d-none');
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }
}

function formatChatReply(text, sources) {
    if (!text) return '';
    let escaped = escapeHTML(text);

    const timeRegex = /\[(?:([^@\]]+)\s*@\s*)?(\d{1,2}:\d{2}(?::\d{2})?)\]|\((?:([^@\)]+)\s*@\s*)?(\d{1,2}:\d{2}(?::\d{2})?)\)/g;

    return escaped.replace(timeRegex, (match, prefix1, time1, prefix2, time2) => {
        const timeStr = time1 || time2;
        const prefix = (prefix1 || prefix2 || '').trim();
        const seconds = parseTimeToSeconds(timeStr);
        const label = prefix ? `${prefix} @ ${timeStr}` : timeStr;
        return `<span class="time-badge" onclick="jumpToTimeInCurrentOrFirst('${escapeHTML(prefix)}', ${seconds})"><i class="bi bi-play-circle"></i> ${label}</span>`;
    });
}

function renderSourcePills(sources) {
    if (!sources || sources.length === 0) return '';
    const pills = sources.map(s => {
        const safePath = (s.file_path || '').replace(/'/g, "\\'");
        return `<span class="time-badge me-1 mb-1" onclick="jumpToPlaylistTrackAndTime('${safePath}', ${s.start_seconds})">
            <i class="bi bi-bookmark-check me-1"></i>${escapeHTML(s.track_title)} [${escapeHTML(s.formatted_time)}]
        </span>`;
    }).join('');
    return `<div class="border-top app-border pt-1 mt-1"><small class="text-white-50 d-block mb-1" style="font-size: 0.7rem;">Cited Excerpts:</small><div class="d-flex flex-wrap">${pills}</div></div>`;
}

function parseTimeToSeconds(timeStr) {
    const parts = timeStr.split(':').map(Number);
    if (parts.length === 3) {
        return parts[0] * 3600 + parts[1] * 60 + parts[2];
    } else if (parts.length === 2) {
        return parts[0] * 60 + parts[1];
    }
    return 0;
}

async function jumpToPlaylistTrackAndTime(filePath, timestamp) {
    let targetIndex = -1;
    if (filePath) {
        const cleanTarget = filePath.toLowerCase().replace(/\\/g, '/');
        targetIndex = currentQueue.findIndex(item => {
            const fp = (item.file_path || '').toLowerCase().replace(/\\/g, '/');
            const mu = decodeURIComponent(item.media_url || '').toLowerCase();
            return fp === cleanTarget || mu.includes(cleanTarget) || cleanTarget.includes(fp);
        });
    }

    if (targetIndex !== -1 && targetIndex !== currentIndex) {
        await playItem(targetIndex);
    }

    seekToTimestamp(timestamp);
}

function jumpToTimeInCurrentOrFirst(titleHint, timestamp) {
    if (titleHint) {
        const lower = titleHint.toLowerCase();
        const found = currentQueue.findIndex(item => item.title && item.title.toLowerCase().includes(lower));
        if (found !== -1 && found !== currentIndex) {
            playItem(found).then(() => seekToTimestamp(timestamp));
            return;
        }
    }
    seekToTimestamp(timestamp);
}

async function loadTrackTranscript(filePath, trackTitle) {
    const titleEl = document.getElementById('transcript-track-title');
    const contentEl = document.getElementById('transcript-content');
    const langBadge = document.getElementById('transcript-lang-badge');

    if (titleEl) titleEl.innerText = trackTitle || 'Current Track';
    currentTrackTranscript = null;

    if (!filePath || !contentEl) return;

    contentEl.innerHTML = `<div class="text-center py-3 app-muted"><span class="spinner-border spinner-border-sm me-1"></span>Loading transcript...</div>`;

    try {
        const res = await fetch(`/api/media/transcript?file_path=${encodeURIComponent(filePath)}`);
        const data = await res.json();

        if (!res.ok || !data.found) {
            if (langBadge) langBadge.classList.add('d-none');
            contentEl.innerHTML = `
                <div class="text-center py-3 app-muted">
                    <i class="bi bi-file-earmark-text fs-4 d-block mb-1"></i>
                    <p class="small mb-1">No transcript generated yet for this track.</p>
                    <small>Run <code>uv run ./transcribe.py</code> to transcribe with Whisper.</small>
                </div>`;
            return;
        }

        if (langBadge) {
            langBadge.innerText = (data.language || 'en').toUpperCase();
            langBadge.classList.remove('d-none');
        }
        currentTrackTranscript = data.segments || [];

        if (currentTrackTranscript.length === 0) {
            contentEl.innerHTML = `<div class="p-2 small text-muted">Transcript is empty.</div>`;
            return;
        }

        contentEl.innerHTML = '';
        currentTrackTranscript.forEach((seg, idx) => {
            const div = document.createElement('div');
            div.className = 'transcript-segment d-flex align-items-start gap-2 mb-1';
            div.dataset.start = seg.start;
            div.dataset.end = seg.end;
            div.dataset.idx = idx;
            div.innerHTML = `
                <span class="time-badge flex-shrink-0 font-monospace">${formatTime(seg.start)}</span>
                <span class="transcript-text small text-break">${escapeHTML(seg.text)}</span>
            `;
            div.onclick = () => seekToTimestamp(seg.start);
            contentEl.appendChild(div);
        });
    } catch (err) {
        console.error('Failed to load transcript:', err);
        if (langBadge) langBadge.classList.add('d-none');
        contentEl.innerHTML = `<div class="text-danger small p-2">Failed to load transcript.</div>`;
    }
}

function setNotesEnabled(enabled) {
    const noteInput = document.getElementById('note-input');
    const addBtn = document.getElementById('add-note-btn');
    const timeBadge = document.getElementById('note-current-time');
    if (noteInput) {
        noteInput.disabled = !enabled;
        if (!enabled) noteInput.value = '';
    }
    if (addBtn) addBtn.disabled = !enabled;
    if (timeBadge && !enabled) timeBadge.innerText = '0:00';
}

function showNoteFeedback(msg) {
    const feedback = document.getElementById('notes-feedback');
    if (!feedback) return;
    if (!msg) {
        feedback.textContent = '';
        feedback.classList.add('d-none');
    } else {
        feedback.textContent = msg;
        feedback.classList.remove('d-none');
    }
}

async function playItem(index) {
    if (index < 0 || index >= currentQueue.length) return;
    currentIndex = index;
    const item = currentQueue[index];

    if (activePlayer) {
        await activePlayer.destroy();
        activePlayer = null;
    }

    document.querySelectorAll('#playlist li').forEach((li) => {
        li.classList.toggle('active', Number(li.dataset.index) === index);
    });

    const pubDate = document.getElementById('pubdate');
    const pubDateWrapper = document.getElementById('pubdate-wrapper');
    if (pubDate) pubDate.innerText = item.pubDate || '';
    if (pubDateWrapper) pubDateWrapper.classList.toggle('d-none', !item.pubDate);

    renderTabbedMetadataModal(item.description || {}, '#metadataModalBody');
    const overlay = document.getElementById('media-overlay');
    const content = document.getElementById('media-content');
    overlay.classList.remove('d-none');

    const isVideo = item.media_type && item.media_type.includes('video');
    content.innerHTML = isVideo
        ? `<video id="main-player" controls style="width: 100%;"></video>`
        : `<audio id="main-player" controls style="width: 100%;"></audio>`;

    document.getElementById('now-playing-title').innerText = item.title || 'Untitled';
    showNoteFeedback('');
    setNotesEnabled(true);

    // Attach tracking player class to main element
    activePlayer = new DircastPlayer('main-player');

    // Auto-advance queue on track completion
    activePlayer.player.onended = async () => {
        await activePlayer.saveProgress(0);
        if (currentIndex < currentQueue.length - 1) {
            playItem(currentIndex + 1);
        }
    };

    // Load transcript for this track
    loadTrackTranscript(item.file_path || item.media_url, item.title);

    // Load file with state, views, and saved notes
    await activePlayer.loadMedia(item.file_path || item.media_url, item.media_url);
}

function playAll() { if (currentQueue.length > 0) playItem(0); }

function toggleMinimize() {
    document.getElementById('media-overlay').classList.toggle('minimized');
}

async function closePlayer() {
    if (activePlayer) {
        await activePlayer.destroy();
        activePlayer = null;
    }
    const content = document.getElementById('media-content');
    const emptyWorkspace = document.getElementById('empty-workspace');
    const workspaceView = document.getElementById('workspace-view');

    if (currentQueue.length === 0) {
        if (workspaceView) workspaceView.classList.add('d-none');
        if (emptyWorkspace) emptyWorkspace.classList.remove('d-none');
        if (content) content.innerHTML = '';
    } else {
        if (content) {
            content.innerHTML = `<div class="text-center py-5 px-3 app-muted"><i class="bi bi-play-circle fs-1 d-block mb-2 app-accent-icon"></i><p class="mb-0">Select a track to resume watching.</p></div>`;
        }
        document.getElementById('media-overlay').classList.add('d-none');
    }
    setNotesEnabled(false);
}

// --- Note Taking Functions ---

async function submitNote() {
    const noteInput = document.getElementById('note-input');
    const content = noteInput ? noteInput.value.trim() : '';

    if (!content || !activePlayer) return;

    showNoteFeedback('');
    const saved = await activePlayer.addNote(content);
    if (saved) {
        noteInput.value = '';
    } else {
        showNoteFeedback('Failed to save note. Please try again.');
    }
}

async function deleteNote(noteId) {
    if (!noteId || !activePlayer) return;
    showNoteFeedback('');
    const deleted = await activePlayer.deleteNote(noteId);
    if (!deleted) {
        showNoteFeedback('Failed to delete note. Please try again.');
    }
}

function renderNotes(notes) {
    const list = document.getElementById('notes-list');
    if (!list) return;

    list.innerHTML = '';

    if (!notes || notes.length === 0) {
        list.innerHTML = '<small class="text-white-50 d-block p-1">No notes yet for this episode.</small>';
        return;
    }

    notes.forEach(note => {
        const div = document.createElement('div');
        div.className = 'd-flex justify-content-between align-items-start gap-2 mb-1 bg-secondary bg-opacity-25 rounded p-1';

        const textSpan = document.createElement('small');
        textSpan.className = 'text-break flex-grow-1';
        textSpan.textContent = note.content;

        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'd-flex align-items-center gap-1 flex-shrink-0';

        const seekBtn = document.createElement('button');
        seekBtn.type = 'button';
        seekBtn.className = 'btn btn-xs btn-outline-info py-0 px-1 font-monospace';
        seekBtn.style.fontSize = '0.75rem';
        seekBtn.title = 'Jump to timestamp';
        seekBtn.textContent = formatTime(note.timestamp);
        seekBtn.addEventListener('click', () => seekToTimestamp(note.timestamp));

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn btn-xs btn-outline-danger py-0 px-1';
        delBtn.style.fontSize = '0.75rem';
        delBtn.title = 'Delete note';
        delBtn.innerHTML = '<i class="bi bi-trash"></i>';
        delBtn.addEventListener('click', () => deleteNote(note.id));

        actionsDiv.appendChild(seekBtn);
        actionsDiv.appendChild(delBtn);

        div.appendChild(textSpan);
        div.appendChild(actionsDiv);
        list.appendChild(div);
    });
}

function seekToTimestamp(seconds) {
    if (!activePlayer || !activePlayer.player) return;
    const targetTime = Number(seconds);
    if (!Number.isFinite(targetTime) || targetTime < 0) return;

    if (activePlayer.player.readyState >= 1) {
        activePlayer.player.currentTime = targetTime;
        activePlayer.player.play().catch(() => {});
    } else {
        activePlayer.pendingSeek = targetTime;
    }
}

function formatTime(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const hrs = Math.floor(total / 3600);
    const mins = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    const mm = hrs > 0 ? String(mins).padStart(2, '0') : String(mins);
    const ss = String(secs).padStart(2, '0');
    return hrs > 0 ? `${hrs}:${mm}:${ss}` : `${mm}:${ss}`;
}


// --- Core Tracking Player Engine ---
class DircastPlayer {
  constructor(mediaElementId) {
    this.player = document.getElementById(mediaElementId);
    this.currentFilePath = null;
    this.viewRecorded = false;
    this.savePromise = null;
    this.pendingSeek = null;
    this.destroyed = false;
    this.boundBeforeUnload = () => this.sendBeaconProgress();
    this.setupListeners();
  }

  setupListeners() {
    this.player.addEventListener('pause', () => {
      if (!this.destroyed && !this.player.ended) {
        this.saveProgress();
      }
    });

    window.addEventListener('beforeunload', this.boundBeforeUnload);

    const updateTimestampBadge = () => {
      const timeBadge = document.getElementById('note-current-time');
      if (timeBadge && !this.destroyed) {
        timeBadge.innerText = formatTime(this.player.currentTime);
      }
    };

    this.player.addEventListener('timeupdate', () => {
      updateTimestampBadge();
      if (!this.viewRecorded && this.player.currentTime > 10 && this.currentFilePath) {
        this.recordView();
      }
      this.updateActiveTranscriptSegment();
    });

    this.player.addEventListener('seeked', updateTimestampBadge);
    this.player.addEventListener('loadedmetadata', updateTimestampBadge);
  }

  async destroy() {
    if (this.destroyed) return;
    if (this.currentFilePath && !this.player.ended) {
      await this.saveProgress();
    }
    this.destroyed = true;
    window.removeEventListener('beforeunload', this.boundBeforeUnload);
    this.player.pause();
    this.player.removeAttribute('src');
    this.player.load();
  }

  async loadMedia(filePath, streamUrl) {
    if (this.currentFilePath) {
      await this.saveProgress();
    }

    this.currentFilePath = filePath;
    this.viewRecorded = false;
    this.pendingSeek = null;
    this.player.src = streamUrl;

    // Fetch progress, views, and notes state
    const res = await fetch(`/api/media/state?file_path=${encodeURIComponent(filePath)}`);
    if (this.destroyed) return;

    if (res.ok) {
        const state = await res.json();

        // Update View Count UI
        const viewEl = document.getElementById('view-count');
        if (viewEl) viewEl.innerText = state.views || 0;

        // Render Notes list
        renderNotes(state.notes || []);

        // Restore position on load
        this.player.addEventListener('loadedmetadata', () => {
          if (this.destroyed) return;
          if (this.pendingSeek !== null) {
            this.player.currentTime = this.pendingSeek;
            this.pendingSeek = null;
          } else if (state.resume_position > 0 && state.resume_position < this.player.duration - 5) {
            this.player.currentTime = state.resume_position;
          }
          this.player.play().catch(() => {});
        }, { once: true });
    } else {
        renderNotes([]);
        this.player.play().catch(() => {});
    }
  }

  async saveProgress(overridePos = null) {
    if (!this.currentFilePath || this.destroyed) return;

    if (this.savePromise) {
      if (overridePos === null) return;
      await this.savePromise;
    }

    const pos = overridePos !== null ? overridePos : (this.player.currentTime || 0);

    this.savePromise = fetch('/api/media/save-position', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: this.currentFilePath, position: pos })
    }).catch((err) => {
      console.error('Failed to save progress:', err);
    }).finally(() => {
      this.savePromise = null;
    });

    await this.savePromise;
  }

  sendBeaconProgress() {
    if (!this.currentFilePath || this.destroyed) return;
    const blob = new Blob([JSON.stringify({
      file_path: this.currentFilePath,
      position: this.player.ended ? 0 : (this.player.currentTime || 0)
    })], { type: 'application/json' });

    navigator.sendBeacon('/api/media/save-position', blob);
  }

  async recordView() {
    this.viewRecorded = true;
    try {
      const res = await fetch('/api/media/view', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: this.currentFilePath })
      });
      if (res.ok) {
        const data = await res.json();
        const viewEl = document.getElementById('view-count');
        if (viewEl) {
          if (typeof data.views === 'number') {
            viewEl.innerText = data.views;
          } else {
            const currentViews = parseInt(viewEl.innerText, 10) || 0;
            viewEl.innerText = currentViews + 1;
          }
        }
      }
    } catch (err) {
      console.error('Failed to record view:', err);
    }
  }

  async refreshNotes() {
    const stateRes = await fetch(`/api/media/state?file_path=${encodeURIComponent(this.currentFilePath)}`);
    if (!stateRes.ok) return false;
    const state = await stateRes.json();
    renderNotes(state.notes || []);
    return true;
  }

  async addNote(content) {
    if (!this.currentFilePath) return false;
    const timestamp = Number.isFinite(this.player.currentTime) ? this.player.currentTime : 0;

    try {
      const res = await fetch('/api/media/notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_path: this.currentFilePath,
          timestamp: timestamp,
          content: content
        })
      });

      if (!res.ok) return false;
      await this.refreshNotes();
      return true;
    } catch (err) {
      console.error('Failed to add note:', err);
      return false;
    }
  }

  async deleteNote(noteId) {
    if (!this.currentFilePath || !noteId) return false;

    try {
      const res = await fetch(`/api/media/notes/${encodeURIComponent(noteId)}`, {
        method: 'DELETE'
      });
      if (!res.ok) return false;
      await this.refreshNotes();
      return true;
    } catch (err) {
      console.error('Failed to delete note:', err);
      return false;
    }
  }

  updateActiveTranscriptSegment() {
    if (!currentTrackTranscript || currentTrackTranscript.length === 0) return;
    const curTime = this.player.currentTime;
    const container = document.getElementById('transcript-content');
    if (!container) return;

    const segments = container.querySelectorAll('.transcript-segment');
    segments.forEach(el => {
      const start = parseFloat(el.dataset.start);
      const end = parseFloat(el.dataset.end);
      const isActive = curTime >= start && curTime <= end;
      if (isActive && !el.classList.contains('active-segment')) {
        el.classList.add('active-segment');
        const pane = document.getElementById('tab-transcript-pane');
        if (pane && pane.classList.contains('active')) {
          el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      } else if (!isActive && el.classList.contains('active-segment')) {
        el.classList.remove('active-segment');
      }
    });
  }
}


/**
 * Renders JSON metadata into a clean overview + collapsible sections for secondary/technical details.
 * @param {Object|String} rawData - Raw JSON string or object.
 * @param {HTMLElement|String} targetContainer - Target DOM element or query selector.
 */
let currentMetadataSections = null;
let currentMetadataRaw = null;

function renderTabbedMetadataModal(rawData, targetContainer) {
    const container = typeof targetContainer === 'string'
        ? document.querySelector(targetContainer)
        : targetContainer;

    if (!container) return;

    currentMetadataSections = null;
    currentMetadataRaw = null;

    if (rawData === null || rawData === undefined || rawData === '') {
        container.innerHTML = `<p class="text-muted p-3 mb-0">No metadata available for this track.</p>`;
        return;
    }

    let data = rawData;
    if (typeof rawData === 'string') {
        try {
            data = JSON.parse(rawData);
        } catch (e) {
            container.innerHTML = `<div class="alert alert-danger m-2">Invalid JSON metadata format.</div>`;
            return;
        }
    }

    if (!data || typeof data !== 'object') {
        container.innerHTML = `<p class="text-muted p-3 mb-0">No metadata available.</p>`;
        return;
    }

    const sections = categorizeMetadata(data);
    currentMetadataSections = sections;
    currentMetadataRaw = data;

    const summaryHtml = buildSummaryCard(data);
    const descriptionText = typeof data.description === 'string' ? data.description.trim() : '';

    let accordionHtml = `<div class="accordion" id="metadataAccordion">`;

    if (descriptionText) {
        const isLongDesc = descriptionText.length > 260 || descriptionText.split('\n').length > 5;
        accordionHtml += buildCollapsibleSection(
            'meta-desc',
            'Description',
            'bi-text-paragraph',
            `<div class="meta-item small" style="white-space: pre-wrap;">${escapeHTML(descriptionText)}</div>`,
            !isLongDesc
        );
    }

    if (sections.tags.length > 0 || sections.categories.length > 0) {
        const badges = [
            ...sections.categories.map(c => `<span class="badge bg-primary me-1 mb-1 meta-item">${escapeHTML(c)}</span>`),
            ...sections.tags.map(t => `<span class="badge bg-secondary bg-opacity-75 me-1 mb-1 meta-item">${escapeHTML(t)}</span>`),
        ].join('');
        const totalTags = sections.categories.length + sections.tags.length;
        accordionHtml += buildCollapsibleSection(
            'meta-tags',
            `Categories & Tags (${totalTags})`,
            'bi-tags',
            `<div class="d-flex flex-wrap">${badges}</div>`,
            false
        );
    }

    if (Object.keys(sections.media).length > 0) {
        accordionHtml += buildCollapsibleSection(
            'meta-media',
            `Media & File Details (${Object.keys(sections.media).length})`,
            'bi-film',
            buildGridSection(sections.media),
            false
        );
    }

    if (Object.keys(sections.channel).length > 0) {
        accordionHtml += buildCollapsibleSection(
            'meta-channel',
            `Channel & Playlist Details (${Object.keys(sections.channel).length})`,
            'bi-person-video2',
            buildGridSection(sections.channel),
            false
        );
    }

    if (Object.keys(sections.technical).length > 0) {
        accordionHtml += buildCollapsibleSection(
            'meta-technical',
            `Technical & Internal Data (${Object.keys(sections.technical).length})`,
            'bi-gear',
            `<div class="text-muted small">Loading technical details...</div>`,
            false,
            'technical'
        );
    }

    accordionHtml += buildCollapsibleSection(
        'meta-raw',
        'Raw JSON',
        'bi-code-slash',
        `<div class="text-muted small">Click to load raw JSON...</div>`,
        false,
        'raw'
    );

    accordionHtml += `</div>`;

    container.innerHTML = `
        <div class="mb-3">
            <input type="text" class="form-control form-control-sm" id="metadataSearchInput"
                   placeholder="Filter metadata fields..." oninput="filterMetadataTabs(this.value)" />
        </div>
        ${summaryHtml}
        ${accordionHtml}
    `;

    attachLazyMetadataListeners(container);
}

function attachLazyMetadataListeners(container) {
    const techEl = container.querySelector('#meta-technical');
    if (techEl) {
        techEl.addEventListener('show.bs.collapse', () => renderLazySection(techEl, 'technical'), { once: true });
    }
    const rawEl = container.querySelector('#meta-raw');
    if (rawEl) {
        rawEl.addEventListener('show.bs.collapse', () => renderLazySection(rawEl, 'raw'), { once: true });
    }
}

function renderLazySection(collapseEl, kind) {
    if (!collapseEl || collapseEl.dataset.rendered === 'true') return;
    const body = collapseEl.querySelector('.accordion-body');
    if (!body) return;

    if (kind === 'technical' && currentMetadataSections) {
        body.innerHTML = buildGridSection(currentMetadataSections.technical, { summarizeLargeCollections: true });
        collapseEl.dataset.rendered = 'true';
    } else if (kind === 'raw' && currentMetadataRaw) {
        body.innerHTML = renderRawJsonView(currentMetadataRaw);
        collapseEl.dataset.rendered = 'true';
    }
}

function buildSummaryCard(data) {
    const title = data.title || data.fulltitle || 'Untitled Track';
    const uploader = data.uploader || data.channel || data.author || '';
    const uploaderUrl = data.channel_url || data.uploader_url || '';
    const playlist = data.playlist_title || data.playlist || '';
    const index = data.playlist_index;
    const count = data.playlist_count || data.n_entries;
    const duration = formatMetadataDuration(data.duration, data.duration_string);
    const uploadDate = formatUploadDate(data.upload_date);
    const views = typeof data.view_count === 'number' ? data.view_count.toLocaleString() : '';
    const likes = typeof data.like_count === 'number' ? data.like_count.toLocaleString() : '';
    const sourceUrl = data.webpage_url || data.original_url || '';
    const thumb = typeof data.thumbnail === 'string' && isImageUrl(data.thumbnail) ? data.thumbnail : '';

    const metaPills = [];
    if (duration) metaPills.push(`<span class="badge bg-dark"><i class="bi bi-clock me-1"></i>${escapeHTML(duration)}</span>`);
    if (uploadDate) metaPills.push(`<span class="badge bg-light text-dark border"><i class="bi bi-calendar3 me-1"></i>${escapeHTML(uploadDate)}</span>`);
    if (views) metaPills.push(`<span class="badge bg-light text-dark border"><i class="bi bi-eye me-1"></i>${escapeHTML(views)} views</span>`);
    if (likes) metaPills.push(`<span class="badge bg-light text-dark border"><i class="bi bi-hand-thumbs-up me-1"></i>${escapeHTML(likes)}</span>`);

    const playlistLabel = playlist
        ? `${escapeHTML(playlist)}${index ? ` (#${escapeHTML(index)}${count ? ` of ${escapeHTML(count)}` : ''})` : ''}`
        : '';

    const uploaderHtml = uploader
        ? (uploaderUrl && (uploaderUrl.startsWith('http://') || uploaderUrl.startsWith('https://'))
            ? `<a href="${escapeHTML(uploaderUrl)}" target="_blank" rel="noopener noreferrer" class="text-decoration-none">${escapeHTML(uploader)}</a>`
            : escapeHTML(uploader))
        : '';

    const sourceBtn = sourceUrl && (sourceUrl.startsWith('http://') || sourceUrl.startsWith('https://'))
        ? `<a href="${escapeHTML(sourceUrl)}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-outline-primary py-0 px-2 mt-2"><i class="bi bi-box-arrow-up-right me-1"></i>Original Source</a>`
        : '';

    return `
        <div class="card border-0 bg-light mb-3 meta-summary-card">
            <div class="card-body p-3">
                <div class="d-flex flex-column flex-sm-row gap-3 align-items-start">
                    ${thumb ? `<img src="${escapeHTML(thumb)}" alt="Thumbnail" class="rounded flex-shrink-0" style="width: 150px; max-height: 100px; object-fit: cover;" />` : ''}
                    <div class="flex-grow-1 min-w-0">
                        <h6 class="fw-bold mb-1 meta-item">${escapeHTML(title)}</h6>
                        ${uploaderHtml ? `<div class="small text-muted mb-1 meta-item"><i class="bi bi-person me-1"></i>${uploaderHtml}</div>` : ''}
                        ${playlistLabel ? `<div class="small text-muted mb-2 meta-item"><i class="bi bi-collection-play me-1"></i>${playlistLabel}</div>` : ''}
                        ${metaPills.length ? `<div class="d-flex flex-wrap gap-1 meta-item">${metaPills.join('')}</div>` : ''}
                        ${sourceBtn}
                    </div>
                </div>
            </div>
        </div>`;
}

function buildCollapsibleSection(id, title, iconClass, bodyHtml, expandedByDefault = false, lazyKind = '') {
    const btnClass = expandedByDefault ? 'accordion-button py-2 px-3' : 'accordion-button collapsed py-2 px-3';
    const collapseClass = expandedByDefault ? 'accordion-collapse collapse show' : 'accordion-collapse collapse';
    const lazyAttr = lazyKind ? `data-lazy-kind="${escapeHTML(lazyKind)}"` : '';
    return `
        <div class="accordion-item meta-section" data-default-open="${expandedByDefault ? 'true' : 'false'}">
            <h2 class="accordion-header">
                <button class="${btnClass} fw-semibold small" type="button" data-bs-toggle="collapse" data-bs-target="#${id}" aria-expanded="${expandedByDefault}">
                    <i class="bi ${iconClass} me-2 text-primary"></i>
                    <span>${escapeHTML(title)}</span>
                </button>
            </h2>
            <div id="${id}" class="${collapseClass}" ${lazyAttr}>
                <div class="accordion-body p-3">
                    ${bodyHtml}
                </div>
            </div>
        </div>`;
}

function categorizeMetadata(data) {
    const sections = {
        categories: Array.isArray(data.categories) ? data.categories.filter(Boolean) : [],
        tags: Array.isArray(data.tags) ? data.tags.filter(Boolean) : [],
        media: {},
        channel: {},
        technical: {}
    };

    if (!data || typeof data !== 'object') return sections;

    // Keys already highlighted cleanly in the top summary card / description / tags
    const primaryHandled = new Set([
        'title', 'fulltitle', 'description', 'thumbnail', 'duration', 'duration_string',
        'upload_date', 'view_count', 'like_count', 'webpage_url', 'categories', 'tags'
    ]);

    const mediaOrder = [
        'resolution', 'width', 'height', 'fps', 'ext', 'format', 'format_note',
        'vcodec', 'acodec', 'tbr', 'vbr', 'abr', 'asr', 'audio_channels',
        'filesize', 'filesize_approx', 'media_type', 'protocol', 'language',
        'dynamic_range', 'aspect_ratio', 'filepath', 'download_path', 'file_path'
    ];

    const channelKeys = new Set([
        'channel', 'channel_id', 'channel_url', 'channel_follower_count',
        'uploader', 'uploader_id', 'uploader_url', 'creators',
        'playlist', 'playlist_title', 'playlist_id', 'playlist_index', 'playlist_count',
        'n_entries', 'playlist_uploader', 'playlist_uploader_id', 'playlist_channel',
        'playlist_channel_id', 'playlist_webpage_url', 'extractor', 'extractor_key',
        'webpage_url_domain', 'availability', 'live_status', 'is_live', 'was_live',
        'age_limit', 'comment_count', 'id', 'display_id', 'original_url'
    ]);

    for (const key of mediaOrder) {
        const val = data[key];
        if (val !== null && val !== undefined && val !== '') {
            sections.media[key] = val;
        }
    }

    for (const [key, value] of Object.entries(data)) {
        if (value === null || value === undefined || value === '') continue;
        if (Array.isArray(value) && value.length === 0) continue;
        if (typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === 0) continue;
        if (primaryHandled.has(key) || key in sections.media) continue;

        if (channelKeys.has(key)) {
            sections.channel[key] = value;
        } else {
            sections.technical[key] = value;
        }
    }

    return sections;
}

const KEY_LABEL_OVERRIDES = {
    id: 'Video ID',
    display_id: 'Display ID',
    webpage_url: 'Source URL',
    original_url: 'Original URL',
    webpage_url_domain: 'Domain',
    filesize: 'File Size',
    filesize_approx: 'Approx. File Size',
    vcodec: 'Video Codec',
    acodec: 'Audio Codec',
    tbr: 'Total Bitrate',
    vbr: 'Video Bitrate',
    abr: 'Audio Bitrate',
    asr: 'Audio Sample Rate',
    fps: 'Frame Rate',
    ext: 'Extension',
    filepath: 'Local File Path',
    _filename: 'Original Filename',
    __finaldir: 'Output Directory',
    automatic_captions: 'Auto-Generated Captions',
};

function formatMetadataDuration(seconds, durationString) {
    if (typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0) {
        return formatTime(seconds);
    }
    return durationString ? String(durationString) : '';
}

function formatUploadDate(rawDate) {
    const str = String(rawDate || '').trim();
    if (/^\d{8}$/.test(str)) {
        return `${str.slice(0, 4)}-${str.slice(4, 6)}-${str.slice(6, 8)}`;
    }
    return str;
}

function formatBytes(bytes) {
    if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes < 0) return String(bytes);
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let idx = 0;
    let val = bytes;
    while (val >= 1024 && idx < units.length - 1) {
        val /= 1024;
        idx++;
    }
    return `${val.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function formatNamedValue(key, val, options = {}) {
    if (typeof val === 'boolean') return val ? 'Yes' : 'No';
    if ((key === 'filesize' || key === 'filesize_approx') && typeof val === 'number') {
        return escapeHTML(formatBytes(val));
    }
    if ((key === 'tbr' || key === 'vbr' || key === 'abr') && typeof val === 'number') {
        return `${Math.round(val)} kbps`;
    }
    if (key === 'asr' && typeof val === 'number') {
        return `${val.toLocaleString()} Hz`;
    }
    if ((key === 'comment_count' || key === 'channel_follower_count' || key === 'view_count' || key === 'like_count') && typeof val === 'number') {
        return escapeHTML(val.toLocaleString());
    }
    if ((key === 'timestamp' || key === 'epoch') && typeof val === 'number') {
        return escapeHTML(new Date(val * 1000).toLocaleString());
    }
    if (options.summarizeLargeCollections) {
        if (Array.isArray(val) && val.length > 20) {
            const sample = val.slice(0, 3).map(item => escapeHTML(summarizeItem(item))).join(', ');
            return `<span class="text-muted">${val.length} items (${sample}, &hellip;)</span>`;
        }
        if (typeof val === 'object' && val !== null && !Array.isArray(val)) {
            const keys = Object.keys(val);
            if (keys.length > 15) {
                const sampleKeys = keys.slice(0, 6).map(k => escapeHTML(k)).join(', ');
                return `<span class="text-muted">${keys.length} entries (${sampleKeys}, &hellip;)</span>`;
            }
        }
    }
    return formatValue(val);
}

/**
 * Builds a clean two-column Key-Value grid for metadata sections.
 */
function buildGridSection(data, options = {}) {
    if (typeof data !== 'object' || data === null) {
        return `<div class="p-2">${formatValue(data)}</div>`;
    }

    let html = `<div class="row g-2 meta-grid">`;

    for (const [key, value] of Object.entries(data)) {
        if (value === null || value === undefined || value === '') continue;

        const formattedKey = formatKeyLabel(key);
        const isFullWidth =
            String(value).length > 60 ||
            Array.isArray(value) ||
            typeof value === 'object';
        const colClass = isFullWidth ? 'col-12' : 'col-md-6';

        html += `
            <div class="${colClass} meta-item">
                <div class="card h-100 bg-light border-0">
                    <div class="card-body p-2">
                        <small class="text-muted fw-bold text-uppercase d-block mb-1" style="font-size: 0.7rem;">${escapeHTML(formattedKey)}</small>
                        <div class="card-text small">${formatNamedValue(key, value, options)}</div>
                    </div>
                </div>
            </div>`;
    }

    html += `</div>`;
    return html;
}

function renderRawJsonView(data) {
    const jsonStr = JSON.stringify(data, null, 2);
    return `
        <div class="position-relative">
            <button type="button" class="btn btn-sm btn-outline-light position-absolute top-0 end-0 m-2"
                    onclick="copyRawMetadataJson(this)">
                <i class="bi bi-clipboard me-1"></i>Copy
            </button>
            <pre class="bg-dark text-light p-3 rounded mb-0" style="max-height: 360px; overflow-y: auto; font-size: 0.8rem;"><code>${escapeHTML(jsonStr)}</code></pre>
        </div>`;
}

function copyRawMetadataJson(btn) {
    const pre = btn.parentElement.querySelector('pre code');
    if (!pre || !navigator.clipboard) return;
    navigator.clipboard.writeText(pre.textContent).then(() => {
        const original = btn.innerHTML;
        btn.innerHTML = '<i class="bi bi-check2 me-1"></i>Copied';
        setTimeout(() => { btn.innerHTML = original; }, 1500);
    }).catch(() => {});
}

// Unique counter to generate distinct HTML IDs for each collapsible array/object section
let arrayCollapseCounter = 0;

function summarizeItem(item) {
    if (item === null || item === undefined) return '';
    if (typeof item !== 'object') return String(item);
    return item.format || item.format_note || item.resolution || item.id || item.ext || `{${Object.keys(item).length} fields}`;
}

function formatValue(val) {
    // 1. Arrays rendered as Collapsible Accordions
    if (Array.isArray(val)) {
        if (val.length === 0) return `<span class="text-muted">None</span>`;

        arrayCollapseCounter++;
        const collapseId = `meta-array-collapse-${arrayCollapseCounter}`;

        const previewItems = val.slice(0, 2).map(item => escapeHTML(summarizeItem(item))).join(', ');
        const overflowCount = val.length - 2;
        const previewLabel = overflowCount > 0
            ? `${previewItems} <span class="text-muted fw-normal">(+${overflowCount} more)</span>`
            : previewItems;

        return `
            <div class="meta-array-wrapper">
                <div class="d-flex align-items-center justify-content-between mb-1">
                    <button class="btn btn-sm btn-outline-secondary py-0 px-2 d-inline-flex align-items-center gap-1"
                            type="button"
                            data-bs-toggle="collapse"
                            data-bs-target="#${collapseId}"
                            aria-expanded="false">
                        <i class="bi bi-list-nested"></i>
                        <span>Show Items (${val.length})</span>
                    </button>
                    <small class="text-truncate text-muted ms-2" style="max-width: 240px;">
                        ${previewLabel}
                    </small>
                </div>
                <div class="collapse mt-2" id="${collapseId}">
                    <ul class="list-group list-group-flush border rounded bg-white" style="max-height: 200px; overflow-y: auto;">
                        ${val.map((item, idx) => `
                            <li class="list-group-item py-1 px-2 border-bottom-0 small font-monospace d-flex align-items-start gap-2">
                                <span class="text-muted user-select-none">${idx + 1}.</span>
                                <div class="text-break w-100">${formatValue(item)}</div>
                            </li>
                        `).join('')}
                    </ul>
                </div>
            </div>`;
    }

    // 2. Nested Objects collapsed cleanly by default
    if (typeof val === 'object' && val !== null) {
        const keys = Object.keys(val);
        if (keys.length === 0) return `<span class="text-muted">None</span>`;

        arrayCollapseCounter++;
        const collapseId = `meta-obj-collapse-${arrayCollapseCounter}`;

        return `
            <div class="meta-object-wrapper">
                <button class="btn btn-sm btn-outline-secondary py-0 px-2 d-inline-flex align-items-center gap-1"
                        type="button"
                        data-bs-toggle="collapse"
                        data-bs-target="#${collapseId}"
                        aria-expanded="false">
                    <i class="bi bi-braces"></i>
                    <span>Inspect Fields (${keys.length})</span>
                </button>
                <div class="collapse mt-2" id="${collapseId}">
                    <div class="border rounded p-2 bg-white" style="max-height: 240px; overflow-y: auto;">
                        ${buildGridSection(val)}
                    </div>
                </div>
            </div>`;
    }

    const strVal = String(val).trim();

    // 3. Image Preview URLs
    if (isImageUrl(strVal)) {
        const safeUrl = escapeHTML(strVal);
        return `<div class="my-1">
            <a href="${safeUrl}" target="_blank" rel="noopener noreferrer">
                <img src="${safeUrl}" class="img-thumbnail" style="max-height: 120px; object-fit: cover;" alt="Metadata image" />
            </a>
        </div>`;
    }

    // 4. Web URLs
    if (strVal.startsWith('http://') || strVal.startsWith('https://')) {
        const safeUrl = escapeHTML(strVal);
        return `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="text-decoration-none text-truncate d-inline-block mw-100">
            <i class="bi bi-box-arrow-up-right me-1"></i>${safeUrl}
        </a>`;
    }

    // 5. Primitive values
    return `<span class="text-break">${escapeHTML(strVal)}</span>`;
}

function filterMetadataTabs(query) {
    const term = query.trim().toLowerCase();
    document.querySelectorAll('#metadataAccordion .meta-section').forEach(section => {
        const collapseEl = section.querySelector('.accordion-collapse');
        const btnEl = section.querySelector('.accordion-button');

        if (term && collapseEl && collapseEl.dataset.lazyKind === 'technical' && collapseEl.dataset.rendered !== 'true') {
            renderLazySection(collapseEl, 'technical');
        }

        const items = section.querySelectorAll('.meta-item');

        if (!term) {
            section.style.display = '';
            items.forEach(item => { item.style.display = ''; });
            const defaultOpen = section.dataset.defaultOpen === 'true';
            if (collapseEl && btnEl) {
                collapseEl.classList.toggle('show', defaultOpen);
                btnEl.classList.toggle('collapsed', !defaultOpen);
                btnEl.setAttribute('aria-expanded', String(defaultOpen));
            }
            return;
        }

        let anyMatch = false;
        if (items.length > 0) {
            items.forEach(item => {
                const matched = item.textContent.toLowerCase().includes(term);
                item.style.display = matched ? '' : 'none';
                if (matched) anyMatch = true;
            });
        } else {
            anyMatch = section.textContent.toLowerCase().includes(term);
        }

        section.style.display = anyMatch ? '' : 'none';
        if (collapseEl && btnEl) {
            collapseEl.classList.toggle('show', anyMatch);
            btnEl.classList.toggle('collapsed', !anyMatch);
            btnEl.setAttribute('aria-expanded', String(anyMatch));
        }
    });
}

function isImageUrl(url) {
    return (
        typeof url === 'string' &&
        (url.match(/\.(jpeg|jpg|gif|png|webp)($|\?)/i) != null ||
         url.includes('ytimg.com') ||
         url.includes('i.scdn.co'))
    );
}

function formatKeyLabel(key) {
    if (KEY_LABEL_OVERRIDES[key]) return KEY_LABEL_OVERRIDES[key];
    return key
        .replace(/^_+/, '')
        .replace(/_/g, ' ')
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .replace(/\b\w/g, c => c.toUpperCase());
}

function escapeHTML(str) {
    return String(str).replace(
        /[&<>'"]/g,
        tag =>
            ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[
                 tag
             ] ||
             tag)
    );
}

// --- Web Downloader & Background Task Management ---

let downloadPollTimer = null;
let knownTaskStatuses = {};

async function handleDownloadSubmit(event) {
    if (event) event.preventDefault();

    const urlInput = document.getElementById('download-url-input');
    const autoTranscribeCheck = document.getElementById('auto-transcribe-check');
    const submitBtn = document.getElementById('download-submit-btn');

    if (!urlInput) return;
    const url = urlInput.value.trim();
    if (!url) return;

    const autoTranscribe = autoTranscribeCheck ? autoTranscribeCheck.checked : false;

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>Starting...';
    }

    try {
        const res = await fetch('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: url, auto_transcribe: autoTranscribe }),
        });

        const data = await res.json();
        if (!res.ok) {
            alert(data.error || 'Failed to start download.');
            return;
        }

        urlInput.value = '';
        await refreshDownloadTasks();
        startDownloadPolling(2000);
    } catch (err) {
        console.error('Download request error:', err);
        alert('Network or server error while starting download.');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="bi bi-download me-1"></i> Download';
        }
    }
}

async function refreshDownloadTasks() {
    try {
        const res = await fetch('/api/download/status');
        if (!res.ok) return;
        const data = await res.json();
        const tasks = data.tasks || [];
        const activeCount = data.active_count || 0;

        // Update Navbar Active Download Indicator
        const activeBtn = document.getElementById('active-download-btn');
        const activeText = document.getElementById('active-download-text');
        if (activeBtn && activeText) {
            if (activeCount > 0) {
                activeBtn.classList.remove('d-none');
                activeBtn.classList.add('d-inline-flex');
                activeText.textContent = activeCount === 1 ? '1 downloading' : `${activeCount} downloading`;
            } else {
                activeBtn.classList.add('d-none');
                activeBtn.classList.remove('d-inline-flex');
            }
        }

        // Check for completed transitions to trigger toast & reload sidebar
        let hasNewCompletion = false;
        let completedPlaylist = '';

        tasks.forEach(t => {
            const prev = knownTaskStatuses[t.task_id];
            if (prev && prev !== 'completed' && t.status === 'completed') {
                hasNewCompletion = true;
                completedPlaylist = t.playlist_title || 'Media';
            }
            knownTaskStatuses[t.task_id] = t.status;
        });

        if (hasNewCompletion) {
            showDownloadToast('Download Complete!', `"${completedPlaylist}" is ready to play.`, completedPlaylist);
            refreshPlaylistsList();
        }

        renderDownloadTasksList(tasks);

        // Adjust polling frequency based on activity
        if (activeCount > 0) {
            startDownloadPolling(2000);
        } else {
            const modalEl = document.getElementById('downloadModal');
            if (modalEl && modalEl.classList.contains('show')) {
                startDownloadPolling(5000);
            } else {
                stopDownloadPolling();
            }
        }
    } catch (e) {
        console.warn('Error refreshing download tasks:', e);
    }
}

function renderDownloadTasksList(tasks) {
    const container = document.getElementById('download-tasks-container');
    if (!container) return;

    if (!tasks || tasks.length === 0) {
        container.innerHTML = '<div class="text-secondary small py-3 text-center" id="no-tasks-msg">No downloads queued or running. Enter a URL above to start!</div>';
        return;
    }

    let html = '';
    tasks.forEach(t => {
        const isQueued = t.status === 'queued';
        const isInspecting = t.status === 'inspecting';
        const isDownloading = t.status === 'downloading';
        const isTranscribing = t.status === 'transcribing';
        const isCompleted = t.status === 'completed';
        const isCancelled = t.status === 'cancelled';
        const isError = t.status === 'error';

        let badgeClass = 'badge-downloading';
        let badgeLabel = 'Downloading';
        let progressPercent = Math.min(100, Math.max(0, t.percent || 0));
        let isStriped = false;

        if (isQueued) {
            badgeClass = 'badge-cancelled';
            badgeLabel = 'Queued';
            progressPercent = 5;
            isStriped = true;
        } else if (isInspecting) {
            badgeClass = 'badge-downloading';
            badgeLabel = 'Inspecting URL';
            progressPercent = 15;
            isStriped = true;
        } else if (isTranscribing) {
            badgeClass = 'badge-transcribing';
            badgeLabel = 'Transcribing AI';
            progressPercent = 100;
            isStriped = true;
        } else if (isCompleted) {
            badgeClass = 'badge-completed';
            badgeLabel = 'Completed';
            progressPercent = 100;
            isStriped = false;
        } else if (isError) {
            badgeClass = 'badge-error';
            badgeLabel = 'Error';
            progressPercent = 100;
            isStriped = false;
        } else if (isCancelled) {
            badgeClass = 'badge-cancelled';
            badgeLabel = 'Cancelled';
            isStriped = false;
        }

        const titleText = t.playlist_title || t.current_track || t.url || 'Download Task';
        const trackDetail = t.current_track ? (t.total_tracks > 1 ? `Track ${t.track_index || 1}/${t.total_tracks}: ${t.current_track}` : t.current_track) : '';

        html += `
        <div class="download-task-card mb-2" id="task-${escapeHTML(t.task_id)}">
            <div class="download-task-header">
                <span class="download-task-title" title="${escapeHTML(titleText)}">${escapeHTML(titleText)}</span>
                <span class="badge-status ${badgeClass}">${badgeLabel}</span>
            </div>

            <div class="download-progress-track">
                <div class="download-progress-bar ${isStriped ? 'striped' : ''}" style="width: ${progressPercent}%;"></div>
            </div>

            <div class="download-task-meta">
                <div class="d-flex align-items-center gap-2 text-truncate" style="max-width: 75%;">
                    <span class="text-truncate">${escapeHTML(trackDetail || t.url)}</span>
                    ${t.speed ? `<span class="opacity-75">• ${escapeHTML(t.speed)}</span>` : ''}
                    ${t.eta ? `<span class="opacity-75">• ETA ${escapeHTML(String(t.eta))}s</span>` : ''}
                </div>
                <div>
                    ${(isDownloading || isInspecting || isQueued || isTranscribing) ? `
                        <button class="btn btn-sm btn-outline-danger py-0 px-2 small" onclick="cancelDownloadTask('${escapeHTML(t.task_id)}')">
                            Cancel
                        </button>
                    ` : isCompleted && t.playlist_title ? `
                        <button class="btn btn-sm btn-outline-info py-0 px-2 small" onclick="loadDownloadedPlaylist('${escapeHTML(t.playlist_title)}')">
                            <i class="bi bi-play-fill"></i> Load
                        </button>
                    ` : ''}
                </div>
            </div>

            ${isError && t.error_message ? `
                <div class="text-danger small mt-1 text-break">
                    <i class="bi bi-exclamation-triangle-fill me-1"></i>${escapeHTML(t.error_message)}
                </div>
            ` : ''}
        </div>
        `;
    });

    container.innerHTML = html;
}

async function cancelDownloadTask(taskId) {
    if (!taskId) return;
    try {
        const res = await fetch(`/api/download/cancel/${encodeURIComponent(taskId)}`, { method: 'POST' });
        if (res.ok) {
            await refreshDownloadTasks();
        }
    } catch (e) {
        console.warn('Error cancelling task:', e);
    }
}

function loadDownloadedPlaylist(playlistTitle) {
    if (!playlistTitle) return;
    const modalEl = document.getElementById('downloadModal');
    if (modalEl) {
        const modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) modalInstance.hide();
    }
    const offcanvasEl = document.getElementById('mainMenuOffcanvas');
    if (offcanvasEl) {
        const offcanvasInstance = bootstrap.Offcanvas.getInstance(offcanvasEl);
        if (offcanvasInstance) offcanvasInstance.hide();
    }
    loadFeed(`${playlistTitle}.xml`);
}

async function refreshPlaylistsList() {
    try {
        const res = await fetch('/api/playlists');
        if (!res.ok) return;
        const data = await res.json();
        const playlistGroups = data.playlists || [];

        const playlistsUl = document.getElementById('playlists');
        if (!playlistsUl) return;

        let html = '';
        playlistGroups.forEach((group, groupIdx) => {
            const groupId = `dir_collapse_${groupIdx + 1}`;
            html += `
            <li class="mt-2 mb-1 border-0">
                <a
                    class="text-decoration-none app-group-link fw-bold d-inline-flex align-items-center gap-2 cursor-pointer"
                    data-bs-toggle="collapse"
                    href="#${groupId}"
                    role="button"
                    aria-expanded="false"
                    aria-controls="${groupId}"
                >
                    <i class="bi bi-folder-fill app-folder-icon"></i>
                    <span>${escapeHTML(group.label || 'Group')}</span>
                    <i class="bi bi-chevron-down ms-1 small opacity-75"></i>
                </a>
            </li>
            <li class="collapse border-0" id="${groupId}">
                <ul class="list-unstyled ps-3 app-group-children ms-2">
            `;

            (group.feed_items || []).forEach(item => {
                html += `
                <li class="d-flex justify-content-between align-items-center py-1 rounded">
                    <a
                        href="#"
                        class="${escapeHTML(group.tag || 'playlist')} feed-link text-decoration-none d-inline-flex align-items-center gap-2 w-100"
                        data-xml="${escapeHTML(item.xml)}"
                    >
                        <i class="bi bi-rss app-accent-icon"></i>
                        <span class="text-break">${escapeHTML(item.label || item.xml)}</span>
                    </a>
                </li>
                `;
            });

            html += `
                </ul>
            </li>
            `;
        });

        playlistsUl.innerHTML = html;
    } catch (e) {
        console.warn('Error refreshing playlists tree:', e);
    }
}

function showDownloadToast(title, message, playlistTitle) {
    const toastEl = document.getElementById('downloadToast');
    if (!toastEl) return;

    const titleEl = document.getElementById('toast-title');
    const msgEl = document.getElementById('toast-message');
    const loadBtn = document.getElementById('toast-load-btn');

    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.textContent = message;

    if (loadBtn && playlistTitle) {
        loadBtn.classList.remove('d-none');
        loadBtn.onclick = function() {
            loadDownloadedPlaylist(playlistTitle);
        };
    } else if (loadBtn) {
        loadBtn.classList.add('d-none');
    }

    const toast = new bootstrap.Toast(toastEl, { delay: 8000 });
    toast.show();
}

function startDownloadPolling(intervalMs = 2500) {
    if (downloadPollTimer) clearInterval(downloadPollTimer);
    downloadPollTimer = setInterval(refreshDownloadTasks, intervalMs);
}

function stopDownloadPolling() {
    if (downloadPollTimer) {
        clearInterval(downloadPollTimer);
        downloadPollTimer = null;
    }
}
