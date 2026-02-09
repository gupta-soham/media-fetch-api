# Changelog

All notable changes to the Media Fetch API are documented here.

---

## 2026-02-07

### YouTube extraction and download

- **InnerTube response handling**  
  InnerTube API responses are now decoded and parsed in a robust way:
  - Response body is decoded as UTF-8 with `errors="replace"` so invalid bytes do not raise; then parsed as JSON with explicit error handling.
  - Avoids `'utf-8' codec can't decode byte ...` and `Expecting value: line 1 column 1` from breaking the built-in extractor when YouTube returns odd encoding or empty bodies.
  - On any InnerTube/watch-page failure, the download flow still falls back to yt-dlp so downloads can succeed.

- **Download format waterfall (5 levels, 2 runs)**  
  Server-side download (`GET /api/download`) keeps a 5-level format fallback but uses at most **two** yt-dlp runs instead of five:
  - **Run 1**: Single format string `bestvideo+bestaudio/best/best/b/worst` so yt-dlp tries merge → best → b → worst in one process.
  - **Run 2**: If the first run fails with “Requested format is not available” or “Sign in to confirm you're not a bot”, retry with no `-f` (yt-dlp default).
  - Result: same “download in any case” behavior with fewer subprocess runs and less overhead.

---

### YouTube extract flow (with InnerTube decode)

```mermaid
flowchart TD
    Start["extract(video_id)"] --> FetchPage["Fetch /watch?v=ID"]
    FetchPage --> PageOK{"ytInitialPlayerResponse<br/>or ytcfg?"}
    PageOK -->|No| InnerTube
    PageOK -->|Yes| UsePage["Use watch page data"]
    InnerTube["Try InnerTube clients<br/>(web, android_vr, ios, …)"]
    InnerTube --> POST["POST youtubei/v1/player"]
    POST --> Decode["Decode response.content<br/>UTF-8, errors=replace"]
    Decode --> Parse["json.loads(text)<br/>catch JSONDecodeError"]
    Parse --> OK{"status OK?"}
    OK -->|Yes| GotResponse["Player response"]
    OK -->|No| NextClient["Try next client"]
    NextClient --> InnerTube
    GotResponse --> StreamingData["Parse streamingData"]
    UsePage --> StreamingData
    StreamingData --> Formats["Process formats<br/>+ cipher + nsig"]
    Formats --> Response["ExtractResponse"]
```

---

### Server-side download flow (format waterfall)

```mermaid
flowchart LR
    subgraph extract["Extract"]
        A["Extract (e.g. YouTube)"]
        A --> B{"Got formats?"}
    end
    B -->|Yes| C{"Stream OK?"}
    B -->|No| D["yt-dlp fallback"]
    C -->|Yes| E["Stream file"]
    C -->|No / 403| D

    subgraph ytdlp["yt-dlp (max 2 runs)"]
        D --> F["Run 1: -f bv+ba/best/b/worst"]
        F --> G{"Success?"}
        G -->|No| H["Run 2: no -f"]
        G -->|Yes| I["Output file(s)"]
        H --> I
    end

    I --> J{"Merge / re-encode?"}
    J -->|Merge| K["FFmpeg → MP4"]
    J -->|Opus in MP4| L["Re-encode audio → AAC"]
    J -->|No| M["Copy out"]
    K --> M
    L --> M
    M --> E
```

---

### Summary

| Area        | Change |
|------------|--------|
| **YouTube** | InnerTube response: decode UTF-8 with `errors="replace"`, then `json.loads` with try/except so invalid or empty bodies don’t crash the extractor. |
| **Download** | 5-level format waterfall (merge → best → b → worst → no -f) in at most 2 yt-dlp runs; retry once on “Sign in to confirm”. |
