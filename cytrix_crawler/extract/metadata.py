"""Read-only page-level metadata extraction."""

from __future__ import annotations

from typing import Any

_EXTRACT_METADATA_JS = """
() => {
  const pageBase = document.baseURI || window.location.href || "";
  const normalizeSpace = (txt) => String(txt || "").trim().replace(/\\s+/g, " ");

  const title = String(document.title || "").trim();

  let metaDescription = null;
  for (const sel of ['meta[name="description"]', 'meta[name="Description"]', 'meta[property="description"]']) {
    const el = document.querySelector(sel);
    if (el) {
      const val = normalizeSpace(el.getAttribute("content") || "");
      metaDescription = val || null;
      if (metaDescription) {
        break;
      }
    }
  }

  let canonicalUrl = null;
  const canon = document.querySelector('link[rel="canonical"]');
  if (canon) {
    const href = canon.getAttribute("href");
    if (href) {
      try {
        canonicalUrl = new URL(href.trim(), pageBase).href;
      } catch (_) {
        canonicalUrl = href.trim();
      }
    }
  }

  const collect = (selector, limit) => {
    const out = [];
    for (const el of document.querySelectorAll(selector)) {
      const t = normalizeSpace(el.textContent || "");
      if (t) {
        out.push(t);
      }
      if (out.length >= limit) {
        break;
      }
    }
    return out;
  };

  let h1 = collect("h1", 5);
  let h2 = collect("h2", 5);
  let h3 = collect("h3", 5);

  while (h1.length + h2.length + h3.length > 10) {
    if (h3.length) {
      h3.pop();
    } else if (h2.length) {
      h2.pop();
    } else if (h1.length) {
      h1.pop();
    } else {
      break;
    }
  }

  const externalSrcs = [];
  let inlineScriptCount = 0;
  for (const s of document.querySelectorAll("script")) {
    const src = s.getAttribute("src");
    if (src && src.trim()) {
      try {
        externalSrcs.push(new URL(src.trim(), pageBase).href);
      } catch (_) {
        externalSrcs.push(src.trim());
      }
    } else {
      inlineScriptCount += 1;
    }
  }

  const buttons = [];
  const seen = new Set();
  const addButton = (txt) => {
    if (!txt || seen.has(txt)) {
      return;
    }
    seen.add(txt);
    buttons.push(txt);
  };

  for (const btn of document.querySelectorAll("button")) {
    addButton(normalizeSpace(btn.textContent || ""));
  }

  for (const inp of document.querySelectorAll("input")) {
    const type = String(inp.getAttribute("type") || "").toLowerCase();
    if (type === "submit" || type === "button" || type === "image") {
      addButton(normalizeSpace(inp.getAttribute("value") || ""));
      if (type === "image") {
        addButton(normalizeSpace(inp.getAttribute("alt") || ""));
      }
    }
  }

  return {
    title,
    meta_description: metaDescription,
    canonical_url: canonicalUrl,
    headings: { h1, h2, h3 },
    scripts: { external_srcs: externalSrcs, inline_script_count: inlineScriptCount },
    buttons,
  };
}
"""


def empty_page_metadata() -> dict[str, Any]:
    return {
        "buttons": [],
        "canonical_url": None,
        "headings": {"h1": [], "h2": [], "h3": []},
        "meta_description": None,
        "scripts": {"external_srcs": [], "inline_script_count": 0},
        "title": "",
    }


async def extract_page_metadata(page: Any) -> dict:
    defaults = empty_page_metadata()
    try:
        raw = await page.evaluate(_EXTRACT_METADATA_JS)
    except Exception:
        return defaults

    if not isinstance(raw, dict):
        return defaults

    title = raw.get("title")
    meta_description = raw.get("meta_description")
    canonical_url = raw.get("canonical_url")

    headings = raw.get("headings") or {}
    h1 = headings.get("h1") if isinstance(headings, dict) else []
    h2 = headings.get("h2") if isinstance(headings, dict) else []
    h3 = headings.get("h3") if isinstance(headings, dict) else []

    scripts = raw.get("scripts") if isinstance(raw.get("scripts"), dict) else {}
    external_raw = scripts.get("external_srcs") or scripts.get("externalSrcs") or []
    inline_raw = scripts.get("inline_script_count")
    if isinstance(inline_raw, int):
        inline_count = inline_raw
    else:
        try:
            inline_count = int(inline_raw or 0)
        except (TypeError, ValueError):
            inline_count = 0

    buttons = raw.get("buttons") or []

    def _clean_str_list(items: Any, limit: int | None = None) -> list[str]:
        result: list[str] = []
        if not isinstance(items, list):
            return result
        for item in items:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text:
                continue
            result.append(text)
            if limit is not None and len(result) >= limit:
                break
        return result

    button_texts = _clean_str_list(buttons)
    seen_btn: set[str] = set()
    buttons_ordered: list[str] = []
    for text in button_texts:
        if text in seen_btn:
            continue
        seen_btn.add(text)
        buttons_ordered.append(text)

    return {
        "title": title.strip() if isinstance(title, str) else "",
        "meta_description": meta_description if isinstance(meta_description, str) else meta_description,
        "canonical_url": canonical_url if isinstance(canonical_url, str) else canonical_url,
        "headings": {
            "h1": _clean_str_list(h1, 5),
            "h2": _clean_str_list(h2, 5),
            "h3": _clean_str_list(h3, 5),
        },
        "scripts": {
            "external_srcs": _clean_str_list(external_raw),
            "inline_script_count": max(0, inline_count),
        },
        "buttons": buttons_ordered,
    }
