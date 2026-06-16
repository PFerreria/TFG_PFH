const TARGET_SAMPLE_RATE = 16000;
const BUFFER_SIZE        = 4096;

class AudioCapture {
  constructor(opts = {}) {
    this.wsUrl         = opts.wsUrl         || "ws://localhost:8000/ws/call/audio";
    this.onPartial     = opts.onPartial     || (() => {});
    this.onPreliminary = opts.onPreliminary || (() => {});
    this.onFinal       = opts.onFinal       || (() => {});
    this.onState       = opts.onState       || (() => {});
    this.onError       = opts.onError       || console.error;
    this.onSessionStart= opts.onSessionStart|| (() => {});

    this._ws          = null;
    this._ctx         = null;
    this._stream      = null;
    this._processor   = null;
    this._source      = null;
    this._sessionId   = null;
    this._recording   = false;
    this._nativeSR    = null;
  }

  async start() {
    if (this._recording) {
      console.warn("[AudioCapture] Already recording");
      return;
    }

    this.onState("connecting");

    await this._openWebSocket();

    try {
      this._stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate:       TARGET_SAMPLE_RATE,
          channelCount:     1,
        },
      });
    } catch (err) {
      this._ws && this._ws.close();
      this.onError(`Microphone access denied: ${err.message}`);
      this.onState("error");
      throw err;
    }

    this._ctx    = new AudioContext();
    this._nativeSR = this._ctx.sampleRate;
    this._source = this._ctx.createMediaStreamSource(this._stream);

    this._processor = this._ctx.createScriptProcessor(BUFFER_SIZE, 1, 1);
    this._processor.onaudioprocess = (e) => this._onAudioProcess(e);

    this._source.connect(this._processor);
    this._processor.connect(this._ctx.destination);

    this._recording = true;
    this.onState("recording");
    console.log(
      `[AudioCapture] Recording started — native SR: ${this._nativeSR}Hz, ` +
      `target: ${TARGET_SAMPLE_RATE}Hz, session: ${this._sessionId}`
    );
  }

  stop() {
    if (!this._recording) return;
    this._recording = false;
    this.onState("stopping");

    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({ type: "hangup" }));
    }

    this._cleanup();
    this.onState("idle");
    console.log("[AudioCapture] Recording stopped");
  }

  get sessionId()  { return this._sessionId; }
  get isRecording(){ return this._recording; }

  _openWebSocket() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.wsUrl);
      ws.binaryType = "arraybuffer";

      ws.onopen = () => {
        this._ws = ws;
        console.log(`[AudioCapture] WebSocket connected to ${this.wsUrl}`);
      };

      ws.onmessage = (e) => this._onWsMessage(e, resolve);

      ws.onerror = (err) => {
        this.onError(`WebSocket error: connection to ${this.wsUrl} failed`);
        this.onState("error");
        reject(new Error("WebSocket connection failed"));
      };

      ws.onclose = () => {
        if (this._recording) {
          this.onState("disconnected");
          console.warn("[AudioCapture] WebSocket closed unexpectedly");
        }
      };

      setTimeout(() => {
        if (!this._sessionId) {
          reject(new Error("WebSocket timeout — no session_started received"));
          ws.close();
        }
      }, 10000);
    });
  }

  _onWsMessage(event, resolveConnect) {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }

    switch (msg.type) {
      case "session_started":
        this._sessionId = msg.session_id;
        this.onSessionStart(this._sessionId);
        console.log(`[AudioCapture] Session: ${this._sessionId}`);
        if (resolveConnect) resolveConnect(this._sessionId);
        break;

      case "transcript_partial":
        this.onPartial(msg.text || "");
        break;

      case "pipeline_report":
        if (msg.report_type === "preliminary") {
          console.log("[AudioCapture] Preliminary report received");
          this.onPreliminary(msg.data);
        } else if (msg.report_type === "final") {
          console.log("[AudioCapture] Final report received");
          this.onFinal(msg.data);
          this.onState("complete");
        }
        break;

      case "pong":
        break;

      case "error":
        this.onError(msg.message || "Server error");
        break;

      default:
        console.debug("[AudioCapture] Unknown WS message type:", msg.type);
    }
  }

  _onAudioProcess(event) {
    if (!this._recording) return;
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return;

    const float32 = event.inputBuffer.getChannelData(0);
    const pcm16 = this._toInt16PCM(float32, this._nativeSR, TARGET_SAMPLE_RATE);
    this._ws.send(pcm16.buffer);
  }

  _toInt16PCM(samples, srcRate, dstRate) {
    const ratio     = srcRate / dstRate;
    const outLength = Math.floor(samples.length / ratio);
    const out       = new Int16Array(outLength);

    for (let i = 0; i < outLength; i++) {
      const pos  = i * ratio;
      const idx  = Math.floor(pos);
      const frac = pos - idx;
      const a    = samples[idx]       || 0;
      const b    = samples[idx + 1]   || 0;
      const val  = a + frac * (b - a);
      out[i]     = Math.max(-32768, Math.min(32767, Math.round(val * 32767)));
    }
    return out;
  }

  _cleanup() {
    if (this._processor) { this._processor.disconnect(); this._processor = null; }
    if (this._source)    { this._source.disconnect();    this._source    = null; }
    if (this._ctx)       { this._ctx.close();            this._ctx       = null; }
    if (this._stream)    {
      this._stream.getTracks().forEach(t => t.stop());
      this._stream = null;
    }
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.close();
    }
    this._ws = null;
  }
}

