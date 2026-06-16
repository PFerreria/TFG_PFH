import { useState, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";

const HEALTH_URL = "http://127.0.0.1:8000/api/health";
const DASHBOARD_URL = "http://127.0.0.1:8000";
const POLL_INTERVAL_MS = 1000;
const MAX_WAIT_SECS = 120;

function App() {
  const [status, setStatus] = useState("starting"); // "starting" | "opened" | "error"
  const [elapsed, setElapsed] = useState(0);
  const [dots, setDots] = useState("");
  const startedAt = useRef(Date.now());

  useEffect(() => {
    const id = setInterval(() => {
      setDots((d) => (d.length >= 3 ? "" : d + "."));
      setElapsed(Math.round((Date.now() - startedAt.current) / 1000));
    }, 500);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      while (!cancelled) {
        if ((Date.now() - startedAt.current) / 1000 > MAX_WAIT_SECS) {
          if (!cancelled) setStatus("error");
          return;
        }

        try {
          const res = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(2000) });
          if (res.ok) {
            if (!cancelled) {
              // Open the full dashboard in the user's default browser
              await invoke("open_in_browser");
              setStatus("opened");
            }
            return;
          }
        } catch {
          // not ready yet
        }

        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      }
    }

    poll();
    return () => { cancelled = true; };
  }, []);

  async function reopen() {
    try { await invoke("open_in_browser"); } catch { /* ignore */ }
  }

  return (
    <div className="splash">
      <div className="card">
        <div className="logo">
          <div className="logo-gem" />
          <span className="logo-name">IMERS</span>
        </div>

        <p className="subtitle">
          Inteligencia Multiagente para Emergencias y Respuesta Sanitaria
        </p>

        {status === "starting" && (
          <>
            <div className="spinner" />
            <p className="status-text">Iniciando servicio backend{dots}</p>
            <span className="elapsed">{elapsed}s</span>
            {elapsed > 15 && (
              <p className="status-sub">
                La primera ejecución puede tardar 1-2 minutos mientras se prepara el entorno.
              </p>
            )}
          </>
        )}

        {status === "opened" && (
          <>
            <div className="checkmark">✓</div>
            <p className="status-text">Panel abierto en el navegador</p>
            <p className="status-sub">
              El servidor IMERS está activo. Cierra esta ventana para detener el servidor.
            </p>
            <button className="retry-btn" onClick={reopen}>
              Abrir panel de nuevo
            </button>
          </>
        )}

        {status === "error" && (
          <>
            <div className="error-icon">✕</div>
            <p className="status-text error">
              El backend no respondió en {MAX_WAIT_SECS} segundos.
            </p>
            <p className="status-sub">
              Cierra y vuelve a abrir la aplicación, o consulta los logs en<br/>
              <code>%TEMP%\imers-backend-err.log</code>
            </p>
            <button
              className="retry-btn"
              onClick={() => {
                startedAt.current = Date.now();
                setStatus("starting");
                setElapsed(0);
              }}
            >
              Reintentar
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export default App;
