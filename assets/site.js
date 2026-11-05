(() => {
  "use strict";

  const siteRoot = new URL(".", window.location.href);
  const assetUrl = (path) => new URL(path, siteRoot).href;
  const sceneData = [
    { year: 2025, quarter: "Q1", catalogRecords: 153, selectedForScreening: 12, tiles: 6, firstDate: "2025-01-01", lastDate: "2025-03-30", meanSceneCloudPct: 51.4 },
    { year: 2025, quarter: "Q2", catalogRecords: 215, selectedForScreening: 12, tiles: 6, firstDate: "2025-04-01", lastDate: "2025-06-28", meanSceneCloudPct: 55.4 },
    { year: 2025, quarter: "Q3", catalogRecords: 145, selectedForScreening: 12, tiles: 6, firstDate: "2025-07-03", lastDate: "2025-08-29", meanSceneCloudPct: 72.4 },
    { year: 2025, quarter: "Q4", catalogRecords: 146, selectedForScreening: 12, tiles: 6, firstDate: "2025-09-01", lastDate: "2025-10-28", meanSceneCloudPct: 69.4 },
    { year: 2026, quarter: "Q1", catalogRecords: 161, selectedForScreening: 12, tiles: 6, firstDate: "2026-01-01", lastDate: "2026-03-27", meanSceneCloudPct: 54.0 },
    { year: 2026, quarter: "Q2", catalogRecords: 208, selectedForScreening: 12, tiles: 6, firstDate: "2026-04-01", lastDate: "2026-06-28", meanSceneCloudPct: 69.4 },
    { year: 2026, quarter: "Q3", catalogRecords: 144, selectedForScreening: 12, tiles: 6, firstDate: "2026-07-02", lastDate: "2026-08-29", meanSceneCloudPct: 56.5 },
    { year: 2026, quarter: "Q4", catalogRecords: 144, selectedForScreening: 12, tiles: 6, firstDate: "2026-09-01", lastDate: "2026-10-30", meanSceneCloudPct: 61.4 }
  ];
  let sceneChart = null;

  const menuButton = document.querySelector(".menu-toggle");
  const nav = document.querySelector(".primary-nav");
  menuButton?.addEventListener("click", () => {
    const expanded = menuButton.getAttribute("aria-expanded") === "true";
    menuButton.setAttribute("aria-expanded", String(!expanded));
    menuButton.setAttribute("aria-label", expanded ? "Open navigation" : "Close navigation");
    nav?.classList.toggle("open", !expanded);
  });
  nav?.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => {
    nav.classList.remove("open");
    menuButton?.setAttribute("aria-expanded", "false");
    menuButton?.setAttribute("aria-label", "Open navigation");
  }));

  const mapElement = document.getElementById("rfo-map");
  if (mapElement) initializeMap();
  initializeCharts();
  initializeDownloads();

  async function initializeMap() {
    const caption = document.getElementById("map-caption-text") || document.getElementById("map-caption");
    const periodFilter = document.getElementById("period-filter");
    const yearFilter = document.getElementById("year-filter");
    const mapPeriodLabel = document.getElementById("map-period-label");
    const engineState = document.getElementById("map-engine-state");
    const basemapSelect = document.getElementById("basemap-select");
    if (!window.L) {
      try {
        await loadLeafletFallback();
      } catch (error) {
        console.error("Could not load Leaflet from either CDN:", error);
        if (engineState) engineState.textContent = "MAP LIBRARY UNAVAILABLE";
        if (caption) caption.textContent = "The interactive map library could not be loaded. Download the boundary GeoJSON from Data & downloads.";
        return;
      }
    }

    const map = L.map(mapElement, { zoomControl: false, scrollWheelZoom: false, preferCanvas: true }).setView([1.72, 28.53], 9);
    L.control.zoom({ position: "topright" }).addTo(map);
    const basemaps = {
      carto: L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        subdomains: "abcd", maxZoom: 19, attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
      }),
      plain: L.tileLayer("https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", {
        subdomains: "abcd", maxZoom: 19, attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
      }),
      osm: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      })
    };
    let activeBasemap = basemaps[basemapSelect?.value || "carto"];
    let tileErrors = 0;
    let tileLoads = 0;
    let switchedToFallback = false;
    const updateEngineState = (message) => {
      if (engineState) engineState.textContent = message;
    };
    const activateBasemap = (name) => {
      if (!basemaps[name] || activeBasemap === basemaps[name]) return;
      map.removeLayer(activeBasemap);
      activeBasemap = basemaps[name];
      tileErrors = 0;
      tileLoads = 0;
      activeBasemap.addTo(map);
      updateEngineState("LOADING BASEMAP");
    };
    for (const [name, layer] of Object.entries(basemaps)) {
      layer.on("tileload", () => {
        if (activeBasemap !== layer) return;
        tileLoads += 1;
        updateEngineState("BASEMAP READY");
      });
      layer.on("tileerror", () => {
        if (activeBasemap !== layer) return;
        tileErrors += 1;
        if (name !== "osm" && !switchedToFallback && tileErrors >= 3 && tileLoads === 0) {
          switchedToFallback = true;
          if (basemapSelect) basemapSelect.value = "osm";
          activateBasemap("osm");
        } else if (name === "osm" && tileErrors >= 3 && tileLoads === 0) {
          updateEngineState("BASEMAP UNAVAILABLE");
        }
      });
    }
    activeBasemap.addTo(map);
    basemapSelect?.addEventListener("change", () => {
      switchedToFallback = basemapSelect.value === "osm";
      activateBasemap(basemapSelect.value);
    });

    let boundaryLayer = null;
    let boundaryBounds = null;
    try {
      const response = await fetch(assetUrl("RFO.json"));
      if (!response.ok) throw new Error(`Boundary request returned ${response.status}`);
      const boundary = await response.json();
      boundaryLayer = L.geoJSON(boundary, {
        style: { color: "#9b783d", weight: 2.2, opacity: 0.97, fillColor: "#607c63", fillOpacity: 0.13 },
        onEachFeature: (_feature, layer) => layer.bindPopup(
          "<b>Okapi Wildlife Reserve</b><br>Source boundary · GeoJSON WGS84<br>Area: 1,393,822 ha (UTM 35N)<br><i>Mining screening incomplete</i>"
        )
      }).addTo(map);
      boundaryBounds = boundaryLayer.getBounds();
      if (!boundaryBounds.isValid()) throw new Error("Boundary GeoJSON contains no mappable coordinates");
      map.fitBounds(boundaryBounds, { padding: [30, 30], maxZoom: 10 });
      updateEngineState("BOUNDARY LOADED");
    } catch (error) {
      console.error("Could not load the RFO boundary:", error);
      updateEngineState("BOUNDARY UNAVAILABLE");
      if (boundaryLayer) map.removeLayer(boundaryLayer);
    }

    document.getElementById("zoom-to-boundary")?.addEventListener("click", () => {
      if (boundaryBounds?.isValid()) map.fitBounds(boundaryBounds, { padding: [30, 30], maxZoom: 10, animate: true });
    });
    document.getElementById("boundary-toggle")?.addEventListener("change", (event) => {
      if (!boundaryLayer) return;
      if (event.currentTarget.checked) boundaryLayer.addTo(map);
      else map.removeLayer(boundaryLayer);
    });

    map.on("mousemove", (event) => {
      const coordinates = document.getElementById("map-coordinates");
      if (coordinates) coordinates.textContent = `${event.latlng.lat.toFixed(5)}° N  /  ${event.latlng.lng.toFixed(5)}° E`;
    });
    map.on("mouseout", () => {
      const coordinates = document.getElementById("map-coordinates");
      if (coordinates) coordinates.textContent = "Move over map for coordinates";
    });

    map.whenReady(() => {
      requestAnimationFrame(() => map.invalidateSize({ pan: false }));
      if ("ResizeObserver" in window) {
        const resizeObserver = new ResizeObserver(() => map.invalidateSize({ pan: false }));
        resizeObserver.observe(mapElement);
      } else {
        window.addEventListener("resize", () => map.invalidateSize({ pan: false }));
      }
    });

    const updatePeriod = () => {
      const period = periodFilter?.value || "all";
      const year = yearFilter?.value || "all";
      const records = sceneData.filter((row) =>
        (period === "all" || row.quarter === period) &&
        (year === "all" || String(row.year) === year)
      );
      const catalogTotal = records.reduce((sum, row) => sum + row.catalogRecords, 0);
      const chosenPeriod = period === "all" ? "ALL PERIODS" : period;
      const chosenYear = year === "all" ? "2025 + 2026" : year;
      if (mapPeriodLabel) mapPeriodLabel.textContent = `${chosenYear} · ${chosenPeriod} · ${catalogTotal} CATALOG RECORDS`;
      if (caption) {
        caption.textContent = period === "all" && year === "all"
          ? "Boundary shown. No mining detections are available from this incomplete assessment."
          : `${chosenYear} / ${chosenPeriod}: ${catalogTotal} Sentinel-2 records matched. This is imagery availability—not mining detections.`;
      }
      if (sceneChart) {
        const periods = ["Q1", "Q2", "Q3", "Q4"];
        sceneChart.data.datasets[0].data = periods.map((q) =>
          sceneData.find((row) => row.year === 2025 && row.quarter === q && (period === "all" || q === period))?.catalogRecords ?? 0
        );
        sceneChart.data.datasets[1].data = periods.map((q) =>
          sceneData.find((row) => row.year === 2026 && row.quarter === q && (period === "all" || q === period))?.catalogRecords ?? 0
        );
        sceneChart.data.datasets[0].hidden = year === "2026";
        sceneChart.data.datasets[1].hidden = year === "2025";
        sceneChart.update();
      }
    };
    periodFilter?.addEventListener("change", updatePeriod);
    yearFilter?.addEventListener("change", updatePeriod);
    updatePeriod();
  }

  function loadLeafletFallback() {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js";
      script.crossOrigin = "anonymous";
      script.onload = () => window.L ? resolve() : reject(new Error("Leaflet did not initialize"));
      script.onerror = () => reject(new Error("Leaflet fallback CDN request failed"));
      document.head.append(script);
    });
  }

  function initializeCharts() {
    if (!window.Chart) return;
    const sceneCanvas = document.getElementById("scene-chart");
    if (sceneCanvas) {
      sceneChart = new Chart(sceneCanvas, {
        type: "bar",
        data: {
          labels: ["Q1 · Jan–Mar", "Q2 · Apr–Jun", "Q3 · Jul–Aug", "Q4 · Sep–Oct"],
          datasets: [
            { label: "2025 baseline catalog records", data: sceneData.filter((x) => x.year === 2025).map((x) => x.catalogRecords), backgroundColor: "#94a997", borderRadius: 5, maxBarThickness: 26 },
            { label: "2026 analysis catalog records", data: sceneData.filter((x) => x.year === 2026).map((x) => x.catalogRecords), backgroundColor: "#c9a65d", borderRadius: 5, maxBarThickness: 26 }
          ]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: { backgroundColor: "#173a30", padding: 11, displayColors: true, callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y} records` } }
          },
          scales: {
            x: { grid: { display: false }, border: { display: false }, ticks: { color: "#78847b", font: { family: "DM Sans", size: 9 } } },
            y: { beginAtZero: true, border: { display: false, dash: [3, 4] }, grid: { color: "#edf0ec" }, ticks: { color: "#909a92", stepSize: 50, font: { family: "DM Sans", size: 9 } }, title: { display: true, text: "Catalog scene records", color: "#8b958d", font: { family: "DM Sans", size: 9 } } }
          }
        }
      });
    }
    const triageCanvas = document.getElementById("triage-chart");
    if (triageCanvas) {
      new Chart(triageCanvas, {
        type: "doughnut",
        data: { labels: ["Priority-selected for attempted screen", "Other catalog records"], datasets: [{ data: [96, 1220], backgroundColor: ["#c5a35b", "#e8ede7"], borderWidth: 0, hoverOffset: 4 }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: "77%", plugins: { legend: { display: false }, tooltip: { backgroundColor: "#173a30", padding: 10, callbacks: { label: (ctx) => ` ${ctx.label}: ${ctx.parsed} records` } } } }
      });
    }
  }

  function initializeDownloads() {
    const grid = document.getElementById("download-grid");
    if (!grid) return;
    const cards = [...grid.querySelectorAll(".download-card")];
    const search = document.getElementById("download-search");
    const empty = document.getElementById("empty-search");
    let activeCategory = "all";

    function update() {
      const term = (search?.value || "").trim().toLowerCase();
      let shown = 0;
      for (const card of cards) {
        const categoryMatch = activeCategory === "all" || card.dataset.category === activeCategory;
        const searchMatch = !term || `${card.dataset.search} ${card.textContent}`.toLowerCase().includes(term);
        const visible = categoryMatch && searchMatch;
        card.hidden = !visible;
        shown += Number(visible);
      }
      if (empty) empty.hidden = shown > 0;
    }
    search?.addEventListener("input", update);
    document.querySelectorAll("[data-download-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        activeCategory = button.dataset.downloadFilter || "all";
        document.querySelectorAll("[data-download-filter]").forEach((chip) => {
          const active = chip === button;
          chip.classList.toggle("active", active);
          chip.setAttribute("aria-pressed", String(active));
        });
        update();
      });
      button.setAttribute("aria-pressed", String(button.classList.contains("active")));
    });
  }
})();
