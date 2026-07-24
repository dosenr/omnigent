# Session traversal demo assets

These files support the before and after product demo for session traversal from an empty focused
composer.

- `before-db110815.gif` and `after-d5f7480a.gif` are the published recordings.
- `before-db110815-grid-stripped.png` and `after-d5f7480a-grid-stripped.png` are still-image
  summaries of those recordings.
- `test_capture.py` starts the real local application, creates three synthetic sessions, injects
  the display-only keystroke HUD, and records the browser.
- `keyhud.js` renders keydown events observed by the page.
- `render_media.py` converts a WebM recording into a GIF and still grid.

The published GIFs were recorded in the default manual mode. Playwright prepared and recorded the
headed browser, while the operator physically pressed Command+Down three times. The
`--automated-smoke` mode injects keys into a headless browser to verify the capture harness, but it
does not reproduce the physical-key focus behavior on macOS.

Run both commands from the root of the Omnigent checkout under test, using that checkout's Python
environment:

```bash
DEMO_VARIANT=after DEMO_SHA="$(git rev-parse --short HEAD)" DEMO_OUTPUT_DIR=/tmp/session-demo \
  .venv/bin/python /path/to/assets/test_capture.py

DEMO_VARIANT=after DEMO_SHA="$(git rev-parse --short HEAD)" DEMO_OUTPUT_DIR=/tmp/session-smoke \
  .venv/bin/python /path/to/assets/test_capture.py --automated-smoke
```
