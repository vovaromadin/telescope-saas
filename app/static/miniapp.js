const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
const state = { initData: tg ? tg.initData : "", projects: [], projectId: null, lastSearchId: null };
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
  return response.json();
}

async function refreshMe() {
  const me = await api("/api/app/me");
  $("planBadge").textContent = me.plan.toUpperCase();
  $("usage").textContent = "Использовано " + me.used + " из " + me.monthly_limit + " поисков в этом месяце";
  if (!me.telegram_ready) {
    $("status").textContent = "Поисковый Telegram-аккаунт пока не подключён. Интерфейс работает, но живые результаты будут недоступны.";
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
    item.onclick = function() {
      state.projectId = p.id;
      renderProjects();
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
  } catch (error) {
    $("status").textContent = error.message;
  }
};

function renderResults(rows) {
  $("results").innerHTML = rows.map(function(r) {
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
      '<div class="links">' + links + '</div></div>' +
      '<div class="score">' + Math.round(r.total_score || 0) + '</div></article>';
  }).join("");
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
  $("status").textContent = run.error || "Поиск завершён";
}

$("searchForm").onsubmit = async function(event) {
  event.preventDefault();
  if (!state.projectId) {
    $("status").textContent = "Сначала создай проект.";
    return;
  }
  const button = $("searchButton");
  button.disabled = true;
  $("toolbar").hidden = true;
  $("results").innerHTML = "";
  $("status").textContent = "Ищу публичные сообщества и считаю рейтинг…";
  try {
    const run = await api("/api/app/projects/" + state.projectId + "/searches", {
      method:"POST",
      body:JSON.stringify({query:$("query").value.trim(),limit:30})
    });
    state.lastSearchId = run.id;
    await pollSearch(run.id);
    await refreshMe();
  } catch (error) {
    $("status").textContent = error.status === 402 ? "Лимит тарифа исчерпан. Выбери Pro или Team ниже." : error.message;
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
  if (tg && tg.openLink) {
    const a = document.createElement("a");
    a.href = url;
    a.download = "telescope-search-" + state.lastSearchId + "." + format;
    a.click();
  } else {
    window.open(url, "_blank");
  }
  setTimeout(function(){ URL.revokeObjectURL(url); }, 2000);
}

$("csv").onclick = function(){ download("csv"); };
$("json").onclick = function(){ download("json"); };

document.querySelectorAll("[data-plan]").forEach(function(button) {
  button.onclick = async function() {
    try {
      const result = await api("/api/app/billing/checkout?plan=" + button.dataset.plan, {method:"POST"});
      if (tg && tg.openLink) tg.openLink(result.url);
      else window.location.href = result.url;
    } catch (error) {
      $("status").textContent = error.message;
    }
  };
});

async function boot() {
  if (!state.initData) {
    $("authError").hidden = false;
    $("authError").textContent = "Открой TeleScope из Telegram-бота — браузерная версия не получает Telegram-авторизацию.";
    return;
  }
  try {
    await Promise.all([refreshMe(), refreshProjects()]);
  } catch (error) {
    $("authError").hidden = false;
    $("authError").textContent = error.message;
  }
}

boot();