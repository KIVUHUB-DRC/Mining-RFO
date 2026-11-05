(() => {
  "use strict";

  const siteRoot = new URL(".", window.location.href);
  const assetUrl = (path) => new URL(path, siteRoot).href;
  let sceneData = [
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
  let coverageDataPromise = null;

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
  initializeCharts();
  initializeDownloads();
  if (mapElement) {
    initializeMap();
    initializeCatalogData();
  }

  function loadCoverageData() {
    if (!coverageDataPromise) {
      coverageDataPromise = fetch(assetUrl("RFO_Mining_Analysis_2026/gis/coverage.geojson"))
        .then((response) => {
          if (!response.ok) throw new Error(`Catalog coverage request returned ${response.status}`);
          return response.json();
        });
    }
    return coverageDataPromise;
  }

  function aggregateCoverage(features) {
    const aggregated = new Map();
    for (const feature of features) {
      const tile = feature.properties.tile;
      for (const record of feature.properties.periods) {
        const key = `${record.year}-${record.period}`;
        if (!aggregated.has(key)) {
          aggregated.set(key, {
            year: record.year,
            quarter: record.period,
            catalogRecords: 0,
            selectedForScreening: 0,
            cloudTotal: 0,
            firstDate: record.first_date,
            lastDate: record.last_date,
            tileIds: new Set()
          });
        }
        const row = aggregated.get(key);
        row.catalogRecords += record.count;
        row.selectedForScreening += record.selected;
        row.cloudTotal += record.mean_cloud_pct * record.count;
        row.firstDate = record.first_date < row.firstDate ? record.first_date : row.firstDate;
        row.lastDate = record.last_date > row.lastDate ? record.last_date : row.lastDate;
        if (record.count > 0) row.tileIds.add(tile);
      }
    }
    return [...aggregated.values()]
      .sort((a, b) => a.year - b.year || a.quarter.localeCompare(b.quarter))
      .map((row) => ({
        year: row.year,
        quarter: row.quarter,
        catalogRecords: row.catalogRecords,
        selectedForScreening: row.selectedForScreening,
        tiles: row.tileIds.size,
        firstDate: row.firstDate,
        lastDate: row.lastDate,
        meanSceneCloudPct: row.catalogRecords ? row.cloudTotal / row.catalogRecords : 0
      }));
  }

  async function initializeCatalogData() {
    const yearFilter = document.getElementById("year-filter");
    const periodFilter = document.getElementById("period-filter");
    const refresh = () => {
      const year = yearFilter?.value || "all";
      const period = periodFilter?.value || "all";
      const records = sceneData.filter((row) =>
        (year === "all" || String(row.year) === year) &&
        (period === "all" || row.quarter === period)
      );
      const total = records.reduce((sum, row) => sum + row.catalogRecords, 0);
      const selected = records.reduce((sum, row) => sum + row.selectedForScreening, 0);
      const tiles = records.length ? Math.max(...records.map((row) => row.tiles)) : 0;
      const meanCloud = total
        ? records.reduce((sum, row) => sum + row.meanSceneCloudPct * row.catalogRecords, 0) / total
        : 0;
      renderCatalogData(total, selected, tiles, meanCloud, year, period);
      if (sceneChart) {
        const periods = ["Q1", "Q2", "Q3", "Q4"];
        sceneChart.data.datasets[0].data = periods.map((q) =>
          period === "all" || q === period
            ? sceneData.find((row) => row.year === 2025 && row.quarter === q)?.catalogRecords ?? 0
            : 0
        );
        sceneChart.data.datasets[1].data = periods.map((q) =>
          period === "all" || q === period
            ? sceneData.find((row) => row.year === 2026 && row.quarter === q)?.catalogRecords ?? 0
            : 0
        );
        sceneChart.data.datasets[0].hidden = year === "2026";
        sceneChart.data.datasets[1].hidden = year === "2025";
        sceneChart.update();
      }
    };
    yearFilter?.addEventListener("change", refresh);
    periodFilter?.addEventListener("change", refresh);
    try {
      const coverage = await loadCoverageData();
      sceneData = aggregateCoverage(coverage.features);
      refresh();
    } catch (error) {
      console.error("Could not load catalog data for the dashboard:", error);
      const tableBody = document.getElementById("catalog-table-body");
      if (tableBody) tableBody.innerHTML = '<tr><td colspan="6">Catalog data could not be loaded. Download the scene register from Data &amp; downloads.</td></tr>';
    }
  }

  async function initializeMap() {
    const caption = document.getElementById("map-caption-text") || document.getElementById("map-caption");
    const periodFilter = document.getElementById("period-filter");
    const yearFilter = document.getElementById("year-filter");
    const mapPeriodLabel = document.getElementById("map-period-label");
    const engineState = document.getElementById("map-engine-state");
    const basemapSelect = document.getElementById("basemap-select");
    const coverageToggle = document.getElementById("coverage-toggle");
    const activeLayerCount = document.getElementById("active-layer-count");
    let selectedYear = yearFilter?.value || "all";
    let selectedPeriod = periodFilter?.value || "all";
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
    let coverageLayer = null;
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
    } catch (error) {
      console.error("Could not load the RFO boundary:", error);
      updateEngineState("BOUNDARY UNAVAILABLE");
      if (caption) caption.textContent = "The reserve boundary could not be loaded. Download the source GeoJSON from Data & downloads.";
      if (boundaryLayer) map.removeLayer(boundaryLayer);
    }

    try {
      const coverage = await loadCoverageData();
      coverageLayer = L.geoJSON(coverage, {
        style: (feature) => coverageStyle(feature),
        onEachFeature: (feature, layer) => {
          layer.bindPopup(() => coveragePopup(feature.properties));
          layer.on("mouseover", () => layer.setStyle({ weight: 2.4, fillOpacity: 0.48 }));
          layer.on("mouseout", () => coverageLayer?.resetStyle(layer));
        }
      });
      if (coverageToggle?.checked !== false) coverageLayer.addTo(map);
      if (boundaryLayer && map.hasLayer(boundaryLayer)) boundaryLayer.bringToFront();
      sceneData = aggregateCoverage(coverage.features);
      updateEngineState("BOUNDARY + CATALOG DATA");
    } catch (error) {
      console.error("Could not load clipped Sentinel-2 catalog footprints:", error);
      updateEngineState(boundaryBounds ? "BOUNDARY ONLY · DATA UNAVAILABLE" : "MAP DATA UNAVAILABLE");
      if (caption) caption.textContent = "The reserve boundary is shown, but the Sentinel-2 coverage layer could not be loaded. No mining detections are available.";
    }

    function coverageStyle(feature) {
      const matching = feature?.properties?.periods?.filter((record) =>
        (selectedYear === "all" || String(record.year) === selectedYear) &&
        (selectedPeriod === "all" || record.period === selectedPeriod)
      ) || [];
      const count = matching.reduce((total, record) => total + record.count, 0);
      return {
        color: "#326f67",
        weight: 1.15,
        opacity: count ? 0.9 : 0,
        fillColor: "#6da99e",
        fillOpacity: count ? 0.34 : 0
      };
    }

    function coveragePopup(properties) {
      const records = properties.periods.filter((record) =>
        (selectedYear === "all" || String(record.year) === selectedYear) &&
        (selectedPeriod === "all" || record.period === selectedPeriod)
      );
      const count = records.reduce((total, record) => total + record.count, 0);
      const selected = records.reduce((total, record) => total + record.selected, 0);
      const meanCloud = count
        ? records.reduce((total, record) => total + record.mean_cloud_pct * record.count, 0) / count
        : null;
      const dates = records.flatMap((record) => [record.first_date, record.last_date]).sort();
      const periodName = selectedPeriod === "all" ? "all periods" : selectedPeriod;
      const yearName = selectedYear === "all" ? "2025–2026" : selectedYear;
      const sourceRecord = records.at(-1);
      const sourceLabel = sourceRecord ? `${sourceRecord.year} ${sourceRecord.period}` : "catalog";
      return `<b>Sentinel-2 tile ${escapeHtml(properties.tile)}</b><br>${count} catalog scenes · ${selected} priority-selected${meanCloud === null ? "" : `<br>Mean scene cloud cover: ${meanCloud.toFixed(1)}%`}<br>${escapeHtml(yearName)} · ${escapeHtml(periodName)}${dates.length ? `<br>Catalog dates: ${escapeHtml(dates[0])} to ${escapeHtml(dates.at(-1))}` : ""}<br><i>Imagery coverage only—not a mining detection.</i>${sourceRecord ? `<br><a href="${escapeHtml(sourceRecord.source_item)}" target="_blank" rel="noopener">Open representative ${escapeHtml(sourceLabel)} STAC scene ↗</a>` : ""}`;
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (character) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      })[character]);
    }

    function updateActiveLayerCount() {
      if (!activeLayerCount) return;
      const count = Number(Boolean(boundaryLayer && map.hasLayer(boundaryLayer))) +
        Number(Boolean(coverageLayer && map.hasLayer(coverageLayer)));
      activeLayerCount.textContent = `${count} ACTIVE`;
    }

    function updateCoverageStyles() {
      coverageLayer?.eachLayer((layer) => {
        if (layer.feature) layer.setStyle(coverageStyle(layer.feature));
      });
    }

    document.getElementById("zoom-to-boundary")?.addEventListener("click", () => {
      if (boundaryBounds?.isValid()) map.fitBounds(boundaryBounds, { padding: [30, 30], maxZoom: 10, animate: true });
    });
    document.getElementById("boundary-toggle")?.addEventListener("change", (event) => {
      if (!boundaryLayer) return;
      if (event.currentTarget.checked) boundaryLayer.addTo(map);
      else map.removeLayer(boundaryLayer);
      if (event.currentTarget.checked) boundaryLayer.bringToFront();
      updateActiveLayerCount();
    });
    coverageToggle?.addEventListener("change", (event) => {
      if (!coverageLayer) return;
      if (event.currentTarget.checked) coverageLayer.addTo(map);
      else map.removeLayer(coverageLayer);
      if (boundaryLayer && map.hasLayer(boundaryLayer)) boundaryLayer.bringToFront();
      updateActiveLayerCount();
    });
    updateActiveLayerCount();

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
      selectedYear = year;
      selectedPeriod = period;
      const records = sceneData.filter((row) =>
        (period === "all" || row.quarter === period) &&
        (year === "all" || String(row.year) === year)
      );
      const catalogTotal = records.reduce((sum, row) => sum + row.catalogRecords, 0);
      const selectedTotal = records.reduce((sum, row) => sum + row.selectedForScreening, 0);
      const tileTotal = records.length ? Math.max(...records.map((row) => row.tiles)) : 0;
      const meanCloud = catalogTotal
        ? records.reduce((sum, row) => sum + row.meanSceneCloudPct * row.catalogRecords, 0) / catalogTotal
        : 0;
      const chosenPeriod = period === "all" ? "ALL PERIODS" : period;
      const chosenYear = year === "all" ? "2025 + 2026" : year;
      if (mapPeriodLabel) mapPeriodLabel.textContent = `${chosenYear} · ${chosenPeriod} · ${catalogTotal.toLocaleString()} CATALOG RECORDS`;
      renderCatalogData(catalogTotal, selectedTotal, tileTotal, meanCloud, year, period);
      if (caption) {
        caption.textContent = `${catalogTotal.toLocaleString()} Sentinel-2 catalog records across six reserve-intersecting tile footprints. Imagery coverage only—not mining detections. The reserve-wide assessment is incomplete.`;
      }
      updateCoverageStyles();
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

  function renderCatalogData(total, selected, tiles, meanCloud, year, period) {
    const values = {
      "catalog-total": total.toLocaleString(),
      "catalog-selected": selected.toLocaleString(),
      "catalog-tiles": `${tiles} / 6`,
      "catalog-cloud": total ? `${meanCloud.toFixed(1)}%` : "—"
    };
    for (const [id, value] of Object.entries(values)) {
      const element = document.getElementById(id);
      if (element) element.textContent = value;
    }

    const tableBody = document.getElementById("catalog-table-body");
    if (!tableBody) return;
    const rows = sceneData.map((row) => {
      const visible = (year === "all" || String(row.year) === year) &&
        (period === "all" || row.quarter === period);
      return `<tr${visible ? "" : " hidden"}><td>${row.year}</td><td>${row.quarter}</td><td>${row.catalogRecords.toLocaleString()}</td><td>${row.selectedForScreening.toLocaleString()}</td><td>${row.meanSceneCloudPct.toFixed(1)}%</td><td>${row.firstDate} – ${row.lastDate}</td></tr>`;
    });
    tableBody.innerHTML = rows.join("") || '<tr><td colspan="6">No catalog records match this filter.</td></tr>';
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
