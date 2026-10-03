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

fn return_to_launcher(app: &tauri::AppHandle) {
    if let Ok(mut state) = app.state::<ClientState>().0.lock() {
        state.selected = None;
        state.generation += 1;
        state.loaded = false;
    }
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
    state.generation += 1;
    state.selected = None;
    drop(state);
    // Explicit user action also clears this app's webview cookies/cache; no cookie is inspected.
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
    // A response cannot gain native privileges, and a stale probe cannot overwrite a newer choice.
    require_launcher(&window)?;
    {
        let handle = app.state::<ClientState>();
        let mut state = handle.0.lock().map_err(|_| "连接状态不可用")?;
        if state.generation != generation { return Err("连接请求已被取消，请重新连接".into()); }
        let path = config_path(&app)?;
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
        return_to_launcher(&app);
        return Err("无法打开服务器页面".into());
    }
    let timeout_app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(20)).await;
        let timed_out = {
            let handle = timeout_app.state::<ClientState>();
            let state = handle.0.lock();
            state.map(|s| s.generation == generation && !s.loaded && s.selected.is_some()).unwrap_or(false)
        };
        if timed_out { return_to_launcher(&timeout_app); }
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
                        let _ = navigate_app.run_on_main_thread(move || return_to_launcher(&back_app));
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
                    if event.id().as_ref() == "connection" { return_to_launcher(app); }
                });
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("native application startup failed");
}
