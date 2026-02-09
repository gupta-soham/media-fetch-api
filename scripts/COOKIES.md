# Cookies for Media Fetch API

The API **uses** cookies from `cookies/<service>.json` (Playwright storage state) or `cookies/<service>.txt` (Netscape format). For YouTube, the **server** also passes `cookies/youtube.txt` to yt-dlp when it runs the download (so the client never needs cookies); that gives better quality when the file is present and valid.

Use **Playwright** for an **interactive** browser session to capture cookies (log in in the browser, then save state).

## Using Playwright (interactive)

1. Install Playwright and a browser:
   ```bash
   pip install playwright
   playwright install chromium
   ```

2. Run the capture script from the project root. It opens a browser; you log in, then press Enter in the terminal to save:
   ```bash
   cd /path/to/media-fetch-api
   python scripts/capture_cookies.py https://www.youtube.com cookies/youtube.json
   ```
   When the browser opens, log in to the site. Back in the terminal, press Enter. The script saves cookies (and localStorage) to the given path.

3. Restart or reload the API so it picks up the new file. The API prefers `.json` over `.txt` when both exist.

### Per-platform

| Service   | Login URL                    | Save as                    |
|-----------|-------------------------------|----------------------------|
| YouTube   | `https://www.youtube.com`     | `cookies/youtube.json`     |
| Instagram | `https://www.instagram.com`   | `cookies/instagram.json`   |
| Facebook  | `https://www.facebook.com`    | `cookies/facebook.json`    |
| Twitter/X | `https://x.com`               | `cookies/twitter.json`     |
| TikTok    | `https://www.tiktok.com`      | `cookies/tiktok.json`      |
| Vimeo     | `https://vimeo.com`           | `cookies/vimeo.json`      |

Examples (preset or explicit URL + path):

```bash
python scripts/capture_cookies.py youtube
python scripts/capture_cookies.py facebook
python scripts/capture_cookies.py twitter
# or:
python scripts/capture_cookies.py https://www.youtube.com cookies/youtube.json
# → log in in the browser, then press Enter in the terminal
```

For **Netscape `.txt`** (e.g. for yt-dlp), export from a browser extension (e.g. “cookies.txt”) or use a converter; the API uses `cookies/youtube.txt` for **server-side yt-dlp** when present.

### Verifying YouTube cookies (quality)

- **Are cookies passed?** When a YouTube download runs via yt-dlp, the server logs either:
  - `yt-dlp using cookies: /absolute/path/to/cookies/youtube.txt` → cookies are used (better quality).
  - `yt-dlp running without cookies (file not found: ...)` → cookie file wasn’t found; quality may be limited.
- The path is **resolved to absolute** from `COOKIE_DIR` (default `./cookies`), so the API’s working directory doesn’t affect lookup. If you see “file not found”, set `COOKIE_DIR` in `.env` to the directory that contains `youtube.txt`, or start the API from the project root.
- **Format (Netscape):** One cookie per line: `domain	TRUE/FALSE	path	TRUE/FALSE	expiry	name	value` (tab-separated). Include `.youtube.com` and `.google.com` (and optionally `accounts.google.com`) for a logged-in session.
- **Quality still low?** Export a **fresh** `youtube.txt` while logged into YouTube (browser extension), replace `cookies/youtube.txt` on the server, and restart the API. Stale or invalid cookies can cause yt-dlp to fall back to lower formats.

### Are cookies actually passed?

- **Extractor (YouTube watch page / InnerTube):** The API loads `cookies/youtube.txt` into the cookie manager and sends a `Cookie` header on every request to `youtube.com`. So yes, they are passed.
- **yt-dlp fallback:** The server runs `yt-dlp --cookies /absolute/path/to/cookies/youtube.txt`. yt-dlp reads that Netscape file and uses it for requests to YouTube. So yes, the same file is passed.
- **403 on “fragment” download:** If yt-dlp can list formats but then fails with “HTTP 403” when downloading HLS fragments, the cookies are still being used for the initial YouTube request. The fragment URLs are on `googlevideo.com`; some environments get 403 there (e.g. IP or datacenter blocking). Refreshing cookies and running the API from a residential or previously working network often helps.

## Security

- Do not commit `cookies/*.json` or `cookies/*.txt` if they contain session data.
- Add them to `.gitignore` if the repo is shared.
