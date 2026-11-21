"use strict";
(async () => {
  const $ = id => document.getElementById(id);
  const token = await window.agentWizSession();
  const revision = 1;
  let settings, conversation, active, last, followUp, polling, sending = false;
  const selected = new Set();
  const completed = new Map();
  const element = (tag, text) => Object.assign(document.createElement(tag), {textContent: text});
  const button = (text, fn) => Object.assign(element("button", text), {type: "button", onclick: fn});
  function show(message) {
    $("notice").textContent = message || "";
    $("notice").hidden = !message;
  }
  function report(error) {
    show(error.message.replaceAll("_", " "));
  }
  if (!token) {
    show("Open AgentWiz from the launcher.");
    $("run").disabled = true;
    return;
  }
  async function request(path, body, headers = {}) {
    const method = body === undefined ? "GET" : "POST";
    const response = await fetch(path, {
      method,
      headers: {"X-Session-Token": token, "Content-Type": "application/json", ...headers},
      body: body === undefined ? undefined : body instanceof Blob ? body : JSON.stringify(body),
    });
    if (!response.ok) {
      const info = await response.json().catch(() => ({}));
      throw new Error(info.message || info.code || info.error || String(response.status));
    }
    return response;
  }
  async function api(path, body) {
    return (await request(path, body)).json();
  }
  async function agent(path, body) {
    return api("/api/agent/" + path, body === undefined ? undefined : {format_version: revision, ...body});
  }
  async function loadSettings() {
    settings = await api("/api/chat/settings");
    const previous = $("role").value;
    $("role").replaceChildren(...Object.entries(settings.roles).map(([id, role]) => new Option(role.name, id)));
    $("role").value = settings.roles[previous] ? previous : "fast";
    $("builtins").checked = settings.tools.builtins;
    $("webspell").checked = settings.tools.webspell;
    $("key-status").textContent = settings.key_source === "none" ? "No OpenRouter key" : "Key configured";
    $("web-status").textContent = settings.webspell.replaceAll("_", " ");
    $("workshop").href = settings.workshop_url;
    renderBinding();
    if (settings.starter_install_failures.length) {
      show("Some starter blocks could not be installed. Review the catalogue in AgentWorkshop.");
    }
  }
  function renderBinding() {
    const mode = $("mode").value;
    const binding = settings.roles[$("role").value]?.[mode];
    const profiles = Object.entries(settings.profiles).filter(([, kind]) =>
      mode === "cloud" ? kind === "openrouter" : ["lmstudio", "openai_compat"].includes(kind));
    $("profile").replaceChildren(new Option("Packaged default", ""), ...profiles.map(([name]) => new Option(name, name)));
    $("profile").value = settings.bindings[$("role").value]?.[mode] || "";
    let reason = "";
    if (!binding?.model) reason = "Select a usable profile.";
    else if (mode === "cloud" && conversation?.private) reason = "Start a new chat for Cloud.";
    else if (mode === "local" && !binding.trusted) reason = "Review the Local destination in AgentWorkshop.";
    else if (mode === "cloud" && settings.key_source === "none") reason = "Set an OpenRouter key in Settings.";
    $("model").textContent = [binding?.model, reason].filter(Boolean).join(" · ");
    $("model").title = [binding?.routing_mode, binding?.provider, binding?.data_collection,
      binding?.zdr ? "ZDR requested. Account enforcement may require separate verification." : ""].filter(Boolean).join(" · ");
    $("run").disabled = Boolean(reason || active || sending);
    $("stop").hidden = !active;
    $("new-chat").disabled = Boolean(active || sending);
    $("verify-webspell").disabled = mode !== "cloud" || Boolean(conversation?.private || active || sending) || !$("webspell").checked;
  }
  function renderFiles() {
    const rows = (conversation?.documents || []).map(item => {
      const label = element("label", "");
      const check = Object.assign(document.createElement("input"), {type: "checkbox", checked: selected.has(item.id)});
      check.onchange = () => check.checked ? selected.add(item.id) : selected.delete(item.id);
      label.append(check, " ", item.name, item.local_only ? " · Local only" : " · Cloud eligible");
      if (item.kind === "draft") label.append(" ", button("Download", () => downloadDraft(item).catch(report)));
      return label;
    });
    $("documents").replaceChildren(...rows);
  }
  function download(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = Object.assign(document.createElement("a"), {href: url, download: name});
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }
  async function downloadDraft(item) {
    const response = await request("/api/agent/drafts/" + encodeURIComponent(item.id) + "?conversation=" + encodeURIComponent(conversation.id));
    download(await response.blob(), item.name);
  }
  function renderRun(run) {
    last = run;
    $("answer").textContent = run.answer || run.partial || "";
    const sources = (run.sources || []).map(source => {
      const parsed = new URL(source);
      if (!["https:", "http:"].includes(parsed.protocol)) return null;
      const item = element("li", "");
      item.append(Object.assign(element("a", source), {href: source, target: "_blank", rel: "noopener noreferrer"}));
      return item;
    }).filter(Boolean);
    $("sources").replaceChildren(...sources);
    $("outcome").textContent = run.state === "running" ? "Running" : (run.stop_detail || run.stop_reason || "").replaceAll("_", " ");
    $("follow-up").hidden = !run.answer || run.state === "running";
    const cards = [...completed.values()].flatMap(entry => entry.trace || []).concat(run.trace || []).map(action => {
      const card = document.createElement("details");
      card.dataset.state = action.outcome;
      card.append(element("summary", [action.identity?.builtin || action.identity?.block || action.identity?.tool || action.capability,
        action.outcome].filter(Boolean).join(" · ")), element("pre", action.result_text || action.code || ""));
      return card;
    });
    $("activity").replaceChildren(...cards);
    const drafts = (run.drafts || []).map(item => button("Download " + item.name, () => downloadDraft(item).catch(report)));
    if (run.answer) drafts.push(button("Download answer", () => download(new Blob([run.answer], {type: "text/plain"}), "answer.txt")));
    $("drafts").replaceChildren(...drafts);
    const approvals = $("approval");
    approvals.hidden = !run.pending_approval;
    approvals.replaceChildren();
    if (run.pending_approval) {
      const approval = run.pending_approval;
      const send = decision => agent("approve", {run_id: run.run_id, call_id: approval.call_id, decision}).catch(report);
      approvals.append(element("strong", "Review external tool request"),
        element("p", approval.destination?.url || ""), element("pre", approval.arguments_text || ""),
        button("Approve", () => send("approve")), button("Deny", () => send("deny")));
    }
  }
  async function refresh() {
    const view = await agent("conversation");
    conversation = view.conversation;
    active = view.active_run;
    const recent = conversation?.recent_runs || [];
    const run = active || recent.at(-1);
    for (const id of completed.keys()) {
      if (!recent.includes(id) || id === run) completed.delete(id);
    }
    for (const id of recent) {
      if (id !== run && !completed.has(id)) completed.set(id, await agent("runs/" + encodeURIComponent(id)));
    }
    if (run) renderRun(await agent("runs/" + encodeURIComponent(run)));
    renderFiles();
    renderBinding();
  }
  function beginPolling() {
    if (polling) return;
    polling = setInterval(async () => {
      try { await refresh(); }
      catch (error) { report(error); clearInterval(polling); polling = null; }
      if (!active && !sending) { clearInterval(polling); polling = null; }
    }, 600);
  }
  async function newChat() {
    if (conversation?.private) {
      const confirm = $("private-close");
      confirm.returnValue = "cancel";
      confirm.showModal();
      await new Promise(resolve => confirm.addEventListener("close", resolve, {once: true}));
      if (confirm.returnValue !== "close") return;
      await agent("conversation/close", {conversation: conversation.id, confirm: true});
    }
    conversation = (await agent("conversation", {})).conversation;
    $("mode").value = "cloud";
    selected.clear();
    completed.clear();
    followUp = last = null;
    for (const id of ["answer", "activity", "outcome", "drafts", "follow-up-label", "sources"]) $(id).replaceChildren();
    $("follow-up").hidden = true;
    $("approval").hidden = true;
    $("task").value = "";
    renderFiles();
    renderBinding();
  }
  async function connectWeb() {
    await api("/api/chat/webspell", {mode: $("mode").value});
    await loadSettings();
  }
  $("new-chat").onclick = () => newChat().catch(report);
  $("mode").onchange = $("role").onchange = renderBinding;
  $("verify-webspell").onclick = () => connectWeb().catch(report);
  $("task").oninput = () => {
    $("task").style.height = "auto";
    $("task").style.height = $("task").scrollHeight + "px";
  };
  $("builtins").onchange = $("webspell").onchange = async event => {
    try {
      await api("/api/chat/tools", {[event.target.id]: event.target.checked});
      await loadSettings();
    } catch (error) { report(error); }
  };
  $("bind").onclick = async () => {
    const bindings = structuredClone(settings.bindings);
    const role = $("role").value;
    const mode = $("mode").value;
    bindings[role] ||= {};
    if ($("profile").value) bindings[role][mode] = $("profile").value;
    else delete bindings[role][mode];
    try {
      await api("/api/chat/bindings", {revision: settings.binding_revision, bindings});
      await loadSettings();
    } catch (error) { report(error); }
  };
  $("save-key").onclick = async () => {
    try {
      await api("/api/openrouter-key", {revision: settings.revision, api_key: $("key").value});
      await loadSettings();
      show("");
    } catch (error) { report(error); }
    finally { $("key").value = ""; }
  };
  $("follow-up").onclick = () => {
    followUp = last.run_id;
    $("follow-up-label").hidden = false;
    $("follow-up-label").replaceChildren("Following up on the previous answer. ",
      button("Clear", () => { followUp = null; $("follow-up-label").hidden = true; }));
    $("task").focus();
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
    try {
      if (!conversation) conversation = (await agent("conversation", {})).conversation;
      for (const [index, file] of Array.from($("files").files).entries()) {
        const known = new Set(conversation.documents.map(item => item.id));
        await request("/api/agent/documents", file, {
          "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name),
          "X-Conversation": conversation.id, "X-Local-Only": String($("labels").querySelectorAll("input")[index].checked),
        });
        conversation = (await agent("conversation")).conversation;
        conversation.documents.filter(item => !known.has(item.id)).forEach(item => selected.add(item.id));
      }
      renderFiles();
    } catch (error) { report(error); }
  };
  $("stop").onclick = () => active && agent("stop", {run_id: active}).catch(report);
  $("run").onclick = async () => {
    if (!$("task").value.trim() || sending || active) return;
    try {
      sending = true;
      renderBinding();
      show("");
      if (!conversation) conversation = (await agent("conversation", {})).conversation;
      const protectedSelection = conversation.documents.some(i => selected.has(i.id) && i.local_only);
      if ($("mode").value === "cloud" && settings.webspell === "verify_connection" && $("webspell").checked && !conversation.private && !protectedSelection) {
        await connectWeb().catch(() => {});
      }
      beginPolling();
      const response = await request("/api/agent/run", {
        format_version: revision, revision: settings.revision, mode: $("mode").value, role: $("role").value,
        conversation: conversation.id, message: $("task").value, items: [...selected], follow_up: followUp,
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
          if (event.type === "started") { active = event.run_id; renderBinding(); }
          if (event.type === "text") $("answer").textContent += event.text || "";
        }
      }
      followUp = null;
      $("follow-up-label").hidden = true;
      $("task").value = "";
    } catch (error) { report(error); }
    finally { sending = false; await refresh().catch(report); }
  };
  await loadSettings();
  await refresh();
  if (conversation?.private) { $("mode").value = "local"; renderBinding(); }
  if (active) beginPolling();
})().catch(error => {
  const notice = document.getElementById("notice");
  notice.hidden = false;
  notice.textContent = error.message;
});
