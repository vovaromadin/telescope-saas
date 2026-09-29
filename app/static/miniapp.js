const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
const state = {
  initData: tg ? tg.initData : "",
  webKey: localStorage.getItem("tgr_web_key") || "",
  projects: [],
  projectId: null,
  lastSearchId: null,
  activeView: "overview",
  telegramConnected: false,
  telegramPoll: null,
  crmAddedCount: 0,
  lastRows: [],
  leadRows: []
};
const $ = (id) => document.getElementById(id);

if (tg) {
  tg.ready();
  tg.expand();
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>'"]/g, function(c) {
    return {"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[c];
  });
}

function buildAuthHeaders() {
  return state.initData
    ? {"X-Telegram-Init-Data":state.initData}
    : state.webKey
      ? {"X-API-Key":state.webKey}
      : {};
}

function storageKey(name) {
  return "tgr_" + name + "_" + String(state.projectId || "none");
}

function loadStoredArray(name) {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey(name)) || "[]");
    return Array.isArray(value) ? value : [];
  } catch (_) {
    return [];
  }
}

function saveStoredArray(name, value) {
  localStorage.setItem(storageKey(name), JSON.stringify(value));
}

function radarRowKey(row) {
  return String(row.username || row.url || row.title || "").trim().toLowerCase();
}

function isFavorite(row) {
  return new Set(loadStoredArray("favorites")).has(radarRowKey(row));
}

function toggleFavorite(row) {
  const key = radarRowKey(row);
  const favorites = new Set(loadStoredArray("favorites"));
  if (favorites.has(key)) favorites.delete(key);
  else favorites.add(key);
  saveStoredArray("favorites", Array.from(favorites));
}

function renderSavedSearches() {
  const box = $("savedSearches");
  if (!box) return;
  const rows = loadStoredArray("saved_searches");
  box.innerHTML = rows.length
    ? rows.map(function(query, index) {
        return '<span class="saved-search-chip"><button type="button" class="saved-query" data-index="' + index + '">' +
          escapeHtml(query) + '</button><button type="button" class="saved-remove" data-index="' + index + '" aria-label="Удалить">×</button></span>';
      }).join("")
    : '<span class="meta">Сохранённых запросов пока нет</span>';

  box.querySelectorAll(".saved-query").forEach(function(button) {
    button.onclick = function() {
      const query = loadStoredArray("saved_searches")[Number(button.dataset.index)] || "";
      if (!query) return;
      $("query").value = query;
      $("searchForm").requestSubmit();
    };
  });
  box.querySelectorAll(".saved-remove").forEach(function(button) {
    button.onclick = function() {
      const rows = loadStoredArray("saved_searches");
      rows.splice(Number(button.dataset.index), 1);
      saveStoredArray("saved_searches", rows);
      renderSavedSearches();
    };
  });
}

function openInfoSheet(title, bodyHtml, eyebrow) {
  $("infoSheetTitle").textContent = title || "Разбор";
  $("infoSheetEyebrow").textContent = eyebrow || "TG Ракета";
  $("infoSheetBody").innerHTML = bodyHtml || "";
  $("infoSheet").hidden = false;
  document.body.classList.add("sheet-open");
}

function closeInfoSheet() {
  $("infoSheet").hidden = true;
  $("infoSheetBody").innerHTML = "";
  document.body.classList.remove("sheet-open");
}

$("infoSheetClose").onclick = closeInfoSheet;
document.querySelectorAll("[data-close-sheet]").forEach(function(node) {
  node.onclick = closeInfoSheet;
});


