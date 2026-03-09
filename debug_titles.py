"""Debug: inspect raw HTML metadata from problem URLs."""

import httpx
import re
import json

urls = [
    ("wallstreetcn", "https://wallstreetcn.com/articles/3766877"),
    ("jin10", "https://go.jin10.com/97hpys"),
    ("futunn", "https://news.futunn.com/post/69729705"),
]

OG_TITLE = re.compile(
    r'''<meta\s[^>]*property\s*=\s*["']og:title["'][^>]*content\s*=\s*["']([^"']+)["']''',
    re.I,
)
OG_TITLE2 = re.compile(
    r'''<meta\s[^>]*content\s*=\s*["']([^"']+)["'][^>]*property\s*=\s*["']og:title["']''',
    re.I,
)
OG_DESC = re.compile(
    r'''<meta\s[^>]*property\s*=\s*["']og:description["'][^>]*content\s*=\s*["']([^"']+)["']''',
    re.I,
)
OG_DESC2 = re.compile(
    r'''<meta\s[^>]*content\s*=\s*["']([^"']+)["'][^>]*property\s*=\s*["']og:description["']''',
    re.I,
)
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.DOTALL)
JSONLD_RE = re.compile(
    r"""<script[^>]*type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""",
    re.I | re.DOTALL,
)

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

for name, url in urls:
    print(f"=== {name} ===")
    print(f"  URL: {url}")
    try:
        with httpx.Client(follow_redirects=True, timeout=15) as c:
            resp = c.get(url, headers=UA)
        html = resp.text[:100_000]

        print(f"  Final URL: {resp.url}")
        print(f"  Status: {resp.status_code}")

        m = TITLE_TAG.search(html)
        print(f"  <title>: {m.group(1).strip()[:100] if m else '(none)'}")

        for pat, label in [(OG_TITLE, "og:title"), (OG_TITLE2, "og:title(v2)")]:
            m = pat.search(html)
            if m:
                print(f"  {label}: {m.group(1)[:100]}")

        for pat, label in [(OG_DESC, "og:desc"), (OG_DESC2, "og:desc(v2)")]:
            m = pat.search(html)
            if m:
                print(f"  {label}: {m.group(1)[:100]}")

        ld_matches = JSONLD_RE.findall(html)
        if ld_matches:
            for i, ld in enumerate(ld_matches):
                try:
                    d = json.loads(ld)
                    print(f"  JSON-LD[{i}]: {json.dumps(d, ensure_ascii=False)[:150]}")
                except Exception:
                    print(f"  JSON-LD[{i}]: (parse error) {ld[:80]}")
        else:
            print("  JSON-LD: (none)")

        # Check for any __NEXT_DATA__ or similar SSR payloads
        for pattern_name, pat in [
            ("__NEXT_DATA__", r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>'),
            ("__NUXT__", r"window\.__NUXT__\s*=\s*(\{.+?\});?\s*</script>"),
            ("initialState", r"window\.__initialState\s*=\s*(\{.+?\});?\s*</script>"),
            ("INITIAL_STATE", r"window\.INITIAL_STATE\s*=\s*(\{.+?\});?\s*</script>"),
        ]:
            m = re.search(pat, html, re.DOTALL)
            if m:
                raw = m.group(1)[:300]
                print(f"  {pattern_name}: found ({len(m.group(1))} chars) preview: {raw[:150]}")

    except Exception as e:
        print(f"  Error: {e}")
    print()
