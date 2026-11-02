// tripwright site: language switch and copy buttons. No dependencies.
(function () {
  var KEY = "tripwright-lang";
  var root = document.documentElement;

  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function store(v) {
    try { localStorage.setItem(KEY, v); } catch (e) { /* private mode or blocked storage */ }
  }
  function apply(lang) {
    root.setAttribute("data-lang", lang);
    root.setAttribute("lang", lang === "zh" ? "zh-CN" : "en");
    var t = root.getAttribute("data-title-" + lang);
    if (t) document.title = t;
    var labelled = document.querySelectorAll("[data-label-zh]");
    for (var i = 0; i < labelled.length; i++) {
      var el = labelled[i];
      el.setAttribute("aria-label", el.getAttribute("data-label-" + lang) || "");
    }
    var btns = document.querySelectorAll(".lang button");
    for (var j = 0; j < btns.length; j++) {
      btns[j].setAttribute("aria-pressed", btns[j].getAttribute("data-set-lang") === lang ? "true" : "false");
    }
  }

  // ?lang=zh or ?lang=en in the URL wins (handy for sharing a link in one language)
  var fromUrl = null;
  try { fromUrl = new URLSearchParams(location.search).get("lang"); } catch (e) { /* old browser */ }
  var initial = (fromUrl === "zh" || fromUrl === "en") ? fromUrl : stored();
  // English by default; a choice the reader made is remembered.
  if (initial !== "zh" && initial !== "en") initial = "en";
  apply(initial);

  document.addEventListener("DOMContentLoaded", function () {
    apply(root.getAttribute("data-lang") || initial);
    var btns = document.querySelectorAll(".lang button");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        var v = this.getAttribute("data-set-lang");
        apply(v);
        store(v);
      });
    }
    var copies = document.querySelectorAll("button.copy");
    for (var k = 0; k < copies.length; k++) {
      copies[k].addEventListener("click", function () {
        var btn = this;
        var text = btn.getAttribute("data-copy") || "";
        function done() {
          btn.classList.add("done");
          var old = btn.innerHTML;
          btn.innerHTML = '<span class="zh">已复制</span><span class="en">Copied</span>';
          setTimeout(function () { btn.classList.remove("done"); btn.innerHTML = old; }, 1400);
        }
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, function () { fallback(text) && done(); });
          } else if (fallback(text)) { done(); }
        } catch (e) { if (fallback(text)) done(); }
      });
    }
  });

  function fallback(text) {
    try {
      var ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", "");
      ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch (e) { return false; }
  }
})();
