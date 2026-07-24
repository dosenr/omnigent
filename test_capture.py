"""Capture the session-traversal behavior through the real local web UI."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import BrowserType, expect

# Run this file from the root of the Omnigent checkout under test. Adding that
# checkout explicitly also supports invoking this script from an assets folder.
sys.path.insert(0, str(Path.cwd()))

from tests.e2e_ui import conftest as e2e

_KEYHUD = Path(__file__).resolve().parent / "keyhud.js"
_MANUAL_INSTRUCTIONS = (
    "Click once on the Inbox page background (not a text field). "
    "Press Cmd+Down three times, about 1 second apart. Do not click anything else. "
    "Then press Enter in this terminal."
)

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


def _active_element(page: Any) -> str:
    return page.evaluate(
        """() => {
          const el = document.activeElement;
          if (!el) return "none";
          const tag = el.tagName.toLowerCase();
          const placeholder = el.getAttribute?.("placeholder");
          return placeholder ? `${tag}[placeholder="${placeholder}"]` : tag;
        }"""
    )


def _observed_manual_result(page: Any, label_by_id: dict[str, str]) -> dict[str, Any]:
    observed = page.evaluate(
        """() => ({
          chordCount: window.__demoChordCount,
          locations: window.__demoLocations,
          activeElement: (() => {
            const el = document.activeElement;
            if (!el) return "none";
            const tag = el.tagName.toLowerCase();
            const placeholder = el.getAttribute?.("placeholder");
            return placeholder ? `${tag}[placeholder="${placeholder}"]` : tag;
          })(),
        })"""
    )
    observed_ids = [
        location.rsplit("/", 1)[-1]
        for location in observed["locations"]
        if "/c/" in location and location.rsplit("/", 1)[-1] in label_by_id
    ]
    observed_labels = [label_by_id[session_id] for session_id in observed_ids]
    return {
        "chord_count": observed["chordCount"],
        "visited_sessions": observed_labels,
        "distinct_sessions": list(dict.fromkeys(observed_labels)),
        "active_element": observed["activeElement"],
    }


def _validate_manual_result(variant: str, result: dict[str, Any]) -> str | None:
    if result["chord_count"] != 3:
        return f"expected 3 physical chords, observed {result['chord_count']}"
    if variant == "before" and (
        result["distinct_sessions"] != ["Session A", "Session B"]
        or not result["active_element"].startswith("textarea[")
    ):
        return (
            "expected Session A -> Session B followed by an empty-composer freeze, "
            f"observed {result['distinct_sessions']} with focus {result['active_element']}"
        )
    if variant == "after" and result["distinct_sessions"] != [
        "Session A",
        "Session B",
        "Session C",
    ]:
        return f"expected three distinct sessions, observed {result['distinct_sessions']}"
    return None


def _run_automated_smoke(
    page: Any,
    live_server: str,
    label_by_id: dict[str, str],
) -> dict[str, Any]:
    """Exercise the capture harness without claiming the physical-key regression."""
    timeline: list[dict[str, str | int]] = []
    visited_session_ids: list[str] = []

    # CDP-injected keys do not reproduce physical-key focus behavior on macOS.
    # This mode is only a headless smoke of the server, UI, HUD, and recorder.
    for index in range(1, 4):
        previous_url = page.url
        page.keyboard.press("ControlOrMeta+ArrowDown")
        if index == 1:
            expect(page).to_have_url(
                re.compile(rf"{re.escape(live_server)}/c/[a-f0-9]+"),
                timeout=10_000,
            )
        else:
            page.wait_for_function(
                "(oldUrl) => window.location.href !== oldUrl",
                arg=previous_url,
                timeout=10_000,
            )
        active_session_id = page.url.rsplit("/", 1)[-1]
        assert active_session_id in label_by_id

        active_row = page.locator(f'a[href="/c/{active_session_id}"]')
        expect(active_row).to_be_visible()
        assert "bg-[var(--sidebar-active)]" in (active_row.get_attribute("class") or "")

        visited_session_ids.append(active_session_id)
        timeline.append(
            {
                "press": index,
                "active_session": label_by_id[active_session_id],
                "focused_element": _active_element(page),
            }
        )
        page.wait_for_timeout(500)

    assert len(set(visited_session_ids)) == 3
    return {"timeline": timeline}


def test_capture_traversal(
    live_server: str,
    browser_type: BrowserType,
) -> None:
    variant = os.environ["DEMO_VARIANT"]
    build_sha = os.environ["DEMO_SHA"]
    output_dir = Path(os.environ["DEMO_OUTPUT_DIR"]).resolve()
    automated_smoke = os.environ.get("DEMO_AUTOMATED_SMOKE") == "1"
    mode = "automated-smoke" if automated_smoke else "manual"
    output_dir.mkdir(parents=True, exist_ok=True)
    if variant not in {"before", "after"}:
        raise ValueError(f"DEMO_VARIANT must be before or after, got {variant!r}")

    runner_id = str(e2e._server_state["runner_id"])
    sessions = [e2e._create_runner_bound_session(live_server, runner_id) for _ in range(3)]
    traversal_sessions = list(reversed(sessions))
    labels = ["Session A", "Session B", "Session C"]
    label_by_id = dict(zip(traversal_sessions, labels, strict=True))

    # The local session list shows newest sessions first. Name those IDs in their
    # visible order so both the sidebar and traversal read A, B, C.
    for session_id, title in zip(traversal_sessions, labels, strict=True):
        _set_title(live_server, session_id, title)

    browser = browser_type.launch(headless=automated_smoke)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(output_dir / "raw-video"),
        record_video_size={"width": 1440, "height": 900},
    )
    page = context.new_page()
    page.add_init_script(path=str(_KEYHUD))
    if not automated_smoke:
        page.add_init_script(
            script="""
              (() => {
                window.__demoLocations = [];
                window.__demoChordCount = 0;
                let lastHref = window.location.href;
                const sampleLocation = () => {
                  if (window.location.href !== lastHref) {
                    lastHref = window.location.href;
                    window.__demoLocations.push(lastHref);
                  }
                };
                window.setInterval(sampleLocation, 25);
                window.addEventListener("keydown", (event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === "ArrowDown") {
                    window.__demoChordCount += 1;
                  }
                }, { capture: true });
              })();
            """
        )

    video = page.video
    video_path = output_dir / f"{variant}-{build_sha}.{mode}.webm"
    result_path = output_dir / f"{variant}-{build_sha}.{mode}.json"
    result: dict[str, Any] = {}
    mismatch: str | None = None
    interrupted = False

    try:
        page.goto(f"{live_server}/inbox")
        session_links = page.locator('a[href^="/c/"]')
        expect(session_links).to_have_count(3, timeout=30_000)
        for session_id, title in zip(traversal_sessions, labels, strict=True):
            expect(page.locator(f'a[href="/c/{session_id}"]')).to_have_text(
                title,
                timeout=30_000,
            )

        page.evaluate("(caption) => window.__keyhudCaption(caption)", build_sha)

        if automated_smoke:
            result = _run_automated_smoke(page, live_server, label_by_id)
        else:
            page.bring_to_front()
            print(f"\n{variant.upper()} {build_sha} is ready.")
            input(f"{_MANUAL_INSTRUCTIONS}\n")
            result = _observed_manual_result(page, label_by_id)
            mismatch = _validate_manual_result(variant, result)

        result_path.write_text(
            json.dumps(
                {
                    "variant": variant,
                    "build_sha": build_sha,
                    "browser": browser_type.name,
                    "mode": mode,
                    **result,
                    "outcome": "pass" if mismatch is None else "diagnostic",
                    "mismatch": mismatch,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        # A terminal Ctrl+C also closes Playwright's driver process. Avoid API
        # calls through that dead connection; pytest tears down the subprocesses.
        if not interrupted:
            with suppress(Exception):
                context.close()
            if video is not None:
                with suppress(Exception):
                    video.save_as(str(video_path))
            with suppress(Exception):
                browser.close()
        for session_id in sessions:
            with suppress(httpx.HTTPError):
                httpx.delete(
                    f"{live_server}/v1/sessions/{session_id}",
                    timeout=2.0 if interrupted else 10.0,
                )

    if mismatch is not None:
        raise AssertionError(mismatch)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture the session traversal before/after demo."
    )
    parser.add_argument(
        "--automated-smoke",
        action="store_true",
        help="inject keys headlessly to smoke-test the harness, not the focus regression",
    )
    args = parser.parse_args()

    os.environ["DEMO_AUTOMATED_SMOKE"] = "1" if args.automated_smoke else "0"
    return pytest.main(["-q", "-s", "-p", "tests.e2e_ui.conftest", str(Path(__file__))])


if __name__ == "__main__":
    raise SystemExit(main())
