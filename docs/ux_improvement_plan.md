# Implementation Plan — Visual & UX Improvements

We want to upgrade the visual design and user experience (UX) of the SKC Live Translation web application. The application consists of two main embedded HTML pages inside `app/server.py`:
1. **Attendee Page (`/live`)** — used by congregation members to read live translations.
2. **Operator Page (`/`)** — used by volunteers to control audio capture and monitor Gemini status.

Currently, these pages have basic, generic styles. We will redesign them to feel premium, modern, and highly polished, using rich typography, harmonious color palettes, subtle animations, and glassmorphism elements.

---

## Current Visual Elements

Below are the screenshots captured from the current layout:

### Operator Page (Current)
![Operator Page Current](//localhost/c$/Users/sungk/.gemini/antigravity/brain/f81f7024-ab8f-47c3-8a40-b4e5c00b1429/operator_page_initial_1783721742966.png)

### Attendee Page (Current Gap Issue)
![Attendee Page Gap](//localhost/c$/Users/sungk/.gemini/antigravity/brain/f81f7024-ab8f-47c3-8a40-b4e5c00b1429/attendee_page_captions_1783721799900.png)
> [!IMPORTANT]
> Notice the large empty vertical gap between the older text at the top and the active caption at the bottom. This requires the reader's eyes to jump across the screen.

---

## Proposed Improvements

### 1. Attendee Page (`/live`) Redesign
* **Bulletin Metaphor & Layout**:
  - Warm cream background (`#faf8f5` or `--color-warm-white`).
  - Single-column editorial layout with generous breathing room (padding/margins) to feel like a carefully set printed bulletin.
  - Bottom-aligned caption flow: Align history list items to the bottom (`flex-direction: column; justify-content: flex-end;`). This removes the large vertical gap, placing the active caption and previous text in a continuous, comfortable flow.
  - Generous line height (`line-height: 1.75`) to accommodate dense Hangul characters and clear reading of captions.
* **Church Branding**:
  - Add the PCA logo (`https://starkvillekoreanchurch.org/images/pca-logo-white-small.webp`) and "스탁빌 한인 교회 Starkville Korean Church" in a dignified, scholarly header using `#1a2a42` (`--color-navy-800`).
* **Design Tokens & Color Palette**:
  - Define custom CSS variables on `:root` corresponding to the design reference (e.g. `--color-navy-800: #1a2a42`, `--color-gold-500: #b8923e`, `--color-warm-white: #faf8f5`, etc.).
  - Accent details (buttons, sliders) colored in Gold (`#b8923e` or `#c9a555` on hover).
* **Typography System**:
  - Import Google Fonts (`Noto Serif KR: 600,700`, `Noto Sans KR: 400,500`, `Source Serif 4: 600,700`, `Inter: 400,500`).
  - Use `Source Serif 4` for English headers, `Inter` for English captions (the body), `Noto Serif KR` for Korean headers, and `Noto Sans KR` for Korean body elements.
* **Clean Status Pill**: Replace the tiny colored status dot with a beautiful badge (`● Live`, `● Reconnecting`) matching the church color system.

### 2. Operator Console (`/`) Redesign
* **Unified Theme**: Redesign the console with the same clean, scholarly aesthetic—using `#faf8f5` as the page background, `#1a2a42` for the header, and gold rules (`#d4b872` or `--color-gold-300`) as delimiters instead of heavy box-shadow cards.
* **Symmetrical Controls**: Center the start/pause/stop buttons, rendering them with clean border outlines, gold/navy branding, and smooth hover micro-animations.
* **Unified Audio Playback Control**: Combine the redundant audio speaker icon (`🔊`) and the "Enable" button into a single button. The button will state `🔇 Audio Off` or `🔊 Audio On` depending on state, and reveal the volume slider when active.

---

## Proposed Changes

### [Component Name] FastAPI Server

#### [MODIFY] [server.py](file:///d:/Desktop/church/live_translation/SKC_live_translation_B/app/server.py)
* Refactor the CSS rules in `_ATTENDEE_HTML` and `_OPERATOR_HTML`.
* Update the HTML structures to support the new layouts (e.g. status pill badge, centered controls, and responsive grid columns).
* Modify client-side JavaScript to match updated IDs and classes (keeping core logic exactly identical).

---

## Verification Plan

### Automated/Tool Verification
- Start the server using the `agent` conda environment.
- Run the browser subagent to:
  - Verify that the operator console loads correctly and all buttons are responsive.
  - Verify that the attendee page shows bottom-aligned captions with the new typography.
  - Verify dark and light themes on the attendee page.
  - Capture final screenshots/recordings of the updated pages.

### Manual Verification
- Verify that clicking "Start" on the operator page successfully initiates the Gemini Live session.
- Ensure audio streaming and SSE caption delivery continue working flawlessly.
