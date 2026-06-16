use std::process::Command;
use std::sync::Mutex;
use tauri::Manager;

struct BackendProcess(Mutex<Option<std::process::Child>>);

fn kill_backend(child: &mut std::process::Child) {
    let _ = child.kill();
    let _ = child.wait();
}

/// Open the dashboard in the user's default browser.
/// Using `cmd /C start` avoids needing the opener crate.
#[tauri::command]
fn open_in_browser() -> Result<(), String> {
    Command::new("cmd")
        .args(["/C", "start", "", "http://127.0.0.1:8000"])
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![open_in_browser])
        .setup(|app| {
            let exe_dir = std::env::current_exe()
                .map_err(|e| e.to_string())?
                .parent()
                .ok_or("could not get exe directory")?
                .to_path_buf();

            let backend_exe = exe_dir.join("imers-backend.exe");

            // Remove stale onedir _internal/ from old installations.
            let stale_internal = exe_dir.join("_internal");
            if stale_internal.exists() {
                let _ = std::fs::remove_dir_all(&stale_internal);
            }

            // Kill any orphaned backend from a previous session (crash / force-kill).
            let _ = Command::new("taskkill")
                .args(["/F", "/IM", "imers-backend.exe"])
                .output();
            std::thread::sleep(std::time::Duration::from_millis(800));

            // Persistent extraction cache: PyInstaller onefile extracts here once
            // and reuses it on every subsequent launch.
            let cache_dir = exe_dir.join("backend_cache");
            std::fs::create_dir_all(&cache_dir).ok();

            let log_dir = std::env::temp_dir();
            let log_out = std::fs::File::create(log_dir.join("imers-backend-out.log")).ok();
            let log_err = std::fs::File::create(log_dir.join("imers-backend-err.log")).ok();

            let mut cmd = Command::new(&backend_exe);
            cmd.env("TEMP", &cache_dir).env("TMP", &cache_dir);
            if let Some(f) = log_out { cmd.stdout(std::process::Stdio::from(f)); }
            if let Some(f) = log_err { cmd.stderr(std::process::Stdio::from(f)); }

            let child = cmd.spawn()
                .map_err(|e| format!("Failed to start {:?}: {}", backend_exe, e))?;

            app.manage(BackendProcess(Mutex::new(Some(child))));
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(mut child) = window
                    .app_handle()
                    .state::<BackendProcess>()
                    .0.lock()
                    .unwrap()
                    .take()
                {
                    kill_backend(&mut child);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running IMERS application");
}
