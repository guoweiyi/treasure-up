mod policy;

use std::{fs, sync::Mutex, time::Duration};
use serde::{Deserialize, Serialize};
use tauri::{Manager, WebviewUrl, WebviewWindow};
use tauri::webview::{PageLoadEvent, WebviewWindowBuilder};
use url::Url;
use policy::{is_launcher, launcher_url, normalize_origin, same_server, CONNECT_LINK};

#[derive(Default)]
struct Navigation {
    selected: Option<Url>,
    generation: u64,
    loaded: bool,
}

impl Navigation {
    fn reset(&mut self) {
        self.selected = None;
        self.generation += 1;
        self.loaded = false;
    }

    fn expire(&mut self, generation: u64) -> bool {
        if self.generation != generation || self.loaded || self.selected.is_none() {
            return false;
        }
        self.reset();
        true
    }
}

#[derive(Default)]
struct ClientState(Mutex<Navigation>);

#[derive(Serialize, Deserialize)]
struct Connection {
    origin: String,
}

#[derive(Deserialize)]
struct ServerInfo {
    application: String,
    api_version: u32,
}

fn require_launcher(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "main" || !is_launcher(&window.url().map_err(|_| "无法识别当前页面")?) {
        return Err("本地操作仅允许从连接页发起".into());
    }
    Ok(())
}

fn config_path(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    let directory = app.path().app_config_dir().map_err(|_| "无法访问应用配置目录")?;
    fs::create_dir_all(&directory).map_err(|_| "无法创建应用配置目录")?;
    Ok(directory.join("connection.json"))
}

// Call on the UI thread, where navigation commits and page-load events are serialized.
fn return_to_launcher(app: &tauri::AppHandle, timeout_generation: Option<u64>) {
    let handle = app.state::<ClientState>();
    let Ok(mut state) = handle.0.lock() else { return; };
    if let Some(generation) = timeout_generation {
        if !state.expire(generation) { return; }
    } else {
        state.reset();
    }
    // Never hold the state lock across a WebView call: navigation callbacks lock it too.
    drop(state);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.navigate(launcher_url());
    }
}

#[tauri::command]
fn load_connection(app: tauri::AppHandle, window: WebviewWindow) -> Result<Option<Connection>, String> {
    require_launcher(&window)?;
    let path = config_path(&app)?;
    if !path.exists() { return Ok(None); }
    let data = fs::read(&path).map_err(|_| "无法读取连接设置")?;
    if data.len() > 4096 { return Err("连接设置损坏，请清除后重新设置".into()); }
    let stored: Connection = serde_json::from_slice(&data).map_err(|_| "连接设置损坏，请清除后重新设置")?;
    let origin = normalize_origin(&stored.origin)?.origin().ascii_serialization();
    Ok(Some(Connection { origin }))
}

#[tauri::command]
fn forget_connection(app: tauri::AppHandle, window: WebviewWindow) -> Result<(), String> {
    require_launcher(&window)?;
    let handle = app.state::<ClientState>();
    let mut state = handle.0.lock().map_err(|_| "连接状态不可用")?;
    let path = config_path(&app)?;
    if path.exists() { fs::remove_file(path).map_err(|_| "无法清除连接设置")?; }
    state.reset();
    drop(state);
    // Request platform cleanup; Tauri does not expose its asynchronous completion callback.
    // No cookie is inspected, and a successful request is not proof cleanup has finished.
    window.clear_all_browsing_data().map_err(|_| "地址已清除，但系统未能清除浏览器数据")?;
    Ok(())
}

async fn verify_server(origin: &Url) -> Result<(), String> {
    let client = reqwest::Client::builder().timeout(Duration::from_secs(8))
        .redirect(reqwest::redirect::Policy::none()).build().map_err(|_| "无法建立连接")?;
    let endpoint = origin.join("api/v1/server").map_err(|_| "服务器地址无效")?;
    let mut response = client.get(endpoint).header("Accept", "application/json")
        .send().await.map_err(|_| "无法连接服务器，请检查网络、服务地址与 HTTPS 证书")?;
    if !response.status().is_success() || response.content_length().unwrap_or(0) > 16384 {
        return Err("服务器未返回兼容的 Treasure Up 标识；不跟随跳转".into());
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "服务器响应读取失败")? {
        if bytes.len() + chunk.len() > 16384 { return Err("服务器标识响应过大".into()); }
        bytes.extend_from_slice(&chunk);
    }
    let identity: ServerInfo = serde_json::from_slice(&bytes).map_err(|_| "该地址未提供 Treasure Up 服务")?;
    if identity.application != "treasure-up" || identity.api_version != 1 {
        return Err("服务器类型或 API 版本不兼容".into());
    }
    Ok(())
}