async function api(path, options) {
  options = options || {};
  if (!state.initData && !state.webKey) {
    const entered = window.prompt("Ключ владельца TG Ракеты");
    if (entered) {
      state.webKey = entered.trim();
      localStorage.setItem("tgr_web_key", state.webKey);
    }
  }
  const authHeaders = buildAuthHeaders();
  const headers = Object.assign(
    {"Content-Type":"application/json"},
    authHeaders,
    options.headers || {}
  );
  const response = await fetch(path, Object.assign({}, options, {headers: headers}));
  if (!response.ok) {
    let detail = "Ошибка " + response.status;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) {}
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

function notify(text) {
  const status = $("status");
  if (status) status.textContent = text;
  if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
}

async function refreshMe() {
  const me = await api("/api/app/me");
  $("planBadge").textContent = me.plan.toUpperCase();
  $("usage").textContent = "Использовано " + me.used + " из " + me.monthly_limit + " поисков в этом месяце";
  state.telegramConnected = Boolean(me.telegram_ready);
}

async function refreshRadarSources() {
  const sourceBadge = $("radarSourceBadge");
  const sourceStatus = $("radarSourceStatus");
  if (!sourceBadge || !sourceStatus) return;

  try {
    const info = await api("/api/app/radar/status");
    const active = ["SEARCH-T.ME", "WEB"];
    const details = ["Основной бесплатный каталог search-t.me активен"];

    if (info.tgstat_ok) {
      active.unshift("TGSTAT");
      details.push("TGStat API отвечает");
    } else if (info.tgstat_configured) {
      details.push("TGStat API настроен, но доступ ограничен тарифом или ключом");
    }

    if (info.telemetr_configured) {
      if (info.telemetr_status === "invalid_key") details.push("Telemetr: ключ не принят");
      else if (info.telemetr_status === "rate_limited") details.push("Telemetr: исчерпан лимит");
      else details.push("Telemetr подключён как дополнительный источник; trial может возвращать пустую выдачу");
    }

    sourceBadge.textContent = active.join(" + ");
    sourceBadge.classList.add("connected");
    sourceStatus.textContent = details.join(". ") + ".";
  } catch (error) {
    sourceBadge.textContent = "SEARCH-T.ME + WEB";
    sourceBadge.classList.remove("connected");
    sourceStatus.textContent = "Public web активен. Диагностика дополнительных источников временно недоступна.";
  }
}

function renderProjects() {
  const box = $("projects");
  box.innerHTML = "";
  state.projects.forEach(function(p) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "project" + (state.projectId === p.id ? " active" : "");
    item.innerHTML = "<b>" + escapeHtml(p.name) + "</b><small>#" + p.id + "</small>";
    item.onclick = async function() {
      state.projectId = p.id;
      state.lastRows = [];
      state.crmAddedCount = 0;
      $("results").innerHTML = "";
      $("toolbar").hidden = true;
      renderProjects();
      renderSavedSearches();
      await refreshCurrentView();
    };
    box.appendChild(item);
  });
  if (!state.projectId && state.projects.length) {
    state.projectId = state.projects[0].id;
    renderProjects();
  }
}

async function refreshProjects() {
  state.projects = await api("/api/app/projects");
  renderProjects();
  renderSavedSearches();
}

function setView(name) {
  state.activeView = name;
  document.querySelectorAll("[data-view]").forEach(function(section) {
    section.hidden = section.dataset.view !== name;
  });
  document.querySelectorAll("[data-view-button]").forEach(function(button) {
    const active = button.dataset.viewButton === name;
    button.classList.toggle("active", active);
    if (active) button.scrollIntoView({behavior:"smooth", block:"nearest", inline:"center"});
  });
  const crmJump = $("crmJump");
  if (crmJump) crmJump.hidden = !(name === "radar" && state.crmAddedCount > 0);
  if (name === "radar") { refreshTelegramConnection(); refreshRadarSources(); }
  refreshCurrentView();
}

document.querySelectorAll("[data-view-button]").forEach(function(button) {
  button.onclick = function() { setView(button.dataset.viewButton); };
});

async function refreshCurrentView() {
  if (!state.projectId) return;
  try {
    if (state.activeView === "overview") await refreshOverview();
    if (state.activeView === "leads") await refreshLeads();
    if (state.activeView === "content") await refreshContent();
    if (state.activeView === "campaigns") await refreshCampaigns();
  } catch (error) {
    notify(error.message);
  }
}

$("projectForm").onsubmit = async function(event) {
  event.preventDefault();
  try {
    const project = await api("/api/app/projects", {
      method:"POST",
      body:JSON.stringify({name:$("projectName").value.trim()})
    });
    state.projects.unshift(project);
    state.projectId = project.id;
    $("projectName").value = "";
    renderProjects();
    await refreshCurrentView();
  } catch (error) {
    notify(error.message);
  }
};


