// Runs before the app bundle. Mirrors resolveTheme() in src/lib/theme.ts:
// the cached preference wins, otherwise follow the operating system.
(function () {
  var dark = false;
  try {
    var pref = localStorage.getItem("jimmyhq.theme");
    dark =
      pref === "dark" ||
      (pref !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    // A Wall set to always be dark starts dark, without a flash of the app's theme.
    if (/^\/wall\/?$/.test(location.pathname) && localStorage.getItem("jimmyhq.wall.force_dark") === "1") {
      dark = true;
    }
  } catch (e) {
    dark = false;
  }
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
})();