fn complete_connection(app: &tauri::AppHandle, window: &WebviewWindow, selected: Url, generation: u64) -> Result<(), String> {
    // Revalidate in the same UI-thread turn as committing the selected origin and navigation.
    // A response cannot gain native privileges, and a stale probe cannot overwrite a newer choice.
    require_launcher(window)?;
    {
        let handle = app.state::<ClientState>();
        let mut state = handle.0.lock().map_err(|_| "连接状态不可用")?;
        if state.generation != generation { return Err("连接请求已被取消，请重新连接".into()); }
        let path = config_path(app)?;
        let temporary = path.with_extension("json.tmp");
        let stored = Connection { origin: selected.origin().ascii_serialization() };
        fs::write(&temporary, serde_json::to_vec(&stored).map_err(|_| "无法保存连接设置")?)
            .map_err(|_| "无法保存连接设置")?;
        fs::rename(temporary, path).map_err(|_| "无法保存连接设置")?;
        state.selected = Some(selected.clone());
        state.loaded = false;
    }
    let mut target = selected;
    target.set_query(Some("client=native"));
    if window.navigate(target).is_err() {
        return_to_launcher(app, None);
        return Err("无法打开服务器页面".into());
    }
    Ok(())
}

#[tauri::command]
async fn connect_server(app: tauri::AppHandle, window: WebviewWindow, origin: String) -> Result<(), String> {
    require_launcher(&window)?;
    let selected = normalize_origin(&origin)?;
    let generation = {
        let handle = app.state::<ClientState>();
        let mut state = handle.0.lock().map_err(|_| "连接状态不可用")?;
        state.generation += 1;
        state.generation
    };
    verify_server(&selected).await?;
    let (result_sender, mut result_receiver) = tauri::async_runtime::channel(1);
    let commit_app = app.clone();
    app.run_on_main_thread(move || {
        let result = complete_connection(&commit_app, &window, selected, generation);
        let _ = result_sender.try_send(result);
    }).map_err(|_| "无法打开服务器页面")?;
    result_receiver.recv().await.ok_or("连接请求已取消")??;
    let timeout_app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(20)).await;
        let recover_app = timeout_app.clone();
        // Check the generation only when the UI task executes, not before queuing it.
        let _ = timeout_app.run_on_main_thread(move || return_to_launcher(&recover_app, Some(generation)));
    });
    Ok(())
}