async function refreshTelegramConnection() {
  const badge = $("tgConnectionBadge");
  const status = $("tgConnectionStatus");
  const startButton = $("tgQrStart");
  const loginLink = $("tgQrLink");
  const qrCode = $("tgQrCode");
  const disconnect = $("tgDisconnect");
  if (!badge || !status) return null;

  try {
    const info = await api("/api/app/telegram/connection");
    state.telegramConnected = Boolean(info.connected);
    badge.classList.remove("connected");
    startButton.hidden = false;
    loginLink.hidden = true;
    disconnect.hidden = true;

    if (!info.api_ready) {
      badge.textContent = "API";
      status.textContent = "Telegram API ещё не настроен.";
      startButton.hidden = true;
      return info;
    }

    if (info.connected) {
      badge.textContent = "Подключён";
      badge.classList.add("connected");
      status.textContent = info.display_name ? "Подключён: " + info.display_name : "Telegram-аккаунт подключён.";
      startButton.hidden = true;
      disconnect.hidden = false;
      qrCode.hidden = true;
      qrCode.innerHTML = "";
      if (state.telegramPoll) {
        clearInterval(state.telegramPoll);
        state.telegramPoll = null;
      }
      return info;
    }

    badge.textContent = "Не подключён";
    if (info.status === "waiting_qr") {
      status.textContent = "Отсканируй QR в Telegram: Настройки → Устройства → Подключить устройство.";
      startButton.textContent = "Показать новый QR";
    } else if (info.status === "two_factor_required") {
      status.textContent = "На аккаунте включена 2FA. QR-вход потребовал дополнительную проверку; TG Ракета не запрашивает пароль.";
      startButton.textContent = "Попробовать другой аккаунт";
      qrCode.hidden = true;
      qrCode.innerHTML = "";
    } else if (info.status === "expired") {
      status.textContent = "QR истёк. Создай новый.";
      startButton.textContent = "Показать новый QR";
      qrCode.hidden = true;
      qrCode.innerHTML = "";
    } else if (info.status === "error") {
      status.textContent = "Telegram не завершил подключение. Создай новый QR.";
      startButton.textContent = "Повторить";
      qrCode.hidden = true;
      qrCode.innerHTML = "";
    } else {
      status.textContent = "Подключение выполняется через подтверждение в официальном Telegram.";
      startButton.textContent = "Показать QR для подключения";
      qrCode.hidden = true;
      qrCode.innerHTML = "";
    }
    return info;
  } catch (error) {
    status.textContent = error.message;
    return null;
  }
}

async function pollTelegramConnection() {
  if (state.telegramPoll) clearInterval(state.telegramPoll);
  state.telegramPoll = setInterval(async function() {
    const info = await refreshTelegramConnection();
    if (!info || info.connected || ["two_factor_required","expired","error"].includes(info.status)) {
      clearInterval(state.telegramPoll);
      state.telegramPoll = null;
      await refreshMe();
    }
  }, 1500);
}

$("tgQrStart").onclick = async function() {
  const button = $("tgQrStart");
  const link = $("tgQrLink");
  const qrCode = $("tgQrCode");
  button.disabled = true;
  try {
    const result = await api("/api/app/telegram/qr/start", {method:"POST"});
    link.href = result.login_url;
    link.hidden = false;
    link.textContent = "Открыть подтверждение в Telegram";
    qrCode.innerHTML = result.qr_svg || "";
    qrCode.hidden = !result.qr_svg;
    $("tgConnectionStatus").textContent = "На телефоне открой Telegram → Настройки → Устройства → Подключить устройство и отсканируй QR.";
    button.textContent = "Показать новый QR";
    pollTelegramConnection();
  } catch (error) {
    $("tgConnectionStatus").textContent = error.message;
  } finally {
    button.disabled = false;
  }
};

$("tgDisconnect").onclick = async function() {
  try {
    await api("/api/app/telegram/connection", {method:"DELETE"});
    await Promise.all([refreshTelegramConnection(), refreshMe()]);
  } catch (error) {
    $("tgConnectionStatus").textContent = error.message;
  }
};

