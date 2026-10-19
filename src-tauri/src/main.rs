#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::{
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::Mutex,
};
use tauri::Manager;

struct Core {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}
impl Drop for Core {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
struct Backend(Mutex<Option<Core>>);

fn spawn_core(app: &tauri::AppHandle) -> Result<Core, String> {
    let mut command;
    if cfg!(debug_assertions) {
        command = Command::new("python");
        command.args(["-u", "-m", "mmtr_editor.rpc"]);
        command.current_dir(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap(),
        );
    } else {
        let path = app
            .path()
            .resource_dir()
            .map_err(|e| e.to_string())?
            .join("mmtr-core.exe");
        command = Command::new(path);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("无法启动 MMTR 核心：{e}"))?;
    let input = child.stdin.take().ok_or("Core stdin unavailable")?;
    let output = BufReader::new(child.stdout.take().ok_or("Core stdout unavailable")?);
    Ok(Core {
        child,
        input,
        output,
    })
}

#[tauri::command]
async fn core_request(app: tauri::AppHandle, payload: Value) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let backend = app.state::<Backend>();
        let mut guard = backend.0.lock().map_err(|_| "Core lock poisoned")?;
        if guard.is_none() {
            *guard = Some(spawn_core(&app)?);
        }
        let exchange = (|| -> Result<String, String> {
            let core = guard.as_mut().unwrap();
            writeln!(core.input, "{}", payload).map_err(|e| e.to_string())?;
            core.input.flush().map_err(|e| e.to_string())?;
            let mut line = String::new();
            if core
                .output
                .read_line(&mut line)
                .map_err(|e| e.to_string())?
                == 0
            {
                return Err("MMTR 核心已退出，请重新打开文件。".to_string());
            }
            Ok(line)
        })();
        let line = match exchange {
            Ok(line) => line,
            Err(error) => {
                *guard = None;
                return Err(error);
            }
        };
        serde_json::from_str(&line).map_err(|e| format!("Core response: {e}"))
    })
    .await
    .map_err(|e| e.to_string())?
}

fn main() {
    tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            if std::env::var("MMTR_HEADLESS_SMOKE").as_deref() != Ok("1") {
                if let Some(window) = app.get_webview_window("main") {
                    window.show()?;
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![core_request])
        .run(tauri::generate_context!())
        .expect("启动 MMTR Workbench 失败");
}
