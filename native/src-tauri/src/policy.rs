use url::Url;

pub const CONNECT_LINK: &str = "https://treasure-up.invalid/connect";

pub fn normalize_origin(input: &str) -> Result<Url, &'static str> {
    let value = input.trim();
    if value.len() > 2048 || value.chars().any(|c| c.is_control()) || value.contains('\\') {
        return Err("服务器地址包含无效字符");
    }
    let url = Url::parse(value).map_err(|_| "请输入完整服务器地址")?;
    if !matches!(url.scheme(), "https" | "http") || url.host_str().is_none()
        || !url.username().is_empty() || url.password().is_some() || value.contains('@')
        || url.query().is_some() || url.fragment().is_some() || url.path() != "/"
    {
        return Err("只接受站点根地址，不支持凭据、查询参数、片段或反向代理子路径");
    }
    let host = url.host_str().unwrap_or_default();
    if host == "tauri.localhost" || host == "ipc.localhost" || host == "treasure-up.invalid" {
        return Err("该地址是客户端保留地址");
    }
    let loopback = host == "localhost" || match url.host() {
        Some(url::Host::Ipv4(ip)) => ip.is_loopback(),
        Some(url::Host::Ipv6(ip)) => ip.is_loopback(),
        _ => false,
    };
    if url.scheme() == "http" && !loopback {
        return Err("远程服务器必须使用 HTTPS；HTTP 仅用于本机回环开发");
    }
    Ok(url)
}

pub fn same_server(target: &Url, selected: &Url) -> bool {
    matches!(target.scheme(), "http" | "https") && target.origin() == selected.origin()
        && target.username().is_empty() && target.password().is_none()
}

pub fn launcher_url() -> Url {
    #[cfg(any(target_os = "windows", target_os = "android"))]
    let value = "http://tauri.localhost/index.html";
    #[cfg(not(any(target_os = "windows", target_os = "android")))]
    let value = "tauri://localhost/index.html";
    Url::parse(value).expect("constant launcher URL")
}

pub fn is_launcher(url: &Url) -> bool {
    url == &launcher_url()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_https_and_only_loopback_http() {
        for value in ["https://example.com", "https://192.168.1.2:8788/", "http://localhost:8788", "http://127.0.0.1:8788", "http://[::1]:8788"] {
            assert!(normalize_origin(value).is_ok(), "{value}");
        }
        for value in ["http://192.168.1.2:8788", "https://example.com/app", "https://user:pass@example.com", "https://@example.com", "https://example.com?", "https://example.com#", "file:///tmp/a", "javascript:alert(1)", "https://tauri.localhost", "https://ipc.localhost", "http://localhost.example.com", "http://localhost:99999", "https://example.com\\@evil.com"] {
            assert!(normalize_origin(value).is_err(), "{value}");
        }
    }

    #[test]
    fn navigation_is_origin_scoped_and_never_enters_local_trust() {
        let selected = normalize_origin("https://example.com:8443").unwrap();
        assert!(same_server(&Url::parse("https://example.com:8443/player/123?client=native").unwrap(), &selected));
        for target in ["https://example.com", "http://example.com:8443", "https://evil.com", "tauri://localhost/index.html", "http://tauri.localhost/index.html", "https://user@example.com:8443/"] {
            assert!(!same_server(&Url::parse(target).unwrap(), &selected));
        }
        assert!(is_launcher(&launcher_url()));
        assert!(!is_launcher(&Url::parse("https://tauri.localhost/index.html").unwrap()));
    }
}