async function refreshOverview() {
  const data = await api("/api/app/growth/projects/" + state.projectId + "/overview");
  $("kpiLeads").textContent = data.leads_total;
  $("kpiQualified").textContent = data.leads_qualified + " квалифицировано";
  $("kpiNew").textContent = data.leads_new;
  $("kpiContent").textContent = data.content_draft;
  $("kpiScheduled").textContent = data.content_scheduled + " запланировано";
  $("kpiCampaigns").textContent = data.campaigns_active;
  $("kpiCommunities").textContent = data.communities_discovered;
  $("kpiSearches").textContent = data.searches + " поисков";
  $("kpiWon").textContent = data.leads_won;
}

function filteredRadarRows() {
  let rows = state.lastRows.slice();
  const minScore = Number($("radarMinScore").value || 0);
  const minAudience = Number($("radarMinAudience").value || 0);
  const favoritesOnly = Boolean($("radarFavoritesOnly").checked);
  const favorites = new Set(loadStoredArray("favorites"));

  rows = rows.filter(function(row) {
    if (Number(row.total_score || 0) < minScore) return false;
    if (Number(row.subscribers || 0) < minAudience) return false;
    if (favoritesOnly && !favorites.has(radarRowKey(row))) return false;
    return true;
  });

  const sort = $("radarSort").value;
  const field = sort === "activity" ? "activity_score" : sort === "audience" ? "subscribers" : sort === "views" ? "avg_views" : "total_score";
  rows.sort(function(a, b) { return Number(b[field] || 0) - Number(a[field] || 0); });
  return rows;
}

function applyRadarFilters() {
  const rows = filteredRadarRows();
  const signalMode = $("radarViewMode").value === "signals";
  const signalCount = signalMode ? renderSignals(rows) : null;
  if (!signalMode) renderResults(rows);
  if (state.lastRows.length) {
    $("count").textContent = signalMode
      ? "Сигналов: " + signalCount
      : rows.length === state.lastRows.length
        ? "Найдено: " + rows.length
        : "Показано: " + rows.length + " из " + state.lastRows.length;
    $("toolbar").hidden = false;
  }
}

async function addRadarRowToCrm(row, button) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Добавляю…";
  try {
    await api("/api/app/growth/projects/" + state.projectId + "/leads", {
      method:"POST",
      body:JSON.stringify({
        display_name:row.title || row.username || "Telegram lead",
        source:"radar",
        source_url:row.url || "",
        username:String(row.username || "").replace(/^@/, ""),
        public_contact:(row.public_contacts || [])[0] || "",
        intent_score:Math.max(0, Math.min(100, Number(row.total_score || 0))),
        note:"Добавлен из TG Radar"
      })
    });
    button.textContent = "В CRM ✓";
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    state.crmAddedCount += 1;
    const crmJump = $("crmJump");
    const crmJumpCount = $("crmJumpCount");
    if (crmJump && crmJumpCount) {
      crmJumpCount.textContent = state.crmAddedCount;
      crmJump.hidden = state.activeView !== "radar";
    }
    refreshOverview();
  } catch (error) {
    button.disabled = false;
    button.textContent = originalText;
    notify(error.message);
  }
}

function openRadarRow(row) {
  if (!row || !row.url) return;
  if (tg && typeof tg.openTelegramLink === "function" && row.url.indexOf("https://t.me/") === 0) {
    tg.openTelegramLink(row.url);
  } else {
    window.open(row.url, "_blank", "noopener");
  }
}

function renderSignals(rows) {
  const signals = [];
  rows.forEach(function(row) {
    (row.matched_snippets || []).slice(0, 3).forEach(function(snippet) {
      if (snippet) signals.push({row:row, snippet:snippet});
    });
  });

  $("results").innerHTML = signals.length ? signals.map(function(item, index) {
    return '<article class="signal-card">' +
      '<div class="signal-head"><div><b>' + escapeHtml(item.row.title) + '</b><div class="meta">' + escapeHtml(item.row.username) + '</div></div>' +
      '<span class="chip">score ' + Math.round(Number(item.row.total_score || 0)) + '</span></div>' +
      '<p>' + escapeHtml(item.snippet) + '</p>' +
      '<div class="result-actions">' +
      '<button type="button" class="ghost signal-add" data-index="' + index + '">+ CRM</button>' +
      '<button type="button" class="ghost signal-open" data-index="' + index + '">Открыть канал</button>' +
      '</div></article>';
  }).join("") : '<div class="empty">В этих результатах пока нет публичных совпадений из свежих постов. Попробуй другой запрос или открой режим «Каналы».</div>';

  document.querySelectorAll(".signal-add").forEach(function(button) {
    button.onclick = function() {
      const item = signals[Number(button.dataset.index)];
      if (item) addRadarRowToCrm(item.row, button);
    };
  });
  document.querySelectorAll(".signal-open").forEach(function(button) {
    button.onclick = function() {
      const item = signals[Number(button.dataset.index)];
      if (item) openRadarRow(item.row);
    };
  });
  return signals.length;
}

