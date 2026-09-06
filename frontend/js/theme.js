// Appearance bootstrapping. Loaded synchronously in <head> on every page so
// theme, width, title, wallpaper, and background color are applied before the
// first paint (no flash). All values are cached in localStorage; each page
// reconciles with the server's /api/settings after load by calling the
// window.apply*() helpers.
(function () {
  var KEY = "nasportal.theme";
  var WIDTH_KEY = "nasportal.portal_width";
  var TITLE_KEY = "nasportal.portal_title";
  var BG_KEY = "nasportal.background_color";
  var WP_KEY = "nasportal.wallpaper";

  function resolve(theme) {
    if (theme === "system") {
      return (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches)
        ? "light" : "dark";
    }
    return theme === "light" ? "light" : "dark";
  }

  function applyThemeVal(theme) {
    document.documentElement.setAttribute("data-theme", resolve(theme));
  }

  // Early apply from the cache (runs in <head>, before the body is painted).
  applyThemeVal(localStorage.getItem(KEY) || "dark");

  // Pages call this after fetching /api/settings; it caches + reapplies.
  window.applyTheme = function (theme) {
    var t = theme || "dark";
    localStorage.setItem(KEY, t);
    applyThemeVal(t);
  };

  // In "system" mode, keep the resolved scheme in sync with the OS preference.
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: light)");
    var handler = function () {
      if ((localStorage.getItem(KEY) || "dark") === "system") applyThemeVal("system");
    };
    if (mq.addEventListener) mq.addEventListener("change", handler);
    else if (mq.addListener) mq.addListener(handler); // older Safari
  }

  // --- Portal width ---
  var cachedWidth = localStorage.getItem(WIDTH_KEY);
  if (cachedWidth !== null) {
    var n = Math.max(50, Math.min(100, Math.round(Number(cachedWidth) || 80)));
    document.documentElement.style.setProperty("--portal-width", n + "%");
  }
  window.applyPortalWidth = function (p) {
    var n = Math.max(50, Math.min(100, Math.round(Number(p) || 80)));
    document.documentElement.style.setProperty("--portal-width", n + "%");
    try { localStorage.setItem(WIDTH_KEY, n); } catch (_) {}
    return n;
  };

  // --- Portal title ---
  var cachedTitle = localStorage.getItem(TITLE_KEY);
  if (cachedTitle) {
    document.title = cachedTitle;
    // The brand <div> is in <body> which doesn't exist yet — watch for it.
    var titleApplied = false;
    var applyToBrand = function () {
      if (titleApplied) return;
      var b = document.getElementById("brand");
      if (b) { b.textContent = cachedTitle; titleApplied = true; }
    };
    if (document.body) applyToBrand();
    else {
      new MutationObserver(function (_, obs) {
        applyToBrand();
        if (titleApplied) obs.disconnect();
      }).observe(document.documentElement, { childList: true, subtree: true });
    }
  }
  window.applyPortalTitle = function (title) {
    var t = title || "NAS Portal";
    localStorage.setItem(TITLE_KEY, t);
    document.title = t;
    var b = document.getElementById("brand");
    if (b) b.textContent = t;
  };

  // --- Background color ---
  var cachedBg = localStorage.getItem(BG_KEY);
  if (cachedBg !== null) {
    var v = cachedBg.trim();
    if (v) {
      document.documentElement.style.setProperty("--bg-custom", v);
      document.documentElement.setAttribute("data-bg-custom", "");
    }
  }
  window.applyBackgroundColor = function (c) {
    var v = (c || "").trim();
    var html = document.documentElement;
    if (v) {
      html.style.setProperty("--bg-custom", v);
      html.setAttribute("data-bg-custom", "");
    } else {
      html.style.removeProperty("--bg-custom");
      html.removeAttribute("data-bg-custom");
    }
    try { localStorage.setItem(BG_KEY, c || ""); } catch (_) {}
  };

  // --- Wallpaper ---
  function cssEscWp(s) { return String(s || "").replace(/["\\]/g, "\\$&"); }
  var cachedWp = localStorage.getItem(WP_KEY);
  if (cachedWp) {
    var tag = document.createElement("style");
    tag.id = "wp-style";
    tag.textContent = 'body::after{background-image:url("' + cssEscWp(cachedWp) + '")}';
    document.head.appendChild(tag);
    document.documentElement.setAttribute("data-wp", /-light\b/.test(cachedWp) ? "light" : "dark");
  }
  window.applyWallpaper = function (url) {
    var html = document.documentElement;
    var tag = document.getElementById("wp-style");
    if (!url) {
      if (tag) tag.remove();
      document.body.style.removeProperty("background-color");
      html.removeAttribute("data-wp");
      try { localStorage.removeItem(WP_KEY); } catch (_) {}
      return;
    }
    if (!tag) {
      tag = document.createElement("style");
      tag.id = "wp-style";
      document.head.appendChild(tag);
    }
    tag.textContent = 'body::after{background-image:url("' + cssEscWp(url) + '")}';
    document.body.style.backgroundColor = "transparent";
    html.setAttribute("data-wp", /-light\b/.test(url) ? "light" : "dark");
    try { localStorage.setItem(WP_KEY, url); } catch (_) {}
  };

  // Service worker registration (PWA installability). Skipped on the
  // login page: the login form is its own page and shouldn't trigger
  // an install prompt or a "Add to Home Screen" hint mid-login.
  // We also bail on plain http://localhost / 127.0.0.1 because some
  // browsers refuse to register a SW over an insecure localhost (and
  // nasportal is typically served plain-HTTP on a LAN).
  if ("serviceWorker" in navigator && location.protocol !== "file:" && location.pathname !== "/login") {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () { /* offline-only feature; ignore */ });
    });
  }
})();