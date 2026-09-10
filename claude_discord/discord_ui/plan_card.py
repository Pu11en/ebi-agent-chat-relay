"""Standard "planning card" renderer.

A recurring failure mode when a bot posts a plan as free text: the goal drifts,
the integration with existing code is glossed over, and "done" is fuzzy. This
module gives every plan a fixed four-slot layout — Goal, What Exists, Steps
(each with an outcome), Done When — so the reader always knows where to look.

Usage from a bot: write a ``*.plan.json`` file into ``.ccdb-attachments-*``.
The file_sender pipeline notices the extension, calls
:func:`render_plan_card_to_png`, and Discord shows the rendered card inline.

Schema (all keys required except *notes*)::

    {
      "goal":   "One-sentence outcome the plan is aiming at.",
      "exists": "What already lives in the codebase / system that this plugs into.",
      "steps":  [
        {"do": "Concrete action", "outcome": "What that step produces"},
        ...
      ],
      "done":   "The exact artifact or observable state that means 'shipped'.",
      "notes":  "Optional caveats — shown small at the bottom."
    }
"""

from __future__ import annotations

import json
import logging
from html import escape
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_STEPS = 8


def _card_html(spec: dict) -> str:
    """Fill the fixed template with *spec*.

    Missing keys become visible placeholder rows so the reader (or the bot on
    the next turn) can see exactly which slot the plan skipped — silent
    fallbacks would defeat the whole point of the fixed layout.
    """
    goal = escape(str(spec.get("goal") or "⚠️ goal missing"))
    exists = escape(str(spec.get("exists") or "⚠️ what exists missing"))
    done = escape(str(spec.get("done") or "⚠️ done-when missing"))
    notes = spec.get("notes")

    raw_steps = spec.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        raw_steps = [{"do": "⚠️ no steps provided", "outcome": ""}]
    steps_html = ""
    for i, step in enumerate(raw_steps[:_MAX_STEPS], 1):
        do = escape(str(step.get("do", "")))
        outcome = escape(str(step.get("outcome", "")))
        steps_html += f"""
          <div class="step">
            <div class="num">{i}</div>
            <div class="body">
              <div class="do">{do}</div>
              {f'<div class="outcome">→ {outcome}</div>' if outcome else ''}
            </div>
          </div>"""
    if len(raw_steps) > _MAX_STEPS:
        steps_html += (
            f'<div class="more">+{len(raw_steps) - _MAX_STEPS} more '
            "steps — truncated for the card</div>"
        )

    notes_html = ""
    if notes:
        notes_html = f'<div class="notes">Notes: {escape(str(notes))}</div>'

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  *{{box-sizing:border-box}}
  body{{font-family:-apple-system,Segoe UI,Inter,sans-serif;background:#1e1f22;
       color:#dbdee1;margin:0;padding:0;width:820px}}
  .card{{padding:32px 36px 28px}}
  .tag{{font-size:11px;letter-spacing:2px;color:#949ba4;text-transform:uppercase;
        font-weight:700;margin-bottom:6px}}
  h1{{font-size:26px;line-height:1.25;margin:0 0 24px;color:#f2f3f5;font-weight:700}}
  .row{{display:grid;grid-template-columns:110px 1fr;gap:18px;padding:14px 0;
        border-top:1px solid #2b2d31}}
  .row:first-of-type{{border-top:none}}
  .lbl{{font-size:11px;letter-spacing:1.5px;color:#949ba4;text-transform:uppercase;
        font-weight:700;padding-top:2px}}
  .val{{font-size:15px;line-height:1.5;color:#dbdee1}}
  .val b{{color:#f2f3f5}}
  .step{{display:flex;gap:12px;padding:10px 0}}
  .step:first-child{{padding-top:0}}
  .num{{flex-shrink:0;width:26px;height:26px;background:#5865f2;color:#fff;
        border-radius:50%;display:flex;align-items:center;justify-content:center;
        font-size:13px;font-weight:700}}
  .step .body{{flex:1;padding-top:2px}}
  .do{{font-size:15px;color:#f2f3f5;line-height:1.4}}
  .outcome{{font-size:13px;color:#00d26a;margin-top:2px;line-height:1.4}}
  .more{{font-size:12px;color:#949ba4;font-style:italic;padding:6px 0 0 38px}}
  .done{{background:#1a3a24;border:1px solid #248046;border-radius:8px;padding:14px 18px}}
  .done .lbl{{color:#00d26a}}
  .notes{{font-size:12px;color:#949ba4;padding:14px 0 0;font-style:italic;
          border-top:1px solid #2b2d31;margin-top:14px}}
</style></head><body>
  <div class="card">
    <div class="tag">📋 Plan</div>
    <h1>{goal}</h1>
    <div class="row"><div class="lbl">What exists</div><div class="val">{exists}</div></div>
    <div class="row"><div class="lbl">Steps</div><div class="val">{steps_html}</div></div>
    <div class="row done"><div class="lbl">Done when</div><div class="val">{done}</div></div>
    {notes_html}
  </div>
</body></html>"""


async def render_plan_card_to_png(json_path: Path) -> bytes | None:
    """Load *json_path*, fill the plan-card template, render to PNG.

    Returns None on any failure (bad JSON, missing renderer, chromium crash)
    so the file_sender caller can still send the raw ``.plan.json`` file.
    """
    try:
        spec = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.info("plan_card: cannot parse %s", json_path, exc_info=True)
        return None
    if not isinstance(spec, dict):
        return None

    from claude_discord.discord_ui.render_preview import _ensure_browser

    browser = await _ensure_browser()
    if browser is None:
        return None
    html = _card_html(spec)
    try:
        page = await browser.new_page(  # type: ignore[attr-defined]
            viewport={"width": 820, "height": 900}, device_scale_factor=2
        )
        try:
            await page.set_content(html, wait_until="load", timeout=8000)
            png: bytes = await page.screenshot(full_page=True, type="png")
        finally:
            await page.close()
    except Exception:
        logger.info("plan_card: render failed for %s", json_path, exc_info=True)
        return None
    return png
