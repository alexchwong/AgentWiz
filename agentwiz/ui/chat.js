"use strict";
(async () => {
  const $ = id => document.getElementById(id);
  const token = await window.agentWizSession();
  const markdown = window.agentWizMarkdown;
  const backend = {openrouter: "OpenRouter", lmstudio: "LM Studio", openai_compat: "llama-server"};
  const element = (tag, text, className = "") => Object.assign(document.createElement(tag), {textContent: text, className});
  const button = (text, fn) => Object.assign(element("button", text), {type: "button", onclick: fn});
  let settings, conversation, active, sending = false, switching = false, discovering = false;
  let choice, savedChats = [], lastHistoryRun, lookupSequence = 0, scrollFrame = 0;
  let models = [], visibleModels = [], selectedModel = -1, currentRefresh;
  const selected = new Set();
  const turns = new Map();
  const catalogs = new Map();
  const mobile = matchMedia("(max-width: 690px)");
  const modeFor = kind => kind === "openrouter" ? "cloud" : "local";

  function status(id, message) {
    $(id).textContent = message || "";
    $(id).hidden = !message;
  }
  function report(error, id = "notice") {
    status(id, error.message.replaceAll("_", " "));
  }
  if (!token) {
    status("notice", "Open AgentWiz from the launcher.");
    return;
  }

  async function request(path, body, headers = {}) {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: {"X-Session-Token": token, "Content-Type": "application/json", ...headers},
      body: body === undefined ? undefined : body instanceof Blob ? body : JSON.stringify(body),
    });
    if (!response.ok) {
      const info = await response.json().catch(() => ({}));
      throw new Error(info.message || info.code || info.error || String(response.status));
    }
    return response;
  }
  const api = async (path, body) => (await request(path, body)).json();
  const agent = (path, body) => api("/api/agent/" + path, body === undefined ? undefined : {format_version: 1, ...body});
  const defaultBinding = () => settings.roles.fast?.[$("mode").value];
  const effectiveChoice = () => choice && modeFor(choice.kind) === $("mode").value ? choice : null;
  const isBusy = () => Boolean(active || sending || switching || discovering);
  const restrictedSelection = () => Boolean(conversation?.documents.some(i => selected.has(i.id) && i.local_only));

  async function loadSettings() {
    const task = $("chat-task").value;
    settings = await api("/api/chat/settings");
    $("chat-task").replaceChildren(...settings.chat_tasks.map(({id, label}) => new Option(label, id)));
    $("chat-task").value = settings.chat_tasks.some(({id}) => id === task) ? task : "general";
    $("builtins").checked = settings.tools.builtins;
    $("webspell").checked = settings.tools.webspell;
    $("key-status").textContent = {
      none: "No OpenRouter key configured.", session: "Using the session override.",
      "env:OPENROUTER_API_KEY": "Using the process environment key.", file: "Using the configured .env key.",
    }[settings.key_source] || "OpenRouter key configured.";
    $("clear-key").disabled = settings.key_source !== "session";
    $("web-status").textContent = settings.webspell.replaceAll("_", " ");
    $("workshop").href = settings.workshop_url;
    renderControls();
    if (settings.starter_install_failures.length) {
      status("notice", "Some starter blocks could not be installed. Review the catalogue in AgentWorkshop.");
    }
  }

  function renderControls() {
    const mode = $("mode").value;
    const binding = defaultBinding();
    const override = effectiveChoice();
    const kind = override?.kind || binding?.kind;
    const model = override?.model || binding?.model;
    const listed = catalogs.get(kind)?.find(entry => entry.id === model);
    const busy = isBusy();
    let reason = "";
    if (mode === "cloud" && conversation?.private) reason = "This chat is Local only. Start a new chat to use Cloud.";
    else if (mode === "cloud" && restrictedSelection()) reason = "A selected attachment is Local only. Change mode to continue.";
    else if (!model) reason = "Choose a model or set a default model.";
    else if (mode === "local" && (override ? listed?.trusted === false : !binding?.trusted)) {
      reason = "Review and trust this model destination in AgentWorkshop.";
    } else if (kind === "openrouter" && settings.key_source === "none") reason = "Set your key using OpenRouter key.";
    status("binding-notice", reason);

    const modelButton = $("model-picker-open");
    const policy = (!override || override.kind === binding?.kind) ? binding : null;
    modelButton.textContent = model || "Choose model";
    modelButton.title = [backend[kind], model, policy?.routing_mode, policy?.provider,
      policy?.data_collection, policy?.zdr ? "ZDR requested" : ""].filter(Boolean).join(" · ");
    modelButton.setAttribute("aria-label", model ? "Choose model · " + model : "Choose model");
    modelButton.disabled = busy;

    const reasoning = $("reasoning");
    const level = reasoning.value;
    const levels = listed?.reasoning ?? settings.reasoning_levels[kind] ?? [];
    const optionsKey = [kind, ...levels].join("|");
    if (reasoning.dataset.options !== optionsKey) {
      const options = levels.map(value => new Option(value[0].toUpperCase() + value.slice(1), value));
      reasoning.replaceChildren(new Option(kind === "openai_compat" ? "Server default" : "Default", ""), ...options);
      reasoning.dataset.options = optionsKey;
    }
    reasoning.value = levels.includes(level) ? level : "";
    reasoning.disabled = busy || !levels.length;
    $("chat-task").disabled = busy;

    $("run").disabled = Boolean(reason || busy || !$("task").value.trim());
    $("run").hidden = Boolean(active);
    $("stop").hidden = !active;
    $("new-chat").disabled = busy;
    $("mode").disabled = busy;
    $("attach").disabled = busy;
    $("bind").disabled = busy;
    $("save-chat").disabled = busy || mode !== "cloud";
    $("verify-webspell").disabled = mode !== "cloud" || Boolean(conversation?.private || busy) || !$("webspell").checked;
    $("key-open").disabled = Boolean(active || sending || switching);
    const profileOptions = Object.entries(settings.profiles).filter(([, source]) =>
      mode === "cloud" ? source === "openrouter" : ["lmstudio", "openai_compat"].includes(source));
    const profile = $("profile");
    const profileKey = JSON.stringify([mode, settings.binding_revision, profileOptions]);
    if (profile.dataset.options !== profileKey) {
      profile.replaceChildren(new Option("Packaged default", ""), ...profileOptions.map(([name]) => new Option(name, name)));
      profile.value = settings.bindings.fast?.[mode] || "";
      profile.dataset.options = profileKey;
    }
  }

  function renderFiles() {
    const rows = (conversation?.documents || []).map(item => {
      const label = element("label", "");
      const check = Object.assign(document.createElement("input"), {type: "checkbox", checked: selected.has(item.id)});
      check.setAttribute("aria-label", item.name);
      check.onchange = () => {
        if (check.checked) selected.add(item.id);
        else selected.delete(item.id);
        renderControls();
      };
      label.append(check, " ", item.name, item.local_only ? " · Local only" : " · Cloud eligible");
      if (item.kind === "draft") label.append(" ", button("Download", () => downloadDraft(item).catch(report)));
      return label;
    });
    $("documents").replaceChildren(...rows);
  }

  function download(blob, name) {
    const url = URL.createObjectURL(blob);
    Object.assign(element("a"), {href: url, download: name}).click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  async function downloadDraft(item) {
    const response = await request("/api/agent/drafts/" + encodeURIComponent(item.id)
      + "?conversation=" + encodeURIComponent(conversation.id));
    download(await response.blob(), item.name);
  }

  function keepScroll(change, force = false) {
    const scroll = $("scrollback");
    const atBottom = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop < 80;
    change();
    if (atBottom || force) scroll.scrollTop = scroll.scrollHeight;
  }

  function createTurn(run) {
    const root = element("article", "", "turn");
    root.dataset.test = "chat-turn";
    const prompt = element("p", run.message || "", "prompt");
    prompt.dataset.test = "prompt-bubble";
    const activity = element("details", "", "activity");
    activity.dataset.test = "turn-activity";
    const summary = element("summary", "Activity");
    const steps = element("ol", "");
    activity.append(summary, steps);
    const approval = element("div", "", "approval");
    const response = element("div", "", "answer");
    response.dataset.test = "turn-answer";
    const outcome = element("p", "", "turn-status");
    const sources = element("ul", "", "sources");
    const usage = element("p", "", "usage");
    usage.dataset.test = "turn-usage";
    usage.hidden = true;
    const actions = element("div", "", "bar turn-actions");
    root.append(prompt, activity, approval, response, outcome, sources, usage, actions);
    const refs = {root, prompt, activity, summary, steps, approval, response, outcome, sources, usage, actions};
    refs.data = run;
    turns.set(run.run_id, refs);
    $("transcript").append(root);
    return refs;
  }

  function renderAnswer(refs, text) {
    if (refs.renderedText !== text) {
      refs.response.replaceChildren(markdown.render(text));
      refs.renderedText = text;
    }
  }

  const currency = new Intl.NumberFormat("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 9});
  const tokens = new Intl.NumberFormat();
  function usageText(usage) {
    if (!usage) return "Usage unreported";
    const fields = [["input_tokens", "input tokens", tokens], ["output_tokens", "output tokens", tokens],
      ["cost", "cost", currency]];
    return fields.map(([key, label, formatter]) => {
      if (usage[key] == null) return label + " unreported";
      const formatted = formatter.format(usage[key]);
      return (key === "cost" ? "US$" + formatted : formatted + " " + label) +
        (usage.partial.includes(key) ? " (partial)" : "");
    }).join(" · ");
  }

  function renderRun(run, force = false) {
    keepScroll(() => {
      const refs = turns.get(run.run_id) || createTurn(run);
      if (run.state === "running" && refs.data.state === "ended") return;
      refs.data = run;
      if (run.message !== undefined && refs.prompt.textContent !== run.message) refs.prompt.textContent = run.message;
      const text = run.answer || run.partial || "";
      const modelTurn = run.counters?.model_turns || 0;
      if (run.state !== "running" || modelTurn > (refs.modelTurn || 0)
          || (modelTurn === (refs.modelTurn || 0) && text.length >= (refs.renderedText?.length || 0))) {
        refs.modelTurn = modelTurn;
        renderAnswer(refs, text);
      }
      const trace = run.trace || [];
      const stepKey = JSON.stringify(trace);
      if (stepKey !== refs.stepKey) {
        refs.steps.replaceChildren(...trace.map(action => {
          const row = element("li", "");
          const title = [action.identity?.builtin || action.identity?.block || action.identity?.tool || action.capability,
            action.outcome].filter(Boolean).join(" · ");
          const details = action.result_text || action.code;
          if (details) {
            const group = element("details", "");
            group.append(element("summary", title), element("pre", details));
            row.append(group);
          } else row.textContent = title;
          return row;
        }));
        refs.stepKey = stepKey;
      }
      refs.summary.textContent = trace.length ? "Activity · " + trace.length : run.state === "running" ? "Activity · working" : "Activity";
      refs.activity.hidden = !trace.length && run.state !== "running";
      const outcome = run.state === "running" ? "Working…" :
        run.stop_reason === "answered" ? "" : (run.stop_detail || run.stop_reason || "").replaceAll("_", " ");
      if (refs.outcome.textContent !== outcome) refs.outcome.textContent = outcome;
      refs.outcome.hidden = !outcome;

      const sourceKey = JSON.stringify(run.sources || []);
      if (sourceKey !== refs.sourceKey) {
        refs.sources.replaceChildren(...(run.sources || []).flatMap(url => {
          const link = markdown.safeLink(url, url);
          if (!link) return [];
          const item = element("li", "");
          item.append(link);
          return [item];
        }));
        refs.sources.hidden = !refs.sources.childElementCount;
        refs.sourceKey = sourceKey;
      }

      if (run.state !== "running") {
        const route = run.served_route;
        const identity = route ? [backend[route.kind] || route.kind, route.model,
          route.provider || (route.kind === "openrouter" ? "Provider unreported" : "Direct")].join(" · ") : "";
        refs.usage.textContent = [identity, usageText(run.accounting)].filter(Boolean).join(" · ");
        refs.usage.hidden = false;
      }

      const actionsKey = JSON.stringify([Boolean(run.state !== "running" && text), run.drafts]);
      if (actionsKey !== refs.actionsKey) {
        const actions = (run.drafts || []).map(item => button("Download " + item.name, () => downloadDraft(item).catch(report)));
        if (run.state !== "running" && text) {
          actions.push(button("Copy", () => navigator.clipboard.writeText(refs.renderedText).catch(report)),
            button("Download answer", () => download(new Blob([refs.renderedText], {type: "text/plain"}), "answer.md")));
        }
        refs.actions.replaceChildren(...actions);
        refs.actions.hidden = !actions.length;
        refs.actionsKey = actionsKey;
      }
      const approval = run.pending_approval;
      refs.approval.hidden = !approval;
      if (approval?.call_id !== refs.approvalId) {
        refs.approvalId = approval?.call_id;
        refs.approval.replaceChildren();
        if (approval) {
          const send = decision => agent("approve", {run_id: run.run_id, call_id: approval.call_id, decision})
            .then(() => { for (const control of refs.approval.querySelectorAll("button")) control.disabled = true; })
            .catch(report);
          refs.approval.append(element("strong", "Review external tool request"),
            element("p", approval.destination?.url || ""), element("pre", approval.arguments_text || ""),
            button("Approve", () => send("approve")), button("Deny", () => send("deny")));
        }
      }
      $("empty-chat").hidden = true;
    }, force);
  }

  function setHistoryOpen(open) {
    $("history").hidden = !open;
    $("toggle-history").setAttribute("aria-expanded", String(open));
  }

  async function updateHistory(path, body, clearComposer = false) {
    switching = true;
    renderControls();
    renderHistory();
    try {
      if (currentRefresh) await currentRefresh;
      await api("/api/chat/history/" + path, body);
      if (clearComposer) {
        $("task").value = "";
        resizeComposer();
      }
      await refresh();
      await refreshHistory();
    } finally {
      switching = false;
      renderControls();
      renderHistory();
    }
  }

  async function renameChat(chat) {
    const dialog = $("rename-chat");
    $("chat-title").value = chat.title;
    dialog.returnValue = "cancel";
    dialog.showModal();
    await new Promise(resolve => dialog.addEventListener("close", resolve, {once: true}));
    const title = $("chat-title").value.trim();
    if (dialog.returnValue === "rename" && title) await updateHistory("rename", {id: chat.id, title});
  }

  function renderHistory() {
    const first = turns.values().next().value;
    const saved = savedChats.find(chat => chat.id === conversation?.id);
    $("current-title").textContent = conversation?.private ? "Local only chat" :
      (saved?.title || first?.data.message?.trim().replace(/\s+/g, " ").slice(0, 72) || "Untitled chat");
    status("chat-totals", conversation?.totals ? "Conversation total · " + usageText(conversation.totals) : "");
    $("save-chat-row").hidden = Boolean(conversation?.private);
    $("save-chat").checked = conversation?.saved ?? true;
    const saveWarning = conversation?.saved && $("mode").value === "local"
      ? "Sending in Local only will remove this chat from saved history." : "";
    status("history-status", conversation?.save_error?.replaceAll("_", " ") || saveWarning);
    $("saved-chats").replaceChildren(...savedChats.map(chat => {
      const row = element("li", "", "saved-row");
      const open = button(chat.title, () => switchChat(chat.id).catch(report));
      open.title = chat.ready ? chat.title : chat.title + " · Interrupted or unavailable";
      open.disabled = !chat.ready || isBusy();
      if (chat.id === conversation?.id) open.setAttribute("aria-current", "page");
      const menu = element("details", "", "chat-menu");
      const trigger = element("summary", "⋯");
      trigger.setAttribute("aria-label", "Actions for " + chat.title);
      trigger.dataset.test = "chat-menu";
      const actions = element("div", "", "chat-menu-actions");
      const rename = button("Rename", () => {
        menu.open = false;
        renameChat(chat).catch(report);
      });
      rename.disabled = !chat.ready || isBusy();
      const remove = button("Delete", () => {
        menu.open = false;
        if (confirm("Delete this saved chat from this workspace?")) {
          updateHistory("delete", {id: chat.id}, chat.id === conversation?.id).catch(report);
        }
      });
      remove.disabled = isBusy();
      actions.append(rename, remove);
      menu.append(trigger, actions);
      row.append(open, menu);
      return row;
    }));
    if (!savedChats.length) $("saved-chats").append(element("li", "No saved chats", "turn-status"));
  }

  async function refreshHistory() {
    savedChats = (await api("/api/chat/history")).chats;
    renderHistory();
  }

  function adoptConversation(next, resetMode) {
    conversation = next;
    choice = next?.model_choice || null;
    if (resetMode) $("mode").value = next?.private ? "local" : "cloud";
    if (next?.private) catalogs.delete("openrouter");
    selected.clear();
    turns.clear();
    $("transcript").replaceChildren();
    $("empty-chat").hidden = false;
    $("use-context").checked = true;
    $("chat-task").value = "general";
    $("reasoning").value = "";
  }

  async function readConversation() {
    const view = await agent("conversation");
    const next = view.conversation;
    const changed = next?.id !== conversation?.id;
    if (changed) adoptConversation(next, Boolean(conversation) || Boolean(next?.private));
    else conversation = next;
    active = view.active_run;
    const visible = [...(conversation?.restored_runs || []), ...(conversation?.recent_runs || [])];
    for (const id of visible) {
      const shown = turns.get(id);
      if (id === active || !shown || shown.data.state === "running") {
        try {
          renderRun(await agent("runs/" + encodeURIComponent(id)));
        } catch (error) {
          // Another browser may have switched chats after the manager supplied its run IDs.
          if (error.message !== "no_such_run") throw error;
        }
      }
    }
    const valid = new Set(visible);
    for (const [id, refs] of turns) {
      if (!valid.has(id) && !(sending && refs.data.state === "running")) {
        refs.root.remove();
        turns.delete(id);
      }
    }
    $("empty-chat").hidden = Boolean(turns.size);
    if (changed || !active) renderFiles();
    renderControls();
    renderHistory();
  }

  function refresh() {
    if (!currentRefresh) currentRefresh = readConversation().finally(() => { currentRefresh = null; });
    return currentRefresh;
  }

  async function switchChat(id) {
    if (isBusy() || (id && id === conversation?.id)) return;
    switching = true;
    renderControls();
    renderHistory();
    try {
      if (currentRefresh) await currentRefresh;
      let closePrivate = null;
      if (conversation?.private) {
        const dialog = $("private-close");
        dialog.returnValue = "cancel";
        dialog.showModal();
        await new Promise(resolve => dialog.addEventListener("close", resolve, {once: true}));
        if (dialog.returnValue !== "close") return;
        closePrivate = conversation.id;
      }
      const view = id ? await api("/api/chat/history/open", {id, close_private: closePrivate})
        : await agent("conversation", {close_private: closePrivate});
      adoptConversation(view.conversation, true);
      $("task").value = "";
      resizeComposer();
      status("notice", "");
      if (mobile.matches) setHistoryOpen(false);
      await refresh();
      await refreshHistory();
      $("task").focus();
    } finally {
      switching = false;
      renderControls();
      renderHistory();
    }
  }

  function resizeComposer() {
    const task = $("task");
    task.style.height = "auto";
    task.style.height = task.scrollHeight + "px";
  }

  async function connectWeb() {
    await api("/api/chat/webspell", {mode: $("mode").value});
    await loadSettings();
  }

  function changeMode() {
    if (conversation?.private && $("mode").value === "cloud") catalogs.delete("openrouter");
    renderControls();
    renderHistory();
  }

  for (const close of document.querySelectorAll("[data-close]")) {
    close.onclick = () => $(close.dataset.close).close();
  }
  $("settings-open").onclick = () => $("settings").showModal();
  $("key-open").onclick = () => {
    status("key-status", settings.key_source === "none" ? "No OpenRouter key configured." :
      "OpenRouter key configured · " + settings.key_source);
    $("key-dialog").showModal();
    $("key").focus();
  };
  $("key-dialog").onclose = () => { $("key").value = ""; };
  async function setKey(value) {
    try {
      await api("/api/openrouter-key", {revision: settings.revision, api_key: value});
      catalogs.delete("openrouter");
      await loadSettings();
      $("key-dialog").close();
      status("notice", "");
    } catch (error) { report(error, "key-status"); }
    finally { $("key").value = ""; }
  }
  $("key-form").onsubmit = event => {
    event.preventDefault();
    if ($("key").value.trim()) setKey($("key").value);
  };
  $("clear-key").onclick = () => setKey("");
  $("new-chat").onclick = () => switchChat().catch(report);
  $("toggle-history").onclick = () => {
    setHistoryOpen($("history").hidden);
    if (!$("history").hidden) refreshHistory().catch(report);
  };
  mobile.addEventListener("change", () => setHistoryOpen(!mobile.matches));
  setHistoryOpen(!mobile.matches);
  $("mode").onchange = changeMode;
  $("task").oninput = () => { resizeComposer(); renderControls(); };
  $("task").onkeydown = event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!$("run").disabled) $("composer").requestSubmit();
    }
  };
  $("verify-webspell").onclick = () => connectWeb().catch(error => report(error, "settings-status"));
  $("builtins").onchange = $("webspell").onchange = async event => {
    const control = event.target;
    try {
      await api("/api/chat/tools", {[control.id]: control.checked});
      await loadSettings();
      status("settings-status", "");
    } catch (error) {
      control.checked = settings.tools[control.id];
      report(error, "settings-status");
    }
  };
  $("bind").onclick = async () => {
    const bindings = structuredClone(settings.bindings);
    const role = "fast";
    const mode = $("mode").value;
    bindings[role] ||= {};
    if ($("profile").value) bindings[role][mode] = $("profile").value;
    else delete bindings[role][mode];
    try {
      await api("/api/chat/bindings", {revision: settings.binding_revision, bindings});
      await loadSettings();
      status("settings-status", "Default model updated.");
    } catch (error) { report(error, "settings-status"); }
  };
  $("save-chat").onchange = async event => {
    const enabled = event.target.checked;
    if (!enabled && !confirm("Remove this chat from saved history? The open chat stays available.")) {
      event.target.checked = true;
      return;
    }
    switching = true;
    renderControls();
    try {
      if (!conversation) conversation = (await agent("conversation", {})).conversation;
      const view = await api("/api/chat/history/save", {conversation: conversation.id, enabled});
      conversation = view.conversation;
      await refreshHistory();
    } catch (error) {
      report(error);
    } finally {
      switching = false;
      renderControls();
      renderHistory();
    }
  };

  function closeHistoryMenus(target) {
    for (const menu of document.querySelectorAll(".chat-menu[open]")) {
      if (!menu.contains(target)) menu.open = false;
    }
  }
  document.addEventListener("click", event => closeHistoryMenus(event.target));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      const menu = document.activeElement?.closest(".chat-menu[open]");
      if (menu) menu.querySelector("summary").focus();
      closeHistoryMenus(null);
    }
  });

  function filterModels() {
    const query = $("model-search").value.trim().toLowerCase();
    visibleModels = models.filter(model => query.split(/\s+/).every(term =>
      model.id.toLowerCase().includes(term) || model.name?.toLowerCase().includes(term))).slice(0, 30);
    selectedModel = -1;
    $("model-options").replaceChildren(...visibleModels.map((model, index) => {
      const option = element("li", model.name || model.id);
      option.id = "model-option-" + index;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", "false");
      if (model.name && model.name !== model.id) option.append(element("small", model.id));
      if (modeFor($("source").value) === "local" && !model.trusted) option.append(element("small", "Requires trust in AgentWorkshop"));
      option.onpointerdown = event => event.preventDefault();
      option.onclick = () => chooseModel(model);
      return option;
    }));
    $("model-search").removeAttribute("aria-activedescendant");
    $("model-options").hidden = !visibleModels.length;
    $("model-search").setAttribute("aria-expanded", String(Boolean(visibleModels.length)));
    if (models.length) status("model-status", visibleModels.length ? "" : "No models match.");
  }

  function chooseModel(model) {
    const kind = $("source").value;
    choice = {kind, model: model.id};
    $("mode").value = modeFor(kind);
    $("reasoning").value = "";
    $("model-picker").close();
    renderControls();
    renderHistory();
  }

  async function lookupModels(resetField) {
    const requestId = ++lookupSequence;
    const kind = $("source").value;
    if (resetField) $("model-search").value = "";
    models = catalogs.get(kind) || [];
    filterModels();
    if (kind === "openrouter" && ($("mode").value !== "cloud" || conversation?.private || restrictedSelection())) {
      status("model-status", "Cloud model lookup is unavailable for this private chat or selection.");
      return;
    }
    discovering = true;
    renderControls();
    $("model-refresh").disabled = true;
    $("source").disabled = true;
    $("model-picker").setAttribute("aria-busy", "true");
    status("model-status", "Looking up models…");
    try {
      const listed = (await api("/api/chat/models", {kind, mode: modeFor(kind)})).models;
      if (requestId !== lookupSequence) return;
      catalogs.set(kind, listed);
      models = listed;
      filterModels();
      if (!listed.length) status("model-status", "No models found on this source.");
    } catch (error) {
      if (requestId === lookupSequence) report(error, "model-status");
    } finally {
      discovering = false;
      $("model-refresh").disabled = false;
      $("source").disabled = false;
      $("model-picker").removeAttribute("aria-busy");
      renderControls();
      renderHistory();
    }
  }

  $("model-picker-open").onclick = () => {
    $("source").querySelector('[value="openrouter"]').disabled = Boolean($("mode").value !== "cloud" || conversation?.private || restrictedSelection());
    $("source").value = effectiveChoice()?.kind || defaultBinding()?.kind
      || ($("mode").value === "local" ? "lmstudio" : "openrouter");
    if ($("source").selectedOptions[0]?.disabled) $("source").value = "lmstudio";
    $("model-picker").showModal();
    $("model-search").focus();
    lookupModels(true);
  };
  $("source").onchange = () => lookupModels(true);
  $("model-refresh").onclick = () => lookupModels(false);
  $("model-default").onclick = () => {
    choice = null;
    $("mode").value = modeFor($("source").value);
    $("reasoning").value = "";
    $("model-picker").close();
    renderControls();
    renderHistory();
  };
  $("model-search").oninput = filterModels;
  $("model-search").onkeydown = event => {
    if (event.key === "Escape" && !$("model-options").hidden) {
      event.preventDefault();
      $("model-options").hidden = true;
      $("model-search").setAttribute("aria-expanded", "false");
      $("model-search").removeAttribute("aria-activedescendant");
    } else if (event.key === "Enter" && !$("model-options").hidden && visibleModels.length) {
      event.preventDefault();
      chooseModel(visibleModels[Math.max(0, selectedModel)]);
    } else if (["ArrowDown", "ArrowUp"].includes(event.key) && visibleModels.length) {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      selectedModel = selectedModel < 0 && delta < 0 ? visibleModels.length - 1
        : (selectedModel + delta + visibleModels.length) % visibleModels.length;
      const options = $("model-options");
      options.hidden = false;
      $("model-search").setAttribute("aria-expanded", "true");
      [...options.children].forEach((item, index) => item.setAttribute("aria-selected", String(index === selectedModel)));
      $("model-search").setAttribute("aria-activedescendant", options.children[selectedModel].id);
      options.children[selectedModel].scrollIntoView({block: "nearest"});
    }
  };

  $("attach").onclick = () => {
    $("files").value = "";
    $("labels").replaceChildren();
    $("upload").returnValue = "cancel";
    $("upload").showModal();
  };
  $("files").onchange = () => {
    $("labels").replaceChildren(...Array.from($("files").files, file => {
      const label = element("label", "");
      const check = Object.assign(document.createElement("input"), {type: "checkbox", checked: true});
      label.append(check, " Local only · ", file.name);
      return label;
    }));
  };
  $("upload").onclose = async () => {
    if ($("upload").returnValue !== "attach") return;
    switching = true;
    renderControls();
    try {
      if (!conversation) conversation = (await agent("conversation", {})).conversation;
      for (const [index, file] of Array.from($("files").files).entries()) {
        const response = await request("/api/agent/documents", file, {
          "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name),
          "X-Conversation": conversation.id, "X-Local-Only": String($("labels").querySelectorAll("input")[index].checked),
        });
        const info = await response.json();
        conversation = info.conversation;
        selected.add(info.document.id);
      }
      renderFiles();
    } catch (error) { report(error); }
    finally {
      switching = false;
      renderControls();
      renderHistory();
    }
  };

  $("stop").onclick = () => active && agent("stop", {run_id: active}).catch(report);
  $("composer").onsubmit = async event => {
    event.preventDefault();
    if ($("run").disabled || isBusy()) return;
    const message = $("task").value;
    sending = true;
    renderControls();
    renderHistory();
    status("notice", "");
    let streaming = "", streamingTurn = 0;
    try {
      if (!conversation) conversation = (await agent("conversation", {})).conversation;
      if ($("mode").value === "cloud" && settings.webspell === "verify_connection" && $("webspell").checked &&
          !conversation.private && !restrictedSelection()) await connectWeb().catch(() => {});
      const response = await request("/api/agent/run", {
        format_version: 1, revision: settings.revision, mode: $("mode").value, chat_task: $("chat-task").value,
        conversation: conversation.id, message, items: [...selected], use_context: $("use-context").checked,
        model_choice: effectiveChoice(), task: $("reasoning").value ? {reasoning: $("reasoning").value} : {},
      });
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      for (;;) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += value;
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (!line) continue;
          const event = JSON.parse(line);
          if (event.type === "started") {
            active = event.run_id;
            renderRun({run_id: active, message, state: "running"}, true);
            if ($("task").value === message) $("task").value = "";
            selected.clear();
            renderFiles();
            resizeComposer();
            renderControls();
          } else if (event.type === "text") {
            if (streamingTurn !== event.model_turn) {
              streamingTurn = event.model_turn;
              streaming = "";
            }
            streaming += event.text || "";
            if (!scrollFrame) scrollFrame = requestAnimationFrame(() => {
              scrollFrame = 0;
              const refs = turns.get(event.run_id);
              if (refs?.data.state === "running" && streamingTurn >= (refs.modelTurn || 0)) {
                refs.modelTurn = streamingTurn;
                keepScroll(() => renderAnswer(refs, streaming));
              }
            });
          } else if (event.type === "final" && event.view) {
            renderRun({...event.view, message});
            lastHistoryRun = event.run_id;
          }
        }
      }
    } catch (error) { report(error); }
    finally {
      sending = false;
      await refresh().catch(report);
      await refreshHistory().catch(report);
    }
  };

  async function poll() {
    if (active && !switching) {
      try {
        const previouslyActive = active;
        await refresh();
        if (!active && previouslyActive !== lastHistoryRun) await refreshHistory();
      } catch (error) { report(error); }
    }
    setTimeout(poll, 650);
  }
  await loadSettings();
  await refresh();
  await refreshHistory();
  resizeComposer();
  setTimeout(poll, 650);
})().catch(error => {
  const notice = document.getElementById("notice");
  notice.hidden = false;
  notice.textContent = error.message;
});
