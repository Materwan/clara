// The colour theme: "auto" (follow the system), "light" or "dark", remembered in this browser. A classic script loaded
// in <head> (the CSP allows no inline script), so the page is drawn with the right colours from the first frame.
// It also points the browser's own bars (theme-color) at the rail's blue of that theme: src/lib/theme.ts does the same
// when you choose a theme by hand.
(function () {
  try {
    var choice = localStorage.getItem('clara-theme');
    if (choice === 'light' || choice === 'dark') {
      document.documentElement.setAttribute('data-theme', choice);
      var metas = document.querySelectorAll('meta[name="theme-color"]');
      for (var i = 0; i < metas.length; i++) {
        metas[i].media = metas[i].getAttribute('data-scheme') === choice ? 'all' : 'not all';
      }
    }
  } catch (e) {
    // storage refused (private mode): the system's choice applies
  }
})();
