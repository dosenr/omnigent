// keyhud.js - KeyCastr-style keystroke HUD for automated browser captures.
//
// Inject via Playwright BEFORE any keys are sent (both work; addInitScript
// survives navigation):
//   await page.addInitScript({ path: "keyhud.js" });   // or
//   await page.evaluate(fs.readFileSync("keyhud.js", "utf8"));
//
// Evidence property: chips render from real `keydown` events observed in the
// page (capture phase, display-only, never preventDefault), so the HUD shows
// what the page actually received, not what the driving script claims it sent.
// A synthetic caption cannot prove delivery; this can.
//
// Extras:
//   window.__keyhudCaption("text")  - pin a persistent label (e.g. build SHA)
//     in the top-left corner; call again to replace it.
//
// Repeated identical chords collapse into one chip with a multiplier (x2, x3),
// KeyCastr-style, so "pressed three times, nothing moved" stays legible.

(() => {
  if (window.__keyhudInstalled) return;
  window.__keyhudInstalled = true;

  const IS_MAC = /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || "");
  const CHIP_TTL_MS = 1600;

  const KEY_GLYPHS = {
    ArrowUp: "↑",
    ArrowDown: "↓",
    ArrowLeft: "←",
    ArrowRight: "→",
    Enter: "↵",
    Escape: "Esc",
    Backspace: "⌫",
    Tab: "⇥",
    " ": "Space",
  };

  const style = document.createElement("style");
  style.textContent = [
    "#__keyhud{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);",
    "display:flex;gap:8px;z-index:2147483647;pointer-events:none;font-family:ui-monospace,Menlo,monospace;}",
    "#__keyhud .chip{background:rgba(20,20,24,.88);color:#fff;border:1px solid rgba(255,255,255,.35);",
    "border-radius:8px;padding:8px 14px;font-size:20px;line-height:1;box-shadow:0 2px 10px rgba(0,0,0,.4);",
    "transition:opacity .3s;}",
    "#__keyhud .chip .mult{font-size:13px;opacity:.75;margin-left:6px;}",
    "#__keyhudcap{position:fixed;left:12px;top:12px;z-index:2147483647;pointer-events:none;",
    "background:rgba(20,20,24,.88);color:#fff;border-radius:6px;padding:4px 10px;",
    "font:13px ui-monospace,Menlo,monospace;}",
  ].join("");

  const ensure = (parent, id, tag) => {
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement(tag);
      el.id = id;
      parent.appendChild(el);
    }
    return el;
  };

  const install = () => {
    (document.head || document.documentElement).appendChild(style);
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }

  const chordLabel = (e) => {
    const parts = [];
    if (e.metaKey) parts.push("⌘");
    if (e.ctrlKey) parts.push(IS_MAC ? "⌃" : "Ctrl");
    if (e.altKey) parts.push(IS_MAC ? "⌥" : "Alt");
    if (e.shiftKey) parts.push("⇧");
    const key = e.key;
    if (!["Meta", "Control", "Alt", "Shift"].includes(key)) {
      parts.push(KEY_GLYPHS[key] ?? (key.length === 1 ? key.toUpperCase() : key));
    }
    return parts.join(IS_MAC ? "" : "+");
  };

  let lastChip = null;

  window.addEventListener(
    "keydown",
    (e) => {
      const label = chordLabel(e);
      if (!label) return;
      const hud = ensure(document.body || document.documentElement, "__keyhud", "div");
      if (lastChip && lastChip.dataset.label === label && lastChip.isConnected) {
        lastChip.dataset.count = String(Number(lastChip.dataset.count) + 1);
        lastChip.querySelector(".mult").textContent = "×" + lastChip.dataset.count;
        clearTimeout(Number(lastChip.dataset.timer));
      } else {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.dataset.label = label;
        chip.dataset.count = "1";
        chip.innerHTML = "<span class=\"label\"></span><span class=\"mult\"></span>";
        chip.querySelector(".label").textContent = label;
        hud.appendChild(chip);
        lastChip = chip;
      }
      const chip = lastChip;
      chip.dataset.timer = String(
        setTimeout(() => {
          chip.style.opacity = "0";
          setTimeout(() => chip.remove(), 300);
          if (lastChip === chip) lastChip = null;
        }, CHIP_TTL_MS),
      );
    },
    { capture: true },
  );

  window.__keyhudCaption = (text) => {
    const cap = ensure(document.body || document.documentElement, "__keyhudcap", "div");
    cap.textContent = String(text);
  };
})();
