// ============================================================================
// Hunting Buddy v1.4 — Tony Fadell Precision Instrument UX
// Okabe-Ito colorblind-safe palette, 3-tab decluttered sidebar, floating
// Layers popover, editorial field briefing renderer (unescapes \n & entities),
// persistent Hunting Plans & removable GeoJSON map annotations.
// Strictly XSS-safe DOM construction (createElement + textContent only).
// ============================================================================
(function () {
  'use strict';

  function decodeHtmlEntities(str) {
    return String(str || '')
      .replace(/&quot;/g, '"')
      .replace(/&#34;/g, '"')
      .replace(/&apos;/g, "'")
      .replace(/&#39;/g, "'")
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&nbsp;/g, ' ');
  }

  function normalizeReportText(raw) {
    return decodeHtmlEntities(raw)
      .replace(/\\r\\n/g, '\n')
      .replace(/\\n/g, '\n')
      .replace(/\\t/g, '  ')
      .replace(/\r\n/g, '\n')
      .trim();
  }

  function registerHuntingBuddy() {
    if (!window.LodgeModules) return;
    window.LodgeModules.register('hunting-buddy', {
      mount(container, ctx) {
        const el = ctx.el;
        let state = {
          agentName: (ctx && ctx.agentName) || (window.LodgeModules && window.LodgeModules.getAgentName && window.LodgeModules.getAgentName()) || 'Agent',
          properties: [],
          active: null,
          hunters: [],
          publicLands: [],
          viewportParcels: [],
          weather: null,
          windDeg: 315,
          windMph: 8,
          thermalMode: 'neutral',
          basemap: 'hybrid',
          sidebarTab: 'camp', // camp | plans | gps
          importOpen: false,
          layersOpen: false,
          layers: {
            parcels: true,
            publicLands: true,
            waypoints: true,
            scent: true,
            annotations: true,
            hunters: true,
            topoContours: true,
            topoLandmarks: true,
            streams: true,
            roads: true,
            fisheries: false,
            waterbodies: true,
            chartOverlays: true,
            bathymetry: true,
            fishStructures: true,
            waterFlows: true,
          },
          appMode: 'hunt', // hunt | fish
          plans: [],
          annotations: [],
          terrain: null,
          topoLandmarks: [],
          routes: [],
          waterbodies: [],
          selectedWaterbodyId: null,
          chartDataUrlCache: {},
          chartImgCache: {},
          aligningWb: null,
          alignBounds: null,
          alignOpacity: 0.80,
          alignRotation: 0,
          alignFlipH: false,
          alignFlipV: false,
          alignBlendMode: 'cyan_transparent',
          alignCrop: { left: 0, top: 0, right: 1, bottom: 1 },
          cropPanelActive: false,
          twoPointSnapActive: false,
          snapStep: 0, // 0=chartA, 1=mapA, 2=chartB, 3=mapB
          snapPts: { chartA: null, mapA: null, chartB: null, mapB: null },
          fishStructures: [],
          waterFlows: [],
          bathyPoints: [],
          bathyGrids: [],
          fishAnalysis: null,
          riverGauges: [],
          publicStreams: [],
          publicRoads: [],
          publicFisheries: [],
          providersOpen: false,
          currentWeatherHour: null,
          weatherFetchedAt: 0,
          manualWindOverride: false,
          activePlanView: null,
          dropMode: false,
          dropKind: 'stand',
          annotateDropMode: false,
          annotateKind: 'callout',
          topoDropMode: false,
          topoDropKind: 'water_source',
          fishStructDropMode: false,
          fishStructKind: 'drop_off',
          flowDropMode: false,
          depthDropMode: false,
          traceMode: false,
          traceKind: 'stream',
          traceContourMode: false,
          tracePoints: [],
          drawBoundsMode: false,
          drawTarget: 'camp', // camp | waterbody
          drawPoints: [],
          drawnBoundary: null,
          drawnAcres: 0,
          beaconWatchId: null,
          beaconHeading: 0,
          pollTimer: null,
        };

        let map = null;
        let baseLayer = null;
        let labelLayer = null;
        let publicLandLayer = null;
        let parcelLayer = null;
        let waypointLayer = null;
        let scentLayer = null;
        let planAnnotationsLayer = null;
        let hunterLayer = null;
        let topoContourLayer = null;
        let topoLandmarkLayer = null;
        let streamLayer = null;
        let roadLayer = null;
        let fisheryLayer = null;
        let waterbodyLayer = null;
        let chartOverlayLayer = null;
        let alignOverlayLayer = null;
        let bathyLayer = null;
        let fishStructureLayer = null;
        let waterFlowLayer = null;
        let drawLayer = null;
        let tracePreviewLayer = null;

        // ── Safe Editorial Report Renderer (zero unsafe sinks) ───────────
        function isRawAgentTranscript(raw) {
          const s = String(raw || '');
          if (!s) return false;
          const head = s.slice(0, 1200);
          const tail = s.slice(-1200);
          return head.includes('── Agentic Loop:')
            || head.includes('● [fifo_parallel]')
            || head.includes('Loop Spec:')
            || head.includes('FIFO Chan:')
            || tail.includes('✓ [gate] Verification passed')
            || tail.includes('✓ Agentic Loop completed:');
        }

        function cleanInlineMath(text) {
          return String(text || '')
            .replace(/\$([^$\n]{1,40})\$/g, (_, expr) => expr
              .replace(/\\text\{([^}]*)\}/g, '$1')
              .replace(/\\circ/g, '°')
              .replace(/\\times/g, '×')
              .replace(/\\pm/g, '±')
              .replace(/\\/g, '')
              .trim());
        }

        function appendInlineFormatted(parent, text) {
          const clean = cleanInlineMath(text);
          const parts = clean.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g);
          parts.forEach((tok) => {
            if (!tok) return;
            if (tok.startsWith('**') && tok.endsWith('**') && tok.length > 4) {
              parent.appendChild(el('strong', { text: tok.slice(2, -2) }));
            } else if (tok.startsWith('`') && tok.endsWith('`') && tok.length > 2) {
              parent.appendChild(el('code', { text: tok.slice(1, -1) }));
            } else if (tok.startsWith('*') && tok.endsWith('*') && tok.length > 2) {
              parent.appendChild(el('em', { text: tok.slice(1, -1) }));
            } else {
              parent.appendChild(document.createTextNode(tok));
            }
          });
        }

        function splitMarkdownTableRow(line) {
          let s = String(line || '').trim();
          if (s.startsWith('|')) s = s.slice(1);
          if (s.endsWith('|')) s = s.slice(0, -1);
          return s.split('|').map((c) => c.trim());
        }

        function isTableSeparatorLine(line) {
          const s = String(line || '').trim();
          if (!s.includes('|') || !s.includes('-')) return false;
          const cells = splitMarkdownTableRow(s);
          return cells.length >= 2 && cells.every((c) => /^:?-{2,}:?$/.test(c));
        }

        function renderSafeReport(target, rawText) {
          target.replaceChildren();
          const cleaned = normalizeReportText(rawText);
          if (!cleaned) {
            target.appendChild(el('p', { className: 'hb-report-p', text: 'No tactical briefing prose recorded.' }));
            return;
          }
          const lines = cleaned.split('\n');
          let ul = null;
          const flushList = () => { if (ul) { target.appendChild(ul); ul = null; } };

          for (let i = 0; i < lines.length; i += 1) {
            const line = lines[i].trim();
            if (!line) { flushList(); continue; }

            // Horizontal rule (---, ***, ___)
            if (/^([-*_])\1{2,}$/.test(line)) {
              flushList();
              target.appendChild(el('hr', { className: 'hb-report-hr' }));
              continue;
            }

            // GitHub-Flavored Markdown Table (header row + separator row + 1+ body rows)
            if (line.includes('|') && i + 1 < lines.length && isTableSeparatorLine(lines[i + 1])) {
              flushList();
              const headers = splitMarkdownTableRow(line);
              const aligns = splitMarkdownTableRow(lines[i + 1]).map((sep) => {
                if (sep.startsWith(':') && sep.endsWith(':')) return 'center';
                if (sep.endsWith(':')) return 'right';
                return 'left';
              });
              i += 1; // skip separator row

              const theadTr = el('tr');
              headers.forEach((hTxt, ci) => {
                const th = el('th', { attrs: { style: `text-align:${aligns[ci] || 'left'}` } });
                appendInlineFormatted(th, hTxt);
                theadTr.appendChild(th);
              });
              const tbody = el('tbody');
              while (i + 1 < lines.length) {
                const nextLine = lines[i + 1].trim();
                if (!nextLine || !nextLine.includes('|') || isTableSeparatorLine(nextLine)) break;
                i += 1;
                const cells = splitMarkdownTableRow(nextLine);
                const tr = el('tr');
                headers.forEach((_, ci) => {
                  const td = el('td', { attrs: { style: `text-align:${aligns[ci] || 'left'}` } });
                  appendInlineFormatted(td, cells[ci] != null ? cells[ci] : '');
                  tr.appendChild(td);
                });
                tbody.appendChild(tr);
              }
              const tbl = el('table', { className: 'hb-report-table' }, [
                el('thead', {}, [theadTr]),
                tbody,
              ]);
              target.appendChild(el('div', { className: 'hb-table-wrap' }, [tbl]));
              continue;
            }

            // Markdown Headings (# / ## / ### / ####)
            const hMatch = line.match(/^(#{1,4})\s+(.+)$/);
            if (hMatch) {
              flushList();
              const lvl = hMatch[1].length;
              const cls = lvl === 1 ? 'hb-report-h1' : (lvl <= 3 ? 'hb-report-h2' : 'hb-report-h3');
              const h = el('div', { className: cls });
              appendInlineFormatted(h, hMatch[2]);
              target.appendChild(h);
              continue;
            }

            // Blockquote (> ...)
            const bqMatch = line.match(/^>\s+(.+)$/);
            if (bqMatch) {
              flushList();
              const bq = el('div', { className: 'hb-report-quote' });
              appendInlineFormatted(bq, bqMatch[1]);
              target.appendChild(bq);
              continue;
            }

            // Bullet or numbered list items
            const liMatch = line.match(/^[-*•]\s+(.+)$/) || line.match(/^\d+\.\s+(.+)$/);
            if (liMatch) {
              if (!ul) ul = el('ul');
              const li = el('li');
              appendInlineFormatted(li, liMatch[1]);
              ul.appendChild(li);
              continue;
            }

            // Structured field callout rows: "**Scenario:** ..." or "Shooting light: ..."
            const kvBold = line.match(/^\*\*([^*:]{2,28}):?\*\*:?\s+(.+)$/);
            const kvPlain = !kvBold && line.match(/^([A-Za-z][A-Za-z0-9 /&()-]{2,24}):\s+(.+)$/);
            const kv = kvBold || kvPlain;
            if (kv && !line.includes('|')) {
              flushList();
              const valNode = el('div', { className: 'hb-report-v' });
              appendInlineFormatted(valNode, kv[2]);
              target.appendChild(el('div', { className: 'hb-report-kv' }, [
                el('div', { className: 'hb-report-k', text: kv[1].trim() }),
                valNode,
              ]));
              continue;
            }

            flushList();
            const p = el('p', { className: 'hb-report-p' });
            appendInlineFormatted(p, line);
            target.appendChild(p);
          }
          flushList();
        }

        // ── Root Layout ──────────────────────────────────────────────────
        const root = el('div', { className: 'hb-root' });
        const sidebar = el('div', { className: 'hb-sidebar' });
        const mapWrap = el('div', { className: 'hb-map-wrap' });
        root.appendChild(sidebar);
        root.appendChild(mapWrap);
        container.replaceChildren(root);

        const statusLine = el('span', { text: 'Ready. Select a camp or click Sync GIS for 30-day cached WI parcels & public land.' });
        const conflictBox = el('div', { className: 'hb-banner safe', text: '✓ Scent analysis ready.' });

        // ── Mode Switcher (Hunt | Fish) + 3-Segment Navigation ───────────
        const modeBtnHunt = el('button', { className: 'hb-seg-btn active', text: '🦌 Hunt Mode' });
        const modeBtnFish = el('button', { className: 'hb-seg-btn', text: '🎣 Fish Mode' });
        const modeRail = el('div', { className: 'hb-mode-rail' }, [modeBtnHunt, modeBtnFish]);
        const tabBtnCamp = el('button', { className: 'hb-seg-btn active', text: 'Camp & Field' });
        const tabBtnPlans = el('button', { className: 'hb-seg-btn', text: 'Plans' });
        const tabBtnGps = el('button', { className: 'hb-seg-btn', text: 'Field GPS' });
        const sidebarNav = el('div', { className: 'hb-seg-rail full' }, [tabBtnCamp, tabBtnPlans, tabBtnGps]);
        const sidebarBody = el('div', { className: 'hb-sidebar-body' });
        sidebar.appendChild(modeRail);
        sidebar.appendChild(sidebarNav);
        sidebar.appendChild(sidebarBody);

        // ── TAB 1: Camp, Wind & Stands ───────────────────────────────────
        const propSelect = el('select', { className: 'hb-select' });
        const privacyBtn = el('button', { className: 'hb-btn sm', text: 'Public' });
        const drawBoundsBtn = el('button', { className: 'hb-btn primary sm', text: 'Draw Bounds', attrs: { title: 'Click corners on the map to outline your camp without typing coordinates' } });
        const toggleImportBtn = el('button', { className: 'hb-btn sm', text: '+ Add Camp' });
        const nameInput = el('input', { className: 'hb-input', placeholder: 'Camp name (e.g. Northwoods 40)' });
        const latInput = el('input', { className: 'hb-input mono', placeholder: 'Lat (auto from map)' });
        const lonInput = el('input', { className: 'hb-input mono', placeholder: 'Lon (auto from map)' });
        const drawnBoundsPill = el('div', { className: 'hb-bounds-pill', attrs: { style: 'display:none' } });
        const fileInput = el('input', { type: 'file', className: 'hb-file-hidden', attrs: { accept: '.gpx,.kml,.kmz,.geojson,.json,.pdf' } });
        const fileLabelTxt = el('span', { text: 'Import GPX, KML, GeoJSON, or PDF…' });
        const dropZone = el('div', {
          className: 'hb-dropzone',
          on: { click: () => fileInput.click() },
        }, [fileLabelTxt, el('span', { className: 'hb-mono-tag', text: 'Browse' })]);
        fileInput.addEventListener('change', () => {
          const f = fileInput.files && fileInput.files[0];
          fileLabelTxt.textContent = f ? f.name : 'Import GPX, KML, GeoJSON, or PDF…';
        });

        const importDrawer = el('div', { className: 'hb-disclosure', attrs: { style: 'display:none' } }, [
          nameInput,
          drawnBoundsPill,
          el('div', { className: 'hb-row nowrap' }, [latInput, lonInput]),
          el('div', { className: 'hb-row' }, [
            el('button', { className: 'hb-btn primary sm', text: 'Save Camp & GIS', on: { click: () => doImportCoords() } }),
            el('button', {
              className: 'hb-btn sm',
              text: 'Draw on Map',
              on: { click: () => startDrawBoundsMode('camp') },
            }),
            el('button', {
              className: 'hb-btn sm',
              text: 'Northwoods Demo',
              on: {
                click: () => {
                  nameInput.value = 'Northwoods Demo Camp';
                  latInput.value = '45.9200';
                  lonInput.value = '-89.2500';
                  state.drawnBoundary = null;
                  state.drawnAcres = 0;
                  drawnBoundsPill.style.display = 'none';
                  doImportCoords();
                },
              },
            }),
          ]),
          fileInput,
          dropZone,
          el('button', { className: 'hb-btn sm', text: 'Upload Selected File', on: { click: () => doImportFile() } }),
        ]);

        const toggleProvidersBtn = el('button', { className: 'hb-btn ghost sm', text: 'Sources', attrs: { title: 'Pluggable spatial data sources, health watchdog & offline camp pack' } });
        const offlinePackBtn = el('button', { className: 'hb-btn primary sm', text: 'Offline Pack', on: { click: () => doDownloadOfflinePack() } });
        const checkHealthBtn = el('button', { className: 'hb-btn sm', text: 'Test Providers', on: { click: () => doCheckProvidersHealth() } });
        const offlineStatsTxt = el('div', { className: 'hb-muted', text: 'Lazy local cache: only your camp cells are cached for offline use.' });
        const providersList = el('div', { className: 'hb-list' });
        const customProvId = el('input', { className: 'hb-input mono', placeholder: 'Provider ID (e.g. mn_parcels)' });
        const customProvLayer = el('select', { className: 'hb-select' }, [
          el('option', { value: 'parcels', text: 'Layer: Parcels' }),
          el('option', { value: 'hydro_waterbodies', text: 'Layer: Lakes & Waterbodies' }),
          el('option', { value: 'hydro_streams', text: 'Layer: Streams' }),
          el('option', { value: 'roads_trails', text: 'Layer: Roads / Trails' }),
          el('option', { value: 'elevation', text: 'Layer: Elevation DEM' }),
          el('option', { value: 'fisheries', text: 'Layer: Fisheries' }),
          el('option', { value: 'public_lands', text: 'Layer: Public Lands' }),
          el('option', { value: 'weather', text: 'Layer: Weather Forecast' }),
        ]);
        const customProvDriver = el('select', { className: 'hb-select' }, [
          el('option', { value: 'arcgis_feature', text: 'Driver: ArcGIS Feature/MapServer' }),
          el('option', { value: 'arcgis_image_dem', text: 'Driver: ArcGIS DEM ImageServer' }),
          el('option', { value: 'overpass_osm', text: 'Driver: OSM Overpass' }),
        ]);
        const customProvUrl = el('input', { className: 'hb-input mono', placeholder: 'Endpoint URL (https://.../query)' });
        const customProvForm = el('div', { className: 'hb-disclosure', attrs: { style: 'display:none' } }, [
          customProvId,
          el('div', { className: 'hb-row nowrap' }, [customProvLayer, customProvDriver]),
          customProvUrl,
          el('button', { className: 'hb-btn primary sm', text: 'Save Custom Source', on: { click: () => doSaveCustomProvider() } }),
        ]);
        const toggleCustomProvBtn = el('button', {
          className: 'hb-btn ghost sm',
          text: '+ Custom Source',
          on: {
            click: () => {
              const show = customProvForm.style.display === 'none';
              customProvForm.style.display = show ? 'flex' : 'none';
            },
          },
        });
        const providersDrawer = el('div', { className: 'hb-disclosure', attrs: { style: 'display:none' } }, [
          offlineStatsTxt,
          el('div', { className: 'hb-row' }, [offlinePackBtn, checkHealthBtn, toggleCustomProvBtn]),
          customProvForm,
          providersList,
        ]);

        const campSection = el('div', { className: 'hb-section' }, [
          el('div', { className: 'hb-section-head' }, [
            el('span', { className: 'hb-eyebrow', text: 'Camp & Ownership' }),
            el('div', { className: 'hb-row' }, [privacyBtn, drawBoundsBtn, toggleImportBtn, toggleProvidersBtn]),
          ]),
          el('div', { className: 'hb-row nowrap' }, [
            propSelect,
            el('button', { className: 'hb-btn sm', text: '↻', attrs: { title: 'Refresh camp data' }, on: { click: () => loadState(propSelect.value ? Number(propSelect.value) : null) } }),
          ]),
          importDrawer,
          providersDrawer,
        ]);

        // Wind, Rich Weather HUD & Explainable Thermals
        const fetchWeatherBtn = el('button', { className: 'hb-btn sm', text: 'Fetch Weather', on: { click: () => fetchWeather() } });
        const windDegInput = el('input', { type: 'range', className: 'hb-range', attrs: { min: '0', max: '355', step: '5' }, value: '315' });
        const windMphInput = el('input', { type: 'range', className: 'hb-range', attrs: { min: '1', max: '30', step: '1' }, value: '8' });
        const windDegNum = el('span', { className: 'hb-slider-num', text: '315°' });
        const windMphNum = el('span', { className: 'hb-slider-num', text: '8 mph' });
        const windHeroVal = el('div', { className: 'hb-wind-val', text: 'NW 315° · 8 mph' });
        const windHeroSub = el('div', { className: 'hb-wind-sub', text: 'Scent → SE' });
        const thermalSel = el('select', { className: 'hb-select' }, [
          el('option', { value: 'neutral', text: 'Thermal: Neutral / Wind-Driven' }),
          el('option', { value: 'upslope_warming', text: 'Thermal: Upslope Warming (AM Rising)' }),
          el('option', { value: 'katabatic_drainage', text: 'Thermal: Katabatic Drainage (PM Sinking)' }),
        ]);
        const thermalCard = el('div', { className: 'hb-thermal-card' });
        const wxGrid = el('div', { className: 'hb-wx-grid', attrs: { style: 'display:none' } });
        const moveCard = el('div', { className: 'hb-move-card', attrs: { style: 'display:none' } });
        const hourScrubber = el('input', { type: 'range', className: 'hb-range', attrs: { min: '0', max: '47', step: '1', style: 'display:none' }, value: '0' });
        const hourLabel = el('div', { className: 'hb-muted', text: 'Click Fetch Weather for live temp, barometer, moon, deer movement score & 48h wind scrubber.' });

        const windSection = el('div', { className: 'hb-section' }, [
          el('div', { className: 'hb-section-head' }, [
            el('span', { className: 'hb-eyebrow', text: 'Weather, Wind & Thermals' }),
            fetchWeatherBtn,
          ]),
          el('div', { className: 'hb-wind-hero' }, [windHeroVal, windHeroSub]),
          wxGrid,
          moveCard,
          el('div', { className: 'hb-slider-row' }, [el('span', { text: 'From' }), windDegInput, windDegNum]),
          el('div', { className: 'hb-slider-row' }, [el('span', { text: 'Speed' }), windMphInput, windMphNum]),
          thermalSel,
          thermalCard,
          hourScrubber,
          hourLabel,
          conflictBox,
        ]);

        // Stands & Waypoints
        const dropBtn = el('button', { className: 'hb-btn sm', text: '+ Drop Pin' });
        const kindSel = el('select', { className: 'hb-select' }, [
          el('option', { value: 'stand', text: 'Stand' }),
          el('option', { value: 'blind', text: 'Blind' }),
          el('option', { value: 'camera', text: 'Trail Cam' }),
          el('option', { value: 'bedding', text: 'Bedding' }),
          el('option', { value: 'scrape', text: 'Scrape' }),
          el('option', { value: 'camp', text: 'Camp' }),
        ]);
        const standList = el('div', { className: 'hb-list' });
        const standsSection = el('div', { className: 'hb-section' }, [
          el('div', { className: 'hb-section-head' }, [
            el('span', { className: 'hb-eyebrow', text: 'Stands & Waypoints' }),
          ]),
          el('div', { className: 'hb-row nowrap' }, [kindSel, dropBtn]),
          standList,
        ]);

        // Topographic Elevation, Landmarks & LiDAR/Topo Dataset Importer
        const contourStepSel = el('select', { className: 'hb-select', attrs: { style: 'max-width:105px', title: 'Contour elevation interval' } }, [
          el('option', { value: '0', text: 'Auto Int.' }),
          el('option', { value: '2', text: '2 ft' }),
          el('option', { value: '5', text: '5 ft' }),
          el('option', { value: '10', text: '10 ft' }),
          el('option', { value: '20', text: '20 ft' }),
        ]);
        const fetchDemBtn = el('button', { className: 'hb-btn primary sm', text: 'Fetch LiDAR DEM', on: { click: () => doFetchDem() } });
        const topoStatsBox = el('div', { className: 'hb-topo-stats', text: 'Click "Fetch LiDAR DEM" for 1m USGS 3DEP elevation contours, hypsometric gradient & thermal drainage arrows.' });
        const topoLegendBox = el('div', { className: 'hb-elev-legend', attrs: { style: 'display:none' } });
        const topoKindSel = el('select', { className: 'hb-select' }, [
          el('option', { value: 'water_source', text: 'Water Source (Creek/Pond)' }),
          el('option', { value: 'ridge', text: 'Ridge / High Point' }),
          el('option', { value: 'saddle', text: 'Saddle / Pinch Point' }),
          el('option', { value: 'drainage', text: 'Drainage / Thermal Draw' }),
          el('option', { value: 'bench', text: 'Bedding Bench' }),
          el('option', { value: 'food_plot', text: 'Food Plot / Oak Flat' }),
          el('option', { value: 'thick_cover', text: 'Thick Cover / Swamp' }),
          el('option', { value: 'access_gate', text: 'Access Gate / Parking' }),
        ]);
        const dropTopoBtn = el('button', { className: 'hb-btn sm', text: '+ Tag Topo' });
        const topoFileInput = el('input', { type: 'file', className: 'hb-file-hidden', attrs: { accept: '.asc,.grd,.csv,.geojson,.json,.gpx,.kml,.kmz' } });
        const topoFileLabel = el('span', { text: 'Upload LiDAR/DEM (.asc, .csv) or Topo (.geojson, .gpx, .kml)…' });
        const topoDropZone = el('div', {
          className: 'hb-dropzone',
          on: { click: () => topoFileInput.click() },
        }, [topoFileLabel, el('span', { className: 'hb-mono-tag', text: 'Browse' })]);
        topoFileInput.addEventListener('change', () => {
          const f = topoFileInput.files && topoFileInput.files[0];
          topoFileLabel.textContent = f ? f.name : 'Upload LiDAR/DEM (.asc, .csv) or Topo (.geojson, .gpx, .kml)…';
        });
        const topoList = el('div', { className: 'hb-list' });
        const topoSection = el('div', { className: 'hb-section' }, [
          el('div', { className: 'hb-section-head' }, [
            el('span', { className: 'hb-eyebrow', text: 'Topo, Elevation & Landmarks' }),
            el('div', { className: 'hb-row nowrap' }, [contourStepSel, fetchDemBtn]),
          ]),
          topoStatsBox,
          topoLegendBox,
          el('div', { className: 'hb-row nowrap' }, [topoKindSel, dropTopoBtn]),
          topoFileInput,
          topoDropZone,
          el('button', { className: 'hb-btn sm', text: 'Import Topo / LiDAR File', on: { click: () => doImportTopoFile() } }),
          topoList,
        ]);

        // Traced Streams, Walking Trails, ATV/Driving Roads & Deer Trails
        const routeKindSel = el('select', { className: 'hb-select' }, [
          el('option', { value: 'stream', text: 'Stream / Creek (Downstream)' }),
          el('option', { value: 'walking_trail', text: 'Walking / Foot Trail' }),
          el('option', { value: 'entry_route', text: 'Stealth Stand Entry' }),
          el('option', { value: 'atv_road', text: 'ATV / Two-Track Road' }),
          el('option', { value: 'driving_road', text: 'Driving / Logging Road' }),
          el('option', { value: 'deer_trail', text: 'Deer Trail / Runway' }),
        ]);
        const traceRouteBtn = el('button', { className: 'hb-btn primary sm', text: '+ Trace Path' });
        const syncHydroBtn = el('button', { className: 'hb-btn sm', text: 'Sync Streams & Roads', on: { click: () => doSyncHydroAndRoads(false) } });
        const routeList = el('div', { className: 'hb-list' });
        const routesSection = el('div', { className: 'hb-section' }, [
          el('div', { className: 'hb-section-head' }, [
            el('span', { className: 'hb-eyebrow', text: 'Streams, Trails & Roads' }),
            syncHydroBtn,
          ]),
          el('div', { className: 'hb-row nowrap' }, [routeKindSel, traceRouteBtn]),
          routeList,
        ]);

        // ── FISHING MODE SECTIONS (Hidden in Hunt Mode) ──────────────────
        const syncWaterbodiesBtn = el('button', { className: 'hb-btn primary sm', text: 'Sync Lakes', on: { click: () => doSyncWaterbodies(false) } });
        const drawWaterbodyBtn = el('button', { className: 'hb-btn sm', text: '+ Draw Pond/Lake' });
        const waterbodiesList = el('div', { className: 'hb-list' });
        const wbEditorBox = el('div', { className: 'hb-wb-card', attrs: { style: 'display:none' } });
        const waterbodiesSection = el('div', { className: 'hb-section', attrs: { style: 'display:none' } }, [
          el('div', { className: 'hb-section-head' }, [
            el('span', { className: 'hb-eyebrow', text: 'Lakes, Reservoirs & Ponds' }),
            el('div', { className: 'hb-row nowrap' }, [drawWaterbodyBtn, syncWaterbodiesBtn]),
          ]),
          waterbodiesList,
          wbEditorBox,
        ]);

        const refreshFishBtn = el('button', { className: 'hb-btn primary sm', text: 'Analyze Bite & Gauges', on: { click: () => doRefreshFishAnalysis() } });
        const biteCard = el('div', { className: 'hb-move-card', attrs: { style: 'display:none' } });
        const windDriftBox = el('div', { className: 'hb-topo-stats', text: 'Click "Analyze Bite & Gauges" for Solunar Bite Index, Windward Baitfish Shore, and live USGS / Global river discharge.' });
        const gaugesBox = el('div', { className: 'hb-list' });
        const fishIntelSection = el('div', { className: 'hb-section', attrs: { style: 'display:none' } }, [
          el('div', { className: 'hb-section-head' }, [
            el('span', { className: 'hb-eyebrow', text: 'Bite Index & Live River Gauges' }),
            refreshFishBtn,
          ]),
          biteCard,
          windDriftBox,
          gaugesBox,
        ]);

        const fishStructKindSel = el('select', { className: 'hb-select' }, [
          el('option', { value: 'drop_off', text: 'Drop-Off / Breakline' }),
          el('option', { value: 'deep_hole', text: 'Deep Hole / Basin' }),
          el('option', { value: 'weed_edge', text: 'Weed Edge / Flat' }),
          el('option', { value: 'rock_pile', text: 'Rock Pile / Reef' }),
          el('option', { value: 'submerged_timber', text: 'Submerged Timber / Brush' }),
          el('option', { value: 'point_bar', text: 'Point / Sandbar' }),
          el('option', { value: 'eddy_seam', text: 'Eddy / Current Seam' }),
          el('option', { value: 'riffle_pool', text: 'Riffle-to-Pool Run' }),
          el('option', { value: 'spring_seep', text: 'Cold Spring / Inflow' }),
          el('option', { value: 'boat_launch', text: 'Boat Launch / Ramp' }),
          el('option', { value: 'shore_access', text: 'Shore / Wade Access' }),
        ]);
        const dropFishStructBtn = el('button', { className: 'hb-btn sm', text: '+ Pin Structure' });
        const dropFlowBtn = el('button', { className: 'hb-btn sm', text: '+ Current Arrow' });
        const fishStructList = el('div', { className: 'hb-list' });
        const fishStructSection = el('div', { className: 'hb-section', attrs: { style: 'display:none' } }, [
          el('div', { className: 'hb-section-head' }, [
            el('span', { className: 'hb-eyebrow', text: 'Fish Structure & Current Flows' }),
            dropFlowBtn,
          ]),
          el('div', { className: 'hb-row nowrap' }, [fishStructKindSel, dropFishStructBtn]),
          fishStructList,
        ]);

        const bathyWbSel = el('select', { className: 'hb-select', attrs: { title: 'Target lake/pond for depth soundings & shoreline-clipped contours' } }, [
          el('option', { value: '', text: 'Target: Active Camp Water' }),
        ]);
        const dropDepthBtn = el('button', { className: 'hb-btn sm', text: '+ Drop Depth (ft)' });
        const traceContourBtn = el('button', { className: 'hb-btn sm', text: '+ Trace Contour' });
        const estimateBathyBtn = el('button', { className: 'hb-btn sm', text: '~ Estimate Depth', attrs: { title: 'Instant shoreline-distance depth estimate using lake max depth' }, on: { click: () => doEstimateBathymetry() } });
        const buildBathyBtn = el('button', { className: 'hb-btn primary sm', text: 'Build Contours', on: { click: () => doRebuildBathymetry() } });
        const clearBathyBtn = el('button', { className: 'hb-btn subtle-danger sm', text: 'Clear', attrs: { title: 'Remove saved/estimated depth contours & soundings for this waterbody' }, on: { click: () => doClearBathymetry() } });
        const bathyStatsBox = el('div', { className: 'hb-topo-stats', text: 'Sync/draw a lake shoreline (0 ft), pin its DNR chart overlay, estimate depth, drop soundings, trace contours, or upload Sonar CSV/GPX.' });
        const bathyLegendBox = el('div', { className: 'hb-elev-legend', attrs: { style: 'display:none' } });
        const bathyFileInput = el('input', { type: 'file', className: 'hb-file-hidden', attrs: { accept: '.csv,.gpx,.geojson,.json' } });
        const bathyFileLabel = el('span', { text: 'Upload Sonar CSV, GPX (<depth>), or Depth GeoJSON…' });
        const bathyDropZone = el('div', {
          className: 'hb-dropzone',
          on: { click: () => bathyFileInput.click() },
        }, [bathyFileLabel, el('span', { className: 'hb-mono-tag', text: 'Browse' })]);
        bathyFileInput.addEventListener('change', () => {
          const f = bathyFileInput.files && bathyFileInput.files[0];
          bathyFileLabel.textContent = f ? f.name : 'Upload Sonar CSV, GPX (<depth>), or Depth GeoJSON…';
        });
        const bathySection = el('div', { className: 'hb-section', attrs: { style: 'display:none' } }, [
          el('div', { className: 'hb-section-head' }, [
            el('span', { className: 'hb-eyebrow', text: 'Bathymetry & Depth Contours' }),
            el('div', { className: 'hb-row nowrap' }, [buildBathyBtn, clearBathyBtn]),
          ]),
          bathyWbSel,
          bathyStatsBox,
          bathyLegendBox,
          el('div', { className: 'hb-row' }, [dropDepthBtn, traceContourBtn, estimateBathyBtn]),
          bathyFileInput,
          bathyDropZone,
          el('button', { className: 'hb-btn sm', text: 'Import Sonar / GPX File', on: { click: () => doImportDepthFile() } }),
        ]);

        const paneCamp = el('div', { className: 'hb-tab-pane active' }, [
          campSection,
          windSection,
          standsSection,
          waterbodiesSection,
          fishIntelSection,
          routesSection,
          fishStructSection,
          bathySection,
          topoSection,
        ]);

        // ── TAB 2: Saved Plans & Removable Map Objects ───────────────────
        const annKindSel = el('select', { className: 'hb-select' }, [
          el('option', { value: 'stand_pick', text: 'Stand Pick' }),
          el('option', { value: 'pinch_point', text: 'Pinch Point' }),
          el('option', { value: 'bedding_zone', text: 'Bedding Zone' }),
          el('option', { value: 'food_source', text: 'Food Source' }),
          el('option', { value: 'avoid_zone', text: 'Avoid / Blown' }),
          el('option', { value: 'callout', text: 'Tactical Callout' }),
        ]);
        const dropAnnBtn = el('button', { className: 'hb-btn sm', text: '+ Drop Object' });
        const clearAnnBtn = el('button', { className: 'hb-btn sm subtle-danger', text: 'Clear All' });
        const savedPlansList = el('div', { className: 'hb-list' });
        const annList = el('div', { className: 'hb-list' });

        const panePlans = el('div', { className: 'hb-tab-pane' }, [
          el('div', { className: 'hb-section' }, [
            el('div', { className: 'hb-section-head' }, [
              el('span', { className: 'hb-eyebrow', text: 'Saved Hunting Plans' }),
            ]),
            savedPlansList,
          ]),
          el('div', { className: 'hb-section' }, [
            el('div', { className: 'hb-section-head' }, [
              el('span', { className: 'hb-eyebrow', text: 'Removable Map Objects' }),
              clearAnnBtn,
            ]),
            el('div', { className: 'hb-row nowrap' }, [annKindSel, dropAnnBtn]),
            annList,
          ]),
        ]);

        // ── TAB 3: Mobile GPS & Compass Bridge ───────────────────────────
        const hunterNameIn = el('input', { className: 'hb-input', placeholder: 'Hunter name (e.g. Dave)' });
        const beaconBtn = el('button', { className: 'hb-btn primary sm', text: 'Start Beacon' });
        const tokenBtn = el('button', { className: 'hb-btn sm', text: 'Issue Android Token' });
        const tokenOut = el('div', { className: 'hb-token-box', text: 'Android / PWA endpoint:\nPOST /api/modules/hunting-buddy/mobile/beacon' });
        const hunterList = el('div', { className: 'hb-list' });

        const paneGps = el('div', { className: 'hb-tab-pane' }, [
          el('div', { className: 'hb-section' }, [
            el('div', { className: 'hb-section-head' }, [
              el('span', { className: 'hb-eyebrow', text: 'Mobile Field Beacon' }),
            ]),
            el('div', { className: 'hb-row nowrap' }, [hunterNameIn, beaconBtn]),
            el('div', { className: 'hb-row' }, [tokenBtn]),
            tokenOut,
          ]),
          el('div', { className: 'hb-section' }, [
            el('div', { className: 'hb-section-head' }, [
              el('span', { className: 'hb-eyebrow', text: 'Active Hunters' }),
            ]),
            hunterList,
          ]),
        ]);

        sidebarBody.appendChild(paneCamp);
        sidebarBody.appendChild(panePlans);
        sidebarBody.appendChild(paneGps);

        function setSidebarTab(tabId) {
          state.sidebarTab = tabId;
          tabBtnCamp.classList.toggle('active', tabId === 'camp');
          tabBtnPlans.classList.toggle('active', tabId === 'plans');
          tabBtnGps.classList.toggle('active', tabId === 'gps');
          paneCamp.classList.toggle('active', tabId === 'camp');
          panePlans.classList.toggle('active', tabId === 'plans');
          paneGps.classList.toggle('active', tabId === 'gps');
          sidebarBody.scrollTop = 0;
        }
        tabBtnCamp.addEventListener('click', () => setSidebarTab('camp'));
        tabBtnPlans.addEventListener('click', () => setSidebarTab('plans'));
        tabBtnGps.addEventListener('click', () => setSidebarTab('gps'));

        // ── Quiet Map Header Bar (Segmented Basemap + Layers Popover) ────
        const bmBtns = {};
        const makeBmBtn = (id, label) => {
          const b = el('button', {
            className: 'hb-seg-btn' + (state.basemap === id ? ' active' : ''),
            text: label,
            on: {
              click: () => {
                state.basemap = id;
                Object.entries(bmBtns).forEach(([k, btn]) => btn.classList.toggle('active', k === id));
                updateBasemap();
              },
            },
          });
          bmBtns[id] = b;
          return b;
        };

        const basemapRail = el('div', { className: 'hb-seg-rail' }, [
          makeBmBtn('hybrid', 'Hybrid'),
          makeBmBtn('usgs_aerial', 'Aerial'),
          makeBmBtn('usgs_topo', 'Topo'),
        ]);

        // Clean Layers Popover with Quick Declutter Filters
        const layerDefs = [
          { key: 'parcels', label: 'Parcels & Owners', color: '#fbbf24' },
          { key: 'publicLands', label: 'Public Hunting Land', color: '#2dd4bf' },
          { key: 'topoContours', label: 'LiDAR Contours & Thermals', color: '#f59e0b' },
          { key: 'topoLandmarks', label: 'Water & Topo Landmarks', color: '#06b6d4' },
          { key: 'streams', label: 'Streams & Flow Arrows', color: '#22d3ee' },
          { key: 'roads', label: 'Trails & Access Roads', color: '#84cc16' },
          { key: 'waterbodies', label: 'Lakes & Ponds (Dossiers)', color: '#0284c7' },
          { key: 'chartOverlays', label: 'Georeferenced DNR Charts', color: '#38bdf8' },
          { key: 'fisheries', label: 'Fisheries & Trout Waters', color: '#38bdf8' },
          { key: 'bathymetry', label: 'Underwater Depth Contours', color: '#0ea5e9' },
          { key: 'fishStructures', label: 'Fish Structures & Launch', color: '#06b6d4' },
          { key: 'waterFlows', label: 'Water Current Arrows', color: '#67e8f9' },
          { key: 'waypoints', label: 'Stands & Trail Cams', color: '#38bdf8' },
          { key: 'scent', label: 'Scent Plumes', color: '#f97316' },
          { key: 'annotations', label: 'Plan Map Objects', color: '#a78bfa' },
          { key: 'hunters', label: 'Live GPS Beacons', color: '#60a5fa' },
        ];
        const layersBtn = el('button', { className: 'hb-btn', text: 'Layers ▾' });
        const layersPopover = el('div', { className: 'hb-layers-popover', attrs: { style: 'display:none' } });

        function renderLayersPopover() {
          const onCount = Object.values(state.layers).filter(Boolean).length;
          layersBtn.textContent = `Layers (${onCount}) ▾`;
          layersPopover.replaceChildren();
          layersPopover.appendChild(el('div', { className: 'hb-layers-quick' }, [
            el('button', {
              className: 'hb-btn ghost sm',
              text: 'All On',
              on: {
                click: (ev) => {
                  ev.stopPropagation();
                  layerDefs.forEach((ld) => { state.layers[ld.key] = true; });
                  renderLayersPopover();
                  syncLayerVisibility();
                },
              },
            }),
            el('button', {
              className: 'hb-btn ghost sm',
              text: 'Clean View',
              attrs: { title: 'Declutter map for current Hunt or Fish mode' },
              on: {
                click: (ev) => {
                  ev.stopPropagation();
                  const keep = state.appMode === 'fish'
                    ? new Set(['waterbodies', 'chartOverlays', 'bathymetry', 'fishStructures', 'waterFlows', 'streams'])
                    : new Set(['waypoints', 'scent', 'streams', 'roads', 'topoContours']);
                  layerDefs.forEach((ld) => { state.layers[ld.key] = keep.has(ld.key); });
                  renderLayersPopover();
                  syncLayerVisibility();
                },
              },
            }),
          ]));
          layerDefs.forEach((ld) => {
            const cb = el('input', { type: 'checkbox' });
            cb.checked = !!state.layers[ld.key];
            const row = el('label', { className: 'hb-layer-row' }, [
              el('span', { className: 'hb-layer-left' }, [
                el('span', { className: 'hb-swatch', attrs: { style: `background:${ld.color}` } }),
                el('span', { text: ld.label }),
              ]),
              cb,
            ]);
            cb.addEventListener('change', () => {
              state.layers[ld.key] = cb.checked;
              const cnt = Object.values(state.layers).filter(Boolean).length;
              layersBtn.textContent = `Layers (${cnt}) ▾`;
              syncLayerVisibility();
            });
            layersPopover.appendChild(row);
          });
        }
        renderLayersPopover();

        layersBtn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          state.layersOpen = !state.layersOpen;
          layersPopover.style.display = state.layersOpen ? 'flex' : 'none';
          layersBtn.classList.toggle('active', state.layersOpen);
        });
        document.addEventListener('click', (ev) => {
          if (state.layersOpen && !layersPopover.contains(ev.target) && ev.target !== layersBtn) {
            state.layersOpen = false;
            layersPopover.style.display = 'none';
            layersBtn.classList.remove('active');
          }
        });

        const fetchViewportBtn = el('button', {
          className: 'hb-btn',
          text: 'Sync GIS',
          attrs: { title: 'Load 30-day cached WI parcels & public hunting land for map view' },
          on: { click: () => loadViewportGis(false) },
        });

        const georgeTaskHost = el('div', { className: 'hb-task-host' });

        const fishScoutBtn = el('button', {
          className: 'hb-btn ghost',
          text: 'Trip & Rigging Scout',
          attrs: { style: 'display:none', title: 'Zero-setup scout: nearby trout/gamefish waters, live CFS gauges & species rigging guide' },
          on: { click: () => launchBriefingWorkflow('fish_trip_scout') },
        });
        function getAgentName() {
          return state.agentName
            || (ctx && typeof ctx.getAgentName === 'function' && ctx.getAgentName())
            || (ctx && ctx.agentName)
            || (window.LodgeModules && typeof window.LodgeModules.getAgentName === 'function' && window.LodgeModules.getAgentName())
            || 'Agent';
        }

        function updateDynamicAgentLabels() {
          const ag = getAgentName();
          primaryBriefingBtn.textContent = state.appMode === 'fish'
            ? `Ask ${ag}: Fish & Flow Plan`
            : `Ask ${ag}: Hunt Briefing`;
        }

        const primaryBriefingBtn = el('button', {
          className: 'hb-btn primary',
          text: `Ask ${getAgentName()}: Hunt Briefing`,
          on: {
            click: () => launchBriefingWorkflow(state.appMode === 'fish' ? 'fish_structure_briefing' : 'camp_tactical_briefing'),
          },
        });

        async function launchBriefingWorkflow(wfId) {
          const prop = state.active && state.active.property;
          const wxStale = !state.weather || (Date.now() - (state.weatherFetchedAt || 0)) > (15 * 60 * 1000);
          if (prop && !state.manualWindOverride && wxStale) {
            setStatus('Syncing fresh live weather for camp before launching briefing…');
            await fetchWeather();
          }
          const lbl = wfId === 'fish_trip_scout' ? 'Fishing Trip & Rigging Scout'
            : (wfId === 'fish_structure_briefing' ? 'Flow & Fish Structure Plan' : 'Hunt Tactical Briefing');
          setStatus(`Launching monitored ${lbl} with live map visual…`);
          let viewBbox = null;
          if (map) {
            try {
              const mb = map.getBounds();
              viewBbox = [
                Number(mb.getWest().toFixed(6)),
                Number(mb.getSouth().toFixed(6)),
                Number(mb.getEast().toFixed(6)),
                Number(mb.getNorth().toFixed(6)),
              ];
            } catch (_) {}
          }
          const prevMaxPlanId = (state.plans || []).reduce((mx, p) => Math.max(mx, Number(p.id || 0)), 0);
          try {
            await ctx.launchTask(wfId, {
              params: {
                property_id: prop ? prop.id : null,
                camp: prop ? prop.name : (nameInput.value.trim() || 'Active Camp'),
                wind_deg: state.windDeg,
                wind_mph: state.windMph,
                thermal_mode: state.thermalMode,
                ...(viewBbox ? { view_bbox: viewBbox } : {}),
              },
            }, {
              streamContainer: georgeTaskHost,
              onResult: async (rd) => {
                renderBriefingFromTask(rd, wfId);
                await loadState(state.active ? state.active.property.id : null);
              },
              onComplete: async (rd) => {
                setStatus(`${lbl} complete (${rd && rd.phase ? rd.phase : 'done'}).`);
                await reconcileCompletedTaskPlan(rd, wfId, prevMaxPlanId);
                refreshTaskHistory();
              },
              onError: (err) => setStatus(`Briefing error: ${err}`),
            });
          } catch (e) {
            setStatus(`Failed to launch briefing: ${e.message}`);
          }
        }

        const mapBar = el('div', { className: 'hb-map-bar' }, [
          el('div', { className: 'hb-row' }, [
            basemapRail,
            el('div', { className: 'hb-layers-wrap' }, [layersBtn, layersPopover]),
            fetchViewportBtn,
          ]),
          el('div', { className: 'hb-row' }, [
            el('button', { className: 'hb-btn ghost', text: 'Export GPX', on: { click: () => doExport('gpx') } }),
            el('button', { className: 'hb-btn ghost', text: 'Post to Discord', on: { click: () => doPostDiscordMap() } }),
            fishScoutBtn,
            primaryBriefingBtn,
          ]),
        ]);

        const mapDiv = el('div', { className: 'hb-map-canvas' });
        const mapHud = el('div', { className: 'hb-map-hud' });
        const mapStage = el('div', { className: 'hb-map-stage' }, [mapDiv, mapHud]);
        const drawBarText = el('span', { text: 'Draw Camp Bounds: Click corners on the map to outline your hunting area.' });
        const drawUndoBtn = el('button', { className: 'hb-btn sm', text: 'Undo Point' });
        const drawFinishBtn = el('button', { className: 'hb-btn primary sm', text: '✓ Use Bounds' });
        const drawCancelBtn = el('button', { className: 'hb-btn sm subtle-danger', text: 'Cancel' });
        const drawBar = el('div', { className: 'hb-draw-bar', attrs: { style: 'display:none' } }, [
          drawBarText,
          el('div', { className: 'hb-row nowrap' }, [drawUndoBtn, drawFinishBtn, drawCancelBtn]),
        ]);
        const traceBarText = el('span', { text: 'Trace Path/Stream: Click points along the stream or trail.' });
        const traceUndoBtn = el('button', { className: 'hb-btn sm', text: 'Undo Point' });
        const traceFlipBtn = el('button', { className: 'hb-btn sm', text: '⇄ Flip Dir' });
        const traceSaveBtn = el('button', { className: 'hb-btn primary sm', text: '✓ Save Trace' });
        const traceCancelBtn = el('button', { className: 'hb-btn sm subtle-danger', text: 'Cancel' });
        const traceBar = el('div', { className: 'hb-draw-bar', attrs: { style: 'display:none' } }, [
          traceBarText,
          el('div', { className: 'hb-row nowrap' }, [traceUndoBtn, traceFlipBtn, traceSaveBtn, traceCancelBtn]),
        ]);
        const alignBarText = el('span', { text: 'Pin Chart Overlay: Rotate, mirror, knock out white paper, or use 2-Point Shoreline Snap.' });
        const alignRotLeftBtn = el('button', { className: 'hb-btn sm', text: '↺ -90°', attrs: { title: 'Rotate 90° counterclockwise' } });
        const alignRotRightBtn = el('button', { className: 'hb-btn sm', text: '↻ +90°', attrs: { title: 'Rotate 90° clockwise (common for WI DNR landscape charts)' } });
        const alignRotIn = el('input', { type: 'range', className: 'hb-range', attrs: { min: '-180', max: '180', step: '1', style: 'min-width:95px;max-width:130px', title: 'Fine rotation (-180° to +180°)' }, value: '0' });
        const alignRotLbl = el('span', { className: 'hb-mono-tag', text: '0°', attrs: { style: 'min-width:34px;text-align:right' } });
        const alignFlipHBtn = el('button', { className: 'hb-btn sm', text: '⇄ Flip H', attrs: { title: 'Mirror horizontally' } });
        const alignFlipVBtn = el('button', { className: 'hb-btn sm', text: '⇅ Flip V', attrs: { title: 'Mirror vertically' } });
        const alignOpacityIn = el('input', { type: 'range', className: 'hb-range', attrs: { min: '5', max: '100', step: '5', style: 'min-width:75px;max-width:105px', title: 'Chart alpha / opacity' }, value: '80' });
        const alignOpacityLbl = el('span', { className: 'hb-mono-tag', text: '80%', attrs: { style: 'min-width:32px;text-align:right' } });
        const alignBlendSel = el('select', { className: 'hb-select', attrs: { style: 'max-width:165px;padding:4px 7px;font-size:11px', title: 'Knock out white paper background so contour lines float transparently over satellite' } }, [
          el('option', { value: 'cyan_transparent', text: 'Cyan Lines (Clear BG)' }),
          el('option', { value: 'gold_transparent', text: 'Gold Lines (Clear BG)' }),
          el('option', { value: 'ink_transparent', text: 'Dark Ink (Clear BG)' }),
          el('option', { value: 'paper', text: 'Original Paper' }),
        ]);
        const alignCropBtn = el('button', { className: 'hb-btn sm', text: '✂ Crop Chart', attrs: { title: 'Crop out legends, graphs, borders & neighboring lakes before aligning or running Vision' } });
        const alignSnapBtn = el('button', { className: 'hb-btn sm', text: '🎯 2-Point Snap', attrs: { title: 'Click 2 shoreline landmarks on the chart & on the satellite map to solve exact rotation, scale & GPS lock' } });
        const alignAutoBtn = el('button', { className: 'hb-btn sm', text: '✨ Auto-Match', attrs: { title: 'Auto-match chart orientation & bounds against the real GIS lake shoreline' } });
        const alignResetBtn = el('button', { className: 'hb-btn ghost sm', text: 'Reset' });
        const alignUnpinBtn = el('button', { className: 'hb-btn warn-outline sm', text: 'Unpin Chart', attrs: { title: 'Remove this chart overlay from the map completely' } });
        const alignSaveBtn = el('button', { className: 'hb-btn primary sm', text: '✓ Save Alignment' });
        const alignExtractBtn = el('button', { className: 'hb-btn primary sm', text: '⚡ Save & Extract Contours' });
        const alignCancelBtn = el('button', { className: 'hb-btn sm subtle-danger', text: 'Cancel' });
        const alignBar = el('div', { className: 'hb-align-bar', attrs: { style: 'display:none' } }, [
          el('div', { className: 'hb-row', attrs: { style: 'justify-content:space-between' } }, [
            alignBarText,
            el('div', { className: 'hb-row nowrap' }, [alignCropBtn, alignSnapBtn, alignAutoBtn, alignResetBtn, alignUnpinBtn, alignCancelBtn]),
          ]),
          el('div', { className: 'hb-row', attrs: { style: 'justify-content:space-between' } }, [
            el('div', { className: 'hb-row nowrap' }, [
              alignRotLeftBtn,
              alignRotRightBtn,
              alignRotIn,
              alignRotLbl,
              alignFlipHBtn,
              alignFlipVBtn,
              el('span', { className: 'hb-wx-sub', text: 'Alpha' }),
              alignOpacityIn,
              alignOpacityLbl,
              alignBlendSel,
            ]),
            el('div', { className: 'hb-row nowrap' }, [alignSaveBtn, alignExtractBtn]),
          ]),
        ]);
        const cropPanelText = el('span', { text: 'Drag a box over the target lake to crop out legends, volume graphs, and neighboring lakes.' });
        const cropStageWrap = el('div', { className: 'hb-crop-stage' });
        const cropApplyBtn = el('button', { className: 'hb-btn primary sm', text: '✓ Apply Crop' });
        const cropResetBtn = el('button', { className: 'hb-btn ghost sm', text: 'Reset Full Sheet' });
        const cropCloseBtn = el('button', { className: 'hb-btn ghost sm', text: 'Close' });
        const cropPanel = el('div', { className: 'hb-crop-panel', attrs: { style: 'display:none' } }, [
          el('div', { className: 'hb-row', attrs: { style: 'justify-content:space-between' } }, [
            cropPanelText,
            el('div', { className: 'hb-row nowrap' }, [cropApplyBtn, cropResetBtn, cropCloseBtn]),
          ]),
          cropStageWrap,
        ]);
        const snapPanelText = el('span', { text: 'Step 1/4: Click Landmark A (e.g. Dam or prominent point) on the DNR chart below.' });
        const snapPanelImgWrap = el('div', { className: 'hb-snap-img-wrap' });
        const snapPanelCancelBtn = el('button', { className: 'hb-btn ghost sm', text: 'Close 2-Point Snap' });
        const snapPanel = el('div', { className: 'hb-snap-panel', attrs: { style: 'display:none' } }, [
          el('div', { className: 'hb-row', attrs: { style: 'justify-content:space-between' } }, [snapPanelText, snapPanelCancelBtn]),
          snapPanelImgWrap,
        ]);
        const scrollDownBtn = el('button', {
          className: 'hb-btn ghost sm',
          text: '↓ Report / Task',
          attrs: { title: 'Scroll to active briefing or agent task below the fixed-size map' },
          on: {
            click: () => {
              const target = planDrawer.style.display !== 'none' ? planDrawer : georgeTaskHost;
              if (target && target.scrollIntoView) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            },
          },
        });
        const statusStrip = el('div', { className: 'hb-status-strip' }, [statusLine, scrollDownBtn]);
        const historyBar = el('div', { className: 'hb-history-bar', attrs: { style: 'display:none' } });
        const planDrawer = el('div', { className: 'hb-plan-drawer', attrs: { style: 'display:none' } });

        mapWrap.appendChild(mapBar);
        mapWrap.appendChild(drawBar);
        mapWrap.appendChild(traceBar);
        mapWrap.appendChild(alignBar);
        mapWrap.appendChild(cropPanel);
        mapWrap.appendChild(snapPanel);
        mapWrap.appendChild(mapStage);
        mapWrap.appendChild(statusStrip);
        mapWrap.appendChild(historyBar);
        mapWrap.appendChild(planDrawer);
        mapWrap.appendChild(georgeTaskHost);

        function degToCompass(deg) {
          const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
          return dirs[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
        }

        function setStatus(msg) {
          statusLine.textContent = msg;
        }

        function syncLayerVisibility() {
          if (!map) return;
          const toggle = (lyr, show) => {
            if (!lyr) return;
            if (show && !map.hasLayer(lyr)) map.addLayer(lyr);
            if (!show && map.hasLayer(lyr)) map.removeLayer(lyr);
          };
          toggle(publicLandLayer, state.layers.publicLands);
          toggle(parcelLayer, state.layers.parcels);
          toggle(topoContourLayer, state.layers.topoContours);
          toggle(topoLandmarkLayer, state.layers.topoLandmarks);
          toggle(streamLayer, state.layers.streams);
          toggle(roadLayer, state.layers.roads);
          toggle(waterbodyLayer, state.layers.waterbodies);
          toggle(chartOverlayLayer, state.layers.chartOverlays);
          toggle(fisheryLayer, state.layers.fisheries);
          toggle(bathyLayer, state.layers.bathymetry);
          toggle(fishStructureLayer, state.layers.fishStructures);
          toggle(waterFlowLayer, state.layers.waterFlows);
          toggle(scentLayer, state.layers.scent);
          toggle(waypointLayer, state.layers.waypoints);
          toggle(planAnnotationsLayer, state.layers.annotations);
          toggle(hunterLayer, state.layers.hunters);
        }

        function syncMapInteractionMode() {
          const active = !!(state.dropMode || state.annotateDropMode || state.drawBoundsMode || state.topoDropMode || state.traceMode || state.traceContourMode || state.fishStructDropMode || state.flowDropMode || state.depthDropMode || (state.twoPointSnapActive && (state.snapStep === 1 || state.snapStep === 3)));
          mapDiv.classList.toggle('hb-placement-mode', active);
          if (active && map) {
            try { map.closePopup(); } catch (_) {}
          }
        }

        function clearAllPlacementModes() {
          state.dropMode = false;
          state.annotateDropMode = false;
          state.topoDropMode = false;
          state.fishStructDropMode = false;
          state.flowDropMode = false;
          state.depthDropMode = false;
          if (state.drawBoundsMode) stopDrawBoundsMode(false);
          if (state.traceMode || state.traceContourMode) stopTraceMode();
          [dropBtn, dropAnnBtn, dropTopoBtn, dropFishStructBtn, dropFlowBtn, dropDepthBtn, drawWaterbodyBtn, traceContourBtn].forEach((b) => b && b.classList.remove('active'));
          syncMapInteractionMode();
        }

        function setAppMode(mode) {
          state.appMode = mode === 'fish' ? 'fish' : 'hunt';
          const isFish = state.appMode === 'fish';
          modeBtnHunt.classList.toggle('active', !isFish);
          modeBtnFish.classList.toggle('active', isFish);

          // Toggle Hunt-only vs Fish-only sidebar cards seamlessly (Camps, Weather & Streams/Trails stay shared!)
          standsSection.style.display = isFish ? 'none' : '';
          topoSection.style.display = isFish ? 'none' : '';
          thermalSel.style.display = isFish ? 'none' : '';
          thermalCard.style.display = isFish ? 'none' : '';
          conflictBox.style.display = isFish ? 'none' : '';
          waterbodiesSection.style.display = isFish ? '' : 'none';
          fishIntelSection.style.display = isFish ? '' : 'none';
          fishStructSection.style.display = isFish ? '' : 'none';
          bathySection.style.display = isFish ? '' : 'none';

          // Update primary map action buttons
          updateDynamicAgentLabels();
          fishScoutBtn.style.display = isFish ? '' : 'none';

          // Auto-declutter map layers to match the selected mode
          state.layers.scent = !isFish;
          state.layers.waterbodies = true;
          state.layers.fisheries = isFish;
          state.layers.bathymetry = isFish;
          state.layers.fishStructures = isFish;
          state.layers.waterFlows = isFish;
          renderLayersPopover();
          syncLayerVisibility();

          if (isFish && state.active && !state.fishAnalysis) {
            doRefreshFishAnalysis();
          }
        }

        function computePolygonAcres(pts) {
          if (!pts || pts.length < 3) return 0;
          const R = 6378137;
          const lat0 = pts.reduce((a, p) => a + p[0], 0) / pts.length;
          const cos0 = Math.cos((lat0 * Math.PI) / 180);
          let area = 0;
          for (let i = 0; i < pts.length; i++) {
            const p1 = pts[i];
            const p2 = pts[(i + 1) % pts.length];
            const x1 = (p1[1] * Math.PI / 180) * R * cos0;
            const y1 = (p1[0] * Math.PI / 180) * R;
            const x2 = (p2[1] * Math.PI / 180) * R * cos0;
            const y2 = (p2[0] * Math.PI / 180) * R;
            area += (x1 * y2 - x2 * y1);
          }
          return Math.abs(area) * 0.5 / 4046.8564224;
        }

        function renderDrawPreview() {
          if (!drawLayer) return;
          drawLayer.clearLayers();
          const pts = state.drawPoints || [];
          pts.forEach((p, idx) => {
            window.L.circleMarker(p, {
              radius: idx === 0 ? 7 : 5,
              color: '#0c1017',
              weight: 2,
              fillColor: idx === 0 ? '#fbbf24' : '#38bdf8',
              fillOpacity: 1,
              interactive: false,
            }).addTo(drawLayer);
          });
          if (pts.length === 2) {
            window.L.polyline(pts, { color: '#38bdf8', weight: 2.5, dashArray: '6 4', interactive: false }).addTo(drawLayer);
          } else if (pts.length >= 3) {
            window.L.polygon(pts, {
              color: '#38bdf8',
              weight: 2.5,
              dashArray: '6 4',
              fillColor: '#38bdf8',
              fillOpacity: 0.18,
              interactive: false,
            }).addTo(drawLayer);
          }
          const acres = computePolygonAcres(pts);
          const isWb = state.drawTarget === 'waterbody';
          const lblPrefix = isWb ? 'Draw Pond / Lake Shoreline' : 'Draw Camp Bounds';
          drawFinishBtn.textContent = isWb ? '✓ Save Waterbody' : '✓ Use Bounds';
          if (pts.length === 0) {
            drawBarText.textContent = isWb
              ? 'Draw Pond / Lake Shoreline: Click points around the shoreline (e.g. Northwoods Pond).'
              : 'Draw Camp Bounds: Click corners on the map to outline your hunting area.';
          } else if (pts.length < 3) {
            drawBarText.textContent = `${lblPrefix}: ${pts.length} corner(s) placed — click ${3 - pts.length} more for a polygon.`;
          } else {
            drawBarText.textContent = `${lblPrefix}: ${pts.length} corners · ~${acres.toFixed(1)} acres — click more or "${drawFinishBtn.textContent}".`;
          }
        }

        function startDrawBoundsMode(targetKind) {
          clearAllPlacementModes();
          state.drawTarget = targetKind === 'waterbody' ? 'waterbody' : 'camp';
          state.drawBoundsMode = true;
          state.drawPoints = [];
          if (state.drawTarget === 'waterbody') drawWaterbodyBtn.classList.add('active');
          else drawBoundsBtn.classList.add('active');
          drawBar.style.display = 'flex';
          syncMapInteractionMode();
          renderDrawPreview();
          setStatus(state.drawTarget === 'waterbody'
            ? 'Draw Pond/Lake active — parcel popups paused. Click around the waterbody shoreline (sets 0 ft depth boundary).'
            : 'Draw Camp Bounds active — parcel popups paused. Click corners on the map to outline your camp.');
        }

        function drawFlowArrowheads(layerGrp, latLngPts, color) {
          if (!latLngPts || latLngPts.length < 2) return;
          const step = Math.max(1, Math.floor(latLngPts.length / 4));
          for (let i = 0; i < latLngPts.length - 1; i += step) {
            const p1 = latLngPts[i];
            const p2 = latLngPts[i + 1];
            const dy = p2[0] - p1[0];
            const dx = (p2[1] - p1[1]) * Math.max(0.2, Math.cos((p1[0] * Math.PI) / 180));
            if (Math.hypot(dx, dy) < 1e-5) continue;
            const brg = (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
            const mid = [(p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5];
            const wingM = 28;
            const cosL = Math.max(0.2, Math.cos((mid[0] * Math.PI) / 180));
            const lRad = ((brg + 150) * Math.PI) / 180;
            const rRad = ((brg - 150) * Math.PI) / 180;
            const lPt = [mid[0] + (wingM / 111320) * Math.cos(lRad), mid[1] + (wingM / (111320 * cosL)) * Math.sin(lRad)];
            const rPt = [mid[0] + (wingM / 111320) * Math.cos(rRad), mid[1] + (wingM / (111320 * cosL)) * Math.sin(rRad)];
            window.L.polyline([lPt, mid, rPt], { color, weight: 2.2, opacity: 0.92, interactive: false }).addTo(layerGrp);
          }
        }

        function renderTracePreview() {
          if (!tracePreviewLayer) return;
          tracePreviewLayer.clearLayers();
          const pts = state.tracePoints || [];
          const isContour = !!state.traceContourMode;
          const isStream = !isContour && ['stream', 'creek', 'current_seam', 'drift_line'].includes(state.traceKind);
          const col = isContour ? '#0ea5e9' : (isStream ? '#22d3ee' : (state.traceKind === 'deer_trail' ? '#fbbf24' : (state.traceKind === 'driving_road' ? '#e2e8f0' : '#84cc16')));
          pts.forEach((p, idx) => {
            window.L.circleMarker(p, {
              radius: idx === 0 ? 6 : 4.5,
              color: '#0c1017',
              weight: 2,
              fillColor: idx === 0 ? '#2dd4bf' : col,
              fillOpacity: 1,
              interactive: false,
            }).addTo(tracePreviewLayer);
          });
          if (pts.length >= 2) {
            window.L.polyline(pts, { color: col, weight: 3, dashArray: (isStream || isContour) ? null : '6 4', interactive: false }).addTo(tracePreviewLayer);
            if (!isContour && (isStream || state.traceKind === 'entry_route')) drawFlowArrowheads(tracePreviewLayer, pts, col);
          }
          if (isContour) {
            traceSaveBtn.textContent = '✓ Save Contour';
            traceBarText.textContent = pts.length < 2
              ? 'Trace Depth Contour: Click points along an underwater contour line or river channel.'
              : `Tracing Depth Contour: ${pts.length} points placed — click more or "✓ Save Contour".`;
          } else {
            traceSaveBtn.textContent = '✓ Save Trace';
            const pretty = state.traceKind.replace(/_/g, ' ');
            traceBarText.textContent = pts.length < 2
              ? `Trace ${pretty}: Click points along the path (${isStream ? 'click upstream → downstream' : 'at least 2 points'}).`
              : `Tracing ${pretty}: ${pts.length} points placed — click more or "✓ Save Trace".`;
          }
        }

        function startTraceMode(asContour) {
          clearAllPlacementModes();
          state.traceMode = !asContour;
          state.traceContourMode = !!asContour;
          state.tracePoints = [];
          if (asContour) traceContourBtn.classList.add('active');
          else traceRouteBtn.classList.add('active');
          traceBar.style.display = 'flex';
          syncMapInteractionMode();
          renderTracePreview();
          setStatus(asContour
            ? 'Tracing Underwater Depth Contour — click along a drop-off, hump, or channel, then click "✓ Save Contour".'
            : `Tracing ${state.traceKind.replace(/_/g, ' ')} — parcel popups paused. Click points along the map.`);
        }

        function stopTraceMode() {
          state.traceMode = false;
          state.traceContourMode = false;
          state.tracePoints = [];
          traceRouteBtn.classList.remove('active');
          traceContourBtn.classList.remove('active');
          traceBar.style.display = 'none';
          if (tracePreviewLayer) tracePreviewLayer.clearLayers();
          syncMapInteractionMode();
        }

        async function finishSaveTrace() {
          const pts = state.tracePoints || [];
          if (pts.length < 2) { setStatus('Click at least 2 points on the map first.'); return; }
          if (!state.active) { setStatus('Create or select a camp first before saving.'); return; }
          const coords = pts.map((p) => [p[1], p[0]]);

          if (state.traceContourMode) {
            const rawD = window.prompt('Depth for this traced contour line (feet, e.g. 10, 20, 30):', '15');
            if (rawD == null || rawD.trim() === '') return;
            const dVal = parseFloat(rawD);
            if (Number.isNaN(dVal) || dVal < 0) return;
            const wbName = bathyWbSel.value || (state.active ? `${state.active.property.name} Water` : 'Camp Water');
            const res = await ctx.action('save_contour_line', {
              property_id: state.active.property.id,
              coordinates: coords,
              depth_ft: dVal,
              waterbody_name: wbName,
            });
            stopTraceMode();
            if (res.status === 'ok') {
              setStatus(`✓ Saved ${dVal} ft contour line on ${wbName} (${res.contour_points_added || 0} pts · ${res.contour_count || 0} contours${res.shoreline_clipped ? ', shoreline-clipped' : ''}).`);
              await loadState(state.active.property.id);
            } else {
              setStatus(`Contour error: ${res.message || 'failed'}`);
            }
            return;
          }

          const pretty = state.traceKind.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
          const label = window.prompt(`Name for this ${pretty}:`, pretty);
          if (!label) return;
          const isStream = ['stream', 'creek', 'current_seam', 'drift_line'].includes(state.traceKind);
          const flowSpeed = isStream ? (window.prompt('Flow speed (calm, slow, moderate, fast):', 'moderate') || 'moderate') : '';
          const notes = window.prompt('Notes (optional, e.g. creek crossing, two-track access):', '') || '';
          const res = await ctx.action('save_route', {
            property_id: state.active.property.id,
            name: label,
            kind: state.traceKind,
            coordinates: coords,
            flow_speed: flowSpeed,
            notes,
          });
          stopTraceMode();
          if (res.status === 'ok') {
            setStatus(`✓ Saved ${pretty} "${label}" (${res.length_yd} yd, → ${res.bearing_compass}).`);
            await loadState(state.active.property.id);
          } else {
            setStatus(`Error saving trace: ${res.message || 'failed'}`);
          }
        }

        function stopDrawBoundsMode(keepPreview) {
          state.drawBoundsMode = false;
          drawBoundsBtn.classList.remove('active');
          drawWaterbodyBtn.classList.remove('active');
          drawBar.style.display = 'none';
          if (!keepPreview && drawLayer) drawLayer.clearLayers();
          syncMapInteractionMode();
        }

        async function finishDrawBounds() {
          const pts = state.drawPoints || [];
          if (state.drawTarget === 'waterbody') {
            if (pts.length < 3) { setStatus('Click at least 3 shoreline points around the pond or lake.'); return; }
            if (!state.active) { setStatus('Select or create a camp first before outlining a pond/lake.'); return; }
            const ring = pts.map((p) => [p[1], p[0]]);
            ring.push([pts[0][1], pts[0][0]]);
            const acres = Number(computePolygonAcres(pts).toFixed(2));
            const wbName = window.prompt('Name for this custom pond / lake (e.g. Northwoods Pond):', `${state.active.property.name} Pond`);
            if (!wbName) return;
            const res = await ctx.action('save_waterbody', {
              property_id: state.active.property.id,
              name: wbName,
              waterbody_type: acres < 15 ? 'pond' : 'lake',
              acres,
              geom: { type: 'Polygon', coordinates: [ring] },
            });
            stopDrawBoundsMode(false);
            if (res.status === 'ok') {
              state.selectedWaterbodyId = res.id;
              setStatus(`✓ Saved "${wbName}" (~${acres} ac). Fill in max depth, bottom & species in the dossier editor on the left.`);
              await loadState(state.active.property.id);
              const created = (state.waterbodies || []).find((w) => w.id === res.id);
              if (created) openWaterbodyEditor(created);
            } else {
              setStatus(`Error saving waterbody: ${res.message || 'failed'}`);
            }
            return;
          }

          if (!pts.length) {
            setStatus('Click at least 1 point (or 3+ corners for a boundary polygon) on the map first.');
            return;
          }
          const cLat = pts.reduce((a, p) => a + p[0], 0) / pts.length;
          const cLon = pts.reduce((a, p) => a + p[1], 0) / pts.length;
          latInput.value = cLat.toFixed(6);
          lonInput.value = cLon.toFixed(6);
          if (pts.length >= 3) {
            const ring = pts.map((p) => [p[1], p[0]]);
            ring.push([pts[0][1], pts[0][0]]);
            state.drawnBoundary = { type: 'Polygon', coordinates: [ring] };
            state.drawnAcres = Number(computePolygonAcres(pts).toFixed(2));
            drawnBoundsPill.textContent = `✓ Drawn bounds ready (${pts.length} pts · ~${state.drawnAcres} ac)`;
            drawnBoundsPill.style.display = 'block';
          } else {
            state.drawnBoundary = null;
            state.drawnAcres = 0;
            drawnBoundsPill.textContent = `✓ Camp center picked (${cLat.toFixed(5)}, ${cLon.toFixed(5)})`;
            drawnBoundsPill.style.display = 'block';
          }
          stopDrawBoundsMode(true);
          state.importOpen = true;
          importDrawer.style.display = 'flex';
          toggleImportBtn.classList.add('active');
          setSidebarTab('camp');
          nameInput.focus();
          setStatus('Bounds captured! Enter a Camp Name on the left and click "Save Camp & GIS".');
        }

        function initMap() {
          if (map || typeof window.L === 'undefined') {
            if (typeof window.L === 'undefined') setStatus('Leaflet library not loaded yet.');
            return;
          }
          map = window.L.map(mapDiv, { zoomControl: true }).setView([45.9200, -89.2500], 14);
          publicLandLayer = window.L.layerGroup().addTo(map);
          parcelLayer = window.L.layerGroup().addTo(map);
          topoContourLayer = window.L.layerGroup().addTo(map);
          topoLandmarkLayer = window.L.layerGroup().addTo(map);
          streamLayer = window.L.layerGroup().addTo(map);
          roadLayer = window.L.layerGroup().addTo(map);
          waterbodyLayer = window.L.layerGroup().addTo(map);
          chartOverlayLayer = window.L.layerGroup().addTo(map);
          alignOverlayLayer = window.L.layerGroup().addTo(map);
          fisheryLayer = window.L.layerGroup();
          bathyLayer = window.L.layerGroup().addTo(map);
          fishStructureLayer = window.L.layerGroup().addTo(map);
          waterFlowLayer = window.L.layerGroup().addTo(map);
          scentLayer = window.L.layerGroup().addTo(map);
          waypointLayer = window.L.layerGroup().addTo(map);
          planAnnotationsLayer = window.L.layerGroup().addTo(map);
          hunterLayer = window.L.layerGroup().addTo(map);
          drawLayer = window.L.layerGroup().addTo(map);
          tracePreviewLayer = window.L.layerGroup().addTo(map);
          updateBasemap();

          map.on('click', async (ev) => {
            if (state.twoPointSnapActive && (state.snapStep === 1 || state.snapStep === 3)) {
              handleTwoPointMapClick(ev.latlng);
              return;
            }
            if (state.drawBoundsMode) {
              state.drawPoints.push([ev.latlng.lat, ev.latlng.lng]);
              renderDrawPreview();
              return;
            }
            if (state.traceMode || state.traceContourMode) {
              state.tracePoints.push([ev.latlng.lat, ev.latlng.lng]);
              renderTracePreview();
              return;
            }
            if (state.fishStructDropMode) {
              state.fishStructDropMode = false;
              dropFishStructBtn.classList.remove('active');
              syncMapInteractionMode();
              const pretty = state.fishStructKind.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
              const label = window.prompt(`Name for this ${pretty}:`, pretty);
              if (!label) return;
              const dRaw = window.prompt('Depth in feet (optional, e.g. 12):', '') || '';
              const bottom = window.prompt('Bottom type (optional: rock, gravel, sand, muck, weed, timber):', '') || '';
              const species = window.prompt('Target species (optional: trout, walleye, bass, pike, panfish):', '') || '';
              const res = await ctx.action('add_fish_structure', {
                property_id: state.active ? state.active.property.id : null,
                name: label,
                kind: state.fishStructKind,
                lat: ev.latlng.lat,
                lon: ev.latlng.lng,
                depth_ft: dRaw.trim() ? Number(dRaw) : null,
                bottom_type: bottom,
                target_species: species,
              });
              if (res.status === 'ok') {
                setStatus(`✓ Pinned fish structure "${label}".`);
                await loadState(state.active ? state.active.property.id : res.property_id);
              } else {
                setStatus(`Error saving structure: ${res.message || 'Create a camp first.'}`);
              }
              return;
            }
            if (state.flowDropMode) {
              state.flowDropMode = false;
              dropFlowBtn.classList.remove('active');
              syncMapInteractionMode();
              const rawDir = window.prompt('Water flows TOWARD compass (N, NE, E, SE, S, SW, W, NW) or degrees (0-359):', 'SE');
              if (!rawDir) return;
              const compassDeg = { N: 0, NNE: 22.5, NE: 45, ENE: 67.5, E: 90, ESE: 112.5, SE: 135, SSE: 157.5, S: 180, SSW: 202.5, SW: 225, WSW: 247.5, W: 270, WNW: 292.5, NW: 315, NNW: 337.5 };
              const u = rawDir.trim().toUpperCase();
              const deg = compassDeg[u] != null ? compassDeg[u] : (Number.isFinite(Number(u)) ? Number(u) : 135);
              const spd = window.prompt('Current speed (calm, slow, moderate, fast):', 'moderate') || 'moderate';
              const ftype = window.prompt('Flow type (river_current, inflow, outflow, eddy, wind_drift):', 'river_current') || 'river_current';
              const lbl = window.prompt('Label (optional):', `${ftype.replace('_', ' ')} → ${degToCompass(deg)}`) || '';
              const res = await ctx.action('add_water_flow', {
                property_id: state.active ? state.active.property.id : null,
                label: lbl,
                lat: ev.latlng.lat,
                lon: ev.latlng.lng,
                flow_to_deg: deg,
                speed_class: spd,
                flow_type: ftype,
              });
              if (res.status === 'ok') {
                setStatus(`✓ Added current arrow → ${res.flow_compass} (${spd}).`);
                await loadState(state.active ? state.active.property.id : res.property_id);
              } else {
                setStatus(`Error saving flow arrow: ${res.message || 'Create a camp first.'}`);
              }
              return;
            }
            if (state.depthDropMode) {
              const rawD = window.prompt('Enter water depth at this point (feet, e.g. 14.5 — Cancel to stop dropping):', '');
              if (rawD == null || rawD.trim() === '') {
                state.depthDropMode = false;
                dropDepthBtn.classList.remove('active');
                syncMapInteractionMode();
                return;
              }
              const dVal = parseFloat(rawD);
              if (Number.isNaN(dVal) || dVal < 0) return;
              const targetWbName = bathyWbSel.value || (state.active ? `${state.active.property.name} Water` : 'Camp Water');
              const res = await ctx.action('add_depth_point', {
                property_id: state.active ? state.active.property.id : null,
                lat: ev.latlng.lat,
                lon: ev.latlng.lng,
                depth_ft: dVal,
                waterbody_name: targetWbName,
              });
              if (res.status === 'ok') {
                setStatus(`✓ Recorded ${dVal} ft sounding (${res.total_soundings} total — click more points or click "Build Contours").`);
                await loadState(state.active ? state.active.property.id : res.property_id);
              }
              return;
            }
            if (state.topoDropMode) {
              state.topoDropMode = false;
              dropTopoBtn.classList.remove('active');
              syncMapInteractionMode();
              const pretty = state.topoDropKind.replace(/_/g, ' ');
              const label = window.prompt(`Name for this ${pretty} landmark:`, pretty.replace(/\b\w/g, (c) => c.toUpperCase()));
              if (!label) return;
              const notes = window.prompt('Terrain / hunting notes (optional):', '') || '';
              const res = await ctx.action('add_landmark', {
                property_id: state.active ? state.active.property.id : null,
                name: label,
                kind: state.topoDropKind,
                lat: ev.latlng.lat,
                lon: ev.latlng.lng,
                notes,
              });
              if (res.status === 'ok') {
                const elevTxt = res.elevation_ft != null ? ` (${res.elevation_ft} ft)` : '';
                setStatus(`✓ Tagged ${pretty} landmark "${label}"${elevTxt}.`);
                await loadState(state.active ? state.active.property.id : res.property_id);
              } else {
                setStatus(`Error saving landmark: ${res.message || 'Create a camp first.'}`);
              }
              return;
            }
            if (state.annotateDropMode) {
              state.annotateDropMode = false;
              dropAnnBtn.classList.remove('active');
              syncMapInteractionMode();
              const label = window.prompt(`Label for removable ${state.annotateKind.replace('_', ' ')} object:`, state.annotateKind.replace('_', ' '));
              if (!label) return;
              const notes = window.prompt('Tactical notes (optional):', '') || '';
              const res = await ctx.action('add_annotation', {
                property_id: state.active ? state.active.property.id : null,
                label,
                kind: state.annotateKind,
                geom_type: 'Point',
                lat: ev.latlng.lat,
                lon: ev.latlng.lng,
                notes,
                created_by: 'operator',
              });
              if (res.status === 'ok') {
                setStatus(`Added removable map object "${label}".`);
                await loadState(state.active ? state.active.property.id : res.property_id);
              } else {
                setStatus(`Error adding annotation: ${res.message || 'unknown'}`);
              }
              return;
            }
            if (!state.dropMode) return;
            state.dropMode = false;
            dropBtn.classList.remove('active');
            syncMapInteractionMode();
            const label = window.prompt(`Name for this ${state.dropKind}:`, `${state.dropKind} ${Date.now().toString().slice(-3)}`);
            if (!label) return;
            const hunter = window.prompt('Assigned hunter (optional):', '') || '';
            const res = await ctx.action('save_waypoint', {
              property_id: state.active ? state.active.property.id : null,
              property_name: state.active ? state.active.property.name : (nameInput.value.trim() || 'My Hunting Camp'),
              name: label,
              kind: state.dropKind,
              lat: ev.latlng.lat,
              lon: ev.latlng.lng,
              assigned_hunter: hunter,
            });
            if (res.status === 'ok') {
              setStatus(`Saved "${label}". Recalculating scent cones…`);
              await loadState(state.active ? state.active.property.id : res.property_id);
            } else {
              setStatus(`Error saving pin: ${res.message || 'unknown'}`);
            }
          });

          let resizeRaf = 0;
          const handleLayoutResize = () => {
            if (!document.body.contains(root)) return;
            const w = root.clientWidth || window.innerWidth;
            root.classList.toggle('hb-compact', w > 0 && w < 860);
            if (resizeRaf) cancelAnimationFrame(resizeRaf);
            resizeRaf = requestAnimationFrame(() => {
              if (map && document.body.contains(mapDiv)) {
                map.invalidateSize({ animate: false });
              }
            });
          };
          window.addEventListener('resize', handleLayoutResize);
          if (typeof ResizeObserver !== 'undefined') {
            const ro = new ResizeObserver(handleLayoutResize);
            ro.observe(root);
            ro.observe(mapDiv);
            state.resizeObserver = ro;
          }
          setTimeout(handleLayoutResize, 220);
        }

        function updateBasemap() {
          if (!map) return;
          if (baseLayer) { map.removeLayer(baseLayer); baseLayer = null; }
          if (labelLayer) { map.removeLayer(labelLayer); labelLayer = null; }

          if (state.basemap === 'hybrid') {
            baseLayer = window.L.tileLayer(
              '/api/modules/hunting-buddy/tiles/esri_imagery/{z}/{x}/{y}',
              { maxZoom: 18, attribution: 'Esri World Imagery' }
            ).addTo(map);
            labelLayer = window.L.tileLayer(
              '/api/modules/hunting-buddy/tiles/esri_labels/{z}/{x}/{y}',
              { maxZoom: 18, opacity: 0.9, attribution: 'Boundaries & Places' }
            ).addTo(map);
          } else {
            baseLayer = window.L.tileLayer(
              `/api/modules/hunting-buddy/tiles/${state.basemap}/{z}/{x}/{y}`,
              { maxZoom: 18, attribution: 'USGS National Map' }
            ).addTo(map);
          }
        }

        function renderPublicLands() {
          if (!map) return;
          publicLandLayer.clearLayers();
          (state.publicLands || []).forEach((pl) => {
            if (!pl.geom) return;
            const gj = window.L.geoJSON(pl.geom, {
              style: { color: '#2dd4bf', weight: 2, fillColor: '#2dd4bf', fillOpacity: 0.2 },
            });
            gj.bindPopup(el('div', {}, [
              el('strong', { text: pl.unit_name || 'Public Hunting Land' }),
              el('div', { text: `${pl.agency || ''} (${pl.source || ''})` }),
              el('div', { text: `${pl.pub_access || 'Open Public Access'}${pl.acres ? ' · ' + pl.acres + ' ac' : ''}` }),
            ]));
            gj.addTo(publicLandLayer);
          });
        }

        function renderParcelsAndWaypoints() {
          if (!map) return;
          parcelLayer.clearLayers();
          waypointLayer.clearLayers();
          standList.replaceChildren();

          const active = state.active;
          const allParcels = [];
          const seenPin = new Set();
          (state.viewportParcels || []).forEach((p) => {
            if (p.parcel_pin && !seenPin.has(p.parcel_pin)) { seenPin.add(p.parcel_pin); allParcels.push(p); }
          });
          if (active) {
            (active.parcels || []).forEach((p) => {
              if (p.parcel_pin && !seenPin.has(p.parcel_pin)) { seenPin.add(p.parcel_pin); allParcels.push(p); }
            });
          }

          const bounds = [];
          allParcels.forEach((p) => {
            if (!p.geom) return;
            const isPub = p.access_status === 'public';
            const isMfl = p.access_status === 'mfl_enrolled';
            const style = isPub
              ? { color: '#2dd4bf', weight: 2.0, fillColor: '#2dd4bf', fillOpacity: 0.16 }
              : isMfl
                ? { color: '#60a5fa', weight: 1.8, dashArray: '5 4', fillColor: '#60a5fa', fillOpacity: 0.13 }
                : { color: '#fbbf24', weight: 1.5, fillColor: '#fbbf24', fillOpacity: 0.05 };
            const gj = window.L.geoJSON(p.geom, { style });
            gj.bindPopup(el('div', {}, [
              el('strong', { text: p.owner_name || 'Private Owner' }),
              el('div', { text: `Parcel ID: ${p.parcel_pin || 'n/a'}` }),
              el('div', { text: `${p.acres || '?'} ac · ${p.plss_desc || p.county || 'WI'}` }),
              el('div', { text: `Status: ${isPub ? 'Public Land' : isMfl ? 'MFL Enrolled' : 'Private'}` }),
            ]));
            gj.addTo(parcelLayer);
            try { bounds.push(gj.getBounds()); } catch (_) {}
          });

          if (!active) return;
          const prop = active.property;
          privacyBtn.textContent = prop.visibility === 'channel_private' ? 'Private' : 'Public';
          privacyBtn.classList.toggle('warn-outline', prop.visibility === 'channel_private');

          // Colorblind-safe Okabe-Ito waypoint marker colors
          const kindColors = {
            stand: '#38bdf8',
            blind: '#2dd4bf',
            camera: '#a78bfa',
            bedding: '#f97316',
            scrape: '#fbbf24',
            camp: '#f8fafc',
          };
          const wps = active.waypoints || [];
          if (!wps.length) {
            standList.appendChild(el('div', { className: 'hb-muted', text: 'No stands or waypoints yet. Click "+ Drop Pin" then click the map.' }));
          }
          wps.forEach((w) => {
            const col = kindColors[w.kind] || '#38bdf8';
            const mk = window.L.circleMarker([w.lat, w.lon], {
              radius: 7,
              color: '#0c1017',
              weight: 2,
              fillColor: col,
              fillOpacity: 0.96,
            });
            mk.bindPopup(el('div', {}, [
              el('strong', { text: `${w.name} (${w.kind})` }),
              el('div', { text: w.assigned_hunter ? `Hunter: ${w.assigned_hunter}` : 'Unassigned' }),
              el('div', { text: `${Number(w.lat).toFixed(5)}, ${Number(w.lon).toFixed(5)}` }),
            ]));
            mk.addTo(waypointLayer);

            const row = el('div', {
              className: 'hb-item clickable',
              on: { click: () => map && map.setView([w.lat, w.lon], 16) },
            }, [
              el('div', { className: 'hb-item-main' }, [
                el('span', { className: 'hb-dot', attrs: { style: `background:${col}` } }),
                el('div', {}, [
                  el('div', { className: 'hb-item-title', text: w.name }),
                  el('div', { className: 'hb-item-sub', text: `${w.kind}${w.assigned_hunter ? ' · ' + w.assigned_hunter : ''}` }),
                ]),
              ]),
              el('button', {
                className: 'hb-btn subtle-danger sm',
                text: '×',
                attrs: { title: 'Delete waypoint' },
                on: {
                  click: async (ev) => {
                    ev.stopPropagation();
                    await ctx.action('delete_waypoint', { id: w.id });
                    await loadState(prop.id);
                  },
                },
              }),
            ]);
            standList.appendChild(row);
          });

          if (bounds.length > 0 && !state.viewportParcels.length) {
            let b = bounds[0];
            for (let i = 1; i < bounds.length; i++) b.extend(bounds[i]);
            map.fitBounds(b, { padding: [24, 24], maxZoom: 16 });
          } else if (prop.center_lat && prop.center_lon && !state.viewportParcels.length) {
            map.setView([prop.center_lat, prop.center_lon], 15);
          }
          syncLayerVisibility();
        }

        async function loadViewportGis(forceRefresh) {
          if (!map) return;
          const b = map.getBounds();
          fetchViewportBtn.disabled = true;
          fetchViewportBtn.textContent = 'Syncing GIS…';
          setStatus('Checking 30-day local spatial cache for parcels & public hunting lands…');
          try {
            const res = await ctx.action('fetch_viewport_gis', {
              min_lon: b.getWest(),
              min_lat: b.getSouth(),
              max_lon: b.getEast(),
              max_lat: b.getNorth(),
              property_id: state.active ? state.active.property.id : null,
              include_parcels: true,
              include_public_lands: true,
              force_refresh: !!forceRefresh,
            });
            if (res.status === 'zoom_in') {
              setStatus(res.message);
            } else if (res.status === 'ok') {
              state.viewportParcels = res.parcels || [];
              state.publicLands = res.public_lands || [];
              renderPublicLands();
              renderParcelsAndWaypoints();
              setStatus(`Loaded ${state.viewportParcels.length} parcel(s) + ${state.publicLands.length} public tract(s) (cache hits: ${res.cache_hits}, upstream: ${res.upstream_fetches}).`);
            } else {
              setStatus(`GIS fetch error: ${res.message || 'unknown'}`);
            }
          } finally {
            fetchViewportBtn.disabled = false;
            fetchViewportBtn.textContent = 'Sync GIS';
          }
        }

        async function refreshScentPlan() {
          const fromC = degToCompass(state.windDeg);
          const toC = degToCompass((state.windDeg + 180) % 360);
          windHeroVal.textContent = `${fromC} ${state.windDeg}° · ${state.windMph} mph`;
          windHeroSub.textContent = `Scent → ${toC}`;
          windDegNum.textContent = `${state.windDeg}°`;
          windMphNum.textContent = `${state.windMph} mph`;

          if (!state.active || !map) return;
          const res = await ctx.action('plan', {
            property_id: state.active.property.id,
            wind_deg: state.windDeg,
            wind_mph: state.windMph,
            thermal_mode: state.thermalMode,
          });
          scentLayer.clearLayers();
          if (res.status !== 'ok') {
            conflictBox.className = 'hb-banner';
            conflictBox.textContent = res.message || 'Add stands to evaluate scent cones.';
            return;
          }
          // Colorblind-safe Vermilion scent plume with dashed boundary
          (res.stand_scores || []).forEach((s) => {
            if (!s.cone_geojson) return;
            window.L.geoJSON(s.cone_geojson, {
              style: { color: '#f97316', weight: 1.5, dashArray: '4 3', fillColor: '#f97316', fillOpacity: 0.22 },
            }).addTo(scentLayer);
          });
          if (res.conflicts && res.conflicts.length > 0) {
            conflictBox.className = 'hb-banner hazard';
            conflictBox.textContent = `⊘ ${res.conflicts.length} downwind conflict(s): ` + res.conflicts.map((c) => c.detail).join(' · ');
          } else {
            conflictBox.className = 'hb-banner safe';
            const top = (res.stand_scores || [])[0];
            conflictBox.textContent = top
              ? `✓ Zero downwind conflicts. Top stand: ${top.name} (score ${top.score}).`
              : '✓ Zero downwind conflicts.';
          }
        }

        function renderHunters() {
          if (!map) return;
          hunterLayer.clearLayers();
          hunterList.replaceChildren();
          const list = state.hunters || [];
          if (!list.length) {
            hunterList.appendChild(el('div', { className: 'hb-muted', text: 'No active mobile GPS beacons.' }));
            return;
          }
          list.forEach((h) => {
            if (h.track && h.track.length > 1) {
              window.L.polyline(h.track, { color: '#38bdf8', weight: 2, dashArray: '4 4', opacity: 0.85 }).addTo(hunterLayer);
            }
            const mk = window.L.circleMarker([h.lat, h.lon], {
              radius: 7,
              color: '#ffffff',
              weight: 2,
              fillColor: '#2563eb',
              fillOpacity: 1.0,
            });
            mk.bindPopup(el('div', {}, [
              el('strong', { text: h.hunter_name || h.hunter_id }),
              el('div', { text: `Heading: ${Math.round(h.heading_deg || 0)}° (${degToCompass(h.heading_deg || 0)})` }),
              el('div', { text: `Status: ${h.status || 'active'} · Battery: ${h.battery_pct != null ? h.battery_pct + '%' : 'n/a'}` }),
            ]));
            mk.addTo(hunterLayer);

            const rad = ((h.heading_deg || 0) * Math.PI) / 180;
            const dLat = (90 / 111320) * Math.cos(rad);
            const dLon = (90 / (111320 * Math.cos((h.lat * Math.PI) / 180))) * Math.sin(rad);
            window.L.polyline([[h.lat, h.lon], [h.lat + dLat, h.lon + dLon]], { color: '#38bdf8', weight: 3 }).addTo(hunterLayer);

            hunterList.appendChild(el('div', {
              className: 'hb-item clickable',
              on: { click: () => map.setView([h.lat, h.lon], 16) },
            }, [
              el('div', { className: 'hb-item-main' }, [
                el('span', { className: 'hb-dot', attrs: { style: 'background:#38bdf8' } }),
                el('div', {}, [
                  el('div', { className: 'hb-item-title', text: h.hunter_name || h.hunter_id }),
                  el('div', { className: 'hb-item-sub', text: `${degToCompass(h.heading_deg || 0)} ${Math.round(h.heading_deg || 0)}° · ${h.status || 'active'}` }),
                ]),
              ]),
              el('span', { className: 'hb-mono-tag', text: 'Track →' }),
            ]));
          });
        }

        function annotationCenter(geom) {
          if (!geom || !geom.coordinates) return null;
          if (geom.type === 'Point') return [geom.coordinates[1], geom.coordinates[0]];
          if (geom.type === 'LineString' && geom.coordinates.length) {
            const m = geom.coordinates[Math.floor(geom.coordinates.length / 2)];
            return [m[1], m[0]];
          }
          if (geom.type === 'Polygon' && geom.coordinates[0] && geom.coordinates[0].length) {
            const ring = geom.coordinates[0];
            let sLat = 0, sLon = 0;
            ring.forEach((c) => { sLon += c[0]; sLat += c[1]; });
            return [sLat / ring.length, sLon / ring.length];
          }
          return null;
        }

        function safeAnnotationColor(ann) {
          // Remap legacy red/green defaults to colorblind-safe Okabe-Ito equivalents
          const mapByKind = {
            stand_pick: '#38bdf8',
            approach_route: '#2dd4bf',
            deer_corridor: '#fbbf24',
            bedding_zone: '#f97316',
            pinch_point: '#a78bfa',
            food_source: '#60a5fa',
            avoid_zone: '#f97316',
            callout: '#a78bfa',
          };
          const raw = (ann.color || '').toLowerCase();
          if (!raw || raw === '#ef4444' || raw === '#22c55e' || raw === '#10b981') {
            return mapByKind[ann.kind] || '#a78bfa';
          }
          return ann.color;
        }

        function renderPlansAndAnnotations() {
          if (!map || !planAnnotationsLayer) return;
          planAnnotationsLayer.clearLayers();
          savedPlansList.replaceChildren();
          annList.replaceChildren();

          const plans = state.plans || [];
          const anns = state.annotations || [];
          tabBtnPlans.textContent = plans.length ? `Plans (${plans.length})` : 'Plans';

          if (!plans.length) {
            savedPlansList.appendChild(el('div', { className: 'hb-muted', text: `No saved plans yet. Click "Ask ${getAgentName()}" above to generate and archive one.` }));
          } else {
            plans.forEach((pl) => {
              const windTxt = pl.wind_deg != null
                ? `${degToCompass(pl.wind_deg)} ${Math.round(pl.wind_deg)}° @ ${Math.round(pl.wind_mph || 0)} mph`
                : 'General Plan';
              const hasVis = (pl.visible_annotation_count || 0) > 0;
              const isSelected = state.activePlanView && state.activePlanView.id === pl.id;

              savedPlansList.appendChild(el('div', {
                className: 'hb-item clickable' + (isSelected ? ' selected' : ''),
                on: { click: () => openPlanDrawer(pl, false) },
              }, [
                el('div', { className: 'hb-item-main' }, [
                  el('div', {}, [
                    el('div', { className: 'hb-item-title', text: `${pl.plan_mode === 'fish' ? '🎣 ' : '🦌 '}${pl.title || 'Hunting Plan'}` }),
                    el('div', {
                      className: 'hb-item-sub',
                      text: `${windTxt} · ${pl.annotation_count || 0} map obj · ${(pl.created_at || '').slice(0, 10)}`,
                    }),
                  ]),
                ]),
                el('div', { className: 'hb-row nowrap' }, [
                  ...(pl.annotation_count > 0 ? [el('button', {
                    className: 'hb-btn ghost sm',
                    text: hasVis ? 'Hide' : 'Show',
                    attrs: { title: 'Toggle this plan’s map objects' },
                    on: {
                      click: async (ev) => {
                        ev.stopPropagation();
                        await ctx.action('toggle_plan_annotations', { plan_id: pl.id, visible: !hasVis });
                        await loadState(state.active ? state.active.property.id : null);
                      },
                    },
                  })] : []),
                  el('button', {
                    className: 'hb-btn subtle-danger sm',
                    text: '×',
                    attrs: { title: 'Delete plan and its map objects' },
                    on: {
                      click: async (ev) => {
                        ev.stopPropagation();
                        await ctx.action('delete_plan', { plan_id: pl.id });
                        if (state.activePlanView && state.activePlanView.id === pl.id) {
                          state.activePlanView = null;
                          planDrawer.style.display = 'none';
                        }
                        await loadState(state.active ? state.active.property.id : null);
                      },
                    },
                  }),
                ]),
              ]));
            });
          }

          if (!anns.length) {
            annList.appendChild(el('div', { className: 'hb-muted', text: 'No removable map objects placed yet.' }));
          } else {
            anns.forEach((ann) => {
              const col = safeAnnotationColor(ann);
              const isVis = ann.visible !== false;
              const center = annotationCenter(ann.geom);

              if (isVis && ann.geom) {
                const removeBtn = el('button', {
                  className: 'hb-btn warn-outline sm',
                  text: 'Remove Object',
                  attrs: { style: 'margin-top:8px' },
                  on: {
                    click: async () => {
                      await ctx.action('delete_annotation', { annotation_id: ann.id });
                      await loadState(state.active ? state.active.property.id : null);
                    },
                  },
                });
                const pop = el('div', {}, [
                  el('strong', { text: ann.label || 'Map Object' }),
                  el('div', { text: `${(ann.kind || 'callout').replace(/_/g, ' ')} · ${ann.geom_type || 'Point'} · by ${ann.created_by || getAgentName().toLowerCase()}` }),
                  ...(ann.notes ? [el('div', { text: normalizeReportText(ann.notes), attrs: { style: 'margin-top:4px;max-width:250px' } })] : []),
                  removeBtn,
                ]);

                if (ann.geom.type === 'Point' && center) {
                  const mk = window.L.circleMarker(center, {
                    radius: 8,
                    color: '#ffffff',
                    weight: 2,
                    fillColor: col,
                    fillOpacity: 0.95,
                  });
                  mk.bindTooltip(ann.label || '', { direction: 'top', offset: [0, -8] });
                  mk.bindPopup(pop);
                  mk.addTo(planAnnotationsLayer);
                } else {
                  const isLine = ann.geom.type === 'LineString';
                  const gj = window.L.geoJSON(ann.geom, {
                    style: isLine
                      ? { color: col, weight: 3.5, dashArray: '7 5', opacity: 0.95 }
                      : { color: col, weight: 2.0, dashArray: '5 4', fillColor: col, fillOpacity: 0.22 },
                  });
                  gj.bindTooltip(ann.label || '', { sticky: true });
                  gj.bindPopup(pop);
                  gj.addTo(planAnnotationsLayer);
                }
              }

              annList.appendChild(el('div', {
                className: 'hb-item clickable' + (isVis ? '' : ' dim'),
                on: { click: () => center && map.setView(center, 16) },
              }, [
                el('div', { className: 'hb-item-main' }, [
                  el('span', { className: 'hb-dot', attrs: { style: `background:${col}` } }),
                  el('div', {}, [
                    el('div', { className: 'hb-item-title', text: ann.label || 'Object' }),
                    el('div', { className: 'hb-item-sub', text: `${(ann.kind || 'callout').replace(/_/g, ' ')} · ${ann.geom_type || 'Point'}` }),
                  ]),
                ]),
                el('div', { className: 'hb-row nowrap' }, [
                  el('button', {
                    className: 'hb-btn ghost sm',
                    text: isVis ? 'Hide' : 'Show',
                    on: {
                      click: async (ev) => {
                        ev.stopPropagation();
                        await ctx.action('toggle_annotation', { annotation_id: ann.id, visible: !isVis });
                        await loadState(state.active ? state.active.property.id : null);
                      },
                    },
                  }),
                  el('button', {
                    className: 'hb-btn subtle-danger sm',
                    text: '×',
                    attrs: { title: 'Remove object from map' },
                    on: {
                      click: async (ev) => {
                        ev.stopPropagation();
                        await ctx.action('delete_annotation', { annotation_id: ann.id });
                        await loadState(state.active ? state.active.property.id : null);
                      },
                    },
                  }),
                ]),
              ]));
            });
          }
          syncLayerVisibility();
        }

        function openPlanDrawer(planObj, allowSaveBtn) {
          state.activePlanView = planObj;
          planDrawer.style.display = 'flex';
          planDrawer.replaceChildren();
          renderPlansAndAnnotations();

          const windTxt = planObj.wind_deg != null
            ? `${degToCompass(planObj.wind_deg)} ${Math.round(planObj.wind_deg)}° @ ${Math.round(planObj.wind_mph || 0)} mph · ${String(planObj.thermal_mode || 'neutral').replace(/_/g, ' ')}`
            : (planObj.wind_summary || 'Tactical Plan');

          const sl = planObj.shooting_light || {};
          const slTxt = typeof sl === 'string'
            ? sl
            : (sl.legal_shooting_start || sl.legal_start
              ? `Legal Light ${sl.legal_shooting_start || sl.legal_start}–${sl.legal_shooting_end || sl.legal_end}`
              : '');

          const headerBtns = [
            el('button', {
              className: 'hb-btn ghost sm',
              text: 'Close',
              on: {
                click: () => {
                  planDrawer.style.display = 'none';
                  state.activePlanView = null;
                  renderPlansAndAnnotations();
                },
              },
            }),
          ];
          if (allowSaveBtn && state.active) {
            const savePlanBtn = el('button', {
              className: 'hb-btn primary sm',
              text: 'Save to Plans',
              on: {
                click: async () => {
                  savePlanBtn.disabled = true;
                  savePlanBtn.textContent = 'Saving…';
                  const res = await ctx.action('save_plan', {
                    property_id: state.active.property.id,
                    title: planObj.title || `${state.active.property.name} Tactical Plan`,
                    wind_deg: planObj.wind_deg != null ? planObj.wind_deg : state.windDeg,
                    wind_mph: planObj.wind_mph != null ? planObj.wind_mph : state.windMph,
                    thermal_mode: planObj.thermal_mode || state.thermalMode,
                    shooting_light: planObj.shooting_light || {},
                    top_stands: planObj.top_stands || [],
                    blown_stands: planObj.blown_stands || [],
                    briefing_markdown: planObj.briefing_markdown || '',
                    annotations: planObj.annotations || [],
                    source_task_id: planObj.source_task_id || '',
                    plan_mode: planObj.plan_mode || state.appMode || 'hunt',
                  });
                  if (res.status === 'ok') {
                    setStatus(`Saved plan #${res.plan_id} with ${res.annotations_created || 0} map object(s).`);
                    await loadState(state.active.property.id);
                    const fresh = (state.plans || []).find((p) => p.id === res.plan_id);
                    if (fresh) openPlanDrawer(fresh, false);
                  } else {
                    savePlanBtn.disabled = false;
                    savePlanBtn.textContent = 'Save to Plans';
                  }
                },
              },
            });
            headerBtns.unshift(savePlanBtn);
          }

          planDrawer.appendChild(el('div', { className: 'hb-plan-header' }, [
            el('div', {}, [
              el('div', { className: 'hb-plan-title', text: decodeHtmlEntities(planObj.title || 'Tactical Hunting Plan') }),
              el('div', { className: 'hb-plan-meta' }, [
                el('span', { className: 'hb-meta-chip', text: windTxt }),
                ...(slTxt ? [el('span', { className: 'hb-meta-chip', text: slTxt })] : []),
                ...(planObj.created_at ? [el('span', { className: 'hb-muted', text: `Saved ${planObj.created_at.slice(0, 16).replace('T', ' ')}` })] : []),
              ]),
            ]),
            el('div', { className: 'hb-row' }, headerBtns),
          ]));

          const isFishPlan = (planObj.plan_mode || '').toLowerCase() === 'fish' || /fish|bathymetr|contour|reservoir|walleye|musky/i.test(planObj.title || '');
          const topStands = Array.isArray(planObj.top_stands) ? planObj.top_stands : [];
          const blownStands = Array.isArray(planObj.blown_stands) ? planObj.blown_stands : [];
          if (topStands.length || blownStands.length) {
            const cols = el('div', { className: 'hb-plan-cols' });
            const goodBox = el('div', { className: 'hb-plan-box good' }, [
              el('div', { className: 'hb-plan-box-head', text: isFishPlan ? '✓ Top Fishing Zones' : '✓ Recommended Stands' }),
            ]);
            if (!topStands.length) {
              goodBox.appendChild(el('div', { className: 'hb-muted', text: 'None specified.' }));
            } else {
              topStands.forEach((s) => {
                const txt = typeof s === 'string'
                  ? s
                  : `${s.name || s.stand || (isFishPlan ? 'Zone' : 'Stand')}${s.score != null ? ` (${s.score}/100)` : ''}${s.rationale || s.reason ? ' — ' + (s.rationale || s.reason) : ''}`;
                goodBox.appendChild(el('div', { text: decodeHtmlEntities(txt) }));
              });
            }
            const badBox = el('div', { className: 'hb-plan-box bad' }, [
              el('div', { className: 'hb-plan-box-head', text: isFishPlan ? '⊘ Low-Percentage / Avoid Zones' : '⊘ Avoid / Blown Stands' }),
            ]);
            if (!blownStands.length) {
              badBox.appendChild(el('div', { className: 'hb-muted', text: isFishPlan ? 'No restricted zones noted.' : 'Zero blown stands on this wind.' }));
            } else {
              blownStands.forEach((s) => {
                const txt = typeof s === 'string'
                  ? s
                  : `${s.name || s.stand || (isFishPlan ? 'Zone' : 'Stand')}${s.reason || s.rationale ? ' — ' + (s.reason || s.rationale) : ''}`;
                badBox.appendChild(el('div', { text: decodeHtmlEntities(txt) }));
              });
            }
            cols.appendChild(goodBox);
            cols.appendChild(badBox);
            planDrawer.appendChild(cols);
          }

          const reportHost = el('div', { className: 'hb-report' });
          renderSafeReport(reportHost, planObj.briefing_markdown || planObj.summary || '');
          planDrawer.appendChild(reportHost);

          // ── Follow-Up Q&A Thread tied to this Saved Plan ──────────────────
          const qaBox = el('div', { className: 'hb-qa-box' });
          qaBox.appendChild(el('div', { className: 'hb-qa-head' }, [
            el('span', { text: 'Follow-Up Q&A About This Plan' }),
            el('span', { className: 'hb-muted', text: planObj.id ? `Plan #${planObj.id}` : 'Save plan to ask follow-ups' }),
          ]));

          if (!planObj.id) {
            qaBox.appendChild(el('div', { className: 'hb-muted', text: 'Click "Save to Plans" above to ask follow-up questions about specific stands, pinch points, or routes in this briefing.' }));
          } else {
            const qaList = el('div', { className: 'hb-qa-list' });
            const fups = Array.isArray(planObj.followups) ? planObj.followups : [];
            if (!fups.length) {
              qaList.appendChild(el('div', { className: 'hb-muted', text: 'Ask about any pin, pinch point, thermal shift, or stealth entry route in this briefing.' }));
            } else {
              fups.forEach((fu) => {
                const ansHost = el('div', { className: 'hb-qa-a hb-report' });
                renderSafeReport(ansHost, fu.answer_markdown || '');
                qaList.appendChild(el('div', { className: 'hb-qa-turn' }, [
                  el('div', { className: 'hb-qa-q', text: `Q: ${fu.question || ''}` }),
                  ansHost,
                ]));
              });
            }
            qaBox.appendChild(qaList);

            const qaInput = el('input', {
              className: 'hb-input',
              placeholder: 'Ask about a pinch point, stand setup, wind shift, or entry route…',
            });
            const qaAskBtn = el('button', { className: 'hb-btn primary sm', text: 'Ask' });
            const qaStatus = el('div', { className: 'hb-muted', attrs: { style: 'display:none' } });

            const submitFollowup = async (qOverride) => {
              const qTxt = (qOverride != null ? qOverride : qaInput.value).trim();
              if (!qTxt) { qaInput.focus(); return; }
              qaInput.value = '';
              qaAskBtn.disabled = true;
              qaAskBtn.textContent = 'Thinking…';
              qaStatus.style.display = 'block';
              qaStatus.textContent = `Asking about Plan #${planObj.id}: "${qTxt}"…`;
              setStatus(`Launching Plan #${planObj.id} follow-up Q&A…`);
              try {
                const prop = state.active && state.active.property;
                await ctx.launchTask('plan_followup', {
                  params: {
                    property_id: prop ? prop.id : (planObj.property_id || 1),
                    plan_id: planObj.id,
                    camp: prop ? prop.name : (planObj.title || 'Active Camp'),
                    question: qTxt,
                  },
                }, {
                  streamContainer: georgeTaskHost,
                  onComplete: async (rd) => {
                    qaAskBtn.disabled = false;
                    qaAskBtn.textContent = 'Ask';
                    qaStatus.style.display = 'none';
                    await loadState(prop ? prop.id : (planObj.property_id || null));
                    const updated = (state.plans || []).find((p) => p.id === planObj.id);
                    // Fallback: if workflow returned answer_markdown in result.json without calling save_followup, save it now
                    const ansFromRes = rd && rd.result && rd.result.answer_markdown;
                    if (updated && ansFromRes && !(updated.followups || []).some((f) => f.question === qTxt)) {
                      await ctx.action('save_plan_followup', {
                        plan_id: planObj.id,
                        property_id: prop ? prop.id : planObj.property_id,
                        question: qTxt,
                        answer_markdown: ansFromRes,
                      });
                      await loadState(prop ? prop.id : (planObj.property_id || null));
                    }
                    const fresh = (state.plans || []).find((p) => p.id === planObj.id);
                    if (fresh) openPlanDrawer(fresh, false);
                    setStatus(`✓ Follow-up answer saved to Plan #${planObj.id}.`);
                  },
                  onError: (err) => {
                    qaAskBtn.disabled = false;
                    qaAskBtn.textContent = 'Ask';
                    qaStatus.textContent = `Follow-up error: ${err}`;
                    setStatus(`Plan follow-up error: ${err}`);
                  },
                });
              } catch (e) {
                qaAskBtn.disabled = false;
                qaAskBtn.textContent = 'Ask';
                qaStatus.textContent = `Failed to launch follow-up: ${e.message}`;
              }
            };

            qaAskBtn.addEventListener('click', () => submitFollowup());
            qaInput.addEventListener('keydown', (ev) => {
              if (ev.key === 'Enter' && !ev.shiftKey) {
                ev.preventDefault();
                submitFollowup();
              }
            });

            const chips = [
              'Explain the map pins and pinch points in detail',
              'What is the best stealth entry & exit route?',
              'How does tomorrow morning wind change the top stand?',
            ];
            qaBox.appendChild(el('div', { className: 'hb-qa-chips' }, chips.map((c) => el('button', {
              className: 'hb-qa-chip',
              text: c,
              on: { click: () => submitFollowup(c) },
            }))));
            qaBox.appendChild(el('div', { className: 'hb-qa-form' }, [qaInput, qaAskBtn]));
            qaBox.appendChild(qaStatus);
          }
          planDrawer.appendChild(qaBox);
        }

        function pickRichestMarkdown(...candidates) {
          let best = '';
          candidates.forEach((c) => {
            if (typeof c !== 'string') return;
            const s = c.trim();
            if (!s || isRawAgentTranscript(s)) return;
            if (s.length > best.length) best = s;
          });
          return best;
        }

        function inferPlanMode(wfId, res, planObj) {
          const explicit = (res && res.plan_mode) || (planObj && planObj.plan_mode) || '';
          if (explicit === 'fish' || explicit === 'hunt') return explicit;
          const wf = String(wfId || (res && res.workflow) || '').toLowerCase();
          if (wf.includes('fish') || wf.includes('lake') || wf.includes('contour')) return 'fish';
          return state.appMode === 'fish' ? 'fish' : 'hunt';
        }

        async function reconcileCompletedTaskPlan(rd, wfId, prevMaxPlanId) {
          const propId = state.active ? state.active.property.id : null;
          await loadState(propId);
          const res = rd && rd.result && typeof rd.result === 'object' ? rd.result : null;
          const plans = state.plans || [];
          const pidSaved = res && res.plan_id ? Number(res.plan_id) : null;
          let matched = (pidSaved && plans.find((p) => Number(p.id) === pidSaved))
            || (rd && rd.task_id && plans.find((p) => p.source_task_id && p.source_task_id === rd.task_id))
            || null;
          if (!matched && prevMaxPlanId != null) {
            const newlyCreated = plans.filter((p) => Number(p.id || 0) > Number(prevMaxPlanId));
            if (newlyCreated.length) {
              newlyCreated.sort((a, b) => Number(b.id) - Number(a.id));
              matched = newlyCreated[0];
            }
          }
          // NEVER feed raw CLI transcript stdout (rd.output_text) into saved plans; backend update_plan cleanly reads final_reply.md from disk!
          const cleanOutput = (rd && rd.output_text && !isRawAgentTranscript(rd.output_text)) ? rd.output_text : '';
          const richestMd = pickRichestMarkdown(
            res && res.briefing_markdown,
            matched && matched.briefing_markdown,
            res && res.summary,
            cleanOutput,
          );
          const mode = inferPlanMode(wfId, res, matched);
          if (matched) {
            try {
              await ctx.action('update_plan', {
                plan_id: matched.id,
                briefing_markdown: richestMd || undefined,
                plan_mode: mode,
                source_task_id: (rd && rd.task_id) || matched.source_task_id || '',
                only_if_richer: true,
              });
              await loadState(propId);
              matched = (state.plans || []).find((p) => Number(p.id) === Number(matched.id)) || matched;
            } catch (_) {}
            openPlanDrawer(matched, false);
          } else {
            renderBriefingFromTask(rd, wfId);
          }
        }

        function renderBriefingFromTask(rd, wfId) {
          if (!rd) return;
          const res = rd.result && typeof rd.result === 'object' ? rd.result : null;
          const cleanOutput = (rd.output_text && !isRawAgentTranscript(rd.output_text)) ? rd.output_text : '';
          const richestMd = pickRichestMarkdown(
            res && res.briefing_markdown,
            res && res.summary,
            cleanOutput,
          );
          const mode = inferPlanMode(wfId, res, null);
          if (res) {
            const existing = (res.plan_id && (state.plans || []).find((p) => Number(p.id) === Number(res.plan_id)))
              || (rd.task_id && (state.plans || []).find((p) => p.source_task_id === rd.task_id));
            if (existing && !isRawAgentTranscript(existing.briefing_markdown) && String(existing.briefing_markdown || '').length >= richestMd.length) {
              openPlanDrawer(existing, false);
              return;
            }
            openPlanDrawer({
              id: (existing && existing.id) || res.plan_id || null,
              title: res.title || `${res.camp_name || 'Camp'} — ${mode === 'fish' ? 'Fishing & Structure Plan' : 'Tactical Briefing'}`,
              plan_mode: mode,
              wind_summary: typeof res.wind_summary === 'string' ? res.wind_summary : JSON.stringify(res.wind_summary || ''),
              shooting_light: res.shooting_light || {},
              top_stands: res.top_stands || res.recommended_stand_spots || [],
              blown_stands: res.blown_stands || [],
              briefing_markdown: richestMd,
              annotations: res.annotations || [],
              source_task_id: rd.task_id || '',
            }, !(existing || res.plan_id));
          } else if (richestMd) {
            openPlanDrawer({
              title: `${getAgentName()} ${mode === 'fish' ? 'Fishing Plan' : 'Briefing'} (${rd.phase || 'done'})`,
              plan_mode: mode,
              briefing_markdown: richestMd,
              source_task_id: rd.task_id || '',
            }, true);
          }
        }

        async function refreshTaskHistory() {
          if (!ctx.listTasks) return;
          try {
            const res = await ctx.listTasks();
            const tasks = (res && res.tasks) || [];
            if (!tasks.length) {
              historyBar.style.display = 'none';
              return;
            }
            historyBar.style.display = 'flex';
            historyBar.replaceChildren(el('span', { className: 'hb-eyebrow', text: 'Recent Runs' }));
            tasks.slice(0, 6).forEach((t) => {
              const shortLabel = (t.label || t.workflow || t.task_id).slice(0, 38);
              const badge = t.done ? (t.has_result ? '✓' : '·') : '●';
              historyBar.appendChild(el('button', {
                className: 'hb-btn sm',
                text: `${badge} ${shortLabel}`,
                on: {
                  click: async () => {
                    const rd = await ctx.getTaskResult(t.task_id);
                    renderBriefingFromTask(rd);
                  },
                },
              }));
            });
          } catch (_) {
            historyBar.style.display = 'none';
          }
        }

        function renderThermalCard(h) {
          const mode = state.thermalMode || 'neutral';
          thermalCard.className = 'hb-thermal-card'
            + (mode === 'katabatic_drainage' ? ' katabatic' : (mode === 'upslope_warming' ? ' upslope' : ''));
          thermalCard.replaceChildren();

          const defaults = {
            katabatic_drainage: {
              label: '↓ Katabatic Drainage (Sinking Downhill)',
              explanation: 'Evening & dawn cooling pulls dense air downhill into creeks, draws, and low bottoms.',
              tactic: 'Hunt upper ridges/benches or set up below bedding so your scent drains away downhill.',
            },
            upslope_warming: {
              label: '↑ Upslope Warming (Rising Uphill)',
              explanation: 'Morning/midday sun warms terrain and lifts air & scent uphill toward ridge tops.',
              tactic: 'Hunt mid-to-lower slopes or keep bedding downhill so rising thermals carry scent over deer.',
            },
            neutral: {
              label: '→ Neutral / Wind-Driven Flow',
              explanation: 'Steady ambient wind (>12 mph) or heavy cloud cover overrides local slope thermals.',
              tactic: 'Trust the primary wind arrow; scent travels mostly straight downwind with minimal slope pull.',
            },
          };
          const fb = defaults[mode] || defaults.neutral;
          const useLive = h && h.thermal_mode === mode && h.thermal_explanation;
          const titleTxt = useLive ? (h.thermal_label || fb.label) : fb.label;
          const expTxt = useLive ? h.thermal_explanation : fb.explanation;
          const tacTxt = useLive ? (h.thermal_tactic || fb.tactic) : fb.tactic;

          const t = state.terrain;
          const demNote = (t && t.has_dem && t.dominant_downhill_deg != null)
            ? `Terrain DEM active: dominant downhill drainage → ${degToCompass(t.dominant_downhill_deg)} (${Math.round(t.relief_ft || 0)} ft relief).`
            : 'Tip: Click "Fetch LiDAR DEM" below to plot uphill/downhill thermal flow arrows on the map.';

          thermalCard.appendChild(el('div', { className: 'hb-thermal-title', text: titleTxt }));
          thermalCard.appendChild(el('div', { text: expTxt }));
          thermalCard.appendChild(el('div', { className: 'hb-thermal-tactic', text: `Tactic: ${tacTxt}` }));
          thermalCard.appendChild(el('div', { className: 'hb-wx-sub', text: demNote }));
        }

        function renderWeatherHud(h) {
          if (!h) {
            wxGrid.style.display = 'none';
            moveCard.style.display = 'none';
            return;
          }
          wxGrid.style.display = 'grid';
          wxGrid.replaceChildren();

          const makeCell = (k, v, sub) => el('div', { className: 'hb-wx-cell' }, [
            el('span', { className: 'hb-wx-k', text: k }),
            el('span', { className: 'hb-wx-v', text: v }),
            el('span', { className: 'hb-wx-sub', text: sub }),
          ]);

          const gustTxt = h.gust_mph && h.gust_mph > h.wind_mph + 2 ? ` · Gust ${h.gust_mph}` : '';
          const precipTxt = h.snowfall_cm > 0
            ? `Snow ${h.snowfall_cm} cm (${h.precip_prob || 0}%)`
            : (h.precip_mm > 0 ? `Rain ${h.precip_mm} mm (${h.precip_prob || 0}%)` : `Precip ${h.precip_prob || 0}% · Cloud ${h.cloud_cover_pct ?? 0}%`);
          const pArrow = h.pressure_trend === 'rising' ? '↗' : (h.pressure_trend === 'falling' ? '↘' : '→');
          const pDelta = h.pressure_delta_3h != null ? `${h.pressure_delta_3h >= 0 ? '+' : ''}${h.pressure_delta_3h} hPa/3h` : '';
          const moon = h.moon || (state.weather && state.weather.moon) || {};
          const sh = h.shooting_light || ((state.weather && state.weather.shooting_hours) || [])[0] || {};
          const lightStr = sh.legal_shooting_start && sh.legal_shooting_end
            ? `Light ${sh.legal_shooting_start}–${sh.legal_shooting_end}`
            : `Vis ${h.visibility_mi ?? 10} mi`;

          wxGrid.appendChild(makeCell('Temp & Sky', `${h.temp_f}°F (Feels ${h.feels_like_f}°)`, `${h.condition || 'Clear'}${gustTxt}`));
          wxGrid.appendChild(makeCell('Barometer', `${h.pressure_inhg} inHg ${pArrow}`, `${h.pressure_trend || 'steady'} · ${pDelta}`));
          wxGrid.appendChild(makeCell('Moisture & Solar', `RH ${h.humidity_pct}% · DP ${h.dew_point_f}°`, `${precipTxt} · ${h.shortwave_wm2 ?? 0} W/m²`));
          wxGrid.appendChild(makeCell('Moon & Light', `${moon.icon || '🌙'} ${moon.name || 'Moon'} (${moon.illumination_pct ?? 0}%)`, lightStr));

          const score = Number(h.movement_score || 50);
          const rating = h.movement_rating || 'MODERATE';
          const barColor = score >= 80 ? '#2dd4bf' : (score >= 65 ? '#38bdf8' : (score >= 45 ? '#fbbf24' : '#f97316'));
          moveCard.style.display = 'flex';
          moveCard.replaceChildren(
            el('div', { className: 'hb-move-head' }, [
              el('span', { text: 'Deer Movement Index' }),
              el('span', { text: `${score}/100 · ${rating}`, attrs: { style: `color:${barColor}` } }),
            ]),
            el('div', { className: 'hb-move-track' }, [
              el('div', { className: 'hb-move-fill', attrs: { style: `width:${Math.max(5, Math.min(100, score))}%;background:${barColor}` } }),
            ]),
            el('div', {
              className: 'hb-wx-sub',
              text: (h.movement_factors && h.movement_factors.length)
                ? h.movement_factors.join(' · ')
                : 'Baseline seasonal movement window.',
            }),
          );
        }

        function renderMapWeatherHud() {
          mapHud.replaceChildren();
          const fromC = degToCompass(state.windDeg);
          const toC = degToCompass((state.windDeg + 180) % 360);
          const thermShort = state.thermalMode === 'katabatic_drainage'
            ? '↓ Katabatic (Downhill)'
            : (state.thermalMode === 'upslope_warming' ? '↑ Upslope (Uphill)' : '→ Neutral Wind');

          mapHud.appendChild(el('div', { className: 'hb-map-hud-title' }, [
            el('span', { text: `${fromC} ${state.windDeg}° · ${state.windMph} mph` }),
            el('span', { text: `Scent → ${toC}`, attrs: { style: 'color:#f97316' } }),
          ]));
          mapHud.appendChild(el('div', { className: 'hb-map-hud-row' }, [
            el('span', { text: 'Thermal' }),
            el('span', { text: thermShort }),
          ]));

          const h = state.currentWeatherHour;
          if (h) {
            const pArrow = h.pressure_trend === 'rising' ? '↗' : (h.pressure_trend === 'falling' ? '↘' : '→');
            mapHud.appendChild(el('div', { className: 'hb-map-hud-row' }, [
              el('span', { text: `${h.temp_f}°F · ${h.condition || ''}` }),
              el('span', { text: `${h.pressure_inhg} ${pArrow}` }),
            ]));
            const moon = h.moon || (state.weather && state.weather.moon) || {};
            mapHud.appendChild(el('div', { className: 'hb-map-hud-row' }, [
              el('span', { text: `${moon.icon || '🌙'} ${moon.illumination_pct ?? 0}%` }),
              el('span', { text: `Move ${h.movement_score}/100 ${h.movement_rating}` }),
            ]));
            const sh = h.shooting_light || ((state.weather && state.weather.shooting_hours) || [])[0];
            if (sh && sh.legal_shooting_start) {
              mapHud.appendChild(el('div', { className: 'hb-map-hud-row' }, [
                el('span', { text: 'Legal Light' }),
                el('span', { text: `${sh.legal_shooting_start}–${sh.legal_shooting_end}` }),
              ]));
            }
          }
          const t = state.terrain;
          if (t && t.has_dem) {
            const stepTag = t.contour_step_ft ? `${t.contour_step_ft} ft int.` : 'Contours';
            mapHud.appendChild(el('div', { className: 'hb-map-hud-row' }, [
              el('span', { text: `Elev ${Math.round(t.min_elevation_ft || 0)}–${Math.round(t.max_elevation_ft || 0)} ft` }),
              el('span', { text: `Drain → ${degToCompass(t.dominant_downhill_deg || 180)}` }),
            ]));
            mapHud.appendChild(el('div', { className: 'hb-elev-legend' }, [
              el('div', { className: 'hb-elev-bar' }),
              el('div', { className: 'hb-elev-labels' }, [
                el('span', { text: `${Math.round(t.min_elevation_ft || 0)} ft (Low)` }),
                el('span', { text: stepTag }),
                el('span', { text: `${Math.round(t.max_elevation_ft || 0)} ft (Ridge)` }),
              ]),
            ]));
          }
        }

        function elevColorFromNorm(norm) {
          const stops = [
            [0.00, [2, 132, 199]],
            [0.25, [20, 184, 166]],
            [0.50, [132, 204, 22]],
            [0.75, [245, 158, 11]],
            [1.00, [244, 63, 94]],
          ];
          const v = Math.max(0, Math.min(1, Number(norm) || 0));
          for (let i = 0; i < stops.length - 1; i++) {
            const [t0, c0] = stops[i];
            const [t1, c1] = stops[i + 1];
            if (v <= t1) {
              const f = (v - t0) / Math.max(1e-6, t1 - t0);
              const r = Math.round(c0[0] + (c1[0] - c0[0]) * f);
              const g = Math.round(c0[1] + (c1[1] - c0[1]) * f);
              const b = Math.round(c0[2] + (c1[2] - c0[2]) * f);
              return `rgb(${r},${g},${b})`;
            }
          }
          return '#f43f5e';
        }

        function renderTopoAndLandmarks() {
          if (!map || !topoContourLayer || !topoLandmarkLayer) return;
          topoContourLayer.clearLayers();
          topoLandmarkLayer.clearLayers();
          topoList.replaceChildren();

          const t = state.terrain;
          if (t && t.has_dem) {
            const downC = degToCompass(t.dominant_downhill_deg || 180);
            const flowDirNote = state.thermalMode === 'katabatic_drainage'
              ? 'Orange arrows = downhill katabatic scent flow.'
              : (state.thermalMode === 'upslope_warming'
                ? 'Teal arrows = uphill thermal lift.'
                : 'Slate arrows = terrain downhill fall-line.');
            const srcLabel = (t.source || '').includes('usgs_3dep')
              ? 'USGS 3DEP 1m LiDAR'
              : ((t.source || '').includes('lidar') ? t.source : 'Copernicus 30m DEM');
            const resTxt = t.resolution_m ? ` (~${t.resolution_m}m grid)` : '';
            const stepTxt = t.contour_step_ft ? `${t.contour_step_ft} ft contours` : 'Contours';
            topoStatsBox.textContent = `${srcLabel}${resTxt} · ${stepTxt} · ${Math.round(t.min_elevation_ft || 0)}–${Math.round(t.max_elevation_ft || 0)} ft (${Math.round(t.relief_ft || 0)} ft relief) · Slope ${t.avg_slope_pct || 0}% avg / ${t.max_slope_pct || 0}% max · Drain → ${downC}. ${flowDirNote}`;

            topoLegendBox.style.display = 'flex';
            topoLegendBox.replaceChildren(
              el('div', { className: 'hb-elev-bar' }),
              el('div', { className: 'hb-elev-labels' }, [
                el('span', { text: `${Math.round(t.min_elevation_ft || 0)} ft Low Basin` }),
                el('span', { text: `${Math.round(((t.min_elevation_ft || 0) + (t.max_elevation_ft || 0)) / 2)} ft Mid` }),
                el('span', { text: `${Math.round(t.max_elevation_ft || 0)} ft High Ridge` }),
              ]),
            );

            // 1. Hypsometric Elevation-Gradient Contours (color-coded low valley -> high ridge)
            const minElev = Number(t.min_elevation_ft || 0);
            const spanElev = Math.max(1, Number(t.max_elevation_ft || 0) - minElev);
            if (t.contours && Array.isArray(t.contours.features) && t.contours.features.length) {
              window.L.geoJSON(t.contours, {
                style: (feat) => {
                  const pr = (feat && feat.properties) || {};
                  const norm = pr.elev_norm != null
                    ? Number(pr.elev_norm)
                    : (pr.elevation_ft != null ? (Number(pr.elevation_ft) - minElev) / spanElev : 0.5);
                  const col = pr.color || elevColorFromNorm(norm);
                  const isIdx = !!pr.is_index;
                  return {
                    color: col,
                    weight: isIdx ? 2.3 : 1.3,
                    opacity: isIdx ? 0.92 : 0.74,
                  };
                },
                onEachFeature: (feat, lyr) => {
                  const pr = (feat && feat.properties) || {};
                  if (pr.elevation_ft != null) {
                    lyr.bindTooltip(`${pr.elevation_ft} ft${pr.is_index ? ' (Index)' : ''}`, { sticky: true });
                  }
                },
              }).addTo(topoContourLayer);
            }

            // 2. Thermal / Slope Flow Arrows
            const isKat = state.thermalMode === 'katabatic_drainage';
            const isUp = state.thermalMode === 'upslope_warming';
            const arrowColor = isKat ? '#f97316' : (isUp ? '#2dd4bf' : '#94a3b8');
            const arrowOpacity = (isKat || isUp) ? 0.9 : 0.45;
            (t.thermal_vectors || []).forEach((v) => {
              const deg = isUp ? v.uphill_deg : v.downhill_deg;
              const rad = (deg * Math.PI) / 180;
              const lenM = Math.max(55, Math.min(130, 55 + (v.slope_pct || 1) * 7));
              const cosLat = Math.max(0.2, Math.cos((v.lat * Math.PI) / 180));
              const dLat = (lenM / 111320) * Math.cos(rad);
              const dLon = (lenM / (111320 * cosLat)) * Math.sin(rad);
              const tipLat = v.lat + dLat;
              const tipLon = v.lon + dLon;

              // Arrow shaft + chevron wings
              const wingLen = lenM * 0.34;
              const leftRad = ((deg + 150) * Math.PI) / 180;
              const rightRad = ((deg - 150) * Math.PI) / 180;
              const lPt = [tipLat + (wingLen / 111320) * Math.cos(leftRad), tipLon + (wingLen / (111320 * cosLat)) * Math.sin(leftRad)];
              const rPt = [tipLat + (wingLen / 111320) * Math.cos(rightRad), tipLon + (wingLen / (111320 * cosLat)) * Math.sin(rightRad)];

              window.L.polyline([[v.lat, v.lon], [tipLat, tipLon]], {
                color: arrowColor,
                weight: isKat || isUp ? 2.2 : 1.5,
                opacity: arrowOpacity,
                interactive: false,
              }).addTo(topoContourLayer);
              window.L.polyline([lPt, [tipLat, tipLon], rPt], {
                color: arrowColor,
                weight: isKat || isUp ? 2.2 : 1.5,
                opacity: arrowOpacity,
                interactive: false,
              }).addTo(topoContourLayer);
            });
          } else {
            topoLegendBox.style.display = 'none';
            topoStatsBox.textContent = 'Click "Fetch LiDAR DEM" for 1m USGS 3DEP elevation contours, hypsometric gradient & thermal drainage arrows.';
          }

          // 3. Topographic & Water Landmarks
          const lmColors = {
            water_source: '#06b6d4',
            ridge: '#fbbf24',
            saddle: '#a78bfa',
            drainage: '#38bdf8',
            bench: '#2dd4bf',
            food_plot: '#84cc16',
            thick_cover: '#f97316',
            access_gate: '#f8fafc',
            contour: '#94a3b8',
          };
          const lms = state.topoLandmarks || [];
          if (!lms.length) {
            topoList.appendChild(el('div', { className: 'hb-muted', text: 'No topo landmarks yet. Fetch 30m DEM, click "+ Tag Topo", or upload a LiDAR/topo file.' }));
          } else {
            lms.forEach((lm) => {
              const col = lmColors[lm.kind] || '#06b6d4';
              const center = annotationCenter(lm.geom);
              const elevTxt = lm.elevation_ft != null ? `${Math.round(lm.elevation_ft)} ft` : '';
              const prettyKind = (lm.kind || 'landmark').replace(/_/g, ' ');

              if (lm.geom) {
                const delBtn = el('button', {
                  className: 'hb-btn warn-outline sm',
                  text: 'Remove Landmark',
                  attrs: { style: 'margin-top:6px' },
                  on: {
                    click: async () => {
                      await ctx.action('delete_landmark', { id: lm.id });
                      await loadState(state.active ? state.active.property.id : null);
                    },
                  },
                });
                const pop = el('div', {}, [
                  el('strong', { text: lm.name || prettyKind }),
                  el('div', { text: `${prettyKind}${elevTxt ? ' · ' + elevTxt : ''} · ${lm.source || 'user'}` }),
                  ...(lm.notes ? [el('div', { text: lm.notes, attrs: { style: 'margin-top:4px;max-width:240px' } })] : []),
                  delBtn,
                ]);
                if (lm.geom.type === 'Point' && center) {
                  const mk = window.L.circleMarker(center, {
                    radius: 7,
                    color: '#0c1017',
                    weight: 2,
                    fillColor: col,
                    fillOpacity: 0.95,
                  });
                  mk.bindTooltip(`${lm.name || prettyKind}${elevTxt ? ' (' + elevTxt + ')' : ''}`, { direction: 'top', offset: [0, -7] });
                  mk.bindPopup(pop);
                  mk.addTo(topoLandmarkLayer);
                } else {
                  const isLine = lm.geom.type === 'LineString' || lm.geom.type === 'MultiLineString';
                  const gj = window.L.geoJSON(lm.geom, {
                    style: isLine
                      ? { color: col, weight: 2.5, opacity: 0.9 }
                      : { color: col, weight: 2, fillColor: col, fillOpacity: 0.22 },
                  });
                  gj.bindTooltip(`${lm.name || prettyKind}${elevTxt ? ' (' + elevTxt + ')' : ''}`, { sticky: true });
                  gj.bindPopup(pop);
                  gj.addTo(topoLandmarkLayer);
                }
              }

              topoList.appendChild(el('div', {
                className: 'hb-item clickable',
                on: { click: () => center && map.setView(center, 16) },
              }, [
                el('div', { className: 'hb-item-main' }, [
                  el('span', { className: 'hb-dot', attrs: { style: `background:${col}` } }),
                  el('div', {}, [
                    el('div', { className: 'hb-item-title', text: lm.name || prettyKind }),
                    el('div', { className: 'hb-item-sub', text: `${prettyKind}${elevTxt ? ' · ' + elevTxt : ''} · ${lm.source || 'user'}` }),
                  ]),
                ]),
                el('button', {
                  className: 'hb-btn subtle-danger sm',
                  text: '×',
                  attrs: { title: 'Delete topo landmark' },
                  on: {
                    click: async (ev) => {
                      ev.stopPropagation();
                      await ctx.action('delete_landmark', { id: lm.id });
                      await loadState(state.active ? state.active.property.id : null);
                    },
                  },
                }),
              ]));
            });
          }
          syncLayerVisibility();
        }

        async function doFetchDem() {
          if (!state.active) { setStatus('Create or select a camp first before fetching LiDAR DEM elevation.'); return; }
          const stepFt = Number(contourStepSel.value || 0);
          fetchDemBtn.disabled = true;
          fetchDemBtn.textContent = 'Fetching LiDAR…';
          setStatus('Fetching USGS 3DEP 1m LiDAR elevation grid, hypsometric contours & thermal drainage vectors…');
          try {
            const res = await ctx.action('fetch_dem', {
              property_id: state.active.property.id,
              grid_n: 64,
              contour_step_ft: stepFt,
            });
            if (res.status !== 'ok') {
              setStatus(`DEM fetch failed: ${res.message || 'unknown error'}`);
              return;
            }
            state.terrain = res;
            state.topoLandmarks = res.landmarks || [];
            renderTopoAndLandmarks();
            renderThermalCard(state.currentWeatherHour);
            renderMapWeatherHud();
            await refreshScentPlan();
            const cCount = (res.contours && res.contours.features ? res.contours.features.length : 0);
            const vCount = (res.thermal_vectors || []).length;
            const srcTag = (res.source || '').includes('usgs_3dep') ? 'USGS 3DEP 1m LiDAR' : '30m DEM';
            const intTag = res.contour_step_ft ? `${res.contour_step_ft} ft int.` : 'auto int.';
            setStatus(`✓ ${srcTag} loaded (${intTag}): ${Math.round(res.min_elevation_ft || 0)}–${Math.round(res.max_elevation_ft || 0)} ft (${Math.round(res.relief_ft || 0)} ft relief) · ${cCount} contours · ${vCount} thermal arrows · ${state.topoLandmarks.length} landmark(s).`);
          } finally {
            fetchDemBtn.disabled = false;
            fetchDemBtn.textContent = (state.terrain && state.terrain.has_dem) ? 'Refresh DEM' : 'Fetch LiDAR DEM';
          }
        }

        async function doImportTopoFile() {
          if (!state.active) { setStatus('Create or select a camp first before importing topo/LiDAR files.'); return; }
          const file = topoFileInput.files && topoFileInput.files[0];
          if (!file) { setStatus('Select a .asc, .csv, .geojson, .gpx, .kml, or .kmz topo/LiDAR file first.'); return; }
          setStatus(`Importing topo/LiDAR dataset "${file.name}"…`);
          const buf = await file.arrayBuffer();
          const bytes = new Uint8Array(buf);
          let bin = '';
          const chunk = 8192;
          for (let i = 0; i < bytes.length; i += chunk) {
            bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
          }
          const res = await ctx.action('import_topo_dataset', {
            property_id: state.active.property.id,
            filename: file.name,
            file_content_b64: btoa(bin),
            default_kind: state.topoDropKind,
          });
          if (res.status === 'ok') {
            topoFileInput.value = '';
            topoFileLabel.textContent = 'Upload LiDAR/DEM (.asc, .csv) or Topo (.geojson, .gpx, .kml)…';
            await loadState(state.active.property.id);
            if (res.imported_type === 'dem_grid') {
              setStatus(`✓ Imported LiDAR/DEM "${file.name}" (${Math.round(res.min_elevation_ft || 0)}–${Math.round(res.max_elevation_ft || 0)} ft, ${Math.round(res.relief_ft || 0)} ft relief).`);
            } else {
              setStatus(`✓ Imported ${res.landmarks_added || 0} topo/water feature(s) from "${file.name}".`);
            }
          } else {
            setStatus(`Topo import error: ${res.message || 'failed'}`);
          }
        }

        function renderRoutesAndHydro() {
          if (!map || !streamLayer || !roadLayer || !fisheryLayer) return;
          streamLayer.clearLayers();
          roadLayer.clearLayers();
          fisheryLayer.clearLayers();
          routeList.replaceChildren();

          // 1. Public Synced Streams (USGS NHD -> Global OSM Overpass)
          (state.publicStreams || []).forEach((s) => {
            if (!s.geom || !s.geom.coordinates) return;
            const gj = window.L.geoJSON(s.geom, { style: { color: '#06b6d4', weight: 2.0, opacity: 0.72 } });
            gj.bindTooltip(`${s.name || 'Stream'} (${s.provider_id || 'NHD/OSM'})`, { sticky: true });
            gj.addTo(streamLayer);
            if (s.geom.type === 'LineString' && s.geom.coordinates.length >= 2) {
              drawFlowArrowheads(streamLayer, s.geom.coordinates.map((c) => [c[1], c[0]]), '#22d3ee');
            }
          });

          // 2. Public Synced Trails & Access Roads (WI DNR / Global OSM Overpass)
          (state.publicRoads || []).forEach((r) => {
            if (!r.geom) return;
            const isTrail = r.kind === 'walking_trail';
            const gj = window.L.geoJSON(r.geom, {
              style: isTrail
                ? { color: '#a3e635', weight: 1.8, dashArray: '5 4', opacity: 0.72 }
                : { color: '#cbd5e1', weight: 2.0, dashArray: r.kind === 'atv_road' ? '6 4' : null, opacity: 0.68 },
            });
            gj.bindTooltip(`${r.name || 'Road/Trail'} (${r.provider_id || 'GIS'})`, { sticky: true });
            gj.addTo(roadLayer);
          });

          // 3. Public Synced Fisheries Waters (WI DNR Trout / Bass / Walleye / Musky / Easements)
          (state.publicFisheries || []).forEach((f) => {
            if (!f.geom) return;
            const gj = window.L.geoJSON(f.geom, { style: { color: '#38bdf8', weight: 3.0, opacity: 0.85, fillColor: '#38bdf8', fillOpacity: 0.16 } });
            gj.bindTooltip(`${f.name || 'Fishery Water'} · ${f.fishery_tag || 'Designated Water'}`, { sticky: true });
            gj.addTo(fisheryLayer);
          });

          // 4. User-Traced Camp Streams, Trails & Roads
          const rts = state.routes || [];
          if (!rts.length) {
            routeList.appendChild(el('div', { className: 'hb-muted', text: 'No traced streams or paths yet. Click "+ Trace Path" or "Sync Streams & Roads".' }));
          } else {
            const styleByKind = {
              stream: { color: '#22d3ee', weight: 3.2 },
              creek: { color: '#22d3ee', weight: 3.0 },
              current_seam: { color: '#38bdf8', weight: 2.8, dashArray: '6 4' },
              drift_line: { color: '#67e8f9', weight: 2.6, dashArray: '4 4' },
              walking_trail: { color: '#84cc16', weight: 2.6, dashArray: '6 4' },
              entry_route: { color: '#2dd4bf', weight: 2.8, dashArray: '7 4' },
              exit_route: { color: '#2dd4bf', weight: 2.4, dashArray: '3 5' },
              atv_road: { color: '#f59e0b', weight: 2.8, dashArray: '8 4' },
              driving_road: { color: '#e2e8f0', weight: 3.2 },
              deer_trail: { color: '#fbbf24', weight: 2.4, dashArray: '2 5' },
            };
            rts.forEach((rt) => {
              const st = styleByKind[rt.kind] || { color: '#84cc16', weight: 2.5 };
              const isStream = ['stream', 'creek', 'current_seam', 'drift_line'].includes(rt.kind);
              const targetLyr = isStream ? streamLayer : roadLayer;
              const center = annotationCenter(rt.geom);
              if (rt.geom && rt.geom.type === 'LineString' && rt.geom.coordinates) {
                const gj = window.L.geoJSON(rt.geom, { style: { ...st, opacity: 0.94 } });
                gj.bindTooltip(`${rt.name} (${(rt.kind || '').replace(/_/g, ' ')} · ${Math.round(rt.length_yd || 0)} yd)`, { sticky: true });
                gj.bindPopup(el('div', {}, [
                  el('strong', { text: rt.name }),
                  el('div', { text: `${(rt.kind || '').replace(/_/g, ' ')} · ${Math.round(rt.length_yd || 0)} yd${rt.flow_speed ? ' · flow: ' + rt.flow_speed : ''}` }),
                  ...(rt.notes ? [el('div', { text: rt.notes, attrs: { style: 'margin-top:4px;max-width:220px' } })] : []),
                ]));
                gj.addTo(targetLyr);
                if (isStream || rt.direction_mode === 'downstream' || rt.direction_mode === 'one_way') {
                  drawFlowArrowheads(targetLyr, rt.geom.coordinates.map((c) => [c[1], c[0]]), st.color);
                }
              }
              routeList.appendChild(el('div', {
                className: 'hb-item clickable',
                on: { click: () => center && map.setView(center, 16) },
              }, [
                el('div', { className: 'hb-item-main' }, [
                  el('span', { className: 'hb-dot', attrs: { style: `background:${st.color}` } }),
                  el('div', {}, [
                    el('div', { className: 'hb-item-title', text: rt.name || 'Path' }),
                    el('div', { className: 'hb-item-sub', text: `${(rt.kind || '').replace(/_/g, ' ')} · ${Math.round(rt.length_yd || 0)} yd${rt.flow_speed ? ' · ' + rt.flow_speed : ''}` }),
                  ]),
                ]),
                el('div', { className: 'hb-row nowrap' }, [
                  el('button', {
                    className: 'hb-btn ghost sm',
                    text: '⇄',
                    attrs: { title: 'Flip stream flow / trail direction' },
                    on: {
                      click: async (ev) => {
                        ev.stopPropagation();
                        await ctx.action('reverse_route', { id: rt.id });
                        await loadState(state.active ? state.active.property.id : null);
                      },
                    },
                  }),
                  el('button', {
                    className: 'hb-btn subtle-danger sm',
                    text: '×',
                    attrs: { title: 'Delete traced stream/path' },
                    on: {
                      click: async (ev) => {
                        ev.stopPropagation();
                        await ctx.action('delete_route', { id: rt.id });
                        await loadState(state.active ? state.active.property.id : null);
                      },
                    },
                  }),
                ]),
              ]));
            });
          }
          syncLayerVisibility();
        }

        async function doSyncHydroAndRoads(forceRefresh) {
          if (!map) return;
          const b = map.getBounds();
          syncHydroBtn.disabled = true;
          syncHydroBtn.textContent = 'Syncing…';
          setStatus('Syncing public streams (USGS NHD / Global OSM), forest trails/roads & fisheries…');
          try {
            const res = await ctx.action('sync_hydro_roads', {
              property_id: state.active ? state.active.property.id : null,
              min_lon: b.getWest(),
              min_lat: b.getSouth(),
              max_lon: b.getEast(),
              max_lat: b.getNorth(),
              include_streams: true,
              include_roads: true,
              include_fisheries: true,
              force_refresh: !!forceRefresh,
            });
            if (res.status === 'ok') {
              state.publicStreams = res.streams || [];
              state.publicRoads = res.roads || [];
              state.publicFisheries = res.fisheries || [];
              renderRoutesAndHydro();
              const offNote = (res.offline_fallbacks && res.offline_fallbacks.length) ? ' (offline cache fallback)' : '';
              setStatus(`✓ Synced ${state.publicStreams.length} stream(s), ${state.publicRoads.length} trail/road(s), ${state.publicFisheries.length} fishery feature(s) via ${(res.providers_used || []).join(', ') || 'cache'}${offNote}.`);
            } else {
              setStatus(`Hydro/roads sync error: ${res.message || 'failed'}`);
            }
          } finally {
            syncHydroBtn.disabled = false;
            syncHydroBtn.textContent = 'Sync Streams & Roads';
          }
        }

        async function loadProvidersDrawer() {
          const res = await ctx.action('providers_list', {});
          if (res.status !== 'ok') return;
          const oc = res.offline_cache || {};
          offlineStatsTxt.textContent = `Offline Cache: ${oc.disk_mb || 0} MB · ${oc.cached_tiles || 0} map tiles · ${oc.cached_gis_cells || 0} GIS cells. Custom sources persist across upgrades.`;
          providersList.replaceChildren();
          (res.providers || []).forEach((p) => {
            const st = p.health_status || 'untested';
            const latTxt = p.latency_ms ? `${p.latency_ms}ms` : '';
            const rightNodes = [
              el('span', { className: `hb-prov-badge ${st}`, text: latTxt ? `${st} ${latTxt}` : st }),
            ];
            if (p.origin === 'user_custom' || p.origin === 'user_override') {
              rightNodes.push(el('button', {
                className: 'hb-btn subtle-danger sm',
                text: '×',
                attrs: { title: 'Remove user override' },
                on: {
                  click: async () => {
                    await ctx.action('provider_delete', { provider_id: p.id });
                    await loadProvidersDrawer();
                  },
                },
              }));
            }
            providersList.appendChild(el('div', { className: 'hb-prov-row' }, [
              el('div', {}, [
                el('div', { text: `${p.name || p.id}` }),
                el('div', { className: 'hb-wx-sub', text: `${p.layer} · ${p.region || 'GLOBAL'} · ${p.driver}${p.last_error ? ' · ' + p.last_error.slice(0, 50) : ''}` }),
              ]),
              el('div', { className: 'hb-row nowrap' }, rightNodes),
            ]));
          });
        }

        async function doCheckProvidersHealth() {
          checkHealthBtn.disabled = true;
          checkHealthBtn.textContent = 'Testing…';
          setStatus('Running spatial provider health & schema-drift check…');
          try {
            const res = await ctx.action('providers_health', {});
            await loadProvidersDrawer();
            if (res.status === 'ok') {
              setStatus(`✓ Provider watchdog complete: ${res.healthy}/${res.total} healthy.`);
            }
          } finally {
            checkHealthBtn.disabled = false;
            checkHealthBtn.textContent = 'Test Providers';
          }
        }

        async function doSaveCustomProvider() {
          const id = customProvId.value.trim();
          const url = customProvUrl.value.trim();
          if (!id || !url) { setStatus('Enter both a Provider ID and Endpoint URL.'); return; }
          const res = await ctx.action('provider_save', {
            provider: {
              id,
              name: id.replace(/_/g, ' '),
              layer: customProvLayer.value,
              driver: customProvDriver.value,
              region: 'CUSTOM',
              bbox: [-180, -85, 180, 85],
              priority: 5,
              enabled: true,
              ttl_days: 30,
              url,
            },
          });
          if (res.status === 'ok') {
            customProvId.value = '';
            customProvUrl.value = '';
            customProvForm.style.display = 'none';
            setStatus(`✓ Saved custom provider "${id}" to persistent providers.user.json.`);
            await loadProvidersDrawer();
          } else {
            setStatus(`Provider error: ${res.message || 'failed'}`);
          }
        }

        async function doDownloadOfflinePack() {
          if (!state.active) { setStatus('Select a camp first before downloading an Offline Camp Pack.'); return; }
          offlinePackBtn.disabled = true;
          offlinePackBtn.textContent = 'Packing…';
          setStatus('Downloading Offline Camp Pack (satellite tiles, LiDAR DEM, streams, roads, parcels & public lands)…');
          try {
            const res = await ctx.action('offline_pack', { property_id: state.active.property.id });
            if (res.status === 'ok') {
              const c = res.cached || {};
              const oc = res.offline_cache || {};
              setStatus(`✓ Offline Pack Ready (${oc.disk_mb || 0} MB): ${c.tiles_warmed || 0} tiles · ${c.streams || 0} streams · ${c.roads || 0} roads · ${c.parcels || 0} parcels.`);
              await loadProvidersDrawer();
              await loadState(state.active.property.id);
            } else {
              setStatus(`Offline pack error: ${res.message || 'failed'}`);
            }
          } finally {
            offlinePackBtn.disabled = false;
            offlinePackBtn.textContent = 'Offline Pack';
          }
        }

        async function ensureChartDataUrl(wb) {
          if (!wb || !wb.id) return null;
          if (state.chartDataUrlCache[wb.id]) return state.chartDataUrlCache[wb.id];
          const res = await ctx.action('get_waterbody_map', { id: wb.id });
          if (res && res.status === 'ok' && res.image_data_url) {
            state.chartDataUrlCache[wb.id] = res.image_data_url;
            return res.image_data_url;
          }
          return null;
        }

        function defaultChartBoundsForWb(wb) {
          if (wb && Array.isArray(wb.overlay_bounds) && wb.overlay_bounds.length === 2) {
            return [[Number(wb.overlay_bounds[0][0]), Number(wb.overlay_bounds[0][1])], [Number(wb.overlay_bounds[1][0]), Number(wb.overlay_bounds[1][1])]];
          }
          if (wb && wb.geom) {
            try {
              const b = window.L.geoJSON(wb.geom).getBounds();
              if (b && b.isValid()) {
                const padLat = Math.max(0.001, (b.getNorth() - b.getSouth()) * 0.14);
                const padLon = Math.max(0.0012, (b.getEast() - b.getWest()) * 0.14);
                return [[b.getSouth() - padLat, b.getWest() - padLon], [b.getNorth() + padLat, b.getEast() + padLon]];
              }
            } catch (_) {}
          }
          if (map) {
            const mb = map.getBounds();
            return [[mb.getSouth(), mb.getWest()], [mb.getNorth(), mb.getEast()]];
          }
          return [[45.28, -88.21], [45.31, -88.17]];
        }

        async function ensureChartImage(wb) {
          const url = await ensureChartDataUrl(wb);
          if (!url) return null;
          if (state.chartImgCache[wb.id]) return state.chartImgCache[wb.id];
          return await new Promise((resolve) => {
            const img = new Image();
            img.onload = () => {
              state.chartImgCache[wb.id] = img;
              resolve(img);
            };
            img.onerror = () => resolve(null);
            img.src = url;
          });
        }

        function normalizeCropBox(crop) {
          if (!crop || typeof crop !== 'object') return { left: 0, top: 0, right: 1, bottom: 1 };
          const l = Math.max(0, Math.min(0.95, Number(crop.left || 0)));
          const t = Math.max(0, Math.min(0.95, Number(crop.top || 0)));
          const r = Math.max(l + 0.05, Math.min(1, Number(crop.right != null ? crop.right : 1)));
          const b = Math.max(t + 0.05, Math.min(1, Number(crop.bottom != null ? crop.bottom : 1)));
          return {
            left: Number(l.toFixed(4)),
            top: Number(t.toFixed(4)),
            right: Number(r.toFixed(4)),
            bottom: Number(b.toFixed(4)),
          };
        }

        function isCroppedBox(crop) {
          const c = normalizeCropBox(crop);
          return c.left > 0.005 || c.top > 0.005 || c.right < 0.995 || c.bottom < 0.995;
        }

        function renderCroppedPreviewDataUrl(img, crop) {
          if (!img || !img.naturalWidth || !img.naturalHeight) return null;
          const c = normalizeCropBox(crop);
          const sx = Math.round(c.left * img.naturalWidth);
          const sy = Math.round(c.top * img.naturalHeight);
          const sw = Math.max(16, Math.round((c.right - c.left) * img.naturalWidth));
          const sh = Math.max(16, Math.round((c.bottom - c.top) * img.naturalHeight));
          const scale = Math.min(1, 1400 / Math.max(sw, sh));
          const cw = Math.max(32, Math.round(sw * scale));
          const ch = Math.max(32, Math.round(sh * scale));
          const canvas = document.createElement('canvas');
          canvas.width = cw;
          canvas.height = ch;
          const c2d = canvas.getContext('2d');
          c2d.drawImage(img, sx, sy, sw, sh, 0, 0, cw, ch);
          return canvas.toDataURL('image/png');
        }

        function renderTransformedChartDataUrl(img, rotationDeg, flipH, flipV, blendMode, crop) {
          if (!img || !img.naturalWidth || !img.naturalHeight) return null;
          const c = normalizeCropBox(crop);
          const sx = Math.round(c.left * img.naturalWidth);
          const sy = Math.round(c.top * img.naturalHeight);
          const sw = Math.max(16, Math.round((c.right - c.left) * img.naturalWidth));
          const sh = Math.max(16, Math.round((c.bottom - c.top) * img.naturalHeight));
          const maxDim = 1500;
          const scale = Math.min(1, maxDim / Math.max(sw, sh));
          const w = Math.max(32, Math.round(sw * scale));
          const h = Math.max(32, Math.round(sh * scale));
          const rad = ((Number(rotationDeg) || 0) * Math.PI) / 180;
          const cosA = Math.abs(Math.cos(rad));
          const sinA = Math.abs(Math.sin(rad));
          const rotW = Math.max(32, Math.round(w * cosA + h * sinA));
          const rotH = Math.max(32, Math.round(w * sinA + h * cosA));

          const canvas = document.createElement('canvas');
          canvas.width = rotW;
          canvas.height = rotH;
          const c2d = canvas.getContext('2d', { willReadFrequently: true });
          c2d.clearRect(0, 0, rotW, rotH);
          c2d.save();
          c2d.translate(rotW / 2, rotH / 2);
          c2d.rotate(rad);
          c2d.scale(flipH ? -1 : 1, flipV ? -1 : 1);
          c2d.drawImage(img, sx, sy, sw, sh, -w / 2, -h / 2, w, h);
          c2d.restore();

          if (blendMode && blendMode !== 'paper') {
            const imgData = c2d.getImageData(0, 0, rotW, rotH);
            const d = imgData.data;
            const tint = blendMode === 'gold_transparent'
              ? [251, 191, 36]
              : (blendMode === 'ink_transparent' ? [15, 23, 42] : [56, 224, 255]);
            for (let i = 0; i < d.length; i += 4) {
              if (d[i + 3] === 0) continue;
              const lum = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
              if (lum > 205) {
                d[i + 3] = 0;
              } else {
                const inkStrength = Math.min(255, Math.round((215 - lum) * 1.65));
                d[i] = tint[0];
                d[i + 1] = tint[1];
                d[i + 2] = tint[2];
                d[i + 3] = inkStrength;
              }
            }
            c2d.putImageData(imgData, 0, 0);
          }
          return canvas.toDataURL('image/png');
        }

        function renderSavedChartOverlays() {
          if (!map || !chartOverlayLayer) return;
          chartOverlayLayer.clearLayers();
          (state.waterbodies || []).forEach((wb) => {
            if (state.aligningWb && state.aligningWb.id === wb.id) return;
            if (!wb.has_cached_map || !wb.overlay_visible || !wb.overlay_bounds || wb.overlay_bounds.length !== 2) return;
            const xf = wb.overlay_transform || {};
            const rot = Number(xf.rotation_deg || 0);
            const fh = !!xf.flip_h;
            const fv = !!xf.flip_v;
            const bm = xf.blend_mode || 'cyan_transparent';
            const crop = normalizeCropBox(xf.crop);
            const op = wb.overlay_opacity != null ? wb.overlay_opacity : 0.80;

            const addOverlay = (img) => {
              if (!img || !map || !chartOverlayLayer) return;
              if (state.aligningWb && state.aligningWb.id === wb.id) return;
              const txUrl = renderTransformedChartDataUrl(img, rot, fh, fv, bm, crop);
              if (txUrl) {
                window.L.imageOverlay(txUrl, wb.overlay_bounds, { opacity: op, interactive: false }).addTo(chartOverlayLayer);
              }
            };
            if (state.chartImgCache[wb.id]) addOverlay(state.chartImgCache[wb.id]);
            else ensureChartImage(wb).then(addOverlay);
          });
        }

        let _draftCrop = { left: 0, top: 0, right: 1, bottom: 1 };

        function stopCropPanel() {
          state.cropPanelActive = false;
          cropPanel.style.display = 'none';
          alignCropBtn.classList.toggle('active', isCroppedBox(state.alignCrop));
        }

        function startCropPanel() {
          if (!state.aligningWb) return;
          const rawUrl = state.chartDataUrlCache[state.aligningWb.id];
          if (!rawUrl) return;
          stopTwoPointSnap();
          state.cropPanelActive = true;
          _draftCrop = normalizeCropBox(state.alignCrop);
          cropPanel.style.display = 'flex';
          alignCropBtn.classList.add('active');

          const cropImg = el('img', { className: 'hb-crop-img', attrs: { src: rawUrl, alt: 'Drag box to crop chart' } });
          const cropRect = el('div', { className: 'hb-crop-rect' });
          const paintRect = () => {
            cropRect.style.left = `${(_draftCrop.left * 100).toFixed(2)}%`;
            cropRect.style.top = `${(_draftCrop.top * 100).toFixed(2)}%`;
            cropRect.style.width = `${((_draftCrop.right - _draftCrop.left) * 100).toFixed(2)}%`;
            cropRect.style.height = `${((_draftCrop.bottom - _draftCrop.top) * 100).toFixed(2)}%`;
            const wPct = Math.round((_draftCrop.right - _draftCrop.left) * 100);
            const hPct = Math.round((_draftCrop.bottom - _draftCrop.top) * 100);
            cropPanelText.textContent = `Crop selection: ${wPct}% × ${hPct}% of sheet — drag a box around the lake to strip legends/graphs, then click "✓ Apply Crop".`;
          };
          paintRect();

          let dragging = false;
          let p0 = null;
          const relPt = (ev) => {
            const r = cropStageWrap.getBoundingClientRect();
            return {
              x: Math.max(0, Math.min(1, (ev.clientX - r.left) / Math.max(1, r.width))),
              y: Math.max(0, Math.min(1, (ev.clientY - r.top) / Math.max(1, r.height))),
            };
          };
          cropStageWrap.onpointerdown = (ev) => {
            ev.preventDefault();
            dragging = true;
            p0 = relPt(ev);
            try { cropStageWrap.setPointerCapture(ev.pointerId); } catch (_) {}
          };
          cropStageWrap.onpointermove = (ev) => {
            if (!dragging || !p0) return;
            const p1 = relPt(ev);
            const l = Math.min(p0.x, p1.x);
            const t = Math.min(p0.y, p1.y);
            const r = Math.max(p0.x, p1.x);
            const b = Math.max(p0.y, p1.y);
            if (r - l >= 0.05 && b - t >= 0.05) {
              _draftCrop = normalizeCropBox({ left: l, top: t, right: r, bottom: b });
              paintRect();
            }
          };
          cropStageWrap.onpointerup = (ev) => {
            dragging = false;
            try { cropStageWrap.releasePointerCapture(ev.pointerId); } catch (_) {}
          };

          cropStageWrap.replaceChildren(cropImg, cropRect);
        }

        function stopTwoPointSnap() {
          state.twoPointSnapActive = false;
          state.snapStep = 0;
          state.snapPts = { chartA: null, mapA: null, chartB: null, mapB: null };
          snapPanel.style.display = 'none';
          alignSnapBtn.classList.remove('active');
          syncMapInteractionMode();
        }

        function solveTwoPointSnap() {
          const img = state.aligningWb && state.chartImgCache[state.aligningWb.id];
          const { chartA, mapA, chartB, mapB } = state.snapPts;
          if (!img || !chartA || !mapA || !chartB || !mapB) return;

          const crop = normalizeCropBox(state.alignCrop);
          const W = img.naturalWidth * (crop.right - crop.left);
          const H = img.naturalHeight * (crop.bottom - crop.top);
          let dxC = (chartB.nx - chartA.nx) * W;
          let dyC = (chartB.ny - chartA.ny) * H; // screen y-down
          if (state.alignFlipH) dxC = -dxC;
          if (state.alignFlipV) dyC = -dyC;
          const chartDist = Math.hypot(dxC, dyC);
          if (chartDist < 5) {
            setStatus('Pick two points farther apart on the chart.');
            return;
          }
          // Convert chart vector to y-up angle
          const chartAngleUp = Math.atan2(-dyC, dxC);

          const latMid = (mapA.lat + mapB.lat) * 0.5;
          const cosLat = Math.max(0.2, Math.cos((latMid * Math.PI) / 180));
          const mapDxM = (mapB.lng - mapA.lng) * 111320 * cosLat;
          const mapDyM = (mapB.lat - mapA.lat) * 111320;
          const mapDistM = Math.hypot(mapDxM, mapDyM);
          if (mapDistM < 10) {
            setStatus('Pick two points farther apart on the satellite map.');
            return;
          }
          const mapAngleUp = Math.atan2(mapDyM, mapDxM);

          // Clockwise rotation theta (rad) rotates y-up vector by -theta -> mapAngleUp = chartAngleUp - theta
          let thetaDeg = ((chartAngleUp - mapAngleUp) * 180) / Math.PI;
          thetaDeg = ((((thetaDeg + 180) % 360) + 360) % 360) - 180;
          const rad = (thetaDeg * Math.PI) / 180;
          const c = Math.cos(rad);
          const s = Math.sin(rad);

          const mpp = mapDistM / chartDist; // meters per original chart pixel
          const rotW = Math.abs(W * c) + Math.abs(H * s);
          const rotH = Math.abs(W * s) + Math.abs(H * c);

          // Where does Landmark A sit relative to the rotated chart center?
          let ax0 = (chartA.nx - 0.5) * W;
          let ay0 = (chartA.ny - 0.5) * H;
          if (state.alignFlipH) ax0 = -ax0;
          if (state.alignFlipV) ay0 = -ay0;
          const rxA = ax0 * c - ay0 * s; // +east in rotated pixels
          const ryA = ax0 * s + ay0 * c; // +south in rotated pixels

          const centerLon = mapA.lng - (rxA * mpp) / (111320 * cosLat);
          const centerLat = mapA.lat + (ryA * mpp) / 111320;
          const halfLon = ((rotW * 0.5) * mpp) / (111320 * cosLat);
          const halfLat = ((rotH * 0.5) * mpp) / 111320;

          state.alignRotation = Math.round(thetaDeg * 10) / 10;
          state.alignBounds = [
            [Number((centerLat - halfLat).toFixed(6)), Number((centerLon - halfLon).toFixed(6))],
            [Number((centerLat + halfLat).toFixed(6)), Number((centerLon + halfLon).toFixed(6))],
          ];
          stopTwoPointSnap();
          syncAlignControlLabels();
          if (state._rebuildAlignWidgets) state._rebuildAlignWidgets();
          try { map.fitBounds(state.alignBounds, { padding: [24, 24], maxZoom: 15 }); } catch (_) {}
          setStatus(`✓ 2-Point Shoreline Snap locked (${state.alignRotation > 0 ? '+' : ''}${state.alignRotation}° rotation). Fine-tune if needed, then click "✓ Save Alignment".`);
        }

        function handleTwoPointMapClick(latlng) {
          if (state.snapStep === 1) {
            state.snapPts.mapA = latlng;
            state.snapStep = 2;
            snapPanelText.textContent = 'Step 3/4: Click Landmark B (opposite end of lake, e.g. north bay or bridge) on the DNR chart below.';
            syncMapInteractionMode();
            setStatus('✓ Landmark A locked on map. Now click Landmark B on the chart preview.');
          } else if (state.snapStep === 3) {
            state.snapPts.mapB = latlng;
            solveTwoPointSnap();
          }
        }

        function startTwoPointSnap() {
          if (!state.aligningWb) return;
          const img = state.chartImgCache[state.aligningWb.id];
          const previewUrl = (img && isCroppedBox(state.alignCrop))
            ? renderCroppedPreviewDataUrl(img, state.alignCrop)
            : state.chartDataUrlCache[state.aligningWb.id];
          if (!previewUrl) return;
          stopCropPanel();
          state.twoPointSnapActive = true;
          state.snapStep = 0;
          state.snapPts = { chartA: null, mapA: null, chartB: null, mapB: null };
          alignSnapBtn.classList.add('active');
          snapPanel.style.display = 'flex';
          snapPanelText.textContent = 'Step 1/4: Click Landmark A (e.g. Dam or sharp peninsula) on the DNR chart below.';

          const pinA = el('div', { className: 'hb-snap-pin a', text: 'A', attrs: { style: 'display:none' } });
          const pinB = el('div', { className: 'hb-snap-pin b', text: 'B', attrs: { style: 'display:none' } });
          const snapImg = el('img', { className: 'hb-snap-img', attrs: { src: previewUrl, alt: 'Click 2 shoreline landmarks' } });
          snapImg.addEventListener('click', (ev) => {
            if (state.snapStep !== 0 && state.snapStep !== 2) return;
            const rect = snapImg.getBoundingClientRect();
            const nx = Math.max(0, Math.min(1, (ev.clientX - rect.left) / Math.max(1, rect.width)));
            const ny = Math.max(0, Math.min(1, (ev.clientY - rect.top) / Math.max(1, rect.height)));
            if (state.snapStep === 0) {
              state.snapPts.chartA = { nx, ny };
              pinA.style.display = 'flex';
              pinA.style.left = `${(nx * 100).toFixed(2)}%`;
              pinA.style.top = `${(ny * 100).toFixed(2)}%`;
              state.snapStep = 1;
              snapPanelText.textContent = 'Step 2/4: Now click the EXACT SAME Landmark A on the satellite map shoreline below!';
              syncMapInteractionMode();
              setStatus('Click Landmark A on the satellite map shoreline.');
            } else if (state.snapStep === 2) {
              state.snapPts.chartB = { nx, ny };
              pinB.style.display = 'flex';
              pinB.style.left = `${(nx * 100).toFixed(2)}%`;
              pinB.style.top = `${(ny * 100).toFixed(2)}%`;
              state.snapStep = 3;
              snapPanelText.textContent = 'Step 4/4: Now click the EXACT SAME Landmark B on the satellite map shoreline below!';
              syncMapInteractionMode();
              setStatus('Click Landmark B on the satellite map shoreline.');
            }
          });
          snapPanelImgWrap.replaceChildren(snapImg, pinA, pinB);
          syncMapInteractionMode();
        }

        function syncAlignControlLabels() {
          alignRotIn.value = String(Math.round(state.alignRotation || 0));
          alignRotLbl.textContent = `${state.alignRotation > 0 ? '+' : ''}${Math.round(state.alignRotation || 0)}°`;
          alignOpacityIn.value = String(Math.round((state.alignOpacity != null ? state.alignOpacity : 0.8) * 100));
          alignOpacityLbl.textContent = `${Math.round((state.alignOpacity != null ? state.alignOpacity : 0.8) * 100)}%`;
          alignFlipHBtn.classList.toggle('active', !!state.alignFlipH);
          alignFlipVBtn.classList.toggle('active', !!state.alignFlipV);
          alignBlendSel.value = state.alignBlendMode || 'cyan_transparent';
          const cropped = isCroppedBox(state.alignCrop);
          alignCropBtn.textContent = cropped ? '✂ Cropped' : '✂ Crop Chart';
          alignCropBtn.classList.toggle('active', cropped || state.cropPanelActive);
        }

        function stopChartAlignMode() {
          stopTwoPointSnap();
          stopCropPanel();
          state.aligningWb = null;
          state.alignBounds = null;
          state._rebuildAlignWidgets = null;
          state._activeAlignOverlay = null;
          alignBar.style.display = 'none';
          if (alignOverlayLayer) alignOverlayLayer.clearLayers();
          renderSavedChartOverlays();
        }

        async function startChartAlignMode(wb) {
          if (!map || !alignOverlayLayer) return;
          clearAllPlacementModes();
          stopTwoPointSnap();
          stopCropPanel();
          setStatus(`Loading cached contour chart for ${wb.name}…`);
          const img = await ensureChartImage(wb);
          if (!img) {
            setStatus(`No cached DNR contour PNG available for ${wb.name}.`);
            return;
          }
          const xf = wb.overlay_transform || {};
          state.aligningWb = wb;
          state.alignBounds = defaultChartBoundsForWb(wb);
          state.alignOpacity = wb.overlay_opacity != null ? wb.overlay_opacity : 0.80;
          state.alignRotation = Number(xf.rotation_deg || 0);
          state.alignFlipH = !!xf.flip_h;
          state.alignFlipV = !!xf.flip_v;
          state.alignBlendMode = xf.blend_mode || 'cyan_transparent';
          state.alignCrop = normalizeCropBox(xf.crop);
          syncAlignControlLabels();
          alignBarText.textContent = `Pin Chart (${wb.name}): Crop noise, Rotate/Flip/Alpha, Auto-Match, or 2-Point Snap to mesh shorelines.`;
          alignBar.style.display = 'flex';
          state.layers.chartOverlays = true;
          renderLayersPopover();
          syncLayerVisibility();
          renderSavedChartOverlays();

          const rebuildAlignWidgets = () => {
            alignOverlayLayer.clearLayers();
            const txUrl = renderTransformedChartDataUrl(img, state.alignRotation, state.alignFlipH, state.alignFlipV, state.alignBlendMode, state.alignCrop);
            const sw = state.alignBounds[0];
            const ne = state.alignBounds[1];
            const imgOv = window.L.imageOverlay(txUrl, [sw, ne], {
              opacity: state.alignOpacity,
              interactive: false,
            }).addTo(alignOverlayLayer);
            const rect = window.L.rectangle([sw, ne], {
              color: '#38bdf8',
              weight: 1.5,
              dashArray: '6 4',
              fill: false,
              interactive: false,
            }).addTo(alignOverlayLayer);

            const makeIcon = (cls) => window.L.divIcon({
              className: `hb-align-handle ${cls}`,
              iconSize: cls === 'center' ? [18, 18] : [16, 16],
              iconAnchor: cls === 'center' ? [9, 9] : [8, 8],
            });
            const swMk = window.L.marker(sw, { draggable: true, icon: makeIcon('sw'), title: 'Drag SW corner to resize' }).addTo(alignOverlayLayer);
            const neMk = window.L.marker(ne, { draggable: true, icon: makeIcon('ne'), title: 'Drag NE corner to resize' }).addTo(alignOverlayLayer);
            const cLat = (sw[0] + ne[0]) * 0.5;
            const cLon = (sw[1] + ne[1]) * 0.5;
            const cMk = window.L.marker([cLat, cLon], { draggable: true, icon: makeIcon('center'), title: 'Drag center to move chart' }).addTo(alignOverlayLayer);

            const syncGeom = (nextSw, nextNe) => {
              const s = Math.min(nextSw[0], nextNe[0]);
              const n = Math.max(nextSw[0], nextNe[0]);
              const w = Math.min(nextSw[1], nextNe[1]);
              const e = Math.max(nextSw[1], nextNe[1]);
              state.alignBounds = [[Number(s.toFixed(6)), Number(w.toFixed(6))], [Number(n.toFixed(6)), Number(e.toFixed(6))]];
              imgOv.setBounds(state.alignBounds);
              rect.setBounds(state.alignBounds);
              cMk.setLatLng([(s + n) * 0.5, (w + e) * 0.5]);
            };
            swMk.on('drag', () => {
              const p1 = swMk.getLatLng();
              const p2 = neMk.getLatLng();
              syncGeom([p1.lat, p1.lng], [p2.lat, p2.lng]);
            });
            neMk.on('drag', () => {
              const p1 = swMk.getLatLng();
              const p2 = neMk.getLatLng();
              syncGeom([p1.lat, p1.lng], [p2.lat, p2.lng]);
            });
            cMk.on('drag', () => {
              const cp = cMk.getLatLng();
              const hLat = (state.alignBounds[1][0] - state.alignBounds[0][0]) * 0.5;
              const hLon = (state.alignBounds[1][1] - state.alignBounds[0][1]) * 0.5;
              const nSw = [Number((cp.lat - hLat).toFixed(6)), Number((cp.lng - hLon).toFixed(6))];
              const nNe = [Number((cp.lat + hLat).toFixed(6)), Number((cp.lng + hLon).toFixed(6))];
              state.alignBounds = [nSw, nNe];
              imgOv.setBounds(state.alignBounds);
              rect.setBounds(state.alignBounds);
              swMk.setLatLng(nSw);
              neMk.setLatLng(nNe);
            });
            state._activeAlignOverlay = imgOv;
          };

          state._rebuildAlignWidgets = rebuildAlignWidgets;
          rebuildAlignWidgets();
          try { map.fitBounds(state.alignBounds, { padding: [30, 30], maxZoom: 15 }); } catch (_) {}
          setStatus(`Aligning "${wb.name}" — tip: click "↻ +90°" for sideways WI DNR charts or "🎯 2-Point Snap" to lock shorelines mathematically.`);
        }

        async function doAutoAlignChart() {
          if (!state.aligningWb) return;
          alignAutoBtn.disabled = true;
          alignAutoBtn.textContent = 'Matching…';
          setStatus(`Running FFT shoreline matcher on ${state.aligningWb.name} across cardinal rotations…`);
          try {
            const res = await ctx.action('auto_align_chart', { id: state.aligningWb.id, crop: state.alignCrop });
            if (res.status === 'ok' && res.bounds) {
              state.alignRotation = Number(res.rotation_deg || 0);
              state.alignBounds = res.bounds;
              syncAlignControlLabels();
              if (state._rebuildAlignWidgets) state._rebuildAlignWidgets();
              try { map.fitBounds(state.alignBounds, { padding: [24, 24], maxZoom: 15 }); } catch (_) {}
              setStatus(`✓ ${res.message}`);
            } else {
              setStatus(res.message || 'Auto-match could not align this chart — try "🎯 2-Point Snap".');
            }
          } finally {
            alignAutoBtn.disabled = false;
            alignAutoBtn.textContent = '✨ Auto-Match';
          }
        }

        async function doUnpinWaterbodyOverlay(wb) {
          if (!wb || !wb.id) return;
          await ctx.action('unpin_waterbody_overlay', { id: wb.id });
          if (state.aligningWb && state.aligningWb.id === wb.id) stopChartAlignMode();
          await loadState(state.active ? state.active.property.id : null);
          setStatus(`✓ Unpinned chart overlay from "${wb.name}".`);
        }

        async function doClearBathymetry() {
          if (!state.active) { setStatus('Select a camp first.'); return; }
          const targetName = bathyWbSel.value || null;
          const lbl = targetName || state.active.property.name;
          if (!window.confirm(`Remove saved/estimated depth contours and soundings for "${lbl}"?`)) return;
          const res = await ctx.action('clear_bathymetry', {
            property_id: state.active.property.id,
            waterbody_name: targetName,
            clear_points: true,
          });
          if (res.status === 'ok') {
            setStatus(`✓ Cleared ${res.grids_removed || 0} contour grid(s) and ${res.points_removed || 0} depth sounding(s) for ${lbl}.`);
            await loadState(state.active.property.id);
          } else {
            setStatus(`Clear bathymetry error: ${res.message || 'failed'}`);
          }
        }

        async function saveChartAlignment(andExtract) {
          if (!state.aligningWb || !state.alignBounds) return;
          const wb = state.aligningWb;
          const res = await ctx.action('save_waterbody_overlay', {
            id: wb.id,
            bounds: state.alignBounds,
            opacity: state.alignOpacity,
            visible: true,
            rotation_deg: state.alignRotation,
            flip_h: state.alignFlipH,
            flip_v: state.alignFlipV,
            blend_mode: state.alignBlendMode,
            crop: state.alignCrop,
          });
          if (res.status !== 'ok') {
            setStatus(`Failed to save chart alignment: ${res.message || 'error'}`);
            return;
          }
          stopChartAlignMode();
          await loadState(state.active ? state.active.property.id : null);
          setStatus(`✓ Saved georeferenced chart overlay for ${wb.name} (${state.alignRotation}° rot, ${Math.round(state.alignOpacity * 100)}% alpha).`);
          if (andExtract) {
            const freshWb = (state.waterbodies || []).find((w) => w.id === wb.id) || wb;
            const pngPath = freshWb.contour_map_png_path;
            if (pngPath) {
              const prevMaxPlanId = (state.plans || []).reduce((mx, p) => Math.max(mx, Number(p.id || 0)), 0);
              setStatus(`Launching ${getAgentName()} North-Up Tiled Vision to extract GPS depth contours & fish structures from ${wb.name}…`);
              await ctx.launchTask('lake_contour_analysis', {
                params: {
                  property_id: state.active ? state.active.property.id : wb.property_id,
                  camp: wb.name,
                  waterbody_id: wb.id,
                  image_path: pngPath,
                },
              }, {
                streamContainer: georgeTaskHost,
                onComplete: async (rd) => {
                  await reconcileCompletedTaskPlan(rd, 'lake_contour_analysis', prevMaxPlanId);
                  refreshTaskHistory();
                },
              });
            }
          }
        }

        async function doEstimateBathymetry() {
          if (!state.active) { setStatus('Select a camp first.'); return; }
          const targetName = bathyWbSel.value || null;
          const matchedWb = (state.waterbodies || []).find((w) => !targetName || w.name === targetName);
          if (!matchedWb) {
            setStatus('Sync Lakes or Draw a Pond/Lake first before generating an estimated depth model.');
            return;
          }
          estimateBathyBtn.disabled = true;
          estimateBathyBtn.textContent = 'Estimating…';
          try {
            const res = await ctx.action('estimate_bathymetry', {
              property_id: state.active.property.id,
              waterbody_id: matchedWb.id,
              waterbody_name: matchedWb.name,
            });
            if (res.status === 'ok') {
              setStatus(`✓ Generated estimated bathymetry for ${res.waterbody_name} (${res.contour_count} contours, max ${res.max_depth_ft} ft). Refine anytime with real soundings or chart extraction.`);
              await loadState(state.active.property.id);
            } else {
              setStatus(res.message || 'Could not estimate bathymetry.');
            }
          } finally {
            estimateBathyBtn.disabled = false;
            estimateBathyBtn.textContent = '~ Estimate Depth';
          }
        }

        function renderWaterbodies() {
          if (!map || !waterbodyLayer) return;
          waterbodyLayer.clearLayers();
          waterbodiesList.replaceChildren();

          const wbs = state.waterbodies || [];
          const prevSel = bathyWbSel.value;
          bathyWbSel.replaceChildren(el('option', { value: '', text: 'Target: Active Camp Water' }));
          wbs.forEach((wb) => {
            const opt = el('option', { value: wb.name, text: `${wb.name}${wb.max_depth_ft ? ' (Max ' + wb.max_depth_ft + ' ft)' : ''}` });
            if (prevSel && prevSel === wb.name) opt.selected = true;
            bathyWbSel.appendChild(opt);
          });
          if (!prevSel && wbs.length === 1) bathyWbSel.value = wbs[0].name;

          if (!wbs.length) {
            waterbodiesList.appendChild(el('div', { className: 'hb-muted', text: 'No lakes or ponds saved yet. Click "Sync Lakes" to pull WI DNR / USGS lake polygons & contour maps, or "+ Draw Pond/Lake" to map a private pond.' }));
            wbEditorBox.style.display = 'none';
            return;
          }

          wbs.forEach((wb) => {
            if (wb.geom) {
              const gj = window.L.geoJSON(wb.geom, {
                style: {
                  color: '#38bdf8',
                  weight: 2.2,
                  fillColor: '#0284c7',
                  fillOpacity: 0.18,
                },
              });
              const metaBits = [];
              if (wb.acres) metaBits.push(`${Number(wb.acres) >= 10 ? Math.round(wb.acres) : wb.acres} ac`);
              if (wb.max_depth_ft) metaBits.push(`Max ${wb.max_depth_ft} ft`);
              if (wb.mean_depth_ft) metaBits.push(`Avg ${wb.mean_depth_ft} ft`);
              if (wb.wbic) metaBits.push(`WBIC ${wb.wbic}`);
              gj.bindTooltip(`${wb.name}${metaBits.length ? ' · ' + metaBits.join(' · ') : ''}`, { sticky: true });
              const popNodes = [
                el('strong', { text: wb.name }),
                ...(metaBits.length ? [el('div', { text: metaBits.join(' · ') })] : []),
                ...(wb.bottom_substrate ? [el('div', { text: `Bottom: ${wb.bottom_substrate}` })] : []),
                ...(wb.species && wb.species.length ? [el('div', { text: `Species: ${wb.species.join(', ')}` })] : []),
                ...(wb.public_access ? [el('div', { text: wb.public_access })] : []),
                el('div', { className: 'hb-row', attrs: { style: 'margin-top:6px' } }, [
                  el('button', {
                    className: 'hb-btn sm',
                    text: 'Edit Dossier',
                    on: { click: () => openWaterbodyEditor(wb) },
                  }),
                  ...((wb.has_cached_map || wb.contour_map_url || wb.wbic) ? [el('button', {
                    className: 'hb-btn primary sm',
                    text: 'Contour Map',
                    on: { click: () => openWaterbodyContourMap(wb) },
                  })] : []),
                ]),
              ];
              gj.bindPopup(el('div', {}, popNodes));
              gj.addTo(waterbodyLayer);
            }

            const subBits = [];
            if (wb.acres) subBits.push(`${Number(wb.acres) >= 10 ? Math.round(wb.acres) : wb.acres} ac`);
            if (wb.max_depth_ft) subBits.push(`Max ${wb.max_depth_ft} ft`);
            if (wb.wbic) subBits.push(`WBIC ${wb.wbic}`);
            else subBits.push('Custom');

            const rowBtns = [
              el('button', {
                className: 'hb-btn ghost sm',
                text: 'Edit',
                on: {
                  click: (ev) => {
                    ev.stopPropagation();
                    openWaterbodyEditor(wb);
                  },
                },
              }),
            ];
            if (wb.has_cached_map || wb.contour_map_url || wb.wbic) {
              rowBtns.push(el('button', {
                className: 'hb-btn sm' + (wb.overlay_visible ? ' active' : ''),
                text: wb.overlay_bounds ? 'Align' : 'Pin Chart',
                attrs: { title: 'Overlay & align official DNR contour chart directly on the satellite map' },
                on: {
                  click: (ev) => {
                    ev.stopPropagation();
                    startChartAlignMode(wb);
                  },
                },
              }));
              rowBtns.push(el('button', {
                className: 'hb-btn sm',
                text: 'Map',
                attrs: { title: 'View cached WI DNR Contour (Bathymetric) Map' },
                on: {
                  click: (ev) => {
                    ev.stopPropagation();
                    openWaterbodyContourMap(wb);
                  },
                },
              }));
            }
            rowBtns.push(el('button', {
              className: 'hb-btn subtle-danger sm',
              text: '×',
              attrs: { title: 'Delete waterbody' },
              on: {
                click: async (ev) => {
                  ev.stopPropagation();
                  await ctx.action('delete_waterbody', { id: wb.id });
                  if (state.selectedWaterbodyId === wb.id) {
                    state.selectedWaterbodyId = null;
                    wbEditorBox.style.display = 'none';
                  }
                  await loadState(state.active ? state.active.property.id : null);
                },
              },
            }));

            waterbodiesList.appendChild(el('div', {
              className: 'hb-item clickable' + (state.selectedWaterbodyId === wb.id ? ' selected' : ''),
              on: {
                click: () => {
                  bathyWbSel.value = wb.name;
                  openWaterbodyEditor(wb);
                  if (wb.geom) {
                    try { map.fitBounds(window.L.geoJSON(wb.geom).getBounds(), { padding: [24, 24], maxZoom: 15 }); } catch (_) {}
                  }
                },
              },
            }, [
              el('div', { className: 'hb-item-main' }, [
                el('span', { className: 'hb-dot', attrs: { style: 'background:#0284c7' } }),
                el('div', {}, [
                  el('div', { className: 'hb-item-title', text: wb.name }),
                  el('div', { className: 'hb-item-sub', text: subBits.join(' · ') }),
                ]),
              ]),
              el('div', { className: 'hb-row nowrap' }, rowBtns),
            ]));
          });

          if (state.selectedWaterbodyId) {
            const match = wbs.find((w) => w.id === state.selectedWaterbodyId);
            if (match && wbEditorBox.style.display !== 'none') openWaterbodyEditor(match);
          }
          renderSavedChartOverlays();
          syncLayerVisibility();
        }

        function openWaterbodyEditor(wb) {
          state.selectedWaterbodyId = wb.id;
          wbEditorBox.style.display = 'flex';
          wbEditorBox.replaceChildren();

          const nameIn = el('input', { className: 'hb-input', value: wb.name || '', placeholder: 'Waterbody name' });
          const typeSel = el('select', { className: 'hb-select' }, [
            el('option', { value: 'lake', text: 'Lake' }),
            el('option', { value: 'reservoir', text: 'Reservoir' }),
            el('option', { value: 'flowage', text: 'Flowage' }),
            el('option', { value: 'pond', text: 'Pond (Private/Camp)' }),
            el('option', { value: 'river_pool', text: 'River Pool' }),
            el('option', { value: 'slough', text: 'Slough / Backwater' }),
          ]);
          typeSel.value = wb.waterbody_type || 'lake';
          const acresIn = el('input', { className: 'hb-input', value: wb.acres != null ? String(wb.acres) : '', placeholder: 'Acres (e.g. 1471)' });
          const maxDIn = el('input', { className: 'hb-input', value: wb.max_depth_ft != null ? String(wb.max_depth_ft) : '', placeholder: 'Max depth (ft)' });
          const meanDIn = el('input', { className: 'hb-input', value: wb.mean_depth_ft != null ? String(wb.mean_depth_ft) : '', placeholder: 'Mean depth (ft)' });
          const clarityIn = el('input', { className: 'hb-input', value: wb.water_clarity || '', placeholder: 'Clarity (e.g. Stained 6ft)' });
          const bottomIn = el('input', { className: 'hb-input', value: wb.bottom_substrate || '', placeholder: 'Bottom (e.g. 50% sand, 40% muck)' });
          const speciesIn = el('input', { className: 'hb-input', value: (wb.species || []).join(', '), placeholder: 'Species (Walleye, Musky, Bass...)' });
          const accessIn = el('input', { className: 'hb-input', value: wb.public_access || '', placeholder: 'Access / Boat landings' });
          const notesIn = el('input', { className: 'hb-input', value: wb.notes || '', placeholder: 'Local notes, structure, springs, hazards' });

          const spChips = (wb.species || []).length
            ? el('div', { className: 'hb-wb-chips' }, wb.species.map((s) => el('span', { className: 'hb-wb-chip', text: s })))
            : null;

          const saveBtn = el('button', {
            className: 'hb-btn primary sm',
            text: 'Save Dossier',
            on: {
              click: async () => {
                const res = await ctx.action('save_waterbody', {
                  id: wb.id,
                  property_id: state.active ? state.active.property.id : wb.property_id,
                  name: nameIn.value.trim(),
                  waterbody_type: typeSel.value,
                  acres: acresIn.value.trim(),
                  max_depth_ft: maxDIn.value.trim(),
                  mean_depth_ft: meanDIn.value.trim(),
                  water_clarity: clarityIn.value.trim(),
                  bottom_substrate: bottomIn.value.trim(),
                  species: speciesIn.value,
                  public_access: accessIn.value.trim(),
                  notes: notesIn.value.trim(),
                });
                if (res.status === 'ok') {
                  setStatus(`✓ Saved dossier for "${nameIn.value.trim()}".`);
                  await loadState(state.active ? state.active.property.id : null);
                } else {
                  setStatus(`Save error: ${res.message || 'failed'}`);
                }
              },
            },
          });

          const headerBtns = [
            el('button', {
              className: 'hb-btn ghost sm',
              text: 'Close',
              on: {
                click: () => {
                  state.selectedWaterbodyId = null;
                  wbEditorBox.style.display = 'none';
                },
              },
            }),
          ];
          if (wb.wbic) {
            headerBtns.unshift(el('button', {
              className: 'hb-btn sm',
              text: 'Refresh DNR',
              on: {
                click: async () => {
                  setStatus(`Refreshing official WI DNR dossier & contour PDF for WBIC ${wb.wbic}…`);
                  const res = await ctx.action('refresh_waterbody_dossier', { id: wb.id });
                  if (res.status === 'ok') {
                    setStatus(`✓ Refreshed WI DNR dossier for "${wb.name}".`);
                    await loadState(state.active ? state.active.property.id : null);
                  } else {
                    setStatus(`DNR refresh error: ${res.message || 'failed'}`);
                  }
                },
              },
            }));
          }

          wbEditorBox.appendChild(el('div', { className: 'hb-section-head' }, [
            el('strong', { text: `${wb.name}${wb.wbic ? ' (WBIC ' + wb.wbic + ')' : ''}` }),
            el('div', { className: 'hb-row nowrap' }, headerBtns),
          ]));
          if (spChips) wbEditorBox.appendChild(spChips);
          wbEditorBox.appendChild(el('div', { className: 'hb-wb-grid' }, [nameIn, typeSel]));
          wbEditorBox.appendChild(el('div', { className: 'hb-wb-grid' }, [acresIn, maxDIn]));
          wbEditorBox.appendChild(el('div', { className: 'hb-wb-grid' }, [meanDIn, clarityIn]));
          wbEditorBox.appendChild(bottomIn);
          wbEditorBox.appendChild(speciesIn);
          wbEditorBox.appendChild(accessIn);
          wbEditorBox.appendChild(notesIn);
          const footBtns = [saveBtn];
          if (wb.has_cached_map || wb.contour_map_url || wb.wbic) {
            footBtns.push(el('button', {
              className: 'hb-btn sm',
              text: wb.overlay_bounds ? 'Adjust Chart Overlay' : 'Pin Chart Overlay',
              on: { click: () => startChartAlignMode(wb) },
            }));
            if (wb.overlay_bounds) {
              footBtns.push(el('button', {
                className: 'hb-btn ghost sm',
                text: wb.overlay_visible ? 'Hide Chart' : 'Show Chart',
                on: {
                  click: async () => {
                    await ctx.action('save_waterbody_overlay', { id: wb.id, visible: !wb.overlay_visible });
                    await loadState(state.active ? state.active.property.id : null);
                  },
                },
              }));
              footBtns.push(el('button', {
                className: 'hb-btn warn-outline sm',
                text: 'Unpin Chart',
                on: { click: () => doUnpinWaterbodyOverlay(wb) },
              }));
            }
            footBtns.push(el('button', {
              className: 'hb-btn sm',
              text: 'View DNR Contour Map',
              on: { click: () => openWaterbodyContourMap(wb) },
            }));
          }
          wbEditorBox.appendChild(el('div', { className: 'hb-row' }, footBtns));
        }

        async function openWaterbodyContourMap(wb) {
          setStatus(`Loading cached Contour (Bathymetric) Map for ${wb.name}…`);
          const res = await ctx.action('get_waterbody_map', { id: wb.id });
          planDrawer.style.display = 'flex';
          planDrawer.replaceChildren();
          const hdrBtns = [
            el('button', {
              className: 'hb-btn ghost sm',
              text: 'Close',
              on: { click: () => { planDrawer.style.display = 'none'; } },
            }),
          ];
          if (res.contour_map_url) {
            const pdfLink = el('a', {
              className: 'hb-btn sm',
              text: 'Open Official PDF ↗',
              attrs: { href: res.contour_map_url, target: '_blank', rel: 'noopener noreferrer', style: 'text-decoration:none' },
            });
            hdrBtns.unshift(pdfLink);
          }
          if (res.contour_map_png_path) {
            hdrBtns.unshift(el('button', {
              className: 'hb-btn sm',
              text: 'Pin Overlay on Map',
              on: {
                click: () => {
                  planDrawer.style.display = 'none';
                  startChartAlignMode(wb);
                },
              },
            }));
            hdrBtns.unshift(el('button', {
              className: 'hb-btn primary sm',
              text: wb.overlay_bounds ? `Extract Contours (${getAgentName()} Vision)` : `Analyze Map with ${getAgentName()} Vision`,
              on: {
                click: async () => {
                  const prop = state.active && state.active.property;
                  const prevMaxPlanId = (state.plans || []).reduce((mx, p) => Math.max(mx, Number(p.id || 0)), 0);
                  setStatus(`Launching ${getAgentName()} North-Up Tiled Lake Contour Vision on ${wb.name}…`);
                  await ctx.launchTask('lake_contour_analysis', {
                    params: {
                      property_id: prop ? prop.id : wb.property_id,
                      camp: wb.name,
                      waterbody_id: wb.id,
                      image_path: res.contour_map_png_path,
                    },
                  }, {
                    streamContainer: georgeTaskHost,
                    onComplete: async (rd) => {
                      await reconcileCompletedTaskPlan(rd, 'lake_contour_analysis', prevMaxPlanId);
                      refreshTaskHistory();
                    },
                  });
                },
              },
            }));
          }
          planDrawer.appendChild(el('div', { className: 'hb-plan-header' }, [
            el('div', {}, [
              el('div', { className: 'hb-plan-title', text: `${wb.name} — Official Contour (Bathymetric) Map` }),
              el('div', { className: 'hb-plan-meta' }, [
                ...(wb.wbic ? [el('span', { className: 'hb-meta-chip', text: `WBIC ${wb.wbic}` })] : []),
                ...(wb.acres ? [el('span', { className: 'hb-meta-chip', text: `${wb.acres} acres` })] : []),
                ...(wb.max_depth_ft ? [el('span', { className: 'hb-meta-chip', text: `Max ${wb.max_depth_ft} ft` })] : []),
              ]),
            ]),
            el('div', { className: 'hb-row' }, hdrBtns),
          ]));
          if (res.status === 'ok' && res.image_data_url) {
            planDrawer.appendChild(el('img', {
              className: 'hb-lake-map-img',
              attrs: { src: res.image_data_url, alt: `${wb.name} Bathymetric Contour Map` },
            }));
            planDrawer.appendChild(el('div', {
              className: 'hb-muted',
              text: 'Cached locally for offline access. Use "+ Drop Depth (ft)" or "+ Trace Contour" on the map to turn key humps/channels from this chart into live shoreline-clipped GIS contours.',
            }));
            setStatus(`✓ Showing locally cached contour map for ${wb.name}.`);
          } else {
            planDrawer.appendChild(el('div', {
              className: 'hb-muted',
              text: res.message || 'No official DNR contour map PDF was found for this waterbody. Use "+ Drop Depth (ft)" or "+ Trace Contour" to survey it manually.',
            }));
            setStatus(res.message || 'No cached contour map available.');
          }
        }

        async function doSyncWaterbodies(forceRefresh) {
          if (!state.active || !map) { setStatus('Create or select a camp first before syncing lakes.'); return; }
          const b = map.getBounds();
          syncWaterbodiesBtn.disabled = true;
          syncWaterbodiesBtn.textContent = 'Syncing…';
          setStatus('Syncing lake/reservoir polygons (WI DNR 24K / USGS NHD) & caching official dossiers + contour maps…');
          try {
            const res = await ctx.action('sync_waterbodies', {
              property_id: state.active.property.id,
              min_lon: b.getWest(),
              min_lat: b.getSouth(),
              max_lon: b.getEast(),
              max_lat: b.getNorth(),
              fetch_dossiers: true,
              download_pdfs: true,
              force_refresh: !!forceRefresh,
            });
            if (res.status === 'ok') {
              await loadState(state.active.property.id);
              setStatus(`✓ Synced ${res.synced_count || 0} waterbody(s) · ${res.dossiers_loaded || 0} official dossier(s) · ${res.maps_cached || 0} contour map(s) cached offline.`);
            } else {
              setStatus(`Lake sync error: ${res.message || 'failed'}`);
            }
          } finally {
            syncWaterbodiesBtn.disabled = false;
            syncWaterbodiesBtn.textContent = 'Sync Lakes';
          }
        }

        function renderFishingLayers() {
          if (!map || !bathyLayer || !fishStructureLayer || !waterFlowLayer) return;
          bathyLayer.clearLayers();
          fishStructureLayer.clearLayers();
          waterFlowLayer.clearLayers();
          fishStructList.replaceChildren();

          // 1. Bathymetry Underwater Depth Contours & Soundings
          const bGrids = state.bathyGrids || [];
          const bPts = state.bathyPoints || [];
          if (bGrids.length) {
            const bg = bGrids[0];
            const estTag = bg.source === 'estimated_shoreline_model' ? ' (Estimated Basin Model)' : '';
            bathyStatsBox.textContent = `${bg.waterbody_name}${estTag}: Max ${bg.max_depth_ft} ft · Avg ${bg.avg_depth_ft} ft · ${bPts.length} sounding(s).`;
            bathyLegendBox.style.display = 'flex';
            bathyLegendBox.replaceChildren(
              el('div', { className: 'hb-bathy-bar' }),
              el('div', { className: 'hb-elev-labels' }, [
                el('span', { text: '0 ft Shoal' }),
                el('span', { text: `${Math.round((bg.max_depth_ft || 10) / 2)} ft Break` }),
                el('span', { text: `${bg.max_depth_ft} ft Deep Hole` }),
              ]),
            );
            bGrids.forEach((gItem) => {
              if (gItem.contours && Array.isArray(gItem.contours.features)) {
                window.L.geoJSON(gItem.contours, {
                  style: (feat) => ({
                    color: (feat && feat.properties && feat.properties.color) || '#0ea5e9',
                    weight: 1.8,
                    opacity: 0.9,
                  }),
                  onEachFeature: (feat, lyr) => {
                    const dv = feat && feat.properties && feat.properties.depth_ft;
                    if (dv != null) lyr.bindTooltip(`-${dv} ft depth`, { sticky: true });
                  },
                }).addTo(bathyLayer);
              }
            });
          } else {
            bathyLegendBox.style.display = 'none';
            bathyStatsBox.textContent = bPts.length
              ? `${bPts.length} depth sounding(s) recorded — add ${Math.max(0, 4 - bPts.length)} more then click "Build Contours".`
              : 'Drop 4+ depth soundings on a pond/lake (or upload sonar CSV/GeoJSON) to generate underwater depth contours.';
          }
          bPts.forEach((bp) => {
            const mk = window.L.circleMarker([bp.lat, bp.lon], {
              radius: 4.5,
              color: '#0c1017',
              weight: 1.5,
              fillColor: '#38bdf8',
              fillOpacity: 0.9,
            });
            mk.bindTooltip(`${bp.depth_ft} ft`, { direction: 'top', offset: [0, -5] });
            mk.addTo(bathyLayer);
          });

          // 2. Water Current Flow Vectors
          (state.waterFlows || []).forEach((wf) => {
            const rad = ((wf.flow_to_deg || 0) * Math.PI) / 180;
            const lenM = ({ calm: 45, slow: 70, moderate: 100, fast: 135 }[wf.speed_class] || 95);
            const cosL = Math.max(0.2, Math.cos((wf.lat * Math.PI) / 180));
            const tip = [wf.lat + (lenM / 111320) * Math.cos(rad), wf.lon + (lenM / (111320 * cosL)) * Math.sin(rad)];
            window.L.polyline([[wf.lat, wf.lon], tip], { color: '#22d3ee', weight: 3.0, opacity: 0.95 }).addTo(waterFlowLayer);
            drawFlowArrowheads(waterFlowLayer, [[wf.lat, wf.lon], tip], '#67e8f9');
            const mk = window.L.circleMarker([wf.lat, wf.lon], { radius: 4, color: '#0c1017', weight: 1.5, fillColor: '#22d3ee', fillOpacity: 1 });
            mk.bindPopup(el('div', {}, [
              el('strong', { text: wf.label || 'Current Flow' }),
              el('div', { text: `${(wf.flow_type || '').replace(/_/g, ' ')} → ${degToCompass(wf.flow_to_deg || 0)} (${wf.speed_class || 'moderate'})` }),
              el('button', {
                className: 'hb-btn warn-outline sm',
                text: 'Remove Flow',
                attrs: { style: 'margin-top:6px' },
                on: {
                  click: async () => {
                    await ctx.action('delete_water_flow', { id: wf.id });
                    await loadState(state.active ? state.active.property.id : null);
                  },
                },
              }),
            ]));
            mk.addTo(waterFlowLayer);
          });

          // 3. Fish Structures
          const fStructs = state.fishStructures || [];
          const fFlows = state.waterFlows || [];
          if (!fStructs.length && !fFlows.length) {
            fishStructList.appendChild(el('div', { className: 'hb-muted', text: 'No fish structures or current arrows pinned yet.' }));
          } else {
            fStructs.forEach((fs) => {
              const dTxt = fs.depth_ft != null ? `${fs.depth_ft} ft` : '';
              const mk = window.L.circleMarker([fs.lat, fs.lon], {
                radius: 7,
                color: '#ffffff',
                weight: 2,
                fillColor: '#06b6d4',
                fillOpacity: 0.95,
              });
              mk.bindTooltip(`${fs.name}${dTxt ? ' (' + dTxt + ')' : ''}`, { direction: 'top', offset: [0, -7] });
              mk.bindPopup(el('div', {}, [
                el('strong', { text: fs.name }),
                el('div', { text: `${(fs.kind || '').replace(/_/g, ' ')}${dTxt ? ' · ' + dTxt : ''}${fs.bottom_type ? ' · ' + fs.bottom_type : ''}` }),
                ...(fs.target_species ? [el('div', { text: `Species: ${fs.target_species}` })] : []),
              ]));
              mk.addTo(fishStructureLayer);

              fishStructList.appendChild(el('div', {
                className: 'hb-item clickable',
                on: { click: () => map.setView([fs.lat, fs.lon], 16) },
              }, [
                el('div', { className: 'hb-item-main' }, [
                  el('span', { className: 'hb-dot', attrs: { style: 'background:#06b6d4' } }),
                  el('div', {}, [
                    el('div', { className: 'hb-item-title', text: fs.name }),
                    el('div', { className: 'hb-item-sub', text: `${(fs.kind || '').replace(/_/g, ' ')}${dTxt ? ' · ' + dTxt : ''}${fs.target_species ? ' · ' + fs.target_species : ''}` }),
                  ]),
                ]),
                el('button', {
                  className: 'hb-btn subtle-danger sm',
                  text: '×',
                  on: {
                    click: async (ev) => {
                      ev.stopPropagation();
                      await ctx.action('delete_fish_structure', { id: fs.id });
                      await loadState(state.active ? state.active.property.id : null);
                    },
                  },
                }),
              ]));
            });
            fFlows.forEach((wf) => {
              fishStructList.appendChild(el('div', {
                className: 'hb-item clickable',
                on: { click: () => map.setView([wf.lat, wf.lon], 16) },
              }, [
                el('div', { className: 'hb-item-main' }, [
                  el('span', { className: 'hb-dot', attrs: { style: 'background:#22d3ee' } }),
                  el('div', {}, [
                    el('div', { className: 'hb-item-title', text: wf.label || 'Water Current' }),
                    el('div', { className: 'hb-item-sub', text: `Flow → ${degToCompass(wf.flow_to_deg || 0)} · ${wf.speed_class || 'moderate'}` }),
                  ]),
                ]),
                el('button', {
                  className: 'hb-btn subtle-danger sm',
                  text: '×',
                  on: {
                    click: async (ev) => {
                      ev.stopPropagation();
                      await ctx.action('delete_water_flow', { id: wf.id });
                      await loadState(state.active ? state.active.property.id : null);
                    },
                  },
                }),
              ]));
            });
          }
          syncLayerVisibility();
        }

        async function doRefreshFishAnalysis() {
          if (!state.active) { setStatus('Create or select a camp first.'); return; }
          refreshFishBtn.disabled = true;
          refreshFishBtn.textContent = 'Analyzing…';
          setStatus('Analyzing Fish Bite Index, windward baitfish shoreline & live USGS / Global river gauges…');
          try {
            const res = await ctx.action('fish_analyze', {
              property_id: state.active.property.id,
              wind_deg: state.windDeg,
              wind_mph: state.windMph,
            });
            if (res.status !== 'ok') { setStatus(`Fish analysis error: ${res.message || 'failed'}`); return; }
            state.fishAnalysis = res;
            state.riverGauges = res.nearest_gauges || [];

            const bi = res.bite_index || {};
            const sc = Number(bi.score || 50);
            const col = sc >= 80 ? '#2dd4bf' : (sc >= 65 ? '#38bdf8' : (sc >= 45 ? '#fbbf24' : '#f97316'));
            biteCard.style.display = 'flex';
            biteCard.replaceChildren(
              el('div', { className: 'hb-move-head' }, [
                el('span', { text: 'Solunar & Barometric Bite Index' }),
                el('span', { text: `${sc}/100 · ${bi.rating || 'MODERATE'}`, attrs: { style: `color:${col}` } }),
              ]),
              el('div', { className: 'hb-move-track' }, [
                el('div', { className: 'hb-move-fill', attrs: { style: `width:${Math.max(5, Math.min(100, sc))}%;background:${col}` } }),
              ]),
              el('div', { className: 'hb-wx-sub', text: (bi.factors || []).join(' · ') || 'Baseline fishing conditions.' }),
            );

            const wd = res.wind_drift || {};
            windDriftBox.textContent = wd.tactic || 'Wind drift analysis ready.';

            gaugesBox.replaceChildren();
            if (!state.riverGauges.length) {
              gaugesBox.appendChild(el('div', { className: 'hb-muted', text: 'No live river gauges returned for this basin.' }));
            } else {
              state.riverGauges.slice(0, 3).forEach((g) => {
                const cfsTxt = g.cfs != null ? `${g.cfs} CFS` : 'n/a';
                const stgTxt = g.gage_height_ft != null ? ` · Stage ${g.gage_height_ft} ft` : '';
                const tmpTxt = g.water_temp_f != null ? ` · ${g.water_temp_f}°F` : '';
                gaugesBox.appendChild(el('div', { className: 'hb-gauge-card' }, [
                  el('strong', { text: `${g.site_name} (${g.distance_mi} mi)` }),
                  el('div', { className: 'hb-wx-sub', text: `${cfsTxt}${stgTxt}${tmpTxt} · ${g.provider_id || 'gauge'}` }),
                ]));
              });
            }
            setStatus(`✓ Fishing analysis ready: Bite ${sc}/100 (${bi.rating}) · Windward shore: ${wd.windward_baitfish_shore} · ${state.riverGauges.length} gauge(s).`);
          } finally {
            refreshFishBtn.disabled = false;
            refreshFishBtn.textContent = 'Analyze Bite & Gauges';
          }
        }

        async function doRebuildBathymetry() {
          if (!state.active) { setStatus('Select a camp first.'); return; }
          buildBathyBtn.disabled = true;
          buildBathyBtn.textContent = 'Building…';
          try {
            const res = await ctx.action('rebuild_bathymetry', {
              property_id: state.active.property.id,
              waterbody_name: bathyWbSel.value || null,
            });
            if (res.status === 'ok') {
              setStatus(`✓ Built ${res.contour_count} depth contour(s) on ${res.waterbody_name} (max ${res.max_depth_ft} ft${res.shoreline_clipped ? ', shoreline-clipped' : ''}).`);
              await loadState(state.active.property.id);
            } else {
              setStatus(res.message || 'Add depth soundings or trace a contour line first.');
            }
          } finally {
            buildBathyBtn.disabled = false;
            buildBathyBtn.textContent = 'Build Contours';
          }
        }

        async function doImportDepthFile() {
          if (!state.active) { setStatus('Select a camp first.'); return; }
          const file = bathyFileInput.files && bathyFileInput.files[0];
          if (!file) { setStatus('Select a sonar .csv or depth .geojson file first.'); return; }
          setStatus(`Importing depth dataset "${file.name}"…`);
          const buf = await file.arrayBuffer();
          const bytes = new Uint8Array(buf);
          let bin = '';
          for (let i = 0; i < bytes.length; i += 8192) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
          const res = await ctx.action('import_depth_dataset', {
            property_id: state.active.property.id,
            filename: file.name,
            file_content_b64: btoa(bin),
            waterbody_name: bathyWbSel.value || undefined,
          });
          if (res.status === 'ok') {
            bathyFileInput.value = '';
            bathyFileLabel.textContent = 'Upload Sonar CSV (lat,lon,depth_ft) or Depth GeoJSON…';
            setStatus(`✓ Imported sonar bathymetry "${file.name}" (${res.points_imported || 0} pts, ${res.contour_count || 0} contours).`);
            await loadState(state.active.property.id);
          } else {
            setStatus(`Bathymetry import error: ${res.message || 'failed'}`);
          }
        }

        async function loadState(propertyId) {
          const res = await ctx.action('get_state', propertyId ? { property_id: propertyId } : {});
          if (res.status !== 'ok') { setStatus(`Load failed: ${res.message || 'error'}`); return; }
          if (res.agent_name) {
            state.agentName = res.agent_name;
            updateDynamicAgentLabels();
          }
          state.properties = res.properties || [];
          state.active = res.active;
          state.hunters = res.hunters || [];
          state.plans = (res.active && res.active.plans) || [];
          state.annotations = (res.active && res.active.annotations) || [];
          state.terrain = (res.active && res.active.terrain) || null;
          state.topoLandmarks = (res.active && res.active.topo_landmarks) || [];
          state.routes = (res.active && res.active.routes) || [];
          state.waterbodies = (res.active && res.active.waterbodies) || [];
          state.fishStructures = (res.active && res.active.fish_structures) || [];
          state.waterFlows = (res.active && res.active.water_flows) || [];
          state.bathyPoints = (res.active && res.active.bathymetry_points) || [];
          state.bathyGrids = (res.active && res.active.bathymetry_grids) || [];

          fetchDemBtn.textContent = (state.terrain && state.terrain.has_dem) ? 'Refresh DEM' : 'Fetch LiDAR DEM';

          propSelect.replaceChildren();
          if (!state.properties.length) {
            propSelect.appendChild(el('option', { value: '', text: '— No camps yet —' }));
          } else {
            state.properties.forEach((p) => {
              const opt = el('option', { value: String(p.id), text: `${p.name} (${p.visibility === 'channel_private' ? 'Private' : 'Public'})` });
              if (state.active && state.active.property.id === p.id) opt.selected = true;
              propSelect.appendChild(opt);
            });
          }

          renderParcelsAndWaypoints();
          renderRoutesAndHydro();
          renderTopoAndLandmarks();
          renderWaterbodies();
          renderFishingLayers();
          renderPlansAndAnnotations();
          renderHunters();
          renderThermalCard(state.currentWeatherHour);
          renderMapWeatherHud();
          await refreshScentPlan();
          const demTag = (state.terrain && state.terrain.has_dem) ? ` · DEM ${Math.round(state.terrain.relief_ft || 0)} ft relief` : '';
          setStatus(state.active
            ? `${state.active.property.name} · ${(state.active.parcels || []).length} parcels · ${(state.active.waypoints || []).length} pins · ${state.routes.length} traced path(s) · ${state.topoLandmarks.length} landmark(s)${demTag}`
            : 'Pan to your hunting area and click Draw Bounds or Sync GIS.');
        }

        async function doImportCoords() {
          const lat = parseFloat(latInput.value);
          const lon = parseFloat(lonInput.value);
          if (Number.isNaN(lat) || Number.isNaN(lon)) {
            setStatus('Click "Draw Bounds" on the map (or enter lat/lon) to set your camp location.');
            return;
          }
          setStatus('Saving camp & fetching statewide parcel GIS…');
          const payload = {
            property_name: nameInput.value.trim() || 'New Hunting Camp',
            lat,
            lon,
            fetch_state_parcels: true,
          };
          if (state.drawnBoundary) {
            payload.boundary_geojson = state.drawnBoundary;
            payload.acres = state.drawnAcres || 0;
          }
          const res = await ctx.action('import_data', payload);
          if (res.status === 'ok') {
            state.viewportParcels = [];
            state.drawnBoundary = null;
            state.drawnAcres = 0;
            drawnBoundsPill.style.display = 'none';
            if (drawLayer) drawLayer.clearLayers();
            state.importOpen = false;
            importDrawer.style.display = 'none';
            toggleImportBtn.classList.remove('active');
            setStatus(`Saved "${payload.property_name}" (${res.parcels_imported} parcel(s) via ${res.state_provider || 'GIS'}).`);
            await loadState(res.property_id);
            if (map) {
              map.setView([lat, lon], 15);
              await loadViewportGis(false);
            }
          } else {
            setStatus(`Import error: ${res.message || 'failed'}`);
          }
        }

        async function doImportFile() {
          const file = fileInput.files && fileInput.files[0];
          if (!file) { setStatus('Select a GPX, KML, KMZ, GeoJSON, or PDF file first.'); return; }
          const buf = await file.arrayBuffer();
          const bytes = new Uint8Array(buf);
          let bin = '';
          for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
          const res = await ctx.action('import_data', {
            property_name: nameInput.value.trim() || file.name.replace(/\.[^.]+$/, ''),
            filename: file.name,
            file_content_b64: btoa(bin),
          });
          if (res.status === 'ok') {
            state.importOpen = false;
            importDrawer.style.display = 'none';
            toggleImportBtn.classList.remove('active');
            setStatus(`Imported ${res.parcels_imported} parcels, ${res.waypoints_imported} pins from ${file.name}.`);
            await loadState(res.property_id);
          } else {
            setStatus(`Import error: ${res.message || 'failed'}`);
          }
        }

        async function fetchWeather() {
          if (!state.active) { setStatus('Create or select a camp first before fetching weather.'); return; }
          fetchWeatherBtn.disabled = true;
          fetchWeatherBtn.textContent = 'Fetching…';
          setStatus('Fetching Open-Meteo weather (wind, temp, pressure, solar, precip) & NOAA DNR shooting hours…');
          try {
            const res = await ctx.action('weather', { property_id: state.active.property.id, days: 2 });
            if (res.status !== 'ok') {
              setStatus(`Weather fetch failed: ${res.message || 'upstream error'}`);
              return;
            }
            state.weather = res;
            state.weatherFetchedAt = Date.now();
            state.manualWindOverride = false;
            const hourly = res.hourly || [];
            hourScrubber.style.display = '';
            hourScrubber.max = String(Math.max(0, hourly.length - 1));
            const startIdx = (typeof res.current_hour_index === 'number' && res.current_hour_index >= 0 && res.current_hour_index < hourly.length)
              ? res.current_hour_index
              : 0;
            hourScrubber.value = String(startIdx);
            applyWeatherHour(startIdx);
            const h = hourly[startIdx];
            const srcTag = res.offline_fallback
              ? 'Offline Cached Forecast'
              : ((res.provider_id || '').includes('nws') ? 'NOAA/NWS Failover' : (res.from_cache ? 'Open-Meteo Cached' : 'Open-Meteo'));
            if (h) {
              setStatus(`✓ Weather loaded (${hourly.length}h ${srcTag} + NOAA light): ${h.temp_f}°F ${h.condition} · ${h.wind_compass} ${h.wind_mph} mph · ${h.pressure_inhg} inHg (${h.pressure_trend}) · Move ${h.movement_score}/100 ${h.movement_rating}.`);
            } else {
              setStatus(`✓ Weather loaded (${hourly.length} hourly points via ${srcTag}).`);
            }
          } catch (err) {
            setStatus(`Weather fetch error: ${err && err.message ? err.message : String(err)}`);
          } finally {
            fetchWeatherBtn.disabled = false;
            fetchWeatherBtn.textContent = state.weather ? 'Refresh Weather' : 'Fetch Weather';
          }
        }

        function applyWeatherHour(idx) {
          if (!state.weather || !state.weather.hourly || !state.weather.hourly[idx]) return;
          const h = state.weather.hourly[idx];
          state.currentWeatherHour = h;
          state.windDeg = Math.round(h.wind_deg || 0);
          state.windMph = Math.round(h.wind_mph || 5);
          const validModes = ['neutral', 'upslope_warming', 'katabatic_drainage'];
          state.thermalMode = validModes.includes(h.thermal_mode) ? h.thermal_mode : 'neutral';
          windDegInput.value = String(state.windDeg);
          windMphInput.value = String(state.windMph);
          thermalSel.value = state.thermalMode;
          const isNow = idx === state.weather.current_hour_index;
          const sh = h.shooting_light || (state.weather.shooting_hours || [])[0];
          const lightTxt = sh ? ` · Legal Light ${sh.legal_shooting_start || sh.legal_start}–${sh.legal_shooting_end || sh.legal_end}` : '';
          hourLabel.textContent = `${h.time.replace('T', ' ')}${isNow ? ' (Now)' : ''} · ${h.condition || ''} · ${h.wind_compass} ${h.wind_mph} mph${lightTxt}`;
          renderWeatherHud(h);
          renderThermalCard(h);
          renderMapWeatherHud();
          renderTopoAndLandmarks();
          refreshScentPlan();
        }

        async function doExport(fmt) {
          if (!state.active) { setStatus('Load a camp first.'); return; }
          const res = await ctx.action('export_gpx', { property_id: state.active.property.id, fmt });
          if (res.status !== 'ok' || !res.content) { setStatus(`Export failed: ${res.message || ''}`); return; }
          const blob = new Blob([res.content], { type: 'application/gpx+xml' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `${state.active.property.name.replace(/\W+/g, '_').toLowerCase()}_onx.${fmt}`;
          a.click();
          URL.revokeObjectURL(url);
          setStatus(`Downloaded ${a.download} (${res.waypoints_count} waypoints) ready for onX Hunt.`);
        }

        async function doPostDiscordMap() {
          if (!state.active) { setStatus('Load a camp first.'); return; }
          setStatus('Rendering high-DPI tactical map & posting to Discord…');
          const res = await ctx.action('post_discord_map', {
            property_id: state.active.property.id,
            wind_deg: state.windDeg,
            wind_mph: state.windMph,
            thermal_mode: state.thermalMode,
            basemap: state.basemap,
            scenario_name: `${degToCompass(state.windDeg)} ${state.windMph} mph Tactical Card`,
          });
          setStatus(res.status === 'ok' ? `Rendered & published: ${res.png_path}` : `Render error: ${res.message}`);
        }

        function toggleFieldBeacon() {
          if (state.beaconWatchId != null) {
            navigator.geolocation.clearWatch(state.beaconWatchId);
            state.beaconWatchId = null;
            beaconBtn.textContent = 'Start Beacon';
            beaconBtn.classList.remove('warn-outline');
            beaconBtn.classList.add('primary');
            setStatus('Mobile Field Beacon stopped.');
            return;
          }
          if (!navigator.geolocation) { setStatus('Browser Geolocation API unavailable.'); return; }
          const hName = hunterNameIn.value.trim() || 'Field Hunter';
          const hId = hName.toLowerCase().replace(/[^a-z0-9_-]+/g, '-') || 'hunter';
          if (window.DeviceOrientationEvent) {
            window.addEventListener('deviceorientation', (ev) => {
              if (typeof ev.webkitCompassHeading === 'number') state.beaconHeading = ev.webkitCompassHeading;
              else if (typeof ev.alpha === 'number') state.beaconHeading = (360 - ev.alpha) % 360;
            });
          }
          beaconBtn.textContent = 'Stop Beacon';
          beaconBtn.classList.remove('primary');
          beaconBtn.classList.add('warn-outline');
          state.beaconWatchId = navigator.geolocation.watchPosition(
            async (pos) => {
              const payload = {
                hunter_id: hId,
                hunter_name: hName,
                lat: pos.coords.latitude,
                lon: pos.coords.longitude,
                heading_deg: pos.coords.heading != null && !Number.isNaN(pos.coords.heading) ? pos.coords.heading : state.beaconHeading,
                speed_mps: pos.coords.speed || 0,
                accuracy_m: pos.coords.accuracy || 0,
                status: 'in_field',
                property_id: state.active ? state.active.property.id : null,
              };
              await fetch('/api/modules/hunting-buddy/mobile/beacon', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
              });
              const hRes = await ctx.action('list_hunters', state.active ? { property_id: state.active.property.id } : {});
              if (hRes.status === 'ok') { state.hunters = hRes.hunters || []; renderHunters(); }
            },
            (err) => setStatus(`GPS error: ${err.message}`),
            { enableHighAccuracy: true, maximumAge: 5000, timeout: 10000 }
          );
          setStatus(`Streaming live GPS & compass heading for ${hName}…`);
        }

        // ── Event Wiring ─────────────────────────────────────────────────
        modeBtnHunt.addEventListener('click', () => setAppMode('hunt'));
        modeBtnFish.addEventListener('click', () => setAppMode('fish'));
        fishStructKindSel.addEventListener('change', () => { state.fishStructKind = fishStructKindSel.value; });
        dropFishStructBtn.addEventListener('click', () => {
          const next = !state.fishStructDropMode;
          clearAllPlacementModes();
          state.fishStructDropMode = next;
          dropFishStructBtn.classList.toggle('active', next);
          syncMapInteractionMode();
          setStatus(next ? `Click the waterbody on the map to pin a ${state.fishStructKind.replace(/_/g, ' ')} (parcel popups paused).` : 'Structure pin cancelled.');
        });
        dropFlowBtn.addEventListener('click', () => {
          const next = !state.flowDropMode;
          clearAllPlacementModes();
          state.flowDropMode = next;
          dropFlowBtn.classList.toggle('active', next);
          syncMapInteractionMode();
          setStatus(next ? 'Click on the waterbody to drop a directional Current Flow Arrow.' : 'Current flow placement cancelled.');
        });
        dropDepthBtn.addEventListener('click', () => {
          const next = !state.depthDropMode;
          clearAllPlacementModes();
          state.depthDropMode = next;
          dropDepthBtn.classList.toggle('active', next);
          syncMapInteractionMode();
          setStatus(next ? 'Click points on the pond/lake to drop depth soundings in feet (Cancel prompt when done).' : 'Depth sounding mode stopped.');
        });
        drawBoundsBtn.addEventListener('click', () => {
          if (state.drawBoundsMode && state.drawTarget === 'camp') {
            stopDrawBoundsMode(false);
            setStatus('Draw Camp Bounds cancelled.');
          } else {
            startDrawBoundsMode('camp');
          }
        });
        drawWaterbodyBtn.addEventListener('click', () => {
          if (state.drawBoundsMode && state.drawTarget === 'waterbody') {
            stopDrawBoundsMode(false);
            setStatus('Draw Pond/Lake cancelled.');
          } else {
            startDrawBoundsMode('waterbody');
          }
        });
        traceContourBtn.addEventListener('click', () => {
          if (state.traceContourMode) {
            stopTraceMode();
            setStatus('Depth contour trace cancelled.');
          } else {
            startTraceMode(true);
          }
        });
        drawUndoBtn.addEventListener('click', () => {
          if (state.drawPoints.length) {
            state.drawPoints.pop();
            renderDrawPreview();
          }
        });
        drawFinishBtn.addEventListener('click', finishDrawBounds);
        drawCancelBtn.addEventListener('click', () => {
          stopDrawBoundsMode(false);
          setStatus('Draw Camp Bounds cancelled.');
        });
        toggleImportBtn.addEventListener('click', () => {
          state.importOpen = !state.importOpen;
          importDrawer.style.display = state.importOpen ? 'flex' : 'none';
          toggleImportBtn.classList.toggle('active', state.importOpen);
          if (state.importOpen && state.providersOpen) {
            state.providersOpen = false;
            providersDrawer.style.display = 'none';
            toggleProvidersBtn.classList.remove('active');
          }
        });
        toggleProvidersBtn.addEventListener('click', () => {
          state.providersOpen = !state.providersOpen;
          providersDrawer.style.display = state.providersOpen ? 'flex' : 'none';
          toggleProvidersBtn.classList.toggle('active', state.providersOpen);
          if (state.providersOpen) {
            if (state.importOpen) {
              state.importOpen = false;
              importDrawer.style.display = 'none';
              toggleImportBtn.classList.remove('active');
            }
            loadProvidersDrawer();
          }
        });
        routeKindSel.addEventListener('change', () => { state.traceKind = routeKindSel.value; });
        traceRouteBtn.addEventListener('click', () => {
          if (state.traceMode) { stopTraceMode(); setStatus('Path tracing cancelled.'); }
          else startTraceMode(false);
        });
        traceUndoBtn.addEventListener('click', () => {
          if (state.tracePoints.length) { state.tracePoints.pop(); renderTracePreview(); }
        });
        traceFlipBtn.addEventListener('click', () => {
          if (state.tracePoints.length >= 2) { state.tracePoints.reverse(); renderTracePreview(); }
        });
        traceSaveBtn.addEventListener('click', finishSaveTrace);
        traceCancelBtn.addEventListener('click', () => { stopTraceMode(); setStatus('Path tracing cancelled.'); });
        alignRotLeftBtn.addEventListener('click', () => {
          state.alignRotation = ((((state.alignRotation - 90) + 180) % 360) + 360) % 360 - 180;
          syncAlignControlLabels();
          if (state._rebuildAlignWidgets) state._rebuildAlignWidgets();
        });
        alignRotRightBtn.addEventListener('click', () => {
          state.alignRotation = ((((state.alignRotation + 90) + 180) % 360) + 360) % 360 - 180;
          syncAlignControlLabels();
          if (state._rebuildAlignWidgets) state._rebuildAlignWidgets();
        });
        alignRotIn.addEventListener('input', () => {
          state.alignRotation = Number(alignRotIn.value);
          syncAlignControlLabels();
          if (state._rebuildAlignWidgets) state._rebuildAlignWidgets();
        });
        alignFlipHBtn.addEventListener('click', () => {
          state.alignFlipH = !state.alignFlipH;
          syncAlignControlLabels();
          if (state._rebuildAlignWidgets) state._rebuildAlignWidgets();
        });
        alignFlipVBtn.addEventListener('click', () => {
          state.alignFlipV = !state.alignFlipV;
          syncAlignControlLabels();
          if (state._rebuildAlignWidgets) state._rebuildAlignWidgets();
        });
        alignBlendSel.addEventListener('change', () => {
          state.alignBlendMode = alignBlendSel.value;
          if (state._rebuildAlignWidgets) state._rebuildAlignWidgets();
        });
        alignOpacityIn.addEventListener('input', () => {
          state.alignOpacity = Number(alignOpacityIn.value) / 100;
          alignOpacityLbl.textContent = `${Math.round(state.alignOpacity * 100)}%`;
          if (state._activeAlignOverlay) state._activeAlignOverlay.setOpacity(state.alignOpacity);
        });
        alignCropBtn.addEventListener('click', () => {
          if (state.cropPanelActive) stopCropPanel();
          else startCropPanel();
        });
        cropApplyBtn.addEventListener('click', () => {
          state.alignCrop = normalizeCropBox(_draftCrop);
          stopCropPanel();
          syncAlignControlLabels();
          if (state._rebuildAlignWidgets) state._rebuildAlignWidgets();
          setStatus('✓ Applied chart crop — legends/noise trimmed! Click "✨ Auto-Match" or "🎯 2-Point Snap" to align the cropped lake.');
        });
        cropResetBtn.addEventListener('click', () => {
          _draftCrop = { left: 0, top: 0, right: 1, bottom: 1 };
          state.alignCrop = { left: 0, top: 0, right: 1, bottom: 1 };
          stopCropPanel();
          syncAlignControlLabels();
          if (state._rebuildAlignWidgets) state._rebuildAlignWidgets();
          setStatus('Reset chart crop back to full sheet.');
        });
        cropCloseBtn.addEventListener('click', () => stopCropPanel());
        alignSnapBtn.addEventListener('click', () => {
          if (state.twoPointSnapActive) stopTwoPointSnap();
          else startTwoPointSnap();
        });
        snapPanelCancelBtn.addEventListener('click', () => stopTwoPointSnap());
        alignAutoBtn.addEventListener('click', () => doAutoAlignChart());
        alignResetBtn.addEventListener('click', () => {
          if (state.aligningWb) {
            const wbCopy = { ...state.aligningWb, overlay_bounds: null, overlay_transform: {} };
            startChartAlignMode(wbCopy);
          }
        });
        alignUnpinBtn.addEventListener('click', () => {
          if (state.aligningWb) doUnpinWaterbodyOverlay(state.aligningWb);
        });
        alignSaveBtn.addEventListener('click', () => saveChartAlignment(false));
        alignExtractBtn.addEventListener('click', () => saveChartAlignment(true));
        alignCancelBtn.addEventListener('click', () => { stopChartAlignMode(); setStatus('Chart alignment cancelled.'); });
        propSelect.addEventListener('change', () => { state.viewportParcels = []; loadState(Number(propSelect.value)); });
        privacyBtn.addEventListener('click', async () => {
          if (!state.active) return;
          const next = state.active.property.visibility === 'channel_private' ? 'public' : 'channel_private';
          await ctx.action('set_privacy', { property_id: state.active.property.id, visibility: next });
          await loadState(state.active.property.id);
        });
        windDegInput.addEventListener('input', () => {
          state.windDeg = Number(windDegInput.value);
          state.manualWindOverride = true;
          renderMapWeatherHud();
          refreshScentPlan();
        });
        windMphInput.addEventListener('input', () => {
          state.windMph = Number(windMphInput.value);
          state.manualWindOverride = true;
          renderMapWeatherHud();
          refreshScentPlan();
        });
        thermalSel.addEventListener('change', () => {
          state.thermalMode = thermalSel.value;
          state.manualWindOverride = true;
          renderThermalCard(state.currentWeatherHour);
          renderMapWeatherHud();
          renderTopoAndLandmarks();
          refreshScentPlan();
        });
        hourScrubber.addEventListener('input', () => applyWeatherHour(Number(hourScrubber.value)));
        kindSel.addEventListener('change', () => { state.dropKind = kindSel.value; });
        dropBtn.addEventListener('click', () => {
          state.dropMode = !state.dropMode;
          if (state.dropMode) {
            state.annotateDropMode = false;
            state.topoDropMode = false;
            dropAnnBtn.classList.remove('active');
            dropTopoBtn.classList.remove('active');
            if (state.drawBoundsMode) stopDrawBoundsMode(false);
          }
          dropBtn.classList.toggle('active', state.dropMode);
          syncMapInteractionMode();
          setStatus(state.dropMode ? `Click anywhere on the map to drop a ${state.dropKind} pin (parcel popups paused).` : 'Pin drop cancelled.');
        });
        topoKindSel.addEventListener('change', () => { state.topoDropKind = topoKindSel.value; });
        contourStepSel.addEventListener('change', () => {
          if (state.active && state.terrain && state.terrain.has_dem) doFetchDem();
        });
        dropTopoBtn.addEventListener('click', () => {
          state.topoDropMode = !state.topoDropMode;
          if (state.topoDropMode) {
            state.dropMode = false;
            state.annotateDropMode = false;
            dropBtn.classList.remove('active');
            dropAnnBtn.classList.remove('active');
            if (state.drawBoundsMode) stopDrawBoundsMode(false);
          }
          dropTopoBtn.classList.toggle('active', state.topoDropMode);
          syncMapInteractionMode();
          const pretty = state.topoDropKind.replace(/_/g, ' ');
          setStatus(state.topoDropMode ? `Click anywhere on the map to tag a ${pretty} landmark (parcel popups paused).` : 'Topo landmark tagging cancelled.');
        });
        annKindSel.addEventListener('change', () => { state.annotateKind = annKindSel.value; });
        dropAnnBtn.addEventListener('click', () => {
          state.annotateDropMode = !state.annotateDropMode;
          if (state.annotateDropMode) {
            state.dropMode = false;
            state.topoDropMode = false;
            dropBtn.classList.remove('active');
            dropTopoBtn.classList.remove('active');
            if (state.drawBoundsMode) stopDrawBoundsMode(false);
          }
          dropAnnBtn.classList.toggle('active', state.annotateDropMode);
          syncMapInteractionMode();
          setStatus(state.annotateDropMode ? `Click anywhere on the map to drop a removable ${state.annotateKind.replace('_', ' ')} object (parcel popups paused).` : 'Map annotation drop cancelled.');
        });
        clearAnnBtn.addEventListener('click', async () => {
          if (!state.active) return;
          if (!window.confirm('Remove all removable map objects for this camp?')) return;
          await ctx.action('clear_annotations', { property_id: state.active.property.id });
          await loadState(state.active.property.id);
          setStatus('Cleared all removable map objects for this camp.');
        });
        beaconBtn.addEventListener('click', toggleFieldBeacon);
        tokenBtn.addEventListener('click', async () => {
          const hName = hunterNameIn.value.trim() || 'Android Hunter';
          const hId = hName.toLowerCase().replace(/[^a-z0-9_-]+/g, '-') || 'android-hunter';
          const res = await ctx.action('issue_beacon_token', { hunter_id: hId, hunter_name: hName });
          if (res.status === 'ok') {
            tokenOut.textContent = `Hunter: ${res.hunter_id}\nHeader: X-Beacon-Token: ${res.beacon_token}\nPOST ${window.location.origin}/api/modules/hunting-buddy/mobile/beacon\nBody: {"hunter_id":"${res.hunter_id}","lat":45.9200,"lon":-89.2500,"heading_deg":315}`;
          }
        });

        initMap();
        loadState(null);
        refreshTaskHistory();

        state.pollTimer = setInterval(async () => {
          if (!document.body.contains(root)) {
            clearInterval(state.pollTimer);
            if (state.resizeObserver) { try { state.resizeObserver.disconnect(); } catch (_) {} }
            return;
          }
          try {
            const hRes = await ctx.action('list_hunters', state.active ? { property_id: state.active.property.id } : {});
            if (hRes.status === 'ok') { state.hunters = hRes.hunters || []; renderHunters(); }
          } catch (_) {}
        }, 8000);
      },
    });
  }

  registerHuntingBuddy();
})();
