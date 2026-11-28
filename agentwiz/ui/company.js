"use strict";
(async () => {
  const $ = id => document.getElementById("company-" + id);
  const dialog = document.getElementById("company");
  const token = await window.agentWizSession();
  if (!token) return;

  const reconnectKey = "agentwiz-company-run";
  let settings, intake, configuration, run, submission, pending = false, polling = false;
  const status = message => { $("status").textContent = message; };
  const report = error => status(error.message.replaceAll("_", " "));
  async function request(path, body) {
    const response = await fetch("/api/company/" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {"X-Session-Token": token, "Content-Type": "application/json"},
      body: body === undefined ? undefined : JSON.stringify({format_version: 1, ...body})
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.code || "Company request failed");
    }
    return response;
  }
  const api = async (path, body) => (await request(path, body)).json();
  const busy = () => pending || Boolean(run && !run.outcome);

  function controls() {
    for (const id of ["example", "new", "model", "policy", "preset", "class", "save-class", "attempts", "time", "eligible",
      "brief", "message", "send", "submit"]) $(id).disabled = !settings || busy();
    $("preset-row").hidden = $("policy").value !== "C";
    $("stop").disabled = !(intake || run) || !busy();
    $("download").hidden = !run?.report_available;
  }

  function reply(label, value) {
    const row = document.createElement("p");
    const name = document.createElement("strong");
    name.textContent = label + " ";
    row.append(name, value);
    $("turns").append(row);
  }

  async function load() {
    const previous = $("model").value;
    settings = await api("settings");
    const eligible = new Set([...$("eligible").selectedOptions].map(option => option.value));
    const option = b => new Option(b.model + (b.profile ? " · " + b.profile : ""), b.id);
    $("model").replaceChildren(...settings.bindings.map(option));
    $("eligible").replaceChildren(...settings.bindings.map(b => {
      const row = option(b);
      row.selected = eligible.has(b.id);
      return row;
    }));
    if (settings.bindings.some(b => b.id === previous)) $("model").value = previous;
    $("class").replaceChildren(...settings.classes.map(value => new Option(value, value)));
    const selectedPreset = $("preset").value;
    $("preset").replaceChildren(new Option("Choose automatically", ""),
      ...settings.workflow_presets.map(row => new Option(`${row.title} · ${row.model_calls + 2} minimum attempts`, row.id)));
    if (settings.workflow_presets.some(row => row.id === selectedPreset)) $("preset").value = selectedPreset;
    showClass();
    if (settings.key_source === "none") status("Set an OpenRouter key using the top-bar control.");
  }
  function showClass() {
    $("class").value = settings.bindings.find(b => b.id === $("model").value)?.class || "Unknown";
  }
  function reset() {
    intake = configuration = run = submission = null;
    sessionStorage.removeItem(reconnectKey);
    for (const id of ["turns", "report"]) $(id).replaceChildren();
    for (const id of ["message", "brief"]) $(id).value = "";
    $("trace").hidden = true;
    $("approval").hidden = true;
    status("");
    controls();
  }

  async function getIntake() {
    const body = {revision: settings.revision, binding: $("model").value, policy: $("policy").value,
      attempts: $("attempts").valueAsNumber, seconds: $("time").valueAsNumber,
      eligible_bindings: [...$("eligible").selectedOptions].map(option => option.value)};
    if (body.policy === "C" && $("preset").value) {
      const preset = settings.workflow_presets.find(row => row.id === $("preset").value);
      body.workflow_preset = {id: preset.id, revision: preset.revision};
    }
    const key = JSON.stringify(body);
    if (!intake || configuration !== key) {
      intake = (await api("intakes", body)).id;
      configuration = key;
      submission = null;
    }
    controls();
    return intake;
  }

  async function show(state) {
    run = state;
    sessionStorage.setItem(reconnectKey, state.id);
    const presetId = state.decisions?.at(-1)?.preset_id;
    const preset = settings.workflow_presets.find(row => row.id === presetId);
    const selection = `Policy ${state.policy} · ${state.pathway || "selecting pathway"}${preset ? " · " + preset.title : ""}`;
    status(`${selection} · ${(state.outcome || state.phase).replaceAll("_", " ")}${state.reason ? " · " + state.reason.replaceAll("_", " ") : ""}`);
    $("trace").hidden = false;
    const {pending_approval: approval, ...trace} = state;
    $("trace-text").textContent = JSON.stringify(trace, null, 2);
    $("approval").hidden = !approval;
    $("approval-scope").textContent = approval ? `${approval.capability} · ${approval.destination.url}\n${approval.arguments_text}` : "";
    if (state.outcome) {
      reply("Representative", state.question ||
        (state.report_available ? "The CEO report is ready." : "The run ended with the findings in the run trace."));
      if (state.report_available) {
        const response = await request(`runs/${state.id}/report`);
        $("report").replaceChildren(window.agentWizMarkdown.render(await response.text()));
      }
    }
    controls();
  }
  async function poll() {
    if (polling || !run || run.outcome) return;
    polling = true;
    try {
      await show(await api("runs/" + run.id));
    } catch (error) {
      report(error);
    } finally {
      polling = false;
      if (run && !run.outcome) setTimeout(poll, 400);
    }
  }

  $("open").onclick = async () => {
    dialog.showModal();
    controls();
    try {
      await load();
      const reconnect = sessionStorage.getItem(reconnectKey);
      if (!run && reconnect) {
        try { await show(await api("runs/" + reconnect)); }
        catch (error) {
          if (error.message !== "not_found") throw error;
          sessionStorage.removeItem(reconnectKey);
        }
        poll();
      }
      controls();
    } catch (error) { report(error); }
  };
  $("open").disabled = false;
  $("policy").onchange = controls;
  $("close").onclick = () => dialog.close();
  $("new").onclick = async () => {
    reset();
    try { await load(); } catch (error) { report(error); }
  };
  $("model").onchange = showClass;
  $("example").onclick = () => { $("brief").value = settings.example; };
  $("save-class").onclick = async () => {
    try {
      const binding = $("model").value;
      settings = await api("model-class", {revision: settings.revision, binding, class: $("class").value});
      intake = null;
      status("Intelligence class saved.");
    } catch (error) { report(error); }
  };
  $("send").onclick = async () => {
    if (busy()) return;
    const message = $("message").value;
    if (!message.trim()) return;
    pending = true;
    controls();
    try {
      const ident = await getIntake();
      const result = await api(`intakes/${ident}/message`, {text: message});
      reply("You", message);
      reply("Representative", result.reply);
      if (result.brief) $("brief").value = result.brief;
      $("message").value = "";
      $("trace").hidden = false;
      $("trace-text").textContent = JSON.stringify({intake_usage: result.usage}, null, 2);
      status("");
    } catch (error) { report(error); }
    finally { pending = false; controls(); }
  };
  $("submit").onclick = async () => {
    if (busy()) return;
    const brief = $("brief").value;
    if (!brief.trim()) return;
    pending = true;
    controls();
    try {
      if (run?.outcome) intake = null;
      const ident = await getIntake();
      if (submission?.brief !== brief) submission = {brief, id: crypto.randomUUID()};
      $("report").replaceChildren();
      await show(await api("runs", {intake_id: ident, brief, revision: settings.revision,
        client_request_id: submission.id}));
      await poll();
    } catch (error) { report(error); }
    finally { pending = false; controls(); }
  };
  $("stop").onclick = async () => {
    try {
      const path = run && !run.outcome ? `runs/${run.id}/stop` : `intakes/${intake}/stop`;
      await api(path, {});
      status("Stopping…");
      if (!run || run.outcome) intake = null;
    } catch (error) { report(error); }
  };
  async function decide(decision) {
    const approval = run?.pending_approval;
    if (!approval) return;
    try {
      await api(`runs/${run.id}/approval`, {call_id: approval.call_id,
        arguments_sha256: approval.arguments_sha256, decision});
      $("approval").hidden = true;
    } catch (error) { report(error); }
  }
  $("approve").onclick = () => decide("approve");
  $("deny").onclick = () => decide("deny");
  $("download").onclick = async () => {
    try {
      const response = await request(`runs/${run.id}/report`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `agentwiz-ceo-${run.id}.md.txt`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { report(error); }
  };
})();