function renderResults(rows) {
  $("results").innerHTML = rows.map(function(r, index) {
    const contacts = (r.public_contacts || []).map(function(c) {
      return '<span class="chip">контакт ' + escapeHtml(c) + '</span>';
    }).join("");
    const links =
      (r.referral_url ? '<a href="' + r.referral_url + '" target="_blank">ref link</a>' : "") +
      (r.max_referral_url ? '<a href="' + r.max_referral_url + '" target="_blank">MAX link</a>' : "");
    const favorite = isFavorite(r);
    const extraStats =
      (Number(r.messages_30d || 0) > 0 ? '<span class="chip">' + Number(r.messages_30d) + ' постов/30д</span>' : '') +
      (Number(r.avg_views || 0) > 0 ? '<span class="chip">' + Math.round(Number(r.avg_views)).toLocaleString("ru-RU") + ' ср. просмотров</span>' : '');
    return '<article class="result"><div>' +
      '<div class="result-title-row"><h3>' + escapeHtml(r.title) + '</h3>' +
      '<button type="button" class="favorite-toggle' + (favorite ? ' active' : '') + '" data-row="' + index + '" aria-label="Избранное">★</button></div>' +
      '<div class="meta">' + escapeHtml(r.username) + ' · ' + escapeHtml(r.kind) + '</div>' +
      '<p class="desc">' + escapeHtml(r.description || "Описание не указано") + '</p>' +
      '<div class="chips"><span class="chip">' + Number(r.subscribers || 0).toLocaleString("ru-RU") + ' участников</span>' +
      '<span class="chip">релевантность ' + Math.round(Number(r.relevance_score || 0)) + '</span>' +
      '<span class="chip">активность ' + Math.round(Number(r.activity_score || 0)) + '</span>' + extraStats + contacts + '</div>' +
      '<div class="result-actions">' +
      '<button type="button" class="ghost lead-add" data-row="' + index + '">+ CRM</button>' +
      '<button type="button" class="ghost analyze-channel" data-row="' + index + '">Разбор канала</button>' +
      '<button type="button" class="ghost channel-open" data-row="' + index + '">Открыть канал</button>' +
      '<div class="links">' + links + '</div></div></div>' +
      '<div class="score">' + Math.round(r.total_score || 0) + '</div></article>';
  }).join("");

  document.querySelectorAll(".favorite-toggle").forEach(function(button) {
    button.onclick = function(event) {
      event.preventDefault();
      event.stopPropagation();
      const row = rows[Number(button.dataset.row)];
      toggleFavorite(row);
      applyRadarFilters();
    };
  });

  document.querySelectorAll(".analyze-channel").forEach(function(button) {
    button.onclick = async function(event) {
      event.preventDefault();
      event.stopPropagation();
      const row = rows[Number(button.dataset.row)];
      const oldText = button.textContent;
      button.disabled = true;
      button.textContent = "Анализирую…";
      try {
        const data = await api(
          "/api/app/radar/analyze?username=" + encodeURIComponent(String(row.username || "").replace(/^@/, "")) +
          "&query=" + encodeURIComponent($("query").value.trim())
        );
        const snippets = (data.snippets || []).length
          ? '<div class="analysis-section"><b>Свежие совпадения</b><ul>' + data.snippets.map(function(item) {
              return '<li>' + escapeHtml(item) + '</li>';
            }).join("") + '</ul></div>'
          : '';
        const body =
          '<div class="analysis-kpis">' +
            '<div><span>Аудитория</span><b>' + Number(data.subscribers || 0).toLocaleString("ru-RU") + '</b></div>' +
            '<div><span>Постов / 30д</span><b>' + Number(data.messages_30d || 0) + '</b></div>' +
            '<div><span>Ср. просмотры</span><b>' + Math.round(Number(data.avg_views || 0)).toLocaleString("ru-RU") + '</b></div>' +
            '<div><span>ER ~</span><b>' + Number(data.estimated_er || 0).toFixed(1) + '%</b></div>' +
          '</div>' +
          '<div class="analysis-section"><b>Почему подходит</b><p>' + escapeHtml(data.why || "") + '</p></div>' +
          '<div class="analysis-section"><b>Рекомендация</b><p>' + escapeHtml(data.recommendation || "") + '</p></div>' +
          snippets;
        openInfoSheet(data.title || row.title, body, "РАЗБОР КАНАЛА");
      } catch (error) {
        notify(error.message);
      } finally {
        button.disabled = false;
        button.textContent = oldText;
      }
    };
  });

  document.querySelectorAll(".lead-add").forEach(function(button) {
    button.onclick = function(event) {
      event.preventDefault();
      event.stopPropagation();
      const row = rows[Number(button.dataset.row)];
      if (row) addRadarRowToCrm(row, button);
      return false;
    };
  });

  document.querySelectorAll(".channel-open").forEach(function(button) {
    button.onclick = function(event) {
      event.preventDefault();
      event.stopPropagation();
      const row = rows[Number(button.dataset.row)];
      openRadarRow(row);
      return false;
    };
  });
}

