// AgentWiz contracts: the shared readable/source contract editor, the Workshop Contracts library, block-port
// contract editors and the pinned contract selector (contract overhaul specification, Sections 12, 13 and 15).
//
// One plain script, loaded by the page through the authenticated /assets/contracts.js route; no framework, no
// build step and no module loader. Dependencies are passed explicitly to create(). Every model or user string is
// placed with textContent or value, never parsed as markup. Exact definitions travel as strings: the page never
// parses contract source to numbers and stringifies it again; the server projects and edits it. Browser numbers
// are used only for revision counters and indices. Nothing is kept in localStorage.
(function () {
  "use strict";

  const TYPE_LABELS = [["Text", "Text field"], ["Whole number", "Whole number"], ["Number", "Number"],
    ["Yes/No", "Yes/No"], ["Choice", "Choice"], ["List", "List"], ["Group", "Group"]];
  // Fresh nodes hold no domain number, so building them from objects is exact.
  const NODES = {
    "Text": () => ({ type: "string" }), "Whole number": () => ({ type: "integer" }), "Number": () => ({ type: "number" }),
    "Yes/No": () => ({ type: "boolean" }), "Choice": () => ({ type: "string", enum: [""] }),
    "List": () => ({ type: "array", items: { type: "string" } }),
    "Group": () => ({ type: "object", properties: {}, required: [], additionalProperties: false }),
  };
  const BOUNDS = { Text: [["min_length", "minLength", "Minimum length"], ["max_length", "maxLength", "Maximum length"]],
    Choice: [["min_length", "minLength", "Minimum length"], ["max_length", "maxLength", "Maximum length"]],
    "Whole number": [["minimum", "minimum", "Minimum"], ["maximum", "maximum", "Maximum"]],
    Number: [["minimum", "minimum", "Minimum"], ["maximum", "maximum", "Maximum"]],
    List: [["min_items", "minItems", "Minimum items"], ["max_items", "maxItems", "Maximum items"]] };
  const FORMATS = [["text", "Text"], ["structured", "Structured JSON"], ["any", "Any JSON"]];
  const MESSAGES = {
    stale_contract: "This contract was changed elsewhere. Your draft is kept: refresh, review and save again.",
    contract_exists: "A contract with this name, or one differing only in letter case, already exists.",
    no_such_contract: "That contract no longer exists.",
    no_such_revision: "That revision does not exist.",
    contract_unusable: "The contract file has a problem and cannot be used until it is repaired.",
    digest_mismatch: "The saved revision no longer matches its recorded digest.",
    contract_in_use: "Block definitions still use this contract, so it was not deleted.",
    reference_scan_incomplete: "Some block definitions could not be inspected, so it is not established that this contract is unused. Nothing was deleted.",
    read_only: "The workspace is read-only.",
    busy: "Another contract or block publication is in progress. Try again when it has finished.",
    validation_unavailable: "Schema validation is unavailable. Repair the AgentWiz validator installation and restart.",
    contract_invalid: "The contract definition needs correction.",
    unsupported_keyword: "The contract uses a keyword outside the supported vocabulary.",
    too_large: "The contract is too large.",
    proposal_stale: "The draft changed after the proposal was requested. Request a new proposal or restore that draft.",
    proposal_expired: "That proposal is no longer available. Request a new one.",
    role_unresolved: "The write_contract role is not assigned to a model profile. Assign it in Roles first.",
    setup_unavailable: "The write_contract role or its model is unavailable. Check Roles and model settings.",
    invalid: "The request was refused as invalid.",
  };

  function create(deps) {
    const { api, h, copyControl, checkBox, dialog } = deps;
    const v2 = (route, body) => api("/api/contracts/" + route, { format_version: 2, ...body });
    const items = () => ((deps.state().contracts || {}).items) || [];
    const usable = () => items().filter((c) => !c.problem);
    const families = () => [...new Set(["General", ...(((deps.state().contracts || {}).families) || [])])];
    const readOnly = () => !!((deps.state().contracts || {}).read_only);

    function errorText(e) {
      const issues = (e && e.body && e.body.issues) || [];
      const more = ((e && e.body && e.body.issue_count) || issues.length) - issues.length;
      const base = (e && MESSAGES[e.code]) || (e && (e.body && e.body.message || e.message)) || String(e);
      const located = issues.map((i) => i.location + ": " + i.message).join("; ");
      return base + (located ? " " + located : "") + (more > 0 ? ` (and ${more} more)` : "") + (e && e.code ? " [" + e.code + "]" : "");
    }
    function issueList(test, e) {
      const list = h("ul", { class: "err-text", "data-test": test });
      for (const i of (e && e.body && e.body.issues) || []) list.append(h("li", { "data-location": i.location, text: i.location + ": " + i.message }));
      return list;
    }
    function setStatus(el, state_, text, code) {
      el.dataset.state = state_; el.textContent = text || "";
      if (code) el.dataset.code = code; else delete el.dataset.code;
    }
    function field(label, control) { return h("label", { class: "ws-field" }, label, control); }
    function nodeText(type, nullable, description) {
      const node = NODES[type]();
      if (nullable) { node.type = [node.type, "null"]; if (node.enum) node.enum.push(null); }
      if (description) node.description = description;
      return JSON.stringify(node);
    }
    function freshDefinition(kind, description) {
      const d = { format: kind === "text" ? "text" : "json" };
      if (description) d.description = description;
      if (kind === "structured") d.schema = { type: "object", properties: {}, required: [], additionalProperties: false };
      return JSON.stringify(d, null, 2);
    }
    const kindOf = (view) => !view ? null : view.format === "text" ? "text" : (view.fields && view.fields.length ? "structured" : "any");
    const pathKey = (path) => path.join("/");
    const short = (digest) => digest ? digest.slice(7, 19) : "";

    // --- the shared editor (spec 12, 15.2) ---------------------------------------------------------------------
    // editor({test, text, readOnly, onChange}) -> {el, text(), view(), valid(), ready(), set(text), setReadOnly(b)}
    // `text` is the last valid exact definition; edits are targeted server operations applied in order.
    function editor(opts) {
      const p = opts.test || "contract";
      const root = h("div", { class: "cw-editor", "data-test": p + "-editor", "data-state": "pending" });
      let text = null, view = null, epoch = 0, chain = Promise.resolve(), sourceInvalid = false, locked = !!opts.readOnly;
      let open = new Set([""]);                  // expanded rows by path key (the root starts open)
      const issues = h("div", { "data-test": p + "-issues" });
      const sourceArea = h("textarea", { class: "editor", spellcheck: "false", "data-test": p + "-source", "aria-label": "Contract source" });
      const sourceIssues = h("div", { "data-test": p + "-source-issues" });
      const guided = h("div", { "data-test": p + "-guided" });
      const sourceBox = h("details", { "data-test": p + "-source-section" }, h("summary", { "data-test": p + "-advanced", text: "Advanced source" }),
        sourceArea, h("div", { class: "row" },
          h("button", { type: "button", "data-test": p + "-apply-source", text: "Apply source", onclick: () => applySource() }),
          h("button", { type: "button", "data-test": p + "-discard-source", text: "Discard source edit", onclick: () => discardSource() }),
          copyControl(p + "-copy-source", () => text || "", () => sourceArea, "Copy source")),
        sourceIssues);
      root.append(guided, sourceBox, issues);

      function stateNow() {
        root.dataset.state = sourceInvalid ? "invalid" : view ? "valid" : "pending";
        root.dataset.format = kindOf(view) || "";
        if (view) root.dataset.digest = view.digest;
      }
      function fail(e) {
        issues.replaceChildren(h("p", { class: "err-text", "data-test": p + "-error", "data-code": (e && e.code) || "error", text: errorText(e) }), issueList(p + "-issue", e));
      }
      function accept(result, notify) {
        text = result.definition_text; view = result;
        if (!sourceInvalid) sourceArea.value = text;
        issues.replaceChildren();
        render(); stateNow();
        if (notify && opts.onChange) opts.onChange(text, view);
      }
      // Run `op` after earlier operations; a later set() discards edits queued against the replaced text.
      function queue(op) {
        const mine = epoch;
        root.dataset.state = "pending";
        chain = chain.then(async () => {
          if (mine !== epoch) return;
          try { await op(mine); } catch (e) { if (mine === epoch) { fail(e); render(); } }
          finally { if (mine === epoch) stateNow(); }
        });
        return chain;
      }
      function set(newText, notify = false) {
        epoch += 1; sourceInvalid = false; sourceIssues.replaceChildren();
        const mine = epoch;
        root.dataset.state = "pending";
        chain = chain.then(async () => {
          if (mine !== epoch) return;
          try { const r = await v2("project", { definition_text: newText }); if (mine === epoch) accept(r, notify); }
          catch (e) {
            if (mine !== epoch) return;
            // A definition that does not pass the recognizer stays as source only; nothing is projected from it.
            text = null; view = null; sourceInvalid = true; sourceArea.value = newText; sourceBox.open = true;
            sourceIssues.replaceChildren(h("p", { class: "err-text", text: errorText(e) }), issueList(p + "-source-issue", e));
            render();
          } finally { if (mine === epoch) stateNow(); }
        });
        return chain;
      }
      function edit(operation) {
        return queue(async (mine) => {
          const r = await v2("edit", { definition_text: text, edit: operation });
          if (mine === epoch) accept(r, true);
        });
      }
      function replace(newText) {             // a whole fresh definition chosen by the author (Change type)
        return queue(async (mine) => {
          const r = await v2("project", { definition_text: newText });
          if (mine === epoch) accept(r, true);
        });
      }
      // Replace the definition only if it is still `expected` when this queued step runs: an author's edit
      // queued earlier wins, and one queued later applies to the replacement. Resolves to whether it replaced.
      function replaceIf(expected, newText) {
        let replaced = false;
        return queue(async (mine) => {
          if (text !== expected || sourceInvalid) return;
          const r = await v2("project", { definition_text: newText });
          if (mine === epoch && text === expected) { accept(r, false); replaced = true; }
        }).then(() => replaced);
      }
      function applySource() {
        const candidate = sourceArea.value;
        return queue(async (mine) => {
          try {
            const r = await v2("project", { definition_text: candidate });
            if (mine !== epoch) return;
            sourceInvalid = false; sourceIssues.replaceChildren(); accept(r, true);
          } catch (e) {
            if (mine !== epoch) return;
            // Invalid source stays in the buffer with its issues; it cannot be saved or applied to a port.
            sourceInvalid = true;
            sourceIssues.replaceChildren(h("p", { class: "err-text", "data-test": p + "-source-error", "data-code": e.code || "error", text: errorText(e) }), issueList(p + "-source-issue", e));
            render();
          }
        });
      }
      function discardSource() {
        if (text === null) return;
        sourceInvalid = false; sourceArea.value = text; sourceIssues.replaceChildren(); render(); stateNow();
      }
      sourceArea.oninput = () => { if (sourceArea.value !== text) { root.dataset.source = "edited"; } else delete root.dataset.source; };

      async function confirmLoss(title, before, after) {
        const box = h("div", {}, h("p", { text: "This replaces the current definition. Constraints, choices or fields it holds are not carried over." }),
          h("h4", { text: "Current" }), h("pre", { "data-test": p + "-change-before", text: before }),
          h("h4", { text: "After the change" }), h("pre", { "data-test": p + "-change-after", text: after }));
        return await dialog(title, box, ["Change type", "Cancel"]) === "Change type";
      }

      function render() {
        guided.replaceChildren();
        guided.inert = locked || sourceInvalid;
        if (sourceInvalid) guided.append(h("p", { class: "warn-text", "data-test": p + "-guided-blocked", text: "The advanced source is invalid. Correct it and apply it, or discard the source edit, to return to the readable editor." }));
        if (!view) return;
        const kind = kindOf(view);
        const format = h("select", { "data-test": p + "-format", "aria-label": "Contract format" }, ...FORMATS.map(([v, t]) => h("option", { value: v, text: t })));
        format.value = kind;
        format.onchange = async () => {
          const next = format.value; format.value = kind;
          if (next === kind) return;
          const after = freshDefinition(next, view.description);
          const lossy = kind === "structured" || (kind === "text" && Object.keys(view.rules || {}).length > 0);
          if (lossy && !await confirmLoss("Change contract type", text, after)) return;
          await replace(after);
        };
        const description = h("textarea", { rows: 2, "data-test": p + "-description", "aria-label": "Contract description", value: view.description || "" });
        description.onchange = () => edit({ op: "set_description", path: null, description: description.value });
        guided.append(field("Format", format), field("Contract description", description));
        const warnings = h("ul", { class: "note", "data-test": p + "-warnings" });
        for (const w of view.warnings || []) warnings.append(h("li", { "data-code": w.code, text: w.message }));
        if (!view.description) warnings.append(h("li", { "data-code": "missing_description", text: "The contract has no description (optional)." }));
        const unnamed = (view.fields || []).filter((r) => !r.description).length;
        if (unnamed) warnings.append(h("li", { "data-code": "missing_field_description", text: `${unnamed} field${unnamed === 1 ? " has" : "s have"} no description (optional).` }));
        if (warnings.childElementCount) guided.append(warnings);
        if (kind === "text") textRules();
        else if (kind === "any") guided.append(h("p", { class: "warn-text", "data-test": p + "-any-json", "data-state": "weak", text: "Any JSON — no field validation. Any well-formed JSON value passes." }));
        else schemaTable();
      }

      function textRules() {
        const rules = view.rules || {};
        const box = h("fieldset", { "data-test": p + "-text-rules" }, h("legend", { text: "Text rules (blank means the rule does not run)" }));
        const min = h("input", { "data-test": p + "-min-length", inputMode: "numeric", value: rules.min_length || "", "aria-label": "Minimum length" });
        const max = h("input", { "data-test": p + "-max-length", inputMode: "numeric", value: rules.max_length || "", "aria-label": "Maximum length" });
        const exactOn = h("input", { type: "checkbox", "data-test": p + "-exact-on", checked: "exact" in rules });
        const exact = h("textarea", { rows: 2, "data-test": p + "-exact", value: rules.exact || "", "aria-label": "Exact text", hidden: !("exact" in rules) });
        box.append(h("div", { class: "fields" }, field("Minimum length (code points)", min), field("Maximum length (code points)", max)),
          h("label", { class: "checkbox-label" }, exactOn, "Require exact text (an empty box means exactly the empty string)"), exact);
        const lists = {};
        for (const [key, label] of [["contains", "Required text"], ["not_contains", "Forbidden text"]]) {
          const area = h("div", { "data-test": p + "-" + key.replace("_", "-") });
          lists[key] = [];
          const add = (value) => {
            const input = h("textarea", { rows: 1, "data-test": p + "-literal", "data-list": key, value, "aria-label": label });
            const line = h("div", { class: "row" }, input, h("button", { type: "button", "data-test": p + "-remove-literal", text: "Remove",
              onclick: () => { lists[key] = lists[key].filter((x) => x !== input); line.remove(); apply(); } }));
            lists[key].push(input); area.append(line); input.onchange = apply;
          };
          for (const value of rules[key] || []) add(value);
          box.append(h("label", { text: label + " (each must " + (key === "contains" ? "appear" : "not appear") + " literally)" }), area,
            h("button", { type: "button", "data-test": p + "-add-" + key.replace("_", "-"), text: "Add " + label.toLowerCase(),
              onclick: () => { add(""); lists[key].at(-1).focus(); } }));
        }
        function apply() {
          const next = {};
          if (min.value.trim()) next.min_length = min.value.trim();
          if (max.value.trim()) next.max_length = max.value.trim();
          if (exactOn.checked) next.exact = exact.value;
          for (const key of ["contains", "not_contains"]) {
            const values = lists[key].map((i) => i.value).filter((v) => v !== "");
            if (values.length || key in rules) next[key] = values;
          }
          return edit({ op: "set_text_rules", rules: next });
        }
        min.onchange = max.onchange = exact.onchange = apply;
        exactOn.onchange = () => { exact.hidden = !exactOn.checked; apply(); };
        guided.append(box);
      }

      function schemaTable() {
        const rows = view.fields || [];
        const table = h("table", { class: "cw-fields", "data-test": p + "-fields" },
          h("thead", {}, h("tr", {}, ...["Name", "Type", "Description", "Required", "Allow null", ""].map((t) => h("th", { scope: "col", text: t })))));
        const body = h("tbody");
        table.append(body);
        for (const row of rows) {
          const key = pathKey(row.path), depth = row.path.length, last = row.path[row.path.length - 1];
          const isRoot = depth === 0, isItem = last === "[]";
          const tr = h("tr", { "data-test": p + "-row", "data-path": key, "data-type": row.type });
          const name = h("input", { "data-test": p + "-row-name", "aria-label": "Field name", value: isRoot ? "(value)" : isItem ? "(each item)" : last, disabled: isRoot || isItem });
          name.style.marginLeft = (depth * 1.1) + "rem";
          name.onchange = () => edit({ op: "rename_property", path: row.path, name: name.value });
          const type = h("select", { "data-test": p + "-row-type", "aria-label": "Field type" }, ...TYPE_LABELS.map(([v, t]) => h("option", { value: v, text: t })));
          type.value = row.type;
          type.onchange = async () => {
            const next = type.value; type.value = row.type;
            const after = nodeText(next, row.nullable, row.description);
            const children = rows.some((r) => r.path.length > depth && pathKey(r.path.slice(0, depth)) === key && r !== row);
            const lossy = children || row.choices || BOUNDS[row.type]?.some(([k]) => k in row);
            if (lossy && !await confirmLoss("Change field type", row.source_text, after)) return;
            open.add(key);
            await edit({ op: "set_node", path: row.path, node_text: after });
          };
          const desc = h("input", { "data-test": p + "-row-description", "aria-label": "Field description", value: row.description || "" });
          desc.onchange = () => edit({ op: "set_description", path: row.path, description: desc.value });
          const required = h("input", { type: "checkbox", "data-test": p + "-row-required", "aria-label": "Required", checked: row.required, disabled: isRoot || isItem });
          required.onchange = () => edit({ op: "set_required", path: row.path, value: required.checked });
          const nullable = h("input", { type: "checkbox", "data-test": p + "-row-nullable", "aria-label": "Allow null", checked: row.nullable });
          nullable.onchange = () => edit({ op: "set_nullable", path: row.path, value: nullable.checked });
          const more = h("button", { type: "button", "data-test": p + "-row-more", "aria-expanded": String(open.has(key)), text: open.has(key) ? "Less" : "More" });
          more.onclick = () => { if (open.has(key)) open.delete(key); else open.add(key); render(); };
          tr.append(h("td", {}, name), h("td", {}, type), h("td", {}, desc), h("td", {}, required), h("td", {}, nullable), h("td", {}, more));
          body.append(tr);
          if (open.has(key)) body.append(h("tr", { "data-test": p + "-row-detail", "data-path": key }, h("td", { colspan: "6" }, detail(row, isRoot, isItem))));
        }
        const rootType = h("p", { class: "note", text: "Change the value's top-level type with the first row's Type control; a type change previews what it replaces." });
        guided.append(rootType, h("div", { class: "ws-table" }, table));
      }

      function detail(row, isRoot, isItem) {
        const box = h("div", { class: "cw-detail" });
        for (const [k, keyword, label] of BOUNDS[row.type] || []) {
          const input = h("input", { "data-test": p + "-bound", "data-keyword": keyword, "aria-label": label, value: row[k] || "", inputMode: "decimal" });
          input.onchange = () => edit({ op: "set_bound", path: row.path, keyword, value_text: input.value.trim() ? input.value.trim() : null });
          box.append(field(label, input));
        }
        if (row.type === "Choice") {
          const list = h("div", { "data-test": p + "-choices" }), inputs = [];
          const apply = () => edit({ op: "set_choices", path: row.path, choices: inputs.map((i) => i.value) });
          (row.choices || []).forEach((value, i) => {
            const input = h("textarea", { rows: 1, "data-test": p + "-choice", "aria-label": "Choice " + (i + 1), value });
            input.onchange = apply; inputs.push(input);
            list.append(h("div", { class: "row" }, input, h("button", { type: "button", "data-test": p + "-remove-choice", text: "Remove",
              onclick: () => { inputs.splice(inputs.indexOf(input), 1); apply(); } })));
          });
          const fresh = h("input", { "data-test": p + "-new-choice", "aria-label": "New choice" });
          box.append(h("label", { text: "Choices (null is added automatically when Allow null is on)" }), list,
            h("div", { class: "row" }, fresh, h("button", { type: "button", "data-test": p + "-add-choice", text: "Add choice",
              onclick: () => edit({ op: "set_choices", path: row.path, choices: [...inputs.map((i) => i.value), fresh.value] }) })));
        }
        if (row.type === "Group") {
          const extra = h("select", { "data-test": p + "-extra", "aria-label": "Unknown properties" },
            h("option", { value: "false", text: "Reject properties not listed" }), h("option", { value: "true", text: "Allow properties not listed" }));
          extra.value = String(row.additional_properties);
          extra.onchange = () => edit({ op: "set_additional_properties", path: row.path, value: extra.value === "true" });
          const name = h("input", { "data-test": p + "-new-name", "aria-label": "New property name", placeholder: "property_name" });
          const type = h("select", { "data-test": p + "-new-type", "aria-label": "New property type" }, ...TYPE_LABELS.map(([v, t]) => h("option", { value: v, text: t })));
          box.append(field("Unknown properties", extra), h("div", { class: "row" }, name, type,
            h("button", { type: "button", "data-test": p + "-add-property", text: "Add property", onclick: () => {
              open.add(pathKey(row.path));
              return edit({ op: "add_property", path: row.path, name: name.value, node_text: nodeText(type.value, false, ""), required: true });
            } })));
          box.append(h("p", { class: "note", text: "New properties are required and do not allow null; change either afterwards." }));
        }
        if (!isRoot && !isItem) box.append(h("button", { type: "button", "data-test": p + "-remove-property", text: "Remove property",
          onclick: () => edit({ op: "remove_property", path: row.path }) }));
        if (!box.childElementCount) box.append(h("p", { class: "note", text: "No further rules apply to this type." }));
        return box;
      }

      if (opts.text !== undefined && opts.text !== null) set(opts.text);
      return {
        el: root, text: () => (sourceInvalid ? null : text), view: () => view,
        valid: () => !sourceInvalid && !!view, invalid: () => sourceInvalid,
        ready: () => chain, set, replaceIf,
        setReadOnly(value) { locked = !!value; render(); },
      };
    }

    // --- readable differences for the save review (spec 15.2) ---------------------------------------------------
    function changes(before, after) {
      if (!before) return ["New contract: revision 1."];
      if (before.digest === after.digest) return ["No change from the latest revision; saving writes nothing."];
      const out = [];
      if (kindOf(before) !== kindOf(after)) out.push(`Format: ${label(kindOf(before))} → ${label(kindOf(after))}`);
      if ((before.description || "") !== (after.description || "")) out.push("Contract description changed.");
      if (kindOf(after) === "text") {
        for (const key of ["min_length", "max_length", "exact", "contains", "not_contains"]) {
          const a = JSON.stringify((before.rules || {})[key]), b = JSON.stringify((after.rules || {})[key]);
          if (a !== b) out.push(`Text rule ${key.replace("_", " ")}: ${a === undefined ? "added" : b === undefined ? "removed" : "changed"}.`);
        }
      }
      const was = new Map((before.fields || []).map((r) => [pathKey(r.path), r]));
      const now = new Map((after.fields || []).map((r) => [pathKey(r.path), r]));
      for (const [k, r] of now) {
        const shown = k || "(value)";
        if (!was.has(k)) out.push(`Added ${shown} (${r.type}${r.required ? ", required" : ""}${r.nullable ? ", allows null" : ""}).`);
        else {
          const o = was.get(k), parts = [];
          if (o.type !== r.type) parts.push(`type ${o.type} → ${r.type}`);
          if (o.required !== r.required) parts.push(r.required ? "now required" : "now optional");
          if (o.nullable !== r.nullable) parts.push(r.nullable ? "now allows null" : "no longer allows null");
          if ((o.description || "") !== (r.description || "")) parts.push("description");
          if (JSON.stringify(o.choices) !== JSON.stringify(r.choices)) parts.push("choices");
          for (const b of ["min_length", "max_length", "minimum", "maximum", "min_items", "max_items"]) if (o[b] !== r[b]) parts.push(b.replace("_", " "));
          if (o.additional_properties !== r.additional_properties) parts.push("unknown-property policy");
          if (parts.length) out.push(`Changed ${shown}: ${parts.join(", ")}.`);
        }
      }
      for (const k of was.keys()) if (!now.has(k)) out.push(`Removed ${k || "(value)"}.`);
      if (!out.length) out.push("Definition changed (ordering or source details).");
      return out;
    }
    const label = (kind) => (FORMATS.find(([v]) => v === kind) || [kind, kind])[1];

    // --- pinned selector: Checked request and saved block ports (spec 13.1, 15.3) -------------------------------
    // picker({test, latestOption, pin, onChange}) -> {el, pin(), select(name), refresh(), detail()}
    function picker(opts) {
      const p = opts.test;
      const contract = h("select", { "data-test": p + "-contract", "aria-label": "Saved contract" });
      const revision = h("select", { "data-test": p + "-revision", "aria-label": "Contract revision" });
      const shown = h("div", { class: "note", "data-test": p + "-pin" });
      const newer = h("span", { class: "warn-text", "data-test": p + "-newer", hidden: true, text: "Newer revision available" });
      const el = h("div", { class: "cw-picker" }, field("Contract", contract), field("Revision", revision), shown, newer);
      let current = opts.pin || null, info = null, generation = 0;
      function options() {
        const keep = contract.value || (current && current.name) || "";
        contract.replaceChildren(h("option", { value: "", text: usable().length ? "Choose a saved contract" : "(no usable contracts)" }));
        for (const c of usable()) contract.add(new Option(`${c.name} · ${c.family} · r${c.latest} (${c.kind === "any_json" ? "any JSON" : c.kind})`, c.name));
        if (current && !usable().some((c) => c.name === current.name)) contract.add(new Option(`${current.name} (unavailable)`, current.name));
        contract.value = keep;
      }
      function show() {
        el.dataset.state = current ? "pinned" : "none";
        const pinned = current;
        shown.replaceChildren();
        for (const key of ["name", "revision", "digest"]) { if (pinned) shown.dataset[key] = String(pinned[key]); else delete shown.dataset[key]; }
        if (!pinned) { shown.textContent = "No contract selected."; newer.hidden = true; return; }
        const summary = info && info.revisions ? info.revisions.find((r) => r.revision === pinned.revision) : null;
        shown.append(h("span", { text: `${pinned.name}${info && info.family ? " · " + info.family : ""} · revision ${pinned.revision} · ${short(pinned.digest)}` }));
        if (summary && summary.description) shown.append(h("div", { text: summary.description }));
        if (info && info.missing) shown.append(h("div", { class: "err-text", "data-test": p + "-pin-problem", text: info.missing }));
        const latest = info && info.latest;
        newer.hidden = !(latest && pinned.revision < latest && revision.value !== "latest");
        newer.dataset.latest = latest ? String(latest) : "";
      }
      async function load(name, keepRevision) {
        const mine = ++generation;
        if (!name) { info = null; current = null; revision.replaceChildren(); show(); if (opts.onChange) opts.onChange(null); return; }
        let detail;
        try { detail = await api("/api/contracts/" + encodeURIComponent(name)); }
        catch (e) { if (mine !== generation) return; info = { missing: errorText(e) }; revision.replaceChildren(); show(); return; }
        if (mine !== generation) return;
        info = detail;
        if (detail.problem) { info.missing = detail.problem.message; }
        const before = revision.value;
        revision.replaceChildren();
        if (opts.latestOption && detail.latest) revision.add(new Option(`Latest (revision ${detail.latest})`, "latest"));
        for (const r of [...(detail.revisions || [])].reverse())
          revision.add(new Option(`Revision ${r.revision}${r.label ? " — " + r.label : ""}${r.revision === detail.latest ? " (latest)" : ""}`, String(r.revision)));
        if (keepRevision && current && current.name === name && current.revision !== null) {
          const asNumber = String(current.revision);
          revision.value = opts.latestOption && before === "latest" ? "latest" : asNumber;
          if (!revision.value) revision.add(new Option(`Revision ${asNumber} (unavailable)`, asNumber)), revision.value = asNumber;
        } else revision.value = opts.latestOption ? "latest" : String(detail.latest || "");
        choose(false);
      }
      function choose(notify = true) {
        const name = contract.value;
        if (!info || !name || info.problem) { current = name && current && current.name === name ? current : null; show(); if (notify && opts.onChange) opts.onChange(current); return; }
        const wanted = revision.value === "latest" ? info.latest : parseInt(revision.value, 10);
        const summary = (info.revisions || []).find((r) => r.revision === wanted);
        const before = current;
        if (summary) current = { name, revision: summary.revision, digest: summary.digest };
        else if (!(current && current.name === name)) current = null;          // an unavailable pin stays as recorded
        show();
        const changed = JSON.stringify(before) !== JSON.stringify(current);
        if (opts.onChange && (notify || changed)) opts.onChange(current);
      }
      contract.onchange = () => load(contract.value, false);
      revision.onchange = () => choose(true);
      options();
      if (current) { contract.value = current.name; load(current.name, true); } else show();
      return {
        el,
        pin: () => current,
        info: () => info,
        async select(name, number) {
          options(); contract.value = name;
          current = number ? { name, revision: number, digest: null } : null;
          await load(name, false);
          if (number) { revision.value = String(number); choose(true); }
        },
        refresh() { options(); if (contract.value) return load(contract.value, true); show(); return Promise.resolve(); },
        selectNewer() { if (info && info.latest) { revision.value = String(info.latest); choose(true); } },
        disable(value) { contract.disabled = revision.disabled = !!value; },
      };
    }

    // --- block port: Inline | Saved contract (spec 15.3) -------------------------------------------------------
    // port({side, name, format, source, captured, onChange(format, source), onOpen(name)}) -> {el, valid(), ready()}
    function port(opts) {
      const p = "port";
      const el = h("div", { class: "cw-port", "data-test": "port-card", "data-side": opts.side, "data-port": opts.name });
      const mode = h("select", { "data-test": p + "-mode", "aria-label": "Contract source" },
        h("option", { value: "inline", text: "Inline" }), h("option", { value: "saved", text: "Saved contract" }));
      const body = h("div"), msg = h("p", { class: "note", role: "status", "data-test": p + "-msg" });
      el.append(field("Contract", mode), body, msg);
      let inline = null, saved = null, pending = Promise.resolve();
      const isSaved = opts.format === "shared";
      mode.value = isSaved ? "saved" : "inline";
      el.dataset.mode = mode.value;

      function showInline(text) {
        body.replaceChildren();
        inline = editor({ test: p, text, onChange: (t, view) => opts.onChange(view.format, t) });
        body.append(inline.el, h("div", { class: "row" },
          h("button", { type: "button", "data-test": p + "-save-reusable", text: "Save inline as reusable", disabled: readOnly(), onclick: () => saveReusable() })));
        pending = inline.ready();
      }
      function showSaved(pin) {
        body.replaceChildren(); inline = null;
        saved = picker({ test: p, pin, onChange: (next) => { if (next && next.digest) { opts.onChange("shared", JSON.stringify(next)); refreshButtons(); } } });
        const openBtn = h("button", { type: "button", "data-test": p + "-open", text: "Open contract", onclick: () => { const c = saved.pin(); if (c) opts.onOpen(c.name, c.revision); } });
        const newer = h("button", { type: "button", "data-test": p + "-select-newer", text: "Select newer", onclick: () => saved.selectNewer() });
        const local = h("button", { type: "button", "data-test": p + "-local-copy", text: "Make local copy", onclick: () => localCopy() });
        body.append(saved.el, h("div", { class: "row" }, openBtn, newer, local));
        function refreshButtons() { const c = saved.pin(); openBtn.disabled = local.disabled = !c; }
        refreshButtons();
      }
      async function localCopy() {
        const pin = saved && saved.pin();
        if (!pin) return;
        msg.textContent = "";
        let text = null;
        try {
          const detail = await api("/api/contracts/" + encodeURIComponent(pin.name) + "?revision=" + encodeURIComponent(String(pin.revision)));
          if (detail.digest === pin.digest) text = detail.definition_text;
        } catch (e) { /* fall back to the captured definition */ }
        if (text === null && typeof opts.captured === "string") {
          const projected = await v2("project", { definition_text: opts.captured }).catch(() => null);
          if (projected && projected.digest === pin.digest) text = opts.captured;
        }
        if (text === null) { msg.textContent = "The pinned revision and its captured copy are both unavailable, so no local copy can be made."; msg.dataset.state = "error"; return; }
        mode.value = "inline"; el.dataset.mode = "inline";
        showInline(text);
        await inline.ready();
        if (inline.valid()) opts.onChange(inline.view().format, inline.text());
        msg.dataset.state = "local_copy"; msg.textContent = `Replaced the reference with a local copy of ${pin.name} revision ${pin.revision}. Save the block to keep it.`;
      }
      async function saveReusable() {
        if (!inline || !inline.valid()) { msg.dataset.state = "error"; msg.textContent = "Correct the contract before saving it as reusable."; return; }
        const box = h("div"), name = h("input", { "data-test": "port-reusable-name", "aria-label": "Contract name" });
        const family = h("select", { "data-test": "port-reusable-family", "aria-label": "Family" }, ...families().map((f) => new Option(f, f)));
        const label_ = h("input", { "data-test": "port-reusable-label", "aria-label": "Revision label" });
        const error = h("p", { role: "alert", "data-test": "port-reusable-error" });
        box.append(h("p", { text: "Creates a new saved contract with this exact definition. The port stays inline unless you choose to use the saved revision." }),
          field("Contract name", name), field("Family", family), field("Revision label (optional)", label_), error);
        let result = null;
        const choice = await dialog("Save inline as reusable", box, ["Save contract", "Cancel"], async (c) => {
          if (c !== "Save contract") return true;
          try {
            result = await v2("save", { name: name.value, family: family.value, expected_file_token: null, definition_text: inline.text(), label: label_.value });
            return true;
          } catch (e) { error.dataset.code = e.code || "error"; error.textContent = errorText(e); return false; }
        });
        if (choice !== "Save contract" || !result) return;
        if (deps.changed) await deps.changed();
        const use = await dialog("Use saved revision?", h("p", { text: `Saved ${result.name} revision ${result.revision}. Use that saved revision for this port? Choosing Keep inline leaves the port unchanged.` }),
          ["Use saved revision", "Keep inline"]);
        if (use !== "Use saved revision") { msg.dataset.state = "saved_reusable"; msg.textContent = `Saved ${result.name}; the port stays inline.`; return; }
        mode.value = "saved"; el.dataset.mode = "saved";
        const pin = { name: result.name, revision: result.revision, digest: result.digest };
        showSaved(pin);
        opts.onChange("shared", JSON.stringify(pin));
        msg.dataset.state = "attached"; msg.textContent = "The port now uses the saved revision. Save the block to keep this change.";
      }
      mode.onchange = async () => {
        const next = mode.value; mode.value = el.dataset.mode;
        if (next === el.dataset.mode) return;
        if (next === "inline") { await localCopy(); return; }
        // Saved: the port keeps its inline definition until a revision is actually chosen.
        mode.value = "saved"; el.dataset.mode = "saved";
        showSaved(null);
        msg.textContent = "Choose a saved contract. Until one is chosen the port keeps its inline definition.";
      };
      if (isSaved) {
        let pin = null;
        try { pin = JSON.parse(opts.source); } catch (e) { pin = null; }      // name/revision/digest: no domain numbers
        showSaved(pin && typeof pin === "object" ? { name: pin.name, revision: pin.revision, digest: pin.digest } : null);
      } else showInline(opts.source || JSON.stringify({ format: opts.format === "json" ? "json" : "text" }));
      return {
        el,
        valid: () => mode.value === "saved" ? !!(saved && saved.pin() && saved.pin().digest) : !!(inline && inline.valid()),
        invalid: () => mode.value === "inline" && !!inline && inline.invalid(),
        ready: () => (inline ? inline.ready() : pending),
        refresh: () => (saved && mode.value === "saved" ? saved.refresh() : Promise.resolve()),
      };
    }

    // --- the Workshop Contracts library (spec 15.1, 15.2) ------------------------------------------------------
    function library(panel) {
      const search = h("input", { "data-test": "contracts-search", placeholder: "Search contracts", "aria-label": "Search contracts" });
      const familyFilter = h("select", { "data-test": "contracts-family-filter", "aria-label": "Family filter" });
      const newBtn = h("button", { type: "button", "data-test": "contracts-new", text: "New contract" });
      const list = h("ul", { class: "list", "data-test": "contracts-list" });
      const validation = h("p", { class: "note", "data-test": "contracts-validation" });
      const left = h("aside", { class: "cw-library-list" }, search, field("Family", familyFilter), newBtn, list, validation);

      const name = h("input", { "data-test": "contracts-name", "aria-label": "Contract name", placeholder: "contract_name" });
      const family = h("select", { "data-test": "contracts-family", "aria-label": "Family" });
      const addFamily = h("button", { type: "button", "data-test": "contracts-add-family", text: "Add family" });
      const saveFamily = h("button", { type: "button", "data-test": "contracts-save-family", text: "Save grouping" });
      const revision = h("select", { "data-test": "contracts-revision", "aria-label": "Revision" });
      const basisNote = h("p", { class: "note", "data-test": "contracts-basis-note" });
      const restore = h("button", { type: "button", "data-test": "contracts-restore", text: "Restore as new revision" });
      const problem = h("div", { class: "err-text", "data-test": "contracts-problem" });
      const editorHost = h("div");
      const label_ = h("input", { "data-test": "contracts-label", "aria-label": "Revision label", placeholder: "optional" });
      const save = h("button", { class: "primary", type: "button", "data-test": "contracts-save", text: "Save" });
      const duplicate = h("button", { type: "button", "data-test": "contracts-duplicate", text: "Duplicate" });
      const del = h("button", { type: "button", "data-test": "contracts-delete", text: "Delete" });
      const refresh = h("button", { type: "button", "data-test": "contracts-refresh", text: "Refresh from disk" });
      const status = h("p", { role: "status", "data-test": "contracts-status", "data-state": "idle" });
      const statusDetail = h("div", { "data-test": "contracts-status-detail" });
      const checkText = h("textarea", { rows: 4, "data-test": "contracts-check-text", "aria-label": "Sample value", placeholder: "Paste a sample value: literal text for a Text contract, a JSON answer for a JSON contract" });
      const runCheck = h("button", { type: "button", "data-test": "contracts-run-check", text: "Check sample" });
      const result = checkBox("contracts");
      const usedBy = h("div", { "data-test": "contracts-used-by" });
      const synth = h("fieldset", { "data-test": "contracts-synthesis" });
      const right = h("section", { class: "cw-library-editor" },
        h("div", { class: "row" }, field("Name", name), field("Family", family), addFamily, saveFamily),
        h("div", { class: "row" }, field("Revision", revision), restore), basisNote, problem, editorHost,
        h("fieldset", {}, h("legend", { text: "Check sample" }),
          h("p", { class: "note", text: "Contract check: structure only. A pass is not a correctness, completeness or privacy check, and nothing is marked verified." }),
          checkText, h("div", { class: "row" }, runCheck), result.el),
        synth,
        h("fieldset", {}, h("legend", { text: "Used by" }), usedBy),
        h("div", { class: "row cw-savebar" }, field("Revision label", label_), save, duplicate, del, refresh), status, statusDetail);
      panel.append(h("p", { class: "panel-intro", text: "Contracts: reusable Text and JSON value requirements, with append-only revisions. Blocks pin an exact revision." }),
        h("div", { class: "cw-library" }, left, right));

      // The open item: {name, family, token, latest, revisions, selected, problem} for a saved contract, null for a new draft.
      let item = null, draftFamily = "General", ed = null, dirty = false, baseView = null, baseText = null, proposal = null, loadGen = 0;

      function setDirty(value) {
        dirty = value; panel.dataset.dirty = value ? "true" : "false";
        if (value && ["saved", "unchanged", "idle"].includes(status.dataset.state)) setStatus(status, "dirty", "Unsaved changes.");
      }
      function renderFamilies() {
        const keep = familyFilter.value;
        familyFilter.replaceChildren(new Option("All families", ""), ...families().map((f) => new Option(f, f)));
        familyFilter.value = families().includes(keep) ? keep : "";
        const chosen = draftFamily;
        family.replaceChildren(...[...new Set([...families(), chosen])].map((f) => new Option(f, f)));
        family.value = chosen;
      }
      function renderList() {
        const q = search.value.trim().toLowerCase(), f = familyFilter.value;
        list.replaceChildren();
        const shown = items().filter((c) => (!f || c.family === f) &&
          (!q || c.name.toLowerCase().includes(q) || (c.description || "").toLowerCase().includes(q)));
        if (!shown.length) list.append(h("li", { class: "muted", text: items().length ? "No contracts match." : "No saved contracts yet." }));
        for (const c of shown) {
          const li = h("li", { class: "pick" + (item && item.name === c.name ? " current" : ""), "data-test": "contracts-row",
            "data-name": c.name, "data-family": c.family || "", "data-kind": c.kind || "", "data-problem": c.problem ? c.problem.code : "" });
          li.append(h("strong", { text: c.name }), " ",
            h("span", { class: "muted", text: c.problem ? `unusable: ${c.problem.message}` : `${c.family} · ${c.kind === "any_json" ? "any JSON" : c.kind} · r${c.latest}` }));
          li.onclick = () => guarded(() => openItem(c.name));
          list.append(li);
        }
        const v = (deps.state().contracts || {}).validation;
        validation.textContent = v && !v.available ? "Schema validation is unavailable: repair the AgentWiz validator installation. Text and Any JSON contracts still work." : "";
        validation.dataset.state = v && !v.available ? "unavailable" : "available";
      }
      function controls() {
        const ro = readOnly(), saved = !!item, broken = !!(item && item.problem);
        name.readOnly = saved; name.dataset.state = saved ? "fixed" : "new";
        save.disabled = ro || broken; restore.disabled = ro || broken;
        del.disabled = ro || !saved; duplicate.disabled = broken || !ed;
        saveFamily.hidden = !saved; saveFamily.disabled = ro || broken || !item || family.value === item.family;
        addFamily.disabled = broken;
        restore.hidden = !(saved && !broken && item.selected && item.selected !== item.latest);
        revision.disabled = !saved || broken;
        if (ed) ed.setReadOnly(broken);
        synthesisControls();
      }
      function newEditor(text) {
        editorHost.replaceChildren();
        ed = editor({ test: "contracts", text, onChange: () => { setDirty(true); synthesisControls(); } });
        editorHost.append(ed.el);
        return ed.ready();
      }

      async function openItem(contractName, number) {
        const mine = ++loadGen;
        let detail;
        try { detail = await api("/api/contracts/" + encodeURIComponent(contractName) + (number ? "?revision=" + number : "")); }
        catch (e) { if (mine === loadGen) setStatus(status, "error", errorText(e), e.code); return; }
        if (mine !== loadGen) return;
        item = { name: detail.name, family: detail.family, token: detail.file_token, latest: detail.latest,
          revisions: detail.revisions || [], selected: detail.revision, problem: detail.problem, digest: detail.digest };
        name.value = detail.name; draftFamily = detail.family || "General"; label_.value = ""; proposal = null; clearCandidate();
        renderFamilies(); renderRevisions(); usedByView(detail.used_by, detail.latest);
        problem.replaceChildren();
        result.clear();
        if (detail.problem) {
          editorHost.replaceChildren(); ed = null; baseView = null;
          problem.append(h("p", { text: `${detail.problem.file}: ${detail.problem.message} [${detail.problem.code}]` }),
            h("p", { text: "This file cannot be edited or overwritten here. Repair or restore it with an editor, or delete it (deletion still checks which blocks use it)." }));
        } else {
          await newEditor(detail.definition_text);
          baseView = ed.view(); baseText = detail.definition_text;
        }
        basisNote.textContent = item.selected && item.selected !== item.latest
          ? `Viewing revision ${item.selected}. Saving appends revision ${item.latest + 1} based on this view; the latest stays revision ${item.latest} until then.` : "";
        setDirty(false); setStatus(status, "idle", ""); statusDetail.replaceChildren();
        renderList(); controls();
      }
      function renderRevisions() {
        revision.replaceChildren();
        if (!item || item.problem) { revision.add(new Option("Unsaved draft", "")); return; }
        for (const r of [...item.revisions].reverse())
          revision.add(new Option(`Revision ${r.revision}${r.label ? " — " + r.label : ""} · ${r.created_at} · ${r.origin}${r.revision === item.latest ? " (latest)" : ""}`, String(r.revision)));
        revision.value = String(item.selected);
      }
      function usedByView(used, latest) {
        usedBy.replaceChildren();
        if (!used) { usedBy.append(h("p", { class: "muted", text: "Save the contract to see which blocks use it." })); return; }
        usedBy.dataset.complete = String(!!used.complete);
        if (!used.complete) usedBy.append(h("div", { class: "warn-text", "data-test": "contracts-used-incomplete" },
          h("p", { text: "Some block definitions could not be inspected, so this list may be incomplete:" }), h("ul", {}, ...used.unreadable.map((u) => h("li", { text: u })))));
        if (!used.items.length) { usedBy.append(h("p", { class: "muted", "data-test": "contracts-used-none", text: used.complete ? "No block definition uses this contract." : "No use was found among the definitions that could be inspected." })); return; }
        const rows = [...used.items].sort((a, b) => [a.block, a.side, a.port].join("\u0000").localeCompare([b.block, b.side, b.port].join("\u0000")) || a.revision - b.revision);
        const ul = h("ul", { class: "list" });
        for (const u of rows) ul.append(h("li", { "data-test": "contracts-used-row", "data-block": u.block, "data-current": String(u.current), "data-side": u.side,
          "data-port": u.port, "data-revision": String(u.revision), "data-newer": String(!!u.newer_available) },
          `${u.block} · ${u.current ? "current" : "retained"} generation · ${u.side === "inputs" ? "input" : "output"} ${u.port} · revision ${u.revision}`,
          u.newer_available ? h("span", { class: "warn-text", text: " · Newer revision available" }) : null));
        usedBy.append(ul, h("p", { class: "note", text: "Upgrading a block is an explicit block edit: select the newer revision on its port, preview and save a block revision." }));
      }

      async function newDraft(text, familyName) {
        item = null; name.value = ""; draftFamily = familyName || "General"; label_.value = ""; proposal = null; clearCandidate();
        revision.replaceChildren(new Option("Unsaved draft", "")); basisNote.textContent = ""; problem.replaceChildren(); result.clear();
        usedByView(null); baseView = null;
        renderFamilies();
        await newEditor(text || freshDefinition("text", ""));
        setDirty(!!text); setStatus(status, text ? "dirty" : "idle", text ? "Unsaved draft." : "New contract draft. Name it, define it and Save."); statusDetail.replaceChildren();
        renderList(); controls();
      }
      async function leave() {
        if (!dirty) return true;
        const choice = await dialog("Unsaved contract changes", h("p", { text: "Save this contract draft before leaving it?" }), ["Save", "Discard", "Stay"]);
        if (choice === "Save") return await doSave();
        if (choice === "Discard") { setDirty(false); return true; }
        return false;
      }
      async function guarded(fn) { if (await leave()) await fn(); }

      async function doSave() {
        if (!ed) return false;
        await ed.ready();
        statusDetail.replaceChildren();
        if (!ed.valid()) { setStatus(status, "error", "Correct or discard the invalid advanced source before saving.", "contract_invalid"); return false; }
        const contractName = item ? item.name : name.value;
        if (!item && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(contractName)) {
          setStatus(status, "error", "A contract name is 1 to 64 letters, digits, '.', '_' or '-', starting with a letter or digit.", "invalid_name"); name.focus(); return false;
        }
        let latestView = null;
        if (item) {
          try {
            const latest = item.selected === item.latest && baseView ? null : await api("/api/contracts/" + encodeURIComponent(item.name));
            latestView = latest ? await v2("project", { definition_text: latest.definition_text }) : baseView;
          } catch (e) { latestView = null; }
        }
        const view = ed.view();
        const review = h("div", { "data-test": "contracts-review" },
          h("dl", { class: "ws-facts" }, h("dt", { text: "Name" }), h("dd", { text: contractName }),
            h("dt", { text: "Revision" }), h("dd", { "data-test": "contracts-review-revision", text: item ? `${item.latest + 1} (after revision ${item.latest})` : "1 (new contract)" }),
            h("dt", { text: "Format" }), h("dd", { text: label(kindOf(view)) }),
            h("dt", { text: "Family" }), h("dd", { text: item ? item.family : family.value })),
          h("h4", { text: "Changes from the latest revision" }), h("ul", {}, ...changes(latestView, view).map((c) => h("li", { text: c }))),
          proposal ? h("p", { class: "note", text: "This draft came from a synthesis proposal; the revision records that origin." }) : null,
          item && family.value !== item.family ? h("p", { class: "warn-text", text: "The family change is not part of this save. Use Save grouping for it." }) : null);
        if (await dialog("Review contract save", review, ["Confirm", "Cancel"]) !== "Confirm") return false;
        try {
          const body = { name: contractName, family: item ? item.family : family.value, expected_file_token: item ? item.token : null,
            definition_text: ed.text(), label: label_.value };
          if (proposal) body.proposal_id = proposal;
          const saved = await v2("save", body);
          setDirty(false);
          if (deps.changed) await deps.changed();
          await openItem(saved.name);
          if (saved.unchanged) setStatus(status, "unchanged", `Unchanged: the draft equals revision ${saved.revision}; nothing was written.`);
          else if (saved.publication === "durability_uncertain") setStatus(status, "uncertain", `Saved as revision ${saved.revision}, but the system could not confirm it is durable. Do not save again; refresh to confirm.`);
          else setStatus(status, "saved", `Saved revision ${saved.revision}.`);
          status.dataset.revision = String(saved.revision);
          return true;
        } catch (e) {
          // A failed or stale save keeps the draft; it is never resubmitted automatically.
          setStatus(status, e.code === "stale_contract" ? "stale" : "error", errorText(e), e.code);
          statusDetail.replaceChildren(issueList("contracts-save-issues", e));
          return false;
        }
      }
      async function refreshKeepingDraft() {
        if (deps.changed) await deps.changed();               // the library listing and every selector
        if (!item) return;
        try {
          const detail = await api("/api/contracts/" + encodeURIComponent(item.name));
          Object.assign(item, { token: detail.file_token, latest: detail.latest, revisions: detail.revisions || [], family: detail.family, problem: detail.problem });
          usedByView(detail.used_by, detail.latest);
          baseView = detail.definition_text ? await v2("project", { definition_text: detail.definition_text }) : null;
          // An unedited view moves to the latest revision; a draft (even one whose edit is still in flight) is kept.
          if (!dirty && ed && detail.definition_text && await ed.replaceIf(baseText, detail.definition_text)) {
            item.selected = detail.latest; baseText = detail.definition_text; basisNote.textContent = "";
          }
          renderRevisions(); renderFamilies(); controls();
          setStatus(status, dirty ? "dirty" : "idle", dirty ? `Refreshed: the latest is revision ${detail.latest}. Your draft is kept; review and save again.` : "Refreshed.");
        } catch (e) { setStatus(status, "error", errorText(e), e.code); }
      }

      search.oninput = renderList; familyFilter.onchange = renderList;
      newBtn.onclick = () => guarded(() => newDraft(null));
      name.oninput = () => { if (!item) setDirty(true); };
      family.onchange = () => { draftFamily = family.value; if (!item) setDirty(true); controls(); };
      addFamily.onclick = async () => {
        const input = h("input", { "data-test": "contracts-new-family", "aria-label": "Family label" }), error = h("p", { role: "alert" });
        let chosen = null;
        const c = await dialog("Add family", h("div", {}, h("p", { text: "A family only groups contracts; it is not part of a contract's identity. Nothing is written until you save." }), field("Family label", input), error), ["Add", "Cancel"], async (choice) => {
          if (choice !== "Add") return true;
          const v = input.value.trim();
          if (!v || [...v].length > 64 || /[\u0000-\u001f\u007f-\u009f]/.test(v)) { error.textContent = "A family label is 1 to 64 characters without control characters."; return false; }
          chosen = v; return true;
        });
        if (c === "Add" && chosen) { draftFamily = chosen; renderFamilies(); if (!item) setDirty(true); controls(); }
      };
      saveFamily.onclick = async () => {
        if (!item) return;
        try {
          const r = await v2("family", { name: item.name, expected_file_token: item.token, family: family.value });
          item.token = r.file_token; item.family = r.family; draftFamily = r.family;
          if (deps.changed) await deps.changed();
          renderFamilies(); renderList(); controls();
          setStatus(status, r.unchanged ? "unchanged" : "family_saved", r.unchanged ? "Grouping unchanged." : `Moved to family ${r.family}. Revisions and pins are unchanged.`);
        } catch (e) { setStatus(status, e.code === "stale_contract" ? "stale" : "error", errorText(e), e.code); }
      };
      revision.onchange = () => { const n = parseInt(revision.value, 10); revision.value = String(item.selected); if (item) guarded(() => openItem(item.name, n)); };
      restore.onclick = async () => {
        if (!item) return;
        const n = item.selected;
        if (await dialog("Restore as new revision", h("p", { text: `Append a new revision ${item.latest + 1} that copies revision ${n}. Existing revisions are not changed, and blocks keep their pins.` }), ["Confirm", "Cancel"]) !== "Confirm") return;
        try {
          const r = await v2("restore", { name: item.name, expected_file_token: item.token, revision: n, label: label_.value });
          if (deps.changed) await deps.changed();
          await openItem(item.name);
          setStatus(status, r.unchanged ? "unchanged" : "restored", r.unchanged ? `Revision ${n} equals the latest; nothing was written.` : `Restored revision ${n} as revision ${r.revision}.`);
        } catch (e) { setStatus(status, e.code === "stale_contract" ? "stale" : "error", errorText(e), e.code); }
      };
      save.onclick = () => doSave();
      refresh.onclick = () => refreshKeepingDraft();
      duplicate.onclick = () => guarded(async () => {
        const text = ed && ed.text();
        if (text === null || text === undefined) return;
        await newDraft(text, item ? item.family : draftFamily);
        setStatus(status, "dirty", "Duplicate: name this new contract. It starts at revision 1.");
      });
      del.onclick = async () => {
        if (!item) return;
        let detail = null;
        try { detail = await api("/api/contracts/" + encodeURIComponent(item.name)); } catch (e) { /* the server decides */ }
        const used = detail && detail.used_by;
        const box = h("div", {}, h("p", { text: `Delete ${item.name} and all its revisions? Blocks, history and other contracts are never changed by this.` }));
        if (used && used.items.length) box.append(h("p", { class: "warn-text", text: `It is used by ${used.items.length} block port${used.items.length === 1 ? "" : "s"}; deletion will be refused.` }));
        if (used && !used.complete) box.append(h("p", { class: "warn-text", text: "Some block definitions could not be inspected; deletion will be refused." }));
        if (await dialog("Delete contract", box, ["Delete", "Cancel"]) !== "Delete") return;
        try {
          await v2("delete", { name: item.name, expected_file_token: detail ? detail.file_token : item.token });
          const gone = item.name;
          if (deps.changed) await deps.changed();
          await newDraft(null);
          setStatus(status, "deleted", `Deleted ${gone}.`);
        } catch (e) {
          setStatus(status, e.code === "stale_contract" ? "stale" : "error", errorText(e), e.code);
          statusDetail.replaceChildren();
          for (const b of (e.body && e.body.blockers) || []) statusDetail.append(h("div", { "data-test": "contracts-blocker", "data-block": b.block, "data-port": b.port, text: `${b.block} · ${b.current ? "current" : "retained"} · ${b.side} ${b.port} · revision ${b.revision}` }));
          for (const u of (e.body && e.body.unreadable) || []) statusDetail.append(h("div", { "data-test": "contracts-unreadable", text: u }));
        }
      };
      runCheck.onclick = async () => {
        if (!ed || !ed.valid()) { result.note("The contract is not valid, so nothing was checked."); return; }
        try { const r = await v2("check", { definition_text: ed.text(), value_text: checkText.value }); result.show(r.check); }
        catch (e) { result.note(errorText(e)); }
      };

      // --- synthesis (spec 14, 15.2) ---
      const task = h("textarea", { rows: 3, "data-test": "contracts-synth-task", "aria-label": "Task description", placeholder: "Describe the value: what it holds, when fields may be missing, identifiers to keep as text…" });
      const output = h("input", { "data-test": "contracts-synth-output", "aria-label": "Output name", value: "value" });
      const temperature = h("input", { "data-test": "contracts-synth-temperature", "aria-label": "Temperature", placeholder: "role default" });
      const reasoning = h("select", { "data-test": "contracts-synth-reasoning", "aria-label": "Reasoning level" },
        h("option", { value: "", text: "Role default" }), ...["off", "low", "medium", "high"].map((v) => h("option", { value: v, text: v })));
      const maxTokens = h("input", { "data-test": "contracts-synth-max-tokens", "aria-label": "Output token cap", placeholder: "4096", inputMode: "numeric" });
      const synthBtn = h("button", { type: "button", "data-test": "contracts-synth-plan", text: "Synthesize draft" });
      const synthStop = h("button", { type: "button", "data-test": "contracts-synth-stop", text: "Stop", disabled: true });
      const synthReason = h("p", { class: "note", "data-test": "contracts-synth-reason" });
      const synthStatus = h("p", { role: "status", "data-test": "contracts-synth-status", "data-state": "idle" });
      const candidate = h("div", { "data-test": "contracts-synth-candidate", hidden: true });
      synth.append(h("legend", { text: "Synthesize (model draft for review)" }),
        h("p", { class: "note", text: "Uses the write_contract role. The proposal is a draft you review; it is never saved or attached to a block automatically. Assumptions are shown beside it." }),
        field("Task description", task), h("div", { class: "fields" }, field("Output name", output), field("Temperature (optional)", temperature), field("Reasoning", reasoning), field("Output token cap (optional)", maxTokens)),
        h("div", { class: "row" }, synthBtn, synthStop), synthReason, synthStatus, candidate);
      let batch = null;
      function synthesisControls() {
        const kind = ed && ed.view() ? kindOf(ed.view()) : null;
        const blocked = !ed || !ed.valid() || kind === "text" || (item && item.problem);
        synthBtn.disabled = !!blocked || synthStatus.dataset.state === "running";
        synthReason.textContent = kind === "text" ? "Synthesis drafts schema-backed JSON. Text contracts are written with the readable form; switch the format to Structured JSON or Any JSON to use it." :
          ed && !ed.valid() ? "Correct the draft first." : "";
      }
      function clearCandidate() { candidate.hidden = true; candidate.replaceChildren(); }
      function synthesisBody() {
        const body = { task_description: task.value, output_name: output.value, definition_text: ed.text(), role: "write_contract" };
        const settings = {};
        if (temperature.value.trim()) settings.temperature = temperature.value.trim();      // exact text, decoded by the server
        if (reasoning.value) settings.reasoning = reasoning.value;
        if (Object.keys(settings).length) body.settings_text = settings;
        if (maxTokens.value.trim()) body.max_tokens = parseInt(maxTokens.value.trim(), 10);
        return body;
      }
      synthBtn.onclick = async () => {
        clearCandidate();
        setStatus(synthStatus, "planning", "Preparing the request…");
        let body, plan;
        try { body = synthesisBody(); plan = await v2("synthesis/plan", body); }
        catch (e) { setStatus(synthStatus, "error", errorText(e), e.code); return; }
        const dest = plan.destination || {};
        const box = h("div", { "data-test": "contracts-synth-review" },
          h("dl", { class: "ws-facts" }, ...[["Role", dest.role || "write_contract"], ["Model", dest.model || "?"], ["Destination", dest.base_url || dest.url || "?"], ["Provider", dest.provider || ""]]
            .flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: String(v) })])),
          h("p", { text: "Start makes a real model call. The task description and the current schema are sent; nothing else from this page is sent." }),
          ...(plan.plan.required_confirmations || []).map((c) => h("p", { text: c.detail })));
        if (await dialog("Review contract synthesis", box, ["Confirm", "Cancel"]) !== "Confirm") { setStatus(synthStatus, "idle", ""); return; }
        try {
          const ticket = (await api("/api/workshop/tickets", { format_version: 1 })).ticket;
          const started = await v2("synthesis/start", { ...body, expected_plan: plan.plan.expected_plan, ticket,
            confirmations: (plan.plan.required_confirmations || []).map(({ kind, digest }) => ({ kind, digest })) });
          batch = started.batch_id;
          const source = body.definition_text;
          setStatus(synthStatus, "running", "Synthesizing…"); synthStop.disabled = false; synthesisControls();
          for (;;) {
            const b = await api("/api/workshop/batches/" + encodeURIComponent(batch));
            if (b.state !== "running") { await finished(b, source); break; }
            await new Promise((r) => setTimeout(r, 500));
          }
        } catch (e) { setStatus(synthStatus, "error", errorText(e), e.code); }
        finally { synthStop.disabled = true; batch = null; synthesisControls(); }
      };
      synthStop.onclick = async () => {
        if (!batch) return;
        try { await api("/api/workshop/batches/" + encodeURIComponent(batch) + "/stop", { format_version: 1 }); } catch (e) { /* the poll reports */ }
      };
      async function finished(b, source) {
        const row = (b.rows || [])[0] || {};
        if (!row.proposal_id) {
          setStatus(synthStatus, b.state === "stopped" || row.outcome === "stopped" ? "stopped" : "failed",
            row.outcome === "stopped" || b.state === "stopped" ? "Stopped; no proposal." : "No usable proposal was produced (" + (row.outcome || b.state) + ").");
          return;
        }
        let applied;
        try { applied = await v2("synthesis/apply", { proposal_id: row.proposal_id, definition_text: source }); }
        catch (e) { setStatus(synthStatus, "error", errorText(e), e.code); return; }
        setStatus(synthStatus, "proposal", "Proposal ready for review. Apply draft replaces the editor's draft; nothing is saved.");
        candidate.hidden = false;
        const rows = (applied.projection.fields || []).map((r) => h("li", { text: `${pathKey(r.path) || "(value)"} · ${r.type}${r.required ? "" : " · optional"}${r.nullable ? " · allows null" : ""}${r.description ? " — " + r.description : ""}` }));
        const pre = h("pre", { "data-test": "contracts-synth-source", text: applied.definition_text });
        candidate.replaceChildren(h("h4", { text: "Candidate" }), h("p", { text: applied.projection.description || "" }), h("ul", {}, ...rows),
          h("h4", { text: "Assumptions to review" }), h("ul", { "data-test": "contracts-synth-notes" }, ...(applied.notes.length ? applied.notes.map((n) => h("li", { text: n })) : [h("li", { class: "muted", text: "None stated." })])),
          h("details", {}, h("summary", { text: "Exact candidate source" }), pre),
          h("div", { class: "row" },
            h("button", { type: "button", "data-test": "contracts-synth-apply", text: "Apply draft", onclick: async () => {
              const current = ed.text();
              if (current !== source) { setStatus(synthStatus, "stale", MESSAGES.proposal_stale, "proposal_stale"); return; }
              await ed.set(applied.definition_text, true);
              proposal = applied.proposal_id; setDirty(true); clearCandidate();
              setStatus(synthStatus, "applied", "Draft applied. Review it, then Save when ready; the revision records its synthesis origin.");
            } }),
            h("button", { type: "button", "data-test": "contracts-synth-discard", text: "Discard", onclick: () => { clearCandidate(); setStatus(synthStatus, "discarded", "Proposal discarded."); } })));
      }

      newDraft(null);
      return {
        leave,
        async open(contractName, number) { if (await leave()) await openItem(contractName, number); },
        onState() { renderFamilies(); renderList(); controls(); },
        async tutorialPrepare(definitionText, sample, contractName) {
          setDirty(false);
          if (items().some((c) => c.name === contractName)) await openItem(contractName);
          else { await newDraft(null); name.value = contractName; }
          await ed.set(definitionText, true);
          checkText.value = sample; result.clear(); setDirty(true);
        },
        dirty: () => dirty,
        submit: () => runCheck.click(),
      };
    }

    // Styles for these components, added once.
    if (!document.getElementById("cw-style")) {
      document.head.append(h("style", { id: "cw-style", text:
        ".cw-library{display:grid;grid-template-columns:280px minmax(0,1fr);gap:1rem;align-items:start}" +
        ".cw-library-list{display:grid;gap:.4rem;align-content:start}.cw-library-editor{min-width:0}" +
        ".cw-fields{border-collapse:collapse;width:100%}.cw-fields td,.cw-fields th{padding:.2rem;vertical-align:top}" +
        ".cw-fields input[type=checkbox]{width:auto}.cw-detail{display:grid;gap:.3rem;padding:.3rem 0 .6rem 1rem}" +
        ".cw-port{border-left:3px solid var(--line);padding-left:.6rem;margin:.3rem 0}.cw-savebar{align-items:end}" +
        "@media(max-width:800px){.cw-library{grid-template-columns:minmax(0,1fr)}}" }));
    }

    return { editor, library, port, picker, changes, errorText };
  }

  window.AgentWizContracts = { create };
})();
