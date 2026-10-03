fn main() {
    // Application commands otherwise bypass capability filtering by default.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["load_connection", "connect_server", "forget_connection"]),
    )).expect("build native permission manifest");
}