async function pollSearch(searchId) {
  let run = await api("/api/app/searches/" + searchId);
  while (run.status === "queued" || run.status === "running") {
    await new Promise(function(resolve) { setTimeout(resolve, 1500); });
    run = await api("/api/app/searches/" + searchId);
  }
  if (run.status === "failed") throw new Error(run.error || "Поиск завершился ошибкой");
  const rows = await api("/api/app/searches/" + searchId + "/results");
  state.lastRows = rows;
  applyRadarFilters();
  notify(run.error || "Поиск завершён");
}

$("saveSearch").onclick = function() {
  const query = $("query").value.trim();
  if (!query || !state.projectId) return;
  const rows = loadStoredArray("saved_searches").filter(function(item) { return item !== query; });
  rows.unshift(query);
  saveStoredArray("saved_searches", rows.slice(0, 12));
  renderSavedSearches();
  notify("Поиск сохранён.");
};

["radarViewMode","radarSort","radarMinScore","radarMinAudience","radarFavoritesOnly"].forEach(function(id) {
  $(id).onchange = applyRadarFilters;
});

$("searchForm").onsubmit = async function(event) {
  event.preventDefault();
  if (!state.projectId) {
    notify("Сначала создай проект.");
    return;
  }
  const button = $("searchButton");
  button.disabled = true;
  $("toolbar").hidden = true;
  $("results").innerHTML = "";
  notify("Ищу публичные сообщества и считаю рейтинг…");
  try {
    const run = await api("/api/app/projects/" + state.projectId + "/searches", {
      method:"POST",
      body:JSON.stringify({query:$("query").value.trim(),limit:30})
    });
    state.lastSearchId = run.id;
    await pollSearch(run.id);
    await Promise.all([refreshMe(), refreshOverview()]);
  } catch (error) {
    notify(error.status === 402 ? "Лимит тарифа исчерпан. Выбери Pro или Team." : error.message);
  } finally {
    button.disabled = false;
  }
};

async function download(format) {
  if (!state.lastSearchId) return;
  const response = await fetch("/api/app/searches/" + state.lastSearchId + "/export." + format, {
    headers:buildAuthHeaders()
  });
  if (!response.ok) return;
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "tg-raketa-radar-" + state.lastSearchId + "." + format;
  a.click();
  setTimeout(function(){ URL.revokeObjectURL(url); }, 2000);
}

$("csv").onclick = function(){ download("csv"); };
$("json").onclick = function(){ download("json"); };

$("crmJump").onclick = function() {
  setView("leads");
};

