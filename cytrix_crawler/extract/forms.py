"""Read-only DOM form inspection (no submits, no clicks, no mutations via Playwright APIs)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# IIFE so browser eval returns the array directly (does not depend on Playwright
# auto-invoking a string expression that evaluates to a function).
_EXTRACT_FORMS_JS = """
(() => {
  const pageBase = document.baseURI || window.location.href || "";
  const lower = (value) => String(value || "").toLowerCase();

  const tokenish = (name) => {
    const n = lower(name);
    return (
      n.includes("csrf") ||
      n.includes("_token") ||
      n.includes("authenticity_token") ||
      n.includes("xsrf")
    );
  };

  const formHasCsrf = (form) => {
    const fields = form.querySelectorAll("input, textarea, select");
    for (const el of fields) {
      const nm = el.getAttribute("name") || el.getAttribute("id") || "";
      const typ = lower(el.getAttribute("type"));
      if (typ === "hidden" && tokenish(nm)) {
        return true;
      }
      if (tokenish(nm)) {
        return true;
      }
    }
    return false;
  };

  let metaCsrf = false;
  for (const m of document.querySelectorAll("meta")) {
    const name = lower(m.getAttribute("name"));
    const prop = lower(m.getAttribute("property"));
    if (
      tokenish(name) ||
      tokenish(prop) ||
      name.includes("csrf-token") ||
      prop.includes("csrf-token")
    ) {
      metaCsrf = true;
      break;
    }
  }

  const forms = Array.from(document.querySelectorAll("form"));
  return forms.map((form) => {
    const rawAction = form.getAttribute("action");
    let actionAbs = "";
    try {
      if (rawAction === null || String(rawAction).trim() === "") {
        actionAbs = new URL(pageBase).href;
      } else {
        actionAbs = new URL(String(rawAction).trim(), pageBase).href;
      }
    } catch (_) {
      actionAbs = String(rawAction || "").trim();
    }

    const rawMethod = form.getAttribute("method");
    const method = rawMethod && String(rawMethod).trim()
      ? String(rawMethod).trim().toUpperCase()
      : "GET";

    const fields = [];
    const buttons = [];

    const pushField = (el) => {
      const tag = el.tagName.toLowerCase();
      const name = el.getAttribute("name") || "";
      let type = "text";
      if (tag === "input") {
        type = lower(el.getAttribute("type") || "text") || "text";
      } else if (tag === "select") {
        type = "select";
      } else if (tag === "textarea") {
        type = "textarea";
      }
      const required = el.required === true || el.getAttribute("required") !== null;
      fields.push({ name, type, required });
    };

    const controls = form.querySelectorAll("input, select, textarea, button");
    for (const el of controls) {
      const tag = el.tagName.toLowerCase();
      if (tag === "button") {
        const btype = lower(el.getAttribute("type")) || "submit";
        if (btype === "submit" || btype === "button" || btype === "") {
          const txt = String(el.textContent || "").trim().replace(/\\s+/g, " ");
          if (txt) {
            buttons.push(txt);
          }
        }
        continue;
      }
      if (tag === "input") {
        const itype = lower(el.getAttribute("type") || "text");
        if (itype === "submit" || itype === "button" || itype === "image") {
          const val = String(el.getAttribute("value") || "").trim();
          if (val) {
            buttons.push(val);
          }
          if (itype === "image") {
            const alt = String(el.getAttribute("alt") || "").trim();
            if (alt) {
              buttons.push(alt);
            }
          }
          continue;
        }
      }

      if (tag === "input" || tag === "select" || tag === "textarea") {
        pushField(el);
      }
    }

    const csrf_detected = metaCsrf || formHasCsrf(form);
    return { action: actionAbs, method, fields, buttons, csrf_detected };
  });
})()
"""


async def extract_forms(page: Any, page_url: str) -> tuple[list[dict], str | None]:
    """Return extracted forms and an optional error message for visibility when extraction fails."""
    try:
        raw = await page.evaluate(_EXTRACT_FORMS_JS)
    except Exception as exc:
        msg = f"page.evaluate failed: {exc.__class__.__name__}: {exc}"
        logger.warning("extract_forms: %s (page_url=%s)", msg, page_url)
        return [], msg

    if not isinstance(raw, list):
        msg = f"expected list from evaluate, got {type(raw).__name__}"
        logger.warning("extract_forms: %s (page_url=%s)", msg, page_url)
        return [], msg

    forms: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action")
        method = entry.get("method") or "GET"
        fields = entry.get("fields") or []
        buttons = entry.get("buttons") or []
        csrf = bool(entry.get("csrf_detected"))

        if not isinstance(action, str):
            action = ""
        if not isinstance(method, str) or not method.strip():
            method = "GET"
        else:
            method = method.strip().upper()

        clean_fields = []
        if isinstance(fields, list):
            for field in fields:
                if not isinstance(field, dict):
                    continue
                name = field.get("name")
                ftype = field.get("type")
                clean_fields.append(
                    {
                        "name": "" if name is None else str(name),
                        "type": (
                            ""
                            if ftype is None
                            else str(ftype).lower()
                        ),
                        "required": bool(field.get("required")),
                    }
                )

        clean_buttons = []
        if isinstance(buttons, list):
            for btn in buttons:
                if isinstance(btn, str):
                    trimmed = btn.strip()
                    if trimmed:
                        clean_buttons.append(trimmed)

        forms.append(
            {
                "action": action,
                "csrf_detected": csrf,
                "fields": clean_fields,
                "method": method,
                "buttons": clean_buttons,
            }
        )
    return forms, None
