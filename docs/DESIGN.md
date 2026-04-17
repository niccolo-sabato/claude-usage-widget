# Design Specification - Claude Usage Widget

## Design Language

Windows 11 Material design: dark theme, rounded corners, translucent background, pill-shaped progress bars, minimal chrome. The visual style mimics a native Windows 11 widget with subtle depth and modern aesthetics.

## Color Palette

### Base Colors

| Name | Variable | Hex | Usage |
|------|----------|:---:|-------|
| Background | `BG` | `#262624` | Window and content background |
| Title Bar BG | `BG_TITLE` | `#1e1e1c` | Title bar background (darker) |
| Bar Background | `BAR_BG` | `#3a3a38` | Progress bar track, separator, hover background |
| Foreground | `FG` | `#e4e4e4` | Primary text color |
| Dim | `DIM` | `#7a7a78` | Secondary text, inactive button icons |
| Menu BG | `MENU_BG` | `#2c2c2a` | Dropdown menu background |
| Hover BG | `HOVER_BG` | `#3a3a38` | Button and menu item hover state |

### Accent Colors

| Name | Variable | Hex | Usage |
|------|----------|:---:|-------|
| Claude Orange | `CLAUDE` | `#DA7756` | Session bar accent, title bar icon color, save button BG |
| Red | `RED` | `#E85858` | Critical usage (>= 90%), error text, close button hover |
| Orange | `ORANGE` | `#E8A838` | Warning usage (>= 75%) |
| Blue | `BLUE` | `#5B9BD5` | Weekly bar accent, refresh button hover |
| Purple | `PURPLE` | `#9B72CF` | Sonnet bar accent |
| Ochre | `OCHRE` | `#C8962A` | Resize dot default color |

### Dot Colors (Expand/Resize Controls)

| Name | Variable | Hex | Usage |
|------|----------|:---:|-------|
| Dot White | `DOT_W` | `#d0d0d0` | Expand dot when expanded (active) |
| Dot White Hover | `DOT_W_H` | `#ffffff` | Expand dot on hover |
| Dot White Dim | `DOT_W_D` | `#a0a09e` | Expand dot when collapsed (inactive) |
| Percentage FG | `PCT_FG` | `#ffffff` | Percentage text inside bar (essential mode) |
| Resize Dot Hover | - | `#E06030` | Ochre dot on mouse enter |

### Save Button States

| State | BG Color | FG Color |
|-------|:--------:|:--------:|
| Normal | `#DA7756` (CLAUDE) | `#262624` (BG) |
| Hover | `#E08060` | `#262624` (BG) |

## Typography

All fonts use the **Segoe UI** family (Windows system font).

| Variable | Font Definition | Usage |
|----------|-----------------|-------|
| `FT` | Segoe UI, 9 | Standard text, section labels, menu items |
| `FT_B` | Segoe UI, 9, **bold** | Title text, percentage labels, dialog titles, save button |
| `FT_S` | Segoe UI, 8 | Sub-labels (reset time), timestamp, instructions, entry fields |
| `FT_BTN` | Segoe UI, 11 | Refresh button (↻) |
| `FT_DOT` | Segoe UI, 10 | Expand/resize dots (●) |
| `FT_BAR` | Segoe UI, 9, **bold** | Percentage text inside bar (essential mode) |

### Special Font Usages

| Element | Font | Size |
|---------|------|:----:|
| Hamburger menu (≡) | Segoe UI | 12 |
| Close button (✕) | Segoe UI | 10 |
| Title bar icon fallback (✱) | Segoe UI | 11 |
| Essential refresh (↻) | Segoe UI | 9 |
| Essential close (✕) | Segoe UI | 9 |

## Layout Dimensions

### Window

| Property | Value | Notes |
|----------|:-----:|-------|
| Default width | 280px | `DEF_W` |
| Minimum width | 260px | `MIN_W` |
| Minimum height (essential) | 46px | `MIN_H_E` |
| Minimum height (normal) | 90px | `MIN_H_N` |
| Window opacity | 0.94 | 6% transparency |
| Corner style | Rounded | DWM attribute 33, value 2 (DWMWCP_ROUND) |
| Shadow | Removed | CS_DROPSHADOW cleared, DWM frame margins zeroed |

### Title Bar