function statusSelect(kind, id, value, values) {
  const labels = {
    new:"Новый", qualified:"Интересный", contacted:"Связались", won:"Успех", lost:"Отказ", snoozed:"Отложено",
    draft:"Черновик", review:"На проверке", scheduled:"Запланирован", published:"Опубликован",
    active:"Активна", paused:"Пауза", completed:"Завершена"
  };
  return '<select class="status-select" data-kind="' + kind + '" data-id="' + id + '">' +
    values.map(function(item) {
      return '<option value="' + item + '"' + (item === value ? " selected" : "") + '>' + (labels[item] || item) + '</option>';
    }).join("") + '</select>';
}

function renderLeadRows() {
  const query = $("leadSearch").value.trim().toLowerCase();
  const status = $("leadStatusFilter").value;
  const rows = state.leadRows.filter(function(row) {
    if (status && row.status !== status) return false;
    if (!query) return true;
    return [row.display_name,row.username,row.public_contact,row.note,row.source].join(" ").toLowerCase().includes(query);
  });

  $("leadList").innerHTML = rows.length ? rows.map(function(row) {
    const sourceButton = row.source_url
      ? '<button type="button" class="ghost lead-source" data-id="' + row.id + '">Источник</button>'
      : '';
    return '<article class="item-card"><div><div class="item-title">' + escapeHtml(row.display_name) + '</div>' +
      '<div class="meta">' + escapeHtml(row.public_contact || row.username || row.source) + '</div>' +
      '<p>' + escapeHtml(row.note || "Без заметки") + '</p>' +
      '<div class="item-actions">' + sourceButton +
      '<button type="button" class="ghost lead-draft" data-id="' + row.id + '">Черновик обращения</button></div></div>' +
      '<div class="item-side"><b>' + Math.round(row.intent_score) + '</b>' +
      statusSelect("lead", row.id, row.status, ["new","qualified","contacted","won","lost","snoozed"]) + '</div></article>';
  }).join("") : '<div class="empty">По выбранным фильтрам лидов нет.</div>';

  bindStatusSelects();

  document.querySelectorAll(".lead-source").forEach(function(button) {
    button.onclick = function() {
      const row = state.leadRows.find(function(item) { return item.id === Number(button.dataset.id); });
      if (!row || !row.source_url) return;
      if (tg && typeof tg.openTelegramLink === "function" && row.source_url.indexOf("https://t.me/") === 0) tg.openTelegramLink(row.source_url);
      else window.open(row.source_url, "_blank", "noopener");
    };
  });

  document.querySelectorAll(".lead-draft").forEach(function(button) {
    button.onclick = async function() {
      const oldText = button.textContent;
      button.disabled = true;
      button.textContent = "Готовлю…";
      try {
        const data = await api("/api/app/growth/leads/" + button.dataset.id + "/draft");
        const body =
          '<div class="analysis-section"><b>' + escapeHtml(data.subject || "Черновик") + '</b>' +
          '<textarea id="draftText" class="draft-text" readonly>' + escapeHtml(data.text || "") + '</textarea>' +
          '<button id="copyDraft" type="button">Копировать текст</button>' +
          '<p class="meta">' + escapeHtml(data.note || "") + '</p></div>';
        openInfoSheet("Черновик обращения", body, "CRM");
        $("copyDraft").onclick = async function() {
          try {
            await navigator.clipboard.writeText($("draftText").value);
            $("copyDraft").textContent = "Скопировано ✓";
          } catch (_) {
            $("draftText").select();
            document.execCommand("copy");
            $("copyDraft").textContent = "Скопировано ✓";
          }
        };
      } catch (error) {
        notify(error.message);
      } finally {
        button.disabled = false;
        button.textContent = oldText;
      }
    };
  });
}

async function refreshLeads() {
  state.leadRows = await api("/api/app/growth/projects/" + state.projectId + "/leads");
  renderLeadRows();
}

$("leadSearch").oninput = renderLeadRows;
$("leadStatusFilter").onchange = renderLeadRows;

