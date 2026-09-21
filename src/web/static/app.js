/* bazos monitor - vanilla JS, no frameworks. Phase 7b visual redesign.
   Talks only to the local Flask API; no external requests. */
(function () {
  "use strict";

  function readJSON(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  var catalog = readJSON("catalog-data") || [];
  var overview = readJSON("overview-data") || {};
  var appConfig = readJSON("app-config") || {};
  var ALL_LABEL = appConfig.all_label || "Všetky stiahnuté kategórie";
  var CURRENT_FILTERS = appConfig.filters || {};
  var CURRENT_SORT = appConfig.sort || "-first_seen";

  var catalogLabels = {};
  catalog.forEach(function (c) { catalogLabels[c.key] = c.label; });

  /* ---------------- tiny inline-icon helpers ---------------- */

  function svgIcon(inner, cls) {
    return '<svg class="lucide ' + (cls || "size-3.5") + '" xmlns="http://www.w3.org/2000/svg" ' +
      'width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      inner + "</svg>";
  }

  var ICON_X = '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>';
  var ICON_CHEVRON_DOWN = '<path d="m6 9 6 6 6-6"/>';
  var ICON_CHEVRON_UP = '<path d="m18 15-6-6-6 6"/>';
  var ICON_SPARKLES = '<path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z"/><path d="M20 2v4"/><path d="M22 4h-4"/><circle cx="4" cy="20" r="2"/>';
  var ICON_PENCIL = '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>';
  var ICON_REFRESH_CW = '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>';
  var ICON_TRASH_2 = '<path d="M10 11v6"/><path d="M14 11v6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>';

  var BATCH_ICONS = {
    pending: '<circle cx="12" cy="12" r="10"/>',
    running: '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
    done: '<path d="M20 6 9 17l-5-5"/>',
    skipped_recent: '<path d="m9 18 6-6-6-6"/>',
    blocked: '<circle cx="12" cy="12" r="10"/><path d="M4.929 4.929 19.07 19.071"/>',
    failed: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    not_run: '<path d="M5 12h14"/>',
    cancelled: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'
  };

  function fold(text) {
    return (text || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  function countLabel(key) {
    var data = overview[key];
    if (!data) return "";
    if (data.complete) return " (" + data.total + " · kompletné)";
    if (data.estimate) return " (" + data.total + " z ~" + data.estimate + ")";
    return " (" + data.total + " / " + data.unseen + ")";
  }

  function apiJSON(method, url, body) {
    var options = { method: method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) options.body = JSON.stringify(body);
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { status: response.status, data: data };
      });
    });
  }

  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

  function setBusy(button, busy) {
    if (!button) return;
    if (busy) {
      button.disabled = true;
      if (!button.querySelector(".spinner")) {
        var spinner = document.createElement("span");
        spinner.className = "spinner";
        spinner.innerHTML = svgIcon('<path d="M21 12a9 9 0 1 1-6.219-8.56"/>', "size-3.5");
        button.insertBefore(spinner, button.firstChild);
      }
    } else {
      button.disabled = false;
      var existing = button.querySelector(".spinner");
      if (existing) existing.remove();
    }
  }

  function makeButton(label, action) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn";
    button.dataset.variant = "ghost";
    button.dataset.size = "xs";
    button.textContent = label;
    if (action) button.dataset.act = action;
    return button;
  }

  function makeIconButton(inner, action, label) {
    var button = makeButton("", action);
    button.dataset.size = "icon-xs";
    button.setAttribute("aria-label", label);
    button.dataset.tooltip = label;
    button.dataset.side = "top";
    button.dataset.align = "start";
    button.innerHTML = svgIcon(inner, "size-3.5");
    return button;
  }

  /* ---------------- dialogs (replace window.confirm/prompt) ---------------- */

  var confirmDialog = document.getElementById("confirm-dialog");
  var confirmMessage = document.getElementById("confirm-message");
  var confirmOk = document.getElementById("confirm-ok");
  var confirmCancel = document.getElementById("confirm-cancel");
  var confirmSecondary = document.getElementById("confirm-secondary");
  var confirmCallback = null;
  var confirmSecondaryCallback = null;

  function askConfirm(message, onOk, options) {
    options = options || {};
    if (!confirmDialog || typeof confirmDialog.showModal !== "function") {
      if (window.confirm(message)) onOk();
      return;
    }
    confirmMessage.textContent = message;
    confirmOk.textContent = options.okLabel || "Potvrdiť";
    if (confirmSecondary) {
      if (options.secondaryLabel) {
        confirmSecondary.hidden = false;
        confirmSecondary.textContent = options.secondaryLabel;
        confirmSecondaryCallback = options.onSecondary || null;
      } else {
        confirmSecondary.hidden = true;
        confirmSecondaryCallback = null;
      }
    }
    confirmCallback = onOk;
    confirmDialog.showModal();
  }

  if (confirmOk) {
    confirmOk.addEventListener("click", function () {
      confirmDialog.close();
      var callback = confirmCallback;
      confirmCallback = null;
      confirmSecondaryCallback = null;
      if (callback) callback();
    });
  }
  if (confirmCancel) {
    confirmCancel.addEventListener("click", function () {
      confirmDialog.close();
      confirmCallback = null;
      confirmSecondaryCallback = null;
    });
  }
  if (confirmSecondary) {
    confirmSecondary.addEventListener("click", function () {
      confirmDialog.close();
      var callback = confirmSecondaryCallback;
      confirmCallback = null;
      confirmSecondaryCallback = null;
      if (callback) callback();
    });
  }

  var promptDialog = document.getElementById("prompt-dialog");
  var promptMessage = document.getElementById("prompt-message");
  var promptInput = document.getElementById("prompt-input");
  var promptOk = document.getElementById("prompt-ok");
  var promptCancel = document.getElementById("prompt-cancel");
  var promptCallback = null;

  function askPrompt(message, value, onOk) {
    if (!promptDialog || typeof promptDialog.showModal !== "function") {
      var result = window.prompt(message, value);
      if (result) onOk(result);
      return;
    }
    promptMessage.textContent = message;
    promptInput.value = value || "";
    promptCallback = onOk;
    promptDialog.showModal();
    promptInput.focus();
    promptInput.select();
  }

  if (promptOk) {
    promptOk.addEventListener("click", function () {
      var value = promptInput.value.trim();
      if (!value) return;
      promptDialog.close();
      var callback = promptCallback;
      promptCallback = null;
      if (callback) callback(value);
    });
  }
  if (promptCancel) {
    promptCancel.addEventListener("click", function () {
      promptDialog.close();
      promptCallback = null;
    });
  }

  [confirmDialog, promptDialog].forEach(function (dialog) {
    if (!dialog) return;
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
  });

  /* ---------------- collapsible filters ---------------- */

  var filtersBody = document.getElementById("filters-body");
  var filtersToggle = document.getElementById("filters-toggle");

  function setFiltersOpen(open) {
    if (!filtersBody || !filtersToggle) return;
    filtersBody.hidden = !open;
    filtersToggle.setAttribute("aria-expanded", String(open));
    filtersToggle.innerHTML = '<span class="filters-toggle-label">' + (open ? "Skryť" : "Zobraziť") +
      "</span>" + svgIcon(open ? ICON_CHEVRON_UP : ICON_CHEVRON_DOWN, "size-4");
  }
  if (filtersToggle) {
    filtersToggle.addEventListener("click", function () {
      setFiltersOpen(filtersBody.hidden);
    });
  }

  /* ---------------- removable filter chips ---------------- */

  var PRICE_TYPE_LABELS = {
    fixed: "Pevná cena", negotiable: "Dohodou", free: "Zadarmo",
    in_text: "V texte", offer: "Ponúknite"
  };
  var SORT_LABELS = {
    price: "Cena vzostupne", "-price": "Cena zostupne", date: "Dátum vzostupne",
    "-date": "Dátum zostupne", views: "Najviac zobrazení", relevance: "Relevancia"
  };

  function removeFilter(name, value) {
    var params = new URLSearchParams(window.location.search);
    if (value === undefined) {
      params.delete(name);
    } else {
      var kept = params.getAll(name).filter(function (v) { return v !== value; });
      params.delete(name);
      kept.forEach(function (v) { params.append(name, v); });
    }
    var query = params.toString();
    window.location.href = "/" + (query ? "?" + query : "");
  }

  function renderChips() {
    var container = document.getElementById("filter-chips");
    if (!container) return;
    clear(container);
    var params = new URLSearchParams(window.location.search);
    var chips = [];

    if (params.get("category")) {
      chips.push({ name: "category", label: "Kategória", value: catalogLabels[params.get("category")] || params.get("category") });
    }
    if (params.get("kw_all")) chips.push({ name: "kw_all", label: "Všetky", value: params.get("kw_all") });
    if (params.get("kw_any")) chips.push({ name: "kw_any", label: "Aspoň jedno", value: params.get("kw_any") });
    if (params.get("kw_exclude")) chips.push({ name: "kw_exclude", label: "Nezahŕňa", value: params.get("kw_exclude") });
    if (params.get("kw_desc")) chips.push({ name: "kw_desc", label: "V popise", value: params.get("kw_desc") });
    if (params.get("price_min")) chips.push({ name: "price_min", label: "Cena od", value: params.get("price_min") + " €" });
    if (params.get("price_max")) chips.push({ name: "price_max", label: "Cena do", value: params.get("price_max") + " €" });
    if (params.get("no_price")) chips.push({ name: "no_price", label: "Aj bez ceny", value: "" });
    params.getAll("price_type").forEach(function (pt) {
      chips.push({ name: "price_type", label: "Typ ceny", value: PRICE_TYPE_LABELS[pt] || pt, raw: pt });
    });
    if (params.get("city")) chips.push({ name: "city", label: "Mesto", value: params.get("city") });
    if (params.get("psc")) chips.push({ name: "psc", label: "PSČ", value: params.get("psc") });
    if (params.get("kraj")) chips.push({ name: "kraj", label: "Kraj", value: params.get("kraj") });
    if (params.get("include_unknown_location") === "0") chips.push({ name: "include_unknown_location", label: "Len potvrdené miesta", value: "" });
    if (params.get("days") === "0") chips.push({ name: "days", label: "Všetky obdobia", value: "" });
    if (params.get("new")) chips.push({ name: "new", label: "Iba nové", value: "" });
    if (params.get("fav")) chips.push({ name: "fav", label: "Iba obľúbené", value: "" });
    if (params.get("hidden")) chips.push({ name: "hidden", label: "Aj skryté", value: "" });
    if (params.get("added")) chips.push({ name: "added", label: "Pridané za", value: params.get("added") });
    if (params.get("sort") && params.get("sort") !== "-first_seen") {
      chips.push({ name: "sort", label: "Zoradené", value: SORT_LABELS[params.get("sort")] || params.get("sort") });
    }

    chips.forEach(function (chip) {
      var span = document.createElement("span");
      span.className = "chip";
      var label = document.createElement("span");
      label.className = "chip-label";
      label.textContent = chip.label;
      span.appendChild(label);
      if (chip.value) span.appendChild(document.createTextNode(" " + chip.value));
      var button = document.createElement("button");
      button.type = "button";
      button.setAttribute("aria-label", "Odstrániť filter: " + chip.label);
      button.innerHTML = svgIcon(ICON_X, "size-3");
      button.addEventListener("click", function () {
        removeFilter(chip.name, chip.raw);
      });
      span.appendChild(button);
      container.appendChild(span);
    });
  }

  renderChips();

  /* ---------------- category combobox ---------------- */

  var searchInput = document.getElementById("cat-search");
  var valueInput = document.getElementById("cat-value");
  var listBox = document.getElementById("cat-list");
  var scrapeBtn = document.getElementById("scrape-btn");
  var backfillBtn = document.getElementById("backfill-btn");
  var watchBtn = document.getElementById("watch-current");

  var options = [{ key: "", label: ALL_LABEL, special: true }];
  var sections = catalog.filter(function (c) { return !c.parent; });
  var subsByParent = {};
  catalog.filter(function (c) { return c.parent; }).forEach(function (c) {
    (subsByParent[c.parent] = subsByParent[c.parent] || []).push(c);
  });
  sections.forEach(function (section) {
    options.push({ key: section.key, label: section.label, group: true });
    (subsByParent[section.key] || []).forEach(function (sub) {
      options.push({ key: sub.key, label: sub.label });
    });
  });

  var visibleOptions = [];
  var activeIndex = -1;

  function setActive(index) {
    if (activeIndex >= 0 && visibleOptions[activeIndex]) {
      visibleOptions[activeIndex].classList.remove("active");
    }
    activeIndex = index;
    if (activeIndex >= 0 && visibleOptions[activeIndex]) {
      visibleOptions[activeIndex].classList.add("active");
      visibleOptions[activeIndex].scrollIntoView({ block: "nearest" });
    }
  }

  function renderOptions(query) {
    var needle = fold(query);
    clear(listBox);
    visibleOptions = [];
    var shown = 0;
    options.forEach(function (opt) {
      var haystack = fold(opt.label) + " " + fold(opt.key);
      if (needle && haystack.indexOf(needle) === -1) return;
      var div = document.createElement("div");
      div.className = "combo-option" + (opt.group ? " combo-group-option" : "");
      div.dataset.key = opt.key;
      div.setAttribute("role", "option");
      div.textContent = opt.label;
      var count = countLabel(opt.key);
      if (count) {
        var span = document.createElement("span");
        span.className = "count";
        span.textContent = count;
        div.appendChild(span);
      }
      div.addEventListener("mousedown", function (event) {
        event.preventDefault();
        choose(opt);
      });
      listBox.appendChild(div);
      visibleOptions.push(div);
      shown += 1;
    });
    if (!shown) {
      var none = document.createElement("div");
      none.className = "combo-option";
      none.textContent = "Nic sa nenašlo";
      listBox.appendChild(none);
    }
    activeIndex = -1;
    listBox.hidden = false;
    if (searchInput) searchInput.setAttribute("aria-expanded", "true");
  }

  function closeOptions() {
    listBox.hidden = true;
    activeIndex = -1;
    if (searchInput) searchInput.setAttribute("aria-expanded", "false");
  }

  function choose(opt) {
    valueInput.value = opt.key;
    searchInput.value = opt.label;
    closeOptions();
    if (scrapeBtn) scrapeBtn.disabled = !opt.key;
    if (backfillBtn) backfillBtn.disabled = !opt.key;
    if (watchBtn) watchBtn.disabled = !opt.key;
  }

  if (searchInput && listBox) {
    searchInput.addEventListener("focus", function () { renderOptions(""); });
    searchInput.addEventListener("input", function () { renderOptions(searchInput.value); });
    searchInput.addEventListener("blur", function () {
      setTimeout(closeOptions, 150);
    });
    searchInput.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (listBox.hidden) { renderOptions(searchInput.value); return; }
        var delta = event.key === "ArrowDown" ? 1 : -1;
        var next = activeIndex + delta;
        if (next < 0) next = visibleOptions.length - 1;
        if (next >= visibleOptions.length) next = 0;
        setActive(next);
      } else if (event.key === "Enter") {
        if (activeIndex >= 0 && visibleOptions[activeIndex]) {
          event.preventDefault();
          visibleOptions[activeIndex].dispatchEvent(new MouseEvent("mousedown"));
        }
      } else if (event.key === "Escape") {
        closeOptions();
      }
    });
    document.addEventListener("click", function (event) {
      if (!listBox.contains(event.target) && event.target !== searchInput) closeOptions();
    });
  }

  /* ---------------- status helpers ---------------- */

  var statusBox = document.getElementById("scrape-status");
  var deepCheckbox = document.getElementById("deep-scrape");
  var singleTimer = null;
  var scrapeProgress = document.getElementById("scrape-progress");

  function setStatus(text, kind) {
    if (!statusBox) return;
    statusBox.textContent = text;
    statusBox.className = "status-line" + (kind ? " " + kind : "");
  }

  function setSideStatus(id, text, kind) {
    var box = document.getElementById(id);
    if (!box) return;
    box.textContent = text;
    box.className = "side-status" + (kind ? " " + kind : "");
  }

  function showProgress(el, show) {
    if (el) el.hidden = !show;
  }

  /* ---------------- single scrape ---------------- */

  function startScrape() {
    var key = valueInput ? valueInput.value : "";
    if (!key) { setStatus("Najprv vyberte konkrétnu kategóriu.", "warn"); return; }
    setStatus("Spúšťam aktualizáciu…");
    setBusy(scrapeBtn, true);
    showProgress(scrapeProgress, true);
    apiJSON("POST", "/api/scrape", { category_key: key, full: !!(deepCheckbox && deepCheckbox.checked) })
      .then(function (result) {
        if (result.status === 409) {
          setStatus(result.data.error || "Aktualizácia už prebieha.", "warn");
          setBusy(scrapeBtn, false);
          showProgress(scrapeProgress, false);
          return;
        }
        if (result.status !== 202 && result.status !== 200) {
          setStatus("Chyba pri spustení: " + (result.data.error || result.status), "warn");
          setBusy(scrapeBtn, false);
          showProgress(scrapeProgress, false);
          return;
        }
        singleTimer = setInterval(pollSingle, 1000);
      }).catch(function (error) {
        setStatus("Chyba siete: " + error, "warn");
        setBusy(scrapeBtn, false);
        showProgress(scrapeProgress, false);
      });
  }

  function pollSingle() {
    fetch("/api/scrape/status").then(function (r) { return r.json(); }).then(function (state) {
      if (state.state === "running") {
        setStatus("Prebieha: " + state.category + " – strán: " + state.pages_fetched +
                  ", nájdených: " + state.listings_seen + ", nových: " + state.listings_new);
        return;
      }
      clearInterval(singleTimer);
      singleTimer = null;
      showProgress(scrapeProgress, false);
      if (state.state === "finished") {
        setStatus("Hotovo (" + state.stop_reason + "). Načítavam výsledky…", "ok");
        document.getElementById("filters").submit();
      } else if (state.state === "blocked") {
        setStatus("STOP: server vrátil 403/429. Skúste neskôr. (" + state.stop_reason + ")", "warn");
        setBusy(scrapeBtn, false);
      } else if (state.state === "failed") {
        setStatus("Zlyhalo: " + (state.error || state.stop_reason), "warn");
        setBusy(scrapeBtn, false);
      } else {
        setStatus("");
        setBusy(scrapeBtn, false);
      }
    }).catch(function (error) {
      clearInterval(singleTimer);
      showProgress(scrapeProgress, false);
      setStatus("Chyba pri zisťovaní stavu: " + error, "warn");
      setBusy(scrapeBtn, false);
    });
  }

  if (scrapeBtn) scrapeBtn.addEventListener("click", startScrape);

  /* ---------------- full-category backfill ---------------- */

  var backfillPanel = document.getElementById("backfill-panel");
  var backfillText = document.getElementById("backfill-text");
  var backfillProgress = document.getElementById("backfill-progress");
  var backfillBar = backfillProgress ? backfillProgress.querySelector("span") : null;
  var backfillCancel = document.getElementById("backfill-cancel");
  var backfillWarning = document.getElementById("backfill-warning");
  var backfillSpinner = document.getElementById("backfill-spinner");
  var backfillPercent = document.getElementById("backfill-percent");
  var backfillEta = document.getElementById("backfill-eta");
  var backfillTimer = null;
  var backfillRunning = false;
  // Only auto-reload after a backfill that THIS page actually saw running.
  // The server keeps the last state (e.g. "complete"), so reloading on a stale
  // "complete" would make the page reload forever.
  var backfillSawRunning = false;

  function fmtEta(seconds) {
    if (seconds === null || seconds === undefined) return "neznámy";
    var m = Math.round(seconds / 60);
    if (m <= 0) return "menej ako minútu";
    return m === 1 ? "1 min" : m + " min";
  }

  function renderBackfill(state) {
    if (!backfillPanel) return;
    state = state || { state: "idle" };
    var running = state.state === "running";
    backfillRunning = running;
    if (running) backfillSawRunning = true;
    var finished = state.state === "complete" || state.state === "paused" ||
      state.state === "blocked" || state.state === "failed";

    if (state.state === "idle") {
      backfillPanel.hidden = true;
      if (backfillTimer) { clearInterval(backfillTimer); backfillTimer = null; }
      return;
    }
    if (state.state === "complete" && !backfillSawRunning) {
      // The backfill finished in an earlier page session - nothing to show
      // and, crucially, nothing to reload.
      backfillPanel.hidden = true;
      if (backfillTimer) { clearInterval(backfillTimer); backfillTimer = null; }
      return;
    }
    backfillPanel.hidden = false;
    if (backfillCancel) backfillCancel.hidden = !running;
    if (backfillSpinner) backfillSpinner.hidden = !running;
    if (backfillBtn) backfillBtn.disabled = running;

    var estimated = state.pages_estimated;
    var done = state.pages_done || 0;
    var percent = estimated ? Math.min(100, Math.round(done * 100 / estimated)) : null;
    var showProgress = running || state.state === "paused" || state.state === "complete";

    if (backfillProgress) {
      backfillProgress.hidden = !showProgress;
      if (backfillBar) {
        backfillBar.style.display = "block";
        if (running && percent === null) {
          // Unknown total: show an indeterminate, pulsing bar.
          backfillBar.style.width = "100%";
          backfillBar.classList.add("animate-pulse");
        } else {
          backfillBar.classList.remove("animate-pulse");
          backfillBar.style.width = (percent === null ? 0 : percent) + "%";
        }
      }
    }
    if (backfillPercent) {
      backfillPercent.hidden = !(showProgress && percent !== null);
      backfillPercent.textContent = percent !== null ? percent + " %" : "";
    }
    if (backfillEta) {
      backfillEta.hidden = !running;
      if (running) {
        var eta = state.eta_seconds;
        var remaining = estimated ? Math.max(0, estimated - done) : null;
        backfillEta.textContent = (eta === null || eta === undefined)
          ? "Odhadovaný čas: neznámy"
          : "Odhadovaný čas: ~" + fmtEta(eta) +
            (remaining !== null ? " (zostáva " + remaining + " strán)" : "");
      }
    }

    if (running) {
      var text = "Sťahujem „" + (catalogLabels[state.category] || state.category) + "“ – strán " +
        done + (estimated ? " / ~" + estimated : "") + ", nájdených " + (state.listings_seen || 0) +
        ", nových " + (state.listings_new || 0);
      if (backfillText) backfillText.textContent = text;
    } else if (state.state === "complete") {
      if (backfillText) backfillText.textContent =
        "Hotovo. Stiahnutých strán: " + done + ", nových inzerátov: " + (state.listings_new || 0) +
        (backfillSawRunning ? ". Načítavam výsledky…" : "");
    } else if (state.state === "paused") {
      if (backfillText) backfillText.textContent =
        "Pozastavené (" + (state.stop_reason || "") + "). Kategória ešte nie je kompletná – môžete pokračovať od strany " +
        (state.next_page || done + 1) + ".";
    } else if (state.state === "failed") {
      if (backfillText) backfillText.textContent =
        "Zlyhalo: " + (state.error || state.stop_reason || "neznáma chyba");
    }

    if (backfillWarning) {
      var blocked = state.state === "blocked";
      backfillWarning.hidden = !blocked;
      var warnText = backfillWarning.querySelector("p");
      if (warnText) warnText.textContent = blocked
        ? "STOP: server vrátil 403/429. Sťahovanie bolo prerušené. Skúste neskôr – pokračuje sa od strany " +
          (state.next_page || done + 1) + "."
        : "";
    }

    if (running && !backfillTimer) {
      backfillTimer = setInterval(pollBackfill, 1000);
    }
    if (finished && backfillTimer) {
      clearInterval(backfillTimer);
      backfillTimer = null;
      refreshSaved();
      refreshWatched();
    }
    if (state.state === "complete" && backfillSawRunning) {
      backfillSawRunning = false;
      setTimeout(function () {
        var form = document.getElementById("filters");
        if (form) form.submit();
      }, 800);
    }
  }

  function pollBackfill() {
    fetch("/api/backfill/status").then(function (r) { return r.json(); })
      .then(renderBackfill)
      .catch(function () {});
  }

  function doBackfill(key, restart) {
    backfillSawRunning = false;
    setBusy(backfillBtn, true);
    apiJSON("POST", "/api/backfill/start", { category_key: key, restart: !!restart })
      .then(function (result) {
        setBusy(backfillBtn, false);
        if (result.status === 409) { setStatus(result.data.error || "Prebieha iná úloha.", "warn"); return; }
        if (result.status !== 202) { setStatus("Chyba: " + (result.data.error || result.status), "warn"); return; }
        // renderBackfill starts the polling timer when the state is running.
        renderBackfill(result.data.status || { state: "running", category: key });
      })
      .catch(function (error) { setBusy(backfillBtn, false); setStatus("Chyba siete: " + error, "warn"); });
  }

  function openBackfillDialog() {
    var key = valueInput ? valueInput.value : "";
    if (!key) { setStatus("Najprv vyberte konkrétnu kategóriu.", "warn"); return; }
    setBusy(backfillBtn, true);
    fetch("/api/coverage?category_key=" + encodeURIComponent(key))
      .then(function (r) { return r.json().then(function (data) { return { status: r.status, data: data }; }); })
      .then(function (result) {
        setBusy(backfillBtn, false);
        if (result.status !== 200) { setStatus(result.data.error || "Chyba.", "warn"); return; }
        var cov = result.data.coverage || {};
        var estimate = cov.estimate || cov.total_estimate || cov.stored || 0;
        var pages = Math.max(1, Math.ceil(estimate / 20));
        var cap = appConfig.backfill_max_pages_per_run || 1000;
        var delay = appConfig.request_delay_seconds || 1.5;
        var minutes = Math.max(1, Math.ceil(pages * delay / 60));
        var message = "Stiahne sa približne " + pages + " stránok (~" + estimate +
          " inzerátov), potrvá aspoň " + minutes + " minút.";
        if (pages > cap) {
          message += " Kategória je väčšia než limit na jeden beh (" + cap +
            " strán), stiahne sa na viac behov.";
        }
        message += " Pokračovať?";
        var resumable = cov.backfill_status === "paused" || cov.backfill_status === "blocked";
        if (resumable) {
          var from = cov.next_page || 1;
          askConfirm(message, function () { doBackfill(key, false); }, {
            okLabel: "Pokračovať (od strany " + from + ")",
            secondaryLabel: "Začať odznova",
            onSecondary: function () { doBackfill(key, true); }
          });
        } else {
          askConfirm(message, function () { doBackfill(key, false); });
        }
      })
      .catch(function (error) { setBusy(backfillBtn, false); setStatus("Chyba siete: " + error, "warn"); });
  }

  if (backfillBtn) backfillBtn.addEventListener("click", openBackfillDialog);
  if (backfillCancel) {
    backfillCancel.addEventListener("click", function () {
      apiJSON("POST", "/api/backfill/cancel", {}).then(function (result) {
        renderBackfill(result.data.status || {});
      });
    });
  }
  // Restore the panel (and the polling timer) after a reload while a
  // backfill is already running on the server.
  pollBackfill();

  var filtersForm = document.getElementById("filters");
  var filtersSubmit = document.getElementById("filters-submit");
  var resultsSkeleton = document.getElementById("results-skeleton");
  if (filtersForm) {
    filtersForm.addEventListener("submit", function () {
      if (resultsSkeleton) resultsSkeleton.classList.remove("hidden");
      setBusy(filtersSubmit, true);
    });
  }

  /* ---------------- saved searches ---------------- */

  var savedList = document.getElementById("saved-list");
  var savedForm = document.getElementById("save-search-form");
  var savedName = document.getElementById("save-search-name");
  var savedEmpty = document.getElementById("saved-empty");

  function savedHref(item) {
    if (item.href) return item.href;
    return "/?" + (item.category_key ? "category=" + encodeURIComponent(item.category_key) + "&" : "") +
      "sort=" + encodeURIComponent(item.sort || "-first_seen");
  }

  function renderSaved(items) {
    if (!savedList) return;
    clear(savedList);
    (items || []).forEach(function (item) {
      var li = document.createElement("li");
      li.className = "side-item";
      li.dataset.id = item.id;
      li.dataset.category = item.category_key || "";

      var row = document.createElement("div");
      row.className = "side-row";
      var link = document.createElement("a");
      link.className = "side-link";
      link.href = savedHref(item);
      link.title = item.name;
      link.textContent = item.name;
      row.appendChild(link);
      if (item.new_count) {
        var badge = document.createElement("span");
        badge.className = "badge border-transparent bg-success text-success-foreground";
        badge.title = "nové inzeráty";
        badge.textContent = item.new_count;
        row.appendChild(badge);
      }
      li.appendChild(row);

      var sub = document.createElement("div");
      sub.className = "side-sub";
      sub.textContent = (item.category_label || "") + " · " + (item.total || 0) + " inzerátov";
      li.appendChild(sub);

      var actions = document.createElement("div");
      actions.className = "side-actions";
      var newLink = document.createElement("a");
      newLink.className = "btn";
      newLink.dataset.variant = "ghost";
      newLink.dataset.size = "icon-xs";
      newLink.href = savedHref(item) + (savedHref(item).indexOf("?") >= 0 ? "&" : "?") + "new=1";
      newLink.setAttribute("aria-label", "Nové inzeráty");
      newLink.dataset.tooltip = "Nové";
      newLink.dataset.side = "top";
      newLink.dataset.align = "start";
      newLink.innerHTML = svgIcon(ICON_SPARKLES, "size-3.5");
      actions.appendChild(newLink);
      actions.appendChild(makeIconButton(ICON_PENCIL, "rename", "Premenovať"));
      actions.appendChild(makeIconButton(ICON_REFRESH_CW, "update", "Aktualizovať filtre"));
      actions.appendChild(makeIconButton(ICON_TRASH_2, "delete", "Zmazať"));
      li.appendChild(actions);
      savedList.appendChild(li);
    });
    if (savedEmpty) savedEmpty.hidden = !!(items && items.length);
  }

  function refreshSaved() {
    return fetch("/api/saved-searches").then(function (r) { return r.json(); })
      .then(function (data) { renderSaved(data.saved_searches || []); });
  }

  if (savedList) {
    savedList.addEventListener("click", function (event) {
      var button = event.target.closest("button[data-act]");
      if (!button) return;
      var li = button.closest(".side-item");
      var id = li && li.dataset.id;
      if (!id) return;
      var action = button.dataset.act;
      var name = li.querySelector(".side-link").textContent;
      if (action === "rename") {
        askPrompt("Nový názov hľadania:", name, function (next) {
          apiJSON("PUT", "/api/saved-searches/" + id, { name: next }).then(function (result) {
            if (result.status !== 200) { setSideStatus("saved-status", result.data.error || "Chyba.", "warn"); return; }
            setSideStatus("saved-status", "Premenované.", "ok");
            refreshSaved();
          });
        });
      } else if (action === "update") {
        askConfirm("Prepísať filtre hľadania „" + name + "“ aktuálnymi filtrami?", function () {
          apiJSON("PUT", "/api/saved-searches/" + id, {
            filters: CURRENT_FILTERS, sort: CURRENT_SORT, category_key: appConfig.category || null
          }).then(function (result) {
            if (result.status !== 200) { setSideStatus("saved-status", result.data.error || "Chyba.", "warn"); return; }
            setSideStatus("saved-status", "Filtre aktualizované.", "ok");
            refreshSaved();
          });
        });
      } else if (action === "delete") {
        askConfirm("Zmazať hľadanie „" + name + "“?", function () {
          apiJSON("DELETE", "/api/saved-searches/" + id).then(function (result) {
            if (result.status !== 200) { setSideStatus("saved-status", result.data.error || "Chyba.", "warn"); return; }
            setSideStatus("saved-status", "Zmazané.", "ok");
            refreshSaved();
          });
        });
      }
    });
  }

  var saveOpen = document.getElementById("save-search-open");
  if (saveOpen && savedForm) {
    saveOpen.addEventListener("click", function () {
      savedForm.classList.remove("hidden");
      saveOpen.classList.add("hidden");
      if (savedName) { savedName.value = ""; savedName.focus(); }
    });
  }
  var saveCancel = document.getElementById("save-search-cancel");
  if (saveCancel && savedForm) {
    saveCancel.addEventListener("click", function () {
      savedForm.classList.add("hidden");
      if (saveOpen) saveOpen.classList.remove("hidden");
    });
  }
  if (savedForm) {
    savedForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var name = savedName ? savedName.value.trim() : "";
      if (!name) { setSideStatus("saved-status", "Zadajte názov.", "warn"); return; }
      var submitButton = savedForm.querySelector('button[type="submit"]');
      setBusy(submitButton, true);
      apiJSON("POST", "/api/saved-searches", {
        name: name,
        category_key: appConfig.category || null,
        filters: CURRENT_FILTERS,
        sort: CURRENT_SORT
      }).then(function (result) {
        setBusy(submitButton, false);
        if (result.status !== 201) {
          setSideStatus("saved-status", result.data.error || "Uloženie zlyhalo.", "warn");
          return;
        }
        setSideStatus("saved-status", "Uložené.", "ok");
        savedForm.classList.add("hidden");
        if (saveOpen) saveOpen.classList.remove("hidden");
        refreshSaved();
      });
    });
  }

  /* ---------------- watched categories ---------------- */

  var watchedList = document.getElementById("watched-list");
  var watchedEmpty = document.getElementById("watched-empty");
  var overlapList = document.getElementById("overlap-warnings");

  function renderWatched(items, overlaps) {
    if (watchedList) {
      clear(watchedList);
      (items || []).forEach(function (item) {
        var li = document.createElement("li");
        li.className = "side-item";
        li.dataset.key = item.category_key;
        var row = document.createElement("div");
        row.className = "side-row";
        var label = document.createElement("a");
        label.className = "side-link";
        label.href = "/?category=" + encodeURIComponent(item.category_key);
        label.title = "Zobraziť kategóriu " + item.category_key;
        label.textContent = item.label;
        row.appendChild(label);
        var unwatch = makeButton("", "unwatch");
        unwatch.dataset.size = "icon-xs";
        unwatch.setAttribute("aria-label", "Odstrániť zo sledovaných");
        unwatch.innerHTML = svgIcon(ICON_X, "size-3.5");
        row.appendChild(unwatch);
        li.appendChild(row);
        var sub = document.createElement("div");
        sub.className = "side-sub";
        sub.textContent = (item.total || 0) + " / " + (item.new || 0) + " nových · " +
          (item.last_run || "nikdy");
        li.appendChild(sub);
        watchedList.appendChild(li);
      });
      if (watchedEmpty) watchedEmpty.hidden = !!(items && items.length);
    }
    if (overlapList) {
      clear(overlapList);
      (overlaps || []).forEach(function (item) {
        var li = document.createElement("li");
        li.textContent = item.message;
        overlapList.appendChild(li);
      });
    }
  }

  function refreshWatched() {
    return fetch("/api/watched").then(function (r) { return r.json(); })
      .then(function (data) { renderWatched(data.watched || [], data.overlaps || []); });
  }

  if (watchedList) {
    watchedList.addEventListener("click", function (event) {
      var button = event.target.closest("button[data-act='unwatch']");
      if (!button) return;
      var li = button.closest(".side-item");
      var key = li && li.dataset.key;
      if (!key) return;
      apiJSON("DELETE", "/api/watched", { category_key: key }).then(function (result) {
        if (result.status !== 200) { setSideStatus("watched-status", result.data.error || "Chyba.", "warn"); return; }
        setSideStatus("watched-status", "Odstránené.", "ok");
        refreshWatched();
      });
    });
  }

  if (watchBtn) {
    watchBtn.addEventListener("click", function () {
      var key = valueInput ? valueInput.value : "";
      if (!key) { setSideStatus("watched-status", "Najprv vyberte kategóriu.", "warn"); return; }
      setBusy(watchBtn, true);
      apiJSON("POST", "/api/watched", { category_key: key }).then(function (result) {
        setBusy(watchBtn, false);
        if (result.status !== 201) { setSideStatus("watched-status", result.data.error || "Chyba.", "warn"); return; }
        setSideStatus("watched-status", "Kategória sa sleduje.", "ok");
        refreshWatched();
      });
    });
  }

  /* ---------------- batch update ---------------- */

  var batchStart = document.getElementById("batch-start");
  var batchCancel = document.getElementById("batch-cancel");
  var batchForce = document.getElementById("batch-force");
  var batchDeep = document.getElementById("batch-deep");
  var batchList = document.getElementById("batch-list");
  var batchWarning = document.getElementById("batch-warning");
  var batchProgress = document.getElementById("batch-progress");
  var batchTimer = null;

  function renderBatch(state) {
    var running = state && state.state === "running";
    if (batchCancel) batchCancel.hidden = !running;
    if (batchStart) batchStart.disabled = running;
    showProgress(batchProgress, running);
    if (batchList) {
      clear(batchList);
      (state && state.categories ? state.categories : []).forEach(function (cat) {
        var li = document.createElement("li");
        li.className = "batch-item batch-" + cat.status;
        var icon = document.createElement("span");
        icon.className = "batch-icon";
        icon.innerHTML = svgIcon(BATCH_ICONS[cat.status] || BATCH_ICONS.pending, "size-3.5");
        li.appendChild(icon);
        var text = document.createElement("span");
        text.textContent = cat.label + " – " + cat.status +
          (cat.pages_fetched ? " (" + cat.pages_fetched + " strán, " + cat.listings_new + " nových)" : "");
        li.appendChild(text);
        batchList.appendChild(li);
      });
      batchList.hidden = !(state && state.categories && state.categories.length);
    }
    if (batchWarning) {
      var warn = state && (state.state === "blocked" || state.state === "failed");
      batchWarning.hidden = !warn;
      var warnText = batchWarning.querySelector("p");
      if (warnText) warnText.textContent = warn ? (state.message || "Dávka zlyhala.") : "";
    }
    var statusEl = document.getElementById("batch-status");
    if (statusEl) {
      if (running) {
        statusEl.textContent = "Prebieha… strán: " + (state.pages_fetched || 0) +
          ", nových: " + (state.listings_new || 0);
        statusEl.className = "status-line";
      } else if (state && state.state === "finished") {
        statusEl.textContent = "Hotovo. Nových: " + (state.listings_new || 0);
        statusEl.className = "status-line ok";
      } else if (state && state.state === "cancelled") {
        statusEl.textContent = "Zrušené.";
        statusEl.className = "status-line warn";
      } else if (state && state.state === "blocked") {
        statusEl.textContent = "Prerušené (blokované).";
        statusEl.className = "status-line warn";
      } else if (state && state.state === "failed") {
        statusEl.textContent = "Zlyhalo.";
        statusEl.className = "status-line warn";
      } else {
        statusEl.textContent = "";
        statusEl.className = "status-line";
      }
    }
    if (!running && batchTimer) {
      clearInterval(batchTimer);
      batchTimer = null;
      refreshSaved();
      refreshWatched();
    }
  }

  function pollBatch() {
    fetch("/api/batch/status").then(function (r) { return r.json(); })
      .then(renderBatch)
      .catch(function () {});
  }

  if (batchStart) {
    batchStart.addEventListener("click", function () {
      setBusy(batchStart, true);
      apiJSON("POST", "/api/batch/start", {
        force: !!(batchForce && batchForce.checked),
        deep: !!(batchDeep && batchDeep.checked)
      }).then(function (result) {
        setBusy(batchStart, false);
        if (result.status === 409) { setStatus(result.data.error || "Prebieha iná úloha.", "warn"); return; }
        if (result.status !== 202) { setStatus("Chyba: " + (result.data.error || result.status), "warn"); return; }
        renderBatch(result.data.status || { state: "running" });
        batchTimer = setInterval(pollBatch, 1000);
      }).catch(function (error) { setBusy(batchStart, false); setStatus("Chyba siete: " + error, "warn"); });
    });
  }

  if (batchCancel) {
    batchCancel.addEventListener("click", function () {
      apiJSON("POST", "/api/batch/cancel", {}).then(function (result) {
        renderBatch(result.data.status || {});
      });
    });
  }

  /* ---------------- row actions ---------------- */

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (response) { return response.json(); });
  }

  var region = document.getElementById("listings-region");
  if (region) {
    region.addEventListener("click", function (event) {
      var button = event.target.closest(".act");
      if (!button) return;
      var id = button.dataset.id;
      var action = button.dataset.action;
      postJSON("/api/listings/" + id + "/" + action, {}).then(function (data) {
        var rows = region.querySelectorAll('.listing-row[data-id="' + id + '"]');
        Array.prototype.forEach.call(rows, function (row) {
          if (action === "seen") {
            row.classList.add("seen-row");
            var badge = row.querySelector(".badge.bg-success");
            if (badge) badge.remove();
          } else if (action === "hide") {
            row.classList.toggle("hidden-row", !!data.is_hidden);
          }
        });
        if (action === "favorite") {
          var favButtons = region.querySelectorAll('.act.fav[data-id="' + id + '"]');
          Array.prototype.forEach.call(favButtons, function (fav) {
            fav.classList.toggle("on", !!data.is_favorite);
          });
        }
      }).catch(function (error) { setStatus("Akcia zlyhala: " + error, "warn"); });
    });
  }

  /* ---------------- bulk actions ---------------- */

  function markRowsSeen(rows) {
    Array.prototype.forEach.call(rows, function (row) {
      row.classList.add("seen-row");
      var badge = row.querySelector(".badge.bg-success");
      if (badge) badge.remove();
    });
  }

  var bulkShown = document.getElementById("bulk-shown");
  if (bulkShown && region) {
    bulkShown.addEventListener("click", function () {
      var seen = {};
      var ids = [];
      Array.prototype.forEach.call(region.querySelectorAll(".listing-row[data-id]"), function (row) {
        if (!seen[row.dataset.id]) { seen[row.dataset.id] = true; ids.push(row.dataset.id); }
      });
      if (!ids.length) return;
      setBusy(bulkShown, true);
      postJSON("/api/mark-seen", { ids: ids }).then(function () {
        setBusy(bulkShown, false);
        markRowsSeen(region.querySelectorAll(".listing-row[data-id]"));
        setStatus("Označené ako videné: " + ids.length, "ok");
      }).catch(function (error) { setBusy(bulkShown, false); setStatus("Akcia zlyhala: " + error, "warn"); });
    });
  }

  var bulkCategory = document.getElementById("bulk-category");
  if (bulkCategory) {
    bulkCategory.addEventListener("click", function () {
      var key = bulkCategory.dataset.category;
      if (!key) return;
      setBusy(bulkCategory, true);
      postJSON("/api/mark-seen", { category_key: key }).then(function (data) {
        setStatus("Označená celá kategória (" + data.updated + ").", "ok");
        window.location.reload();
      }).catch(function (error) { setBusy(bulkCategory, false); setStatus("Akcia zlyhala: " + error, "warn"); });
    });
  }

  /* ---------------- location filter ---------------- */

  var unknownCheckbox = document.getElementById("inc-unknown");
  var unknownHidden = document.getElementById("inc-unknown-value");
  if (unknownCheckbox && unknownHidden) {
    unknownCheckbox.addEventListener("change", function () {
      // The hidden field is only submitted when the checkbox is OFF, so the
      // flag appears in the URL only when it differs from the default (on).
      unknownHidden.disabled = unknownCheckbox.checked;
    });
  }

  var ageCheckbox = document.getElementById("age-limit");
  var ageHidden = document.getElementById("age-limit-value");
  if (ageCheckbox && ageHidden) {
    ageCheckbox.addEventListener("change", function () {
      // days=0 (submitted only when unchecked) turns the age window off.
      ageHidden.disabled = ageCheckbox.checked;
    });
  }

  /* ---------------- sidebar toggle ---------------- */

  var sidebarEl = document.getElementById("sidebar");
  var sidebarToggle = document.getElementById("sidebar-toggle");
  var sidebarClose = document.getElementById("sidebar-close");
  function syncSidebarToggle() {
    if (sidebarToggle && sidebarEl) {
      sidebarToggle.setAttribute("aria-expanded", String(sidebarEl.getAttribute("aria-hidden") !== "true"));
    }
  }
  if (sidebarToggle && sidebarEl) {
    sidebarToggle.addEventListener("click", function () {
      if (typeof sidebarEl.toggle === "function") sidebarEl.toggle();
      syncSidebarToggle();
    });
  }
  if (sidebarClose && sidebarEl) {
    sidebarClose.addEventListener("click", function () {
      if (typeof sidebarEl.close === "function") sidebarEl.close();
      syncSidebarToggle();
    });
  }

  /* ---------------- scheduler (automatic check) ---------------- */

  var schedData = readJSON("scheduler-data") || {};
  var schedEnabled = document.getElementById("sched-enabled");
  var schedPreset = document.getElementById("sched-interval-preset");
  var schedCustomWrap = document.getElementById("sched-custom-wrap");
  var schedCustom = document.getElementById("sched-interval-custom");
  var schedCountdown = document.getElementById("sched-countdown");
  var schedLast = document.getElementById("sched-last");
  var schedRunNow = document.getElementById("sched-run-now");
  var schedBanner = document.getElementById("sched-banner");
  var schedSeconds = null;
  var schedStateName = schedData.state || "off";
  var lastSchedulerRun = schedData.last_run_finished || null;
  var SCHED_PRESETS = ["30", "60", "120", "360", "720", "1440"];

  function fmtMinutes(seconds) {
    var m = Math.round(seconds / 60);
    if (m <= 0) return "menej ako minútu";
    return m === 1 ? "1 min" : m + " min";
  }

  function relativeTime(iso) {
    var then = Date.parse(iso);
    if (isNaN(then)) return "neznámy čas";
    var diff = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (diff < 60) return "pred chvíľou";
    var m = Math.round(diff / 60);
    if (m < 60) return "pred " + m + " min";
    var h = Math.round(m / 60);
    if (h < 24) return "pred " + h + " h";
    return "pred " + Math.round(h / 24) + " d";
  }

  function updateCountdown() {
    if (!schedCountdown) return;
    if (schedStateName === "off") { schedCountdown.textContent = "Vypnutá"; return; }
    if (schedStateName === "paused_blocked") { schedCountdown.textContent = "Pozastavená (blokované)"; return; }
    if (schedStateName === "paused_failures") { schedCountdown.textContent = "Pozastavená (chyby)"; return; }
    if (schedStateName === "nothing_to_watch") { schedCountdown.textContent = "Nie je čo sledovať"; return; }
    if (schedStateName === "running") { schedCountdown.textContent = "Prebieha kontrola…"; return; }
    if (schedSeconds === null || schedSeconds === undefined) { schedCountdown.textContent = ""; return; }
    schedCountdown.textContent = "Ďalšia kontrola o " + fmtMinutes(schedSeconds);
  }

  function renderScheduler(state) {
    if (!state) return;
    schedStateName = state.state || "off";
    schedSeconds = state.seconds_until_next_run;
    if (schedEnabled) schedEnabled.checked = !!state.enabled;
    if (schedPreset) {
      var value = String(state.interval_minutes);
      if (SCHED_PRESETS.indexOf(value) >= 0) {
        schedPreset.value = value;
        if (schedCustomWrap) schedCustomWrap.classList.add("hidden");
      } else {
        schedPreset.value = "custom";
        if (schedCustomWrap) schedCustomWrap.classList.remove("hidden");
        if (schedCustom) schedCustom.value = value;
      }
    }
    if (schedLast) {
      var result = state.last_result;
      if (result && state.last_run_finished) {
        schedLast.textContent = "Naposledy: " + relativeTime(state.last_run_finished) +
          ", " + (result.new_listings || 0) + " nových";
      } else {
        schedLast.textContent = "Zatiaľ žiadna kontrola.";
      }
    }
    if (schedBanner) {
      var bannerText = schedBanner.querySelector("p");
      if (schedStateName === "paused_blocked") {
        schedBanner.hidden = false;
        schedBanner.dataset.variant = "destructive";
        if (bannerText) bannerText.textContent = "Bazoš dočasne zablokoval prístup. Automatická kontrola je vypnutá. Skúste neskôr a zapnite ju ručne.";
      } else if (schedStateName === "paused_failures") {
        schedBanner.hidden = false;
        schedBanner.dataset.variant = "warning";
        if (bannerText) bannerText.textContent = "Automatická kontrola bola pozastavená po " +
          (state.consecutive_failures || 0) + " neúspešných behoch. Zapnite ju ručne.";
      } else {
        schedBanner.hidden = true;
        if (bannerText) bannerText.textContent = "";
      }
    }
    updateCountdown();
    if (state.last_run_finished && state.last_run_finished !== lastSchedulerRun) {
      lastSchedulerRun = state.last_run_finished;
      refreshSaved();
      refreshWatched();
    }
  }

  function refreshScheduler() {
    fetch("/api/scheduler").then(function (r) { return r.json(); })
      .then(renderScheduler).catch(function () {});
  }

  if (schedEnabled) {
    schedEnabled.addEventListener("change", function () {
      var action = schedEnabled.checked ? "enable" : "disable";
      apiJSON("POST", "/api/scheduler/" + action, {}).then(function (result) {
        if (result.status !== 200) {
          setSideStatus("sched-status", result.data.error || "Chyba.", "warn");
          return;
        }
        setSideStatus("sched-status", schedEnabled.checked ? "Zapnutá." : "Vypnutá.", "ok");
        renderScheduler(result.data);
      });
    });
  }

  function applyInterval(minutes) {
    apiJSON("POST", "/api/scheduler/interval", { minutes: minutes }).then(function (result) {
      if (result.status !== 200) {
        setSideStatus("sched-status", result.data.error || "Neplatný interval.", "warn");
        return;
      }
      setSideStatus("sched-status", "Interval uložený.", "ok");
      renderScheduler(result.data);
    });
  }

  if (schedPreset) {
    schedPreset.addEventListener("change", function () {
      if (schedPreset.value === "custom") {
        if (schedCustomWrap) schedCustomWrap.classList.remove("hidden");
        if (schedCustom) schedCustom.focus();
        return;
      }
      applyInterval(parseInt(schedPreset.value, 10));
    });
  }
  if (schedCustom) {
    schedCustom.addEventListener("change", function () {
      var minutes = parseInt(schedCustom.value, 10);
      if (isNaN(minutes) || minutes < 30) {
        setSideStatus("sched-status", "Minimálny interval je 30 minút.", "warn");
        return;
      }
      applyInterval(minutes);
    });
  }
  if (schedRunNow) {
    schedRunNow.addEventListener("click", function () {
      setBusy(schedRunNow, true);
      apiJSON("POST", "/api/scheduler/run-now", {}).then(function (result) {
        setBusy(schedRunNow, false);
        if (result.status === 409) { setStatus(result.data.error || "Prebieha iná úloha.", "warn"); return; }
        if (result.status !== 202) { setStatus("Chyba: " + (result.data.error || result.status), "warn"); return; }
        setStatus("Spustené.", "ok");
        renderBatch(result.data.status || { state: "running" });
        if (batchTimer) clearInterval(batchTimer);
        batchTimer = setInterval(pollBatch, 1000);
        refreshScheduler();
      }).catch(function (error) { setBusy(schedRunNow, false); setStatus("Chyba siete: " + error, "warn"); });
    });
  }

  setInterval(function () {
    if (schedSeconds !== null && schedSeconds !== undefined && schedSeconds > 0) {
      schedSeconds -= 1;
      updateCountdown();
    }
  }, 1000);
  setInterval(refreshScheduler, 30000);
  renderScheduler(schedData);

  /* ---------------- tab title: number of new listings ---------------- */

  function refreshNewCount() {
    fetch("/api/new-count").then(function (r) { return r.json(); }).then(function (data) {
      var count = data.total_new || 0;
      document.title = (count > 0 ? "(" + count + ") " : "") + "Bazoš monitor";
    }).catch(function () {});
  }
  refreshNewCount();
  setInterval(refreshNewCount, 60000);
})();