| Property | Value |
|----------|:-----:|
| Height | 28px (`TITLE_H`) |
| Background | `#1e1e1c` |
| Icon left padding | 6px (padx=(6, 0)) |
| Title left padding | 2px (padx=(2, 0)) |
| Close button right padding | 2px (padx=(0, 2)) |

### Content Area

| Property | Value |
|----------|:-----:|
| Horizontal padding | 12px (`PAD`) |
| Section top padding | 3px (pady=(3, 0)) |
| Bar top padding | 1px (pady=(1, 0)) |
| Bottom spacer | 6px height |

### Progress Bars

| Property | Value |
|----------|:-----:|
| Height | 16px (`BAR_H`) |
| Shape | Pill (capsule with rounded ends) |
| End cap radius | 8px (BAR_H / 2) |
| Background color | `#3a3a38` |
| Minimum fill width | 16px (= BAR_H, prevents visual artifacts) |
| Border | None (highlightthickness=0, bd=0) |

### Separator

| Property | Value |
|----------|:-----:|
| Height | 1px |
| Color | `#3a3a38` (BAR_BG) |

### Overlay Controls

| Element | Position | Anchor |
|---------|----------|--------|
| Expand dot | x=6, rely=1.0, y=-4 | SW (bottom-left) |
| Resize dot | relx=1.0, x=-6, rely=1.0, y=-4 | SE (bottom-right) |
| Essential time | relx=1.0, x=-20, rely=1.0, y=-5 | SE |
| Essential refresh | relx=1.0, x=-82, rely=1.0, y=-4 | SE |
| Essential close | relx=1.0, x=-97, rely=1.0, y=-4 | SE |

All overlay elements use `place()` (absolute positioning on the `main` frame) rather than `pack()`, ensuring they float above content regardless of layout changes.

## Menu Design

### Dropdown Menu

| Property | Value |
|----------|:-----:|
| Background | `#2c2c2a` |
| Corner style | Rounded (DWM) |
| Item padding | padx=6, pady=4 |
| Item hover BG | `#3a3a38` |
| Separator height | 1px |
| Separator color | `#3a3a38` |
| Separator horizontal padding | 10px |
| Separator vertical padding | 3px |
| Position | Below hamburger button, right-aligned |

### Menu Items

```
  ↻  Aggiorna
  ⇅  Modalita normale / Modalita essential
  ─────────────────────
  ⚒  Rinnova sessione...
  ⚙  Apri config.json
  ─────────────────────
  ✕  Chiudi
```

## Session Renewal Dialog

| Property | Value |
|----------|:-----:|
| Width | 420px |
| Height | 310px |
| Background | `#262624` |
| Corner style | Rounded (DWM) |
| Position | Centered above widget; if off-screen, below widget (10px gap) |

### Dialog Title Bar

| Property | Value |
|----------|:-----:|
| Height | 30px |
| Background | `#1e1e1c` |
| Title | "Rinnova Sessione" (bold) |
| Draggable | Yes |

### Dialog Entry Field

| Property | Value |
|----------|:-----:|
| Font | Segoe UI 8 |
| Background | `#3a3a38` |
| Foreground | `#e4e4e4` |
| Cursor color | `#e4e4e4` (insertbackground) |
| Border | 0 (bd=0) |
| Highlight thickness | 1px |
| Highlight color (focused) | `#DA7756` (CLAUDE) |
| Highlight color (unfocused) | `#7a7a78` (DIM) |
| Internal vertical padding | 4px (ipady) |

### Instruction Steps Styling

- Normal steps: foreground `#e4e4e4` (FG)
- Indented sub-steps (starting with spaces): foreground `#7a7a78` (DIM)

## Visual States

### Progress Bar States

| State | Foreground Color | Percentage Label Color |
|-------|:----------------:|:----------------------:|
| 0% | No fill drawn | accent color, shows "0%" |
| 1-74% | Accent color | Accent color |
| 75-89% | `#E8A838` (ORANGE) | `#E8A838` |
| 90-100% | `#E85858` (RED) | `#E85858` |
| Not available | No fill | `#7a7a78` (DIM), shows "N/D" |

### Button Hover States

