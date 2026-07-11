// Color-scheme bootstrapping. Loaded synchronously in <head> on every page so
// the theme is applied before the first paint (no flash). The chosen theme is
// cached in localStorage; each page reconciles with the server's /api/settings
// after load by calling window.applyTheme(settings.theme).
(function () {
  var KEY = "nasportal.theme";

  function resolve(theme) {
    if (theme === "system") {
      return (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches)
        ? "light" : "dark";
    }
    return theme === "light" ? "light" : "dark";
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", resolve(theme));
  }

  // Early apply from the cache (runs in <head>, before the body is painted).
  apply(localStorage.getItem(KEY) || "dark");

  // Pages call this after fetching /api/settings; it caches + reapplies.
  window.applyTheme = function (theme) {
    var t = theme || "dark";
    localStorage.setItem(KEY, t);
    apply(t);
  };

  // In "system" mode, keep the resolved scheme in sync with the OS preference.
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: light)");
    var handler = function () {
      if ((localStorage.getItem(KEY) || "dark") === "system") apply("system");
    };
    if (mq.addEventListener) mq.addEventListener("change", handler);
    else if (mq.addListener) mq.addListener(handler); // older Safari
  }
})();