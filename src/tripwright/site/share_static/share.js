(function () {
  var root = document.documentElement;
  var KEY = "tw-lang";
  function stored() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function remember(language) { try { localStorage.setItem(KEY, language); } catch (e) { } }

  // language: ?lang= in the address wins (a link can say it), then the last choice, then the page's own default (English)
  function setLanguage(language, save) {
    if (language !== "zh" && language !== "en") return;
    root.dataset.language = language;
    root.lang = language === "zh" ? "zh-CN" : "en";
    if (save) remember(language);
    document.querySelectorAll("[data-lang-button]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.langButton === language));
    });
    document.querySelectorAll("a[data-lang-link]").forEach(function (link) {
      var url = new URL(link.getAttribute("href"), location.href);
      url.searchParams.set("lang", language);
      link.setAttribute("href", url.pathname.split("/").pop() + url.search + url.hash);
    });
    document.dispatchEvent(new Event("languagechange"));
  }
  var asked = new URLSearchParams(location.search).get("lang");
  setLanguage(asked || stored() || root.dataset.language, Boolean(asked));
  document.querySelectorAll("[data-lang-button]").forEach(function (button) {
    button.addEventListener("click", function () { setLanguage(button.dataset.langButton, true); });
  });

  // the day strip: the current day in view
  var current = document.querySelector(".days [aria-current]");
  if (current && current.scrollIntoView) current.scrollIntoView({ inline: "center", block: "nearest" });

  // key times: on the day itself the ones already past fade (?now=2026-10-29T12:00:00+01:00 shows another moment)
  var override = new URLSearchParams(location.search).get("now");
  function fade() {
    var now = override ? new Date(override) : new Date();
    var zone = document.body.dataset.zone;
    var today;
    try { today = new Intl.DateTimeFormat("en-CA", { timeZone: zone }).format(now); } catch (e) { return; }
    var next = null;
    document.querySelectorAll("li[data-at]").forEach(function (item) {
      var past = item.dataset.date === today && new Date(item.dataset.at) < now;
      item.classList.toggle("past", past);
      item.classList.remove("next");
      if (item.dataset.date === today && !past && !next && item.closest(".anchors")) next = item;
    });
    if (next) next.classList.add("next");
  }
  if (document.querySelector("li[data-at]")) { fade(); setInterval(fade, 60000); }

  // maps: Leaflet and the tiles come from the internet; without them the page stays as it is and says so
  var maps = document.querySelectorAll(".map[data-src]");
  if (!maps.length) return;
  var VERSION = "1.9.4";
  function failed() {
    maps.forEach(function (box) {
      if (box._leaflet_id) return;
      box.innerHTML = '<p class="note"><span class="en">The map could not be loaded (no connection?). The Map links still work.</span><span class="zh">地图加载不了（没网？）。“地图”链接照样能用。</span></p>';
    });
  }
  function draw(box) {
    var data = JSON.parse(document.getElementById(box.dataset.src).textContent);
    var map = L.map(box, { scrollWheelZoom: false });
    // the pages send no referrer; OpenStreetMap's tile servers block requests without one, so tiles send the origin only
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap", referrerPolicy: "strict-origin" }).addTo(map);
    var points = [];
    data.points.forEach(function (point) {
      var marker = L.circleMarker([point.lat, point.lon], { radius: data.big ? 9 : 7, color: "#1d1d1f", weight: 2, fillColor: point.color || "#2f6fdb", fillOpacity: 0.95 }).addTo(map);
      marker.bindPopup(function () {
        var zh = root.dataset.language === "zh";
        var label = document.createElement("div");
        var name = document.createElement("b");
        name.textContent = zh ? point.zh : point.en;
        label.appendChild(name);
        if (point.href) {
          var link = document.createElement("a");
          link.href = point.href;
          link.target = point.external ? "_blank" : "_self";
          link.rel = "noopener noreferrer";
          link.textContent = zh ? " 打开 ↗" : " Open ↗";
          label.appendChild(link);
        }
        return label;
      });
      points.push([point.lat, point.lon]);
    });
    if (data.line && points.length > 1) L.polyline(points, { color: "#1d1d1f", weight: 3, opacity: 0.6, dashArray: "6 6" }).addTo(map);
    if (points.length === 1) map.setView(points[0], 14); else map.fitBounds(points, { padding: [24, 24], maxZoom: 15 });
  }
  var style = document.createElement("link");
  style.rel = "stylesheet";
  style.href = "https://unpkg.com/leaflet@" + VERSION + "/dist/leaflet.css";
  document.head.appendChild(style);
  var script = document.createElement("script");
  script.src = "https://unpkg.com/leaflet@" + VERSION + "/dist/leaflet.js";
  var timer = setTimeout(failed, 9000);
  script.onload = function () {
    clearTimeout(timer);
    maps.forEach(function (box) { box.innerHTML = ""; try { draw(box); } catch (e) { failed(); } });
  };
  script.onerror = function () { clearTimeout(timer); failed(); };
  document.head.appendChild(script);
})();
