"""Security headers must survive nginx's all-or-nothing add_header inheritance."""
from pathlib import Path
import re


DEPLOY = Path(__file__).resolve().parents[1]


def test_locations_with_cache_headers_keep_server_security_headers():
    config = (DEPLOY / "nginx.conf").read_text()
    include = "include /etc/nginx/security-headers.conf;"
    assert include in config.split("location", 1)[0]
    locations = re.findall(r"location\s+([^{}]+)\{([^{}]+)\}", config)
    assert locations
    for path, body in locations:
        if "add_header " in body:
            assert include in body, f"{path.strip()} shadows all server security headers"
    headers = (DEPLOY / "security-headers.conf").read_text()
    for name in ("X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options", "Content-Security-Policy"):
        assert re.search(rf"add_header {name}\s+[^\n]+ always;", headers)
    assert "frame-ancestors 'none'" in headers
    dockerfile = (DEPLOY / "Dockerfile.web").read_text()
    assert "COPY deploy/security-headers.conf /etc/nginx/security-headers.conf" in dockerfile