// Constant, privilege-free recovery hooks. No server response is interpolated into JavaScript.
const VIEWER_GUARD: &str = r#"
(() => {
  if (window !== window.top) return;
  if (location.protocol !== 'https:' && location.protocol !== 'http:') return;
  if (location.hostname === 'tauri.localhost') return;
  const back = () => { location.href = 'https://treasure-up.invalid/connect'; };
  window.addEventListener('offline', back);
  window.addEventListener('keydown', e => { if (e.key === 'Escape' && e.shiftKey) back(); });
  // Desktop also denies native new-window requests. Mobile has no on_new_window API.
  try { Object.defineProperty(window, 'open', {value: () => null, writable: false, configurable: false}); } catch {}
  document.addEventListener('click', e => {
    const a = e.target.closest?.('a[target]');
    if (a && a.target !== '_self') { e.preventDefault(); if (a.href === 'https://treasure-up.invalid/connect') back(); }
  }, true);
})();
"#;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default().manage(ClientState::default())
        .invoke_handler(tauri::generate_handler![load_connection, connect_server, forget_connection])
        .setup(|app| {
            let navigate_app = app.handle().clone();
            let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Treasure Up").inner_size(1180.0, 800.0).min_inner_size(360.0, 560.0)
                .initialization_script(VIEWER_GUARD)
                .on_navigation(move |url| {
                    if url.as_str() == CONNECT_LINK {
                        let back_app = navigate_app.clone();
                        let _ = navigate_app.run_on_main_thread(move || return_to_launcher(&back_app, None));
                        return false;
                    }
                    let handle = navigate_app.state::<ClientState>();
                    let Ok(state) = handle.0.lock() else { return false; };
                    match &state.selected {
                        Some(origin) => same_server(url, origin),
                        None => is_launcher(url),
                    }
                })
                .on_page_load(|window, payload| {
                    if let PageLoadEvent::Finished = payload.event() {
                        let handle = window.state::<ClientState>();
                        if let Ok(mut state) = handle.0.lock() {
                            if state.selected.as_ref().map(|origin| same_server(payload.url(), origin)).unwrap_or(false) {
                                state.loaded = true;
                            }
                        };
                    }
                });
            #[cfg(desktop)]
            let builder = builder.on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny);
            builder.build()?;
            #[cfg(desktop)]
            {
                use tauri::menu::{Menu, MenuItem};
                let connect = MenuItem::with_id(app, "connection", "返回连接页", true, Some("CmdOrCtrl+Shift+C"))?;
                let menu = Menu::with_items(app, &[&connect])?;
                app.set_menu(menu)?;
                app.on_menu_event(|app, event| {
                    if event.id().as_ref() == "connection" { return_to_launcher(app, None); }
                });
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("native application startup failed");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;

    #[test]
    fn delayed_timeout_does_not_cancel_a_new_connection() {
        let mut navigation = Navigation {
            selected: Some(Url::parse("https://old.example").unwrap()),
            generation: 3,
            loaded: false,
        };
        // The old timeout has been queued, then a menu action and new probe win the race.
        navigation.reset();
        navigation.generation += 1;
        let current = Url::parse("https://new.example").unwrap();
        navigation.selected = Some(current.clone());
        assert!(!navigation.expire(3));
        assert_eq!(navigation.selected, Some(current));
        assert_eq!(navigation.generation, 5);
        assert!(navigation.expire(5));
        assert!(navigation.selected.is_none());
        assert_eq!(navigation.generation, 6);
        assert!(!navigation.expire(5));
    }

    #[test]
    fn completed_or_cleared_connection_cannot_expire() {
        let mut navigation = Navigation {
            selected: Some(Url::parse("https://loaded.example").unwrap()),
            generation: 9,
            loaded: true,
        };
        assert!(!navigation.expire(9));
        assert!(navigation.selected.is_some());
        navigation.reset();
        assert!(!navigation.loaded);
        assert!(!navigation.expire(10));
        assert_eq!(navigation.generation, 10);
    }

    fn probe_response(response: String) -> Result<(), String> {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let origin = Url::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
        let server = std::thread::spawn(move || {
            let deadline = std::time::Instant::now() + Duration::from_secs(5);
            let (mut stream, _) = loop {
                match listener.accept() {
                    Ok(connection) => break connection,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        assert!(std::time::Instant::now() < deadline, "identity probe did not connect");
                        std::thread::sleep(Duration::from_millis(10));
                    }
                    Err(error) => panic!("identity fixture failed: {error}"),
                }
            };
            // Darwin can inherit O_NONBLOCK from the listener; fixture reads and writes block.
            stream.set_nonblocking(false).unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
            let mut request = Vec::new();
            let mut buffer = [0; 1024];
            while !request.windows(4).any(|end| end == b"\r\n\r\n") {
                let size = stream.read(&mut buffer).unwrap();
                assert!(size > 0 && request.len() < 16384);
                request.extend_from_slice(&buffer[..size]);
            }
            assert!(request.starts_with(b"GET /api/v1/server HTTP/1.1\r\n"));
            let request = String::from_utf8_lossy(&request).to_ascii_lowercase();
            assert!(!request.contains("\r\ncookie:"));
            assert!(!request.contains("\r\nauthorization:"));
            let _ = stream.write_all(response.as_bytes());
        });
        let result = tauri::async_runtime::block_on(verify_server(&origin));
        server.join().unwrap();
        result
    }

    fn json_response(body: &str) -> String {
        format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body)
    }

    #[test]
    fn identity_probe_rejects_redirects_and_wrong_services_without_credentials() {
        assert!(probe_response(json_response(r#"{"application":"treasure-up","api_version":1}"#)).is_ok());
        for body in [r#"{"application":"other","api_version":1}"#, r#"{"application":"treasure-up","api_version":2}"#, "<html>login</html>"] {
            assert!(probe_response(json_response(body)).is_err());
        }
        // The redirect target is never contacted; the error must be the 302 rejection, not DNS/TLS.
        let redirected = probe_response("HTTP/1.1 302 Found\r\nLocation: https://redirect.invalid\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into());
        assert!(redirected.unwrap_err().contains("不跟随跳转"));
    }

    #[test]
    fn chunked_identity_response_cannot_bypass_the_size_limit() {
        let body = " ".repeat(16385);
        let response = format!("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n{:x}\r\n{}\r\n0\r\n\r\n", body.len(), body);
        assert_eq!(probe_response(response).unwrap_err(), "服务器标识响应过大");
        assert!(probe_response(json_response(&body)).unwrap_err().contains("服务器未返回兼容"));
    }
}