class CallPanel {
  constructor(opts = {}) {
    this._capture = null;
    this._onPrelim   = opts.onPreliminaryReport  || (() => {});
    this._onFinal    = opts.onFinalReport         || (() => {});
    this._onTranscript= opts.onTranscriptUpdate  || (() => {});
    this._state      = "idle";
    this._simInterval = null;
  }

  async answerCall() {
    if (this._state === "recording") return;

    const wsUrl = (location.protocol === "https:" ? "wss://" : "ws://") +
                  location.host + "/ws/call/audio";

    this._capture = new AudioCapture({
      wsUrl,
      onPartial:     (text)   => this._onTranscript(text),
      onPreliminary: (report) => {
        console.log("[CallPanel] Preliminary dispatch received");
        this._onPrelim(report);
      },
      onFinal:       (report) => {
        console.log("[CallPanel] Final report received");
        this._onFinal(report);
        this._state = "complete";
        this._notifyUI();
      },
      onState:       (s)      => { this._state = s; this._notifyUI(); },
      onError:       (msg)    => console.error("[CallPanel]", msg),
    });

    try {
      await this._capture.start();
    } catch (err) {
      console.error("[CallPanel] Could not start audio capture:", err);
      this._state = "error";
      this._notifyUI();
    }
  }

  async simulateCall() {
    if (this._state === "recording") return;

    const wsUrl = (location.protocol === "https:" ? "wss://" : "ws://") +
                  location.host + "/ws/call/audio";

    this._capture = new AudioCapture({
      wsUrl,
      onPartial:     (text)   => this._onTranscript(text),
      onPreliminary: (report) => {
        console.log("[CallPanel] Preliminary dispatch received");
        this._onPrelim(report);
      },
      onFinal:       (report) => {
        console.log("[CallPanel] Final report received");
        this._onFinal(report);
        this._state = "complete";
        this._notifyUI();
      },
      onState:       (s)      => { this._state = s; this._notifyUI(); },
      onError:       (msg)    => console.error("[CallPanel]", msg),
    });

    try {
      this._state = "connecting";
      this._notifyUI();

      await this._capture._openWebSocket();
      this._capture._recording = true;
      this._state = "recording";
      this._notifyUI();

      const speechSteps = [
        "Ha habido",
        "Ha habido un accidente",
        "Ha habido un accidente de tráfico",
        "Ha habido un accidente de tráfico grave",
        "Ha habido un accidente de tráfico grave en la Avenida de la Constitución",
        "Ha habido un accidente de tráfico grave en la Avenida de la Constitución esquina con Calle Sierpes",
        "Ha habido un accidente de tráfico grave en la Avenida de la Constitución esquina con Calle Sierpes. Hay tres heridos y un coche echando humo.",
        "Ha habido un accidente de tráfico grave en la Avenida de la Constitución esquina con Calle Sierpes. Hay tres heridos y un coche echando humo. Por favor envíen una ambulancia de urgencia y a los bomberos."
      ];

      let step = 0;
      this._simInterval = setInterval(() => {
        if (!this._capture || !this._capture._ws || this._capture._ws.readyState !== WebSocket.OPEN) {
          clearInterval(this._simInterval);
          this._simInterval = null;
          return;
        }
        if (step < speechSteps.length) {
          const text = speechSteps[step];
          this._capture._ws.send(JSON.stringify({ type: "simulate_text", text: text }));
          step++;
        } else {
          clearInterval(this._simInterval);
          this._simInterval = null;
          setTimeout(() => this.hangUp(), 1500);
        }
      }, 1500);

    } catch (err) {
      console.error("[CallPanel] Could not start audio simulation:", err);
      this._state = "error";
      this._notifyUI();
    }
  }

  hangUp() {
    if (this._simInterval) {
      clearInterval(this._simInterval);
      this._simInterval = null;
    }
    if (this._capture) {
      this._capture.stop();
      this._capture = null;
    }
    this._state = "hung_up";
    this._notifyUI();
  }

  get state()     { return this._state; }
  get sessionId() { return this._capture ? this._capture.sessionId : null; }

  _notifyUI() {
    document.dispatchEvent(new CustomEvent("callStateChange", {
      detail: { state: this._state, sessionId: this.sessionId }
    }));
  }
}

window.AudioCapture = AudioCapture;
window.CallPanel    = CallPanel;
