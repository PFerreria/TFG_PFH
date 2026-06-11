/**
 * js/call_queue.js
 * ----------------
 * IMERS — Call Queue Panel
 *
 * Manages the call queue UI:
 *   - Displays queued items (recordings + live mic sessions)
 *   - Drag-and-drop / click upload of audio files
 *   - Enqueue live mic calls
 *   - Remove items from the queue
 *   - Real-time sync via WebSocket (queue_update, mic_ready, session_ended)
 *
 * Expects these DOM elements (added to index.html):
 *   #queue-panel         — outer container
 *   #queue-list          — scrollable item list
 *   #queue-count         — item count badge
 *   #queue-drop-zone     — drag-and-drop / click upload area
 *   #queue-file-input    — hidden <input type="file">
 *   #queue-add-mic-btn   — "Añadir llamada en vivo" button
 *   #queue-upload-bar    — upload progress bar fill element
 *   #queue-upload-wrap   — upload progress wrapper (shown/hidden)
 */

class CallQueuePanel {
  /**
   * @param {object} opts
   * @param {function} opts.onMicReady  — called when a mic item reaches the front
   */
  constructor(opts = {}) {
    this._onMicReady = opts.onMicReady || (() => {});
    this._items      = [];   // local queue mirror
    this._uploading  = false;

    this._listEl     = document.getElementById("queue-list");
    this._countEl    = document.getElementById("queue-count");
    this._dropZone   = document.getElementById("queue-drop-zone");
    this._fileInput  = document.getElementById("queue-file-input");
    this._addMicBtn  = document.getElementById("queue-add-mic-btn");
    this._uploadBar  = document.getElementById("queue-upload-bar");
    this._uploadWrap = document.getElementById("queue-upload-wrap");

    if (!this._listEl) return;  // panel not present

    this._bindEvents();
    this._fetchQueue();
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  /** Called from app.js when a queue_update WS message arrives. */
  handleQueueUpdate(data) {
    this._items = data.queue || [];
    this._render();

    if (data.started) {
      this._flashItem(data.started.item_id || "", "processing");
    }
  }

  /** Called when mic_ready WS event arrives — notify operator. */
  handleMicReady(data) {
    this._onMicReady(data.item || {});
    this._showToast("🎙 Llamada en vivo lista — pulse Responder");
  }

  /** Called when session_ended WS event arrives. */
  handleSessionEnded(data) {
    this._showToast(`✓ Sesión ${(data.session_id || "").slice(0, 14)} completada`);
  }

  /** Called when a recording session starts processing (recording_started WS event). */
  handleRecordingStarted(data) {
    const label = data.label || "grabación";
  }

  // ── Private: data ───────────────────────────────────────────────────────────

  async _fetchQueue() {
    try {
      const res  = await fetch("/api/call/queue");
      const data = await res.json();
      this._items = data.queue || [];
      this._render();
    } catch (e) {
      console.warn("[CallQueuePanel] Could not fetch queue:", e);
    }
  }

  async _enqueueMic() {
    try {
      const res  = await fetch("/api/call/enqueue", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ source: "mic", label: "Llamada en vivo" }),
      });
      const data = await res.json();
      this._items = data.queue || this._items;
      this._render();
    } catch (e) {
      console.error("[CallQueuePanel] enqueueMic failed:", e);
    }
  }

  async _startItem(itemId) {
    const el = document.getElementById(`qi-${itemId}`);
    if (el) el.classList.add("starting");
    try {
      const res = await fetch(`/api/call/queue/${encodeURIComponent(itemId)}/start`, { method: "POST" });
      if (res.status === 409) {
        this._showToast("Ya hay una sesión activa", true);
        if (el) el.classList.remove("starting");
      } else if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        this._showToast(`✗ Error: ${data.detail || res.status}`, true);
        if (el) el.classList.remove("starting");
      }
      // On success, WS events (recording_started / mic_ready) drive the UI
    } catch (e) {
      console.error("[CallQueuePanel] startItem failed:", e);
      this._showToast("✗ Error de conexión", true);
      if (el) el.classList.remove("starting");
    }
  }

  async _removeItem(itemId) {
    try {
      await fetch(`/api/call/queue/${encodeURIComponent(itemId)}`, { method: "DELETE" });
      // Optimistic update — WS will confirm
      this._items = this._items.filter(i => i.item_id !== itemId);
      this._render();
    } catch (e) {
      console.error("[CallQueuePanel] removeItem failed:", e);
    }
  }

  async _uploadFile(file) {
    if (this._uploading) return;
    this._uploading = true;
    this._setProgress(0);
    this._showProgress(true);

    const form = new FormData();
    form.append("file", file);

    try {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/recording/upload");

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          this._setProgress(e.loaded / e.total);
        }
      };

      await new Promise((resolve, reject) => {
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(JSON.parse(xhr.responseText));
          } else {
            let msg = `HTTP ${xhr.status}`;
            try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (_) {}
            reject(new Error(msg));
          }
        };
        xhr.onerror = () => reject(new Error("Network error"));
        xhr.send(form);
      });

      this._setProgress(1);
      this._showToast(`✓ Grabación encolada: ${file.name}`);

    } catch (e) {
      console.error("[CallQueuePanel] Upload failed:", e);
      this._showToast(`✗ Error al subir: ${e.message}`, true);
    } finally {
      this._uploading = false;
      setTimeout(() => this._showProgress(false), 1200);
    }
  }

  // ── Private: rendering ──────────────────────────────────────────────────────

  _render() {
    if (!this._listEl) return;

    // Update count
    if (this._countEl) {
      this._countEl.textContent = this._items.length || "0";
    }

    if (this._items.length === 0) {
      this._listEl.innerHTML = `<div class="queue-empty">Cola vacía</div>`;
      return;
    }

    this._listEl.innerHTML = this._items.map(item => this._renderItem(item)).join("");

    // Bind remove buttons
    this._listEl.querySelectorAll("[data-remove-id]").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._removeItem(btn.dataset.removeId);
      });
    });

    // Bind item click → start processing
    this._listEl.querySelectorAll(".queue-item[data-item-id]").forEach(el => {
      el.addEventListener("click", () => {
        this._startItem(el.dataset.itemId);
      });
    });
  }

  _renderItem(item) {
    const icon   = item.source === "mic" ? "🎙" : "🎵";
    const label  = _escHtml(item.label || item.item_id);
    const pos    = item.position || "—";
    const ts     = item.queued_at
      ? new Date(item.queued_at).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" })
      : "—";
    const srcTag = item.source === "mic" ? "MIC" : "REC";

    return `
      <div class="queue-item" id="qi-${_escHtml(item.item_id)}" data-item-id="${_escHtml(item.item_id)}">
        <div class="qi-pos">${pos}</div>
        <div class="qi-icon">${icon}</div>
        <div class="qi-body">
          <div class="qi-label" title="${label}">${label}</div>
          <div class="qi-meta">
            <span class="qi-src-tag">${srcTag}</span>
            <span class="qi-time">${ts}</span>
          </div>
        </div>
        <button class="qi-remove" data-remove-id="${_escHtml(item.item_id)}" title="Eliminar">✕</button>
      </div>`;
  }

  _flashItem(itemId, cls) {
    const el = document.getElementById(`qi-${itemId}`);
    if (el) {
      el.classList.add(cls);
      setTimeout(() => el.classList.remove(cls), 1600);
    }
  }

  // ── Private: events ─────────────────────────────────────────────────────────

  _bindEvents() {
    // Add mic button
    if (this._addMicBtn) {
      this._addMicBtn.addEventListener("click", () => this._enqueueMic());
    }

    // Drop zone: click triggers file input
    if (this._dropZone) {
      this._dropZone.addEventListener("click", () => {
        if (!this._uploading) this._fileInput && this._fileInput.click();
      });

      this._dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        this._dropZone.classList.add("drag-over");
      });
      this._dropZone.addEventListener("dragleave", () => {
        this._dropZone.classList.remove("drag-over");
      });
      this._dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        this._dropZone.classList.remove("drag-over");
        const file = e.dataTransfer.files[0];
        if (file) this._uploadFile(file);
      });
    }

    // Hidden file input
    if (this._fileInput) {
      this._fileInput.addEventListener("change", () => {
        const file = this._fileInput.files[0];
        if (file) {
          this._uploadFile(file);
          this._fileInput.value = "";
        }
      });
    }
  }

  // ── Private: UI helpers ─────────────────────────────────────────────────────

  _setProgress(ratio) {
    if (this._uploadBar) {
      this._uploadBar.style.width = `${Math.round(ratio * 100)}%`;
    }
  }

  _showProgress(visible) {
    if (this._uploadWrap) {
      this._uploadWrap.style.display = visible ? "block" : "none";
    }
  }

  _showToast(msg, isError = false) {
    const toast = document.createElement("div");
    toast.className = "queue-toast" + (isError ? " queue-toast-err" : "");
    toast.textContent = msg;
    document.body.appendChild(toast);
    // Animate in
    requestAnimationFrame(() => toast.classList.add("queue-toast-show"));
    setTimeout(() => {
      toast.classList.remove("queue-toast-show");
      setTimeout(() => toast.remove(), 350);
    }, 3000);
  }
}

// ── Util ─────────────────────────────────────────────────────────────────────

function _escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

window.CallQueuePanel = CallQueuePanel;
