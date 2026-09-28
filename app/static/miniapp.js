const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
const state = {
  initData: tg ? tg.initData : "",
  projects: [],
  projectId: null,
  lastSearchId: null,
  activeView: "overview",
  telegramConnected: false,
  telegramPoll: null
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

async function api(path, options) {
  options = options || {};
  const headers = Object.assign(
    {"Content-Type":"application/json","X-Telegram-Init-Data":state.initData},
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
    const active = ["WEB"];
    const details = [];

    if (info.tgstat_ok) {
      active.unshift("TGSTAT");
      details.push("TGStat API отвечает");
    } else if (info.tgstat_configured) {
      details.push("TGStat: ключ есть, но тариф/доступ нужно проверить");
    }

    if (info.telemetr_ok) {
      active.unshift("TELEMETR");
      details.push("Telemetr API отвечает");
    } else if (info.telemetr_configured) {
      if (info.telemetr_status === "forbidden") details.push("Telemetr: ключ есть, каталог ограничен тарифом");
      else if (info.telemetr_status === "rate_limited") details.push("Telemetr: исчерпан лимит");
      else if (info.telemetr_status === "invalid_key") details.push("Telemetr: ключ не принят");
      else details.push("Telemetr: дополнительный источник сейчас недоступен");
    }

    sourceBadge.textContent = active.join(" + ");
    sourceBadge.classList.toggle("connected", active.length > 1);
    sourceStatus.textContent = details.length
      ? details.join(". ") + ". Public web остаётся резервным источником."
      : "Radar работает через публичный web-поиск. API-источники можно подключить дополнительно.";
  } catch (error) {
    sourceBadge.textContent = "WEB";
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
      renderProjects();
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

function renderResults(rows) {
  $("results").innerHTML = rows.map(function(r, index) {
    const contacts = (r.public_contacts || []).map(function(c) {
      return '<span class="chip">контакт ' + escapeHtml(c) + '</span>';
    }).join("");
    const links =
      (r.referral_url ? '<a href="' + r.referral_url + '" target="_blank">ref link</a>' : "") +
      (r.max_referral_url ? '<a href="' + r.max_referral_url + '" target="_blank">MAX link</a>' : "");
    return '<article class="result"><div>' +
      '<h3><a href="' + r.url + '" target="_blank" rel="noopener">' + escapeHtml(r.title) + '</a></h3>' +
      '<div class="meta">' + escapeHtml(r.username) + ' · ' + escapeHtml(r.kind) + '</div>' +
      '<p class="desc">' + escapeHtml(r.description || "Описание не указано") + '</p>' +
      '<div class="chips"><span class="chip">' + Number(r.subscribers || 0).toLocaleString("ru-RU") + ' участников</span>' +
      '<span class="chip">релевантность ' + r.relevance_score + '</span>' +
      '<span class="chip">активность ' + r.activity_score + '</span>' + contacts + '</div>' +
      '<div class="result-actions"><button type="button" class="ghost lead-add" data-row="' + index + '">+ CRM</button>' +
      '<div class="links">' + links + '</div></div></div>' +
      '<div class="score">' + Math.round(r.total_score || 0) + '</div></article>';
  }).join("");

  document.querySelectorAll(".lead-add").forEach(function(button) {
    button.onclick = async function() {
      const row = rows[Number(button.dataset.row)];
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
        button.disabled = true;
      } catch (error) {
        notify(error.message);
      }
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
  renderResults(rows);
  $("count").textContent = "Найдено: " + rows.length;
  $("toolbar").hidden = false;
  notify(run.error || "Поиск завершён");
}

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
    headers:{"X-Telegram-Init-Data":state.initData}
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

function statusSelect(kind, id, value, values) {
  return '<select class="status-select" data-kind="' + kind + '" data-id="' + id + '">' +
    values.map(function(item) {
      return '<option value="' + item + '"' + (item === value ? " selected" : "") + '>' + item + '</option>';
    }).join("") + '</select>';
}

async function refreshLeads() {
  const rows = await api("/api/app/growth/projects/" + state.projectId + "/leads");
  $("leadList").innerHTML = rows.length ? rows.map(function(row) {
    return '<article class="item-card"><div><div class="item-title">' + escapeHtml(row.display_name) + '</div>' +
      '<div class="meta">' + escapeHtml(row.public_contact || row.username || row.source) + '</div>' +
      '<p>' + escapeHtml(row.note || "Без заметки") + '</p></div>' +
      '<div class="item-side"><b>' + Math.round(row.intent_score) + '</b>' +
      statusSelect("lead", row.id, row.status, ["new","qualified","contacted","won","lost","snoozed"]) + '</div></article>';
  }).join("") : '<div class="empty">Лидов пока нет. Добавь вручную или перенеси результат из Radar.</div>';
  bindStatusSelects();
}

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
  if (!state.initData) {
    $("authError").hidden = false;
    $("authError").textContent = "Открой TG Ракета из Telegram-бота — браузерная версия не получает Telegram-авторизацию.";
    return;
  }
  try {
    await Promise.all([refreshMe(), refreshProjects(), refreshTelegramConnection(), refreshRadarSources()]);
    if (state.projectId) await refreshOverview();
  } catch (error) {
    $("authError").hidden = false;
    $("authError").textContent = error.message;
  }
}

boot();
