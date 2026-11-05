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
    const caption = document.getElementById("map-caption");
    const periodFilter = document.getElementById("period-filter");
    const mapPeriodLabel = document.getElementById("map-period-label");
    if (!window.L) {
      caption.textContent = "The map library did not load. The boundary GeoJSON remains available from Data & downloads.";
      return;
    }

    const map = L.map(mapElement, { zoomControl: false, scrollWheelZoom: false, preferCanvas: true }).setView([1.72, 28.53], 9);
    L.control.zoom({ position: "topright" }).addTo(map);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    try {
      const response = await fetch(assetUrl("RFO.json"));
      if (!response.ok) throw new Error(`Boundary request returned ${response.status}`);
      const boundary = await response.json();
      const outline = L.geoJSON(boundary, {
        style: { color: "#b28a42", weight: 2.4, opacity: 0.96, fillColor: "#547252", fillOpacity: 0.17 },
        onEachFeature: (_feature, layer) => layer.bindPopup(
          "<b>Okapi Wildlife Reserve</b><br>Source boundary · GeoJSON WGS84<br>Area: 1,393,822 ha (UTM 35N)"
        )
      }).addTo(map);
      map.fitBounds(outline.getBounds(), { padding: [25, 25] });
      L.control.layers(null, { "RFO reserve boundary": outline }, { position: "topright", collapsed: true }).addTo(map);
    } catch (error) {
      console.error("Could not load the RFO boundary:", error);
      caption.textContent = "The reserve boundary could not be loaded. Check network access; the source GeoJSON is available under Data & downloads.";
    }

    periodFilter?.addEventListener("change", () => {
      const period = periodFilter.value;
      const records = sceneData.filter((row) => period === "all" || row.quarter === period);
      const catalogTotal = records.reduce((sum, row) => sum + row.catalogRecords, 0);
      const label = period === "all" ? "ALL PERIODS" : `${period} · ${catalogTotal} CATALOG RECORDS`;
      mapPeriodLabel.textContent = `BOUNDARY VIEW · ${label}`;
      caption.textContent = period === "all"
        ? "The map displays the supplied RFO boundary only. No site locations are shown because a complete, verified detection layer is unavailable."
        : `${period} selected: ${catalogTotal} Sentinel-2 catalog records matched across 2025 and 2026. This is imagery availability—not mining detections. No candidate layer is available.`;
    });
  }

  function initializeCharts() {
    if (!window.Chart) return;
    const sceneCanvas = document.getElementById("scene-chart");
    if (sceneCanvas) {
      new Chart(sceneCanvas, {
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
