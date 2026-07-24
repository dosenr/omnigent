"""Capture the session-traversal regression through the real local web UI."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
from playwright.sync_api import BrowserType, expect

from tests.e2e_ui import conftest as e2e

_COMPOSER = "Ask the agent anything…"
_KEYHUD = Path(__file__).resolve().parent / "keyhud.js"

# A cold local startup can exceed the suite's 30-second CI-oriented ceiling while
# registering every built-in agent. This changes only the demo harness wait.
e2e._HEALTH_TIMEOUT_S = 60.0


def _set_title(base_url: str, session_id: str, title: str) -> None:
    response = httpx.patch(
        f"{base_url}/v1/sessions/{session_id}",
        json={"title": title},
        timeout=10.0,
    )
    response.raise_for_status()


def _active_element(page) -> str:
    return page.evaluate(
        """() => {
          const el = document.activeElement;
          if (!el) return "none";
          const tag = el.tagName.toLowerCase();
          const placeholder = el.getAttribute?.("placeholder");
          return placeholder ? `${tag}[placeholder="${placeholder}"]` : tag;
        }"""
    )


def test_capture_traversal(
    live_server: str,
    browser_type: BrowserType,
) -> None:
    variant = os.environ["DEMO_VARIANT"]
    build_sha = os.environ["DEMO_SHA"]
    output_dir = Path(os.environ["DEMO_OUTPUT_DIR"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if variant not in {"before", "after"}:
        raise ValueError(f"DEMO_VARIANT must be before or after, got {variant!r}")

    runner_id = str(e2e._server_state["runner_id"])
    sessions = [e2e._create_runner_bound_session(live_server, runner_id) for _ in range(3)]
    traversal_sessions = list(reversed(sessions))
    labels = ["Session A", "Session B", "Session C"]
    by_id = dict(zip(traversal_sessions, labels, strict=True))

    # The local session list shows newest sessions first. Name those IDs in their
    # visible order so both the sidebar and traversal read A, B, C.
    for session_id, title in zip(traversal_sessions, labels, strict=True):
        _set_title(live_server, session_id, title)

    browser = browser_type.launch(headless=False)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(output_dir / "raw-video"),
        record_video_size={"width": 1440, "height": 900},
    )
    context.tracing.start(screenshots=True, snapshots=True, sources=False)
    page = context.new_page()
    page.add_init_script(path=str(_KEYHUD))
    video = page.video
    timeline: list[dict[str, str | int]] = []

    try:
        page.goto(f"{live_server}/inbox")
        for session_id, title in zip(traversal_sessions, labels, strict=True):
            expect(page.locator(f'a[href="/c/{session_id}"]')).to_have_text(title, timeout=30_000)

        page.evaluate(
            "(caption) => window.__keyhudCaption(caption)",
            f"{variant.upper()} {build_sha}",
        )
        page.wait_for_timeout(1_000)

        first_session_id: str | None = None
        visited_session_ids: list[str] = []
        for index in range(1, 4):
            previous_url = page.url
            page.keyboard.press("ControlOrMeta+ArrowDown")
            if index == 1:
                expect(page).to_have_url(
                    re.compile(rf"{re.escape(live_server)}/c/[a-f0-9]+"),
                    timeout=10_000,
                )
            elif variant == "after":
                page.wait_for_function(
                    "(oldUrl) => window.location.href !== oldUrl",
                    previous_url,
                    timeout=10_000,
                )
            else:
                page.wait_for_timeout(500)

            active_session_id = page.url.rsplit("/", 1)[-1]
            assert active_session_id in by_id
            if index == 1:
                first_session_id = active_session_id
                if variant == "before":
                    expect(page.get_by_placeholder(_COMPOSER)).to_be_focused()
            elif variant == "before":
                assert active_session_id == first_session_id
            else:
                assert active_session_id not in visited_session_ids

            active_row = page.locator(f'a[href="/c/{active_session_id}"]')
            expect(active_row).to_be_visible()
            assert "bg-[var(--sidebar-active)]" in (active_row.get_attribute("class") or "")
            visited_session_ids.append(active_session_id)
            timeline.append(
                {
                    "press": index,
                    "active_session": by_id[active_session_id],
                    "focused_element": _active_element(page),
                }
            )
            page.wait_for_timeout(1_000)

        if variant == "after":
            assert len(set(visited_session_ids)) == 3
        page.wait_for_timeout(1_000)
    finally:
        trace_path = output_dir / f"{variant}-{build_sha}.trace.zip"
        context.tracing.stop(path=str(trace_path))
        context.close()
        if video is not None:
            video.save_as(str(output_dir / f"{variant}-{build_sha}.webm"))
        browser.close()
        for session_id in sessions:
            httpx.delete(f"{live_server}/v1/sessions/{session_id}", timeout=10.0)

        (output_dir / f"{variant}-{build_sha}.timeline.json").write_text(
            json.dumps(
                {
                    "variant": variant,
                    "build_sha": build_sha,
                    "browser": browser_type.name,
                    "timeline": timeline,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