| Button | Default FG | Hover FG | Hover BG |
|--------|:----------:|:--------:|:--------:|
| Close (✕) | `#7a7a78` | `#E85858` (RED) | `#3a3a38` |
| Refresh (↻) | `#7a7a78` | `#5B9BD5` (BLUE) | - |
| Hamburger (≡) | `#7a7a78` | `#e4e4e4` (FG) | - |
| Expand dot (●) | `#a0a09e` / `#d0d0d0` | `#ffffff` | - |
| Resize dot (●) | `#C8962A` | `#E06030` | - |

### Expand Dot State

| State | Color |
|-------|:-----:|
| Collapsed (inactive) | `#a0a09e` (DOT_W_D) |
| Expanded (active) | `#d0d0d0` (DOT_W) |
| Hover (either state) | `#ffffff` (DOT_W_H) |

### Time Label States

| State | Text | Color |
|-------|------|:-----:|
| Loading | `•••` | `#7a7a78` |
| Counting down | `HH:MM (Ns)` | `#7a7a78` |
| Countdown done | `HH:MM` | `#7a7a78` |
| Error | `errore` / `err` (essential) | `#7a7a78` |

## Normal Mode Layout

```
┌──────────────────────────────────────┐
│ [icon] Claude Usage    14:30 (287s) ↻ ≡ ✕ │  <- Title bar (28px, #1e1e1c)
├──────────────────────────────────────┤  <- Separator (1px)
│                                      │
│ Sessione Corrente              54%   │  <- Header row
│ [████████████████░░░░░░░░░░░░░░░░░]  │  <- Pill bar (16px)
│ alle 18:00 (tra 3h 26min)           │  <- Sub-label
│                                      │
│ ●                                  ● │  <- Dots (expand left, resize right)
└──────────────────────────────────────┘
```

## Normal Mode Expanded Layout

```
┌──────────────────────────────────────┐
│ [icon] Claude Usage    14:30 (287s) ↻ ≡ ✕ │
├──────────────────────────────────────┤
│ Sessione Corrente              54%   │
│ [████████████████░░░░░░░░░░░░░░░░░]  │
│ alle 18:00 (tra 3h 26min)           │
│                                      │
│ Tutti i modelli (7gg)          78%   │
│ [████████████████████████░░░░░░░░░]  │
│ lun 00:00 (3gg 10h)                 │
│                                      │
│ Solo Sonnet (7gg)               0%   │
│ [░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░]  │
│ non utilizzato                       │
│                                      │
│ ●                                  ● │
└──────────────────────────────────────┘
```

## Essential Mode Layout

```
┌──────────────────────────────────────┐
│ [████████████ 54% ░░░░░░░░░░░░░░░]  │  <- Compact bar with % inside
│                        ✕ ↻ 14:30  ● │  <- Controls at bottom-right
└──────────────────────────────────────┘
```

Essential mode minimum: 260px wide x 46px tall.

## Pill Bar Rendering

The pill shape is drawn using three canvas primitives:

```
1. Left oval:   create_oval(x, y, x+h, y+h)
2. Right oval:  create_oval(x+w-h, y, x+w, y+h)
3. Center rect: create_rectangle(x+r, y, x+w-r, y+h)
```

Where `r = h/2` (radius = half the bar height). The `outline` parameter is set equal to `fill` to prevent visible seams between shapes. The center rectangle is only drawn when `w > h` (bar wider than its height).

## Multi-Monitor Support

The widget uses `winfo_vrootwidth()` and `winfo_vrootheight()` for virtual screen dimensions, allowing placement across multiple monitors including negative coordinates (monitors to the left/above the primary). Bounds checking ensures at least 50px of the widget remains visible horizontally and vertically.

## DWM Integration

### Rounded Corners

Applied via `DwmSetWindowAttribute`:
- Attribute: 33 (`DWMWA_WINDOW_CORNER_PREFERENCE`)
- Value: 2 (`DWMWCP_ROUND`)

Applied to: main window (without shadow), menu Toplevels (with shadow), session dialog (with shadow).

### Shadow Removal (main window only)

Three-step process:
1. Remove `CS_DROPSHADOW` (0x00020000) from window class style via `SetClassLongPtrW`
2. Zero DWM frame margins via `DwmExtendFrameIntoClientArea(hwnd, MARGINS(0,0,0,0))`
3. Only on the main widget window; dropdown menus and dialogs retain default shadow