$("leadForm").onsubmit = async function(event) {
  event.preventDefault();
  if (!state.projectId) return;
  await api("/api/app/growth/projects/" + state.projectId + "/leads", {
    method:"POST",
    body:JSON.stringify({
      display_name:$("leadName").value.trim(),
      public_contact:$("leadContact").value.trim(),
      intent_score:Number($("leadScore").value || 0),
      note:$("leadNote").value.trim(),
      source:"manual"
    })
  });
  event.target.reset();
  $("leadScore").value = "50";
  await Promise.all([refreshLeads(), refreshOverview()]);
};

async function refreshContent() {
  const rows = await api("/api/app/growth/projects/" + state.projectId + "/content");
  $("contentList").innerHTML = rows.length ? rows.map(function(row) {
    return '<article class="item-card"><div><div class="item-title">' + escapeHtml(row.title) + '</div>' +
      '<div class="meta">' + escapeHtml(row.format) + '</div><p>' + escapeHtml(row.body || "Пустой черновик") + '</p></div>' +
      '<div class="item-side">' + statusSelect("content", row.id, row.status, ["draft","review","scheduled","published"]) + '</div></article>';
  }).join("") : '<div class="empty">Контент-план пуст.</div>';
  bindStatusSelects();
}

$("contentForm").onsubmit = async function(event) {
  event.preventDefault();
  await api("/api/app/growth/projects/" + state.projectId + "/content", {
    method:"POST",
    body:JSON.stringify({
      title:$("contentTitle").value.trim(),
      body:$("contentBody").value.trim(),
      format:$("contentFormat").value
    })
  });
  event.target.reset();
  await Promise.all([refreshContent(), refreshOverview()]);
};

async function refreshCampaigns() {
  const rows = await api("/api/app/growth/projects/" + state.projectId + "/campaigns");
  $("campaignList").innerHTML = rows.length ? rows.map(function(row) {
    return '<article class="item-card"><div><div class="item-title">' + escapeHtml(row.name) + '</div>' +
      '<div class="meta">' + escapeHtml(row.channel) + (row.budget_daily ? " · " + row.budget_daily + "/день" : "") + '</div>' +
      '<p>' + escapeHtml(row.goal || "Цель не указана") + '</p></div>' +
      '<div class="item-side">' + statusSelect("campaign", row.id, row.status, ["draft","active","paused","completed"]) + '</div></article>';
  }).join("") : '<div class="empty">Кампаний пока нет.</div>';
  bindStatusSelects();
}

$("campaignForm").onsubmit = async function(event) {
  event.preventDefault();
  await api("/api/app/growth/projects/" + state.projectId + "/campaigns", {
    method:"POST",
    body:JSON.stringify({
      name:$("campaignName").value.trim(),
      goal:$("campaignGoal").value.trim(),
      channel:$("campaignChannel").value,
      budget_daily:Number($("campaignBudget").value || 0)
    })
  });
  event.target.reset();
  $("campaignBudget").value = "0";
  await Promise.all([refreshCampaigns(), refreshOverview()]);
};

function bindStatusSelects() {
  document.querySelectorAll(".status-select").forEach(function(select) {
    select.onchange = async function() {
      const kind = select.dataset.kind;
      const id = select.dataset.id;
      const path = kind === "lead" ? "/leads/" : kind === "content" ? "/content/" : "/campaigns/";
      await api("/api/app/growth" + path + id, {
        method:"PATCH",
        body:JSON.stringify({status:select.value})
      });
      await refreshOverview();
    };
  });
}

document.querySelectorAll("[data-plan]").forEach(function(button) {
  button.onclick = async function() {
    try {
      const result = await api("/api/app/billing/checkout?plan=" + button.dataset.plan, {method:"POST"});
      if (tg && tg.openLink) tg.openLink(result.url);
      else window.location.href = result.url;
    } catch (error) {
      notify(error.message);
    }
  };
});

async function boot() {
  try {
    await Promise.all([refreshMe(), refreshProjects(), refreshTelegramConnection(), refreshRadarSources()]);
    $("authError").hidden = true;
    renderSavedSearches();
    if (state.projectId) await refreshOverview();
  } catch (error) {
    $("authError").hidden = false;
    $("authError").textContent = !state.initData
      ? "Web-доступ владельца: " + error.message + ". Обнови страницу и введи корректный ключ."
      : error.message;
  }
}

boot();
