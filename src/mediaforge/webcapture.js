// Web capture, in-page helpers (docs/SDD-web-capture.md §4, §12.2, §12.3). Evaluated by webcapture.py into the
// page it opened; defines window.__mf. Reads the page only, except: one consent-button click, hiding fixed overlays
// before screenshots, and data-mf-* attributes that let Python address the elements it listed.
(() => {
  // a currency word must end there: "9 đánh giá" is not 9 đ
  const PRICE = /(?:\d[\d.,\s]{0,14}\d|\d)\s?(?:đ|₫|vnđ|vnd|usd|aud|\$|€|£)(?!\p{L})|(?:\$|€|£|a\$|us\$)\s?\d[\d.,]*/iu;
  const CONSENT_TEXT = ["accept", "accept all", "chấp nhận", "đồng ý", "ok", "got it", "đóng", "×", "x", "close",
    "allow all", "i agree", "tôi đồng ý", "đã hiểu"];
  const CONSENT_BOX = /cookie|consent|gdpr|modal|popup/i;
  const LOGOISH = /logo|brand/i;

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && Number(s.opacity) > 0.05;
  };
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim();
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return ""; } };

  // A selector that finds el again on a later visit: the nearest ancestor with a unique id, then tag:nth-of-type
  // steps (class names are often hashed per build, so they are not used).
  const selectorOf = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
      if (n.id && document.querySelectorAll("#" + CSS.escape(n.id)).length === 1) {
        parts.unshift("#" + CSS.escape(n.id));
        return parts.join(" > ");
      }
      if (n === document.body) { parts.unshift("body"); break; }
      let k = 1;
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) k++;
      parts.unshift(n.tagName.toLowerCase() + ":nth-of-type(" + k + ")");
    }
    return parts.join(" > ");
  };

  // The largest candidate of a srcset (by w descriptor, else x), else currentSrc/src.
  const bestSrc = (img) => {
    let best = img.currentSrc || img.src, bw = 0;
    const sets = [img.getAttribute("srcset"), img.getAttribute("data-srcset"),
      ...(img.parentElement && img.parentElement.tagName === "PICTURE"
        ? [...img.parentElement.querySelectorAll("source")].map((s) => s.getAttribute("srcset")) : [])];
    for (const set of sets) {
      if (!set) continue;
      for (const part of set.split(/,\s+/)) {
        const [u, d] = part.trim().split(/\s+/);
        const w = d ? parseFloat(d) * (d.endsWith("x") ? 1000 : 1) : 1;
        if (u && w > bw) { bw = w; best = u; }
      }
    }
    if (!best || best.startsWith("data:")) best = img.getAttribute("data-src") || img.getAttribute("data-lazy-src") || best;
    return abs(best);
  };

  const inHeader = (el) => !!el.closest("header, [role=banner], nav, #header, .header, .navbar");

  window.__mf = {
    dismiss() {
      for (const b of document.querySelectorAll("button, [role=button], a, input[type=button], input[type=submit]")) {
        if (!visible(b)) continue;
        const t = clean(b.innerText || b.value || b.getAttribute("aria-label")).toLowerCase();
        if (!CONSENT_TEXT.includes(t)) continue;
        let box = false;
        for (let n = b, i = 0; n && i < 8; n = n.parentElement, i++) {
          if (CONSENT_BOX.test((n.id || "") + " " + (n.className && n.className.baseVal !== undefined ? n.className.baseVal : n.className || "") + " " + (n.getAttribute("aria-label") || ""))) { box = true; break; }
        }
        if (!box) continue;
        b.click();
        return t;
      }
      return null;
    },

    // Hide fixed/sticky layers covering more than 30 % of the viewport (left-over pop-ups); returns how many.
    hideOverlays() {
      const vw = innerWidth, vh = innerHeight;
      let n = 0;
      for (const el of document.querySelectorAll("body *")) {
        const s = getComputedStyle(el);
        if (s.position !== "fixed" && s.position !== "sticky") continue;
        if (!visible(el)) continue;
        const r = el.getBoundingClientRect();
        const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
        const h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
        if (w * h > 0.3 * vw * vh) { el.style.setProperty("visibility", "hidden", "important"); n++; }
      }
      return n;
    },

    // Instant scrolls: with CSS scroll-behavior: smooth a plain scrollTo animates, and a screenshot taken meanwhile
    // catches a sticky header over the wrong part of the page.
    async scrollThrough(maxSteps) {
      const step = innerHeight * 0.9;
      for (let i = 0; i < maxSteps && scrollY + innerHeight < document.documentElement.scrollHeight; i++) {
        scrollBy({ top: step, behavior: "instant" });
        await new Promise((r) => setTimeout(r, 250));
      }
      await this.toTop();
    },

    async toTop() {
      scrollTo({ top: 0, left: 0, behavior: "instant" });
      for (let i = 0; i < 20 && scrollY > 0; i++) await new Promise((r) => setTimeout(r, 50));
      await new Promise((r) => setTimeout(r, 300));
      return scrollY;
    },

    scan() {
      const meta = {};
      for (const m of document.querySelectorAll("meta[property], meta[name]")) {
        const k = (m.getAttribute("property") || m.getAttribute("name") || "").toLowerCase();
        if (/^(og:|twitter:|description$)/.test(k) && m.content && !(k in meta)) meta[k] = clean(m.content);
      }
      const jsonld = [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => s.textContent).slice(0, 20);
      const headings = [...document.querySelectorAll("h1, h2")].filter(visible).slice(0, 12)
        .map((h) => ({ tag: h.tagName.toLowerCase(), text: clean(h.innerText).slice(0, 200), sel: selectorOf(h) }))
        .filter((h) => h.text);
      const prices = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let t; (t = walker.nextNode()) && prices.length < 20;) {
        const s = clean(t.textContent);
        // a price line is short and mostly the price ("45.000 ₫", "Giá: 45.000đ"): a sentence that merely holds a
        // price-looking string ("…write a price of 1đ…") is not a fact
        if (!s || s.length > 40 || s.split(" ").length > 6 || !PRICE.test(s)) continue;
        const el = t.parentElement;
        if (!el || !visible(el) || el.closest("script, style, noscript")) continue;
        if (prices.some((p) => p.text === s)) continue;
        prices.push({ text: s, sel: selectorOf(el) });
      }
      const images = [];
      for (const img of document.images) {
        const r = img.getBoundingClientRect();
        const src = bestSrc(img);
        if (!src || src.startsWith("data:")) continue;
        images.push({ src, alt: clean(img.alt).slice(0, 200), w: img.naturalWidth, h: img.naturalHeight,
          area: Math.round(r.width * r.height), visible: visible(img), header: inHeader(img),
          logo: LOGOISH.test((img.alt || "") + " " + (img.className || "") + " " + (img.id || "") + " " + src),
          sel: selectorOf(img) });
      }
      const icons = [...document.querySelectorAll('link[rel~="icon"], link[rel="apple-touch-icon"], link[rel="apple-touch-icon-precomposed"]')]
        .map((l) => ({ rel: l.rel, href: abs(l.getAttribute("href")), sizes: l.getAttribute("sizes") || "", type: l.type || "" }));
      // Logos drawn in the page rather than linked as a file: an element in the header whose id/class/aria-label
      // says logo or brand (an inline SVG plus a wordmark), else the header's home link when it is short text or a
      // picture (a text wordmark). Python screenshots them.
      const header = document.querySelector("header, [role=banner]") || document.querySelector("nav");
      const logoBlocks = [];
      if (header) {
        const fits = (el) => { const r = el.getBoundingClientRect();
          return visible(el) && r.width >= 40 && r.height >= 16 && r.width <= innerWidth * 0.5 && r.height <= 220; };
        const named = [...header.querySelectorAll("a, div, span, svg, picture")].filter((el) =>
          LOGOISH.test((el.id || "") + " " + (el.getAttribute("class") || "") + " " + (el.getAttribute("aria-label") || "")) && fits(el));
        const outer = named.filter((el) => !named.some((o) => o !== el && o.contains(el)));
        const home = [...header.querySelectorAll('a[href="/"], a[href="#top"], a[href="./"], a[rel~=home], a[aria-label*="home" i], a[aria-label*="trang chủ" i]')]
          .filter((a) => fits(a) && clean(a.innerText).length <= 30);
        for (const el of [...new Set([...outer, ...home])].slice(0, 3)) {
          const r = el.getBoundingClientRect();
          logoBlocks.push({ sel: selectorOf(el), w: Math.round(r.width), h: Math.round(r.height), text: clean(el.innerText).slice(0, 40) });
        }
      }
      return { url: location.href, title: clean(document.title).slice(0, 200), meta, jsonld, headings, prices,
        images, icons, logoBlocks, text: (document.body ? document.body.innerText : "").slice(0, 200000) };
    },

    // The smallest ancestor of the element at `sel` whose text holds one of `texts` (a price, the name) and that
    // is smaller than most of the page: the product block. Marked data-mf-block; returns its selector or null.
    productBlock(sel, texts) {
      const el = document.querySelector(sel);
      if (!el) return null;
      const page = document.documentElement.scrollWidth * document.documentElement.scrollHeight;
      for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
        const r = n.getBoundingClientRect();
        if (r.width * r.height > 0.6 * page) break;
        const t = clean(n.innerText);
        if (texts.some((x) => x && t.includes(x))) {
          document.querySelectorAll("[data-mf-block]").forEach((x) => x.removeAttribute("data-mf-block"));
          n.setAttribute("data-mf-block", "1");
          return selectorOf(n);
        }
      }
      return null;
    },

    // §12.3: does a saved selector still find something usable? kind: image | text | price.
    probe(sel, kind) {
      let el;
      try { el = document.querySelector(sel); } catch (e) { return null; }
      if (!el) return null;
      if (kind === "image") {
        const img = el.tagName === "IMG" ? el : el.querySelector("img");
        return img ? { src: bestSrc(img), w: img.naturalWidth, h: img.naturalHeight, alt: clean(img.alt) } : null;
      }
      const t = clean(el.innerText).slice(0, 300);
      if (!t || (kind === "price" && !PRICE.test(t))) return null;
      return { text: t };
    },

    // §12.2: numbered menus for the agent. Elements get data-mf-link / data-mf-button / data-mf-image = n.
    menus() {
      for (const a of ["data-mf-link", "data-mf-button", "data-mf-image"])
        document.querySelectorAll("[" + a + "]").forEach((x) => x.removeAttribute(a));
      const host = location.hostname.replace(/^www\./, "");
      const links = [], seen = new Set();
      for (const a of document.querySelectorAll("a[href]")) {
        if (links.length >= 40 || !visible(a)) continue;
        let u;
        try { u = new URL(a.getAttribute("href"), location.href); } catch (e) { continue; }
        const h = u.hostname.replace(/^www\./, "");
        if (!/^https?:$/.test(u.protocol) || (h !== host && !h.endsWith("." + host))) continue;
        u.hash = "";
        if (seen.has(u.href) || u.href === location.href.split("#")[0]) continue;
        seen.add(u.href);
        a.setAttribute("data-mf-link", links.length + 1);
        links.push({ n: links.length + 1, text: clean(a.innerText || a.getAttribute("aria-label") || a.title).slice(0, 80), path: (u.pathname + u.search).slice(0, 120), href: u.href });
      }
      const buttons = [];
      for (const b of document.querySelectorAll("button, [role=tab], summary, [aria-expanded]")) {
        if (buttons.length >= 20 || !visible(b) || b.closest("form") || b.type === "submit") continue;
        const t = clean(b.innerText || b.getAttribute("aria-label")).slice(0, 60);
        if (!t) continue;
        b.setAttribute("data-mf-button", buttons.length + 1);
        buttons.push({ n: buttons.length + 1, text: t });
      }
      const images = [];
      for (const img of [...document.images].filter(visible).sort((a, b) => {
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        return rb.width * rb.height - ra.width * ra.height; })) {
        if (images.length >= 20) break;
        const src = bestSrc(img);
        if (!src || src.startsWith("data:")) continue;
        img.setAttribute("data-mf-image", images.length + 1);
        images.push({ n: images.length + 1, alt: clean(img.alt).slice(0, 80), w: img.naturalWidth, h: img.naturalHeight, src, sel: selectorOf(img) });
      }
      return { url: location.href, title: clean(document.title).slice(0, 120), links, buttons, images };
    },

    // The card around image n of the menu (for capture_block): smallest ancestor with some text and at least
    // twice the image's area; marked data-mf-block.
    blockAround(n) {
      const img = document.querySelector('[data-mf-image="' + n + '"]');
      if (!img) return null;
      const ri = img.getBoundingClientRect();
      for (let p = img.parentElement; p && p !== document.body; p = p.parentElement) {
        const r = p.getBoundingClientRect();
        if (clean(p.innerText).length > 10 && r.width * r.height >= 2 * ri.width * ri.height) {
          document.querySelectorAll("[data-mf-block]").forEach((x) => x.removeAttribute("data-mf-block"));
          p.setAttribute("data-mf-block", "1");
          return selectorOf(p);
        }
      }
      return null;
    },

    text() { return document.body ? document.body.innerText.slice(0, 200000) : ""; },
  };
})();
