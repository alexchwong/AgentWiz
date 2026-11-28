"use strict";
// CEO mode inside the Chat thread: representative turns, handoffs, live CEO runs and original reports.
// chat.js owns the conversation and the composer and calls the factory below once it has a session.
window.agentWizCEO = ctx => {
  const $ = id => document.getElementById(id);
  const {element, button, markdown, usageText} = ctx;
  const optionsKey = "agentwiz-ceo-options";
  const STATE = {starting: "Starting", planning: "Planning", executing: "Executing", checking: "Checking",
    awaiting_approval: "Awaiting approval", reporting: "Writing report", finished: "Finished"};
  const OUTCOME = {complete: "Complete", partial: "Partial", needs_clarification: "Needs clarification",
    blocked: "Blocked", failed: "Failed", stopped: "Stopped"};
  const BADGE = {queued: "Queued", running: "Running", awaiting_approval: "Awaiting approval", complete: "Complete",
    failed: "Failed", blocked: "Blocked", cancelled: "Cancelled", not_run: "Not run"};
  const POLICY = {E: "E · Unrestricted CEO", A: "Owner trial · Policy A · Solo", B: "Owner trial · Policy B · Tools/Blocks",
    C: "Owner trial · Policy C · Reviewed workflow", D: "Owner trial · Policy D · Specialist structure"};
  const nodes = new Map();
  const live = new Map();
  const reports = new Map();
  const briefs = new Map();
  let settings = null, modelInfo = null, modelKey = null, intake = null, entries = [], polling = false;
  const readable = code => String(code || "").replaceAll("_", " ");

  sessionStorage.removeItem("agentwiz-company-run");      // the retired dialog's reconnect key

  async function request(path, body) {
    return ctx.request("/api/company/" + path, body === undefined ? undefined : {format_version: 1, ...body});
  }
  const api = async (path, body) => (await request(path, body)).json();

  // --- options -------------------------------------------------------------------------------------------------
  function options() {
    let saved = {};
    try { saved = JSON.parse(sessionStorage.getItem(optionsKey) || "{}"); } catch { saved = {}; }
    return {policy: "E", preset: "", attempts: 12, seconds: 300, eligible: [], ...saved};
  }
  function saveOptions() {
    const value = {policy: $("ceo-policy").value, preset: $("ceo-preset").value,
      attempts: $("ceo-attempts").valueAsNumber || 12, seconds: $("ceo-seconds").valueAsNumber || 300,
      eligible: [...$("ceo-eligible").selectedOptions].map(option => option.value)};
    sessionStorage.setItem(optionsKey, JSON.stringify(value));
    $("ceo-preset-row").hidden = value.policy !== "C";
    renderAll();
  }
  async function loadSettings() {
    settings = await api("settings");
    const chosen = options();
    $("ceo-class").replaceChildren(...settings.classes.map(value => new Option(value, value)));
    $("ceo-eligible").replaceChildren(...settings.bindings.map(row => {
      const option = new Option(row.model + (row.profile ? " · " + row.profile : ""), row.id);
      option.selected = chosen.eligible.includes(row.id);
      return option;
    }));
    $("ceo-preset").replaceChildren(new Option("Choose automatically", ""), ...settings.workflow_presets.map(row =>
      new Option(`${row.title} · ${row.model_calls + 2} minimum attempts`, row.id)));
    $("ceo-policy").value = chosen.policy;
    $("ceo-preset").value = settings.workflow_presets.some(row => row.id === chosen.preset) ? chosen.preset : "";
    $("ceo-attempts").value = chosen.attempts;
    $("ceo-seconds").value = chosen.seconds;
    $("ceo-preset-row").hidden = chosen.policy !== "C";
    return settings;
  }
  async function refreshModel(force = false) {
    const choice = ctx.modelChoice();
    const key = JSON.stringify([choice, ctx.revision()]);
    if (!force && key === modelKey) return modelInfo;
    modelKey = key;
    if (choice === "local") {
      modelInfo = {eligible: false, reason: "cloud_model_required"};
    } else {
      try { modelInfo = await api("model", {revision: ctx.revision(), model_choice: choice}); }
      catch (error) { modelInfo = {eligible: false, reason: error.message}; }
    }
    $("ceo-class").value = modelInfo.class || "Unknown";
    $("ceo-model-line").textContent = modelInfo.model ? routeText() : "No eligible Cloud model is selected.";
    ctx.renderControls();
    renderAll();
    return modelInfo;
  }
  function routeText() {
    const route = modelInfo?.route;
    if (!modelInfo?.model) return "Choose an OpenRouter model";
    return ["Cloud · OpenRouter", modelInfo.model, route?.routing_mode && "routing " + route.routing_mode,
      route?.data_collection && "data collection " + route.data_collection, route?.zdr ? "ZDR requested" : "",
      "Class " + (modelInfo.class || "Unknown")].filter(Boolean).join(" · ");
  }
  function modelReason() {
    if (!modelInfo) return "";
    if (modelInfo.eligible) return "";
    return {cloud_model_required: "CEO runs use Cloud models. Choose an OpenRouter model.",
      missing_credential: "Set your key using OpenRouter key."}[modelInfo.reason] || readable(modelInfo.reason);
  }

  // --- intake and commands ------------------------------------------------------------------------------------
  function configuration(conversation) {
    const chosen = options();
    const body = {revision: ctx.revision(), conversation, model_choice: ctx.modelChoice(), policy: chosen.policy,
      attempts: chosen.attempts, seconds: chosen.seconds, eligible_bindings: chosen.eligible};
    if (chosen.policy === "C" && chosen.preset && settings) {
      const preset = settings.workflow_presets.find(row => row.id === chosen.preset);
      if (preset) body.workflow_preset = {id: preset.id, revision: preset.revision};
    }
    return body;
  }
  async function ensureIntake(fresh = false) {
    const conversation = await ctx.ensureConversation();
    if (!settings) await loadSettings();
    const body = configuration(conversation);
    const key = JSON.stringify(body);
    if (fresh || !intake || intake.key !== key || intake.used) {
      intake = {id: (await api("intakes", body)).id, key, used: false};
    }
    return intake.id;
  }
  async function withIntake(fn) {
    try { return await fn(await ensureIntake()); }
    catch (error) {
      if (!["intake_unavailable", "allowance_reserved", "not_found", "stale_revision"].includes(error.message)) throw error;
      if (error.message === "stale_revision") await ctx.reloadSettings();
      return fn(await ensureIntake(true));
    }
  }
  async function send(message) {
    try { await withIntake(id => api(`intakes/${id}/message`, {text: message})); }
    finally { await ctx.refresh(); }
  }
  async function createHandoff(brief, supersedes) {
    const body = supersedes ? {brief, supersedes} : {brief};
    await withIntake(id => api(`intakes/${id}/handoffs`, body));
    await ctx.refresh();
  }
  async function startRun(entry) {
    const view = await withIntake(id => api("runs", {intake_id: id, handoff_id: entry.id, brief_sha256: entry.brief_sha256,
      revision: ctx.revision(), client_request_id: crypto.randomUUID()}));
    intake.used = true;
    live.set(view.id, view);
    await ctx.refresh();
    poll();
  }
  async function stop(active) {
    if (active.kind === "run") await api(`runs/${active.id}/stop`, {});
    else if (intake) {
      await api(`intakes/${intake.id}/stop`, {});
      intake = null;
    }
  }
  async function poll() {
    const active = ctx.companyActive();
    if (polling || active?.kind !== "run") return;
    polling = true;
    try {
      const view = await api("runs/" + active.id);
      live.set(view.id, view);
      const entry = entries.find(row => row.id === view.id);
      if (entry) ctx.keepScroll(() => renderEntry(entry));
      if (view.outcome) await ctx.refresh();
    } catch (error) { ctx.report(error); }
    finally {
      polling = false;
      if (ctx.companyActive()?.kind === "run") setTimeout(poll, 500);
    }
  }

  // --- rendering ---------------------------------------------------------------------------------------------
  function sync(conversation) {
    entries = conversation?.company || [];
    const ids = new Set(entries.map(entry => entry.id));
    for (const [id, refs] of nodes) {
      if (!ids.has(id)) { refs.root.remove(); nodes.delete(id); }
    }
    for (const entry of entries) renderEntry(entry);
    if (ctx.companyActive()?.kind === "run") poll();
    return nodes;
  }
  function renderAll() { for (const entry of entries) renderEntry(entry, true); }
  function node(id) { return nodes.get(id)?.root; }

  function renderEntry(entry, force = false) {
    let refs = nodes.get(entry.id);
    if (!refs) {
      refs = {root: element("div", "", "turn ceo-entry")};
      refs.root.dataset.entry = entry.id;
      nodes.set(entry.id, refs);
    }
    const model = entry.type === "run" ? runModel(entry) : entry;
    const key = JSON.stringify([model, entry.type === "handoff" ? [modelInfo, options(), briefs.get(entry.id), ctx.busy(), ctx.privateChat()] : null,
      reports.get(entry.id)?.state, refs.editing]);
    if (!force && key === refs.key) return;
    refs.key = key;
    ({user: renderUser, representative: renderRepresentative, handoff: renderHandoff, run: renderRun})[entry.type](refs, entry, model);
  }

  function renderUser(refs, entry) {
    refs.root.dataset.test = "ceo-user";
    const parts = [element("span", "To CEO", "ceo-tag"), element("p", entry.text, "prompt")];
    if (entry.error) parts.push(element("p", "No representative reply: " + readable(entry.error), "ceo-error"));
    refs.root.replaceChildren(...parts);
  }

  function renderRepresentative(refs, entry) {
    refs.root.dataset.test = "ceo-representative";
    const head = element("strong", entry.commentary_on ? "Representative commentary" : "Representative");
    const body = element("div", "", "answer");
    body.append(markdown.render(entry.text));
    const parts = [head, body];
    if (entry.commentary_on) parts.push(element("p", "Not part of the CEO report.", "ceo-muted"));
    parts.push(element("p", [entry.model, "Representative usage · " + usageText(entry.usage)].filter(Boolean).join(" · "), "usage"));
    refs.root.replaceChildren(...parts);
  }

  async function loadBrief(entry) {
    if (!briefs.has(entry.id)) {
      try { briefs.set(entry.id, (await api("handoffs/" + entry.id)).brief); }
      catch (error) { briefs.set(entry.id, null); ctx.report(error); }
    }
    renderEntry(entry);
  }

  function renderHandoff(refs, entry) {
    refs.root.dataset.test = "ceo-handoff";
    const card = element("section", "", "ceo-card");
    const origin = {direct: " · Direct brief", edited: " · Edited by you"}[entry.origin] || "";
    card.append(element("h3", "Representative → CEO · Handoff" + origin));
    const list = element("dl", "");
    const row = (label, value) => {
      const dd = element("dd", "");
      if (value instanceof Node) dd.append(value); else dd.textContent = value;
      list.append(element("dt", label), dd);
    };
    if (entry.summary && entry.origin === "representative") {
      row("Objective", entry.summary.objective);
      const constraints = element("ul", "");
      constraints.append(...(entry.summary.constraints.length ? entry.summary.constraints : ["None stated"])
        .map(item => element("li", item)));
      row("Constraints", constraints);
      row("Deliverable", entry.summary.deliverable);
    } else {
      card.append(element("p", entry.origin === "direct" ? "Direct brief — no representative summary. Review the exact text."
        : entry.origin === "edited" ? "Edited by you — summary withdrawn. Review the exact text."
        : "Summary unavailable — review the exact text.", "ceo-muted"));
    }
    if (entry.state === "draft") {
      row("Route", routeText());
      row("Policy", POLICY[options().policy] || options().policy);
    }
    row("Material", "Typed text in this brief only. No attachments or earlier chat turns are sent.");
    card.append(list);
    const exact = element("details", "");
    exact.dataset.test = "ceo-exact-text";
    exact.append(element("summary", "Exact text sent to the CEO (Cloud)"));
    const text = briefs.get(entry.id);
    if (refs.editing) {
      const area = Object.assign(document.createElement("textarea"), {rows: 6, value: text || ""});
      area.dataset.test = "ceo-edit-text";
      const save = button("Save as new handoff", () => createHandoff(area.value, entry.id)
        .then(() => { refs.editing = false; }).catch(ctx.report));
      save.dataset.test = "ceo-edit-save";
      exact.append(area, element("div", "", "bar"));
      exact.lastChild.append(save, button("Cancel", () => { refs.editing = false; renderEntry(entry); }));
      exact.open = true;
    } else if (text !== undefined) {
      exact.append(element("pre", text === null ? "The exact text is unavailable." : text));
    }
    exact.open = exact.open || refs.exactOpen || (!entry.summary && text !== undefined);
    exact.ontoggle = () => {
      refs.exactOpen = exact.open;
      if (exact.open && !briefs.has(entry.id)) loadBrief(entry);
    };
    if (!entry.summary && !briefs.has(entry.id)) loadBrief(entry);
    card.append(exact);
    const actions = element("div", "", "bar");
    if (entry.state === "draft") {
      const sendButton = button("Send to CEO", () => {
        sendButton.disabled = true;
        startRun(entry).catch(error => { sendButton.disabled = false; ctx.report(error); });
      });
      sendButton.className = "primary";
      sendButton.dataset.test = "ceo-send";
      sendButton.disabled = ctx.busy() || Boolean(modelReason()) || ctx.privateChat();
      const edit = button("Edit exact text", async () => {
        if (!briefs.has(entry.id)) await loadBrief(entry);
        refs.editing = true;
        renderEntry(entry);
      });
      edit.dataset.test = "ceo-edit";
      edit.disabled = ctx.busy();
      actions.append(sendButton, edit);
    } else {
      actions.append(element("span", entry.state === "sent" ? "Sent · see the run below" : "Superseded", "ceo-muted"));
    }
    card.append(actions);
    refs.root.replaceChildren(card);
  }

  function runModel(entry) {
    const view = live.get(entry.id);
    if (view && entry.state === "running") {
      return {...view, state: view.outcome ? "finished" : "running", diagnostics: true};
    }
    if (entry.state !== "running") live.delete(entry.id);
    return {id: entry.id, state: entry.state, outcome: entry.outcome, reason: entry.reason, policy: entry.policy,
      model: entry.model, activity: {operations: entry.operations, updates: entry.updates, summary: entry.summary},
      question: entry.question, report_available: Boolean(entry.report), report_digest: entry.report?.sha256,
      report_size: entry.report?.size, report_status: entry.report?.status, report_withheld: entry.report_withheld,
      usage: entry.usage, pending_approval: null, open_conditions: entry.open_conditions};
  }

  function stripText(run) {
    const summary = run.activity?.summary || {};
    const finished = run.state !== "running";
    let first;
    if (run.state === "interrupted") first = "Interrupted · AgentWiz closed before this run finished. No report was produced.";
    else if (finished) first = OUTCOME[run.outcome] || readable(run.outcome || "Finished");
    else {
      const current = summary.current;
      first = [STATE[summary.state] || "Running", current?.activity, current?.path].filter(Boolean).join(" · ");
      if (current?.served) {
        first += current.served.provider ? ` · served ${current.served.model || "model"} via ${current.served.provider}`
          : " · provider unreported";
      }
    }
    const attempts = summary.model_attempts;
    const second = [attempts && `Model attempts ${attempts.used} of ${attempts.limit}`,
      summary.elapsed_ms != null && `${(summary.elapsed_ms / 1000).toFixed(1)} s`, usageText(run.usage)].filter(Boolean).join(" · ");
    return [first, second];
  }

  function opRow(op, depth, interrupted) {
    const item = element("li", "");
    item.style.marginLeft = depth * 1.1 + "rem";
    item.dataset.test = "ceo-op";
    item.dataset.kind = op.kind;
    const title = element("div", "");
    title.append(element("strong", op.path.map(row => row.label).join(" / ")), " — " + op.activity);
    const stateName = interrupted && ["running", "awaiting_approval"].includes(op.state) ? "unknown" : op.state;
    const badge = element("span", stateName === "unknown" ? "Unknown (interrupted)" : BADGE[op.state] || op.state, "ceo-badge");
    badge.dataset.state = stateName;
    title.append(badge);
    const facts = [];
    if (op.requested_model) {
      let modelText = "Model " + op.requested_model.model;
      if (op.served) modelText += op.served.provider ? ` · served ${op.served.model || "?"} via ${op.served.provider}`
        : ` · served ${op.served.model || "?"} · provider unreported`;
      facts.push(modelText);
    } else if (["tool", "packaged_block", "approval"].includes(op.kind)) facts.push("No model call");
    if (op.started_ms != null) {
      facts.push(op.ended_ms != null ? `${((op.ended_ms - op.started_ms) / 1000).toFixed(1)} s` : "running");
    }
    if (op.usage) facts.push(usageText(op.usage));
    if (op.detail?.evidence_id) facts.push("evidence " + op.detail.evidence_id);
    if (op.detail?.findings != null) facts.push(`${op.detail.findings} finding(s)`);
    if (op.detail?.quotes != null) facts.push(`${op.detail.quotes} verified quotation(s)`);
    item.append(title, element("div", facts.join(" · "), "ceo-muted"));
    if (op.code && op.state !== "complete") {
      const error = element("div", "", "ceo-error");
      error.append((op.reason_text || readable(op.code)) + " ", element("code", op.code));
      item.append(error);
    }
    return item;
  }

  function renderRun(refs, entry, run) {
    refs.root.dataset.test = "ceo-run";
    const parts = [];
    const updates = run.activity?.updates || [];
    const finalUpdate = updates.length && updates.at(-1).kind === "final" ? updates.at(-1) : null;
    const list = element("ul", "", "ceo-updates");
    list.setAttribute("aria-live", "polite");
    for (const update of updates) {
      if (update === finalUpdate) continue;
      const row = element("li", "");
      row.dataset.test = "ceo-update";
      row.dataset.source = update.source;
      row.append(element("strong", "Representative · Progress "), update.text + " ",
        element("span", update.source === "ceo" ? "from the CEO" : "from the run record", "ceo-muted"));
      list.append(row);
    }
    parts.push(list);
    const [first, second] = stripText(run);
    const strip = element("div", "", "ceo-strip");
    strip.dataset.test = "ceo-strip";
    strip.setAttribute("role", "status");
    strip.append(element("strong", first), element("div", second, "ceo-muted"));
    parts.push(strip);

    const operations = run.activity?.operations || [];
    const counts = run.activity?.summary?.counts || {};
    const activity = element("details", "", "activity");
    activity.dataset.test = "ceo-activity";
    const countText = [["running", "running"], ["complete", "complete"], ["queued", "queued"], ["failed", "failed"],
      ["cancelled", "cancelled"], ["not_run", "not run"]].filter(([key]) => counts[key]).map(([key, label]) => `${counts[key]} ${label}`);
    activity.append(element("summary", ["Activity", ...countText].join(" · ")));
    const byId = new Map(operations.map(op => [op.id, op]));
    const children = new Map();
    for (const op of operations) {
      const parent = op.parent && byId.has(op.parent) ? op.parent : null;
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(op);
    }
    const ordered = [];
    const visit = (parent, level) => {
      for (const op of children.get(parent) || []) {
        ordered.push([op, level]);
        if (level < 8) visit(op.id, level + 1);
      }
    };
    visit(null, 0);
    const opsList = element("ol", "", "ceo-ops");
    opsList.append(...ordered.map(([op, level]) => opRow(op, level, run.state === "interrupted")));
    activity.append(opsList);
    if (run.diagnostics) {
      const diagnostics = element("details", "");
      diagnostics.append(element("summary", "Diagnostics"));
      const pre = element("pre", "");
      diagnostics.ontoggle = () => {
        if (diagnostics.open) pre.textContent = JSON.stringify({trace: run.trace, decisions: run.decisions,
          evidence: run.evidence, open_conditions: run.open_conditions, catalogue_revision: run.catalogue_revision}, null, 2);
      };
      diagnostics.append(pre);
      activity.append(diagnostics);
    }
    const failed = operations.some(op => ["failed", "blocked"].includes(op.state)) || ["failed", "blocked"].includes(run.outcome);
    activity.open = refs.activityOpen ?? (failed && !refs.autoOpened);
    if (failed) refs.autoOpened = true;
    activity.ontoggle = () => { refs.activityOpen = activity.open; };
    parts.push(activity);

    const approval = run.pending_approval;
    if (approval) {
      const box = element("section", "", "approval");
      box.dataset.test = "ceo-approval";
      box.setAttribute("aria-live", "assertive");
      const toolOp = [...operations].reverse().find(op => op.kind === "tool" && op.state === "running");
      const decide = decision => api(`runs/${run.id}/approval`, {call_id: approval.call_id,
        arguments_sha256: approval.arguments_sha256, decision}).then(() => {
        for (const control of box.querySelectorAll("button")) control.disabled = true;
      }).catch(ctx.report);
      const approve = button("Approve exact call", () => decide("approve"));
      approve.dataset.test = "ceo-approve";
      const deny = button("Deny", () => decide("deny"));
      deny.dataset.test = "ceo-deny";
      box.append(element("strong", "Allow this read-only tool call?"),
        element("p", toolOp ? toolOp.path.map(row => row.label).join(" / ") : approval.capability),
        element("p", approval.capability + " · " + (approval.destination?.url || "")),
        element("pre", approval.arguments_text || ""),
        element("p", `Expires in ${Math.round(approval.expires_in_s)} s`, "ceo-muted"), approve, deny);
      parts.push(box);
    }
    if (run.question) {
      const box = element("section", "", "ceo-question");
      box.dataset.test = "ceo-question";
      box.append(element("strong", "CEO question (relayed by the representative)"), element("p", run.question),
        element("p", "Reply in CEO mode; the representative will prepare a revised brief for a new run.", "ceo-muted"));
      parts.push(box);
    }
    if (run.report_available) parts.push(reportBlock(entry, run));
    else if (run.report_withheld) {
      parts.push(element("p", "The saved report was withheld from history because it contained a credential.", "ceo-error"));
    }
    if (finalUpdate) {
      const delivery = element("p", "");
      delivery.dataset.test = "ceo-delivery";
      delivery.append(element("strong", "Representative · "), finalUpdate.text);
      parts.push(delivery);
    }
    refs.root.replaceChildren(...parts);
  }

  function reportBlock(entry, run) {
    const block = element("article", "", "ceo-report");
    block.dataset.test = "ceo-report";
    const status = run.report_status || run.outcome;
    block.append(Object.assign(element("header", `CEO report · original, unchanged · Status: ${readable(status)}`)));
    const state = reports.get(entry.id);
    if (!state) {
      reports.set(entry.id, {state: "loading"});
      request(`runs/${entry.id}/report`).then(response => response.text()).then(raw => {
        reports.set(entry.id, {state: "ready", raw});
        renderEntry(entry);
      }).catch(error => {
        reports.set(entry.id, {state: "error", code: error.message});
        renderEntry(entry);
      });
      block.append(element("p", "Loading the report…", "ceo-muted"));
      return block;
    }
    if (state.state === "loading") {
      block.append(element("p", "Loading the report…", "ceo-muted"));
      return block;
    }
    if (state.state === "error") {
      block.append(element("p", state.code === "report_integrity"
        ? "The saved report failed its integrity check and is not shown." : "Report unavailable: " + readable(state.code), "ceo-error"));
      return block;
    }
    const body = element("div", "", "answer");
    body.append(markdown.render(state.raw));
    const copy = button("Copy", () => navigator.clipboard.writeText(state.raw).catch(ctx.report));
    copy.dataset.test = "ceo-copy";
    const download = button("Download .md.txt", async () => {
      try {
        const response = await request(`runs/${entry.id}/report`);
        ctx.download(await response.blob(), `agentwiz-ceo-${entry.id}.md.txt`);
      } catch (error) { ctx.report(error); }
    });
    download.dataset.test = "ceo-download";
    const actions = element("div", "", "bar turn-actions");
    actions.append(copy, download);
    block.append(body, actions, element("p", `sha256 ${String(run.report_digest || "").slice(0, 12)}… · ${run.report_size} bytes`, "usage"));
    return block;
  }

  // --- options dialog wiring ------------------------------------------------------------------------------------
  $("ceo-options-open").onclick = async () => {
    $("ceo-options").showModal();
    try {
      await loadSettings();
      await refreshModel(true);
    } catch (error) { ctx.report(error, "ceo-options-status"); }
  };
  for (const id of ["ceo-policy", "ceo-preset", "ceo-attempts", "ceo-seconds", "ceo-eligible"]) $(id).onchange = saveOptions;
  $("ceo-example").onclick = async () => {
    try {
      if (!settings) await loadSettings();
      $("ceo-direct").value = settings.example;
    } catch (error) { ctx.report(error, "ceo-options-status"); }
  };
  $("ceo-direct-handoff").onclick = async () => {
    const brief = $("ceo-direct").value;
    if (!brief.trim()) return;
    try {
      await createHandoff(brief);
      $("ceo-direct").value = "";
      $("ceo-options").close();
    } catch (error) { ctx.report(error, "ceo-options-status"); }
  };
  $("ceo-save-class").onclick = async () => {
    try {
      const choice = ctx.modelChoice();
      if (choice === "local") throw new Error("cloud_model_required");
      settings = await api("model-class", {revision: ctx.revision(), model_choice: choice, class: $("ceo-class").value});
      intake = null;
      await refreshModel(true);
      ctx.report(new Error("Intelligence class saved."), "ceo-options-status");
    } catch (error) { ctx.report(error, "ceo-options-status"); }
  };

  return {sync, node, send, stop, poll, refreshModel, modelReason, loadSettings,
    update: () => { for (const entry of entries) renderEntry(entry); },
    modelClass: () => modelInfo?.class || null,
    hasEntries: () => entries.length > 0,
    lastIsFinishedRun: () => {
      const last = ctx.lastTimelineItem();
      const entry = last && last[0] === "company" ? entries.find(row => row.id === last[1]) : null;
      return Boolean(entry && entry.type === "run" && entry.state !== "running");
    }};
};
