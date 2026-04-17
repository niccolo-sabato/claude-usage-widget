# Configuration — Claude Usage Widget

## File Location

```
C:\Users\<User>\AppData\Local\Claude Usage\config.json
```

Previously stored beside the script/executable. On first launch after migration, the file is automatically copied from the old location to the new one.

## Format

Standard JSON with 2-space indentation. Written by `json.dump(data, f, indent=2)`.

## Complete Schema

```json
{
  "session_key": "sk-ant-sid02-...",
  "org_id": "35e32db5-a5be-4592-b287-bcc9e4f12768",
  "x": -444,
  "y": 1033,
  "refresh_ms": 300000,
  "width": 341,
  "height": 46,
  "expanded": false,
  "essential": true
}
```

## Fields

### Required (Authentication)

| Field | Type | Description |
|-------|------|-------------|
| `session_key` | `string` | Claude.ai session cookie value. Must start with `sk-ant-`. Obtained from browser DevTools: Application -> Cookies -> `https://claude.ai` -> `sessionKey`. Auto-rotated when the API sends a new key via `Set-Cookie` header. |
| `org_id` | `string` | Claude.ai organization ID (UUID format). Found in the API URL when viewing usage on claude.ai. Sent as both URL path parameter and `lastActiveOrg` cookie. |

### Optional (Refresh)

| Field | Type | Default | Description |
|-------|------|:-------:|-------------|
| `refresh_ms` | `integer` | `300000` | Refresh interval in milliseconds. Default is 5 minutes (300,000ms). Controls both the automatic fetch schedule and the visual countdown timer. |

### Auto-Managed (Geometry)

These fields are automatically written by the widget whenever geometry is saved. They can be manually edited when the widget is not running.

| Field | Type | Default | Description |
|-------|------|:-------:|-------------|
| `x` | `integer` | `100` | Window X position (pixels from left of virtual screen). Can be negative for multi-monitor setups with monitors left of primary. |
| `y` | `integer` | `100` | Window Y position (pixels from top of virtual screen). |
| `width` | `integer` | `280` | Window width in pixels. Minimum enforced: 260px. |
| `height` | `integer` | `0` (auto) | Window height in pixels. If 0 or absent, the widget auto-calculates from content. Minimum enforced: 46px (essential) or 90px (normal). |
| `expanded` | `boolean` | `false` | Whether the weekly/Sonnet extra sections are visible. |
| `essential` | `boolean` | `false` | Whether essential (compact) mode is active. |

## Default Values

When config.json is missing or a field is absent, the following defaults apply:

| Field | Fallback | Source |
|-------|----------|--------|
| `session_key` | — (widget shows error) | None |
| `org_id` | — (widget shows error) | None |
| `refresh_ms` | `300000` | `REFRESH` constant |
| `x` | `100` | `cfg.get('x', 100)` |
| `y` | `100` | `cfg.get('y', 100)` |
| `width` | `280` | `cfg.get('width', DEF_W)` |
| `height` | `0` (auto-calculated) | `cfg.get('height', 0)` |
| `expanded` | `false` | `self._expanded = False` |
| `essential` | `false` | `self._essential = False` |

## Loading

```python
def load_cfg():
    if os.path.exists(CFG):
        with open(CFG, encoding='utf-8') as f:
            return json.load(f)
    return {}
```

- Called once at Widget initialization
- Returns empty dict if file doesn't exist
- No error handling for malformed JSON (will raise and be caught by crash protection)
- The returned dict is stored as `self.cfg` and mutated in-place throughout the widget's lifetime

## Saving

```python
def save_cfg(data):
    with open(CFG, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
```

- Overwrites the entire file atomically (write mode, not append)
- Called from:
  - `_save_geometry()` — position, size, mode state
  - `fetch_usage()` — session key auto-rotation
  - `_session_dialog()` — manual session key renewal
- Encoding: UTF-8
- Indentation: 2 spaces

## Save Triggers

| Trigger | Fields Updated |
|---------|----------------|
| Window drag release | x, y, width, height, expanded, essential |
| Window resize release | x, y, width, height, expanded, essential |
| Successful data fetch | x, y, width, height, expanded, essential |
| Normal quit | x, y, width, height, expanded, essential |
| Signal termination | x, y, width, height, expanded, essential |
| Session key rotation | session_key |
| Manual session renewal | session_key |

## Bounds Validation on Load

When restoring position from config:

```python
# Virtual screen dimensions (multi-monitor)
vw = root.winfo_vrootwidth()
vh = root.winfo_vrootheight()

# At least 50px visible horizontally
if x < -w + 50 or x > vw - 50:
    x = 100

# Not too far above or below screen
if y < -20 or y > vh - 50:
    y = 100
```

No validation on width/height beyond the minimum size constraints enforced by `root.minsize(MIN_W, MIN_H_E)` and the resize handler.

## Example Configurations

### Minimal (first setup)

```json
{
  "session_key": "sk-ant-sid02-...",
  "org_id": "35e32db5-a5be-4592-b287-bcc9e4f12768"
}
```

### Full (after running)

```json
{
  "session_key": "sk-ant-sid02-Z6vOHoQBT-...",
  "org_id": "35e32db5-a5be-4592-b287-bcc9e4f12768",
  "x": -444,
  "y": 1033,
  "refresh_ms": 300000,
  "width": 341,
  "height": 46,
  "expanded": false,
  "essential": true
}
```

### Faster refresh (1 minute)

```json
{
  "session_key": "sk-ant-...",
  "org_id": "...",
  "refresh_ms": 60000
}
```

## Security Notes

- `session_key` is a sensitive credential that grants full access to the Claude.ai account
- The config file is stored in the user's AppData folder (per-user, not world-readable)
- The session key is sent to Claude.ai servers via HTTPS (curl) with a browser-like User-Agent
- The key is NOT encrypted at rest in config.json
- The key value is visible in the log file in error messages but NOT logged on successful fetches
