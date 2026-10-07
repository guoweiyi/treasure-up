"""Check the shared Treasure Up brand assets before building a release."""
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import struct


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend/public"
BRAND = PUBLIC / "brand"
NATIVE = ROOT / "native/apple/TreasureUp/Assets.xcassets"
EXPECTED = {
    "logo-mark.png": (1024, 1024, True),
    "logo-lockup.png": (1024, 1024, True),
    "app-icon-192.png": (192, 192, False),
    "app-icon-512.png": (512, 512, False),
    "app-icon-1024.png": (1024, 1024, False),
    "app-icon-maskable-512.png": (512, 512, False),
    "apple-touch-icon.png": (180, 180, False),
    "favicon-32.png": (32, 32, False),
    "social-card.png": (1200, 630, False),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def png_header(path):
    data = path.read_bytes()
    require(len(data) >= 33 and data[:8] == b"\x89PNG\r\n\x1a\n", f"Invalid PNG signature: {path}")
    require(data[8:16] == b"\x00\x00\x00\x0dIHDR", f"Missing PNG IHDR: {path}")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", data[16:29]
    )
    require(width > 0 and height > 0 and depth == 8 and color in {2, 6}, f"Expected 8-bit RGB/RGBA PNG: {path}")
    require(compression == 0 and filtering == 0 and interlace in {0, 1}, f"Invalid PNG IHDR settings: {path}")
    return width, height, color, hashlib.sha256(data).hexdigest()


def public_file(url):
    require(isinstance(url, str) and url.startswith("/") and not url.startswith("//"), f"Expected local public URL: {url}")
    require("?" not in url and "#" not in url, f"Expected an unambiguous public asset URL: {url}")
    path = (PUBLIC / url.lstrip("/")).resolve()
    require(path.is_relative_to(PUBLIC.resolve()) and path.is_file(), f"Public asset missing: {url}")
    return path


class HTMLReferences(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.links = []
        self.images = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "link":
            self.links.append(attributes)
        elif tag == "img":
            self.images.append(attributes)


def check():
    metadata = json.loads((BRAND / "assets.json").read_text(encoding="utf-8"))
    assets = metadata["assets"]
    require(set(EXPECTED).issubset(assets), "Brand asset inventory is incomplete")
    for name, details in assets.items():
        require(Path(name).name == name and name.endswith(".png"), f"Invalid brand asset name: {name}")
        width, height, color, digest = png_header(BRAND / name)
        require((width, height) == (details["width"], details["height"]), f"PNG dimensions differ from inventory: {name}")
        require(isinstance(details["alpha"], bool) and (color == 6) == details["alpha"], f"PNG alpha channel differs from inventory: {name}")
        require(digest == details["sha256"], f"PNG hash differs from inventory: {name}")
        if name in EXPECTED:
            require((width, height, color == 6) == EXPECTED[name], f"Wrong dimensions/alpha for brand role: {name}")
    for role in ("source", "master"):
        path = (ROOT / metadata[role]).resolve()
        require(path.is_relative_to((ROOT / "assets/branding").resolve()), f"Brand {role} must be in assets/branding")
        header = png_header(path)
        if role == "master":
            require(header[2] == 6, "Transparent brand master must declare an alpha channel")

    for folder, filename, web_name in (
        ("BrandMark.imageset", "brand-mark.png", "logo-mark.png"),
        ("BrandLockup.imageset", "brand-lockup.png", "logo-lockup.png"),
        ("AppIcon.appiconset", "AppIcon.png", "app-icon-1024.png"),
    ):
        native = NATIVE / folder
        catalog = json.loads((native / "Contents.json").read_text(encoding="utf-8"))
        require(any(image.get("filename") == filename for image in catalog["images"]), f"Native catalog does not reference {filename}")
        require((native / filename).read_bytes() == (BRAND / web_name).read_bytes(), f"Native and web brand assets differ: {folder}")
    require(png_header(NATIVE / "AppIcon.appiconset/AppIcon.png")[:3] == (1024, 1024, 2), "Native AppIcon must be opaque 1024px RGB")

    manifest = json.loads((PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    icons = manifest["icons"]
    require(len(icons) == 3, "PWA must have separate 192px/512px any icons and a 512px maskable icon")
    for name, size, purpose in (
        ("app-icon-192.png", 192, "any"),
        ("app-icon-512.png", 512, "any"),
        ("app-icon-maskable-512.png", 512, "maskable"),
    ):
        matching = [icon for icon in icons if icon.get("src") == f"/brand/{name}"]
        require(len(matching) == 1, f"PWA icon missing or duplicated: {name}")
        icon = matching[0]
        require(icon.get("sizes") == f"{size}x{size}" and icon.get("purpose") == purpose and icon.get("type") == "image/png", f"Wrong PWA size/type/purpose: {name}")
        public_file(icon["src"])

    index = HTMLReferences((ROOT / "frontend/index.html").read_text(encoding="utf-8"))
    for role in ("icon", "apple-touch-icon"):
        links = [link for link in index.links if role in link.get("rel", "").split()]
        require(links, f"HTML {role} link missing")
        for link in links:
            public_file(link.get("href"))
    offline = HTMLReferences((PUBLIC / "offline.html").read_text(encoding="utf-8"))
    logo_images = [image for image in offline.images if image.get("src") in {f"/brand/{name}" for name in assets if name.startswith(("logo-mark", "logo-lockup"))}]
    require(logo_images, "Offline page must display the public brand logo")
    for image in logo_images:
        public_file(image["src"])

    worker = (PUBLIC / "service-worker.js").read_text(encoding="utf-8")
    declaration = re.search(r"const\s+PUBLIC_FILES\s*=\s*(\[[\s\S]*?\]);", worker)
    require(declaration is not None, "Service worker needs an explicit public asset cache list")
    cached = ast.literal_eval(declaration[1])
    allowed = {"/offline.html", "/favicon.ico", "/app-icon.svg"} | {f"/brand/{name}" for name in assets}
    require(isinstance(cached, list) and cached and all(isinstance(url, str) and url in allowed for url in cached), "Service worker may only cache the offline page and public brand assets")
    require("/offline.html" in cached and any(image["src"] in cached for image in logo_images), "Offline page and its logo must be precached together")
    for url in cached:
        public_file(url)
    require(worker.count("caches.open(") == 1 and "cache.addAll(PUBLIC_FILES)" in worker, "Service worker must only precache the public asset list")
    require(not re.search(r"\bcache\.(?:put|add)\s*\(", worker), "Service worker must not cache responses dynamically")
    require("url.pathname.startsWith('/api/')" in worker and "PUBLIC_FILES.includes(url.pathname)" in worker, "Service worker must exclude API requests and restrict asset fallback to the public list")
    print(f"Branding verified: {len(assets)} PNG assets, native catalogs, PWA icons and public offline cache")


if __name__ == "__main__":
    try:
        check()
    except (ValueError, KeyError, OSError, SyntaxError) as error:
        raise SystemExit(f"Branding check failed: {error}") from error
