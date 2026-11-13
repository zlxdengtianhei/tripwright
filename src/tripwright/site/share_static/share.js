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

  // maps: each plate shows a position sketch drawn into the page. When Leaflet and the OpenStreetMap tiles load, the
  // sketch is swapped for a real map with the same pins; if the library or the tiles do not load, the sketch stays.
  var plates = [];
  document.querySelectorAll(".plate[data-map]").forEach(function (plate) {
    var source = document.getElementById(plate.dataset.map);
    if (source) plates.push({ plate: plate, data: JSON.parse(source.textContent) });
  });
  plates = plates.filter(function (item) { return item.data.points.length; });
  if (!plates.length) return;
  var VERSION = "1.9.4";
  function back(item) {
    if (item.map) { try { item.map.remove(); } catch (e) { } item.map = null; }
    item.plate.classList.remove("live");
    var box = item.plate.querySelector(".livemap");
    box.hidden = true;
    item.plate.querySelector(".live-note").hidden = true;
    item.plate.querySelector(".sketch-note").hidden = false;
  }
  function icon(point) {
    var wide = point.shape === "opt";
    var html = '<span class="pin ' + point.shape + '">' + point.label.replace(/[^0-9A-Za-z]/g, "") + "</span>";
    return L.divIcon({ className: "pinmark", html: html, iconSize: [wide ? 36 : 28, 28], iconAnchor: [(wide ? 18 : 14) - (point.shift || 0), 14] });
  }
  function draw(item) {
    var plate = item.plate, data = item.data, box = plate.querySelector(".livemap");
    box.hidden = false;
    plate.classList.add("live");
    var map = L.map(box, { scrollWheelZoom: false });
    item.map = map;
    var loaded = false;
    // the pages send no referrer; OpenStreetMap's tile servers block requests without one, so tiles send the origin only
    var layer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap", referrerPolicy: "strict-origin" });
    layer.on("tileload", function () { loaded = true; });
    layer.addTo(map);
    var solid = { color: "#1b2430", weight: 2, opacity: 0.8 }, dashed = { color: "#55606b", weight: 2, opacity: 0.8, dashArray: "5 4" };
    if (data.path.length > 1) L.polyline(data.path, data.dashed ? dashed : solid).addTo(map);
    data.branches.forEach(function (pair) { L.polyline(pair, dashed).addTo(map); });
    var points = [];
    data.points.forEach(function (point) {
      var marker = L.marker([point.lat, point.lon], { icon: icon(point), keyboard: true, title: point.label }).addTo(map);
      marker.bindPopup(function () {
        var zh = root.dataset.language === "zh";
        var label = document.createElement("div");
        var name = document.createElement("b");
        name.textContent = point.label + " " + (zh ? point.zh : point.en);
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
    if (points.length === 1) map.setView(points[0], 14); else map.fitBounds(points, { padding: [32, 32], maxZoom: 15 });
    plate.querySelector(".live-note").hidden = false;
    plate.querySelector(".sketch-note").hidden = true;
    // no tile within 8 seconds: the base map is not reachable, so the sketch comes back
    setTimeout(function () { if (!loaded) back(item); }, 8000);
  }
  var style = document.createElement("link");
  style.rel = "stylesheet";
  style.href = "https://unpkg.com/leaflet@" + VERSION + "/dist/leaflet.css";
  document.head.appendChild(style);
  var script = document.createElement("script");
  script.src = "https://unpkg.com/leaflet@" + VERSION + "/dist/leaflet.js";
  script.onload = function () { plates.forEach(function (item) { try { draw(item); } catch (e) { back(item); } }); };
  document.head.appendChild(script);
})();
